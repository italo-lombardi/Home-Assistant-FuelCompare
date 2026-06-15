"""Tests for NoDrivstoffProvider (Drivstoffpriser, Norway)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.no_drivstoff import (
    NoDrivstoffProvider,
    _DEFAULT_BASE_URL,
    _HEADERS,
    _display_name,
    _extract_prices,
    _find_station,
    _latest_timestamp,
    _parse_price,
    _parse_station,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_STATION_UUID = "4f6e9a12-1234-4b56-8def-aabbcc001122"
_API_KEY = "firebase-id-token-abc123"
_LAT = 59.9139
_LNG = 10.7522
_RADIUS_KM = 5.0

_BASE_STATION: dict = {
    "id": _STATION_UUID,
    "externalId": "ext-001",
    "name": "Oslo S",
    "provider": "CIRCLE_K",
    "address": "Jernbanetorget 1",
    "city": "Oslo",
    "location": {"lat": 59.9110, "lng": 10.7528},
    "prices": [
        {
            "fuelType": "DIESEL",
            "price": "20.90",
            "registeredAt": "2026-06-14T10:00:00+00:00",
        },
        {
            "fuelType": "GASOLINE_95",
            "price": "21.50",
            "registeredAt": "2026-06-14T09:00:00+00:00",
        },
        {
            "fuelType": "GASOLINE_98",
            "price": "22.10",
            "registeredAt": "2026-06-14T08:00:00+00:00",
        },
    ],
}

_STATIONS_PAYLOAD_OK: dict = {
    "stations": [_BASE_STATION],
}

_STATIONS_PAYLOAD_EMPTY: dict = {
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
    station_id: str = _STATION_UUID,
    api_key: str = _API_KEY,
    latitude: float | None = _LAT,
    longitude: float | None = _LNG,
    radius_km: float = _RADIUS_KM,
    base_url: str = _DEFAULT_BASE_URL,
) -> NoDrivstoffProvider:
    return NoDrivstoffProvider(
        station_id=station_id,
        api_key=api_key,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        base_url=base_url,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """NoDrivstoffProvider declares COUNTRY='NO'."""
    assert NoDrivstoffProvider.COUNTRY == "NO"


def test_provider_key() -> None:
    """NoDrivstoffProvider declares PROVIDER_KEY='no_drivstoff'."""
    assert NoDrivstoffProvider.PROVIDER_KEY == "no_drivstoff"


def test_provider_label_contains_norway() -> None:
    """NoDrivstoffProvider label mentions Norway."""
    assert "Norway" in NoDrivstoffProvider.LABEL


def test_provider_label_contains_drivstoff() -> None:
    """NoDrivstoffProvider label mentions Drivstoffpriser."""
    assert "Drivstoffpriser" in NoDrivstoffProvider.LABEL


def test_provider_config_mode_is_station_id() -> None:
    """NoDrivstoffProvider uses CONFIG_MODE='station_id'."""
    assert NoDrivstoffProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """NoDrivstoffProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert NoDrivstoffProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_api_key() -> None:
    """NoDrivstoffProvider requires an API key (Firebase token)."""
    assert NoDrivstoffProvider.REQUIRES_API_KEY is True


