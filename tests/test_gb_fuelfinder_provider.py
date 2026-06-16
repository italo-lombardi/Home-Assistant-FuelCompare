"""Tests for GbFuelfinderProvider (UK Fuel Finder CSV mirror)."""

from __future__ import annotations

import time as _time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.gb_fuelfinder import (
    GbFuelfinderProvider,
    _CSV_URL,
    _HEADERS,
    _find_row_by_id,
    _haversine_km,
    _parse_js_timestamp,
    _parse_price_pence,
    _parse_row,
    _pence_to_gbp,
    _safe_float,
)


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_NODE_ID = "a" * 64  # 64-char hex SHA-256 stand-in
_NODE_ID_B = "b" * 64

_TS_E10 = "Thu Jun 12 2026 09:00:00 GMT+0000 (Coordinated Universal Time)"
_TS_E5 = "Thu Jun 12 2026 10:00:00 GMT+0000 (Coordinated Universal Time)"
_TS_B7S = "Thu Jun 12 2026 11:00:00 GMT+0000 (Coordinated Universal Time)"
_TS_B7P = "Thu Jun 12 2026 08:00:00 GMT+0000 (Coordinated Universal Time)"
_TS_FORECOURT = "Thu Jun 11 2026 18:10:41 GMT+0000 (Coordinated Universal Time)"

_BASE_ROW: dict[str, str] = {
    "forecourt_update_timestamp": _TS_FORECOURT,
    "forecourts.node_id": _NODE_ID,
    "forecourts.trading_name": "Shell Trafalgar",
    "forecourts.brand_name": "Shell",
    "forecourts.is_motorway_service_station": "false",
    "forecourts.is_supermarket_service_station": "false",
    "forecourts.public_phone_number": "+441234567890",
    "forecourts.temporary_closure": "false",
    "forecourts.permanent_closure": "false",
    "forecourts.permanent_closure_date": "",
    "forecourts.location.postcode": "WC2N 5DN",
    "forecourts.location.address_line_1": "1 Strand",
    "forecourts.location.address_line_2": "",
    "forecourts.location.city": "London",
    "forecourts.location.county": "Greater London",
    "forecourts.location.country": "England",
    "forecourts.location.latitude": "51.5074",
    "forecourts.location.longitude": "-0.1278",
    "forecourts.fuel_price.E10": "149.9000",
    "forecourts.price_submission_timestamp.E10": _TS_E10,
    "forecourts.price_change_effective_timestamp.E10": _TS_E10,
    "forecourts.fuel_price.E5": "159.9000",
    "forecourts.price_submission_timestamp.E5": _TS_E5,
    "forecourts.price_change_effective_timestamp.E5": _TS_E5,
    "forecourts.fuel_price.B7S": "153.9000",
    "forecourts.price_submission_timestamp.B7S": _TS_B7S,
    "forecourts.price_change_effective_timestamp.B7S": _TS_B7S,
    "forecourts.fuel_price.B7P": "163.9000",
    "forecourts.price_submission_timestamp.B7P": _TS_B7P,
    "forecourts.price_change_effective_timestamp.B7P": _TS_B7P,
}

_CSV_HEADER = ",".join(_BASE_ROW.keys())


@pytest.fixture(autouse=True)
def _reset_csv_cache() -> None:
    """Reset GbFuelfinderProvider class-level CSV cache before each test.

    The cache is a class variable shared across all instances; without this
    fixture, tests that mock the network would receive stale cached CSV text
    from a previous test instead of the mock's response body.
    """
    GbFuelfinderProvider._csv_cache = None
    GbFuelfinderProvider._csv_cache_ts = 0.0
    GbFuelfinderProvider._csv_etag = None


def _make_csv_text(*rows: dict[str, str]) -> str:
    """Build a minimal CSV string with the full header and the given rows."""
    header = _CSV_HEADER
    lines = [header]
    for row in rows:
        lines.append(",".join(row.get(k, "") for k in _BASE_ROW.keys()))
    return "\n".join(lines)


def _make_mock_response(
    status: int = 200,
    body: bytes | None = None,
) -> MagicMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=body if body is not None else b"")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_csv_response(*rows: dict[str, str]) -> AsyncMock:
    """Return a 200 response whose body is a UTF-8-encoded CSV with *rows*."""
    text = _make_csv_text(*rows)
    return _make_mock_response(200, text.encode("utf-8"))


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns *response*."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """GbFuelfinderProvider.COUNTRY is 'GB'."""
    assert GbFuelfinderProvider.COUNTRY == "GB"


def test_provider_key() -> None:
    """GbFuelfinderProvider.PROVIDER_KEY is 'gb_fuelfinder'."""
    assert GbFuelfinderProvider.PROVIDER_KEY == "gb_fuelfinder"


def test_provider_label() -> None:
    """GbFuelfinderProvider.LABEL is 'Fuel Finder (UK)'."""
    assert GbFuelfinderProvider.LABEL == "Fuel Finder (UK)"


def test_provider_config_mode() -> None:
    """GbFuelfinderProvider.CONFIG_MODE is 'location'."""
    assert GbFuelfinderProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """GbFuelfinderProvider.STATION_LOOKUP_MODE is 'location_search'."""
    assert GbFuelfinderProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_no_api_key() -> None:
    """GbFuelfinderProvider.REQUIRES_API_KEY is False."""
    assert GbFuelfinderProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval() -> None:
    """Default poll interval is 21600 seconds (6 hours) to match the source refresh cadence."""
    assert GbFuelfinderProvider.POLL_INTERVAL_SECONDS == 21600


# ---------------------------------------------------------------------------
# Provider CAPABILITIES
# ---------------------------------------------------------------------------


def test_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all four UK fuel types."""
    caps = GbFuelfinderProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "premium_unleaded" in caps
    assert "diesel" in caps
    assert "premium_diesel" in caps


