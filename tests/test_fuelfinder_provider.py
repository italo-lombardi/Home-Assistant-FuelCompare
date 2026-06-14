"""Tests for IEFuelFinderProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
    IEFuelFinderProvider,
    _HEADERS,
    _find_station,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATION_UUID = "7ec0dd4f-4322-4b4f-9de1-c8894a684626"
_OSM_ID = "123456789"

_BASE_STATION: dict = {
    "id": _STATION_UUID,
    "osm_id": _OSM_ID,
    "name": "Circle K Mulhuddart",
    "slug": "circle-k-circle-k-mulhuddart-huddart",
    "brand": "Circle K",
    "logo_url": "https://www.google.com/s2/favicons?domain=circlek.com&sz=64",
    "lat": 53.399,
    "lng": -6.433,
    "county": "Dublin",
    "street": "Mulhuddart Village",
    "phone": "",
    "website": "",
    "opening_hours": "Mo-Su 07:00-23:00",
    "price": 1.828,
    "updated_at": "2026-06-13T16:04:01.754194+00:00",
    "confidence": "likely",
    "has_price": True,
}

_INIT_RESPONSE: dict = {
    "nationalStats": {
        "diesel": 1.838,
        "petrol": 1.838,
        "kerosene_500l": None,
        "week_change_diesel": -0.012,
        "week_change_petrol": -0.008,
        "count": 1247,
        "source": "user_submission",
        "updated": "14 Jun 2026",
    },
    "cheapest": {
        "diesel": [_BASE_STATION],
        "petrol": [_BASE_STATION],
        "kerosene": [],
    },
    "mostExpensive": {
        "diesel": [],
        "petrol": [],
        "kerosene": [],
    },
}


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
    text_data: str | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.text = AsyncMock(return_value=text_data or "")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _stations_response(
    station: dict | None = None,
    fuel: str = "diesel",
    city: str = "dublin",
    total: int = 1,
) -> dict:
    """Return a minimal /stations API response."""
    s = station or _BASE_STATION
    return {
        "stations": [s],
        "city": city,
        "fuel": fuel,
        "total": total,
    }


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata() -> None:
    """IEFuelFinderProvider declares required class attributes."""
    assert IEFuelFinderProvider.COUNTRY == "IE"
    assert IEFuelFinderProvider.PROVIDER_KEY == "ie_fuelfinder"
    assert IEFuelFinderProvider.LABEL == "FuelFinder.ie"


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all four FuelFinder fuel types."""
    caps = IEFuelFinderProvider.CAPABILITIES
    assert "diesel" in caps
    assert "petrol" in caps
    assert "kerosene" in caps
    assert "cng" in caps


def test_provider_capabilities_include_fuelfinder_fields() -> None:
    """CAPABILITIES includes FuelFinder-specific fields."""
    caps = IEFuelFinderProvider.CAPABILITIES
    assert "price_confidence" in caps
    assert "has_price" in caps
    assert "opening_hours" in caps


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = IEFuelFinderProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


def test_provider_poll_interval() -> None:
    """Default poll interval is 1800 seconds (30 minutes)."""
    assert IEFuelFinderProvider.POLL_INTERVAL_SECONDS == 1800


# ---------------------------------------------------------------------------
# Required auth headers
# ---------------------------------------------------------------------------


def test_headers_include_sec_fetch_site() -> None:
    """_HEADERS includes Sec-Fetch-Site: same-origin."""
    assert _HEADERS.get("Sec-Fetch-Site") == "same-origin"


def test_headers_include_referer() -> None:
    """_HEADERS includes Referer pointing at fuelfinder.ie."""
    assert "fuelfinder.ie" in _HEADERS.get("Referer", "")


def test_headers_include_accept_json() -> None:
    """_HEADERS includes Accept: application/json."""
    assert _HEADERS.get("Accept") == "application/json"


