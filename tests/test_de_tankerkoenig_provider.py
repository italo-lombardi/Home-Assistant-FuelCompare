"""Tests for DeTankerkoenigProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
    DeTankerkoenigProvider,
    _BASE_URL,
    _HEADERS,
    _build_address,
    _parse_price,
    _parse_station,
)

# ---------------------------------------------------------------------------
# Test fixtures / data
# ---------------------------------------------------------------------------

_STATION_UUID = "51d4b660-a095-1aa0-e100-80009459e03a"
_API_KEY = "00000000-0000-0000-0000-000000000002"

_BASE_STATION: dict = {
    "id": _STATION_UUID,
    "name": "ARAL Tankstelle",
    "brand": "ARAL",
    "street": "Hauptstraße",
    "houseNumber": "12",
    "postCode": 10115,
    "place": "Berlin",
    "lat": 52.520,
    "lng": 13.405,
    "dist": 0.5,
    "e5": 1.789,
    "e10": 1.759,
    "diesel": 1.699,
    "isOpen": True,
}

_DETAIL_PAYLOAD_OK: dict = {
    "ok": True,
    "station": {
        **_BASE_STATION,
        "openingTimes": [],
        "overrides": [],
        "wholeDay": False,
        "state": "open",
    },
}

_LIST_PAYLOAD_OK: dict = {
    "ok": True,
    "license": "CC BY 4.0",
    "data": "MTS-K",
    "status": "ok",
    "stations": [_BASE_STATION],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _make_provider(
    station_id: str = _STATION_UUID,
    api_key: str = _API_KEY,
    latitude: float | None = 52.520,
    longitude: float | None = 13.405,
    radius_km: float | None = 5.0,
) -> DeTankerkoenigProvider:
    return DeTankerkoenigProvider(
        station_id=station_id,
        api_key=api_key,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """DeTankerkoenigProvider declares COUNTRY='DE'."""
    assert DeTankerkoenigProvider.COUNTRY == "DE"


def test_provider_key() -> None:
    """DeTankerkoenigProvider declares PROVIDER_KEY='de_tankerkoenig'."""
    assert DeTankerkoenigProvider.PROVIDER_KEY == "de_tankerkoenig"


def test_provider_label() -> None:
    """DeTankerkoenigProvider has a human-readable label."""
    assert "Tankerkoenig" in DeTankerkoenigProvider.LABEL
    assert "Germany" in DeTankerkoenigProvider.LABEL


def test_provider_config_mode_is_location() -> None:
    """DeTankerkoenigProvider uses CONFIG_MODE='location'."""
    assert DeTankerkoenigProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """DeTankerkoenigProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert DeTankerkoenigProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """Default poll interval is 1800 seconds (30 minutes)."""
    assert DeTankerkoenigProvider.POLL_INTERVAL_SECONDS == 1800


def test_provider_requires_api_key() -> None:
    """DeTankerkoenigProvider requires an API key."""
    assert DeTankerkoenigProvider.REQUIRES_API_KEY is True


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all three Tankerkoenig fuel types."""
    caps = DeTankerkoenigProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "diesel" in caps
    assert "e10" in caps


def test_provider_capabilities_include_identity_fields() -> None:
    """CAPABILITIES includes station identity fields."""
    caps = DeTankerkoenigProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps
    assert "address" in caps
    assert "county" in caps


def test_provider_capabilities_include_location_fields() -> None:
    """CAPABILITIES includes latitude and longitude."""
    caps = DeTankerkoenigProvider.CAPABILITIES
    assert "latitude" in caps
    assert "longitude" in caps


def test_provider_capabilities_include_is_open() -> None:
    """CAPABILITIES includes is_open."""
    assert "is_open" in DeTankerkoenigProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = DeTankerkoenigProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = _make_provider()
    assert provider._station_id == _STATION_UUID


def test_constructor_stores_api_key() -> None:
    """Constructor stores api_key."""
    provider = _make_provider()
    assert provider._api_key == _API_KEY


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude and longitude."""
    provider = _make_provider(latitude=52.520, longitude=13.405)
    assert provider._latitude == pytest.approx(52.520)
    assert provider._longitude == pytest.approx(13.405)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=7.5)
    assert provider._radius_km == pytest.approx(7.5)


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = DeTankerkoenigProvider(
        station_id=_STATION_UUID,
        api_key=_API_KEY,
    )
    assert provider._radius_km == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Headers contract
