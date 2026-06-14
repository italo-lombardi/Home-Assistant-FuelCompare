"""Tests for GrFuelgovProvider (Greek Ministry of Energy via nireas.iee.ihu.gr)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.gr_fuelgov import (
    GrFuelgovProvider,
    _API_URL,
    _NATIONAL_AVG_ID,
    _NATIONAL_AVG_NAME,
)


# ---------------------------------------------------------------------------
# Sample API payloads
# ---------------------------------------------------------------------------

_DATE = "2026-06-11"

_ENTRY_ATTIKI = {
    "prefecture": {"id": 1, "name": "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"},
    "prices": {
        "Αμόλυβδη 95 οκτ.": 1.973,
        "Αμόλυβδη 100 οκτ.": 2.231,
        "Diesel Κίνησης": 1.722,
        "Υγραέριο κίνησης (Autogas)": 1.017,
    },
}

_ENTRY_AITOLIA = {
    "prefecture": {"id": 2, "name": "ΝΟΜΟΣ ΑΙΤΩΛΙΑΣ ΚΑΙ ΑΚΑΡΝΑΝΙΑΣ"},
    "prices": {
        "Αμόλυβδη 95 οκτ.": 2.019,
        "Αμόλυβδη 100 οκτ.": 2.276,
        "Diesel Κίνησης": 1.758,
        "Υγραέριο κίνησης (Autogas)": 1.121,
    },
}

_ENTRY_NATIONAL = {
    "prefecture": {"id": 52, "name": _NATIONAL_AVG_NAME},
    "prices": {
        "Αμόλυβδη 95 οκτ.": 2.001,
        "Αμόλυβδη 100 οκτ.": 2.247,
        "Diesel Κίνησης": 1.745,
        "Υγραέριο κίνησης (Autogas)": 1.059,
    },
}

_FULL_PAYLOAD = {
    "data": {
        "date": _DATE,
        "entries": [_ENTRY_ATTIKI, _ENTRY_AITOLIA, _ENTRY_NATIONAL],
    },
    "meta": {"count": 52, "unit": "EUR/L"},
}

_PAYLOAD_MISSING_DATA_KEY = {"meta": {"count": 0, "unit": "EUR/L"}}

_PAYLOAD_EMPTY_ENTRIES = {
    "data": {"date": _DATE, "entries": []},
    "meta": {"count": 0, "unit": "EUR/L"},
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status: int, json_body: object = None) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_body)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_response_error(status: int) -> AsyncMock:
    """Build a mock response that raises on raise_for_status."""
    mock_resp = _make_mock_response(status)
    err = ClientResponseError(request_info=MagicMock(), history=(), status=status)
    mock_resp.raise_for_status = MagicMock(side_effect=err)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns the given response."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """GrFuelgovProvider declares COUNTRY='GR'."""
    assert GrFuelgovProvider.COUNTRY == "GR"


def test_provider_key() -> None:
    """GrFuelgovProvider declares PROVIDER_KEY='gr_fuelgov'."""
    assert GrFuelgovProvider.PROVIDER_KEY == "gr_fuelgov"


def test_provider_label_is_non_empty() -> None:
    """GrFuelgovProvider declares a non-empty human-readable LABEL."""
    assert GrFuelgovProvider.LABEL
    assert isinstance(GrFuelgovProvider.LABEL, str)


def test_provider_config_mode_is_location() -> None:
    """GrFuelgovProvider uses CONFIG_MODE='location' (no station-level data)."""
    assert GrFuelgovProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert GrFuelgovProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_is_daily() -> None:
    """Poll interval is 86400 seconds (daily — bulletin updated once per day)."""
    assert GrFuelgovProvider.POLL_INTERVAL_SECONDS == 86400


def test_provider_capabilities_fuel_types() -> None:
    """CAPABILITIES includes the four Greek fuel types."""
    caps = GrFuelgovProvider.CAPABILITIES
    for fuel in ("unleaded", "premium_unleaded", "diesel", "lpg"):
        assert fuel in caps, f"Fuel type '{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_identity_fields() -> None:
    """CAPABILITIES includes name, county, lastupdated, and source_station_id."""
    caps = GrFuelgovProvider.CAPABILITIES
    for field in ("name", "county", "lastupdated", "source_station_id"):
        assert field in caps, f"Field '{field}' missing from CAPABILITIES"


def test_provider_capabilities_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = GrFuelgovProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Provider constructor / init
# ---------------------------------------------------------------------------


def test_provider_init_defaults_to_national_average() -> None:
    """Constructor defaults to national average (prefecture_id=52) when no args given."""
    provider = GrFuelgovProvider()
    assert provider._prefecture_id == _NATIONAL_AVG_ID


def test_provider_init_stores_prefecture_name() -> None:
    """Constructor stores the prefecture name when supplied."""
    provider = GrFuelgovProvider(prefecture="ΝΟΜΟΣ ΑΤΤΙΚΗΣ")
    assert provider._prefecture == "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"
    assert provider._prefecture_id is None


def test_provider_init_stores_prefecture_id() -> None:
    """Constructor stores the prefecture id when supplied."""
    provider = GrFuelgovProvider(prefecture_id=1)
    assert provider._prefecture_id == 1


def test_provider_init_prefecture_id_overrides_name() -> None:
    """Constructor uses prefecture_id when both prefecture and prefecture_id given."""
    provider = GrFuelgovProvider(prefecture="ΝΟΜΟΣ ΑΤΤΙΚΗΣ", prefecture_id=1)
    assert provider._prefecture_id == 1


def test_provider_init_stores_station_id() -> None:
    """Constructor stores station_id (always 'GR' at runtime)."""
    provider = GrFuelgovProvider("GR")
    assert provider._station_id == "GR"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_api_url_points_to_nireas() -> None:
    """_API_URL points to the nireas.iee.ihu.gr community API."""
    assert "nireas.iee.ihu.gr" in _API_URL
    assert _API_URL.startswith("https://")
    assert "latest" in _API_URL


def test_national_avg_id_is_52() -> None:
    """National average prefecture id is 52."""
    assert _NATIONAL_AVG_ID == 52


# ---------------------------------------------------------------------------
# async_fetch — national average (default)
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data is not None


async def test_async_fetch_national_avg_diesel_price() -> None:
    """async_fetch returns the national average diesel price."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] == pytest.approx(1.745)


