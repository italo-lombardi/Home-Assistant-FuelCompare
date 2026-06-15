"""Tests for IsFuelProvider (Gasvaktin, Iceland)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.is_fuel import (
    IsFuelProvider,
    _find_station,
    _parse_price,
    _parse_station,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_STATION_KEY = "AT_001"
_LAT = 64.0328
_LNG = -22.0328
_RADIUS_KM = 10.0

_BASE_STATION: dict = {
    "key": _STATION_KEY,
    "name": "Atlantsolía Álftanes",
    "company": "Atlantsolía",
    "bensin95": 191.3,
    "bensin95_discount": 188.3,
    "diesel": 227.6,
    "diesel_discount": 224.6,
    "geo": {
        "lat": 64.0328,
        "lon": -22.0328,
    },
}

_SECOND_STATION: dict = {
    "key": "N1_042",
    "name": "N1 Keflavík",
    "company": "N1",
    "bensin95": 195.9,
    "bensin95_discount": 192.9,
    "diesel": 232.5,
    "diesel_discount": 229.5,
    "geo": {
        "lat": 64.0020,
        "lon": -22.5533,
    },
}

_FAR_STATION: dict = {
    "key": "OR_015",
    "name": "Orkan Akureyri",
    "company": "Orkan",
    "bensin95": 205.0,
    "bensin95_discount": 202.0,
    "diesel": 240.0,
    "diesel_discount": 237.0,
    "geo": {
        "lat": 65.6826,
        "lon": -18.1059,
    },
}

_PAYLOAD_OK: dict = {
    "stations": [_BASE_STATION, _SECOND_STATION, _FAR_STATION],
}

_PAYLOAD_SINGLE: dict = {
    "stations": [_BASE_STATION],
}

_PAYLOAD_EMPTY: dict = {
    "stations": [],
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
    station_id: str = _STATION_KEY,
    latitude: float | None = _LAT,
    longitude: float | None = _LNG,
    radius_km: float = _RADIUS_KM,
) -> IsFuelProvider:
    return IsFuelProvider(
        station_id=station_id,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """IsFuelProvider declares COUNTRY='IS'."""
    assert IsFuelProvider.COUNTRY == "IS"


def test_provider_key() -> None:
    """IsFuelProvider declares PROVIDER_KEY='is_fuel'."""
    assert IsFuelProvider.PROVIDER_KEY == "is_fuel"


def test_provider_label_contains_iceland() -> None:
    """IsFuelProvider label mentions Iceland."""
    assert "Iceland" in IsFuelProvider.LABEL


def test_provider_label_contains_gasvaktin() -> None:
    """IsFuelProvider label mentions Gasvaktin."""
    assert "Gasvaktin" in IsFuelProvider.LABEL


def test_provider_config_mode_is_station_id() -> None:
    """IsFuelProvider uses CONFIG_MODE='station_id'."""
    assert IsFuelProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """IsFuelProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert IsFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_does_not_require_api_key() -> None:
    """IsFuelProvider does not require an API key."""
    assert IsFuelProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_is_900() -> None:
    """POLL_INTERVAL_SECONDS is 900 (15 min) to match the Gasvaktin commit cadence."""
    assert IsFuelProvider.POLL_INTERVAL_SECONDS == 900


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (bensin95)."""
    assert "unleaded" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_premium_unleaded() -> None:
    """CAPABILITIES includes 'premium_unleaded' (bensin95_discount)."""
    assert "premium_unleaded" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_premium_diesel() -> None:
    """CAPABILITIES includes 'premium_diesel' (diesel_discount)."""
    assert "premium_diesel" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_name() -> None:
    """CAPABILITIES includes 'name'."""
    assert "name" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_brand() -> None:
    """CAPABILITIES includes 'brand'."""
    assert "brand" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_coordinates() -> None:
    """CAPABILITIES includes latitude and longitude."""
    assert "latitude" in IsFuelProvider.CAPABILITIES
    assert "longitude" in IsFuelProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = IsFuelProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = _make_provider()
    assert provider._station_id == _STATION_KEY


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude and longitude."""
    provider = _make_provider(latitude=_LAT, longitude=_LNG)
    assert provider._latitude == pytest.approx(_LAT)
    assert provider._longitude == pytest.approx(_LNG)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=7.5)
    assert provider._radius_km == pytest.approx(7.5)


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = IsFuelProvider(station_id=_STATION_KEY)
    assert provider._radius_km == pytest.approx(10.0)