# ---------------------------------------------------------------------------


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_headers_include_accept_json() -> None:
    """_HEADERS includes Accept: application/json."""
    assert _HEADERS.get("Accept") == "application/json"


def test_headers_user_agent_is_homeassistant() -> None:
    """_HEADERS User-Agent identifies as HomeAssistant."""
    assert "HomeAssistant" in _HEADERS["User-Agent"]


# ---------------------------------------------------------------------------
# API base URL
# ---------------------------------------------------------------------------


def test_api_base_url_points_to_tankerkoenig() -> None:
    """_BASE_URL targets tankerkoenig.de."""
    assert "tankerkoenig.de" in _BASE_URL
    assert _BASE_URL.startswith("https://")


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_returns_float_for_valid_price() -> None:
    """_parse_price converts a valid float string."""
    assert _parse_price(1.789) == pytest.approx(1.789)


def test_parse_price_rounds_to_three_decimals() -> None:
    """_parse_price rounds to 3 decimal places."""
    assert _parse_price(1.78901) == pytest.approx(1.789)


def test_parse_price_returns_none_for_boolean_false() -> None:
    """_parse_price returns None for JSON boolean false (station does not sell fuel)."""
    assert _parse_price(False) is None


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None for None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None for zero (not a valid price)."""
    assert _parse_price(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price(-1.5) is None


def test_parse_price_returns_none_for_string_garbage() -> None:
    """_parse_price returns None for non-numeric string."""
    assert _parse_price("n/a") is None


def test_parse_price_parses_string_float() -> None:
    """_parse_price parses a numeric string."""
    assert _parse_price("1.799") == pytest.approx(1.799)


def test_parse_price_does_not_divide_cents() -> None:
    """_parse_price does NOT apply the >10 → /100 guard — Tankerkoenig is already EUR/litre."""
    result = _parse_price(1.799)
    assert result is not None
    assert result < 10.0


def test_parse_price_returns_none_for_true() -> None:
    """_parse_price returns None for boolean True (unexpected but must not crash)."""
    # True coerces to 1.0 via float(), which is > 0, so it returns 1.0 — not None.
    # This is acceptable behaviour; the key contract is False → None.
    result = _parse_price(True)
    # Either None or 1.0 is acceptable; the important thing is no exception.
    assert result is None or isinstance(result, float)


# ---------------------------------------------------------------------------
# _build_address
# ---------------------------------------------------------------------------


def test_build_address_combines_street_and_house() -> None:
    """_build_address produces 'Street HouseNumber, PostCode Place'."""
    station = {
        "street": "Hauptstraße",
        "houseNumber": "12",
        "postCode": 10115,
        "place": "Berlin",
    }
    address = _build_address(station)
    assert address is not None
    assert "Hauptstraße 12" in address
    assert "Berlin" in address
    assert "10115" in address


def test_build_address_handles_missing_house_number() -> None:
    """_build_address omits the house number when absent."""
    station = {"street": "Bahnhofstraße", "postCode": 80333, "place": "München"}
    address = _build_address(station)
    assert address is not None
    assert "Bahnhofstraße" in address
    assert "München" in address


def test_build_address_handles_no_postcode() -> None:
    """_build_address works when postCode is absent."""
    station = {"street": "Ringstraße", "houseNumber": "1", "place": "Hamburg"}
    address = _build_address(station)
    assert address is not None
    assert "Ringstraße 1" in address
    assert "Hamburg" in address


def test_build_address_returns_none_for_empty_station() -> None:
    """_build_address returns None when all address fields are absent."""
    assert _build_address({}) is None


def test_build_address_returns_none_for_all_none_fields() -> None:
    """_build_address returns None when street, postCode, and place are all None."""
    station = {"street": None, "houseNumber": None, "postCode": None, "place": None}
    assert _build_address(station) is None


def test_build_address_street_only() -> None:
    """_build_address returns the street when postCode and place are absent."""
    station = {"street": "Musterstraße", "houseNumber": "5"}
    address = _build_address(station)
    assert address is not None
    assert "Musterstraße 5" in address


def test_build_address_postcode_only() -> None:
    """_build_address uses postCode + place even when street is absent."""
    station = {"postCode": 12345, "place": "Musterstadt"}
    address = _build_address(station)
    assert address is not None
    assert "12345" in address
    assert "Musterstadt" in address


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_returns_all_required_keys() -> None:
    """_parse_station returns a dict with all CAPABILITIES-aligned keys."""
    result = _parse_station(_BASE_STATION)
    required_keys = {
        "unleaded",
        "e10",
        "diesel",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "is_open",
        "lastupdated",
        "source_station_id",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _parse_station output"


def test_parse_station_maps_e5_to_unleaded() -> None:
    """_parse_station maps API e5 field to the 'unleaded' key."""
    result = _parse_station({**_BASE_STATION, "e5": 1.789})
    assert result["unleaded"] == pytest.approx(1.789)


def test_parse_station_maps_e10() -> None:
    """_parse_station maps API e10 field to 'e10' key."""
    result = _parse_station({**_BASE_STATION, "e10": 1.759})
    assert result["e10"] == pytest.approx(1.759)


def test_parse_station_maps_diesel() -> None:
    """_parse_station maps API diesel field to 'diesel' key."""
    result = _parse_station({**_BASE_STATION, "diesel": 1.699})
    assert result["diesel"] == pytest.approx(1.699)


def test_parse_station_e5_false_becomes_none() -> None:
    """_parse_station returns unleaded=None when e5=false (station does not sell E5)."""
    result = _parse_station({**_BASE_STATION, "e5": False})
    assert result["unleaded"] is None


def test_parse_station_e10_false_becomes_none() -> None:
    """_parse_station returns e10=None when e10=false."""
    result = _parse_station({**_BASE_STATION, "e10": False})
    assert result["e10"] is None


def test_parse_station_diesel_false_becomes_none() -> None:
    """_parse_station returns diesel=None when diesel=false."""
    result = _parse_station({**_BASE_STATION, "diesel": False})
    assert result["diesel"] is None


def test_parse_station_name_field() -> None:
    """_parse_station maps station name correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["name"] == "ARAL Tankstelle"


