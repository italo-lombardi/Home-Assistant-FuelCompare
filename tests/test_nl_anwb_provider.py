"""Tests for NlAnwbProvider (Netherlands national average via EU Oil Bulletin)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientSession

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.nl_anwb import (
    NlAnwbProvider,
    _extract_price,
    _parse_bulletin,
)

# ---------------------------------------------------------------------------
# Helpers — build a minimal XLSX workbook in memory
# ---------------------------------------------------------------------------


def _make_xlsx_bytes(
    nl_benzine: float | None = 2255.94,
    nl_diesel: float | None = 2150.59,
    nl_lpg: float | None = 900.0,
    nl_heating: float | None = 1200.0,
    include_nl: bool = True,
    bulletin_date: str = "2026-06-08",
) -> bytes:
    """Build a minimal EC Oil Bulletin XLSX in memory.

    Row layout mirrors the real workbook:
      Row 0: date row  (col 1 = bulletin_date string)
      Row 1: units header
      Row 2: Germany  (a filler country)
      Row 3: Netherlands (when include_nl=True)
    """
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        pytest.skip("openpyxl not installed — skipping XLSX builder")

    wb = openpyxl.Workbook()
    ws = wb.active

    # Row 1 — date row
    ws.append(["", bulletin_date, "", "", "", ""])
    # Row 2 — units
    ws.append(
        ["Country", "Euro/1000L", "Euro/1000L", "Euro/1000L", "change", "Euro/1000L"]
    )
    # Row 3 — Germany (filler)
    ws.append(["Germany", 1900.0, 1750.0, 850.0, 5.0, 1100.0])
    # Row 4 — Netherlands (optional)
    if include_nl:
        ws.append(
            [
                "Netherlands",
                nl_benzine,
                nl_diesel,
                nl_lpg,
                0.0,  # col 4 is a change column, skipped
                nl_heating,
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_mock_response(
    status: int = 200,
    body: bytes = b"",
) -> AsyncMock:
    """Build a mock aiohttp response for use as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=body)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock aiohttp ClientSession whose .get() returns *response*."""
    session = MagicMock(spec=ClientSession)
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """NlAnwbProvider declares COUNTRY='NL'."""
    assert NlAnwbProvider.COUNTRY == "NL"


def test_provider_key() -> None:
    """NlAnwbProvider declares PROVIDER_KEY='nl_anwb'."""
    assert NlAnwbProvider.PROVIDER_KEY == "nl_anwb"


def test_provider_label_contains_netherlands() -> None:
    """NlAnwbProvider label mentions Netherlands."""
    assert (
        "Netherlands" in NlAnwbProvider.LABEL
        or "Dutch" in NlAnwbProvider.LABEL
        or "NL" in NlAnwbProvider.LABEL
    )


def test_provider_config_mode_is_location() -> None:
    """NlAnwbProvider uses CONFIG_MODE='location' (national average)."""
    assert NlAnwbProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """NlAnwbProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert NlAnwbProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_does_not_require_api_key() -> None:
    """NlAnwbProvider does not require an API key (public data)."""
    assert NlAnwbProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_is_daily() -> None:
    """POLL_INTERVAL_SECONDS is at least 3600 (weekly bulletin, poll daily)."""
    assert NlAnwbProvider.POLL_INTERVAL_SECONDS >= 3600


def test_provider_capabilities_include_e10() -> None:
    """CAPABILITIES includes 'e10' (benzine / Euro-super 95 E10)."""
    assert "e10" in NlAnwbProvider.CAPABILITIES


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in NlAnwbProvider.CAPABILITIES


def test_provider_capabilities_include_lpg() -> None:
    """CAPABILITIES includes 'lpg'."""
    assert "lpg" in NlAnwbProvider.CAPABILITIES


def test_provider_capabilities_include_kerosene() -> None:
    """CAPABILITIES includes 'kerosene' (heating gas oil)."""
    assert "kerosene" in NlAnwbProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = NlAnwbProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_default_station_id() -> None:
    """Default station_id is 'NL'."""
    provider = NlAnwbProvider()
    assert provider._station_id == "NL"


def test_constructor_ignores_custom_station_id() -> None:
    """Custom station_id is normalised to 'NL'."""
    provider = NlAnwbProvider(station_id="anything")
    assert provider._station_id == "NL"


def test_constructor_custom_bulletin_url() -> None:
    """Constructor stores a custom bulletin URL for testing."""
    url = "https://example.com/fake_bulletin.xlsx"
    provider = NlAnwbProvider(bulletin_url=url)
    assert provider._bulletin_url == url


def test_constructor_default_bulletin_url_is_ec() -> None:
    """Default bulletin URL points to the European Commission."""
    provider = NlAnwbProvider()
    assert "energy.ec.europa.eu" in provider._bulletin_url


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_string() -> None:
    """async_fetch_station_name returns a non-empty string without HTTP calls."""
    provider = NlAnwbProvider()
    session = MagicMock()
    name = await provider.async_fetch_station_name(session, "NL")
    assert isinstance(name, str)
    assert name  # non-empty