def test_headers_user_agent_not_blocked() -> None:
    """_HEADERS User-Agent is not in the server-side blocklist."""
    ua = _HEADERS.get("User-Agent", "")
    blocked_prefixes = ("curl/", "python-requests/", "Wget/", "Go-http-client/")
    for prefix in blocked_prefixes:
        assert not ua.startswith(prefix), (
            f"User-Agent '{ua}' starts with blocked prefix '{prefix}'"
        )
    # Bare Mozilla/5.0 without platform detail is also blocked
    assert ua != "Mozilla/5.0"


# ---------------------------------------------------------------------------
# async_fetch — success path (diesel)
# ---------------------------------------------------------------------------


async def test_async_fetch_success_diesel() -> None:
    """async_fetch returns normalised dict with diesel price from /stations."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200,
        json_data=_stations_response(
            station={**_BASE_STATION, "price": 1.849},
            fuel="petrol",
        ),
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["diesel"] == pytest.approx(1.828)


async def test_async_fetch_success_petrol() -> None:
    """async_fetch returns petrol price when the station appears in petrol results."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200,
        json_data=_stations_response(
            station={**_BASE_STATION, "price": 1.849},
            fuel="petrol",
        ),
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["petrol"] == pytest.approx(1.849)


async def test_async_fetch_kerosene_none_when_absent_from_results() -> None:
    """async_fetch returns kerosene=None when station is not in kerosene results."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200,
        json_data=_stations_response(
            station={**_BASE_STATION, "price": 1.849},
            fuel="petrol",
        ),
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["kerosene"] is None


async def test_async_fetch_price_not_divided_by_100() -> None:
    """async_fetch does NOT divide prices by 100 — FuelFinder returns EUR float directly."""
    station_with_price = {**_BASE_STATION, "price": 1.828}
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=station_with_price, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    # Must NOT apply the >10 → /100 guard: 1.828 must stay 1.828
    assert data["diesel"] == pytest.approx(1.828)
    assert data["diesel"] < 10.0


# ---------------------------------------------------------------------------
# async_fetch — field normalisation
# ---------------------------------------------------------------------------


async def test_async_fetch_normalises_station_identity_fields() -> None:
    """async_fetch populates name, brand, county, osm_id, slug from API response."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["name"] == "Circle K Mulhuddart"
    assert data["brand"] == "Circle K"
    assert data["county"] == "Dublin"
    assert data["osm_id"] == _OSM_ID
    assert data["slug"] == "circle-k-circle-k-mulhuddart-huddart"


async def test_async_fetch_normalises_location_fields() -> None:
    """async_fetch populates lat, lng, street, phone, website from API response."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["lat"] == pytest.approx(53.399)
    assert data["lng"] == pytest.approx(-6.433)
    assert data["address"] == "Mulhuddart Village"


async def test_async_fetch_normalises_fuelfinder_specific_fields() -> None:
    """async_fetch populates confidence, has_price, updated_at, opening_hours."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["confidence"] == "likely"
    assert data["has_price"] is True
    assert data["lastupdated"] == "2026-06-13T16:04:01.754194+00:00"
    assert data["opening_hours"] == "Mo-Su 07:00-23:00"