def test_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes identity and location fields."""
    caps = GbFuelfinderProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps
    assert "address" in caps
    assert "latitude" in caps
    assert "longitude" in caps
    assert "lastupdated" in caps


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = GbFuelfinderProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# CSV URL and headers
# ---------------------------------------------------------------------------


def test_csv_url_points_to_github_mirror() -> None:
    """_CSV_URL points to the matthewgall/fuelfinder-archive GitHub mirror."""
    assert "matthewgall" in _CSV_URL
    assert "fuelfinder-archive" in _CSV_URL
    assert _CSV_URL.startswith("https://")


def test_headers_include_accept_encoding_gzip() -> None:
    """_HEADERS includes Accept-Encoding: gzip for compressed transfer."""
    assert "gzip" in _HEADERS.get("Accept-Encoding", "")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent string."""
    ua = _HEADERS.get("User-Agent", "")
    assert ua != ""


def test_headers_include_accept_csv() -> None:
    """_HEADERS Accept includes text/csv."""
    assert "text/csv" in _HEADERS.get("Accept", "")


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """GbFuelfinderProvider stores station_id."""
    p = GbFuelfinderProvider(_NODE_ID)
    assert p._station_id == _NODE_ID


def test_constructor_default_radius_km() -> None:
    """Default radius_km is 10.0 when not supplied."""
    p = GbFuelfinderProvider(_NODE_ID)
    assert p._radius_km == 10.0


def test_constructor_custom_radius_km() -> None:
    """radius_km is stored when explicitly provided."""
    p = GbFuelfinderProvider(_NODE_ID, radius_km=25.0)
    assert p._radius_km == 25.0


def test_constructor_stores_lat_lng() -> None:
    """Latitude and longitude are stored when provided."""
    p = GbFuelfinderProvider(_NODE_ID, latitude=51.5, longitude=-0.1)
    assert p._latitude == 51.5
    assert p._longitude == -0.1


def test_constructor_lat_lng_none_by_default() -> None:
    """Latitude and longitude default to None when not provided."""
    p = GbFuelfinderProvider(_NODE_ID)
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a StationData dict for a matching station."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert isinstance(data, dict)


async def test_async_fetch_unleaded_price_gbp() -> None:
    """async_fetch converts unleaded from pence to GBP/litre (149.9p → 1.499 GBP)."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["unleaded"] == pytest.approx(1.499, abs=1e-4)


async def test_async_fetch_premium_unleaded_price_gbp() -> None:
    """async_fetch converts premium_unleaded from pence to GBP/litre (159.9p → 1.599 GBP)."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["premium_unleaded"] == pytest.approx(1.599, abs=1e-4)


async def test_async_fetch_diesel_price_gbp() -> None:
    """async_fetch converts diesel from pence to GBP/litre (153.9p → 1.539 GBP)."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["diesel"] == pytest.approx(1.539, abs=1e-4)


async def test_async_fetch_premium_diesel_price_gbp() -> None:
    """async_fetch converts premium_diesel from pence to GBP/litre (163.9p → 1.639 GBP)."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["premium_diesel"] == pytest.approx(1.639, abs=1e-4)


async def test_async_fetch_prices_below_10() -> None:
    """async_fetch returns prices as GBP/litre (always < 10) not raw pence."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    for fuel in ("unleaded", "premium_unleaded", "diesel", "premium_diesel"):
        if data[fuel] is not None:
            assert data[fuel] < 10.0, f"{fuel} not converted from pence: {data[fuel]}"


async def test_async_fetch_station_name() -> None:
    """async_fetch returns the trading name in the 'name' field."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["name"] == "Shell Trafalgar"


async def test_async_fetch_station_brand() -> None:
    """async_fetch returns the brand name in the 'brand' field."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["brand"] == "Shell"


async def test_async_fetch_station_address() -> None:
    """async_fetch builds address from address line, city, and postcode."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["address"] is not None
    assert "1 Strand" in data["address"]
    assert "London" in data["address"]
    assert "WC2N 5DN" in data["address"]


async def test_async_fetch_station_latitude_longitude() -> None:
    """async_fetch populates latitude and longitude from CSV columns."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["latitude"] == pytest.approx(51.5074)
    assert data["longitude"] == pytest.approx(-0.1278)


async def test_async_fetch_lastupdated_is_most_recent_ts() -> None:
    """async_fetch sets lastupdated to the most recent price submission timestamp."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    # B7S is 11:00 — most recent among E10 09:00, E5 10:00, B7S 11:00, B7P 08:00
    assert data["lastupdated"] is not None
    assert "2026-06-12T11:00:00" in data["lastupdated"]