def test_provider_poll_interval_is_3600() -> None:
    """POLL_INTERVAL_SECONDS is 3600 (1 hour) as specified."""
    assert NoDrivstoffProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (GASOLINE_95)."""
    assert "unleaded" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_premium_unleaded() -> None:
    """CAPABILITIES includes 'premium_unleaded' (GASOLINE_98)."""
    assert "premium_unleaded" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_name() -> None:
    """CAPABILITIES includes 'name'."""
    assert "name" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_brand() -> None:
    """CAPABILITIES includes 'brand'."""
    assert "brand" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_address() -> None:
    """CAPABILITIES includes 'address'."""
    assert "address" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_county() -> None:
    """CAPABILITIES includes 'county' (mapped from city)."""
    assert "county" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_coordinates() -> None:
    """CAPABILITIES includes latitude and longitude."""
    assert "latitude" in NoDrivstoffProvider.CAPABILITIES
    assert "longitude" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_lastupdated() -> None:
    """CAPABILITIES includes 'lastupdated'."""
    assert "lastupdated" in NoDrivstoffProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = NoDrivstoffProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


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
    provider = _make_provider(latitude=_LAT, longitude=_LNG)
    assert provider._latitude == pytest.approx(_LAT)
    assert provider._longitude == pytest.approx(_LNG)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=7.5)
    assert provider._radius_km == pytest.approx(7.5)


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = NoDrivstoffProvider(
        station_id=_STATION_UUID,
        api_key=_API_KEY,
    )
    assert provider._radius_km == pytest.approx(10.0)


def test_constructor_base_url_strips_trailing_slash() -> None:
    """Constructor strips trailing slash from base_url."""
    provider = NoDrivstoffProvider(
        station_id=_STATION_UUID,
        api_key=_API_KEY,
        base_url="https://api.example.com/",
    )
    assert not provider._base_url.endswith("/")


def test_constructor_stores_base_url() -> None:
    """Constructor stores custom base_url."""
    url = "https://my-instance.example.com"
    provider = NoDrivstoffProvider(
        station_id=_STATION_UUID,
        api_key=_API_KEY,
        base_url=url,
    )
    assert provider._base_url == url


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
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_parses_decimal_string() -> None:
    """_parse_price parses '20.90' to 20.9."""
    assert _parse_price("20.90") == pytest.approx(20.90)


def test_parse_price_parses_float() -> None:
    """_parse_price parses a float value."""
    assert _parse_price(21.50) == pytest.approx(21.50)


def test_parse_price_rounds_to_two_decimals() -> None:
    """_parse_price rounds to 2 decimal places."""
    assert _parse_price("20.999") == pytest.approx(21.00)


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None for None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None for zero."""
    assert _parse_price(0) is None