async def test_async_fetch_station_name_no_http_call() -> None:
    """async_fetch_station_name does not make any HTTP request."""
    provider = NlAnwbProvider()
    session = MagicMock()
    await provider.async_fetch_station_name(session, "NL")
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_single_nl_entry() -> None:
    """async_list_stations returns exactly one ('NL', label) tuple."""
    provider = NlAnwbProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session)
    assert len(result) == 1
    uid, label = result[0]
    assert uid == "NL"
    assert isinstance(label, str)
    assert label


async def test_async_list_stations_with_lat_lng_still_returns_nl() -> None:
    """async_list_stations with coordinates still returns the NL entry."""
    provider = NlAnwbProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session, lat=52.37, lng=4.90)
    assert len(result) == 1
    assert result[0][0] == "NL"


async def test_async_list_stations_with_zero_coords_returns_nl() -> None:
    """async_list_stations with lat=0.0/lng=0.0 must not drop the entry (is-not-None check)."""
    provider = NlAnwbProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session, lat=0.0, lng=0.0)
    assert len(result) == 1
    assert result[0][0] == "NL"


async def test_async_list_stations_no_http_calls() -> None:
    """async_list_stations does not make HTTP requests."""
    provider = NlAnwbProvider()
    session = MagicMock()
    await provider.async_list_stations(session, lat=52.0, lng=5.0)
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_price helper
# ---------------------------------------------------------------------------


def test_extract_price_divides_by_1000() -> None:
    """_extract_price converts EUR/1000L to EUR/L by dividing by 1000."""
    row = ("Netherlands", 2255.94, 2150.59, 900.0, 0.0, 1200.0)
    result = _extract_price(row, 1)
    assert result == pytest.approx(2.25594, rel=1e-4)


def test_extract_price_rounds_to_4_decimals() -> None:
    """_extract_price rounds to 4 decimal places."""
    row = ("Netherlands", 2255.945, 0.0, 0.0, 0.0, 0.0)
    result = _extract_price(row, 1)
    assert result is not None
    assert result == round(2255.945 / 1000.0, 4)


def test_extract_price_returns_none_for_none_cell() -> None:
    """_extract_price returns None when the cell value is None."""
    row = ("Netherlands", None, None, None, None, None)
    assert _extract_price(row, 1) is None


def test_extract_price_returns_none_for_zero() -> None:
    """_extract_price returns None for a zero value."""
    row = ("Netherlands", 0.0, 2150.0, 900.0, 0.0, 1200.0)
    assert _extract_price(row, 1) is None


def test_extract_price_returns_none_for_negative() -> None:
    """_extract_price returns None for a negative value."""
    row = ("Netherlands", -100.0, 2150.0, 900.0, 0.0, 1200.0)
    assert _extract_price(row, 1) is None


def test_extract_price_returns_none_for_implausibly_large_value() -> None:
    """_extract_price returns None for values above 10 000 EUR/1000L."""
    row = ("Netherlands", 99999.0, 2150.0, 900.0, 0.0, 1200.0)
    assert _extract_price(row, 1) is None


def test_extract_price_returns_none_for_out_of_bounds_column() -> None:
    """_extract_price returns None when col_idx exceeds row length."""
    row = ("Netherlands", 2255.94)
    assert _extract_price(row, 10) is None


def test_extract_price_rejects_non_numeric_string() -> None:
    """_extract_price returns None for non-numeric string cell values."""
    row = ("Netherlands", "n/a", 2150.0, 900.0, 0.0, 1200.0)
    assert _extract_price(row, 1) is None


# ---------------------------------------------------------------------------
# _parse_bulletin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_bulletin_returns_e10_price() -> None:
    """_parse_bulletin returns correct EUR/L benzine (e10) price."""
    raw = _make_xlsx_bytes(nl_benzine=2255.94)
    data = await _parse_bulletin(raw)
    assert data["e10"] == pytest.approx(2.25594, rel=1e-4)


@pytest.mark.asyncio
async def test_parse_bulletin_returns_diesel_price() -> None:
    """_parse_bulletin returns correct EUR/L diesel price."""
    raw = _make_xlsx_bytes(nl_diesel=2150.59)
    data = await _parse_bulletin(raw)
    assert data["diesel"] == pytest.approx(2.15059, rel=1e-4)


@pytest.mark.asyncio
async def test_parse_bulletin_returns_lpg_price() -> None:
    """_parse_bulletin returns correct EUR/L LPG price."""
    raw = _make_xlsx_bytes(nl_lpg=900.0)
    data = await _parse_bulletin(raw)
    assert data["lpg"] == pytest.approx(0.9, rel=1e-4)


@pytest.mark.asyncio
async def test_parse_bulletin_returns_kerosene_price() -> None:
    """_parse_bulletin maps heating gas oil to the 'kerosene' key."""
    raw = _make_xlsx_bytes(nl_heating=1200.0)
    data = await _parse_bulletin(raw)
    assert data["kerosene"] == pytest.approx(1.2, rel=1e-4)


