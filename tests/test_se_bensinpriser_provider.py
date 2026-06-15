"""Tests for SEBensinpriserProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.se_bensinpriser import (
    SEBensinpriserProvider,
    _DATA_URL,
    _HEADERS,
    _find_station,
    _parse_price,
    _parse_station,
)

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_STATION_ID = "13"

_BASE_STATION: dict = {
    "id": 13,
    "lat": 57.92975,
    "lng": 12.5553,
    "company": "St1",
    "address": "Götaplan / Järngatan",
    "commune": "Alingsås",
    "county": "Västra Götalands län",
    "link": "/station/vastra-gotalands-lan/alingsas/gotaplan-jarngatan",
    "price95": 17.54,
    "priceDiesel": 19.84,
    "priceEtanol": 14.39,
    "priceBiodiesel": None,
    "countyLink": "vastra-gotalands-lan",
    "communeLink": "alingsas",
    "companyLink": "st1",
}

_NO_PRICE_STATION: dict = {
    **_BASE_STATION,
    "id": 99,
    "price95": None,
    "priceDiesel": None,
    "priceEtanol": None,
}

_NEARBY_STATION: dict = {
    **_BASE_STATION,
    "id": 200,
    "lat": 57.930,  # ~0.006 degrees away — within any reasonable radius
    "lng": 12.556,
    "price95": 17.20,
    "priceDiesel": 19.50,
}

_FAR_STATION: dict = {
    **_BASE_STATION,
    "id": 300,
    "lat": 69.0,  # Northern Sweden — far from Stockholm
    "lng": 18.0,
    "price95": 16.00,
}

_DATASET: list[dict] = [_BASE_STATION]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: object = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else [])
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _make_provider(
    station_id: str = _STATION_ID,
    latitude: float | None = 57.929,
    longitude: float | None = 12.555,
    radius_km: float | None = 10.0,
) -> SEBensinpriserProvider:
    return SEBensinpriserProvider(
        station_id=station_id,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """SEBensinpriserProvider declares COUNTRY='SE'."""
    assert SEBensinpriserProvider.COUNTRY == "SE"


def test_provider_key() -> None:
    """SEBensinpriserProvider declares PROVIDER_KEY='se_bensinpriser'."""
    assert SEBensinpriserProvider.PROVIDER_KEY == "se_bensinpriser"


def test_provider_label_contains_sweden() -> None:
    """SEBensinpriserProvider LABEL mentions Sweden."""
    assert (
        "Sweden" in SEBensinpriserProvider.LABEL or "SE" in SEBensinpriserProvider.LABEL
    )


def test_provider_label_contains_bensinpriser() -> None:
    """SEBensinpriserProvider LABEL mentions Bensinpriser."""
    assert "Bensinpriser" in SEBensinpriserProvider.LABEL


def test_provider_config_mode_is_station_id() -> None:
    """SEBensinpriserProvider uses CONFIG_MODE='station_id'."""
    assert SEBensinpriserProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """SEBensinpriserProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert SEBensinpriserProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """SEBensinpriserProvider POLL_INTERVAL_SECONDS is 3600 (1 hour)."""
    assert SEBensinpriserProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_does_not_require_api_key() -> None:
    """SEBensinpriserProvider does not require an API key."""
    assert SEBensinpriserProvider.REQUIRES_API_KEY is False


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (petrol 95/E10)."""
    assert "unleaded" in SEBensinpriserProvider.CAPABILITIES


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in SEBensinpriserProvider.CAPABILITIES


def test_provider_capabilities_include_e85() -> None:
    """CAPABILITIES includes 'e85' (etanol)."""
    assert "e85" in SEBensinpriserProvider.CAPABILITIES


def test_provider_capabilities_include_identity_fields() -> None:
    """CAPABILITIES includes station identity fields."""
    caps = SEBensinpriserProvider.CAPABILITIES
    for key in ("name", "brand", "address", "county", "latitude", "longitude"):
        assert key in caps, f"Missing capability: {key}"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = SEBensinpriserProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = _make_provider(station_id="42")
    assert provider._station_id == "42"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude and longitude."""
    provider = _make_provider(latitude=59.33, longitude=18.07)
    assert provider._latitude == pytest.approx(59.33)
    assert provider._longitude == pytest.approx(18.07)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=5.0)
    assert provider._radius_km == pytest.approx(5.0)


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = SEBensinpriserProvider(station_id="1")
    assert provider._radius_km == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Headers and URL constants