def test_parse_price_returns_none_above_100_nok() -> None:
    """_parse_price returns None for implausibly high values (> 100 NOK/L)."""
    assert _parse_price(101) is None
    assert _parse_price(100) == pytest.approx(100.0)


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for a negative value."""
    assert _parse_price(-1.0) is None


def test_parse_price_returns_none_for_garbage_string() -> None:
    """_parse_price returns None for a non-numeric string."""
    assert _parse_price("n/a") is None


def test_parse_price_does_not_divide_by_100() -> None:
    """_parse_price does NOT apply the >10 /100 guard — prices are NOK/litre."""
    result = _parse_price("20.90")
    assert result is not None
    assert result > 10.0  # Norwegian prices are genuinely above 10 NOK/litre


def test_parse_price_accepts_integer() -> None:
    """_parse_price accepts integer values."""
    assert _parse_price(21) == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# _extract_prices
# ---------------------------------------------------------------------------


def test_extract_prices_maps_diesel() -> None:
    """_extract_prices maps DIESEL fuelType to 'diesel' key."""
    prices = _extract_prices([{"fuelType": "DIESEL", "price": "20.90"}])
    assert prices.get("diesel") == pytest.approx(20.90)


def test_extract_prices_maps_gasoline_95_to_unleaded() -> None:
    """_extract_prices maps GASOLINE_95 to 'unleaded'."""
    prices = _extract_prices([{"fuelType": "GASOLINE_95", "price": "21.50"}])
    assert prices.get("unleaded") == pytest.approx(21.50)


def test_extract_prices_maps_gasoline_98_to_premium_unleaded() -> None:
    """_extract_prices maps GASOLINE_98 to 'premium_unleaded'."""
    prices = _extract_prices([{"fuelType": "GASOLINE_98", "price": "22.10"}])
    assert prices.get("premium_unleaded") == pytest.approx(22.10)


def test_extract_prices_skips_unknown_fuel_type() -> None:
    """_extract_prices skips fuel types not in the mapping."""
    prices = _extract_prices([{"fuelType": "HYDROGEN", "price": "99.99"}])
    assert "HYDROGEN" not in prices
    assert len(prices) == 0


def test_extract_prices_returns_none_for_missing_price() -> None:
    """_extract_prices returns None when price field is absent."""
    prices = _extract_prices([{"fuelType": "DIESEL"}])
    assert prices.get("diesel") is None


def test_extract_prices_deduplicates_keeps_first() -> None:
    """_extract_prices keeps only the first occurrence when fuel type appears twice."""
    prices = _extract_prices(
        [
            {"fuelType": "DIESEL", "price": "20.90"},
            {"fuelType": "DIESEL", "price": "19.99"},  # second — should be ignored
        ]
    )
    assert prices.get("diesel") == pytest.approx(20.90)


def test_extract_prices_all_three_fuel_types() -> None:
    """_extract_prices handles all three Norwegian fuel types correctly."""
    prices = _extract_prices(_BASE_STATION["prices"])
    assert prices.get("diesel") == pytest.approx(20.90)
    assert prices.get("unleaded") == pytest.approx(21.50)
    assert prices.get("premium_unleaded") == pytest.approx(22.10)


def test_extract_prices_empty_list() -> None:
    """_extract_prices returns empty dict for empty prices list."""
    assert _extract_prices([]) == {}


# ---------------------------------------------------------------------------
# _latest_timestamp
# ---------------------------------------------------------------------------


def test_latest_timestamp_returns_most_recent() -> None:
    """_latest_timestamp returns the lexicographically largest ISO 8601 string."""
    prices = _BASE_STATION["prices"]
    ts = _latest_timestamp(prices)
    assert ts == "2026-06-14T10:00:00+00:00"


def test_latest_timestamp_returns_none_for_empty() -> None:
    """_latest_timestamp returns None for an empty list."""
    assert _latest_timestamp([]) is None


def test_latest_timestamp_skips_null_timestamps() -> None:
    """_latest_timestamp skips records where registeredAt is null."""
    prices = [
        {"fuelType": "DIESEL", "price": "20.90", "registeredAt": None},
        {
            "fuelType": "GASOLINE_95",
            "price": "21.50",
            "registeredAt": "2026-06-14T09:00:00+00:00",
        },
    ]
    ts = _latest_timestamp(prices)
    assert ts == "2026-06-14T09:00:00+00:00"


# ---------------------------------------------------------------------------
# _display_name
# ---------------------------------------------------------------------------


def test_display_name_combines_brand_and_name() -> None:
    """_display_name prefixes the brand display name before the station name."""
    station = {"name": "Oslo S", "provider": "CIRCLE_K"}
    result = _display_name(station)
    assert "Circle K" in result
    assert "Oslo S" in result


def test_display_name_avoids_duplication() -> None:
    """_display_name does not duplicate the brand when name starts with it."""
    station = {"name": "Circle K Oslo S", "provider": "CIRCLE_K"}
    result = _display_name(station)
    assert result.count("Circle K") == 1


def test_display_name_uses_brand_when_no_name() -> None:
    """_display_name falls back to brand when name is absent."""
    station = {"name": "", "provider": "YX"}
    result = _display_name(station)
    assert "YX" in result


def test_display_name_uses_name_when_no_brand() -> None:
    """_display_name falls back to name when provider is absent."""
    station = {"name": "Moen Bensinstasjon", "provider": ""}
    result = _display_name(station)
    assert "Moen Bensinstasjon" in result


def test_display_name_falls_back_to_id() -> None:
    """_display_name falls back to UUID when both name and provider are absent."""
    station = {"id": "some-uuid", "name": "", "provider": ""}
    result = _display_name(station)
    assert "some-uuid" in result


def test_display_name_maps_all_known_providers() -> None:
    """_display_name maps every ProviderType enum value to a human-readable name."""
    provider_map = {
        "AUTOMAT_1": "Automat1",
        "BEST": "Best",
        "BUNKER_OIL": "Bunker Oil",
        "CIRCLE_K": "Circle K",
        "DRIV": "Driv",
        "ESSO": "Esso",
        "HALTBAKK_EXPRESS": "Haltbakk Express",
        "ST1": "St1",
        "TANKEN": "Tanken",
        "TRONDER_OIL": "Trønder Oil",
        "UNO_X": "Uno-X",
        "YX": "YX",
        "YX_TRUCK": "YX Truck",
    }
    for api_key, display in provider_map.items():
        station = {"id": "x", "name": "", "provider": api_key}
        assert display in _display_name(station), f"Missing display name for {api_key}"


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station dict with the matching UUID."""
    stations = [_BASE_STATION, {**_BASE_STATION, "id": "other-uuid"}]
    result = _find_station(stations, _STATION_UUID)
    assert result is not None
    assert result["id"] == _STATION_UUID


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station has the target UUID."""
    result = _find_station([_BASE_STATION], "does-not-exist")
    assert result is None


def test_find_station_returns_none_for_empty_list() -> None:
    """_find_station returns None for an empty list."""
    assert _find_station([], _STATION_UUID) is None


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_returns_all_required_keys() -> None:
    """_parse_station returns a dict with all CAPABILITIES-aligned keys."""
    result = _parse_station(_BASE_STATION)
    required_keys = {
        "diesel",
        "unleaded",
        "premium_unleaded",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
        "source_station_id",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _parse_station output"


def test_parse_station_diesel_price() -> None:
    """_parse_station returns correct diesel price."""
    result = _parse_station(_BASE_STATION)
    assert result["diesel"] == pytest.approx(20.90)


def test_parse_station_unleaded_price() -> None:
    """_parse_station returns correct unleaded (GASOLINE_95) price."""
    result = _parse_station(_BASE_STATION)
    assert result["unleaded"] == pytest.approx(21.50)


def test_parse_station_premium_unleaded_price() -> None:
    """_parse_station returns correct premium_unleaded (GASOLINE_98) price."""
    result = _parse_station(_BASE_STATION)
    assert result["premium_unleaded"] == pytest.approx(22.10)


def test_parse_station_name_field() -> None:
    """_parse_station maps station name correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["name"] == "Oslo S"