@pytest.mark.asyncio
async def test_parse_bulletin_sets_name() -> None:
    """_parse_bulletin sets a non-empty 'name' field."""
    raw = _make_xlsx_bytes()
    data = await _parse_bulletin(raw)
    assert data.get("name")


@pytest.mark.asyncio
async def test_parse_bulletin_sets_source_station_id_to_nl() -> None:
    """_parse_bulletin sets source_station_id to 'NL'."""
    raw = _make_xlsx_bytes()
    data = await _parse_bulletin(raw)
    assert data.get("source_station_id") == "NL"


@pytest.mark.asyncio
async def test_parse_bulletin_sets_lastupdated() -> None:
    """_parse_bulletin populates lastupdated from the date row."""
    raw = _make_xlsx_bytes(bulletin_date="2026-06-08")
    data = await _parse_bulletin(raw)
    assert data.get("lastupdated") is not None


@pytest.mark.asyncio
async def test_parse_bulletin_raises_when_nl_row_missing() -> None:
    """_parse_bulletin raises ProviderError when Netherlands row is absent."""
    raw = _make_xlsx_bytes(include_nl=False)
    with pytest.raises(ProviderError, match="Netherlands"):
        await _parse_bulletin(raw)


@pytest.mark.asyncio
async def test_parse_bulletin_handles_none_prices() -> None:
    """_parse_bulletin returns None for price cells that are None."""
    raw = _make_xlsx_bytes(nl_benzine=None, nl_diesel=None)
    data = await _parse_bulletin(raw)
    assert data["e10"] is None
    assert data["diesel"] is None


@pytest.mark.asyncio
async def test_parse_bulletin_raises_on_invalid_bytes() -> None:
    """_parse_bulletin raises ProviderError for non-XLSX bytes."""
    with pytest.raises(ProviderError):
        await _parse_bulletin(b"not an xlsx file at all")


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns normalised StationData on a successful response."""
    raw = _make_xlsx_bytes()
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()
    data = await provider.async_fetch(session, "NL")

    assert data.get("e10") is not None
    assert data.get("diesel") is not None


async def test_async_fetch_e10_price() -> None:
    """async_fetch returns correct EUR/L benzine price."""
    raw = _make_xlsx_bytes(nl_benzine=2255.94)
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()
    data = await provider.async_fetch(session, "NL")

    assert data["e10"] == pytest.approx(2.25594, rel=1e-4)


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns correct EUR/L diesel price."""
    raw = _make_xlsx_bytes(nl_diesel=2150.59)
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()
    data = await provider.async_fetch(session, "NL")

    assert data["diesel"] == pytest.approx(2.15059, rel=1e-4)


async def test_async_fetch_prices_in_eur_per_litre() -> None:
    """async_fetch prices are EUR/litre (not EUR/1000L)."""
    raw = _make_xlsx_bytes(nl_benzine=2000.0, nl_diesel=1800.0)
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()
    data = await provider.async_fetch(session, "NL")

    # EUR/1000L ÷ 1000 → EUR/litre; a typical price is around 1.5–3.0 EUR/L
    assert data["e10"] == pytest.approx(2.0, rel=1e-4)
    assert data["diesel"] == pytest.approx(1.8, rel=1e-4)


async def test_async_fetch_station_id_is_nl() -> None:
    """async_fetch sets source_station_id to 'NL'."""
    raw = _make_xlsx_bytes()
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()
    data = await provider.async_fetch(session, "NL")

    assert data.get("source_station_id") == "NL"


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_http_404() -> None:
    """async_fetch raises ProviderError when the bulletin returns HTTP 404."""
    resp = _make_mock_response(404, body=b"")
    session = _make_session(resp)

    provider = NlAnwbProvider()

    with pytest.raises(ProviderError, match="404"):
        await provider.async_fetch(session, "NL")


async def test_async_fetch_raises_provider_error_on_http_500() -> None:
    """async_fetch raises ProviderError when the server returns HTTP 500."""
    resp = _make_mock_response(500, body=b"")
    session = _make_session(resp)

    provider = NlAnwbProvider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "NL")


async def test_async_fetch_raises_provider_error_on_connection_error() -> None:
    """async_fetch raises ProviderError on a network connection error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=Exception("connection refused"))

    provider = NlAnwbProvider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "NL")


async def test_async_fetch_raises_provider_error_on_invalid_xlsx() -> None:
    """async_fetch raises ProviderError when the response body is not a valid XLSX."""
    resp = _make_mock_response(200, body=b"garbage bytes not xlsx")
    session = _make_session(resp)

    provider = NlAnwbProvider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "NL")


async def test_async_fetch_raises_provider_error_when_nl_row_missing() -> None:
    """async_fetch raises ProviderError when Netherlands row is absent from workbook."""
    raw = _make_xlsx_bytes(include_nl=False)
    resp = _make_mock_response(200, body=raw)
    session = _make_session(resp)

    provider = NlAnwbProvider()

    with pytest.raises(ProviderError, match="Netherlands"):
        await provider.async_fetch(session, "NL")


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """NlAnwbProvider is registered in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "nl_anwb" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["nl_anwb"] is NlAnwbProvider