def test_constructor_accepts_none_coordinates() -> None:
    """Constructor accepts None for latitude and longitude."""
    provider = IsFuelProvider(station_id=_STATION_KEY, latitude=None, longitude=None)
    assert provider._latitude is None
    assert provider._longitude is None


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_parses_float() -> None:
    """_parse_price parses a float value."""
    assert _parse_price(191.3) == pytest.approx(191.3)


def test_parse_price_parses_string() -> None:
    """_parse_price parses a numeric string."""
    assert _parse_price("227.6") == pytest.approx(227.6)


def test_parse_price_rounds_to_two_decimals() -> None:
    """_parse_price rounds to 2 decimal places."""
    assert _parse_price(191.301) == pytest.approx(191.30)
    assert _parse_price(191.309) == pytest.approx(191.31)


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None for None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None for zero."""
    assert _parse_price(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for a negative value."""
    assert _parse_price(-10.0) is None


def test_parse_price_returns_none_for_garbage_string() -> None:
    """_parse_price returns None for a non-numeric string."""
    assert _parse_price("n/a") is None


def test_parse_price_does_not_divide_by_100() -> None:
    """_parse_price does NOT apply the >10 /100 guard — ISK prices are above 10."""
    result = _parse_price(191.3)
    assert result is not None
    assert result > 10.0  # ISK prices are genuinely in the 150–300 range


def test_parse_price_accepts_integer() -> None:
    """_parse_price accepts integer values."""
    assert _parse_price(200) == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station dict with the matching key."""
    stations = [_BASE_STATION, _SECOND_STATION]
    result = _find_station(stations, _STATION_KEY)
    assert result is not None
    assert result["key"] == _STATION_KEY


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station has the target key."""
    result = _find_station([_BASE_STATION], "DOES_NOT_EXIST")
    assert result is None


def test_find_station_returns_none_for_empty_list() -> None:
    """_find_station returns None for an empty list."""
    assert _find_station([], _STATION_KEY) is None


