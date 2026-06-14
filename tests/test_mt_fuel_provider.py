"""Tests for MtFuelProvider — Malta national-average fuel prices (EU Oil Bulletin)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.mt_fuel import (
    MtFuelProvider,
    _FALLBACK_XLSX_URL,
    _make_absolute,
    _parse_malta_row,
    _parse_price_cell,
)


# ---------------------------------------------------------------------------
# Helpers — minimal XLSX builder
# ---------------------------------------------------------------------------


def _make_xlsx_bytes(rows: list[list]) -> bytes:
    """Build an in-memory XLSX with *rows* in the first sheet.

    Each element of *rows* is a list of cell values for one row.
    Returns raw XLSX bytes suitable for passing to _parse_malta_row().
    """
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _malta_xlsx(
    petrol: float = 1340.0,
    diesel: float = 1210.0,
    lpg: float = 1000.0,
    heating_oil: float = 980.0,
) -> bytes:
    """Return XLSX bytes with a minimal Malta row at the expected column positions."""
    rows = [
        # Header row matching EU Oil Bulletin layout
        [
            "Country",
            "Euro-super 95",
            "Diesel",
            "Heating gas oil",
            "Fuel oil (LS)",
            "Fuel oil (HS)",
            "LPG",
        ],
        ["Germany", 1590.0, 1480.0, 850.0, 800.0, 780.0, 870.0],
        ["Malta", petrol, diesel, heating_oil, 800.0, 780.0, lpg],
        ["Netherlands", 1780.0, 1550.0, 890.0, 900.0],
    ]
    return _make_xlsx_bytes(rows)


def _make_mock_response(
    status: int = 200,
    body: bytes | str | None = None,
    *,
    is_binary: bool = False,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    if is_binary:
        mock_resp.read = AsyncMock(return_value=body or b"")
    else:
        mock_resp.text = AsyncMock(
            return_value=body.decode("utf-8")
            if isinstance(body, bytes)
            else (body or "")
        )
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


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """MtFuelProvider.COUNTRY is 'MT'."""
    assert MtFuelProvider.COUNTRY == "MT"


def test_provider_key() -> None:
    """MtFuelProvider.PROVIDER_KEY is 'mt_fuel'."""
    assert MtFuelProvider.PROVIDER_KEY == "mt_fuel"


def test_provider_label() -> None:
    """MtFuelProvider.LABEL contains 'Malta'."""
    assert "Malta" in MtFuelProvider.LABEL


def test_provider_config_mode() -> None:
    """MtFuelProvider.CONFIG_MODE is 'location'."""
    assert MtFuelProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """MtFuelProvider.STATION_LOOKUP_MODE is 'location_search'."""
    assert MtFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_weekly() -> None:
    """MtFuelProvider.POLL_INTERVAL_SECONDS is 604800 (one week)."""
    assert MtFuelProvider.POLL_INTERVAL_SECONDS == 604800


def test_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes unleaded, diesel, lpg, kerosene (standard StationData keys)."""
    caps = MtFuelProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "diesel" in caps
    assert "lpg" in caps
    assert "kerosene" in caps


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = MtFuelProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_default_station_id() -> None:
    """MtFuelProvider() defaults station_id to 'MT'."""
    p = MtFuelProvider()
    assert p._station_id == "MT"


def test_constructor_stores_station_id() -> None:
    """MtFuelProvider accepts a custom station_id (treated as 'MT' logically)."""
    p = MtFuelProvider("MT")
    assert p._station_id == "MT"


def test_constructor_stores_optional_params() -> None:
    """Constructor stores optional county, lat, lng, radius_km."""
    p = MtFuelProvider(
        "MT",
        county="Valletta",
        latitude=35.8989,
        longitude=14.5146,
        radius_km=5.0,
    )
    assert p._county == "Valletta"
    assert p._latitude == pytest.approx(35.8989)
    assert p._longitude == pytest.approx(14.5146)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_defaults_radius_to_10() -> None:
    """Constructor defaults radius_km to 10.0."""
    p = MtFuelProvider()
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_no_cached_xlsx_url() -> None:
    """Provider starts with no cached XLSX URL."""
    p = MtFuelProvider()
    assert p._cached_xlsx_url is None


# ---------------------------------------------------------------------------
# _parse_price_cell — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_cell_converts_eur_per_1000l() -> None:
    """_parse_price_cell divides EUR/1000L by 1000 to get EUR/litre."""
    assert _parse_price_cell(1340.0) == pytest.approx(1.340)


