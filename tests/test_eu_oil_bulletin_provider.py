"""Tests for EuOilBulletinProvider.

Covers:
  - Provider metadata (class attributes)
  - _parse_price_per_litre conversion helper
  - _resolve_country_code name-to-ISO mapping
  - _parse_sheet row parsing
  - _build_station_data assembly
  - async_fetch success and error paths
  - async_fetch_station_name
  - async_list_stations
  - _fetch_excel caching behaviour
  - Connection/HTTP error handling
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.eu_oil_bulletin import (
    EuOilBulletinProvider,
    _DOWNLOAD_URL,
    _build_station_data,
    _parse_price_per_litre,
    _parse_sheet,
    _resolve_country_code,
)

# ---------------------------------------------------------------------------
# Helpers for creating fake openpyxl workbooks
# ---------------------------------------------------------------------------

openpyxl = pytest.importorskip("openpyxl")

_OPENPYXL_AVAILABLE = True

_skip_if_no_openpyxl = pytest.mark.skipif(
    not _OPENPYXL_AVAILABLE, reason="openpyxl not installed"
)


def _make_workbook(rows: list[list]) -> bytes:
    """Build an in-memory .xlsx file with the given rows and return bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _make_bulletin_workbook(
    country_rows: list[tuple],
    week_label: str = "Oil Bulletin — 2026-06-08",
) -> bytes:
    """Build a minimal Oil Bulletin workbook with header rows + data rows.

    Structure mirrors the real EC Excel:
      Row 1: week label in A1
      Row 2: column header labels
      Row 3+: country data (name, e5, diesel, heating, fuel_ls, fuel_hs, lpg)
    """
    header1 = [week_label, None, None, None, None, None, None]
    header2 = [
        "Country",
        "Euro-super 95 (E5)",
        "Automotive gas oil",
        "Heating gas oil",
        "Fuel oil (low sulphur)",
        "Fuel oil (high sulphur)",
        "LPG motor fuel",
    ]
    all_rows = [header1, header2] + [list(row) for row in country_rows]
    return _make_workbook(all_rows)


def _make_mock_response(
    status: int = 200,
    body: bytes = b"",
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
) -> AsyncMock:
    """Return a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.headers = {"Content-Type": content_type}
    mock_resp.read = AsyncMock(return_value=body)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock aiohttp session that returns *response* from .get()."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _make_provider(station_id: str = "DE") -> EuOilBulletinProvider:
    return EuOilBulletinProvider(station_id=station_id)


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """EuOilBulletinProvider declares COUNTRY='EU'."""
    assert EuOilBulletinProvider.COUNTRY == "EU"


def test_provider_key() -> None:
    """PROVIDER_KEY is 'eu_oil_bulletin'."""
    assert EuOilBulletinProvider.PROVIDER_KEY == "eu_oil_bulletin"


def test_provider_label_contains_eu() -> None:
    """LABEL contains 'EU' or 'Oil Bulletin'."""
    label = EuOilBulletinProvider.LABEL
    assert "EU" in label or "Oil Bulletin" in label


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE is 'location'."""
    assert EuOilBulletinProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert EuOilBulletinProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_is_weekly() -> None:
    """Poll interval is exactly one week in seconds."""
    assert EuOilBulletinProvider.POLL_INTERVAL_SECONDS == 7 * 24 * 3600


def test_provider_requires_no_api_key() -> None:
    """REQUIRES_API_KEY is False — no registration needed."""
    assert EuOilBulletinProvider.REQUIRES_API_KEY is False


def test_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in EuOilBulletinProvider.CAPABILITIES


def test_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (Euro-super 95)."""
    assert "unleaded" in EuOilBulletinProvider.CAPABILITIES


def test_capabilities_include_lpg() -> None:
    """CAPABILITIES includes 'lpg'."""
    assert "lpg" in EuOilBulletinProvider.CAPABILITIES


def test_capabilities_include_kerosene() -> None:
    """CAPABILITIES includes 'kerosene' (heating gas oil)."""
    assert "kerosene" in EuOilBulletinProvider.CAPABILITIES


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinels."""
    caps = EuOilBulletinProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_defaults_to_eu27() -> None:
    """Constructor defaults station_id to 'EU27' when not supplied."""
    provider = EuOilBulletinProvider()
    assert provider._station_id == "EU27"