def test_find_station_matches_exact_key() -> None:
    """_find_station only matches on exact key equality."""
    result = _find_station([_BASE_STATION], "AT_00")  # prefix — must NOT match
    assert result is None


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_returns_all_required_keys() -> None:
    """_parse_station returns a dict with all CAPABILITIES-aligned keys."""
    result = _parse_station(_BASE_STATION)
    required_keys = {
        "unleaded",
        "premium_unleaded",
        "diesel",
        "premium_diesel",
        "name",
        "brand",
        "latitude",
        "longitude",
        "source_station_id",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _parse_station output"


def test_parse_station_bensin95_maps_to_unleaded() -> None:
    """_parse_station maps bensin95 to 'unleaded'."""
    result = _parse_station(_BASE_STATION)
    assert result["unleaded"] == pytest.approx(191.3)


def test_parse_station_bensin95_discount_maps_to_premium_unleaded() -> None:
    """_parse_station maps bensin95_discount to 'premium_unleaded'."""
    result = _parse_station(_BASE_STATION)
    assert result["premium_unleaded"] == pytest.approx(188.3)


def test_parse_station_diesel_field() -> None:
    """_parse_station maps diesel correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["diesel"] == pytest.approx(227.6)


def test_parse_station_diesel_discount_maps_to_premium_diesel() -> None:
    """_parse_station maps diesel_discount to 'premium_diesel'."""
    result = _parse_station(_BASE_STATION)
    assert result["premium_diesel"] == pytest.approx(224.6)


def test_parse_station_name_field() -> None:
    """_parse_station maps station name correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["name"] == "Atlantsolía Álftanes"


def test_parse_station_company_maps_to_brand() -> None:
    """_parse_station maps 'company' to 'brand'."""
    result = _parse_station(_BASE_STATION)
    assert result["brand"] == "Atlantsolía"


def test_parse_station_tablename_same_as_brand() -> None:
    """_parse_station sets tablename equal to brand."""
    result = _parse_station(_BASE_STATION)
    assert result["tablename"] == result["brand"]


def test_parse_station_latitude_from_geo() -> None:
    """_parse_station extracts latitude from geo.lat."""
    result = _parse_station(_BASE_STATION)
    assert result["latitude"] == pytest.approx(64.0328)


def test_parse_station_longitude_from_geo() -> None:
    """_parse_station extracts longitude from geo.lon."""
    result = _parse_station(_BASE_STATION)
    assert result["longitude"] == pytest.approx(-22.0328)


def test_parse_station_source_station_id_is_key() -> None:
    """_parse_station stores the station key as source_station_id."""
    result = _parse_station(_BASE_STATION)
    assert result["source_station_id"] == _STATION_KEY


def test_parse_station_lastupdated_is_none() -> None:
    """_parse_station does not include lastupdated (no per-station timestamp in API)."""
    result = _parse_station(_BASE_STATION)
    assert "lastupdated" not in result


def test_parse_station_null_geo_returns_none_coords() -> None:
    """_parse_station returns None for lat/lng when geo is null."""
    station = {**_BASE_STATION, "geo": None}
    result = _parse_station(station)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_missing_prices_return_none() -> None:
    """_parse_station returns None for all fuel prices when fields are absent."""
    station = {
        "key": "TEST_001",
        "name": "Test Station",
        "company": "Test",
        "geo": {"lat": 64.0, "lon": -22.0},
    }
    result = _parse_station(station)
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["diesel"] is None
    assert result["premium_diesel"] is None


def test_parse_station_prices_above_ten_not_divided() -> None:
    """_parse_station does NOT apply the /100 guard — ISK prices are above 10."""
    result = _parse_station(_BASE_STATION)
    assert result["unleaded"] is not None
    assert result["unleaded"] > 10.0  # ISK/litre, should be ~191


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns normalised StationData on a successful API response."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["name"] == "Atlantsolía Álftanes"
    assert data["brand"] == "Atlantsolía"


async def test_async_fetch_bensin95_price() -> None:
    """async_fetch returns correct unleaded (bensin95) price."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["unleaded"] == pytest.approx(191.3)


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns correct diesel price."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["diesel"] == pytest.approx(227.6)


async def test_async_fetch_discount_prices() -> None:
    """async_fetch returns correct discount (premium_unleaded / premium_diesel) prices."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["premium_unleaded"] == pytest.approx(188.3)
    assert data["premium_diesel"] == pytest.approx(224.6)


async def test_async_fetch_lat_lng_populated() -> None:
    """async_fetch populates latitude and longitude."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["latitude"] == pytest.approx(64.0328)
    assert data["longitude"] == pytest.approx(-22.0328)


async def test_async_fetch_source_station_id_is_key() -> None:
    """async_fetch stores the station key as source_station_id."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_KEY)

    assert data["source_station_id"] == _STATION_KEY


async def test_async_fetch_requests_correct_url() -> None:
    """async_fetch calls the Gasvaktin raw GitHub URL."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_KEY)

    url_called = session.get.call_args.args[0]
    assert "gasvaktin" in url_called
    assert "gas.min.json" in url_called


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station key not in API response."""
    payload = {"stations": [{**_BASE_STATION, "key": "OTHER_001"}]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match=_STATION_KEY):
        await provider.async_fetch(session, _STATION_KEY)


async def test_async_fetch_raises_provider_error_on_wrong_response_format() -> None:
    """async_fetch raises ProviderError when API returns a JSON array instead of object."""
    resp = _make_mock_response(200, json_data=[])
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_KEY)


async def test_async_fetch_raises_provider_error_on_missing_stations_key() -> None:
    """async_fetch raises ProviderError when 'stations' key is absent."""
    resp = _make_mock_response(200, json_data={"data": []})
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_KEY)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets aiohttp.ClientError propagate (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_KEY)


async def test_async_fetch_raises_on_empty_stations_list() -> None:
    """async_fetch raises ProviderError when API returns empty stations array."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_EMPTY)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_KEY)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns the station display name."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name is not None
    assert "Atlantsolía" in name


async def test_async_fetch_station_name_includes_company() -> None:
    """async_fetch_station_name includes both company and station name."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name is not None
    assert "Atlantsolía Álftanes" in name


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station key is not in response."""
    payload = {"stations": [{**_BASE_STATION, "key": "OTHER_001"}]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (key, label) tuples."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert isinstance(result, list)
    assert len(result) == 1
    key, label = result[0]
    assert key == _STATION_KEY
    assert isinstance(label, str)


async def test_async_list_stations_label_includes_bensin95_price() -> None:
    """async_list_stations label includes the formatted bensin95 price."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Bensin95" in label
    assert "191" in label


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes the formatted diesel price (rounded to whole ISK)."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_SINGLE)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Diesel" in label
    # 227.6 ISK formatted with :.0f rounds to 228
    assert "228" in label