async def test_async_fetch_national_avg_unleaded_price() -> None:
    """async_fetch returns the national average unleaded 95 price."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["unleaded"] == pytest.approx(2.001)


async def test_async_fetch_national_avg_premium_unleaded_price() -> None:
    """async_fetch returns the national average unleaded 100 price."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["premium_unleaded"] == pytest.approx(2.247)


async def test_async_fetch_national_avg_lpg_price() -> None:
    """async_fetch returns the national average autogas/LPG price."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["lpg"] == pytest.approx(1.059)


async def test_async_fetch_national_avg_name_is_descriptive() -> None:
    """async_fetch sets name to a human-readable Greek national average label."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["name"] is not None
    assert "Greece" in data["name"] or "National" in data["name"]


async def test_async_fetch_national_avg_county_is_none() -> None:
    """async_fetch returns county=None for the national average entry."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["county"] is None


async def test_async_fetch_national_avg_lastupdated_is_bulletin_date() -> None:
    """async_fetch sets lastupdated to the API bulletin date string."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["lastupdated"] == _DATE


async def test_async_fetch_source_station_id_is_gr() -> None:
    """async_fetch sets source_station_id to 'GR' (country code)."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["source_station_id"] == "GR"


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch output contains all CAPABILITIES keys (excluding sentinels)."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    provider_caps = GrFuelgovProvider.CAPABILITIES - sentinel_keys
    for key in provider_caps:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch output"


async def test_async_fetch_prices_are_eur_per_litre() -> None:
    """async_fetch returns prices in EUR/litre (no /100 conversion applied)."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    # Prices should be in the 1.0–3.0 EUR/litre range, not cents (100+).
    assert 1.0 < data["diesel"] < 4.0
    assert 1.0 < data["unleaded"] < 4.0


# ---------------------------------------------------------------------------
# async_fetch — prefecture selection by id
# ---------------------------------------------------------------------------


async def test_async_fetch_by_prefecture_id_diesel() -> None:
    """async_fetch returns Attiki diesel price when prefecture_id=1."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture_id=1)
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] == pytest.approx(1.722)


async def test_async_fetch_by_prefecture_id_unleaded() -> None:
    """async_fetch returns Attiki unleaded price when prefecture_id=1."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture_id=1)
    data = await provider.async_fetch(session, "GR")

    assert data["unleaded"] == pytest.approx(1.973)