# ---------------------------------------------------------------------------


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_headers_user_agent_is_homeassistant() -> None:
    """_HEADERS User-Agent identifies as HomeAssistant."""
    assert "HomeAssistant" in _HEADERS["User-Agent"]


def test_data_url_points_to_bensinpriser() -> None:
    """_DATA_URL targets bensinpriser.nu."""
    assert "bensinpriser.nu" in _DATA_URL
    assert _DATA_URL.startswith("https://")
    assert "/karta/data" in _DATA_URL


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_valid_float() -> None:
    """_parse_price returns a rounded float for a valid price."""
    assert _parse_price(17.54) == pytest.approx(17.54)


def test_parse_price_rounds_to_three_decimals() -> None:
    """_parse_price rounds to 3 decimal places."""
    assert _parse_price(17.5401) == pytest.approx(17.54, rel=1e-3)


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None when value is None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None for zero."""
    assert _parse_price(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price(-5.0) is None


def test_parse_price_returns_none_for_string_garbage() -> None:
    """_parse_price returns None for non-numeric string."""
    assert _parse_price("saknas") is None


def test_parse_price_parses_numeric_string() -> None:
    """_parse_price parses a numeric string."""
    assert _parse_price("19.84") == pytest.approx(19.84)


def test_parse_price_does_not_divide_by_100() -> None:
    """_parse_price does NOT apply any /100 conversion — prices are SEK/litre already."""
    result = _parse_price(17.54)
    assert result is not None
    assert result > 10.0  # Swedish prices are ~17 SEK/litre


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_maps_price95_to_unleaded() -> None:
    """_parse_station maps price95 to 'unleaded'."""
    result = _parse_station(_BASE_STATION)
    assert result["unleaded"] == pytest.approx(17.54)


def test_parse_station_maps_price_diesel() -> None:
    """_parse_station maps priceDiesel to 'diesel'."""
    result = _parse_station(_BASE_STATION)
    assert result["diesel"] == pytest.approx(19.84)


def test_parse_station_maps_price_etanol_to_e85() -> None:
    """_parse_station maps priceEtanol to 'e85'."""
    result = _parse_station(_BASE_STATION)
    assert result["e85"] == pytest.approx(14.39)


def test_parse_station_null_price95_is_none() -> None:
    """_parse_station returns unleaded=None when price95 is null."""
    result = _parse_station({**_BASE_STATION, "price95": None})
    assert result["unleaded"] is None


def test_parse_station_null_diesel_is_none() -> None:
    """_parse_station returns diesel=None when priceDiesel is null."""
    result = _parse_station({**_BASE_STATION, "priceDiesel": None})
    assert result["diesel"] is None


def test_parse_station_company_maps_to_name_and_brand() -> None:
    """_parse_station maps 'company' field to both 'name' and 'brand'."""
    result = _parse_station(_BASE_STATION)
    assert result["name"] == "St1"
    assert result["brand"] == "St1"


def test_parse_station_address_field() -> None:
    """_parse_station maps 'address' field correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["address"] == "Götaplan / Järngatan"