def test_parse_station_brand_maps_provider_enum() -> None:
    """_parse_station maps 'provider' enum to a human-readable brand."""
    result = _parse_station(_BASE_STATION)
    assert result["brand"] == "Circle K"


def test_parse_station_tablename_same_as_brand() -> None:
    """_parse_station sets tablename equal to brand."""
    result = _parse_station(_BASE_STATION)
    assert result["tablename"] == result["brand"]


def test_parse_station_county_maps_from_city() -> None:
    """_parse_station maps 'city' to 'county'."""
    result = _parse_station(_BASE_STATION)
    assert result["county"] == "Oslo"


def test_parse_station_address_field() -> None:
    """_parse_station maps 'address' correctly."""
    result = _parse_station(_BASE_STATION)
    assert result["address"] == "Jernbanetorget 1"


def test_parse_station_latitude_from_location() -> None:
    """_parse_station extracts latitude from location.lat."""
    result = _parse_station(_BASE_STATION)
    assert result["latitude"] == pytest.approx(59.9110)


def test_parse_station_longitude_from_location() -> None:
    """_parse_station extracts longitude from location.lng."""
    result = _parse_station(_BASE_STATION)
    assert result["longitude"] == pytest.approx(10.7528)


def test_parse_station_lastupdated_most_recent() -> None:
    """_parse_station sets lastupdated to the most recent price timestamp."""
    result = _parse_station(_BASE_STATION)
    assert result["lastupdated"] == "2026-06-14T10:00:00+00:00"


def test_parse_station_source_station_id() -> None:
    """_parse_station stores the UUID as source_station_id."""
    result = _parse_station(_BASE_STATION)
    assert result["source_station_id"] == _STATION_UUID


def test_parse_station_null_location_returns_none_coords() -> None:
    """_parse_station returns None for lat/lng when location is null."""
    station = {**_BASE_STATION, "location": None}
    result = _parse_station(station)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_missing_prices_returns_none() -> None:
    """_parse_station returns None for all fuel prices when prices list is empty."""
    station = {**_BASE_STATION, "prices": []}
    result = _parse_station(station)
    assert result["diesel"] is None
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None