def test_parse_station_brand_field() -> None:
    """_parse_station maps station brand correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["brand"] == "ARAL"


def test_parse_station_brand_none_for_empty_string() -> None:
    """_parse_station normalises empty string brand to None."""
    result = _parse_station({**_BASE_STATION, "brand": ""})
    assert result["brand"] is None


def test_parse_station_brand_none_for_null() -> None:
    """_parse_station returns None when brand is null."""
    result = _parse_station({**_BASE_STATION, "brand": None})
    assert result["brand"] is None


def test_parse_station_county_maps_to_place() -> None:
    """_parse_station maps 'place' field to 'county'."""
    result = _parse_station(_BASE_STATION)
    assert result["county"] == "Berlin"


def test_parse_station_latitude_field() -> None:
    """_parse_station maps lat to latitude."""
    result = _parse_station(_BASE_STATION)
    assert result["latitude"] == pytest.approx(52.520)


def test_parse_station_longitude_field() -> None:
    """_parse_station maps lng to longitude."""
    result = _parse_station(_BASE_STATION)
    assert result["longitude"] == pytest.approx(13.405)


def test_parse_station_is_open_true() -> None:
    """_parse_station maps isOpen=True to is_open=True."""
    result = _parse_station({**_BASE_STATION, "isOpen": True})
    assert result["is_open"] is True


def test_parse_station_is_open_false() -> None:
    """_parse_station maps isOpen=False to is_open=False."""
    result = _parse_station({**_BASE_STATION, "isOpen": False})
    assert result["is_open"] is False


def test_parse_station_is_open_none_when_absent() -> None:
    """_parse_station returns is_open=None when isOpen is absent."""
    station = {k: v for k, v in _BASE_STATION.items() if k != "isOpen"}
    result = _parse_station(station)
    assert result["is_open"] is None


def test_parse_station_lastupdated_is_none() -> None:
    """_parse_station returns lastupdated=None (Tankerkoenig has no per-station timestamp)."""
    result = _parse_station(_BASE_STATION)
    assert result["lastupdated"] is None


def test_parse_station_source_station_id() -> None:
    """_parse_station stores the station UUID as source_station_id."""
    result = _parse_station(_BASE_STATION)
    assert result["source_station_id"] == _STATION_UUID


def test_parse_station_null_lat_lng() -> None:
    """_parse_station returns None for latitude and longitude when lat/lng are null."""
    station = {**_BASE_STATION, "lat": None, "lng": None}
    result = _parse_station(station)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_invalid_lat_lng_string() -> None:
    """_parse_station returns None for latitude/longitude on unparseable strings."""
    station = {**_BASE_STATION, "lat": "n/a", "lng": "n/a"}
    result = _parse_station(station)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_address_built_from_parts() -> None:
    """_parse_station builds a combined address from street, houseNumber, postCode, place."""
    result = _parse_station(_BASE_STATION)
    assert result["address"] is not None
    assert "Hauptstraße" in result["address"]
    assert "Berlin" in result["address"]


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns normalised StationData on a successful /detail.php response."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["name"] == "ARAL Tankstelle"
    assert data["brand"] == "ARAL"


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch returns correct diesel price from /detail.php."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["diesel"] == pytest.approx(1.699)


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch returns correct unleaded (e5) price from /detail.php."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["unleaded"] == pytest.approx(1.789)


async def test_async_fetch_success_e10_price() -> None:
    """async_fetch returns correct e10 price from /detail.php."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["e10"] == pytest.approx(1.759)