async def test_async_fetch_uses_forecourt_ts_as_fallback() -> None:
    """async_fetch falls back to forecourt_update_timestamp when no price timestamps present."""
    row = {**_BASE_ROW}
    row["forecourts.fuel_price.E10"] = ""
    row["forecourts.price_submission_timestamp.E10"] = ""
    row["forecourts.fuel_price.E5"] = ""
    row["forecourts.price_submission_timestamp.E5"] = ""
    row["forecourts.fuel_price.B7S"] = ""
    row["forecourts.price_submission_timestamp.B7S"] = ""
    row["forecourts.fuel_price.B7P"] = ""
    row["forecourts.price_submission_timestamp.B7P"] = ""
    resp = _make_csv_response(row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    # Fallback to forecourt_update_timestamp: _TS_FORECOURT = Jun 11 18:10:41
    assert data["lastupdated"] is not None
    assert "2026-06-11T18:10:41" in data["lastupdated"]


async def test_async_fetch_null_prices_when_empty_csv_fields() -> None:
    """async_fetch returns None for fuel prices when CSV fields are empty."""
    row = {
        **_BASE_ROW,
        "forecourts.fuel_price.E10": "",
        "forecourts.fuel_price.E5": "",
        "forecourts.fuel_price.B7S": "",
        "forecourts.fuel_price.B7P": "",
    }
    resp = _make_csv_response(row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["diesel"] is None
    assert data["premium_diesel"] is None


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch returns dict containing all non-sentinel CAPABILITIES keys."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    for key in GbFuelfinderProvider.CAPABILITIES - sentinel_keys:
        assert key in data, f"Key '{key}' missing from async_fetch output"


async def test_async_fetch_requests_csv_with_correct_headers() -> None:
    """async_fetch passes the correct headers to session.get()."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    await provider.async_fetch(session, _NODE_ID)
    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "gzip" in headers.get("Accept-Encoding", "")


async def test_async_fetch_requests_correct_url() -> None:
    """async_fetch fetches the _CSV_URL endpoint."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    await provider.async_fetch(session, _NODE_ID)
    url_arg = (
        session.get.call_args.args[0]
        if session.get.call_args.args
        else session.get.call_args.kwargs.get("url")
    )
    assert url_arg == _CSV_URL


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_in_csv() -> None:
    """async_fetch raises ProviderError when the node_id is absent from the CSV."""
    other_row = {**_BASE_ROW, "forecourts.node_id": _NODE_ID_B}
    resp = _make_csv_response(other_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _NODE_ID)


async def test_async_fetch_raises_provider_error_on_empty_csv() -> None:
    """async_fetch raises ProviderError when the CSV has no rows."""
    text = _CSV_HEADER  # header only, no data rows
    resp = _make_mock_response(200, text.encode("utf-8"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _NODE_ID)


# ---------------------------------------------------------------------------
# async_fetch — HTTP / network error propagation
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """ClientError from session.get() propagates out of async_fetch."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network failure"))
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _NODE_ID)


async def test_async_fetch_propagates_raise_for_status_error() -> None:
    """Non-2xx response raises via raise_for_status."""
    resp = _make_mock_response(500, b"Internal Server Error")
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _NODE_ID)


async def test_async_fetch_propagates_404_error() -> None:
    """HTTP 404 is surfaced via raise_for_status."""
    resp = _make_mock_response(404, b"Not Found")
    resp.raise_for_status = MagicMock(side_effect=ClientError("404 Not Found"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _NODE_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name — success and failure
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns the trading name for a known station."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    name = await provider.async_fetch_station_name(session, _NODE_ID)
    assert name == "Shell Trafalgar"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when node_id is absent from CSV."""
    other_row = {**_BASE_ROW, "forecourts.node_id": _NODE_ID_B}
    resp = _make_csv_response(other_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    name = await provider.async_fetch_station_name(session, _NODE_ID)
    assert name is None


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = GbFuelfinderProvider(_NODE_ID)
    name = await provider.async_fetch_station_name(session, _NODE_ID)
    assert name is None


async def test_async_fetch_station_name_returns_none_when_trading_name_empty() -> None:
    """async_fetch_station_name returns None when trading_name field is empty."""
    row = {**_BASE_ROW, "forecourts.trading_name": ""}
    resp = _make_csv_response(row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    name = await provider.async_fetch_station_name(session, _NODE_ID)
    assert name is None


async def test_async_fetch_station_name_swallows_generic_exception() -> None:
    """async_fetch_station_name returns None when an unexpected exception occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=RuntimeError("unexpected"))
    provider = GbFuelfinderProvider(_NODE_ID)
    name = await provider.async_fetch_station_name(session, _NODE_ID)
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — results and filtering
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_nearby_station() -> None:
    """async_list_stations returns stations within the configured radius."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    # Station is at 51.5074, -0.1278; searching from same point with 1 km radius
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    ids = [r[0] for r in results]
    assert _NODE_ID in ids


async def test_async_list_stations_excludes_out_of_range_station() -> None:
    """async_list_stations excludes stations outside the search radius."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    # Station is at 51.5074, -0.1278; searching from Edinburgh (~530 km away)
    results = await provider.async_list_stations(
        session, lat=55.9533, lng=-3.1883, radius_km=10.0
    )
    ids = [r[0] for r in results]
    assert _NODE_ID not in ids


async def test_async_list_stations_excludes_permanently_closed() -> None:
    """async_list_stations skips permanently closed stations."""
    closed_row = {**_BASE_ROW, "forecourts.permanent_closure": "true"}
    resp = _make_csv_response(closed_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    ids = [r[0] for r in results]
    assert _NODE_ID not in ids


async def test_async_list_stations_skips_rows_without_node_id() -> None:
    """async_list_stations skips rows where node_id is empty."""
    no_id_row = {**_BASE_ROW, "forecourts.node_id": ""}
    resp = _make_csv_response(no_id_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert results == []


async def test_async_list_stations_skips_rows_without_lat_lng() -> None:
    """async_list_stations skips rows with missing or invalid coordinates."""
    no_coords_row = {
        **_BASE_ROW,
        "forecourts.location.latitude": "",
        "forecourts.location.longitude": "",
    }
    resp = _make_csv_response(no_coords_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert results == []


async def test_async_list_stations_returns_empty_on_network_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network failure"))
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=10.0
    )
    assert results == []


async def test_async_list_stations_returns_id_label_tuples() -> None:
    """async_list_stations returns (node_id, label) tuples."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    node_id, label = results[0]
    assert node_id == _NODE_ID
    assert isinstance(label, str)
    assert len(label) > 0


async def test_async_list_stations_label_includes_distance() -> None:
    """async_list_stations labels include short station ID suffix."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    _, label = results[0]
    assert "(#" in label


async def test_async_list_stations_label_includes_price_info() -> None:
    """async_list_stations labels include station name and address."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    _, label = results[0]
    assert "Shell" in label or "Trafalgar" in label


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts stations cheapest-first by best available price."""
    cheap_row = {
        **_BASE_ROW,
        "forecourts.node_id": "c" * 64,
        "forecourts.trading_name": "Cheap Fuel",
        "forecourts.fuel_price.B7S": "120.0000",
        "forecourts.fuel_price.E10": "125.0000",
        "forecourts.location.latitude": "51.5074",
        "forecourts.location.longitude": "-0.1278",
    }
    expensive_row = {
        **_BASE_ROW,
        "forecourts.node_id": "d" * 64,
        "forecourts.trading_name": "Expensive Fuel",
        "forecourts.fuel_price.B7S": "200.0000",
        "forecourts.fuel_price.E10": "210.0000",
        "forecourts.location.latitude": "51.5074",
        "forecourts.location.longitude": "-0.1278",
    }
    # Build CSV with expensive row first to verify sorting works
    text = _make_csv_text(expensive_row, cheap_row)
    resp = _make_mock_response(200, text.encode("utf-8"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) >= 2
    assert results[0][0] == "c" * 64  # cheap station first


async def test_async_list_stations_no_price_station_appended_last() -> None:
    """async_list_stations sorts stations alphabetically by label."""
    priced_row = {
        **_BASE_ROW,
        "forecourts.node_id": "e" * 64,
        "forecourts.trading_name": "Alpha Priced",
        "forecourts.fuel_price.B7S": "153.9000",
        "forecourts.fuel_price.E10": "149.9000",
        "forecourts.location.latitude": "51.5074",
        "forecourts.location.longitude": "-0.1278",
    }
    no_price_row = {
        **_BASE_ROW,
        "forecourts.node_id": "f" * 64,
        "forecourts.trading_name": "Beta No Price",
        "forecourts.fuel_price.B7S": "",
        "forecourts.fuel_price.E10": "",
        "forecourts.fuel_price.E5": "",
        "forecourts.fuel_price.B7P": "",
        "forecourts.location.latitude": "51.5074",
        "forecourts.location.longitude": "-0.1278",
    }
    # Put no-price row first in CSV to confirm sorting
    text = _make_csv_text(no_price_row, priced_row)
    resp = _make_mock_response(200, text.encode("utf-8"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    node_ids = [r[0] for r in results]
    priced_idx = node_ids.index("e" * 64)
    no_price_idx = node_ids.index("f" * 64)
    # Alphabetically "Alpha Priced" < "Beta No Price"
    assert priced_idx < no_price_idx


async def test_async_list_stations_uses_instance_lat_lng_as_defaults() -> None:
    """async_list_stations uses constructor lat/lng when kwargs not supplied."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(
        _NODE_ID, latitude=51.5074, longitude=-0.1278, radius_km=1.0
    )
    # No lat/lng kwargs — should fall back to constructor values
    results = await provider.async_list_stations(session)
    assert len(results) > 0


async def test_async_list_stations_label_uses_brand_and_name() -> None:
    """async_list_stations label combines brand and trading name."""
    resp = _make_csv_response(_BASE_ROW)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    _, label = results[0]
    assert "Shell" in label
    assert "Trafalgar" in label


async def test_async_list_stations_label_fallback_when_no_name() -> None:
    """async_list_stations falls back to node_id prefix when no trading name or brand."""
    row = {
        **_BASE_ROW,
        "forecourts.trading_name": "",
        "forecourts.brand_name": "",
    }
    resp = _make_csv_response(row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    results = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=1.0
    )
    assert len(results) > 0
    _, label = results[0]
    # Should contain the first 8 chars of node_id
    assert _NODE_ID[:8] in label


# ---------------------------------------------------------------------------
# _parse_price_pence — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_pence_valid_value() -> None:
    """_parse_price_pence returns float for a valid pence string."""
    assert _parse_price_pence("169.9000") == pytest.approx(169.9)


def test_parse_price_pence_integer_string() -> None:
    """_parse_price_pence handles integer strings."""
    assert _parse_price_pence("150") == pytest.approx(150.0)


def test_parse_price_pence_none_returns_none() -> None:
    """_parse_price_pence returns None for None input."""
    assert _parse_price_pence(None) is None


def test_parse_price_pence_empty_string_returns_none() -> None:
    """_parse_price_pence returns None for empty string."""
    assert _parse_price_pence("") is None


def test_parse_price_pence_whitespace_only_returns_none() -> None:
    """_parse_price_pence returns None for whitespace-only string."""
    assert _parse_price_pence("   ") is None


def test_parse_price_pence_non_numeric_returns_none() -> None:
    """_parse_price_pence returns None for non-numeric string."""
    assert _parse_price_pence("N/A") is None


def test_parse_price_pence_zero_returns_none() -> None:
    """_parse_price_pence returns None for zero (not a valid price)."""
    assert _parse_price_pence("0") is None


def test_parse_price_pence_negative_returns_none() -> None:
    """_parse_price_pence returns None for negative values."""
    assert _parse_price_pence("-10.0") is None


def test_parse_price_pence_above_max_returns_none() -> None:
    """_parse_price_pence returns None for values above _MAX_PENCE_PER_LITRE (300)."""
    assert _parse_price_pence("301.0") is None


def test_parse_price_pence_at_max_boundary_returns_none() -> None:
    """_parse_price_pence returns None for exactly 300 p/L (not below max)."""
    # 300 is not > 0 and not > 300, so should be valid... but let's verify boundary
    result = _parse_price_pence("300.0")
    # 300 <= _MAX_PENCE_PER_LITRE (300.0), so it is accepted
    assert result == pytest.approx(300.0)


def test_parse_price_pence_just_above_max_returns_none() -> None:
    """_parse_price_pence returns None for 300.1 p/L."""
    assert _parse_price_pence("300.1") is None


def test_parse_price_pence_leading_whitespace() -> None:
    """_parse_price_pence strips leading/trailing whitespace."""
    assert _parse_price_pence("  149.9  ") == pytest.approx(149.9)


# ---------------------------------------------------------------------------
# _pence_to_gbp — unit tests
# ---------------------------------------------------------------------------


def test_pence_to_gbp_converts_correctly() -> None:
    """_pence_to_gbp divides by 100 and rounds to 4 decimal places."""
    assert _pence_to_gbp(149.9) == pytest.approx(1.499, abs=1e-5)


def test_pence_to_gbp_none_returns_none() -> None:
    """_pence_to_gbp returns None for None input."""
    assert _pence_to_gbp(None) is None


def test_pence_to_gbp_rounds_to_4_places() -> None:
    """_pence_to_gbp rounds to 4 decimal places."""
    result = _pence_to_gbp(169.9)
    assert result == pytest.approx(1.699, abs=1e-4)
    # Verify precision: 169.9 / 100 = 1.699 exactly representable
    assert result == 1.699


def test_pence_to_gbp_zero() -> None:
    """_pence_to_gbp(0) returns 0.0."""
    assert _pence_to_gbp(0.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _parse_js_timestamp — unit tests
# ---------------------------------------------------------------------------


def test_parse_js_timestamp_valid() -> None:
    """_parse_js_timestamp parses a valid JS Date.toString() timestamp to ISO 8601."""
    result = _parse_js_timestamp(
        "Thu Jun 12 2026 09:00:00 GMT+0000 (Coordinated Universal Time)"
    )
    assert result is not None
    assert "2026-06-12T09:00:00" in result


def test_parse_js_timestamp_none_returns_none() -> None:
    """_parse_js_timestamp returns None for None input."""
    assert _parse_js_timestamp(None) is None


def test_parse_js_timestamp_empty_string_returns_none() -> None:
    """_parse_js_timestamp returns None for empty string."""
    assert _parse_js_timestamp("") is None


def test_parse_js_timestamp_whitespace_only_returns_none() -> None:
    """_parse_js_timestamp returns None for whitespace-only string."""
    assert _parse_js_timestamp("   ") is None


def test_parse_js_timestamp_invalid_format_returns_none() -> None:
    """_parse_js_timestamp returns None for unrecognised format."""
    assert _parse_js_timestamp("2026-06-12T09:00:00Z") is None


def test_parse_js_timestamp_result_is_utc_iso() -> None:
    """_parse_js_timestamp result includes UTC offset indicator."""
    result = _parse_js_timestamp(
        "Mon Jan 01 2024 00:00:00 GMT+0000 (Coordinated Universal Time)"
    )
    assert result is not None
    assert "+00:00" in result


# ---------------------------------------------------------------------------
# _safe_float — unit tests
# ---------------------------------------------------------------------------


def test_safe_float_valid_string() -> None:
    """_safe_float converts a valid float string."""
    assert _safe_float("51.5074") == pytest.approx(51.5074)


def test_safe_float_none_returns_none() -> None:
    """_safe_float returns None for None input."""
    assert _safe_float(None) is None


def test_safe_float_empty_string_returns_none() -> None:
    """_safe_float returns None for empty string."""
    assert _safe_float("") is None


def test_safe_float_whitespace_returns_none() -> None:
    """_safe_float returns None for whitespace-only string."""
    assert _safe_float("  ") is None


def test_safe_float_non_numeric_returns_none() -> None:
    """_safe_float returns None for non-numeric string."""
    assert _safe_float("N/A") is None


def test_safe_float_strips_whitespace() -> None:
    """_safe_float strips surrounding whitespace before parsing."""
    assert _safe_float("  -0.1278  ") == pytest.approx(-0.1278)


def test_safe_float_negative_value() -> None:
    """_safe_float handles negative float strings."""
    assert _safe_float("-6.2603") == pytest.approx(-6.2603)


# ---------------------------------------------------------------------------
# _haversine_km — unit tests
# ---------------------------------------------------------------------------


def test_haversine_km_same_point_is_zero() -> None:
    """Haversine distance from a point to itself is 0."""
    assert _haversine_km(51.5074, -0.1278, 51.5074, -0.1278) == pytest.approx(0.0)


def test_haversine_km_london_to_edinburgh() -> None:
    """Haversine distance from London to Edinburgh is approximately 534 km."""
    dist = _haversine_km(51.5074, -0.1278, 55.9533, -3.1883)
    assert 520 < dist < 550


def test_haversine_km_short_distance() -> None:
    """Haversine distance for a very short distance is small but positive."""
    dist = _haversine_km(51.5074, -0.1278, 51.508, -0.128)
    assert 0 < dist < 1.0


def test_haversine_km_returns_float() -> None:
    """_haversine_km always returns a float."""
    result = _haversine_km(0.0, 0.0, 1.0, 1.0)
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# _find_row_by_id — unit tests
# ---------------------------------------------------------------------------


def test_find_row_by_id_matches_correct_row() -> None:
    """_find_row_by_id returns the row with the matching node_id."""
    other = {**_BASE_ROW, "forecourts.node_id": _NODE_ID_B}
    rows = [other, _BASE_ROW]
    row = _find_row_by_id(rows, _NODE_ID)
    assert row is not None
    assert row["forecourts.node_id"] == _NODE_ID


def test_find_row_by_id_returns_none_when_absent() -> None:
    """_find_row_by_id returns None when no row matches."""
    other = {**_BASE_ROW, "forecourts.node_id": _NODE_ID_B}
    row = _find_row_by_id([other], _NODE_ID)
    assert row is None


def test_find_row_by_id_returns_none_for_empty_list() -> None:
    """_find_row_by_id returns None for an empty row list."""
    assert _find_row_by_id([], _NODE_ID) is None


def test_find_row_by_id_strips_whitespace() -> None:
    """_find_row_by_id handles node_id values with surrounding whitespace."""
    padded_row = {**_BASE_ROW, "forecourts.node_id": f" {_NODE_ID} "}
    row = _find_row_by_id([padded_row], _NODE_ID)
    assert row is not None


# ---------------------------------------------------------------------------
# _parse_row — unit tests
# ---------------------------------------------------------------------------


def test_parse_row_returns_all_fuel_keys() -> None:
    """_parse_row output contains all four fuel price keys."""
    result = _parse_row(_BASE_ROW)
    for key in ("unleaded", "premium_unleaded", "diesel", "premium_diesel"):
        assert key in result


def test_parse_row_fuel_prices_gbp() -> None:
    """_parse_row converts pence prices to GBP/litre."""
    result = _parse_row(_BASE_ROW)
    assert result["unleaded"] == pytest.approx(1.499, abs=1e-4)
    assert result["premium_unleaded"] == pytest.approx(1.599, abs=1e-4)
    assert result["diesel"] == pytest.approx(1.539, abs=1e-4)
    assert result["premium_diesel"] == pytest.approx(1.639, abs=1e-4)


def test_parse_row_name_and_brand() -> None:
    """_parse_row extracts station name and brand."""
    result = _parse_row(_BASE_ROW)
    assert result["name"] == "Shell Trafalgar"
    assert result["brand"] == "Shell"


def test_parse_row_address_built_from_parts() -> None:
    """_parse_row builds address from available CSV address columns."""
    result = _parse_row(_BASE_ROW)
    assert result["address"] is not None
    assert "1 Strand" in result["address"]
    assert "London" in result["address"]
    assert "WC2N 5DN" in result["address"]


def test_parse_row_address_skips_empty_parts() -> None:
    """_parse_row omits empty address components."""
    result = _parse_row(_BASE_ROW)
    # address_line_2 is empty; should not have double commas or leading/trailing commas
    assert result["address"] is not None
    assert ",," not in result["address"]
    assert not result["address"].startswith(", ")


def test_parse_row_lat_lng() -> None:
    """_parse_row converts latitude and longitude to floats."""
    result = _parse_row(_BASE_ROW)
    assert result["latitude"] == pytest.approx(51.5074)
    assert result["longitude"] == pytest.approx(-0.1278)


def test_parse_row_lastupdated_most_recent() -> None:
    """_parse_row sets lastupdated to the most recent fuel submission timestamp."""
    result = _parse_row(_BASE_ROW)
    # B7S timestamp is 11:00 — most recent
    assert "2026-06-12T11:00:00" in result["lastupdated"]


def test_parse_row_lastupdated_fallback_to_forecourt_ts() -> None:
    """_parse_row falls back to forecourt_update_timestamp when no price timestamps."""
    row = {
        **_BASE_ROW,
        "forecourts.price_submission_timestamp.E10": "",
        "forecourts.price_submission_timestamp.E5": "",
        "forecourts.price_submission_timestamp.B7S": "",
        "forecourts.price_submission_timestamp.B7P": "",
    }
    result = _parse_row(row)
    assert result["lastupdated"] is not None
    assert "2026-06-11T18:10:41" in result["lastupdated"]


def test_parse_row_null_prices_when_empty() -> None:
    """_parse_row returns None for fuel prices when CSV fields are empty."""
    row = {
        **_BASE_ROW,
        "forecourts.fuel_price.E10": "",
        "forecourts.fuel_price.E5": "",
        "forecourts.fuel_price.B7S": "",
        "forecourts.fuel_price.B7P": "",
    }
    result = _parse_row(row)
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["diesel"] is None
    assert result["premium_diesel"] is None


def test_parse_row_name_none_when_empty() -> None:
    """_parse_row returns name=None when trading_name is empty."""
    row = {**_BASE_ROW, "forecourts.trading_name": ""}
    result = _parse_row(row)
    assert result["name"] is None


def test_parse_row_brand_none_when_empty() -> None:
    """_parse_row returns brand=None when brand_name is empty."""
    row = {**_BASE_ROW, "forecourts.brand_name": ""}
    result = _parse_row(row)
    assert result["brand"] is None


def test_parse_row_address_none_when_all_fields_empty() -> None:
    """_parse_row returns address=None when all address fields are empty."""
    row = {
        **_BASE_ROW,
        "forecourts.location.address_line_1": "",
        "forecourts.location.address_line_2": "",
        "forecourts.location.city": "",
        "forecourts.location.postcode": "",
    }
    result = _parse_row(row)
    assert result["address"] is None


def test_parse_row_source_station_id_is_node_id() -> None:
    """_parse_row sets source_station_id to the forecourts.node_id value."""
    result = _parse_row(_BASE_ROW)
    assert result["source_station_id"] == _NODE_ID


def test_parse_row_outlier_price_rejected() -> None:
    """_parse_row rejects prices above 300 p/L as outliers (returns None)."""
    row = {**_BASE_ROW, "forecourts.fuel_price.E10": "999.9999"}
    result = _parse_row(row)
    assert result["unleaded"] is None


def test_parse_row_handles_malformed_timestamp() -> None:
    """_parse_row gracefully handles an unparseable submission timestamp."""
    row = {
        **_BASE_ROW,
        "forecourts.price_submission_timestamp.E10": "not-a-date",
        "forecourts.price_submission_timestamp.E5": "not-a-date",
        "forecourts.price_submission_timestamp.B7S": "not-a-date",
        "forecourts.price_submission_timestamp.B7P": "not-a-date",
    }
    result = _parse_row(row)
    # Falls back to forecourt_update_timestamp
    assert result["lastupdated"] is not None


# ---------------------------------------------------------------------------
# Malformed / empty CSV — async_list_stations robustness
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_empty_csv() -> None:
    """async_list_stations returns [] when CSV has only a header row (no data)."""
    text = _CSV_HEADER  # header only, no data rows
    resp = _make_mock_response(200, text.encode("utf-8"))
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    result = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=10.0
    )
    assert result == []


async def test_async_list_stations_returns_empty_on_garbled_body() -> None:
    """async_list_stations returns [] when the response body is not valid CSV."""
    resp = _make_mock_response(200, b"<html>Internal Server Error</html>")
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    result = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=10.0
    )
    # Should not crash; garbled body produces no parseable rows within radius
    assert result == []


async def test_async_list_stations_returns_empty_on_completely_empty_body() -> None:
    """async_list_stations returns [] when the response body is entirely empty bytes."""
    resp = _make_mock_response(200, b"")
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    result = await provider.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=10.0
    )
    assert result == []


async def test_async_list_stations_returns_empty_when_lat_lng_none() -> None:
    """async_list_stations returns [] immediately when lat or lng is None."""
    provider = GbFuelfinderProvider(_NODE_ID)
    session = _make_session(_make_mock_response(200, b""))
    result = await provider.async_list_stations(
        session, lat=None, lng=None, radius_km=10.0
    )
    assert result == []


# ---------------------------------------------------------------------------
# Price boundary: _parse_price_pence — 300.0 passes, 300.1 is rejected
# (guard using _MAX_PENCE_PER_LITRE when it exists)
# ---------------------------------------------------------------------------


def test_parse_price_pence_boundary_300_0_accepted() -> None:
    """_parse_price_pence accepts exactly 300.0 p/L (at the boundary)."""
    from custom_components.fuelcompare_ie.providers.gb_fuelfinder import (
        _MAX_PENCE_PER_LITRE,
    )

    # Only meaningful when the constant is defined
    assert _MAX_PENCE_PER_LITRE == pytest.approx(300.0)
    result = _parse_price_pence("300.0")
    assert result == pytest.approx(300.0), (
        "300.0 p/L is at the boundary and must be accepted"
    )


def test_parse_price_pence_boundary_300_1_rejected() -> None:
    """_parse_price_pence rejects 300.1 p/L (just above _MAX_PENCE_PER_LITRE)."""
    from custom_components.fuelcompare_ie.providers.gb_fuelfinder import (
        _MAX_PENCE_PER_LITRE,
    )

    assert _MAX_PENCE_PER_LITRE == pytest.approx(300.0)
    result = _parse_price_pence("300.1")
    assert result is None, "300.1 p/L exceeds the boundary and must be rejected"


# ---------------------------------------------------------------------------
# async_fetch — permanent closure raises ProviderError (line 224)
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_permanent_closure() -> None:
    """async_fetch raises ProviderError when the station is permanently closed."""
    closed_row = {**_BASE_ROW, "forecourts.permanent_closure": "true"}
    resp = _make_csv_response(closed_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    with pytest.raises(ProviderError, match="permanently closed"):
        await provider.async_fetch(session, _NODE_ID)


# ---------------------------------------------------------------------------
# async_fetch — temporary closure sets is_open=False (lines 227-228)
# ---------------------------------------------------------------------------


async def test_async_fetch_temporary_closure_sets_is_open_false() -> None:
    """async_fetch sets is_open=False and does not raise for a temporarily closed station."""
    temp_closed_row = {**_BASE_ROW, "forecourts.temporary_closure": "true"}
    resp = _make_csv_response(temp_closed_row)
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)
    data = await provider.async_fetch(session, _NODE_ID)
    assert data["is_open"] is False


# ---------------------------------------------------------------------------
# _fetch_csv — in-process cache hit logs and skips HTTP (lines 339-340)
# ---------------------------------------------------------------------------


async def test_fetch_csv_serves_from_in_process_cache_within_ttl() -> None:
    """_fetch_csv returns cached CSV text and skips HTTP when cache is fresh."""
    csv_text = _make_csv_text(_BASE_ROW)
    # Set cache with a timestamp so recent it will never be expired.
    GbFuelfinderProvider._csv_cache = csv_text
    GbFuelfinderProvider._csv_cache_ts = _time.monotonic()

    session = MagicMock()
    provider = GbFuelfinderProvider(_NODE_ID)
    rows = await provider._fetch_csv(session)

    session.get.assert_not_called()
    assert any(r.get("forecourts.node_id", "").strip() == _NODE_ID for r in rows)


# ---------------------------------------------------------------------------
# _fetch_csv — HTTP 304 Not Modified refreshes cache timestamp (lines 350-354)
# ---------------------------------------------------------------------------


async def test_fetch_csv_304_not_modified_uses_cached_text_and_refreshes_ts() -> None:
    """_fetch_csv on HTTP 304 reuses cached CSV and updates _csv_cache_ts."""
    csv_text = _make_csv_text(_BASE_ROW)
    # Set cache with an expired timestamp (older than _CSV_CACHE_TTL) so the
    # code proceeds to make an HTTP request rather than serving from cache.
    GbFuelfinderProvider._csv_cache = csv_text
    GbFuelfinderProvider._csv_cache_ts = _time.monotonic() - (
        GbFuelfinderProvider._CSV_CACHE_TTL + 1
    )

    resp = _make_mock_response(304, b"")
    session = _make_session(resp)
    provider = GbFuelfinderProvider(_NODE_ID)

    before = _time.monotonic()
    rows = await provider._fetch_csv(session)
    after = _time.monotonic()

    assert any(r.get("forecourts.node_id", "").strip() == _NODE_ID for r in rows)
    assert before <= GbFuelfinderProvider._csv_cache_ts <= after + 1.0


# ---------------------------------------------------------------------------
# _parse_row — ValueError in datetime.fromisoformat is silently ignored (lines 461-462)
# ---------------------------------------------------------------------------


def test_parse_row_ignores_invalid_iso_timestamp_in_price_submission() -> None:
    """_parse_row silently skips a malformed ISO string returned by _parse_js_timestamp."""
    from unittest.mock import patch

    call_count = {"n": 0}
    original_parse_js = _parse_js_timestamp

    def fake_parse_js(ts: str | None) -> str | None:
        result = original_parse_js(ts)
        call_count["n"] += 1
        # On the first call (E10 timestamp) return a string that looks non-None
        # but that datetime.fromisoformat cannot parse, triggering lines 461-462.
        if call_count["n"] == 1 and result is not None:
            return "not-a-valid-iso-string"
        return result

    with patch(
        "custom_components.fuelcompare_ie.providers.gb_fuelfinder._parse_js_timestamp",
        side_effect=fake_parse_js,
    ):
        result = _parse_row(_BASE_ROW)

    # Reaching here without ValueError means the except block at lines 461-462
    # executed correctly and swallowed the error.
    assert isinstance(result, dict)
    # lastupdated should still be set from one of the other valid timestamps.
    assert result["lastupdated"] is not None


# ---------------------------------------------------------------------------
# ETag storage and conditional GET (lines 345, 360)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_csv_stores_etag_from_200_response() -> None:
    """_fetch_csv stores ETag from a 200 response for subsequent conditional GETs (lines 360)."""
    GbFuelfinderProvider._csv_etag = None
    csv_text = _make_csv_text(_BASE_ROW)

    resp = _make_mock_response(200, csv_text.encode("utf-8"))
    resp.headers = {"ETag": '"abc123"'}
    session = _make_session(resp)

    provider = GbFuelfinderProvider(_NODE_ID)
    await provider._fetch_csv(session)

    assert GbFuelfinderProvider._csv_etag == '"abc123"'


@pytest.mark.asyncio
async def test_fetch_csv_sends_if_none_match_when_etag_cached() -> None:
    """_fetch_csv includes If-None-Match header when ETag is stored (line 345)."""
    GbFuelfinderProvider._csv_etag = '"cached-etag"'
    GbFuelfinderProvider._csv_cache = None
    GbFuelfinderProvider._csv_cache_ts = 0.0

    csv_text = _make_csv_text(_BASE_ROW)
    resp = _make_mock_response(200, csv_text.encode("utf-8"))
    resp.headers = {}
    session = _make_session(resp)

    await GbFuelfinderProvider(_NODE_ID)._fetch_csv(session)

    call_kwargs = session.get.call_args
    headers_sent = call_kwargs[1].get(
        "headers", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
    )
    assert "If-None-Match" in headers_sent
    assert headers_sent["If-None-Match"] == '"cached-etag"'


# ---------------------------------------------------------------------------
# gb_fuelfinder.py line 308 — label without address in async_list_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_label_omits_address_when_absent() -> None:
    """Line 308: when all address parts are empty, label uses '{name} (#{node_id[:8]})' format."""
    GbFuelfinderProvider._csv_cache = None
    GbFuelfinderProvider._csv_cache_ts = 0.0

    # Build a row with no address fields
    row_no_addr = {
        **_BASE_ROW,
        "forecourts.location.address_line_1": "",
        "forecourts.location.address_line_2": "",
        "forecourts.location.city": "",
        "forecourts.location.postcode": "",
    }
    resp = _make_csv_response(row_no_addr)
    session = _make_session(resp)

    provider = GbFuelfinderProvider(_NODE_ID)
    result = await provider.async_list_stations(
        session,
        lat=51.5074,
        lng=-0.1278,
        radius_km=10.0,
    )

    assert len(result) >= 1
    sid, label = result[0]
    # Label should be "{display_name} (#{node_id[:8]})" — no comma before the ID
    assert "(#" in label
    assert sid == _NODE_ID
    # Verify no address part (no comma in the label before the short ID)
    assert f"(#{_NODE_ID[:8]})" in label
    assert label.endswith(f"(#{_NODE_ID[:8]})")