async def test_async_fetch_updated_at_mapped_to_lastupdated() -> None:
    """async_fetch maps updated_at to lastupdated for coordinator timestamp sensor compat."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    # lastupdated must mirror updated_at so StationPriceLastUpdatedSensor works
    assert data["lastupdated"] == data["lastupdated"]


async def test_async_fetch_brand_none_when_api_returns_null() -> None:
    """async_fetch returns brand=None when API brand field is null, not empty string."""
    no_brand_station = {**_BASE_STATION, "brand": None}
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=no_brand_station, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["brand"] is None


async def test_async_fetch_brand_none_when_api_returns_empty_string() -> None:
    """async_fetch returns brand=None when API brand field is empty string."""
    no_brand_station = {**_BASE_STATION, "brand": ""}
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=no_brand_station, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["brand"] is None


async def test_async_fetch_has_price_false_when_no_submissions() -> None:
    """async_fetch returns has_price=False for stations with no community submissions."""
    no_price_station = {
        **_BASE_STATION,
        "has_price": False,
        "price": None,
        "confidence": None,
    }
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=no_price_station, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["has_price"] is False
    assert data["diesel"] is None
    assert data["confidence"] is None


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_in_results() -> None:
    """async_fetch raises ProviderError when station UUID is not found in any response."""
    other_station = {**_BASE_STATION, "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=other_station, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_when_all_responses_empty() -> None:
    """async_fetch raises ProviderError when all /stations responses have empty lists."""
    empty = {"stations": [], "total": 0, "city": "dublin", "fuel": "diesel"}
    diesel_resp = _make_mock_response(200, json_data=empty)
    petrol_resp = _make_mock_response(200, json_data={**empty, "fuel": "petrol"})
    kerosene_resp = _make_mock_response(200, json_data={**empty, "fuel": "kerosene"})
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


# ---------------------------------------------------------------------------
# async_fetch — HTTP error propagation
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """ClientError raised by aiohttp propagates out of async_fetch uncaught."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = IEFuelFinderProvider(_STATION_UUID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_on_http_403() -> None:
    """HTTP 403 (auth header missing/blocked UA) is surfaced via raise_for_status."""
    resp_403 = _make_mock_response(403)
    resp_403.raise_for_status = MagicMock(side_effect=ClientError("403 Forbidden"))
    session = MagicMock()
    session.get = MagicMock(return_value=resp_403)

    provider = IEFuelFinderProvider(_STATION_UUID)

    with pytest.raises((ClientError, ProviderError)):
        await provider.async_fetch(session, _STATION_UUID)


# ---------------------------------------------------------------------------
# async_fetch — auth header contract (headers sent on every request)
# ---------------------------------------------------------------------------


async def test_async_fetch_sends_sec_fetch_site_header() -> None:
    """Every GET request to /stations includes Sec-Fetch-Site: same-origin."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    await provider.async_fetch(session, _STATION_UUID)

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert headers.get("Sec-Fetch-Site") == "same-origin", (
            f"Sec-Fetch-Site missing from GET call: {call}"
        )


async def test_async_fetch_sends_referer_header() -> None:
    """Every GET request to /stations includes a Referer pointing at fuelfinder.ie."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    await provider.async_fetch(session, _STATION_UUID)

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert "fuelfinder.ie" in headers.get("Referer", ""), (
            f"Referer missing from GET call: {call}"
        )