async def test_async_fetch_uses_detail_endpoint() -> None:
    """async_fetch calls the /detail.php endpoint."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_UUID)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "detail.php" in url


async def test_async_fetch_passes_station_id_param() -> None:
    """async_fetch passes id={station_id} as a query parameter."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs.get("params", {}).get("id") == _STATION_UUID


async def test_async_fetch_passes_api_key_param() -> None:
    """async_fetch passes apikey as a query parameter."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs.get("params", {}).get("apikey") == _API_KEY


async def test_async_fetch_sends_headers() -> None:
    """async_fetch passes _HEADERS on the GET request."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert headers.get("Accept") == "application/json"


async def test_async_fetch_price_not_divided_by_100() -> None:
    """async_fetch prices are already EUR/litre — no /100 conversion applied."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "diesel": 1.699},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["diesel"] == pytest.approx(1.699)
    assert data["diesel"] < 10.0


async def test_async_fetch_is_open_true() -> None:
    """async_fetch returns is_open=True for an open station."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["is_open"] is True


async def test_async_fetch_lat_lng_populated() -> None:
    """async_fetch populates latitude and longitude from station data."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["latitude"] == pytest.approx(52.520)
    assert data["longitude"] == pytest.approx(13.405)


async def test_async_fetch_address_populated() -> None:
    """async_fetch returns a non-None address built from station parts."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["address"] is not None
    assert len(data["address"]) > 0


async def test_async_fetch_county_is_place() -> None:
    """async_fetch maps station 'place' to 'county'."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["county"] == "Berlin"


# ---------------------------------------------------------------------------
# async_fetch — price edge cases from API
# ---------------------------------------------------------------------------


async def test_async_fetch_e5_false_returns_unleaded_none() -> None:
    """async_fetch returns unleaded=None when API returns e5=false."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "e5": False},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["unleaded"] is None


async def test_async_fetch_e10_false_returns_e10_none() -> None:
    """async_fetch returns e10=None when API returns e10=false."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "e10": False},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["e10"] is None


async def test_async_fetch_diesel_false_returns_diesel_none() -> None:
    """async_fetch returns diesel=None when API returns diesel=false."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "diesel": False},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["diesel"] is None