def test_parse_station_prices_above_ten_not_divided() -> None:
    """_parse_station does NOT apply the /100 guard — NOK prices are above 10."""
    result = _parse_station(_BASE_STATION)
    assert result["diesel"] is not None
    assert result["diesel"] > 10.0


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns normalised StationData on a successful API response."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["name"] == "Oslo S"
    assert data["brand"] == "Circle K"


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns correct diesel price."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["diesel"] == pytest.approx(20.90)


async def test_async_fetch_unleaded_price() -> None:
    """async_fetch returns correct unleaded price."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["unleaded"] == pytest.approx(21.50)


async def test_async_fetch_premium_unleaded_price() -> None:
    """async_fetch returns correct premium_unleaded price."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["premium_unleaded"] == pytest.approx(22.10)


async def test_async_fetch_lat_lng_populated() -> None:
    """async_fetch populates latitude and longitude from station data."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["latitude"] == pytest.approx(59.9110)
    assert data["longitude"] == pytest.approx(10.7528)


async def test_async_fetch_county_populated() -> None:
    """async_fetch maps city to county."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _STATION_UUID)

    assert data["county"] == "Oslo"


async def test_async_fetch_sends_bearer_token() -> None:
    """async_fetch passes api_key as Authorization: Bearer header."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert headers.get("Authorization") == f"Bearer {_API_KEY}"


async def test_async_fetch_passes_distance_in_metres() -> None:
    """async_fetch converts radius_km to metres for the 'distance' query param."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider(radius_km=5.0)
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    # 5.0 km = 5000.0 m
    assert float(params.get("distance", 0)) == pytest.approx(5000.0)


async def test_async_fetch_passes_lat_lng_params() -> None:
    """async_fetch passes lat and lng query parameters."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider(latitude=_LAT, longitude=_LNG)
    await provider.async_fetch(session, _STATION_UUID)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert float(params.get("lat", 0)) == pytest.approx(_LAT)
    assert float(params.get("lng", 0)) == pytest.approx(_LNG)


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station UUID not in API response."""
    payload = {"stations": [{**_BASE_STATION, "id": "different-uuid"}]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match=_STATION_UUID):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_when_no_coordinates() -> None:
    """async_fetch raises ProviderError when lat/lng are not configured."""
    session = MagicMock()
    provider = NoDrivstoffProvider(station_id=_STATION_UUID, api_key=_API_KEY)

    with pytest.raises(ProviderError, match="latitude"):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_on_401() -> None:
    """async_fetch raises ProviderError when API returns HTTP 401."""
    resp = _make_mock_response(401)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match=r"api_key|API key|api key"):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_on_403() -> None:
    """async_fetch raises ProviderError when API returns HTTP 403."""
    resp = _make_mock_response(403)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match=r"api_key|API key|api key"):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_provider_error_on_client_error() -> None:
    """async_fetch raises ProviderError when a network error prevents the API call.

    Unlike some other providers that let ClientError propagate, NoDrivstoffProvider
    wraps network failures in ProviderError so the coordinator's stale-retention
    logic triggers correctly on transient connection issues.
    """
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


async def test_async_fetch_raises_on_empty_stations_list() -> None:
    """async_fetch raises ProviderError when API returns empty stations list."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_EMPTY)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_UUID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns the station display name."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is not None
    assert "Oslo S" in name or "Circle K" in name


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_no_coordinates() -> None:
    """async_fetch_station_name returns None when lat/lng are not configured."""
    session = MagicMock()
    provider = NoDrivstoffProvider(station_id=_STATION_UUID, api_key=_API_KEY)

    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_not_found() -> None:
    """async_fetch_station_name returns None when UUID is not in response."""
    payload = {"stations": [{**_BASE_STATION, "id": "different-uuid"}]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_auth_failure() -> None:
    """async_fetch_station_name returns None when API returns 401."""
    resp = _make_mock_response(401)
    session = _make_session(resp)

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (uuid, label) tuples."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert isinstance(result, list)
    assert len(result) == 1
    uid, label = result[0]
    assert uid == _STATION_UUID
    assert isinstance(label, str)


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes formatted diesel price."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Diesel" in label
    assert "20.90" in label


async def test_async_list_stations_label_includes_bensin_price() -> None:
    """async_list_stations label includes formatted bensin (unleaded/95) price."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Bensin" in label
    assert "21.50" in label