def test_parse_station_county_field() -> None:
    """_parse_station maps 'county' field correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["county"] == "Västra Götalands län"


def test_parse_station_latitude_and_longitude() -> None:
    """_parse_station maps lat/lng to latitude/longitude."""
    result = _parse_station(_BASE_STATION)
    assert result["latitude"] == pytest.approx(57.92975)
    assert result["longitude"] == pytest.approx(12.5553)


def test_parse_station_null_lat_lng_returns_none() -> None:
    """_parse_station returns None for latitude/longitude when lat/lng are null."""
    result = _parse_station({**_BASE_STATION, "lat": None, "lng": None})
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_invalid_lat_lng_returns_none() -> None:
    """_parse_station returns None for latitude/longitude on unparseable strings."""
    result = _parse_station({**_BASE_STATION, "lat": "n/a", "lng": "n/a"})
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_website_built_from_link() -> None:
    """_parse_station builds a full website URL from the 'link' field."""
    result = _parse_station(_BASE_STATION)
    assert result["website"] is not None
    assert "bensinpriser.nu" in result["website"]
    assert result["website"].startswith("https://")


def test_parse_station_website_none_when_link_absent() -> None:
    """_parse_station returns website=None when 'link' field is absent."""
    station = {k: v for k, v in _BASE_STATION.items() if k != "link"}
    result = _parse_station(station)
    assert result["website"] is None


def test_parse_station_source_station_id_is_string() -> None:
    """_parse_station stores source_station_id as a string."""
    result = _parse_station(_BASE_STATION)
    assert result["source_station_id"] == "13"
    assert isinstance(result["source_station_id"], str)


def test_parse_station_lastupdated_is_none() -> None:
    """_parse_station returns lastupdated=None (no per-price timestamps in API)."""
    result = _parse_station(_BASE_STATION)
    assert result["lastupdated"] is None


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station whose id matches station_id."""
    result = _find_station([_BASE_STATION], "13")
    assert result is not None
    assert result["id"] == 13


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station matches station_id."""
    result = _find_station([_BASE_STATION], "9999")
    assert result is None


def test_find_station_returns_none_for_empty_list() -> None:
    """_find_station returns None for an empty list."""
    assert _find_station([], "13") is None


def test_find_station_matches_by_string_id() -> None:
    """_find_station matches integer API id against string station_id."""
    station = {**_BASE_STATION, "id": 42}
    result = _find_station([station], "42")
    assert result is not None
    assert result["id"] == 42


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "St1"
    assert data["brand"] == "St1"


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch returns correct unleaded (price95) price."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(17.54)


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch returns correct diesel price."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(19.84)


async def test_async_fetch_success_e85_price() -> None:
    """async_fetch returns correct e85 (etanol) price."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["e85"] == pytest.approx(14.39)


async def test_async_fetch_success_address_populated() -> None:
    """async_fetch returns address from station record."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["address"] == "Götaplan / Järngatan"


async def test_async_fetch_success_coordinates_populated() -> None:
    """async_fetch populates latitude and longitude."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(57.92975)
    assert data["longitude"] == pytest.approx(12.5553)


async def test_async_fetch_calls_karta_data_endpoint() -> None:
    """async_fetch calls the /karta/data endpoint."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_ID)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "karta/data" in url


async def test_async_fetch_sends_headers() -> None:
    """async_fetch passes _HEADERS on the GET request."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "User-Agent" in headers


async def test_async_fetch_prices_not_divided_by_100() -> None:
    """async_fetch prices remain in SEK/litre — no /100 conversion applied."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    # Swedish prices are around 17–20 SEK — must not be divided to < 1
    assert data["unleaded"] is not None
    assert data["unleaded"] > 10.0


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station_id is not in the dataset."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider(station_id="9999")

    with pytest.raises(ProviderError, match="9999"):
        await provider.async_fetch(session, "9999")


async def test_async_fetch_raises_provider_error_for_non_array_response() -> None:
    """async_fetch raises ProviderError when the API returns a non-array JSON value."""
    resp = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match="unexpected format"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets aiohttp ClientError propagate for coordinator to handle."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_http_error() -> None:
    """async_fetch raises when raise_for_status() raises (e.g. HTTP 503)."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_company_and_address() -> None:
    """async_fetch_station_name returns 'Company — Address' when both are present."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is not None
    assert "St1" in name


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station_id is not in the dataset."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider(station_id="9999")
    name = await provider.async_fetch_station_name(session, "9999")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_http_error() -> None:
    """async_fetch_station_name returns None when raise_for_status raises."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (str, str) tuples."""
    # Centre near the station; radius large enough to include it
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider(latitude=57.929, longitude=12.555, radius_km=5.0)
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert isinstance(result, list)
    assert len(result) >= 1
    uid, label = result[0]
    assert isinstance(uid, str)
    assert isinstance(label, str)