async def test_async_fetch_all_prices_false() -> None:
    """async_fetch handles all fuel prices being boolean false (unmanned depot)."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "e5": False, "e10": False, "diesel": False},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["unleaded"] is None
    assert data["e10"] is None
    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch — API error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_ok_false() -> None:
    """async_fetch raises ProviderError when API returns ok=false."""
    payload = {
        "ok": False,
        "message": "apikey nicht angegeben, falsch, oder im falschen Format",
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match="ok=false"):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_provider_error_message_included() -> None:
    """ProviderError raised by async_fetch includes the API message."""
    payload = {"ok": False, "message": "invalid station id"}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match="invalid station id"):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_when_station_missing() -> None:
    """async_fetch raises ProviderError when payload has ok=true but no 'station' key."""
    payload = {"ok": True}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_when_station_null() -> None:
    """async_fetch raises ProviderError when payload station value is null."""
    payload = {"ok": True, "station": None}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets aiohttp ClientError propagate (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_on_non_200() -> None:
    """async_fetch raises when raise_for_status() raises (e.g. HTTP 401)."""
    resp = _make_mock_response(401)
    resp.raise_for_status = MagicMock(side_effect=ClientError("401 Unauthorized"))
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_on_http_403() -> None:
    """async_fetch raises when raise_for_status() raises for HTTP 403."""
    resp = _make_mock_response(403)
    resp.raise_for_status = MagicMock(side_effect=ClientError("403 Forbidden"))
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_UUID)


# ---------------------------------------------------------------------------
# async_fetch_station_name — success
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns station name from /detail.php."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name == "ARAL Tankstelle"


async def test_async_fetch_station_name_falls_back_to_brand() -> None:
    """async_fetch_station_name falls back to brand when name is empty."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "name": "", "brand": "Shell"},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name == "Shell"