async def test_async_list_stations_passes_distance_in_metres() -> None:
    """async_list_stations converts radius_km to metres for the API."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider(radius_km=10.0)
    await provider.async_list_stations(session, lat=_LAT, lng=_LNG, radius_km=10.0)

    call_kwargs = session.get.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert float(params.get("distance", 0)) == pytest.approx(10000.0)


async def test_async_list_stations_passes_bearer_token() -> None:
    """async_list_stations passes api_key as Authorization header."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_OK)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert headers.get("Authorization") == f"Bearer {_API_KEY}"


async def test_async_list_stations_sorted_nearest_first() -> None:
    """async_list_stations sorts results nearest-first."""
    near_station = {
        **_BASE_STATION,
        "id": "near-uuid",
        "location": {"lat": _LAT + 0.001, "lng": _LNG},  # ~110 m away
    }
    far_station = {
        **_BASE_STATION,
        "id": "far-uuid",
        "location": {"lat": _LAT + 0.03, "lng": _LNG},  # ~3.3 km away
    }
    payload = {"stations": [far_station, near_station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=10.0
    )

    assert result[0][0] == "near-uuid"
    assert result[1][0] == "far-uuid"


async def test_async_list_stations_filters_by_radius() -> None:
    """async_list_stations excludes stations outside the radius via haversine."""
    outside_station = {
        **_BASE_STATION,
        "id": "outside-uuid",
        "location": {"lat": _LAT + 1.0, "lng": _LNG},  # ~111 km away
    }
    payload = {"stations": [_BASE_STATION, outside_station]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=5.0
    )

    ids = [r[0] for r in result]
    assert _STATION_UUID in ids
    assert "outside-uuid" not in ids


async def test_async_list_stations_skips_stations_without_id() -> None:
    """async_list_stations skips station records with no 'id' field."""
    no_id_station = {k: v for k, v in _BASE_STATION.items() if k != "id"}
    payload = {"stations": [no_id_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    assert result[0][0] == _STATION_UUID


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when no lat/lng are provided."""
    session = MagicMock()
    provider = NoDrivstoffProvider(station_id=_STATION_UUID, api_key=_API_KEY)

    result = await provider.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when an HTTP error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=5.0
    )

    assert result == []


async def test_async_list_stations_returns_empty_on_401() -> None:
    """async_list_stations returns [] when API returns HTTP 401."""
    resp = _make_mock_response(401)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_returns_empty_when_stations_list_empty() -> None:
    """async_list_stations returns [] when the API stations array is empty."""
    resp = _make_mock_response(200, json_data=_STATIONS_PAYLOAD_EMPTY)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_label_omits_price_when_no_prices() -> None:
    """async_list_stations label omits the price section when prices list is empty."""
    station_no_prices = {**_BASE_STATION, "prices": []}
    payload = {"stations": [station_no_prices]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    _, label = result[0]
    assert "kr" not in label


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """NoDrivstoffProvider is registered in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "no_drivstoff" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["no_drivstoff"] is NoDrivstoffProvider


async def test_async_list_stations_includes_zero_coordinate_station() -> None:
    """Stations at lat=0.0, lng=0.0 must not be silently dropped by falsy coord check."""
    from custom_components.fuelcompare_ie.providers.no_drivstoff import (
        NoDrivstoffProvider,
    )

    station_at_null_island = {
        "id": "zero-island",
        "name": "Station at Null Island",
        "provider": "CIRCLE_K",
        "address": "Null Island Road 0",
        "city": "Null Island",
        "location": {"lat": 0.0, "lng": 0.0},
        "prices": [
            {
                "fuelType": "DIESEL",
                "price": "1.85",
                "registeredAt": "2026-06-14T12:00:00Z",
            }
        ],
    }

    from unittest.mock import AsyncMock, MagicMock

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"stations": [station_at_null_island]})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)

    provider = NoDrivstoffProvider("")
    provider._latitude = 0.0
    provider._longitude = 0.0
    provider._radius_km = 100.0

    # Station is exactly at our query point — haversine distance = 0 — must be included
    stations = await provider.async_list_stations(
        session, lat=0.0, lng=0.0, radius_km=100.0
    )
    uids = [uid for uid, _ in stations]
    assert "zero-island" in uids, (
        "Station at lat=0.0/lng=0.0 must not be dropped by falsy check"
    )
    # Verify the label contains the expected station name from the API 'name' field
    label = next(label for uid, label in stations if uid == "zero-island")
    assert "Station at Null Island" in label