def test_parse_price_cell_diesel_value() -> None:
    """_parse_price_cell handles diesel typical value 1210."""
    assert _parse_price_cell(1210.0) == pytest.approx(1.210)


def test_parse_price_cell_lpg_value() -> None:
    """_parse_price_cell handles LPG typical value 1000."""
    assert _parse_price_cell(1000.0) == pytest.approx(1.000)


def test_parse_price_cell_none_input() -> None:
    """_parse_price_cell returns None for None input."""
    assert _parse_price_cell(None) is None


def test_parse_price_cell_zero_returns_none() -> None:
    """_parse_price_cell returns None for zero (non-positive)."""
    assert _parse_price_cell(0) is None


def test_parse_price_cell_negative_returns_none() -> None:
    """_parse_price_cell returns None for negative values."""
    assert _parse_price_cell(-500.0) is None


def test_parse_price_cell_string_numeric() -> None:
    """_parse_price_cell parses a string representation of a number."""
    assert _parse_price_cell("1340") == pytest.approx(1.340)


def test_parse_price_cell_invalid_string_returns_none() -> None:
    """_parse_price_cell returns None for non-numeric strings."""
    assert _parse_price_cell("n/a") is None


def test_parse_price_cell_rounds_to_4_decimal_places() -> None:
    """_parse_price_cell rounds to 4 decimal places."""
    result = _parse_price_cell(1340.5)
    assert result is not None
    assert result == pytest.approx(round(1340.5 / 1000.0, 4))


# ---------------------------------------------------------------------------
# _make_absolute — unit tests
# ---------------------------------------------------------------------------


def test_make_absolute_already_https() -> None:
    """_make_absolute leaves https:// URLs unchanged."""
    url = "https://energy.ec.europa.eu/document/download/abc_en"
    assert _make_absolute(url) == url


def test_make_absolute_already_http() -> None:
    """_make_absolute raises ProviderError for non-energy.ec.europa.eu http:// URLs."""
    url = "http://example.com/file.xlsx"
    with pytest.raises(ProviderError, match="SSRF guard"):
        _make_absolute(url)


def test_make_absolute_protocol_relative() -> None:
    """_make_absolute prepends https: to protocol-relative //... URLs."""
    assert (
        _make_absolute("//energy.ec.europa.eu/path")
        == "https://energy.ec.europa.eu/path"
    )


def test_make_absolute_root_relative() -> None:
    """_make_absolute prepends energy.ec.europa.eu to root-relative paths."""
    result = _make_absolute("/document/download/abc_en")
    assert result == "https://energy.ec.europa.eu/document/download/abc_en"


def test_make_absolute_relative_path() -> None:
    """_make_absolute prepends energy.ec.europa.eu/ to bare relative paths."""
    result = _make_absolute("document/download/abc_en")
    assert result.startswith("https://energy.ec.europa.eu/")


# ---------------------------------------------------------------------------
# _parse_malta_row — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_malta_row_returns_petrol_price() -> None:
    """_parse_malta_row extracts petrol_95 from the Malta row."""
    xlsx = _malta_xlsx(petrol=1340.0)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["petrol_95"] == pytest.approx(1.340)


@pytest.mark.asyncio
async def test_parse_malta_row_returns_diesel_price() -> None:
    """_parse_malta_row extracts diesel from the Malta row."""
    xlsx = _malta_xlsx(diesel=1210.0)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["diesel"] == pytest.approx(1.210)


@pytest.mark.asyncio
async def test_parse_malta_row_returns_lpg_price() -> None:
    """_parse_malta_row extracts lpg from the Malta row."""
    xlsx = _malta_xlsx(lpg=1000.0)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["lpg"] == pytest.approx(1.000)


@pytest.mark.asyncio
async def test_parse_malta_row_returns_heating_oil_price() -> None:
    """_parse_malta_row extracts heating_oil from the Malta row."""
    xlsx = _malta_xlsx(heating_oil=980.0)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["heating_oil"] == pytest.approx(0.980)


@pytest.mark.asyncio
async def test_parse_malta_row_returns_none_when_malta_absent() -> None:
    """_parse_malta_row returns None when no Malta row is found."""
    rows = [
        ["Country", "Euro-super 95", "Diesel", "LPG", "Heating Oil"],
        ["Germany", 1590.0, 1480.0, 870.0, 850.0],
        ["Netherlands", 1780.0, 1550.0, 890.0, 900.0],
    ]
    xlsx = _make_xlsx_bytes(rows)
    result = await _parse_malta_row(xlsx)
    assert result is None