async def test_async_list_stations_label_contains_price95() -> None:
    """async_list_stations label includes the petrol 95 price."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    _, label = result[0]
    assert "17.54" in label or "95" in label


async def test_async_list_stations_label_contains_diesel_price() -> None:
    """async_list_stations label includes the diesel price."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    _, label = result[0]
    assert "Diesel" in label or "19.84" in label


async def test_async_list_stations_station_id_is_string() -> None:
    """async_list_stations returns station id as a string."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    uid, _ = result[0]
    assert uid == "13"


async def test_async_list_stations_filters_by_radius() -> None:
    """async_list_stations excludes stations outside the radius."""
    dataset = [_BASE_STATION, _FAR_STATION]
    resp = _make_mock_response(200, json_data=dataset)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "13" in ids  # local station included
    assert "300" not in ids  # far station excluded


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts stations cheapest-first by price95."""
    cheap = {**_BASE_STATION, "id": 1, "lat": 57.929, "lng": 12.555, "price95": 16.00}
    expensive = {
        **_BASE_STATION,
        "id": 2,
        "lat": 57.929,
        "lng": 12.555,
        "price95": 18.50,
    }
    resp = _make_mock_response(200, json_data=[expensive, cheap])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result[0][0] == "1"  # cheapest first
    assert result[1][0] == "2"  # more expensive second


async def test_async_list_stations_no_price_station_sorted_last() -> None:
    """Stations with no prices sort after stations with prices."""
    no_price = {
        **_BASE_STATION,
        "id": 50,
        "lat": 57.929,
        "lng": 12.555,
        "price95": None,
        "priceDiesel": None,
    }
    with_price = {
        **_BASE_STATION,
        "id": 51,
        "lat": 57.929,
        "lng": 12.555,
        "price95": 17.50,
    }
    resp = _make_mock_response(200, json_data=[no_price, with_price])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result[0][0] == "51"  # station with price first
    assert result[1][0] == "50"  # no-price station last


async def test_async_list_stations_kwargs_override_constructor_coords() -> None:
    """async_list_stations uses lat/lng kwargs instead of constructor values."""
    resp = _make_mock_response(200, json_data=_DATASET)
    session = _make_session(resp)

    # Constructor has dummy coords far away; kwargs provide correct coords
    provider = _make_provider(latitude=0.0, longitude=0.0)
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "13" in ids


async def test_async_list_stations_skips_stations_with_null_lat_lng() -> None:
    """async_list_stations skips stations whose lat or lng is null."""
    null_lat = {**_BASE_STATION, "id": 77, "lat": None, "lng": 12.555}
    resp = _make_mock_response(200, json_data=[null_lat, _BASE_STATION])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "77" not in ids  # null-lat station skipped
    assert "13" in ids


async def test_async_list_stations_skips_stations_with_null_id() -> None:
    """async_list_stations skips stations whose 'id' is null."""
    null_id = {**_BASE_STATION, "id": None}
    resp = _make_mock_response(200, json_data=[null_id, _BASE_STATION])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "None" not in ids  # null-id station must be filtered out
    assert "13" in ids  # valid station must be present


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_lat_lng() -> None:
    """async_list_stations returns [] without calling the API when lat/lng absent."""
    session = MagicMock()
    provider = SEBensinpriserProvider(station_id=_STATION_ID)

    result = await provider.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=57.929, lng=12.555)

    assert result == []


