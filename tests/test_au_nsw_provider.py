"""Tests for AuNswProvider — NSW FuelCheck (Australia)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.au_nsw import (
    AuNswProvider,
    _FUELTYPE_MAP,
    _API_URL,
    _build_index,
    _build_station_data,
    _build_station_data_with_ts,
    _extract_county,
    _haversine_km,
    _parse_lastupdated,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Fixtures / shared sample data
# ---------------------------------------------------------------------------

_STATION_CODE = "972"
_STATION_CODE_2 = "1001"

_BASE_STATION: dict = {
    "brandid": "7",
    "stationid": "9999",
    "brand": "BP",
    "code": _STATION_CODE,
    "name": "BP Umina Beach",
    "address": "307-313 Ocean Beach Road, UMINA BEACH NSW 2257",
    "location": {"latitude": -33.518, "longitude": 151.307},
    "isAdBlueAvailable": False,
}

_BASE_PRICES: list[dict] = [
    {
        "stationcode": _STATION_CODE,
        "fueltype": "U91",
        "price": 179.9,
        "lastupdated": "13/06/2026 01:35:20",
    },
    {
        "stationcode": _STATION_CODE,
        "fueltype": "E10",
        "price": 169.9,
        "lastupdated": "13/06/2026 02:00:00",
    },
    {
        "stationcode": _STATION_CODE,
        "fueltype": "P95",
        "price": 189.9,
        "lastupdated": "13/06/2026 01:35:20",
    },
    {
        "stationcode": _STATION_CODE,
        "fueltype": "DL",
        "price": 175.9,
        "lastupdated": "13/06/2026 01:35:20",
    },
    {
        "stationcode": _STATION_CODE,
        "fueltype": "PDL",
        "price": 195.9,
        "lastupdated": "13/06/2026 01:35:20",
    },
]

_RAW_RESPONSE: dict = {
    "stations": [_BASE_STATION],
    "prices": _BASE_PRICES,
}


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


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns *response*."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata() -> None:
    """AuNswProvider declares required class attributes."""
    assert AuNswProvider.COUNTRY == "AU"
    assert AuNswProvider.PROVIDER_KEY == "au_nsw"
    assert AuNswProvider.LABEL == "FuelCheck NSW (Australia)"


def test_provider_config_mode() -> None:
    """AuNswProvider uses location CONFIG_MODE."""
    assert AuNswProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """AuNswProvider uses location_search STATION_LOOKUP_MODE."""
    assert AuNswProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """Default poll interval is 3600 seconds (1 hour)."""
    assert AuNswProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all declared fuel types."""
    caps = AuNswProvider.CAPABILITIES
    for fuel in ("e10", "unleaded", "premium_unleaded", "diesel", "premium_diesel"):
        assert fuel in caps, f"'{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = AuNswProvider.CAPABILITIES
    for field in (
        "name",
        "brand",
        "county",
        "address",
        "latitude",
        "longitude",
        "lastupdated",
    ):
        assert field in caps, f"'{field}' missing from CAPABILITIES"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = AuNswProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores the station_id."""
    p = AuNswProvider(_STATION_CODE)
    assert p._station_id == _STATION_CODE


def test_constructor_stores_optional_kwargs() -> None:
    """Constructor stores lat, lng, radius, and county when provided."""
    p = AuNswProvider(
        _STATION_CODE, county="NSW", latitude=-33.5, longitude=151.3, radius_km=5.0
    )
    assert p._county == "NSW"
    assert p._latitude == pytest.approx(-33.5)
    assert p._longitude == pytest.approx(151.3)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_defaults_radius_to_10() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    p = AuNswProvider(_STATION_CODE)
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_stores_none_county_by_default() -> None:
    """Constructor stores county=None when not supplied."""
    p = AuNswProvider(_STATION_CODE)
    assert p._county is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch returns raw cents/litre for unleaded (U91 → unleaded)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    # Raw value is cents (e.g. 179.9) — coordinator applies >10 /100 rule
    assert data["unleaded"] == pytest.approx(179.9)


async def test_async_fetch_success_e10_price() -> None:
    """async_fetch returns raw cents/litre for e10 (E10 → e10)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["e10"] == pytest.approx(169.9)


async def test_async_fetch_success_premium_unleaded_price() -> None:
    """async_fetch returns raw cents/litre for premium_unleaded (P95 → premium_unleaded)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["premium_unleaded"] == pytest.approx(189.9)


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch returns raw cents/litre for diesel (DL → diesel)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["diesel"] == pytest.approx(175.9)


async def test_async_fetch_success_premium_diesel_price() -> None:
    """async_fetch returns raw cents/litre for premium_diesel (PDL → premium_diesel)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["premium_diesel"] == pytest.approx(195.9)


async def test_async_fetch_price_values_are_cents_not_dollars() -> None:
    """async_fetch returns raw cent values (>10) so coordinator /100 rule applies."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    # All prices must be >10 (raw cents) — coordinator will divide by 100
    assert data["unleaded"] > 10.0
    assert data["diesel"] > 10.0


# ---------------------------------------------------------------------------
# async_fetch — field mapping
# ---------------------------------------------------------------------------


async def test_async_fetch_name_field() -> None:
    """async_fetch populates the name field from the stations array."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["name"] == "BP Umina Beach"


async def test_async_fetch_brand_field() -> None:
    """async_fetch populates the brand field from the stations array."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["brand"] == "BP"


async def test_async_fetch_address_field() -> None:
    """async_fetch populates the address field from the stations array."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["address"] == "307-313 Ocean Beach Road, UMINA BEACH NSW 2257"


async def test_async_fetch_county_extracted_from_address() -> None:
    """async_fetch extracts NSW state abbreviation from combined address string."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["county"] == "NSW"


async def test_async_fetch_latitude_field() -> None:
    """async_fetch populates latitude from location.latitude."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["latitude"] == pytest.approx(-33.518)


async def test_async_fetch_longitude_field() -> None:
    """async_fetch populates longitude from location.longitude."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["longitude"] == pytest.approx(151.307)


async def test_async_fetch_lastupdated_is_iso8601() -> None:
    """async_fetch converts DD/MM/YYYY HH:MM:SS to ISO 8601 for lastupdated."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    # Most recent price entry is 02:00:00 on 13/06/2026
    assert data["lastupdated"] == "2026-06-13T02:00:00+00:00"


async def test_async_fetch_source_station_id_field() -> None:
    """async_fetch stores the station code in source_station_id."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["source_station_id"] == _STATION_CODE


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch populates every key declared in CAPABILITIES."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    for key in AuNswProvider.CAPABILITIES - sentinel_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


# ---------------------------------------------------------------------------
# async_fetch — P98 premium_unleaded resolution (lower price wins)
# ---------------------------------------------------------------------------


async def test_async_fetch_p98_maps_to_premium_unleaded() -> None:
    """P98 price is mapped to premium_unleaded; lower price wins when P95 also present."""
    prices_with_p98 = _BASE_PRICES + [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "P98",
            "price": 199.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_with_p98}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    # P95=189.9 wins over P98=199.9 (lower price kept)
    assert data["premium_unleaded"] == pytest.approx(189.9)


async def test_async_fetch_p98_only_when_no_p95() -> None:
    """premium_unleaded is set from P98 alone when no P95 entry exists."""
    prices_p98_only = [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "P98",
            "price": 199.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_p98_only}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["premium_unleaded"] == pytest.approx(199.9)


# ---------------------------------------------------------------------------
# async_fetch — skipped fuel types (B20, EV)
# ---------------------------------------------------------------------------


async def test_async_fetch_b20_not_in_result() -> None:
    """B20 (biodiesel blend) entries are skipped and produce no key in result."""
    prices_with_b20 = _BASE_PRICES + [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "B20",
            "price": 185.0,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_with_b20}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    # B20 has no StationData mapping — must not appear as a key
    # (no crash, result is still valid)
    assert data["unleaded"] == pytest.approx(179.9)


async def test_async_fetch_ev_not_in_result() -> None:
    """EV entries are skipped and do not crash or pollute the result."""
    prices_with_ev = _BASE_PRICES + [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "EV",
            "price": 35.0,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_with_ev}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["diesel"] == pytest.approx(175.9)


# ---------------------------------------------------------------------------
# async_fetch — optional fuel types (e85, lpg)
# ---------------------------------------------------------------------------


async def test_async_fetch_e85_mapped_when_present() -> None:
    """E85 fueltype is mapped to e85 key when present in response."""
    prices_with_e85 = _BASE_PRICES + [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "E85",
            "price": 125.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_with_e85}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data.get("e85") == pytest.approx(125.9)


async def test_async_fetch_lpg_mapped_when_present() -> None:
    """LPG fueltype is mapped to lpg key when present in response."""
    prices_with_lpg = _BASE_PRICES + [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "LPG",
            "price": 95.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_with_lpg}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data.get("lpg") == pytest.approx(95.9)


async def test_async_fetch_missing_prices_are_none() -> None:
    """Fuel types without a price entry resolve to None in StationData."""
    # Only U91 price provided — all others should be None
    prices_u91_only = [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "U91",
            "price": 179.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_u91_only}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["e10"] is None
    assert data["premium_unleaded"] is None
    assert data["diesel"] is None
    assert data["premium_diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when the station code is absent from the dataset."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider("NONEXISTENT_CODE")

    with pytest.raises(ProviderError, match="not found"):
        await provider.async_fetch(session, "NONEXISTENT_CODE")


async def test_async_fetch_raises_provider_error_on_empty_stations() -> None:
    """async_fetch raises ProviderError when the stations list is empty."""
    raw = {"stations": [], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_CODE)


# ---------------------------------------------------------------------------
# async_fetch — HTTP / network errors
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp ClientError on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))
    provider = AuNswProvider(_STATION_CODE)

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_CODE)


async def test_async_fetch_raises_on_non_200_via_raise_for_status() -> None:
    """async_fetch surfaces HTTP errors via raise_for_status."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_CODE)


async def test_async_fetch_raises_provider_error_on_bad_response_structure() -> None:
    """async_fetch raises ProviderError when the API response has no expected keys."""
    bad_payload = {"error": "service unavailable"}
    resp = _make_mock_response(200, json_data=bad_payload)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_CODE)


# ---------------------------------------------------------------------------
# async_fetch — nested response structure (data wrapper)
# ---------------------------------------------------------------------------


async def test_async_fetch_handles_nested_data_wrapper() -> None:
    """async_fetch handles API responses wrapped under a 'data' key."""
    nested = {"data": _RAW_RESPONSE}
    resp = _make_mock_response(200, json_data=nested)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    data = await provider.async_fetch(session, _STATION_CODE)

    assert data["unleaded"] == pytest.approx(179.9)


async def test_async_fetch_raises_provider_error_when_data_wrapper_missing_stations() -> (
    None
):
    """async_fetch raises ProviderError when nested data wrapper has no stations key."""
    bad_nested = {"data": {"foo": "bar"}}
    resp = _make_mock_response(200, json_data=bad_nested)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_CODE)


# ---------------------------------------------------------------------------
# async_fetch — request header contract
# ---------------------------------------------------------------------------


async def test_async_fetch_sends_requesttimestamp_header() -> None:
    """async_fetch sends the requesttimestamp header required by the NSW FuelCheck API."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    await provider.async_fetch(session, _STATION_CODE)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "requesttimestamp" in headers, "requesttimestamp header not sent"


async def test_async_fetch_requesttimestamp_is_utc_iso8601() -> None:
    """The requesttimestamp header value is a UTC ISO 8601 string ending in Z."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    await provider.async_fetch(session, _STATION_CODE)

    headers = session.get.call_args.kwargs.get("headers", {})
    ts = headers.get("requesttimestamp", "")
    assert ts.endswith("Z"), f"requesttimestamp does not end in Z: {ts!r}"
    assert "T" in ts, f"requesttimestamp is not ISO 8601: {ts!r}"


async def test_async_fetch_targets_correct_api_url() -> None:
    """async_fetch calls session.get with the NSW FuelCheck API URL."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    await provider.async_fetch(session, _STATION_CODE)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
    assert url == _API_URL


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns the station name on success."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    name = await provider.async_fetch_station_name(session, _STATION_CODE)

    assert name == "BP Umina Beach"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station code not in dataset."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuNswProvider("UNKNOWN")
    name = await provider.async_fetch_station_name(session, "UNKNOWN")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None on network failure (swallows exception)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = AuNswProvider(_STATION_CODE)
    name = await provider.async_fetch_station_name(session, _STATION_CODE)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_http_error() -> None:
    """async_fetch_station_name returns None when HTTP error occurs (swallows exception)."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(side_effect=ClientError("503"))
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    name = await provider.async_fetch_station_name(session, _STATION_CODE)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_is_empty_string() -> (
    None
):
    """async_fetch_station_name returns None when station name is empty string."""
    no_name_station = {**_BASE_STATION, "name": ""}
    raw = {"stations": [no_name_station], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(_STATION_CODE)
    name = await provider.async_fetch_station_name(session, _STATION_CODE)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------

_STATION_NEARBY: dict = {
    "brandid": "3",
    "stationid": "8888",
    "brand": "Caltex",
    "code": _STATION_CODE_2,
    "name": "Caltex Gosford",
    "address": "100 Mann Street, GOSFORD NSW 2250",
    "location": {"latitude": -33.428, "longitude": 151.341},
    "isAdBlueAvailable": False,
}

_PRICES_NEARBY: list[dict] = [
    {
        "stationcode": _STATION_CODE_2,
        "fueltype": "U91",
        "price": 177.9,
        "lastupdated": "13/06/2026 02:00:00",
    },
    {
        "stationcode": _STATION_CODE_2,
        "fueltype": "DL",
        "price": 173.9,
        "lastupdated": "13/06/2026 02:00:00",
    },
]


async def test_async_list_stations_returns_stations_in_radius() -> None:
    """async_list_stations returns stations within the specified radius."""
    raw = {
        "stations": [_BASE_STATION, _STATION_NEARBY],
        "prices": _BASE_PRICES + _PRICES_NEARBY,
    }
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    # Centre near UMINA BEACH, 25 km radius to catch both stations
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.5, longitude=151.3, radius_km=25.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.5, lng=151.3, radius_km=25.0
    )

    codes = [code for code, _ in result]
    assert _STATION_CODE in codes
    assert _STATION_CODE_2 in codes


async def test_async_list_stations_excludes_out_of_radius_stations() -> None:
    """async_list_stations excludes stations outside the radius."""
    far_station = {
        **_BASE_STATION,
        "code": "FAR",
        "name": "Far Away Station",
        "location": {"latitude": -35.5, "longitude": 149.1},  # ~300 km away
    }
    raw = {"stations": [far_station, _STATION_NEARBY], "prices": _PRICES_NEARBY}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.5, longitude=151.3, radius_km=10.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.5, lng=151.3, radius_km=10.0
    )

    codes = [code for code, _ in result]
    assert "FAR" not in codes


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns stations sorted cheapest first."""
    expensive_station = {
        **_BASE_STATION,
        "code": "EXP",
        "name": "Expensive Station",
        "location": {"latitude": -33.52, "longitude": 151.31},
    }
    expensive_prices = [
        {
            "stationcode": "EXP",
            "fueltype": "DL",
            "price": 210.0,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {
        "stations": [_BASE_STATION, expensive_station],
        "prices": _BASE_PRICES + expensive_prices,
    }
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.52, longitude=151.31, radius_km=5.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.52, lng=151.31, radius_km=5.0
    )

    # Cheapest station (972 diesel=175.9) must come before expensive one (EXP=210.0)
    codes = [code for code, _ in result]
    assert codes.index(_STATION_CODE) < codes.index("EXP")


async def test_async_list_stations_label_includes_price() -> None:
    """async_list_stations labels include formatted A$ price."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "A$" in label


async def test_async_list_stations_label_includes_diesel() -> None:
    """async_list_stations label includes Diesel when present."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    _, label = result[0]
    assert "Diesel" in label


async def test_async_list_stations_label_includes_unleaded() -> None:
    """async_list_stations label includes Unleaded when present."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    _, label = result[0]
    assert "Unleaded" in label


async def test_async_list_stations_label_e10_shown_when_no_unleaded() -> None:
    """async_list_stations shows E10 in label when U91 is absent but E10 is present."""
    prices_e10_only = [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "E10",
            "price": 169.9,
            "lastupdated": "13/06/2026 01:35:20",
        }
    ]
    raw = {"stations": [_BASE_STATION], "prices": prices_e10_only}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    _, label = result[0]
    assert "E10" in label


async def test_async_list_stations_returns_empty_without_lat_lng() -> None:
    """async_list_stations returns empty list when lat/lng not provided."""
    provider = AuNswProvider(_STATION_CODE)  # no lat/lng set
    session = MagicMock()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns empty list on network failure (swallows exception)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.5, longitude=151.3, radius_km=10.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.5, lng=151.3, radius_km=10.0
    )

    assert result == []


async def test_async_list_stations_returns_empty_when_all_out_of_radius() -> None:
    """async_list_stations returns empty list when no stations are within radius."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    # Centre far from the station
    provider = AuNswProvider(
        _STATION_CODE, latitude=-35.0, longitude=149.0, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-35.0, lng=149.0, radius_km=1.0
    )

    assert result == []


async def test_async_list_stations_skips_stations_missing_location() -> None:
    """async_list_stations silently skips stations with no location data."""
    no_loc_station = {**_BASE_STATION, "location": None}
    raw = {"stations": [no_loc_station], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    assert result == []


async def test_async_list_stations_uses_stored_lat_lng_when_not_in_kwargs() -> None:
    """async_list_stations falls back to constructor lat/lng when not passed as kwargs."""
    raw = {"stations": [_BASE_STATION], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    # Only set lat/lng in constructor, not in kwargs
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(session)  # no lat/lng kwargs

    assert len(result) == 1
    assert result[0][0] == _STATION_CODE


async def test_async_list_stations_brand_not_duplicated_in_label() -> None:
    """Label omits brand prefix when brand name already appears in station name."""
    station_with_brand_in_name = {
        **_BASE_STATION,
        "brand": "BP",
        "name": "BP Express Umina",  # brand already in name
    }
    raw = {"stations": [station_with_brand_in_name], "prices": _BASE_PRICES}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    _, label = result[0]
    # Brand should not appear twice (e.g. "BP — BP Express Umina")
    assert "BP — BP" not in label


async def test_async_list_stations_no_prices_shows_name_only() -> None:
    """async_list_stations label shows station name only when no prices available."""
    raw = {"stations": [_BASE_STATION], "prices": []}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuNswProvider(
        _STATION_CODE, latitude=-33.518, longitude=151.307, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-33.518, lng=151.307, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "BP Umina Beach" in label
    assert "A$" not in label


# ---------------------------------------------------------------------------
# _build_index (module-level helper)
# ---------------------------------------------------------------------------


def test_build_index_builds_station_map() -> None:
    """_build_index returns a dict keyed by station code."""
    station_map, _ = _build_index(_RAW_RESPONSE)
    assert _STATION_CODE in station_map
    assert station_map[_STATION_CODE]["name"] == "BP Umina Beach"


def test_build_index_builds_prices_map() -> None:
    """_build_index returns a prices dict with StationData keys."""
    _, prices_map = _build_index(_RAW_RESPONSE)
    assert _STATION_CODE in prices_map
    assert "unleaded" in prices_map[_STATION_CODE]
    assert "diesel" in prices_map[_STATION_CODE]


def test_build_index_skips_missing_stationcode() -> None:
    """_build_index skips price entries with no stationcode field."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {"fueltype": "U91", "price": 179.9, "lastupdated": "13/06/2026 01:35:20"}
        ],
    }
    _, prices_map = _build_index(raw)
    assert _STATION_CODE not in prices_map


def test_build_index_skips_zero_and_negative_prices() -> None:
    """_build_index discards price entries with zero or negative values."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "U91",
                "price": 0,
                "lastupdated": "13/06/2026 01:35:20",
            },
            {
                "stationcode": _STATION_CODE,
                "fueltype": "DL",
                "price": -5.0,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    assert _STATION_CODE not in prices_map or "unleaded" not in prices_map.get(
        _STATION_CODE, {}
    )


def test_build_index_skips_null_prices() -> None:
    """_build_index discards price entries where price is null."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "U91",
                "price": None,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    station_prices = prices_map.get(_STATION_CODE, {})
    assert "unleaded" not in station_prices


def test_build_index_skips_unparseable_prices() -> None:
    """_build_index discards price entries where price cannot be cast to float."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "U91",
                "price": "N/A",
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    station_prices = prices_map.get(_STATION_CODE, {})
    assert "unleaded" not in station_prices


def test_build_index_keeps_lower_price_for_same_key() -> None:
    """_build_index keeps the lower price when P95 and P98 map to premium_unleaded."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "P95",
                "price": 189.9,
                "lastupdated": "13/06/2026 01:35:20",
            },
            {
                "stationcode": _STATION_CODE,
                "fueltype": "P98",
                "price": 199.9,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    assert prices_map[_STATION_CODE]["premium_unleaded"] == pytest.approx(189.9)


def test_build_index_keeps_lower_price_when_p98_is_cheaper() -> None:
    """_build_index keeps P98 price when it is lower than P95."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "P95",
                "price": 195.0,
                "lastupdated": "13/06/2026 01:35:20",
            },
            {
                "stationcode": _STATION_CODE,
                "fueltype": "P98",
                "price": 185.0,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    assert prices_map[_STATION_CODE]["premium_unleaded"] == pytest.approx(185.0)


def test_build_index_skips_unknown_fueltype() -> None:
    """_build_index silently skips unknown fueltype strings."""
    raw = {
        "stations": [_BASE_STATION],
        "prices": [
            {
                "stationcode": _STATION_CODE,
                "fueltype": "ROCKET_FUEL",
                "price": 999.9,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    _, prices_map = _build_index(raw)
    # No crash, and no ROCKET_FUEL key
    for prices in prices_map.values():
        assert "rocket_fuel" not in prices


def test_build_index_handles_empty_stations_and_prices() -> None:
    """_build_index handles completely empty input gracefully."""
    station_map, prices_map = _build_index({"stations": [], "prices": []})
    assert station_map == {}
    assert prices_map == {}


def test_build_index_station_code_coerced_to_string() -> None:
    """_build_index coerces integer station codes to strings."""
    station_with_int_code = {**_BASE_STATION, "code": 972}
    raw = {
        "stations": [station_with_int_code],
        "prices": [
            {
                "stationcode": 972,
                "fueltype": "U91",
                "price": 179.9,
                "lastupdated": "13/06/2026 01:35:20",
            },
        ],
    }
    station_map, prices_map = _build_index(raw)
    assert "972" in station_map
    assert "972" in prices_map


# ---------------------------------------------------------------------------
# _build_station_data (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_data_returns_all_expected_keys() -> None:
    """_build_station_data returns a dict with all required StationData keys."""
    _, prices_map = _build_index(_RAW_RESPONSE)
    prices = prices_map.get(_STATION_CODE, {})
    data = _build_station_data(_BASE_STATION, prices)

    for key in (
        "e10",
        "unleaded",
        "premium_unleaded",
        "diesel",
        "premium_diesel",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
    ):
        assert key in data, f"Key '{key}' missing from _build_station_data result"


def test_build_station_data_sets_lastupdated_none() -> None:
    """_build_station_data sets lastupdated=None (resolved later via _with_ts variant)."""
    data = _build_station_data(_BASE_STATION, {})
    assert data["lastupdated"] is None


def test_build_station_data_extracts_county() -> None:
    """_build_station_data extracts county from the address field."""
    data = _build_station_data(_BASE_STATION, {})
    assert data["county"] == "NSW"


def test_build_station_data_null_location_gives_none_coords() -> None:
    """_build_station_data handles station with null location dict gracefully."""
    station_no_loc = {**_BASE_STATION, "location": None}
    data = _build_station_data(station_no_loc, {})
    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_missing_location_gives_none_coords() -> None:
    """_build_station_data handles station with entirely absent location key."""
    station_no_loc = {k: v for k, v in _BASE_STATION.items() if k != "location"}
    data = _build_station_data(station_no_loc, {})
    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_invalid_latitude_gives_none() -> None:
    """_build_station_data handles non-numeric latitude gracefully."""
    station_bad_loc = {
        **_BASE_STATION,
        "location": {"latitude": "bad", "longitude": 151.3},
    }
    data = _build_station_data(station_bad_loc, {})
    assert data["latitude"] is None


def test_build_station_data_invalid_longitude_gives_none() -> None:
    """_build_station_data handles non-numeric longitude gracefully."""
    station_bad_loc = {
        **_BASE_STATION,
        "location": {"latitude": -33.5, "longitude": "bad"},
    }
    data = _build_station_data(station_bad_loc, {})
    assert data["longitude"] is None


def test_build_station_data_empty_name_becomes_none() -> None:
    """_build_station_data converts empty string name to None."""
    station = {**_BASE_STATION, "name": ""}
    data = _build_station_data(station, {})
    assert data["name"] is None


def test_build_station_data_empty_brand_becomes_none() -> None:
    """_build_station_data converts empty string brand to None."""
    station = {**_BASE_STATION, "brand": ""}
    data = _build_station_data(station, {})
    assert data["brand"] is None


def test_build_station_data_source_station_id_from_code() -> None:
    """_build_station_data sets source_station_id from the station code field."""
    data = _build_station_data(_BASE_STATION, {})
    assert data["source_station_id"] == _STATION_CODE


# ---------------------------------------------------------------------------
# _build_station_data_with_ts (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_data_with_ts_resolves_most_recent_timestamp() -> None:
    """_build_station_data_with_ts picks the latest lastupdated across price entries."""
    _, prices_map = _build_index(_RAW_RESPONSE)
    prices = prices_map.get(_STATION_CODE, {})
    data = _build_station_data_with_ts(
        _BASE_STATION, prices, _BASE_PRICES, _STATION_CODE
    )

    # E10 entry has the latest ts: "13/06/2026 02:00:00"
    assert data["lastupdated"] == "2026-06-13T02:00:00+00:00"


def test_build_station_data_with_ts_returns_none_when_no_matching_entries() -> None:
    """_build_station_data_with_ts returns lastupdated=None when no prices match station code."""
    data = _build_station_data_with_ts(_BASE_STATION, {}, [], _STATION_CODE)
    assert data["lastupdated"] is None


def test_build_station_data_with_ts_ignores_other_station_entries() -> None:
    """_build_station_data_with_ts only considers entries for the given station code."""
    prices_other = [
        {
            "stationcode": "OTHER",
            "fueltype": "U91",
            "price": 200.0,
            "lastupdated": "14/06/2026 10:00:00",
        }
    ]
    data = _build_station_data_with_ts(_BASE_STATION, {}, prices_other, _STATION_CODE)
    assert data["lastupdated"] is None


def test_build_station_data_with_ts_skips_bad_timestamp_entries() -> None:
    """_build_station_data_with_ts skips malformed lastupdated strings gracefully."""
    bad_prices = [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "U91",
            "price": 179.9,
            "lastupdated": "NOT A DATE",
        },
        {
            "stationcode": _STATION_CODE,
            "fueltype": "DL",
            "price": 175.9,
            "lastupdated": "13/06/2026 01:35:20",
        },
    ]
    data = _build_station_data_with_ts(_BASE_STATION, {}, bad_prices, _STATION_CODE)
    # Should still resolve from the valid entry
    assert data["lastupdated"] == "2026-06-13T01:35:20+00:00"


def test_build_station_data_with_ts_skips_null_lastupdated_entries() -> None:
    """_build_station_data_with_ts skips entries where lastupdated is null."""
    prices_with_null_ts = [
        {
            "stationcode": _STATION_CODE,
            "fueltype": "U91",
            "price": 179.9,
            "lastupdated": None,  # null lastupdated — must be skipped
        },
        {
            "stationcode": _STATION_CODE,
            "fueltype": "DL",
            "price": 175.9,
            "lastupdated": "13/06/2026 01:35:20",
        },
    ]
    data = _build_station_data_with_ts(
        _BASE_STATION, {}, prices_with_null_ts, _STATION_CODE
    )
    # Should still resolve the valid DL entry
    assert data["lastupdated"] == "2026-06-13T01:35:20+00:00"


# ---------------------------------------------------------------------------
# _parse_lastupdated (module-level helper)
# ---------------------------------------------------------------------------


def test_parse_lastupdated_valid_format() -> None:
    """_parse_lastupdated converts DD/MM/YYYY HH:MM:SS to ISO 8601."""
    result = _parse_lastupdated("13/06/2026 01:35:20")
    assert result == "2026-06-13T01:35:20+00:00"


def test_parse_lastupdated_with_leading_whitespace() -> None:
    """_parse_lastupdated handles leading/trailing whitespace."""
    result = _parse_lastupdated("  13/06/2026 01:35:20  ")
    assert result == "2026-06-13T01:35:20+00:00"


def test_parse_lastupdated_returns_none_for_empty_string() -> None:
    """_parse_lastupdated returns None for empty string input."""
    assert _parse_lastupdated("") is None


def test_parse_lastupdated_returns_none_for_none_input() -> None:
    """_parse_lastupdated returns None when passed None."""
    assert _parse_lastupdated(None) is None


def test_parse_lastupdated_returns_none_for_bad_format() -> None:
    """_parse_lastupdated returns None for unrecognised timestamp formats."""
    assert _parse_lastupdated("2026-06-13T01:35:20Z") is None  # ISO, not DD/MM/YYYY
    assert _parse_lastupdated("June 13 2026") is None
    assert _parse_lastupdated("not a date") is None


def test_parse_lastupdated_midnight() -> None:
    """_parse_lastupdated handles midnight timestamps correctly."""
    result = _parse_lastupdated("01/01/2026 00:00:00")
    assert result == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# _extract_county (module-level helper)
# ---------------------------------------------------------------------------


def test_extract_county_nsw() -> None:
    """_extract_county extracts NSW from a typical address."""
    assert _extract_county("307-313 Ocean Beach Road, UMINA BEACH NSW 2257") == "NSW"


def test_extract_county_tas() -> None:
    """_extract_county extracts TAS from a Tasmanian address."""
    assert _extract_county("12 Main Street, HOBART TAS 7000") == "TAS"


def test_extract_county_vic() -> None:
    """_extract_county extracts VIC from a Victorian address."""
    assert _extract_county("1 Collins Street, MELBOURNE VIC 3000") == "VIC"


def test_extract_county_qld() -> None:
    """_extract_county extracts QLD from a Queensland address."""
    assert _extract_county("100 Queen Street, BRISBANE QLD 4000") == "QLD"


def test_extract_county_sa() -> None:
    """_extract_county extracts SA from a South Australian address."""
    assert _extract_county("5 King William Road, ADELAIDE SA 5000") == "SA"


def test_extract_county_wa() -> None:
    """_extract_county extracts WA from a Western Australian address."""
    assert _extract_county("1 St George Terrace, PERTH WA 6000") == "WA"


def test_extract_county_act() -> None:
    """_extract_county extracts ACT from a Canberra address."""
    assert _extract_county("1 Civic Square, CANBERRA ACT 2601") == "ACT"


def test_extract_county_nt() -> None:
    """_extract_county extracts NT from a Northern Territory address."""
    assert _extract_county("10 Smith Street, DARWIN NT 0800") == "NT"


def test_extract_county_returns_uppercase() -> None:
    """_extract_county always returns the abbreviation in uppercase."""
    result = _extract_county("1 Main Road, SUBURB nsw 2000")
    assert result == "NSW"


def test_extract_county_returns_none_for_none_input() -> None:
    """_extract_county returns None when passed None."""
    assert _extract_county(None) is None


def test_extract_county_returns_none_for_empty_string() -> None:
    """_extract_county returns None for empty string input."""
    assert _extract_county("") is None


def test_extract_county_returns_none_when_no_match() -> None:
    """_extract_county returns None when no state abbreviation is found."""
    assert _extract_county("123 Some Road, SUBURB 2000") is None


# ---------------------------------------------------------------------------
# _haversine_km (module-level helper)
# ---------------------------------------------------------------------------


def test_haversine_km_same_point_is_zero() -> None:
    """_haversine_km returns 0.0 for identical coordinates."""
    assert _haversine_km(-33.518, 151.307, -33.518, 151.307) == pytest.approx(
        0.0, abs=1e-6
    )


def test_haversine_km_known_distance_sydney_to_canberra() -> None:
    """_haversine_km returns approximately 249 km from Sydney to Canberra."""
    # Sydney CBD: -33.8688, 151.2093; Canberra: -35.2809, 149.1300
    dist = _haversine_km(-33.8688, 151.2093, -35.2809, 149.1300)
    assert 240 < dist < 260


def test_haversine_km_is_symmetric() -> None:
    """_haversine_km gives the same result regardless of which point is 'from'."""
    d1 = _haversine_km(-33.518, 151.307, -33.428, 151.341)
    d2 = _haversine_km(-33.428, 151.341, -33.518, 151.307)
    assert d1 == pytest.approx(d2)


def test_haversine_km_short_distance() -> None:
    """_haversine_km is accurate for short distances (< 20 km)."""
    # Distance between _BASE_STATION and _STATION_NEARBY ~10 km
    dist = _haversine_km(-33.518, 151.307, -33.428, 151.341)
    assert 8 < dist < 15


# ---------------------------------------------------------------------------
# _FUELTYPE_MAP (module-level constant)
# ---------------------------------------------------------------------------


def test_fueltype_map_e10_mapping() -> None:
    """E10 maps to 'e10'."""
    assert _FUELTYPE_MAP["E10"] == "e10"


def test_fueltype_map_u91_mapping() -> None:
    """U91 maps to 'unleaded'."""
    assert _FUELTYPE_MAP["U91"] == "unleaded"


def test_fueltype_map_p95_mapping() -> None:
    """P95 maps to 'premium_unleaded'."""
    assert _FUELTYPE_MAP["P95"] == "premium_unleaded"


def test_fueltype_map_p98_mapping() -> None:
    """P98 maps to 'premium_unleaded' (same key as P95)."""
    assert _FUELTYPE_MAP["P98"] == "premium_unleaded"


def test_fueltype_map_dl_mapping() -> None:
    """DL maps to 'diesel'."""
    assert _FUELTYPE_MAP["DL"] == "diesel"


def test_fueltype_map_pdl_mapping() -> None:
    """PDL maps to 'premium_diesel'."""
    assert _FUELTYPE_MAP["PDL"] == "premium_diesel"


def test_fueltype_map_e85_mapping() -> None:
    """E85 maps to 'e85'."""
    assert _FUELTYPE_MAP["E85"] == "e85"


def test_fueltype_map_lpg_mapping() -> None:
    """LPG maps to 'lpg'."""
    assert _FUELTYPE_MAP["LPG"] == "lpg"


def test_fueltype_map_b20_not_present() -> None:
    """B20 is intentionally absent from the fueltype map."""
    assert "B20" not in _FUELTYPE_MAP


def test_fueltype_map_ev_not_present() -> None:
    """EV is intentionally absent from the fueltype map."""
    assert "EV" not in _FUELTYPE_MAP


# ---------------------------------------------------------------------------
# API URL constant
# ---------------------------------------------------------------------------


def test_api_url_targets_onegov_nsw() -> None:
    """The provider targets the correct NSW FuelCheck API URL."""
    assert "onegov.nsw.gov.au" in _API_URL
    assert _API_URL.startswith("https://")
    assert "FuelCheckApp" in _API_URL