@pytest.mark.asyncio
async def test_parse_malta_row_matches_malta_with_asterisk() -> None:
    """_parse_malta_row matches 'Malta *' (footnote variant) as the Malta row."""
    rows = [
        ["Country", "Euro-super 95", "Diesel", "LPG", "Heating Oil"],
        ["Malta *", 1340.0, 1210.0, 1000.0, 980.0],
    ]
    xlsx = _make_xlsx_bytes(rows)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["petrol_95"] == pytest.approx(1.340)


@pytest.mark.asyncio
async def test_parse_malta_row_handles_none_price_cells() -> None:
    """_parse_malta_row returns None prices when cells are empty."""
    rows = [
        ["Malta", None, None, None, None],
    ]
    xlsx = _make_xlsx_bytes(rows)
    result = await _parse_malta_row(xlsx)
    assert result is not None
    assert result["petrol_95"] is None
    assert result["diesel"] is None
    assert result["lpg"] is None
    assert result["heating_oil"] is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_petrol_price() -> None:
    """async_fetch populates unleaded (petrol 95) from the EU Oil Bulletin Malta row."""
    xlsx_bytes = _malta_xlsx(
        petrol=1340.0, diesel=1210.0, lpg=1000.0, heating_oil=980.0
    )

    page_resp = _make_mock_response(
        200,
        body=b'<a href="/document/download/264c2d0f_en?filename=Weekly%20Oil%20Bulletin%20Weekly%20prices%20with%20Taxes%20-%202024.xlsx">download</a>',
    )
    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(page_resp, xlsx_resp)

    provider = MtFuelProvider()
    data = await provider.async_fetch(session, "MT")

    assert data["unleaded"] == pytest.approx(1.340)


async def test_async_fetch_returns_diesel_price() -> None:
    """async_fetch populates diesel from the EU Oil Bulletin Malta row."""
    xlsx_bytes = _malta_xlsx(diesel=1210.0)

    _make_mock_response(200, body=b"<html>no matching link here</html>")
    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)

    provider = MtFuelProvider()
    # Inject the fallback URL directly to skip landing-page scrape
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    session = _make_session(xlsx_resp)
    data = await provider.async_fetch(session, "MT")

    assert data["diesel"] == pytest.approx(1.210)


async def test_async_fetch_returns_lpg_price() -> None:
    """async_fetch populates lpg from the EU Oil Bulletin Malta row."""
    xlsx_bytes = _malta_xlsx(lpg=1000.0)
    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(xlsx_resp)
    data = await provider.async_fetch(session, "MT")

    assert data["lpg"] == pytest.approx(1.000)


async def test_async_fetch_returns_heating_oil_as_kerosene() -> None:
    """async_fetch maps heating_oil to the 'kerosene' StationData key."""
    xlsx_bytes = _malta_xlsx(heating_oil=980.0)
    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(xlsx_resp)
    data = await provider.async_fetch(session, "MT")

    assert data["kerosene"] == pytest.approx(0.980)


async def test_async_fetch_returns_name_and_county() -> None:
    """async_fetch sets name and county to Malta national average strings."""
    xlsx_bytes = _malta_xlsx()
    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(xlsx_resp)
    data = await provider.async_fetch(session, "MT")

    assert data["name"] == "Malta — national average"
    assert data["county"] == "Malta"


async def test_async_fetch_source_station_id_is_mt() -> None:
    """async_fetch sets source_station_id to 'MT'."""
    xlsx_bytes = _malta_xlsx()
    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(xlsx_resp)
    data = await provider.async_fetch(session, "MT")

    assert data["source_station_id"] == "MT"


async def test_async_fetch_caches_xlsx_url_from_landing_page() -> None:
    """async_fetch caches the discovered XLSX URL for subsequent calls."""
    xlsx_bytes = _malta_xlsx()

    landing_html = (
        b'<a href="/document/download/abc123_en'
        b'?filename=Weekly%20Oil%20Bulletin%20Weekly%20prices%20with%20Taxes%20-%202024.xlsx">'
        b"download</a>"
    )
    page_resp = _make_mock_response(200, body=landing_html)
    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(page_resp, xlsx_resp)

    provider = MtFuelProvider()
    assert provider._cached_xlsx_url is None

    await provider.async_fetch(session, "MT")

    # URL should now be cached
    assert provider._cached_xlsx_url is not None
    assert "abc123" in provider._cached_xlsx_url


async def test_async_fetch_uses_fallback_url_when_page_scrape_fails() -> None:
    """async_fetch falls back to _FALLBACK_XLSX_URL when landing page returns error."""
    xlsx_bytes = _malta_xlsx()

    page_resp = _make_mock_response(500, body=b"Internal Server Error")
    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(page_resp, xlsx_resp)

    provider = MtFuelProvider()
    data = await provider.async_fetch(session, "MT")

    assert data["diesel"] == pytest.approx(1.210)
    # Fallback URL was used; should NOT be cached (only discovered URLs are cached)
    assert provider._cached_xlsx_url is None


async def test_async_fetch_raises_on_xlsx_download_failure() -> None:
    """async_fetch raises ProviderError when XLSX cannot be downloaded."""
    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    with pytest.raises(ProviderError, match="MtFuelProvider"):
        await provider.async_fetch(session, "MT")


async def test_async_fetch_raises_when_malta_row_missing() -> None:
    """async_fetch raises ProviderError when Malta row is absent from XLSX."""
    rows = [
        ["Country", "Euro-super 95", "Diesel", "LPG", "Heating Oil"],
        ["Germany", 1590.0, 1480.0, 870.0, 850.0],
    ]
    xlsx_bytes = _make_xlsx_bytes(rows)

    provider = MtFuelProvider()
    provider._cached_xlsx_url = _FALLBACK_XLSX_URL

    xlsx_resp = _make_mock_response(200, body=xlsx_bytes, is_binary=True)
    session = _make_session(xlsx_resp)

    with pytest.raises(ProviderError, match="Malta row not found"):
        await provider.async_fetch(session, "MT")


async def test_async_fetch_raises_on_http_404_for_xlsx() -> None:
    """async_fetch raises ProviderError and clears cache on 404 XLSX response."""
    provider = MtFuelProvider()
    provider._cached_xlsx_url = "https://energy.ec.europa.eu/document/download/stale_en"

    xlsx_resp = _make_mock_response(404, body=b"")
    xlsx_resp.read = AsyncMock(return_value=b"")
    session = _make_session(xlsx_resp)

    with pytest.raises(ProviderError, match="MtFuelProvider"):
        await provider.async_fetch(session, "MT")

    # Cache should be cleared so next poll re-scrapes the landing page
    assert provider._cached_xlsx_url is None


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_static_string() -> None:
    """async_fetch_station_name returns 'Malta — national average' without HTTP."""
    provider = MtFuelProvider()
    session = MagicMock()
    session.get = MagicMock(side_effect=AssertionError("should not make HTTP requests"))

    name = await provider.async_fetch_station_name(session, "MT")
    assert name == "Malta — national average"


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_single_entry() -> None:
    """async_list_stations returns exactly one entry."""
    provider = MtFuelProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session)

    assert len(result) == 1


async def test_async_list_stations_station_id_is_mt() -> None:
    """async_list_stations returns 'MT' as the station ID."""
    provider = MtFuelProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session)

    sid, _ = result[0]
    assert sid == "MT"


async def test_async_list_stations_label_contains_malta() -> None:
    """async_list_stations label mentions Malta."""
    provider = MtFuelProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "Malta" in label


async def test_async_list_stations_label_mentions_eu_bulletin() -> None:
    """async_list_stations label mentions EU Oil Bulletin."""
    provider = MtFuelProvider()
    session = MagicMock()
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "EU Oil Bulletin" in label or "Oil Bulletin" in label


async def test_async_list_stations_no_http_request_made() -> None:
    """async_list_stations returns data without making any HTTP requests."""
    provider = MtFuelProvider()
    session = MagicMock()
    session.get = MagicMock(side_effect=AssertionError("should not make HTTP requests"))

    result = await provider.async_list_stations(session)
    assert isinstance(result, list)
    assert len(result) == 1


async def test_async_list_stations_accepts_coord_kwargs() -> None:
    """async_list_stations accepts lat/lng/radius_km kwargs without error."""
    provider = MtFuelProvider()
    session = MagicMock()
    result = await provider.async_list_stations(
        session, lat=35.8989, lng=14.5146, radius_km=10.0
    )
    assert len(result) == 1


async def test_async_list_stations_is_not_none_coord_check() -> None:
    """async_list_stations uses is-not-None checks (0.0 lat/lng are valid)."""
    provider = MtFuelProvider(latitude=0.0, longitude=0.0)
    session = MagicMock()
    # Should not raise and should still return the single entry
    result = await provider.async_list_stations(session)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """MtFuelProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "mt_fuel" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["mt_fuel"] is MtFuelProvider