async def test_async_fetch_by_prefecture_id_county_is_prefecture_name() -> None:
    """async_fetch sets county to the Greek prefecture name for non-national entries."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture_id=1)
    data = await provider.async_fetch(session, "GR")

    assert data["county"] == "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"


async def test_async_fetch_by_prefecture_id_not_found_raises_provider_error() -> None:
    """async_fetch raises ProviderError when prefecture_id is not in the API response."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture_id=99)
    with pytest.raises(ProviderError, match="99"):
        await provider.async_fetch(session, "GR")


# ---------------------------------------------------------------------------
# async_fetch — prefecture selection by name
# ---------------------------------------------------------------------------


async def test_async_fetch_by_prefecture_name_diesel() -> None:
    """async_fetch returns correct diesel price when selecting by Greek prefecture name."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture="ΝΟΜΟΣ ΑΙΤΩΛΙΑΣ ΚΑΙ ΑΚΑΡΝΑΝΙΑΣ")
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] == pytest.approx(1.758)


async def test_async_fetch_by_prefecture_name_not_found_raises_provider_error() -> None:
    """async_fetch raises ProviderError when prefecture name does not match any entry."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider(prefecture="NONEXISTENT PREFECTURE")
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "GR")


# ---------------------------------------------------------------------------
# async_fetch — error handling
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_connection_error() -> None:
    """async_fetch propagates network errors (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = GrFuelgovProvider()
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "GR")


async def test_async_fetch_propagates_http_error() -> None:
    """async_fetch propagates HTTP errors via raise_for_status."""
    resp = _make_mock_response_error(503)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    with pytest.raises(ClientResponseError):
        await provider.async_fetch(session, "GR")


async def test_async_fetch_raises_provider_error_for_missing_data_key() -> None:
    """async_fetch raises ProviderError when API response is missing 'data' key."""
    resp = _make_mock_response(200, _PAYLOAD_MISSING_DATA_KEY)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "GR")


async def test_async_fetch_raises_provider_error_for_empty_entries() -> None:
    """async_fetch raises ProviderError when entries list is empty."""
    resp = _make_mock_response(200, _PAYLOAD_EMPTY_ENTRIES)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "GR")


# ---------------------------------------------------------------------------
# async_fetch — missing / null price fields
# ---------------------------------------------------------------------------


async def test_async_fetch_none_price_when_fuel_absent() -> None:
    """async_fetch returns None for a fuel type missing from the API prices dict."""
    payload_partial = {
        "data": {
            "date": _DATE,
            "entries": [
                {
                    "prefecture": {"id": 52, "name": _NATIONAL_AVG_NAME},
                    "prices": {
                        "Diesel Κίνησης": 1.745,
                        # unleaded, premium_unleaded, lpg omitted
                    },
                }
            ],
        },
        "meta": {"count": 1, "unit": "EUR/L"},
    }
    resp = _make_mock_response(200, payload_partial)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] == pytest.approx(1.745)
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["lpg"] is None


async def test_async_fetch_none_price_when_zero() -> None:
    """async_fetch returns None for zero-value prices (invalid / not sold)."""
    payload_zero = {
        "data": {
            "date": _DATE,
            "entries": [
                {
                    "prefecture": {"id": 52, "name": _NATIONAL_AVG_NAME},
                    "prices": {"Diesel Κίνησης": 0.0, "Αμόλυβδη 95 οκτ.": 1.973},
                }
            ],
        },
        "meta": {"count": 1, "unit": "EUR/L"},
    }
    resp = _make_mock_response(200, payload_zero)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] is None
    assert data["unleaded"] == pytest.approx(1.973)


async def test_async_fetch_none_price_when_negative() -> None:
    """async_fetch returns None for negative prices."""
    payload_neg = {
        "data": {
            "date": _DATE,
            "entries": [
                {
                    "prefecture": {"id": 52, "name": _NATIONAL_AVG_NAME},
                    "prices": {"Diesel Κίνησης": -1.0, "Αμόλυβδη 95 οκτ.": 1.973},
                }
            ],
        },
        "meta": {"count": 1, "unit": "EUR/L"},
    }
    resp = _make_mock_response(200, payload_neg)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    data = await provider.async_fetch(session, "GR")

    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_prefecture_name() -> None:
    """async_fetch_station_name returns the prefecture name on success."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    name = await provider.async_fetch_station_name(session, "GR")

    assert name is not None
    assert isinstance(name, str)