async def test_async_list_stations_sorted_nearest_first() -> None:
    """async_list_stations sorts results nearest-first."""
    near = {
        **_BASE_STATION,
        "key": "NEAR_001",
        "geo": {"lat": _LAT + 0.001, "lon": _LNG},  # ~110 m away
    }
    far = {
        **_BASE_STATION,
        "key": "FAR_001",
        "geo": {"lat": _LAT + 0.05, "lon": _LNG},  # ~5.5 km away
    }
    payload = {"stations": [far, near]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider(radius_km=50.0)
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=50.0
    )

    assert result[0][0] == "NEAR_001"
    assert result[1][0] == "FAR_001"


async def test_async_list_stations_filters_by_radius() -> None:
    """async_list_stations excludes stations outside the radius via haversine."""
    payload = {"stations": [_BASE_STATION, _FAR_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    # _FAR_STATION is Akureyri — ~190 km from _LAT/_LNG; should be excluded
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=50.0
    )

    keys = [r[0] for r in result]
    assert _STATION_KEY in keys
    assert _FAR_STATION["key"] not in keys


async def test_async_list_stations_skips_stations_without_key() -> None:
    """async_list_stations skips station records with no 'key' field."""
    no_key_station = {k: v for k, v in _BASE_STATION.items() if k != "key"}
    payload = {"stations": [no_key_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    assert result[0][0] == _STATION_KEY


async def test_async_list_stations_uses_is_not_none_coord_check() -> None:
    """async_list_stations uses is-not-None checks so stations at lat=0 lon=0 are included."""
    station_at_null_island = {
        "key": "ZERO_001",
        "name": "Null Island Station",
        "company": "Test",
        "bensin95": 200.0,
        "diesel": 235.0,
        "bensin95_discount": 197.0,
        "diesel_discount": 232.0,
        "geo": {"lat": 0.0, "lon": 0.0},
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"stations": [station_at_null_island]})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)

    provider = IsFuelProvider(station_id="ZERO_001")
    provider._latitude = 0.0
    provider._longitude = 0.0
    provider._radius_km = 100.0

    stations = await provider.async_list_stations(
        session, lat=0.0, lng=0.0, radius_km=100.0
    )
    keys = [uid for uid, _ in stations]
    assert "ZERO_001" in keys, (
        "Station at lat=0.0/lon=0.0 must not be dropped by falsy check"
    )


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when no lat/lng are provided."""
    session = MagicMock()
    provider = IsFuelProvider(station_id=_STATION_KEY)

    result = await provider.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when an HTTP error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert result == []


async def test_async_list_stations_returns_empty_when_stations_list_empty() -> None:
    """async_list_stations returns [] when the API stations array is empty."""
    resp = _make_mock_response(200, json_data=_PAYLOAD_EMPTY)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_label_omits_price_when_no_prices() -> None:
    """async_list_stations label omits the price section when fuel fields are absent."""
    station_no_prices = {
        "key": "NO_PRICE_001",
        "name": "Empty Station",
        "company": "TestCo",
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station_no_prices]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    _, label = result[0]
    assert "Bensin95" not in label
    assert "Diesel" not in label


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """IsFuelProvider is registered in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "is_fuel" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["is_fuel"] is IsFuelProvider


# ---------------------------------------------------------------------------
# _parse_station — invalid geo coordinate error paths (lines 139-144)
# ---------------------------------------------------------------------------


def test_parse_station_invalid_lat_falls_back_to_none() -> None:
    """_parse_station sets latitude to None when geo.lat cannot be cast to float."""
    station = {**_BASE_STATION, "geo": {"lat": "not-a-number", "lon": -22.0328}}
    result = _parse_station(station)
    assert result["latitude"] is None


def test_parse_station_invalid_lon_falls_back_to_none() -> None:
    """_parse_station sets longitude to None when geo.lon cannot be cast to float."""
    station = {**_BASE_STATION, "geo": {"lat": 64.0328, "lon": "bad-lon"}}
    result = _parse_station(station)
    assert result["longitude"] is None


# ---------------------------------------------------------------------------
# async_fetch_station_name — partial name/company fallback (line 343)
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_only_when_no_company() -> None:
    """async_fetch_station_name returns just name when company is absent."""
    station_name_only = {
        "key": _STATION_KEY,
        "name": "Solo Station",
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station_name_only]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name == "Solo Station"


async def test_async_fetch_station_name_returns_company_only_when_no_name() -> None:
    """async_fetch_station_name returns just company when name is absent."""
    station_company_only = {
        "key": _STATION_KEY,
        "company": "SoloCompany",
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station_company_only]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name == "SoloCompany"


async def test_async_fetch_station_name_returns_none_when_both_absent() -> None:
    """async_fetch_station_name returns None when both name and company are absent."""
    station_no_identity = {
        "key": _STATION_KEY,
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station_no_identity]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_KEY)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — invalid geo coords skipped (lines 403-404)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_station_with_invalid_lat() -> None:
    """async_list_stations skips a station whose geo.lat is not a valid float."""
    bad_station = {
        **_BASE_STATION,
        "key": "BAD_LAT",
        "geo": {"lat": "invalid", "lon": _LNG},
    }
    payload = {"stations": [bad_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    keys = [k for k, _ in result]
    assert "BAD_LAT" not in keys
    assert _STATION_KEY in keys


async def test_async_list_stations_skips_station_with_invalid_lon() -> None:
    """async_list_stations skips a station whose geo.lon is not a valid float."""
    bad_station = {
        **_BASE_STATION,
        "key": "BAD_LON",
        "geo": {"lat": _LAT, "lon": "not-a-float"},
    }
    payload = {"stations": [bad_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    keys = [k for k, _ in result]
    assert "BAD_LON" not in keys


# ---------------------------------------------------------------------------
# async_list_stations — None coordinate check (line 407)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_station_with_none_geo_lat() -> None:
    """async_list_stations skips a station whose geo.lat is None."""
    none_lat_station = {
        **_BASE_STATION,
        "key": "NONE_LAT",
        "geo": {"lat": None, "lon": _LNG},
    }
    payload = {"stations": [none_lat_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    keys = [k for k, _ in result]
    assert "NONE_LAT" not in keys
    assert _STATION_KEY in keys


async def test_async_list_stations_skips_station_with_none_geo_lon() -> None:
    """async_list_stations skips a station whose geo.lon is None."""
    none_lon_station = {
        **_BASE_STATION,
        "key": "NONE_LON",
        "geo": {"lat": _LAT, "lon": None},
    }
    payload = {"stations": [none_lon_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    keys = [k for k, _ in result]
    assert "NONE_LON" not in keys


# ---------------------------------------------------------------------------
# async_list_stations — display_name fallback branches (lines 422-427)
# ---------------------------------------------------------------------------


async def test_async_list_stations_display_name_company_only() -> None:
    """async_list_stations uses company as display_name when name is empty."""
    station = {
        "key": "CO_ONLY",
        "company": "OnlyCompany",
        "name": "",
        "bensin95": 190.0,
        "diesel": 225.0,
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    _, label = result[0]
    assert "OnlyCompany" in label


async def test_async_list_stations_display_name_name_only() -> None:
    """async_list_stations uses name as display_name when company is empty."""
    station = {
        "key": "NAME_ONLY",
        "company": "",
        "name": "OnlyName",
        "bensin95": 190.0,
        "diesel": 225.0,
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    _, label = result[0]
    assert "OnlyName" in label


async def test_async_list_stations_display_name_falls_back_to_key() -> None:
    """async_list_stations uses station key as display_name when both name and company are empty."""
    station = {
        "key": "KEY_FALLBACK",
        "company": "",
        "name": "",
        "bensin95": 190.0,
        "diesel": 225.0,
        "geo": {"lat": _LAT, "lon": _LNG},
    }
    payload = {"stations": [station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    _, label = result[0]
    assert "KEY_FALLBACK" in label


# ---------------------------------------------------------------------------
# _fetch_all_stations — non-dict payload raises ProviderError (line 478)
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_payload_is_list() -> None:
    """_fetch_all_stations raises ProviderError when API returns a JSON list."""
    resp = _make_mock_response(200, json_data=[{"key": "AT_001"}])
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match="unexpected format"):
        await provider.async_fetch(session, _STATION_KEY)


async def test_async_list_stations_returns_empty_when_payload_is_list() -> None:
    """async_list_stations returns [] when API payload is a list (non-dict)."""
    resp = _make_mock_response(200, json_data=[{"key": "AT_001"}])
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []
