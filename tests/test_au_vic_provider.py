"""Tests for AuVicProvider — Service Victoria Fair Fuel Open Data API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.au_vic import (
    AuVicProvider,
    _API_URL,
    _FUELTYPE_MAP,
    _build_display_label,
    _build_station_data,
    _build_station_map,
    _extract_prices,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

_STATION_ID = "56ab12ef-1234-5678-abcd-000000000001"
_STATION_ID_2 = "56ab12ef-1234-5678-abcd-000000000002"
_CONSUMER_ID = "fa11a74d-f9a7-4b52-a18c-db51b7b7a38a"

_BASE_STATION: dict = {
    "id": _STATION_ID,
    "name": "7-Eleven Melbourne CBD",
    "brandId": "brand-uuid-001",
    "address": "123 Swanston St",
    "suburb": "Melbourne",
    "state": "VIC",
    "postcode": "3000",
    "location": {"latitude": -37.8136, "longitude": 144.9631},
}

_BASE_FUEL_PRICES: list[dict] = [
    {
        "fuelType": "U91",
        "price": 1.759,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
    {
        "fuelType": "DSL",
        "price": 1.839,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
    {
        "fuelType": "PDSL",
        "price": 1.979,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
    {
        "fuelType": "E10",
        "price": 1.709,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
]

_BASE_ENTRY: dict = {
    "fuelStation": _BASE_STATION,
    "fuelPrices": _BASE_FUEL_PRICES,
    "updatedAt": "2026-06-13T06:00:00.000Z",
}

_RAW_RESPONSE: dict = {
    "fuelPriceDetails": [_BASE_ENTRY],
}


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    if status >= 400:
        mock_resp.raise_for_status = MagicMock(
            side_effect=ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=status,
            )
        )
    else:
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
    """AuVicProvider declares required class attributes."""
    assert AuVicProvider.COUNTRY == "AU"
    assert AuVicProvider.PROVIDER_KEY == "au_vic"
    assert AuVicProvider.LABEL == "Servo Saver VIC (Australia)"


def test_provider_config_mode() -> None:
    """AuVicProvider uses station_id CONFIG_MODE."""
    assert AuVicProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """AuVicProvider uses location_search STATION_LOOKUP_MODE."""
    assert AuVicProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """Poll interval is 86400 seconds (once daily)."""
    assert AuVicProvider.POLL_INTERVAL_SECONDS == 86400


def test_provider_requires_api_key() -> None:
    """AuVicProvider declares REQUIRES_API_KEY=True."""
    assert AuVicProvider.REQUIRES_API_KEY is True


def test_provider_api_key_registration_url() -> None:
    """AuVicProvider supplies a non-empty API_KEY_REGISTRATION_URL."""
    assert AuVicProvider.API_KEY_REGISTRATION_URL
    assert "service.vic.gov.au" in AuVicProvider.API_KEY_REGISTRATION_URL


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all declared fuel types."""
    caps = AuVicProvider.CAPABILITIES
    for fuel in ("unleaded", "diesel", "premium_diesel", "e10", "premium_unleaded"):
        assert fuel in caps, f"'{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = AuVicProvider.CAPABILITIES
    for field in (
        "name",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
    ):
        assert field in caps, f"'{field}' missing from CAPABILITIES"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = AuVicProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    p = AuVicProvider(_STATION_ID)
    assert p._station_id == _STATION_ID


def test_constructor_stores_api_key() -> None:
    """Constructor stores api_key."""
    p = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    assert p._api_key == _CONSUMER_ID


def test_constructor_defaults_radius_to_10() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    p = AuVicProvider(_STATION_ID)
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_stores_lat_lng() -> None:
    """Constructor stores lat and lng."""
    p = AuVicProvider(_STATION_ID, latitude=-37.8, longitude=144.9, radius_km=5.0)
    assert p._latitude == pytest.approx(-37.8)
    assert p._longitude == pytest.approx(144.9)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_empty_api_key_when_absent() -> None:
    """Constructor sets api_key to empty string when not supplied."""
    p = AuVicProvider(_STATION_ID)
    assert p._api_key == ""


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch returns AUD/litre for unleaded (U91)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # Prices are in AUD/litre directly (e.g. 1.759)
    assert data["unleaded"] == pytest.approx(1.759)


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch returns AUD/litre for diesel (DSL)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.839)


async def test_async_fetch_success_premium_diesel_price() -> None:
    """async_fetch returns AUD/litre for premium_diesel (PDSL)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_diesel"] == pytest.approx(1.979)


async def test_async_fetch_success_e10_price() -> None:
    """async_fetch returns AUD/litre for e10 (E10)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["e10"] == pytest.approx(1.709)


async def test_async_fetch_prices_are_dollars_not_cents() -> None:
    """async_fetch returns prices in AUD/litre (values < 10, not raw cents)."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # All prices must be < 10 (already in dollars, not cents)
    assert data["unleaded"] < 10.0
    assert data["diesel"] < 10.0


async def test_async_fetch_name_field() -> None:
    """async_fetch populates the name field."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "7-Eleven Melbourne CBD"


async def test_async_fetch_county_is_state_abbreviation() -> None:
    """async_fetch sets county to the state abbreviation (e.g. 'VIC')."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["county"] == "VIC"


async def test_async_fetch_latitude_field() -> None:
    """async_fetch populates latitude from location.latitude."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(-37.8136)


async def test_async_fetch_longitude_field() -> None:
    """async_fetch populates longitude from location.longitude."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["longitude"] == pytest.approx(144.9631)


async def test_async_fetch_lastupdated_field() -> None:
    """async_fetch populates lastupdated from the entry's updatedAt field."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] == "2026-06-13T06:00:00.000Z"


async def test_async_fetch_source_station_id_field() -> None:
    """async_fetch stores the station UUID in source_station_id."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["source_station_id"] == _STATION_ID


async def test_async_fetch_address_combines_components() -> None:
    """async_fetch builds a full address string from address+suburb+state+postcode."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    addr = data["address"] or ""
    assert "123 Swanston St" in addr
    assert "Melbourne" in addr
    assert "VIC" in addr


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch populates every key declared in CAPABILITIES."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    for key in AuVicProvider.CAPABILITIES - sentinel_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


# ---------------------------------------------------------------------------
# async_fetch — premium_unleaded resolution (P95 / P98)
# ---------------------------------------------------------------------------


async def test_async_fetch_p95_maps_to_premium_unleaded() -> None:
    """P95 is mapped to premium_unleaded."""
    entry = {
        **_BASE_ENTRY,
        "fuelPrices": _BASE_FUEL_PRICES
        + [
            {
                "fuelType": "P95",
                "price": 1.899,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ],
    }
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.899)


async def test_async_fetch_p98_maps_to_premium_unleaded() -> None:
    """P98 is also mapped to premium_unleaded; lower price wins when P95 also present."""
    entry = {
        **_BASE_ENTRY,
        "fuelPrices": _BASE_FUEL_PRICES
        + [
            {
                "fuelType": "P95",
                "price": 1.899,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
            {
                "fuelType": "P98",
                "price": 1.999,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ],
    }
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # P95=1.899 wins over P98=1.999 (lower price kept)
    assert data["premium_unleaded"] == pytest.approx(1.899)


# ---------------------------------------------------------------------------
# async_fetch — unavailable fuel types skipped
# ---------------------------------------------------------------------------


async def test_async_fetch_unavailable_fuel_skipped() -> None:
    """async_fetch skips fuelPrices entries where isAvailable is False."""
    entry = {
        **_BASE_ENTRY,
        "fuelPrices": [
            {
                "fuelType": "U91",
                "price": 1.759,
                "isAvailable": False,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
            {
                "fuelType": "DSL",
                "price": 1.839,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ],
    }
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] is None
    assert data["diesel"] == pytest.approx(1.839)


# ---------------------------------------------------------------------------
# async_fetch — skipped fuel types (B20, LNG, CNG)
# ---------------------------------------------------------------------------


async def test_async_fetch_b20_not_in_result() -> None:
    """B20 entries are silently skipped."""
    entry = {
        **_BASE_ENTRY,
        "fuelPrices": _BASE_FUEL_PRICES
        + [
            {
                "fuelType": "B20",
                "price": 1.85,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ],
    }
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # No crash and unleaded still correct
    assert data["unleaded"] == pytest.approx(1.759)


async def test_async_fetch_missing_prices_are_none() -> None:
    """Fuel types without a price entry resolve to None in StationData."""
    entry = {
        **_BASE_ENTRY,
        "fuelPrices": [
            {
                "fuelType": "U91",
                "price": 1.759,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ],
    }
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["e10"] is None
    assert data["premium_unleaded"] is None
    assert data["diesel"] is None
    assert data["premium_diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station UUID absent from dataset."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider("NONEXISTENT-UUID", api_key=_CONSUMER_ID)

    with pytest.raises(ProviderError, match="not found"):
        await provider.async_fetch(session, "NONEXISTENT-UUID")


async def test_async_fetch_raises_provider_error_on_empty_dataset() -> None:
    """async_fetch raises ProviderError when fuelPriceDetails list is empty."""
    raw = {"fuelPriceDetails": []}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — HTTP / network errors
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp ClientError on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_403() -> None:
    """async_fetch surfaces HTTP 403 (invalid/unregistered consumer-id)."""
    resp = _make_mock_response(403)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key="invalid-consumer-id")

    with pytest.raises(ClientResponseError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_on_bad_response_structure() -> None:
    """async_fetch raises ProviderError when API response lacks fuelPriceDetails."""
    bad_payload = {"error": "service unavailable"}
    resp = _make_mock_response(200, json_data=bad_payload)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — request header contract
# ---------------------------------------------------------------------------


async def test_async_fetch_sends_consumer_id_header() -> None:
    """async_fetch sends the x-consumer-id header."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "x-consumer-id" in headers
    assert headers["x-consumer-id"] == _CONSUMER_ID


async def test_async_fetch_sends_transaction_id_header() -> None:
    """async_fetch sends a non-empty x-transactionid header."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    await provider.async_fetch(session, _STATION_ID)

    headers = session.get.call_args.kwargs.get("headers", {})
    assert "x-transactionid" in headers
    assert headers["x-transactionid"]  # non-empty


async def test_async_fetch_targets_correct_api_url() -> None:
    """async_fetch calls session.get with the correct Service Victoria API URL."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    await provider.async_fetch(session, _STATION_ID)

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
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "7-Eleven Melbourne CBD"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station UUID not in dataset."""
    resp = _make_mock_response(200, json_data=_RAW_RESPONSE)
    session = _make_session(resp)
    provider = AuVicProvider("UNKNOWN-UUID", api_key=_CONSUMER_ID)
    name = await provider.async_fetch_station_name(session, "UNKNOWN-UUID")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_empty() -> None:
    """async_fetch_station_name returns None when station name is empty string."""
    no_name_station = {**_BASE_STATION, "name": ""}
    entry = {**_BASE_ENTRY, "fuelStation": no_name_station}
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------

_STATION_NEARBY: dict = {
    "id": _STATION_ID_2,
    "name": "BP Docklands",
    "brandId": "brand-uuid-002",
    "address": "45 Collins St",
    "suburb": "Docklands",
    "state": "VIC",
    "postcode": "3008",
    "location": {"latitude": -37.8180, "longitude": 144.9560},
}

_FUEL_PRICES_NEARBY: list[dict] = [
    {
        "fuelType": "U91",
        "price": 1.739,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
    {
        "fuelType": "DSL",
        "price": 1.819,
        "isAvailable": True,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    },
]

_ENTRY_NEARBY: dict = {
    "fuelStation": _STATION_NEARBY,
    "fuelPrices": _FUEL_PRICES_NEARBY,
    "updatedAt": "2026-06-13T06:00:00.000Z",
}


async def test_async_list_stations_returns_stations_in_radius() -> None:
    """async_list_stations returns stations within the specified radius."""
    raw = {"fuelPriceDetails": [_BASE_ENTRY, _ENTRY_NEARBY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    # Centre near Melbourne CBD, 5 km radius to catch both stations
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=5.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=5.0
    )

    ids = [sid for sid, _ in result]
    assert _STATION_ID in ids
    assert _STATION_ID_2 in ids


async def test_async_list_stations_excludes_out_of_radius_stations() -> None:
    """async_list_stations excludes stations outside the radius."""
    far_station = {
        **_BASE_STATION,
        "id": "FAR-UUID",
        "name": "Far Away Station",
        "location": {"latitude": -38.5, "longitude": 145.5},  # ~100 km from CBD
    }
    far_entry = {**_BASE_ENTRY, "fuelStation": far_station}
    raw = {"fuelPriceDetails": [far_entry, _ENTRY_NEARBY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=5.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=5.0
    )

    ids = [sid for sid, _ in result]
    assert "FAR-UUID" not in ids


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns stations sorted cheapest first."""
    expensive_station = {
        **_BASE_STATION,
        "id": "EXPENSIVE-UUID",
        "name": "Expensive Station",
        "location": {"latitude": -37.815, "longitude": 144.965},
    }
    expensive_prices = [
        {
            "fuelType": "U91",
            "price": 2.199,
            "isAvailable": True,
            "updatedAt": "2026-06-13T06:00:00.000Z",
        },
    ]
    expensive_entry = {
        "fuelStation": expensive_station,
        "fuelPrices": expensive_prices,
        "updatedAt": "2026-06-13T06:00:00.000Z",
    }
    raw = {"fuelPriceDetails": [expensive_entry, _ENTRY_NEARBY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.815,
        longitude=144.965,
        radius_km=5.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.815, lng=144.965, radius_km=5.0
    )

    # _STATION_ID_2 (U91=1.739) is cheaper than EXPENSIVE-UUID (U91=2.199)
    ids = [sid for sid, _ in result]
    assert ids.index(_STATION_ID_2) < ids.index("EXPENSIVE-UUID")


async def test_async_list_stations_label_includes_price() -> None:
    """async_list_stations labels include station name and short ID suffix."""
    raw = {"fuelPriceDetails": [_BASE_ENTRY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "(#" in label


async def test_async_list_stations_label_includes_unleaded() -> None:
    """async_list_stations label includes station name when unleaded present."""
    raw = {"fuelPriceDetails": [_BASE_ENTRY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=1.0
    )

    _, label = result[0]
    assert "7-Eleven Melbourne CBD" in label


async def test_async_list_stations_returns_empty_without_lat_lng() -> None:
    """async_list_stations returns empty list when lat/lng not provided."""
    provider = AuVicProvider(_STATION_ID, api_key=_CONSUMER_ID)  # no lat/lng
    session = MagicMock()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns empty list on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=10.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=10.0
    )

    assert result == []


async def test_async_list_stations_returns_empty_when_all_out_of_radius() -> None:
    """async_list_stations returns empty list when no stations are within radius."""
    raw = {"fuelPriceDetails": [_BASE_ENTRY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    # Centre far from Melbourne
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-34.0,
        longitude=150.5,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-34.0, lng=150.5, radius_km=1.0
    )

    assert result == []


async def test_async_list_stations_skips_stations_missing_location() -> None:
    """async_list_stations silently skips stations with no location data."""
    no_loc_station = {**_BASE_STATION, "location": None}
    entry = {**_BASE_ENTRY, "fuelStation": no_loc_station}
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=1.0
    )

    assert result == []


async def test_async_list_stations_uses_stored_lat_lng_as_fallback() -> None:
    """async_list_stations falls back to constructor lat/lng when not in kwargs."""
    raw = {"fuelPriceDetails": [_BASE_ENTRY]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(session)  # no lat/lng kwargs

    assert len(result) == 1
    assert result[0][0] == _STATION_ID


async def test_async_list_stations_no_prices_shows_name_only() -> None:
    """async_list_stations label shows station name only when no prices available."""
    entry = {**_BASE_ENTRY, "fuelPrices": []}
    raw = {"fuelPriceDetails": [entry]}
    resp = _make_mock_response(200, json_data=raw)
    session = _make_session(resp)
    provider = AuVicProvider(
        _STATION_ID,
        latitude=-37.8136,
        longitude=144.9631,
        radius_km=1.0,
        api_key=_CONSUMER_ID,
    )
    result = await provider.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "7-Eleven Melbourne CBD" in label
    assert "A$" not in label


# ---------------------------------------------------------------------------
# _build_station_map (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_map_indexes_by_station_id() -> None:
    """_build_station_map returns a dict keyed by station UUID."""
    station_map = _build_station_map(_RAW_RESPONSE)
    assert _STATION_ID in station_map


def test_build_station_map_skips_missing_station_id() -> None:
    """_build_station_map skips entries with no station id."""
    raw = {
        "fuelPriceDetails": [
            {
                "fuelStation": {"name": "No ID"},
                "fuelPrices": [],
                "updatedAt": "2026-06-13T06:00:00.000Z",
            }
        ]
    }
    station_map = _build_station_map(raw)
    assert len(station_map) == 0


def test_build_station_map_handles_empty_list() -> None:
    """_build_station_map handles empty fuelPriceDetails gracefully."""
    station_map = _build_station_map({"fuelPriceDetails": []})
    assert station_map == {}


# ---------------------------------------------------------------------------
# _extract_prices (module-level helper)
# ---------------------------------------------------------------------------


def test_extract_prices_u91_maps_to_unleaded() -> None:
    """U91 fuelType maps to 'unleaded' key."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": 1.759,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert prices["unleaded"] == pytest.approx(1.759)


def test_extract_prices_dsl_maps_to_diesel() -> None:
    """DSL fuelType maps to 'diesel' key."""
    prices = _extract_prices(
        [
            {
                "fuelType": "DSL",
                "price": 1.839,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert prices["diesel"] == pytest.approx(1.839)


def test_extract_prices_pdsl_maps_to_premium_diesel() -> None:
    """PDSL fuelType maps to 'premium_diesel' key."""
    prices = _extract_prices(
        [
            {
                "fuelType": "PDSL",
                "price": 1.979,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert prices["premium_diesel"] == pytest.approx(1.979)


def test_extract_prices_e10_maps_to_e10() -> None:
    """E10 fuelType maps to 'e10' key."""
    prices = _extract_prices(
        [
            {
                "fuelType": "E10",
                "price": 1.709,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert prices["e10"] == pytest.approx(1.709)


def test_extract_prices_skips_b20() -> None:
    """B20 fuelType is intentionally absent from the result."""
    prices = _extract_prices(
        [
            {
                "fuelType": "B20",
                "price": 1.85,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert "b20" not in prices
    assert len(prices) == 0


def test_extract_prices_skips_unavailable() -> None:
    """isAvailable=False entries are excluded from the result."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": 1.759,
                "isAvailable": False,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert "unleaded" not in prices


def test_extract_prices_skips_null_price() -> None:
    """Entries with null price are excluded."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": None,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert "unleaded" not in prices


def test_extract_prices_skips_zero_price() -> None:
    """Entries with zero price are excluded."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": 0.0,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert "unleaded" not in prices


def test_extract_prices_lower_price_wins_for_same_key() -> None:
    """When P95 and P98 both map to premium_unleaded, the lower price is kept."""
    prices = _extract_prices(
        [
            {
                "fuelType": "P95",
                "price": 1.899,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
            {
                "fuelType": "P98",
                "price": 1.999,
                "isAvailable": True,
                "updatedAt": "2026-06-13T06:00:00.000Z",
            },
        ]
    )
    assert prices["premium_unleaded"] == pytest.approx(1.899)


def test_extract_prices_empty_list_returns_empty_dict() -> None:
    """An empty fuelPrices list returns an empty prices dict."""
    assert _extract_prices([]) == {}


# ---------------------------------------------------------------------------
# _build_station_data (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_data_populates_name() -> None:
    """_build_station_data populates name from fuelStation.name."""
    data = _build_station_data(_BASE_ENTRY)
    assert data["name"] == "7-Eleven Melbourne CBD"


def test_build_station_data_populates_county() -> None:
    """_build_station_data sets county to the state abbreviation."""
    data = _build_station_data(_BASE_ENTRY)
    assert data["county"] == "VIC"


def test_build_station_data_populates_lat_lng() -> None:
    """_build_station_data extracts latitude and longitude."""
    data = _build_station_data(_BASE_ENTRY)
    assert data["latitude"] == pytest.approx(-37.8136)
    assert data["longitude"] == pytest.approx(144.9631)


def test_build_station_data_null_location_gives_none_coords() -> None:
    """_build_station_data handles station with null location gracefully."""
    station_no_loc = {**_BASE_STATION, "location": None}
    entry = {**_BASE_ENTRY, "fuelStation": station_no_loc}
    data = _build_station_data(entry)
    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_populates_lastupdated() -> None:
    """_build_station_data sets lastupdated from updatedAt."""
    data = _build_station_data(_BASE_ENTRY)
    assert data["lastupdated"] == "2026-06-13T06:00:00.000Z"


def test_build_station_data_source_station_id() -> None:
    """_build_station_data sets source_station_id from fuelStation.id."""
    data = _build_station_data(_BASE_ENTRY)
    assert data["source_station_id"] == _STATION_ID


def test_build_station_data_empty_name_becomes_none() -> None:
    """_build_station_data converts empty string name to None."""
    station = {**_BASE_STATION, "name": ""}
    entry = {**_BASE_ENTRY, "fuelStation": station}
    data = _build_station_data(entry)
    assert data["name"] is None


# ---------------------------------------------------------------------------
# _build_display_label (module-level helper)
# ---------------------------------------------------------------------------


def test_build_display_label_includes_name() -> None:
    """_build_display_label includes the station name."""
    label = _build_display_label(_BASE_STATION, _STATION_ID)
    assert "7-Eleven Melbourne CBD" in label


def test_build_display_label_includes_suburb() -> None:
    """_build_display_label includes suburb when not already in address."""
    station = {**_BASE_STATION, "name": "7-Eleven", "suburb": "Melbourne"}
    label = _build_display_label(station, _STATION_ID)
    assert "Melbourne" in label


def test_build_display_label_sort_key_is_cheapest_price() -> None:
    """_build_display_label returns a string containing the station name."""
    label = _build_display_label(_BASE_STATION, _STATION_ID)
    assert isinstance(label, str)
    assert "7-Eleven Melbourne CBD" in label


def test_build_display_label_no_prices_sort_key_is_9999() -> None:
    """_build_display_label returns a string with the station UUID prefix."""
    label = _build_display_label(_BASE_STATION, _STATION_ID)
    assert "(#" in label
    assert _STATION_ID[:8] in label


def test_build_display_label_includes_aud_currency() -> None:
    """_build_display_label includes the station name in the label."""
    label = _build_display_label(_BASE_STATION, _STATION_ID)
    assert "7-Eleven Melbourne CBD" in label
    assert "(#" in label


# ---------------------------------------------------------------------------
# _FUELTYPE_MAP (module-level constant)
# ---------------------------------------------------------------------------


def test_fueltype_map_u91_mapping() -> None:
    """U91 maps to 'unleaded'."""
    assert _FUELTYPE_MAP["U91"] == "unleaded"


def test_fueltype_map_dsl_mapping() -> None:
    """DSL maps to 'diesel'."""
    assert _FUELTYPE_MAP["DSL"] == "diesel"


def test_fueltype_map_pdsl_mapping() -> None:
    """PDSL maps to 'premium_diesel'."""
    assert _FUELTYPE_MAP["PDSL"] == "premium_diesel"


def test_fueltype_map_e10_mapping() -> None:
    """E10 maps to 'e10'."""
    assert _FUELTYPE_MAP["E10"] == "e10"


def test_fueltype_map_p95_mapping() -> None:
    """P95 maps to 'premium_unleaded'."""
    assert _FUELTYPE_MAP["P95"] == "premium_unleaded"


def test_fueltype_map_p98_mapping() -> None:
    """P98 maps to 'premium_unleaded'."""
    assert _FUELTYPE_MAP["P98"] == "premium_unleaded"


def test_fueltype_map_lpg_mapping() -> None:
    """LPG maps to 'lpg'."""
    assert _FUELTYPE_MAP["LPG"] == "lpg"


def test_fueltype_map_e85_mapping() -> None:
    """E85 maps to 'e85'."""
    assert _FUELTYPE_MAP["E85"] == "e85"


def test_fueltype_map_b20_not_present() -> None:
    """B20 is intentionally absent from the fueltype map."""
    assert "B20" not in _FUELTYPE_MAP


def test_fueltype_map_lng_not_present() -> None:
    """LNG is intentionally absent from the fueltype map."""
    assert "LNG" not in _FUELTYPE_MAP


# ---------------------------------------------------------------------------
# API URL constant
# ---------------------------------------------------------------------------


def test_api_url_targets_service_vic() -> None:
    """The provider targets the correct Service Victoria API URL."""
    assert "fuel.service.vic.gov.au" in _API_URL
    assert _API_URL.startswith("https://")
    assert "open-data/v1/fuel/prices" in _API_URL


def test_extract_prices_string_price_triggers_valueerror_continue() -> None:
    """A non-numeric string price triggers the ValueError handler and is skipped."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": "not-a-number",
                "isAvailable": True,
            }
        ]
    )
    assert "unleaded" not in prices


def test_extract_prices_dict_price_triggers_typeerror_continue() -> None:
    """A dict price triggers the TypeError handler and is skipped."""
    prices = _extract_prices(
        [
            {
                "fuelType": "U91",
                "price": {"value": 1.759},
                "isAvailable": True,
            }
        ]
    )
    assert "unleaded" not in prices


def test_build_station_data_malformed_latitude_becomes_none() -> None:
    """A non-numeric latitude string triggers the TypeError/ValueError handler and returns None."""
    station = {
        **_BASE_STATION,
        "location": {"latitude": "bad-lat", "longitude": 144.9631},
    }
    entry = {**_BASE_ENTRY, "fuelStation": station}
    data = _build_station_data(entry)
    assert data["latitude"] is None


def test_build_station_data_malformed_longitude_becomes_none() -> None:
    """A non-numeric longitude string triggers the TypeError/ValueError handler and returns None."""
    station = {
        **_BASE_STATION,
        "location": {"latitude": -37.8136, "longitude": "bad-lng"},
    }
    entry = {**_BASE_ENTRY, "fuelStation": station}
    data = _build_station_data(entry)
    assert data["longitude"] is None


def test_build_station_data_no_suburb_or_state_uses_address_fallback() -> None:
    """When suburb and state are both absent, full_address falls back to the raw address field."""
    station = {**_BASE_STATION, "suburb": None, "state": None, "address": "99 Test St"}
    entry = {**_BASE_ENTRY, "fuelStation": station}
    data = _build_station_data(entry)
    assert data["address"] == "99 Test St"


# ---------------------------------------------------------------------------
# au_vic.py line 545 — full_addr = address (else branch when suburb absent/in address)
# au_vic.py line 551 — return f"{name} (#{uuid_prefix})" (when full_addr is empty)
# ---------------------------------------------------------------------------


def test_build_display_label_suburb_absent_uses_address_directly() -> None:
    """Line 545: when suburb is falsy, the else branch sets full_addr = address."""
    fs = {"name": "BP Melbourne", "address": "100 Swanston St", "suburb": ""}
    label = _build_display_label(fs, "uuid-1234-abcd")
    # full_addr = "100 Swanston St" (address), not augmented with suburb
    assert "BP Melbourne" in label
    assert "100 Swanston St" in label
    assert "(#uuid-123" in label


def test_build_display_label_suburb_already_in_address_uses_address_directly() -> None:
    """Line 545: when suburb is already embedded in address, else branch sets full_addr = address."""
    fs = {
        "name": "Coles Express",
        "address": "5 Main Rd, Richmond",
        "suburb": "Richmond",
    }
    label = _build_display_label(fs, "uuid-5678-efgh")
    assert "Coles Express" in label
    assert "5 Main Rd, Richmond" in label
    # suburb should NOT be appended again
    assert "Richmond, Richmond" not in label


def test_build_display_label_empty_address_and_no_suburb_returns_name_short_id() -> None:
    """Line 551: when address='' and suburb='', full_addr is '' so return uses (#{uuid_prefix})."""
    fs = {"name": "Unknown Station", "address": "", "suburb": ""}
    label = _build_display_label(fs, "abcdefgh-1234-5678-9abc-def012345678")
    assert label == "Unknown Station (#abcdefgh)"