async def test_async_fetch_station_name_returns_none_on_error() -> None:
    """async_fetch_station_name returns None on any network/parsing error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = GrFuelgovProvider()
    name = await provider.async_fetch_station_name(session, "GR")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_http_error() -> None:
    """async_fetch_station_name returns None when the API returns HTTP 503."""
    resp = _make_mock_response_error(503)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    name = await provider.async_fetch_station_name(session, "GR")

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (id_str, label) 2-tuples."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    assert isinstance(results, list)
    for item in results:
        assert len(item) == 2
        pid, label = item
        assert isinstance(pid, str)
        assert isinstance(label, str)


async def test_async_list_stations_length_matches_entries() -> None:
    """async_list_stations returns one entry per API entry."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    # Our test payload has 3 entries
    assert len(results) == 3


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes 'Diesel €x.xxx' for stations with diesel."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    # All our test entries have diesel prices
    for _pid, label in results:
        assert "Diesel" in label, f"'Diesel' missing from label: {label}"


async def test_async_list_stations_label_includes_unleaded_price() -> None:
    """async_list_stations label includes 'Unleaded €x.xxx' price."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    for _pid, label in results:
        assert "Unleaded" in label, f"'Unleaded' missing from label: {label}"


async def test_async_list_stations_ids_are_string_integers() -> None:
    """async_list_stations returns prefecture id as a string (e.g. '1', '52')."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    ids = [pid for pid, _ in results]
    assert "1" in ids
    assert "52" in ids


async def test_async_list_stations_sorted_by_id_ascending() -> None:
    """async_list_stations returns entries sorted by prefecture id ascending."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    ids_int = [int(pid) for pid, _ in results]
    assert ids_int == sorted(ids_int)


async def test_async_list_stations_national_avg_id_last() -> None:
    """async_list_stations places the national average (id=52) last by sort order."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    last_id = results[-1][0]
    assert last_id == str(_NATIONAL_AVG_ID)


async def test_async_list_stations_returns_empty_on_connection_error() -> None:
    """async_list_stations returns empty list when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns empty list when HTTP error occurs."""
    resp = _make_mock_response_error(500)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_empty_entries() -> None:
    """async_list_stations returns empty list when API response has no entries."""
    resp = _make_mock_response(200, _PAYLOAD_EMPTY_ENTRIES)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_skips_entry_with_no_prefecture_id() -> None:
    """async_list_stations silently skips entries where prefecture.id is None."""
    payload_no_id = {
        "data": {
            "date": _DATE,
            "entries": [
                {
                    "prefecture": {"id": None, "name": "NO ID ENTRY"},
                    "prices": {"Diesel Κίνησης": 1.800},
                },
                _ENTRY_ATTIKI,
            ],
        },
        "meta": {"count": 2, "unit": "EUR/L"},
    }
    resp = _make_mock_response(200, payload_no_id)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    ids = [pid for pid, _ in results]
    assert "None" not in ids
    assert "1" in ids


async def test_async_list_stations_label_no_price_hint_when_no_prices() -> None:
    """async_list_stations label has no '€' when the entry has no price data."""
    payload_no_prices = {
        "data": {
            "date": _DATE,
            "entries": [
                {
                    "prefecture": {"id": 10, "name": "ΝΟΜΟΣ ΑΡΚΑΔΙΑΣ"},
                    "prices": {},
                }
            ],
        },
        "meta": {"count": 1, "unit": "EUR/L"},
    }
    resp = _make_mock_response(200, payload_no_prices)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    results = await provider.async_list_stations(session)

    assert len(results) == 1
    _pid, label = results[0]
    assert "€" not in label


# ---------------------------------------------------------------------------
# _fetch_payload — request details
# ---------------------------------------------------------------------------


async def test_fetch_payload_calls_correct_url() -> None:
    """_fetch_payload issues GET to _API_URL."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    await provider._fetch_payload(session)

    call_args = session.get.call_args
    assert call_args[0][0] == _API_URL


async def test_fetch_payload_sends_accept_json_header() -> None:
    """_fetch_payload sends Accept: application/json header."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    await provider._fetch_payload(session)

    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert headers.get("Accept") == "application/json"


async def test_fetch_payload_calls_raise_for_status() -> None:
    """_fetch_payload calls raise_for_status on the response."""
    resp = _make_mock_response(200, _FULL_PAYLOAD)
    session = _make_session(resp)

    provider = GrFuelgovProvider()
    await provider._fetch_payload(session)

    resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# _select_entry — edge cases
# ---------------------------------------------------------------------------


def test_select_entry_national_avg_by_id() -> None:
    """_select_entry returns national average entry when prefecture_id=52."""
    provider = GrFuelgovProvider()
    entry = provider._select_entry(_FULL_PAYLOAD)
    assert entry["prefecture"]["id"] == _NATIONAL_AVG_ID