# ---------------------------------------------------------------------------
# New coverage tests: lines 356-358, 405-407, 423, 427-428, 511-514, 587,
# 667-668, 673-674
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None when _fetch_stations raises a generic exception (lines 356-358)."""
    provider = _make_provider()

    # Patch _fetch_stations directly so the exception bypasses its own try/except
    # and propagates to the caller's except block at lines 356-358.
    provider._fetch_stations = AsyncMock(side_effect=RuntimeError("unexpected boom"))

    session = MagicMock()
    name = await provider.async_fetch_station_name(session, _STATION_UUID)

    assert name is None


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_generic_exception() -> None:
    """async_list_stations returns [] when _fetch_stations raises a generic exception (lines 405-407)."""
    provider = _make_provider()

    # Patch _fetch_stations directly so the exception bypasses its own try/except
    # and propagates to the caller's except block at lines 405-407.
    provider._fetch_stations = AsyncMock(side_effect=RuntimeError("unexpected boom"))

    session = MagicMock()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_with_no_gps() -> None:
    """async_list_stations skips stations whose location has no lat/lng (line 423)."""
    no_gps_station = {
        **_BASE_STATION,
        "id": "no-gps",
        "location": {"lat": None, "lng": None},
    }
    payload = {"stations": [no_gps_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    ids = [uid for uid, _ in result]
    assert "no-gps" not in ids
    assert _STATION_UUID in ids


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_with_malformed_coords() -> None:
    """async_list_stations skips stations with non-numeric lat/lng strings (lines 427-428)."""
    bad_coords_station = {
        **_BASE_STATION,
        "id": "bad-coords",
        "location": {"lat": "not-a-number", "lng": "also-bad"},
    }
    payload = {"stations": [bad_coords_station, _BASE_STATION]}
    resp = _make_mock_response(200, json_data=payload)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    ids = [uid for uid, _ in result]
    assert "bad-coords" not in ids
    assert _STATION_UUID in ids


@pytest.mark.asyncio
async def test_fetch_stations_returns_none_on_http_error() -> None:
    """_fetch_stations returns None when raise_for_status raises ClientResponseError (lines 511-514)."""
    from aiohttp import ClientResponseError

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(request_info=None, history=(), status=500)
    )
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)

    provider = _make_provider()
    result = await provider._fetch_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert result is None


def test_extract_prices_skips_record_with_none_fuel_type() -> None:
    """_extract_prices skips price records where fuelType is None (line 587)."""
    prices = _extract_prices(
        [
            {"fuelType": None, "price": "20.90"},
            {"fuelType": "DIESEL", "price": "21.00"},
        ]
    )
    assert prices.get("diesel") == pytest.approx(21.00)
    assert len(prices) == 1


def test_parse_station_lat_none_on_non_numeric_lat() -> None:
    """_parse_station sets latitude to None when lat is non-numeric (lines 667-668)."""
    station = {**_BASE_STATION, "location": {"lat": "bad-lat", "lng": 10.7528}}
    result = _parse_station(station)
    assert result["latitude"] is None
    assert result["longitude"] == pytest.approx(10.7528)


def test_parse_station_lng_none_on_non_numeric_lng() -> None:
    """_parse_station sets longitude to None when lng is non-numeric (lines 673-674)."""
    station = {**_BASE_STATION, "location": {"lat": 59.9110, "lng": "bad-lng"}}
    result = _parse_station(station)
    assert result["latitude"] == pytest.approx(59.9110)
    assert result["longitude"] is None