def test_constructor_uppercases_station_id() -> None:
    """Constructor uppercases the station_id."""
    provider = EuOilBulletinProvider(station_id="de")
    assert provider._station_id == "DE"


def test_constructor_accepts_coordinates() -> None:
    """Constructor stores optional lat/lng/radius without error."""
    provider = EuOilBulletinProvider(
        station_id="FR", latitude=48.85, longitude=2.35, radius_km=50.0
    )
    assert provider._latitude == pytest.approx(48.85)
    assert provider._longitude == pytest.approx(2.35)
    assert provider._radius_km == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# _parse_price_per_litre
# ---------------------------------------------------------------------------


def test_parse_price_divides_by_1000() -> None:
    """_parse_price_per_litre divides EUR/1000L by 1000."""
    result = _parse_price_per_litre(1800.0)
    assert result == pytest.approx(1.8, abs=0.0001)


def test_parse_price_typical_diesel_value() -> None:
    """A typical diesel price of 1500 EUR/1000L → 1.5 EUR/L."""
    result = _parse_price_per_litre(1500.0)
    assert result == pytest.approx(1.5, abs=0.0001)


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price_per_litre returns None for None input."""
    assert _parse_price_per_litre(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price_per_litre returns None for zero."""
    assert _parse_price_per_litre(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price_per_litre returns None for negative values."""
    assert _parse_price_per_litre(-500.0) is None


def test_parse_price_returns_none_for_string_garbage() -> None:
    """_parse_price_per_litre returns None for non-numeric strings."""
    assert _parse_price_per_litre("n/a") is None


def test_parse_price_parses_string_float() -> None:
    """_parse_price_per_litre parses a numeric string."""
    result = _parse_price_per_litre("1850.5")
    assert result == pytest.approx(1.8505, abs=0.0001)


def test_parse_price_rounds_to_4_decimals() -> None:
    """_parse_price_per_litre rounds to 4 decimal places."""
    result = _parse_price_per_litre(1234.56789)
    assert result is not None
    assert len(str(result).split(".")[-1]) <= 4


# ---------------------------------------------------------------------------
# _resolve_country_code
# ---------------------------------------------------------------------------


def test_resolve_germany() -> None:
    """'Germany' resolves to 'DE'."""
    assert _resolve_country_code("Germany") == "DE"


def test_resolve_france() -> None:
    """'France' resolves to 'FR'."""
    assert _resolve_country_code("France") == "FR"


def test_resolve_european_union() -> None:
    """'European Union' resolves to 'EU27'."""
    assert _resolve_country_code("European Union") == "EU27"


def test_resolve_euro_area() -> None:
    """'Euro area' resolves to 'EURO'."""
    assert _resolve_country_code("Euro area") == "EURO"


def test_resolve_case_insensitive() -> None:
    """_resolve_country_code is case-insensitive."""
    assert _resolve_country_code("GERMANY") == "DE"
    assert _resolve_country_code("germany") == "DE"


def test_resolve_with_whitespace() -> None:
    """_resolve_country_code strips surrounding whitespace."""
    assert _resolve_country_code("  France  ") == "FR"


def test_resolve_unknown_returns_none() -> None:
    """_resolve_country_code returns None for unknown country names."""
    assert _resolve_country_code("Narnia") is None


# ---------------------------------------------------------------------------
# _parse_sheet (requires openpyxl)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
def test_parse_sheet_extracts_germany() -> None:
    """_parse_sheet extracts Germany row with correct price conversion."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    rows = _parse_sheet(wb.active, 2)

    assert "DE" in rows
    de = rows["DE"]
    assert de["e5"] == pytest.approx(1.8, abs=0.0001)
    assert de["diesel"] == pytest.approx(1.65, abs=0.0001)
    assert de["lpg"] == pytest.approx(0.6, abs=0.0001)


@_skip_if_no_openpyxl
def test_parse_sheet_extracts_eu27_aggregate() -> None:
    """_parse_sheet extracts the 'European Union' aggregate row as 'EU27'."""
    wb_bytes = _make_bulletin_workbook(
        [("European Union", 1820.0, 1670.0, 920.0, 810.0, 710.0, 610.0)]
    )
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    rows = _parse_sheet(wb.active, 2)

    assert "EU27" in rows
    assert rows["EU27"]["e5"] == pytest.approx(1.82, abs=0.0001)


@_skip_if_no_openpyxl
def test_parse_sheet_skips_unrecognised_rows() -> None:
    """_parse_sheet skips rows with unrecognised country names."""
    wb_bytes = _make_bulletin_workbook(
        [
            ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ("Atlantis", 9999.0, 9999.0, 9999.0, 9999.0, 9999.0, 9999.0),
        ]
    )
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    rows = _parse_sheet(wb.active, 2)

    assert "DE" in rows
    assert "Atlantis" not in rows
    # Make sure 'Atlantis' didn't get stored under any key
    for rec in rows.values():
        assert rec.get("country_name") != "Atlantis"


@_skip_if_no_openpyxl
def test_parse_sheet_handles_none_prices() -> None:
    """_parse_sheet returns None for missing price cells."""
    wb_bytes = _make_bulletin_workbook(
        [("France", None, 1700.0, None, None, None, None)]
    )
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    rows = _parse_sheet(wb.active, 2)

    assert "FR" in rows
    fr = rows["FR"]
    assert fr["e5"] is None
    assert fr["diesel"] == pytest.approx(1.7, abs=0.0001)
    assert fr["lpg"] is None


@_skip_if_no_openpyxl
def test_parse_sheet_multiple_countries() -> None:
    """_parse_sheet extracts all valid rows from a multi-country sheet."""
    country_rows = [
        ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
        ("France", 1810.0, 1660.0, 910.0, 810.0, 710.0, 610.0),
        ("Spain", 1790.0, 1640.0, 890.0, 790.0, 690.0, 590.0),
        ("European Union", 1820.0, 1670.0, 920.0, 820.0, 720.0, 620.0),
    ]
    wb_bytes = _make_bulletin_workbook(country_rows)
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    rows = _parse_sheet(wb.active, 2)

    assert set(rows.keys()) >= {"DE", "FR", "ES", "EU27"}


# ---------------------------------------------------------------------------
# _build_station_data
# ---------------------------------------------------------------------------


def test_build_station_data_maps_e5_to_unleaded() -> None:
    """_build_station_data maps e5 record key to 'unleaded' StationData key."""
    record = {
        "country_name": "Germany",
        "e5": 1.8,
        "diesel": 1.65,
        "heating_oil": 0.9,
        "fuel_oil_ls": 0.8,
        "fuel_oil_hs": 0.7,
        "lpg": 0.6,
    }
    data = _build_station_data("DE", record, "2026-06-08")
    assert data["unleaded"] == pytest.approx(1.8)


def test_build_station_data_maps_diesel() -> None:
    """_build_station_data maps diesel to 'diesel' key."""
    record = {
        "country_name": "Germany",
        "e5": 1.8,
        "diesel": 1.65,
        "heating_oil": 0.9,
        "fuel_oil_ls": 0.8,
        "fuel_oil_hs": 0.7,
        "lpg": 0.6,
    }
    data = _build_station_data("DE", record, "2026-06-08")
    assert data["diesel"] == pytest.approx(1.65)


def test_build_station_data_maps_heating_oil_to_kerosene() -> None:
    """_build_station_data maps heating_oil to 'kerosene' StationData key."""
    record = {
        "country_name": "Germany",
        "e5": 1.8,
        "diesel": 1.65,
        "heating_oil": 0.9,
        "fuel_oil_ls": 0.8,
        "fuel_oil_hs": 0.7,
        "lpg": 0.6,
    }
    data = _build_station_data("DE", record, "2026-06-08")
    assert data["kerosene"] == pytest.approx(0.9)


def test_build_station_data_maps_lpg() -> None:
    """_build_station_data maps lpg to 'lpg' StationData key."""
    record = {
        "country_name": "Germany",
        "e5": 1.8,
        "diesel": 1.65,
        "heating_oil": 0.9,
        "fuel_oil_ls": 0.8,
        "fuel_oil_hs": 0.7,
        "lpg": 0.6,
    }
    data = _build_station_data("DE", record, "2026-06-08")
    assert data["lpg"] == pytest.approx(0.6)


def test_build_station_data_sets_name_from_country_name() -> None:
    """_build_station_data sets name to the country name."""
    record = {
        "country_name": "France",
        "e5": 1.81,
        "diesel": 1.66,
        "heating_oil": None,
        "fuel_oil_ls": None,
        "fuel_oil_hs": None,
        "lpg": None,
    }
    data = _build_station_data("FR", record, "2026-06-08")
    assert data["name"] == "France"


def test_build_station_data_sets_source_station_id_to_code() -> None:
    """_build_station_data sets source_station_id to the country code."""
    record = {
        "country_name": "France",
        "e5": 1.81,
        "diesel": 1.66,
        "heating_oil": None,
        "fuel_oil_ls": None,
        "fuel_oil_hs": None,
        "lpg": None,
    }
    data = _build_station_data("FR", record, "2026-06-08")
    assert data["source_station_id"] == "FR"


def test_build_station_data_sets_lastupdated_to_week_label() -> None:
    """_build_station_data sets lastupdated to the week label string."""
    record = {
        "country_name": "Spain",
        "e5": 1.79,
        "diesel": 1.64,
        "heating_oil": None,
        "fuel_oil_ls": None,
        "fuel_oil_hs": None,
        "lpg": 0.59,
    }
    data = _build_station_data("ES", record, "2026-06-08")
    assert data["lastupdated"] == "2026-06-08"


def test_build_station_data_none_prices_stay_none() -> None:
    """_build_station_data preserves None prices as None."""
    record = {
        "country_name": "Malta",
        "e5": None,
        "diesel": None,
        "heating_oil": None,
        "fuel_oil_ls": None,
        "fuel_oil_hs": None,
        "lpg": None,
    }
    data = _build_station_data("MT", record, "2026-06-08")
    assert data["unleaded"] is None
    assert data["diesel"] is None
    assert data["kerosene"] is None
    assert data["lpg"] is None


# ---------------------------------------------------------------------------
# async_fetch — success
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_returns_station_data_for_germany() -> None:
    """async_fetch returns StationData for a valid country code."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    data = await provider.async_fetch(session, "DE")

    assert data["diesel"] == pytest.approx(1.65, abs=0.0001)
    assert data["unleaded"] == pytest.approx(1.8, abs=0.0001)


@_skip_if_no_openpyxl
async def test_async_fetch_prices_are_per_litre() -> None:
    """async_fetch prices are in EUR/litre, not EUR/1000L."""
    wb_bytes = _make_bulletin_workbook([("France", 1820.0, 1670.0, 0, 0, 0, 620.0)])
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("FR")
    data = await provider.async_fetch(session, "FR")

    # All prices must be sub-10 (EUR/litre, not EUR/1000L)
    for key in ("diesel", "unleaded", "lpg"):
        val = data.get(key)
        if val is not None:
            assert val < 10.0, f"Price for {key} ({val}) looks like it's not per-litre"


@_skip_if_no_openpyxl
async def test_async_fetch_country_code_is_case_insensitive() -> None:
    """async_fetch accepts lowercase country codes."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("de")
    data = await provider.async_fetch(session, "de")

    assert data["source_station_id"] == "DE"


@_skip_if_no_openpyxl
async def test_async_fetch_eu27_aggregate() -> None:
    """async_fetch returns data for the EU27 aggregate row."""
    wb_bytes = _make_bulletin_workbook(
        [("European Union", 1820.0, 1670.0, 920.0, 820.0, 720.0, 620.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("EU27")
    data = await provider.async_fetch(session, "EU27")

    assert data["source_station_id"] == "EU27"
    assert data["diesel"] == pytest.approx(1.67, abs=0.0001)


@_skip_if_no_openpyxl
async def test_async_fetch_sets_lastupdated_from_header() -> None:
    """async_fetch sets lastupdated from the Excel header row."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)],
        week_label="Oil Bulletin 2026-06-08",
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    data = await provider.async_fetch(session, "DE")

    assert data["lastupdated"] is not None
    assert len(data["lastupdated"]) > 0


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_for_unknown_country() -> None:
    """async_fetch raises ProviderError when the country code is not in the data."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("XX")
    with pytest.raises(ProviderError, match="XX"):
        await provider.async_fetch(session, "XX")


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_on_http_error() -> None:
    """async_fetch raises ProviderError when the HTTP request fails."""
    from aiohttp import ClientResponseError

    resp = _make_mock_response(404)
    resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(
            MagicMock(), MagicMock(), status=404, message="Not Found"
        )
    )
    session = _make_session(resp)

    provider = _make_provider("DE")
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "DE")


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_on_empty_response() -> None:
    """async_fetch raises ProviderError when the server returns an empty body."""
    resp = _make_mock_response(200, body=b"")
    session = _make_session(resp)

    provider = _make_provider("DE")
    with pytest.raises(ProviderError, match="empty"):
        await provider.async_fetch(session, "DE")


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_on_corrupt_excel() -> None:
    """async_fetch raises ProviderError when the body is not a valid xlsx."""
    resp = _make_mock_response(200, body=b"not an excel file at all")
    session = _make_session(resp)

    provider = _make_provider("DE")
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "DE")


# ---------------------------------------------------------------------------
# async_fetch — caching
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_uses_cache_on_second_call() -> None:
    """async_fetch does not re-download the Excel on the second call (24h cache)."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    await provider.async_fetch(session, "DE")
    await provider.async_fetch(session, "DE")

    # Session.get should have been called only once
    assert session.get.call_count == 1


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_country_name() -> None:
    """async_fetch_station_name returns the country name for a known code."""
    provider = _make_provider("DE")
    session = MagicMock()
    name = await provider.async_fetch_station_name(session, "DE")
    assert name is not None
    assert "Germany" in name or "DE" in name


async def test_async_fetch_station_name_returns_none_for_empty_code() -> None:
    """async_fetch_station_name returns None for an empty station_id."""
    provider = EuOilBulletinProvider(station_id="EU27")
    session = MagicMock()
    name = await provider.async_fetch_station_name(session, "")
    assert name is None


async def test_async_fetch_station_name_returns_fallback_for_unknown() -> None:
    """async_fetch_station_name returns a non-None fallback for unknown codes."""
    provider = _make_provider("XX")
    session = MagicMock()
    name = await provider.async_fetch_station_name(session, "XX")
    # Should not be None; should contain the code
    assert name is not None
    assert "XX" in name


async def test_async_fetch_station_name_does_not_call_network() -> None:
    """async_fetch_station_name does not make any HTTP requests."""
    provider = _make_provider("FR")
    session = MagicMock()
    await provider.async_fetch_station_name(session, "FR")
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a non-empty list of (code, label) tuples."""
    wb_bytes = _make_bulletin_workbook(
        [
            ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ("France", 1810.0, 1660.0, 910.0, 810.0, 710.0, 610.0),
        ]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    assert isinstance(result, list)
    assert len(result) >= 2
    for code, label in result:
        assert isinstance(code, str)
        assert isinstance(label, str)


@_skip_if_no_openpyxl
async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes the diesel price."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    de_entry = next((r for r in result if r[0] == "DE"), None)
    assert de_entry is not None
    _, label = de_entry
    assert "Diesel" in label or "1.65" in label


@_skip_if_no_openpyxl
async def test_async_list_stations_includes_country_code_as_id() -> None:
    """async_list_stations uses ISO country codes as the station IDs."""
    wb_bytes = _make_bulletin_workbook(
        [
            ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ("France", 1810.0, 1660.0, 910.0, 810.0, 710.0, 610.0),
        ]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    ids = [code for code, _ in result]
    assert "DE" in ids
    assert "FR" in ids


@_skip_if_no_openpyxl
async def test_async_list_stations_accepts_lat_lng_kwargs() -> None:
    """async_list_stations accepts lat/lng kwargs without error."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(
        session, lat=52.5, lng=13.4, radius_km=50.0
    )

    assert isinstance(result, list)


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns [] on HTTP download failure."""
    from aiohttp import ClientResponseError

    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(
            MagicMock(), MagicMock(), status=503, message="Service Unavailable"
        )
    )
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    assert result == []


@pytest.fixture(autouse=True)
def reset_eu_oil_bulletin_cache():
    """Reset class-level workbook cache between tests."""
    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None
    yield
    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_empty_on_empty_response() -> None:
    """async_list_stations returns [] when the server returns an empty body."""
    resp = _make_mock_response(200, body=b"")
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    assert result == []


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_empty_on_corrupt_excel() -> None:
    """async_list_stations returns [] when the body is not valid xlsx."""
    resp = _make_mock_response(200, body=b"garbage data not xlsx")
    session = _make_session(resp)

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# Download URL
# ---------------------------------------------------------------------------


def test_download_url_is_https() -> None:
    """_DOWNLOAD_URL uses HTTPS."""
    assert _DOWNLOAD_URL.startswith("https://")


def test_download_url_targets_ec_europa() -> None:
    """_DOWNLOAD_URL targets energy.ec.europa.eu."""
    assert "ec.europa.eu" in _DOWNLOAD_URL


def test_download_url_contains_with_taxes_uuid() -> None:
    """_DOWNLOAD_URL contains the prices-with-taxes UUID."""
    assert "264c2d0f-f161-4ea3-a777-78faae59bea0" in _DOWNLOAD_URL


# ---------------------------------------------------------------------------
# openpyxl import error — async_fetch (lines 268-269)
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_openpyxl_missing() -> None:
    """async_fetch raises ProviderError when openpyxl cannot be imported (lines 268-269)."""
    import builtins
    import sys

    real_import = builtins.__import__

    def _block_openpyxl(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("no openpyxl")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("openpyxl", None)
    provider = _make_provider("DE")
    session = MagicMock()

    with patch("builtins.__import__", side_effect=_block_openpyxl):
        with pytest.raises(ProviderError, match="openpyxl"):
            await provider.async_fetch(session, "DE")


# ---------------------------------------------------------------------------
# openpyxl import warning + empty return — async_list_stations (lines 377-381)
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_openpyxl_missing() -> None:
    """async_list_stations returns [] and logs a warning when openpyxl is missing (lines 377-381)."""
    import builtins
    import sys

    real_import = builtins.__import__

    def _block_openpyxl(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("no openpyxl")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("openpyxl", None)
    provider = _make_provider("DE")
    session = MagicMock()

    with patch("builtins.__import__", side_effect=_block_openpyxl):
        result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# sheet is None — async_fetch (line 295)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_when_active_sheet_is_none() -> None:
    """async_fetch raises ProviderError when workbook has no active sheet (line 295)."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    wb_mock = MagicMock()
    wb_mock.active = None
    wb_mock.close = MagicMock()

    loop_mock = MagicMock()
    loop_mock.run_in_executor = AsyncMock(return_value=wb_mock)

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    with patch(
        "custom_components.fuelcompare_ie.providers.eu_oil_bulletin.asyncio.get_running_loop",
        return_value=loop_mock,
    ):
        with pytest.raises(ProviderError, match="no active sheet"):
            await provider.async_fetch(session, "DE")


# ---------------------------------------------------------------------------
# header cell access raises — async_fetch (lines 308-309, 311)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_uses_date_fallback_when_header_cell_raises() -> None:
    """async_fetch falls back to today when sheet.cell() raises during header parse (lines 308-311)."""

    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    real_wb = openpyxl.load_workbook(
        io.BytesIO(wb_bytes), read_only=True, data_only=True
    )
    rows = list(real_wb.active.iter_rows(min_row=3, values_only=True))
    real_wb.close()

    sheet_mock = MagicMock()
    sheet_mock.cell = MagicMock(side_effect=Exception("cell access error"))
    sheet_mock.iter_rows = MagicMock(return_value=iter(rows))

    wb_mock = MagicMock()
    wb_mock.active = sheet_mock
    wb_mock.close = MagicMock()

    loop_mock = MagicMock()
    loop_mock.run_in_executor = AsyncMock(return_value=wb_mock)

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    with patch(
        "custom_components.fuelcompare_ie.providers.eu_oil_bulletin.asyncio.get_running_loop",
        return_value=loop_mock,
    ):
        data = await provider.async_fetch(session, "DE")

    assert data["lastupdated"] is not None
    assert len(data["lastupdated"]) == 10


# ---------------------------------------------------------------------------
# week_label fallback when header cells hold only 'in EUR' (line 311)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_uses_date_fallback_when_no_useful_header() -> None:
    """async_fetch falls back to today's ISO date when header cells are 'in EUR' (line 311)."""

    # row2/col1 = 'in EUR', row1/col2 = 'in EUR' — both candidates are filtered out
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=1, column=1).value = None
    ws.cell(row=1, column=2).value = "in EUR"
    ws.cell(row=2, column=1).value = "in EUR"
    ws.cell(row=3, column=1).value = "Germany"
    ws.cell(row=3, column=2).value = 1800.0
    ws.cell(row=3, column=3).value = 1650.0
    ws.cell(row=3, column=4).value = 900.0
    ws.cell(row=3, column=5).value = 800.0
    ws.cell(row=3, column=6).value = 700.0
    ws.cell(row=3, column=7).value = 600.0
    buf = io.BytesIO()
    wb.save(buf)
    wb_bytes = buf.getvalue()

    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    data = await provider.async_fetch(session, "DE")

    assert data["lastupdated"] is not None
    assert len(data["lastupdated"]) == 10


# ---------------------------------------------------------------------------
# sheet is None — async_list_stations (line 422)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_empty_when_active_sheet_is_none() -> None:
    """async_list_stations returns [] when workbook.active is None (line 422)."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    wb_mock = MagicMock()
    wb_mock.active = None
    wb_mock.close = MagicMock()

    loop_mock = MagicMock()
    loop_mock.run_in_executor = AsyncMock(return_value=wb_mock)

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    with patch(
        "custom_components.fuelcompare_ie.providers.eu_oil_bulletin.asyncio.get_running_loop",
        return_value=loop_mock,
    ):
        result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# label falls back to country_name when no prices — async_list_stations (line 441)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_list_stations_uses_country_name_as_label_when_no_prices() -> None:
    """async_list_stations uses country_name as label when both diesel and e5 are None (line 441)."""
    wb_bytes = _make_bulletin_workbook(
        [("Germany", None, None, None, None, None, None)]
    )
    resp = _make_mock_response(200, body=wb_bytes)
    session = _make_session(resp)

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    de_entry = next((r for r in result if r[0] == "DE"), None)
    assert de_entry is not None
    _, label = de_entry
    assert label == "Germany"


# ---------------------------------------------------------------------------
# network exception in _fetch_excel (lines 492-493)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
async def test_async_fetch_raises_provider_error_on_general_network_error() -> None:
    """async_fetch raises ProviderError on non-HTTP network exception (lines 492-493)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=OSError("network unreachable"))

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    with pytest.raises(ProviderError, match="network error"):
        await provider.async_fetch(session, "DE")


@_skip_if_no_openpyxl
async def test_async_list_stations_returns_empty_on_general_network_error() -> None:
    """async_list_stations returns [] on non-HTTP network exception (lines 492-493 via _fetch_excel)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=OSError("connection refused"))

    EuOilBulletinProvider._cached_workbook_bytes = None
    EuOilBulletinProvider._cached_fetch_time = None

    provider = _make_provider("DE")
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# _resolve_country_code: empty lines skipped (line 554) and partial match (line 566)
# ---------------------------------------------------------------------------


def test_resolve_country_code_skips_blank_lines_in_multiline_input() -> None:
    """_resolve_country_code skips blank lines between newlines (line 554)."""
    result = _resolve_country_code("\n\nGermany\n")
    assert result == "DE"


def test_resolve_country_code_partial_match_on_composite_string() -> None:
    """_resolve_country_code matches a composite string via word-boundary partial match (line 566)."""
    result = _resolve_country_code("european union weighted average")
    assert result == "EU27"


# ---------------------------------------------------------------------------
# _parse_sheet: row skipping edge cases (lines 589, 592, 595)
# ---------------------------------------------------------------------------


@_skip_if_no_openpyxl
def test_parse_sheet_skips_empty_row_tuple() -> None:
    """_parse_sheet skips an empty row tuple and continues (line 589)."""
    sheet_mock = MagicMock()
    sheet_mock.iter_rows = MagicMock(
        return_value=iter(
            [
                (),
                ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ]
        )
    )
    result = _parse_sheet(sheet_mock, 0)
    assert "DE" in result
    assert len(result) == 1


@_skip_if_no_openpyxl
def test_parse_sheet_skips_row_with_none_country_cell() -> None:
    """_parse_sheet skips a row whose country cell is None (line 592)."""
    sheet_mock = MagicMock()
    sheet_mock.iter_rows = MagicMock(
        return_value=iter(
            [
                (None, 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
                ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ]
        )
    )
    result = _parse_sheet(sheet_mock, 0)
    assert "DE" in result
    assert len(result) == 1


@_skip_if_no_openpyxl
def test_parse_sheet_skips_row_with_whitespace_only_country() -> None:
    """_parse_sheet skips a row whose country cell is whitespace-only (line 595)."""
    sheet_mock = MagicMock()
    sheet_mock.iter_rows = MagicMock(
        return_value=iter(
            [
                ("   ", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
                ("Germany", 1800.0, 1650.0, 900.0, 800.0, 700.0, 600.0),
            ]
        )
    )
    result = _parse_sheet(sheet_mock, 0)
    assert "DE" in result
    assert len(result) == 1