def test_select_entry_attiki_by_id() -> None:
    """_select_entry returns Attiki entry when prefecture_id=1."""
    provider = GrFuelgovProvider(prefecture_id=1)
    entry = provider._select_entry(_FULL_PAYLOAD)
    assert entry["prefecture"]["id"] == 1


def test_select_entry_attiki_by_name() -> None:
    """_select_entry returns Attiki entry when matching by name."""
    provider = GrFuelgovProvider(prefecture="ΝΟΜΟΣ ΑΤΤΙΚΗΣ")
    entry = provider._select_entry(_FULL_PAYLOAD)
    assert entry["prefecture"]["name"] == "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"


def test_select_entry_name_match_is_case_insensitive() -> None:
    """_select_entry matches prefecture name case-insensitively (uppercased comparison)."""
    provider = GrFuelgovProvider(prefecture="νομοσ αττικησ")
    entry = provider._select_entry(_FULL_PAYLOAD)
    assert entry["prefecture"]["id"] == 1


def test_select_entry_raises_for_unknown_id() -> None:
    """_select_entry raises ProviderError for a prefecture id not in the response."""
    provider = GrFuelgovProvider(prefecture_id=99)
    with pytest.raises(ProviderError):
        provider._select_entry(_FULL_PAYLOAD)


def test_select_entry_raises_for_unknown_name() -> None:
    """_select_entry raises ProviderError for a prefecture name not in the response."""
    provider = GrFuelgovProvider(prefecture="DOES NOT EXIST")
    with pytest.raises(ProviderError):
        provider._select_entry(_FULL_PAYLOAD)


# ---------------------------------------------------------------------------
# _build_station_data — unit tests
# ---------------------------------------------------------------------------


def test_build_station_data_all_prices_mapped() -> None:
    """_build_station_data maps all four fuel prices from the national average entry."""
    provider = GrFuelgovProvider()
    data = provider._build_station_data(_ENTRY_NATIONAL, _FULL_PAYLOAD)

    assert data["unleaded"] == pytest.approx(2.001)
    assert data["premium_unleaded"] == pytest.approx(2.247)
    assert data["diesel"] == pytest.approx(1.745)
    assert data["lpg"] == pytest.approx(1.059)


def test_build_station_data_lastupdated_from_payload() -> None:
    """_build_station_data sets lastupdated from payload['data']['date']."""
    provider = GrFuelgovProvider()
    data = provider._build_station_data(_ENTRY_NATIONAL, _FULL_PAYLOAD)

    assert data["lastupdated"] == _DATE


def test_build_station_data_source_station_id_is_gr() -> None:
    """_build_station_data always sets source_station_id to 'GR'."""
    provider = GrFuelgovProvider()
    data = provider._build_station_data(_ENTRY_ATTIKI, _FULL_PAYLOAD)

    assert data["source_station_id"] == "GR"


def test_build_station_data_prefecture_county() -> None:
    """_build_station_data sets county to the prefecture name for non-national entries."""
    provider = GrFuelgovProvider(prefecture_id=1)
    data = provider._build_station_data(_ENTRY_ATTIKI, _FULL_PAYLOAD)

    assert data["county"] == "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"


def test_build_station_data_national_county_is_none() -> None:
    """_build_station_data sets county=None for the national average entry."""
    provider = GrFuelgovProvider()
    data = provider._build_station_data(_ENTRY_NATIONAL, _FULL_PAYLOAD)

    assert data["county"] is None


def test_build_station_data_prices_rounded_to_three_decimals() -> None:
    """_build_station_data rounds prices to 3 decimal places."""
    entry_extra = {
        "prefecture": {"id": 52, "name": _NATIONAL_AVG_NAME},
        "prices": {"Diesel Κίνησης": 1.74567890},
    }
    provider = GrFuelgovProvider()
    data = provider._build_station_data(entry_extra, _FULL_PAYLOAD)

    assert data["diesel"] == pytest.approx(1.746, abs=1e-3)


def test_build_station_data_missing_price_is_none() -> None:
    """_build_station_data returns None for fuel types absent from the prices dict."""
    entry_partial = {
        "prefecture": {"id": 1, "name": "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"},
        "prices": {"Diesel Κίνησης": 1.722},
    }
    provider = GrFuelgovProvider(prefecture_id=1)
    data = provider._build_station_data(entry_partial, _FULL_PAYLOAD)

    assert data["diesel"] == pytest.approx(1.722)
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["lpg"] is None