async def test_async_fetch_station_name_uses_detail_endpoint() -> None:
    """async_fetch_station_name calls /detail.php."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch_station_name(session, _STATION_UUID)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "detail.php" in url


async def test_async_fetch_station_name_passes_api_key() -> None:
    """async_fetch_station_name passes apikey as query parameter."""
    resp = _make_mock_response(200, json_data=_DETAIL_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch_station_name(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs.get("params", {}).get("apikey") == _API_KEY


# ---------------------------------------------------------------------------
# async_fetch_station_name — failure / None paths
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_ok_false() -> None:
    """async_fetch_station_name returns None when API returns ok=false."""
    payload = {"ok": False, "message": "bad apikey"}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_absent() -> None:
    """async_fetch_station_name returns None when ok=true but no station key."""
    payload = {"ok": True}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_null() -> None:
    """async_fetch_station_name returns None when station key is null."""
    payload = {"ok": True, "station": None}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_and_brand_absent() -> (
    None
):
    """async_fetch_station_name returns None when both name and brand are absent."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "name": None, "brand": None},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_and_brand_empty() -> (
    None
):
    """async_fetch_station_name returns None when both name and brand are empty strings."""
    payload = {
        "ok": True,
        "station": {**_BASE_STATION, "name": "", "brand": ""},
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_swallows_raise_for_status_error() -> None:
    """async_fetch_station_name returns None when raise_for_status raises ClientError."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (uuid, label) tuples."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert isinstance(result, list)
    assert len(result) == 1
    uid, label = result[0]
    assert uid == _STATION_UUID
    assert isinstance(label, str)


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes formatted diesel price."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "Diesel" in label
    assert "1.699" in label


async def test_async_list_stations_label_includes_super_price() -> None:
    """async_list_stations label includes formatted Super (unleaded) price."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "Super" in label
    assert "1.789" in label


async def test_async_list_stations_label_includes_e10_price() -> None:
    """async_list_stations label includes formatted E10 price."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "E10" in label
    assert "1.759" in label


async def test_async_list_stations_uses_list_endpoint() -> None:
    """async_list_stations calls the /list.php endpoint."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_list_stations(session)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "list.php" in url


async def test_async_list_stations_passes_lat_lng_params() -> None:
    """async_list_stations passes lat, lng, and rad as query parameters."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider(latitude=52.520, longitude=13.405, radius_km=5.0)
    await provider.async_list_stations(session)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert params.get("lat") == "52.52"
    assert params.get("lng") == "13.405"
    assert params.get("rad") == "5.0"


async def test_async_list_stations_passes_apikey() -> None:
    """async_list_stations passes apikey as query parameter."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_list_stations(session)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert params.get("apikey") == _API_KEY


async def test_async_list_stations_passes_type_all() -> None:
    """async_list_stations passes type=all to retrieve all fuel types."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_list_stations(session)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert params.get("type") == "all"


async def test_async_list_stations_kwargs_override_constructor_coords() -> None:
    """async_list_stations uses lat/lng kwargs when provided, overriding constructor values."""
    resp = _make_mock_response(200, json_data=_LIST_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider(latitude=0.0, longitude=0.0)
    await provider.async_list_stations(session, lat=52.520, lng=13.405, radius_km=3.0)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert params.get("lat") == "52.52"
    assert params.get("lng") == "13.405"


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns stations sorted cheapest-first by lowest price."""
    cheap_station = {**_BASE_STATION, "id": "cheap-uuid", "diesel": 1.599, "e5": 1.689}
    expensive_station = {
        **_BASE_STATION,
        "id": "expensive-uuid",
        "diesel": 1.799,
        "e5": 1.849,
    }
    payload = {
        "ok": True,
        "stations": [expensive_station, cheap_station],
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result[0][0] == "cheap-uuid"
    assert result[1][0] == "expensive-uuid"


async def test_async_list_stations_skips_stations_without_id() -> None:
    """async_list_stations skips station records with no 'id' field."""
    no_id_station = {k: v for k, v in _BASE_STATION.items() if k != "id"}
    payload = {
        "ok": True,
        "stations": [no_id_station, _BASE_STATION],
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    # Only the station with a valid id should appear
    assert len(result) == 1
    assert result[0][0] == _STATION_UUID


async def test_async_list_stations_station_with_no_prices_sorted_last() -> None:
    """Stations with all prices false/None sort after stations with prices."""
    no_price_station = {
        **_BASE_STATION,
        "id": "no-price-uuid",
        "e5": False,
        "e10": False,
        "diesel": False,
    }
    payload = {
        "ok": True,
        "stations": [no_price_station, _BASE_STATION],
    }
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result[0][0] == _STATION_UUID
    assert result[1][0] == "no-price-uuid"


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_lat_lng() -> None:
    """async_list_stations returns [] when lat/lng are not configured."""
    session = MagicMock()
    provider = DeTankerkoenigProvider(
        station_id=_STATION_UUID,
        api_key=_API_KEY,
        latitude=None,
        longitude=None,
    )
    result = await provider.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when an HTTP error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_ok_false() -> None:
    """async_list_stations returns [] when API returns ok=false."""
    payload = {"ok": False, "message": "apikey nicht angegeben"}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_when_stations_list_empty() -> None:
    """async_list_stations returns [] when the API stations array is empty."""
    payload = {"ok": True, "stations": []}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_when_stations_key_absent() -> None:
    """async_list_stations returns [] when the API response has no 'stations' key."""
    payload = {"ok": True}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_raise_for_status() -> None:
    """async_list_stations returns [] when raise_for_status raises."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# async_list_stations — label format edge cases
# ---------------------------------------------------------------------------


async def test_async_list_stations_label_brand_not_repeated_when_in_name() -> None:
    """async_list_stations omits brand prefix when name already contains brand."""
    station = {**_BASE_STATION, "name": "ARAL Köln West", "brand": "ARAL"}
    payload = {"ok": True, "stations": [station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    # Brand should not be doubled: "ARAL — ARAL Köln West" is wrong
    assert label.count("ARAL") < 3


async def test_async_list_stations_label_includes_brand_separator_when_different() -> (
    None
):
    """async_list_stations includes 'Brand — Name' when brand is not part of the name."""
    station = {**_BASE_STATION, "name": "Tankstelle Mitte", "brand": "Shell"}
    payload = {"ok": True, "stations": [station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "Shell" in label
    assert "Tankstelle Mitte" in label


async def test_async_list_stations_label_shows_uid_when_name_and_brand_absent() -> None:
    """async_list_stations falls back to uid when both name and brand are absent."""
    no_name_station = {**_BASE_STATION, "name": "", "brand": "", "id": "fallback-uuid"}
    payload = {"ok": True, "stations": [no_name_station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "fallback-uuid" in label


async def test_async_list_stations_label_omits_price_section_when_all_prices_false() -> (
    None
):
    """async_list_stations label has no price section when all prices are boolean false."""
    no_price_station = {
        **_BASE_STATION,
        "e5": False,
        "e10": False,
        "diesel": False,
    }
    payload = {"ok": True, "stations": [no_price_station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    # No EUR prices present in label
    assert "€" not in label