async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns [] when raise_for_status raises."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=57.929, lng=12.555)

    assert result == []


async def test_async_list_stations_returns_empty_on_non_array_response() -> None:
    """async_list_stations returns [] when the API returns a non-array JSON value."""
    resp = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=57.929, lng=12.555)

    assert result == []


async def test_async_list_stations_returns_empty_for_empty_dataset() -> None:
    """async_list_stations returns [] when the API dataset is empty."""
    resp = _make_mock_response(200, json_data=[])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=57.929, lng=12.555)

    assert result == []


async def test_async_list_stations_returns_empty_when_all_stations_out_of_radius() -> (
    None
):
    """async_list_stations returns [] when no stations fall within the radius."""
    resp = _make_mock_response(200, json_data=[_FAR_STATION])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=1.0
    )

    assert result == []


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """SEBensinpriserProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "se_bensinpriser" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["se_bensinpriser"] is SEBensinpriserProvider


# ---------------------------------------------------------------------------
# async_fetch_station_name — name-only path (line 296) and generic exception
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_when_address_absent() -> None:
    """async_fetch_station_name returns just the company name when address is empty (line 296)."""
    station_no_address = {**_BASE_STATION, "address": ""}
    resp = _make_mock_response(200, json_data=[station_no_address])
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "St1"


async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None when _fetch_all_stations raises a non-aiohttp error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ValueError("json decode error"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — invalid lat/lng strings (lines 348-349)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_stations_with_invalid_lat_string() -> None:
    """async_list_stations skips stations whose lat is an unparseable string."""
    bad_lat = {**_BASE_STATION, "id": 55, "lat": "n/a", "lng": 12.555}
    resp = _make_mock_response(200, json_data=[bad_lat, _BASE_STATION])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "55" not in ids  # invalid-lat station skipped
    assert "13" in ids


async def test_async_list_stations_skips_stations_with_invalid_lng_string() -> None:
    """async_list_stations skips stations whose lng is an unparseable string."""
    bad_lng = {**_BASE_STATION, "id": 56, "lat": 57.929, "lng": "bad"}
    resp = _make_mock_response(200, json_data=[bad_lng, _BASE_STATION])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert "56" not in ids  # invalid-lng station skipped
    assert "13" in ids


# ---------------------------------------------------------------------------
# async_list_stations — display name fallbacks (lines 370-375)
# ---------------------------------------------------------------------------


async def test_async_list_stations_display_name_company_only() -> None:
    """async_list_stations uses company alone when address is absent (line 371)."""
    no_address = {
        **_BASE_STATION,
        "id": 60,
        "company": "CircleK",
        "address": "",
        "commune": "",
    }
    resp = _make_mock_response(200, json_data=[no_address])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    _, label = result[0]
    assert "CircleK" in label
    assert "—" not in label.split("CircleK")[0]  # company used directly, not compound


async def test_async_list_stations_display_name_address_only() -> None:
    """async_list_stations uses address alone when company is absent (line 373)."""
    no_company = {
        **_BASE_STATION,
        "id": 61,
        "company": "",
        "address": "Storgatan 1",
        "commune": "",
    }
    resp = _make_mock_response(200, json_data=[no_company])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    _, label = result[0]
    assert "Storgatan 1" in label


async def test_async_list_stations_display_name_falls_back_to_uid() -> None:
    """async_list_stations uses uid when both company and address are absent (line 375)."""
    no_name = {
        **_BASE_STATION,
        "id": 62,
        "company": "",
        "address": "",
        "commune": "",
    }
    resp = _make_mock_response(200, json_data=[no_name])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=57.929, lng=12.555, radius_km=5.0
    )

    assert result
    uid, label = result[0]
    assert uid == "62"
    assert label.startswith("62")