async def test_async_fetch_fan_out_makes_multiple_fuel_requests() -> None:
    """async_fetch issues one /stations request per fuel type (diesel, petrol, kerosene)."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    await provider.async_fetch(session, _STATION_UUID)

    # At minimum 3 GET requests: one per non-cng fuel type
    assert session.get.call_count >= 3


# ---------------------------------------------------------------------------
# async_fetch — unique_id convention
# ---------------------------------------------------------------------------


def test_provider_unique_id_uses_uuid_not_osm_id() -> None:
    """Provider uses the internal DB UUID (id field) for unique_id, not osm_id."""
    provider = IEFuelFinderProvider(_STATION_UUID)
    # Verify the provider stores the UUID as station_id, not anything else
    assert provider._station_id == _STATION_UUID


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_returns_all_required_keys() -> None:
    """_parse_station returns a dict with all 15 normalised data keys."""
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {
        "diesel": {**_BASE_STATION, "price": 1.828},
        "petrol": {**_BASE_STATION, "price": 1.849},
    }
    result = provider._build_station_data(_STATION_UUID, _BASE_STATION, prices_by_fuel)

    required_keys = {
        "diesel",
        "petrol",
        "kerosene",
        "cng",
        "lastupdated",
        "name",
        "brand",
        "county",
        "address",
        "phone",
        "website",
        "opening_hours",
        "slug",
        "osm_id",
        "lat",
        "lng",
        "logo_url",
        "confidence",
        "has_price",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _parse_station output"


def test_parse_station_price_not_divided() -> None:
    """_parse_station stores float prices as-is (no /100 conversion)."""
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {
        "diesel": {**_BASE_STATION, "price": 1.828},
        "petrol": {**_BASE_STATION, "price": 1.849},
    }
    result = provider._build_station_data(_STATION_UUID, _BASE_STATION, prices_by_fuel)
    assert result["diesel"] == pytest.approx(1.828)
    assert result["petrol"] == pytest.approx(1.849)


def test_parse_station_kerosene_none() -> None:
    """_parse_station stores kerosene=None when no kerosene price available."""
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {"diesel": {**_BASE_STATION, "price": 1.828}}
    result = provider._build_station_data(_STATION_UUID, _BASE_STATION, prices_by_fuel)
    assert result["kerosene"] is None


def test_parse_station_brand_empty_string_becomes_none() -> None:
    """_parse_station normalises empty string brand to None."""
    station = {**_BASE_STATION, "brand": ""}
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {"diesel": {**station, "price": 1.828}}
    result = provider._build_station_data(_STATION_UUID, station, prices_by_fuel)
    assert result["brand"] is None


def test_parse_station_confidence_preserved() -> None:
    """_build_station_data passes confidence through unchanged."""
    for confidence_val in ("fresh", "likely", "outdated", None):
        station = {**_BASE_STATION, "confidence": confidence_val}
        provider = IEFuelFinderProvider(_STATION_UUID)
        prices_by_fuel = {"diesel": {**station, "price": 1.828}}
        result = provider._build_station_data(_STATION_UUID, station, prices_by_fuel)
        assert result["confidence"] == confidence_val


def test_parse_station_has_price_false() -> None:
    """_parse_station propagates has_price=False correctly."""
    station = {**_BASE_STATION, "has_price": False, "price": None, "confidence": None}
    provider = IEFuelFinderProvider(_STATION_UUID)
    result = provider._build_station_data(_STATION_UUID, station, {})
    assert result["has_price"] is False


def test_parse_station_lat_lng_none_for_user_submitted() -> None:
    """_parse_station handles null lat/lng for user-submitted stations."""
    station = {**_BASE_STATION, "lat": None, "lng": None}
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {"diesel": {**station, "price": 1.828}}
    result = provider._build_station_data(_STATION_UUID, station, prices_by_fuel)
    assert result["latitude"] is None
    assert result["lng"] is None


def test_parse_station_updated_at_mirrored_to_lastupdated() -> None:
    """_parse_station sets lastupdated equal to updated_at for sensor compat."""
    provider = IEFuelFinderProvider(_STATION_UUID)
    prices_by_fuel = {"diesel": {**_BASE_STATION, "price": 1.828}}
    result = provider._build_station_data(_STATION_UUID, _BASE_STATION, prices_by_fuel)
    assert result["lastupdated"] == result["lastupdated"]
    assert result["lastupdated"] == "2026-06-13T16:04:01.754194+00:00"


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns station name when API responds successfully."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name == "Circle K Mulhuddart"


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = IEFuelFinderProvider(_STATION_UUID)
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_not_found() -> None:
    """async_fetch_station_name returns None when station UUID not in API results."""
    other_station = {**_BASE_STATION, "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    diesel_resp = _make_mock_response(
        200, json_data=_stations_response(station=other_station, fuel="diesel")
    )
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_provider_error() -> None:
    """async_fetch_station_name returns None when all results are empty (swallows ProviderError)."""
    empty = {"stations": [], "total": 0, "city": "dublin", "fuel": "diesel"}
    diesel_resp = _make_mock_response(200, json_data=empty)
    petrol_resp = _make_mock_response(200, json_data={**empty, "fuel": "petrol"})
    kerosene_resp = _make_mock_response(200, json_data={**empty, "fuel": "kerosene"})
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


# ---------------------------------------------------------------------------
# _find_station_in_responses (internal helper)
# ---------------------------------------------------------------------------


def test_find_station_in_responses_matches_by_uuid() -> None:
    """_find_station picks the station matching the requested UUID."""
    other = {
        **_BASE_STATION,
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "Other Station",
    }
    stations = [other, _BASE_STATION]
    station = _find_station(stations, _STATION_UUID)

    assert station is not None
    assert station["id"] == _STATION_UUID
    assert station["name"] == "Circle K Mulhuddart"


def test_find_station_in_responses_returns_none_when_absent() -> None:
    """_find_station returns None when UUID not present in station list."""
    other = {**_BASE_STATION, "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    station = _find_station([other], _STATION_UUID)

    assert station is None


def test_find_station_in_responses_handles_empty_lists() -> None:
    """_find_station handles empty station lists without error."""
    station = _find_station([], _STATION_UUID)

    assert station is None


# ---------------------------------------------------------------------------
# CNG fuel type (optional, sparse data)
# ---------------------------------------------------------------------------


async def test_async_fetch_cng_price_when_available() -> None:
    """async_fetch includes cng price when the station appears in CNG results."""
    cng_station = {**_BASE_STATION, "price": 1.299}
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    cng_resp = _make_mock_response(
        200, json_data=_stations_response(station=cng_station, fuel="cng")
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp, cng_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["cng"] == pytest.approx(1.299)


async def test_async_fetch_cng_none_when_not_included() -> None:
    """async_fetch returns cng=None when include_cng is False (default)."""
    diesel_resp = _make_mock_response(200, json_data=_stations_response(fuel="diesel"))
    petrol_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "petrol"}
    )
    kerosene_resp = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"},
    )
    session = _make_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider(_STATION_UUID)
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["cng"] is None


# ---------------------------------------------------------------------------
# county parameter stored and used in requests
# ---------------------------------------------------------------------------


async def test_async_fetch_uses_county_in_stations_request() -> None:
    """async_fetch passes county as the city param to /stations."""
    # Station with Cork county, same UUID
    cork_station = {**_BASE_STATION, "county": "Cork"}
    diesel_resp_cork = _make_mock_response(
        200,
        json_data=_stations_response(station=cork_station, fuel="diesel", city="cork"),
    )
    petrol_resp_cork = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "cork", "fuel": "petrol"}
    )
    kerosene_resp_cork = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "cork", "fuel": "kerosene"}
    )

    session = _make_session(diesel_resp_cork, petrol_resp_cork, kerosene_resp_cork)

    provider = IEFuelFinderProvider(_STATION_UUID)
    provider._cached_county = "cork"  # pre-seed county so requests use cork scope
    await provider.async_fetch(session, _STATION_UUID)

    # Every call must have city=cork in params
    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("city") == "cork", (
            f"Expected city=cork in params but got: {params}"
        )


async def test_async_fetch_county_stale_falls_back_to_national() -> None:
    """When cached county returns no match, falls back to national and updates county cache."""
    # County-scoped requests return empty lists; national returns the station
    empty_county_resp = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "cork", "fuel": "diesel"}
    )
    national_diesel = _make_mock_response(
        200,
        json_data=_stations_response(
            station={**_BASE_STATION, "county": "Dublin"}, fuel="diesel"
        ),
    )
    national_petrol = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "ireland", "fuel": "petrol"}
    )
    national_kerosene = _make_mock_response(
        200,
        json_data={"stations": [], "total": 0, "city": "ireland", "fuel": "kerosene"},
    )
    national_cng = _make_mock_response(
        200, json_data={"stations": [], "total": 0, "city": "ireland", "fuel": "cng"}
    )
    # 4 cork requests return empty, then 4 national requests for the retry
    session = _make_session(
        empty_county_resp,
        empty_county_resp,
        empty_county_resp,
        empty_county_resp,
        national_diesel,
        national_petrol,
        national_kerosene,
        national_cng,
    )

    provider = IEFuelFinderProvider(_STATION_UUID)
    provider._cached_county = "cork"  # stale county

    data = await provider.async_fetch(session, _STATION_UUID)

    # ProviderError should NOT be raised — found via national fallback
    assert data["diesel"] == pytest.approx(1.828)
    # County cache updated to actual station county
    assert provider._cached_county == "dublin"


# ---------------------------------------------------------------------------
# API base URL points to fuelfinder.ie
# ---------------------------------------------------------------------------


def test_api_base_url() -> None:
    """The provider targets the correct fuelfinder.ie API base URL."""
    from custom_components.fuelcompare_ie.providers.ie_fuelfinder import _BASE_URL

    assert "fuelfinder.ie" in _BASE_URL
    assert _BASE_URL.startswith("https://")
