"""Tests for DkFuelFinderProvider (fuelfinder.dk Denmark)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.dk_fuelfinder import (
    DkFuelFinderProvider,
    _HEADERS,
    _LISTPRICES_URL,
    _MAX_DKK_PER_LITRE,
    _TableParser,
    _parse_price_dkk,
    _parse_table,
)


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

# Minimal HTML table that matches the fuelfinder.dk structure.
# Columns: Benzinselskab, Blyfri 92, Blyfri 95 (E10), Blyfri 95+ (E10),
#          Blyfri + (E5), Diesel (B7), Diesel +, HVO (XTL)
_TABLE_HTML = """\
<!DOCTYPE html>
<html>
<body>
<table>
  <tr>
    <th>Benzinselskab</th>
    <th>Blyfri 92</th>
    <th>Blyfri 95 (E10)</th>
    <th>Blyfri 95+ (E10)</th>
    <th>Blyfri + (E5)</th>
    <th>Diesel (B7)</th>
    <th>Diesel +</th>
    <th>HVO (XTL)</th>
  </tr>
  <tr>
    <td>Circle K</td>
    <td>13,50</td>
    <td>14,13</td>
    <td>14,75</td>
    <td>15,00</td>
    <td>13,89</td>
    <td>14,50</td>
    <td>16,00</td>
  </tr>
  <tr>
    <td>Q8</td>
    <td>13,40</td>
    <td>14,05</td>
    <td>14,65</td>
    <td>14,90</td>
    <td>13,79</td>
    <td>14,40</td>
    <td></td>
  </tr>
  <tr>
    <td>Shell</td>
    <td></td>
    <td>14,20</td>
    <td></td>
    <td></td>
    <td>13,95</td>
    <td></td>
    <td></td>
  </tr>
</table>
</body>
</html>
"""

# HTML with no table at all.
_NO_TABLE_HTML = "<html><body><p>No table here</p></body></html>"

# HTML with a table but no data rows (header only).
_HEADER_ONLY_HTML = """\
<html><body><table>
  <tr><th>Benzinselskab</th><th>Blyfri 95 (E10)</th><th>Diesel (B7)</th></tr>
</table></body></html>
"""

# HTML with wrong header (first column not Benzinselskab).
_BAD_HEADER_HTML = """\
<html><body><table>
  <tr><th>Station</th><th>Price</th></tr>
  <tr><td>Circle K</td><td>14.13</td></tr>
</table></body></html>
"""

# HTML using period as decimal separator (some locales).
_PERIOD_DECIMAL_HTML = """\
<html><body><table>
  <tr>
    <th>Benzinselskab</th>
    <th>Blyfri 95 (E10)</th>
    <th>Diesel (B7)</th>
  </tr>
  <tr>
    <td>OK</td>
    <td>14.20</td>
    <td>13.85</td>
  </tr>
</table></body></html>
"""


def _make_mock_response(status: int = 200, body: str = "") -> AsyncMock:
    """Build a mock aiohttp response as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=body)
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


def test_provider_country() -> None:
    """DkFuelFinderProvider.COUNTRY is 'DK'."""
    assert DkFuelFinderProvider.COUNTRY == "DK"


def test_provider_key() -> None:
    """DkFuelFinderProvider.PROVIDER_KEY is 'dk_fuelfinder'."""
    assert DkFuelFinderProvider.PROVIDER_KEY == "dk_fuelfinder"


def test_provider_label() -> None:
    """DkFuelFinderProvider.LABEL is non-empty and mentions Denmark or FuelFinder."""
    label = DkFuelFinderProvider.LABEL
    assert isinstance(label, str)
    assert len(label) > 0
    assert "Denmark" in label or "DK" in label or "Fuel" in label


def test_provider_config_mode() -> None:
    """DkFuelFinderProvider.CONFIG_MODE is 'station_id'."""
    assert DkFuelFinderProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """DkFuelFinderProvider.STATION_LOOKUP_MODE is 'location_search'."""
    assert DkFuelFinderProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_no_api_key() -> None:
    """DkFuelFinderProvider.REQUIRES_API_KEY is False."""
    assert DkFuelFinderProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval() -> None:
    """Poll interval is 3600 seconds (1 hour)."""
    assert DkFuelFinderProvider.POLL_INTERVAL_SECONDS == 3600


def test_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes unleaded and diesel."""
    caps = DkFuelFinderProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "diesel" in caps


def test_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes name and brand."""
    caps = DkFuelFinderProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = DkFuelFinderProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constants and headers
# ---------------------------------------------------------------------------


def test_listprices_url_points_to_fuelfinder_dk() -> None:
    """_LISTPRICES_URL points to fuelfinder.dk/listprices.php."""
    assert "fuelfinder.dk" in _LISTPRICES_URL
    assert "listprices" in _LISTPRICES_URL
    assert _LISTPRICES_URL.startswith("https://")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a Chrome-like User-Agent to bypass the WAF."""
    ua = _HEADERS.get("User-Agent", "")
    assert "Mozilla" in ua or "Chrome" in ua


def test_headers_include_accept() -> None:
    """_HEADERS includes an Accept header."""
    assert _HEADERS.get("Accept", "") != ""


def test_max_dkk_per_litre_is_reasonable() -> None:
    """_MAX_DKK_PER_LITRE is a positive float in a reasonable range (20–100)."""
    assert 20.0 <= _MAX_DKK_PER_LITRE <= 100.0


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    p = DkFuelFinderProvider("Circle K")
    assert p._station_id == "Circle K"


def test_constructor_default_radius() -> None:
    """Default radius_km is 10.0 when not supplied."""
    p = DkFuelFinderProvider("Circle K")
    assert p._radius_km == 10.0


def test_constructor_custom_radius() -> None:
    """radius_km is stored when explicitly provided."""
    p = DkFuelFinderProvider("Q8", radius_km=25.0)
    assert p._radius_km == 25.0


def test_constructor_accepts_lat_lng() -> None:
    """Constructor accepts latitude and longitude."""
    p = DkFuelFinderProvider("Shell", latitude=55.67, longitude=12.57)
    assert p._latitude == 55.67
    assert p._longitude == 12.57


def test_constructor_lat_lng_none_by_default() -> None:
    """Latitude and longitude default to None when not provided."""
    p = DkFuelFinderProvider("Circle K")
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# _parse_price_dkk — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_dkk_comma_separator() -> None:
    """_parse_price_dkk handles Danish comma decimal separator."""
    assert _parse_price_dkk("14,13") == pytest.approx(14.13)


def test_parse_price_dkk_period_separator() -> None:
    """_parse_price_dkk handles period decimal separator."""
    assert _parse_price_dkk("14.13") == pytest.approx(14.13)


def test_parse_price_dkk_none_returns_none() -> None:
    """_parse_price_dkk returns None for None input."""
    assert _parse_price_dkk(None) is None


def test_parse_price_dkk_empty_string_returns_none() -> None:
    """_parse_price_dkk returns None for empty string."""
    assert _parse_price_dkk("") is None


def test_parse_price_dkk_whitespace_only_returns_none() -> None:
    """_parse_price_dkk returns None for whitespace-only string."""
    assert _parse_price_dkk("   ") is None


def test_parse_price_dkk_non_numeric_returns_none() -> None:
    """_parse_price_dkk returns None for non-numeric string."""
    assert _parse_price_dkk("N/A") is None


def test_parse_price_dkk_zero_returns_none() -> None:
    """_parse_price_dkk returns None for zero (not a valid price)."""
    assert _parse_price_dkk("0") is None


def test_parse_price_dkk_negative_returns_none() -> None:
    """_parse_price_dkk returns None for negative values."""
    assert _parse_price_dkk("-5.00") is None


def test_parse_price_dkk_above_max_returns_none() -> None:
    """_parse_price_dkk returns None for values above _MAX_DKK_PER_LITRE."""
    assert _parse_price_dkk(str(_MAX_DKK_PER_LITRE + 0.1)) is None


def test_parse_price_dkk_typical_danish_price() -> None:
    """_parse_price_dkk accepts a typical Danish pump price (~13–15 DKK/L)."""
    assert _parse_price_dkk("13,89") == pytest.approx(13.89)
    assert _parse_price_dkk("15,50") == pytest.approx(15.50)


def test_parse_price_dkk_rounds_to_two_places() -> None:
    """_parse_price_dkk rounds to 2 decimal places."""
    result = _parse_price_dkk("14,1234")
    assert result is not None
    assert round(result, 2) == result


# ---------------------------------------------------------------------------
# _TableParser — unit tests
# ---------------------------------------------------------------------------


def test_table_parser_extracts_header_row() -> None:
    """_TableParser extracts the header row from the first table."""
    parser = _TableParser()
    parser.feed(_TABLE_HTML)
    rows = parser.rows
    assert len(rows) >= 1
    assert "Benzinselskab" in rows[0]


def test_table_parser_extracts_data_rows() -> None:
    """_TableParser extracts all data rows."""
    parser = _TableParser()
    parser.feed(_TABLE_HTML)
    rows = parser.rows
    # 1 header + 3 data rows
    assert len(rows) == 4


def test_table_parser_first_col_is_brand() -> None:
    """_TableParser row[0][0] is the brand name."""
    parser = _TableParser()
    parser.feed(_TABLE_HTML)
    rows = parser.rows
    assert rows[1][0] == "Circle K"
    assert rows[2][0] == "Q8"


def test_table_parser_extracts_price_cells() -> None:
    """_TableParser preserves cell text including prices."""
    parser = _TableParser()
    parser.feed(_TABLE_HTML)
    rows = parser.rows
    # Row 1 is Circle K; index 2 is Blyfri 95 (E10) = "14,13"
    header = rows[0]
    col_idx = header.index("Blyfri 95 (E10)")
    assert rows[1][col_idx] == "14,13"


def test_table_parser_empty_cell_is_empty_string() -> None:
    """_TableParser returns an empty string for empty table cells."""
    parser = _TableParser()
    parser.feed(_TABLE_HTML)
    rows = parser.rows
    header = rows[0]
    hvos_idx = header.index("HVO (XTL)")
    # Q8 has no HVO price (empty cell)
    assert rows[2][hvos_idx] == ""


def test_table_parser_no_table_returns_empty_rows() -> None:
    """_TableParser returns empty rows list when no table is present."""
    parser = _TableParser()
    parser.feed(_NO_TABLE_HTML)
    assert parser.rows == []


# ---------------------------------------------------------------------------
# _parse_table — unit tests
# ---------------------------------------------------------------------------


def test_parse_table_returns_all_brands() -> None:
    """_parse_table returns all brands from the HTML table."""
    result = _parse_table(_TABLE_HTML)
    assert "Circle K" in result
    assert "Q8" in result
    assert "Shell" in result


def test_parse_table_circle_k_unleaded() -> None:
    """_parse_table parses Circle K unleaded price correctly."""
    result = _parse_table(_TABLE_HTML)
    assert result["Circle K"]["unleaded"] == pytest.approx(14.13)


def test_parse_table_circle_k_diesel() -> None:
    """_parse_table parses Circle K diesel price correctly."""
    result = _parse_table(_TABLE_HTML)
    assert result["Circle K"]["diesel"] == pytest.approx(13.89)


def test_parse_table_q8_diesel() -> None:
    """_parse_table parses Q8 diesel price correctly."""
    result = _parse_table(_TABLE_HTML)
    assert result["Q8"]["diesel"] == pytest.approx(13.79)


def test_parse_table_shell_missing_premium_unleaded() -> None:
    """_parse_table returns None for Shell premium_unleaded (empty cell)."""
    result = _parse_table(_TABLE_HTML)
    assert result["Shell"].get("premium_unleaded") is None


def test_parse_table_circle_k_premium_unleaded() -> None:
    """_parse_table parses Circle K premium_unleaded price correctly."""
    result = _parse_table(_TABLE_HTML)
    assert result["Circle K"]["premium_unleaded"] == pytest.approx(14.75)


def test_parse_table_circle_k_premium_diesel() -> None:
    """_parse_table parses Circle K premium_diesel price correctly."""
    result = _parse_table(_TABLE_HTML)
    assert result["Circle K"]["premium_diesel"] == pytest.approx(14.50)


def test_parse_table_no_table_raises_provider_error() -> None:
    """_parse_table raises ProviderError when no table exists in HTML."""
    with pytest.raises(ProviderError, match="No table found"):
        _parse_table(_NO_TABLE_HTML)


def test_parse_table_header_only_raises_provider_error() -> None:
    """_parse_table raises ProviderError when table has only a header row."""
    with pytest.raises(ProviderError):
        _parse_table(_HEADER_ONLY_HTML)


def test_parse_table_bad_header_raises_provider_error() -> None:
    """_parse_table raises ProviderError when first column is not Benzinselskab."""
    with pytest.raises(ProviderError, match="Unexpected table header"):
        _parse_table(_BAD_HEADER_HTML)


def test_parse_table_period_decimal_separator() -> None:
    """_parse_table handles period decimal separators correctly."""
    result = _parse_table(_PERIOD_DECIMAL_HTML)
    assert result["OK"]["unleaded"] == pytest.approx(14.20)
    assert result["OK"]["diesel"] == pytest.approx(13.85)


def test_parse_table_unmapped_columns_excluded() -> None:
    """_parse_table does not include unmapped fuel columns (e.g. Blyfri 92)."""
    result = _parse_table(_TABLE_HTML)
    # 'Blyfri 92' is not in _COLUMN_MAP so should not appear in prices
    for brand_prices in result.values():
        assert "blyfri_92" not in brand_prices
        assert "Blyfri 92" not in brand_prices


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a StationData dict for a known brand."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert isinstance(data, dict)


async def test_async_fetch_unleaded_price_dkk() -> None:
    """async_fetch returns unleaded price in DKK/litre."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert data["unleaded"] == pytest.approx(14.13)


async def test_async_fetch_diesel_price_dkk() -> None:
    """async_fetch returns diesel price in DKK/litre."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert data["diesel"] == pytest.approx(13.89)


async def test_async_fetch_premium_unleaded_price() -> None:
    """async_fetch returns premium_unleaded price when available."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert data["premium_unleaded"] == pytest.approx(14.75)


async def test_async_fetch_premium_diesel_price() -> None:
    """async_fetch returns premium_diesel price when available."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert data["premium_diesel"] == pytest.approx(14.50)


async def test_async_fetch_name_is_brand() -> None:
    """async_fetch returns the brand name in the 'name' and 'brand' fields."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Q8")
    data = await provider.async_fetch(session, "Q8")
    assert data["name"] == "Q8"
    assert data["brand"] == "Q8"


async def test_async_fetch_source_station_id() -> None:
    """async_fetch sets source_station_id to the brand name."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Shell")
    data = await provider.async_fetch(session, "Shell")
    assert data["source_station_id"] == "Shell"


async def test_async_fetch_null_premium_when_empty_cell() -> None:
    """async_fetch returns None for premium_unleaded when cell is empty."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Shell")
    data = await provider.async_fetch(session, "Shell")
    assert data.get("premium_unleaded") is None


async def test_async_fetch_lastupdated_none() -> None:
    """async_fetch returns lastupdated=None (listprices.php has no timestamp)."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    assert data.get("lastupdated") is None


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch returns dict containing all non-sentinel CAPABILITIES keys."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    data = await provider.async_fetch(session, "Circle K")
    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    for key in DkFuelFinderProvider.CAPABILITIES - sentinel_keys:
        assert key in data, f"Key '{key}' missing from async_fetch output"


async def test_async_fetch_case_insensitive_lookup() -> None:
    """async_fetch finds brand with case-insensitive lookup."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("circle k")
    # "circle k" != "Circle K" — case-insensitive fallback should find it.
    data = await provider.async_fetch(session, "circle k")
    assert data["diesel"] == pytest.approx(13.89)


async def test_async_fetch_fetches_correct_url() -> None:
    """async_fetch fetches _LISTPRICES_URL."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Q8")
    await provider.async_fetch(session, "Q8")
    url_arg = (
        session.get.call_args.args[0]
        if session.get.call_args.args
        else session.get.call_args.kwargs.get("url")
    )
    assert url_arg == _LISTPRICES_URL


async def test_async_fetch_sends_browser_user_agent() -> None:
    """async_fetch passes a browser-like User-Agent header."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Q8")
    await provider.async_fetch(session, "Q8")
    call_kwargs = session.get.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    ua = headers.get("User-Agent", "")
    assert "Mozilla" in ua or "Chrome" in ua


# ---------------------------------------------------------------------------
# async_fetch — brand not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_brand_not_found() -> None:
    """async_fetch raises ProviderError when the brand is absent from the table."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("NonexistentBrand")
    with pytest.raises(ProviderError, match="NonexistentBrand"):
        await provider.async_fetch(session, "NonexistentBrand")


async def test_async_fetch_provider_error_lists_available_brands() -> None:
    """ProviderError message lists available brands for debugging."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Unknown")
    with pytest.raises(ProviderError) as exc_info:
        await provider.async_fetch(session, "Unknown")
    error_message = str(exc_info.value)
    # Should mention at least one available brand
    assert (
        "Circle K" in error_message or "Q8" in error_message or "Shell" in error_message
    )


# ---------------------------------------------------------------------------
# async_fetch — HTTP / network error propagation
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """ClientError from session.get() propagates out of async_fetch."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network failure"))
    provider = DkFuelFinderProvider("Circle K")
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "Circle K")


async def test_async_fetch_propagates_raise_for_status_error() -> None:
    """Non-2xx response raises via raise_for_status."""
    resp = _make_mock_response(500, "Internal Server Error")
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "Circle K")


async def test_async_fetch_propagates_454_waf_error() -> None:
    """HTTP 454 (WAF block) is surfaced via raise_for_status."""
    resp = _make_mock_response(454, "Security Incident Detected")
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("454 Security Incident Detected")
    )
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "Circle K")


# ---------------------------------------------------------------------------
# async_fetch_station_name — success and failure
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns the brand name when it exists."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    name = await provider.async_fetch_station_name(session, "Circle K")
    assert name == "Circle K"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when brand is absent from table."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("UnknownBrand")
    name = await provider.async_fetch_station_name(session, "UnknownBrand")
    assert name is None


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = DkFuelFinderProvider("Q8")
    name = await provider.async_fetch_station_name(session, "Q8")
    assert name is None


async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None on an unexpected exception."""
    session = MagicMock()
    session.get = MagicMock(side_effect=RuntimeError("unexpected"))
    provider = DkFuelFinderProvider("Q8")
    name = await provider.async_fetch_station_name(session, "Q8")
    assert name is None


async def test_async_fetch_station_name_case_insensitive() -> None:
    """async_fetch_station_name finds the brand with case-insensitive lookup."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("circle k")
    name = await provider.async_fetch_station_name(session, "circle k")
    # Should return the canonical brand name from the table.
    assert name == "Circle K"


# ---------------------------------------------------------------------------
# async_list_stations — results, sorting, and error handling
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_all_brands() -> None:
    """async_list_stations returns all brands from the table."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    brand_ids = [r[0] for r in results]
    assert "Circle K" in brand_ids
    assert "Q8" in brand_ids
    assert "Shell" in brand_ids


async def test_async_list_stations_returns_id_label_tuples() -> None:
    """async_list_stations returns (brand_name, label) tuples."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    assert len(results) > 0
    brand, label = results[0]
    assert isinstance(brand, str)
    assert isinstance(label, str)
    assert len(label) > 0


async def test_async_list_stations_label_includes_price() -> None:
    """async_list_stations labels include price information for priced brands."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    priced = {brand: label for brand, label in results if "DKK" in label}
    assert len(priced) > 0


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts brands cheapest-first by best available price."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    # Q8 diesel=13.79, Circle K diesel=13.89 → Q8 should appear first
    assert len(results) >= 2
    brand_ids = [r[0] for r in results]
    q8_idx = brand_ids.index("Q8")
    circk_idx = brand_ids.index("Circle K")
    assert q8_idx < circk_idx


async def test_async_list_stations_no_price_brand_appended_last() -> None:
    """async_list_stations appends brands with no recognised prices at the end."""
    # Minimal HTML: one brand with prices, one without.
    html = """\
<html><body><table>
  <tr>
    <th>Benzinselskab</th>
    <th>Blyfri 95 (E10)</th>
    <th>Diesel (B7)</th>
  </tr>
  <tr><td>Priced</td><td>14,00</td><td>13,50</td></tr>
  <tr><td>NoPrices</td><td></td><td></td></tr>
</table></body></html>
"""
    resp = _make_mock_response(200, html)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Priced")
    results = await provider.async_list_stations(session, lat=55.67, lng=12.57)
    brand_ids = [r[0] for r in results]
    priced_idx = brand_ids.index("Priced")
    no_price_idx = brand_ids.index("NoPrices")
    assert priced_idx < no_price_idx


async def test_async_list_stations_returns_empty_on_network_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network failure"))
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    assert results == []


async def test_async_list_stations_returns_empty_on_parse_error() -> None:
    """async_list_stations returns [] when the response HTML has no table."""
    resp = _make_mock_response(200, _NO_TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    results = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=10.0
    )
    assert results == []


async def test_async_list_stations_works_without_lat_lng_kwargs() -> None:
    """async_list_stations works when no lat/lng/radius kwargs are provided."""
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K", latitude=55.67, longitude=12.57)
    results = await provider.async_list_stations(session)
    assert len(results) > 0


async def test_async_list_stations_returns_all_brands_regardless_of_location() -> None:
    """async_list_stations returns all brands regardless of lat/lng/radius.

    fuelfinder.dk has no per-station GPS data, so location filtering is not
    applied and all brands are returned no matter what coordinates are provided.
    """
    resp = _make_mock_response(200, _TABLE_HTML)
    session = _make_session(resp)
    provider = DkFuelFinderProvider("Circle K")
    # Use bogus coordinates far from Denmark
    results_dk = await provider.async_list_stations(
        session, lat=55.67, lng=12.57, radius_km=5.0
    )

    resp2 = _make_mock_response(200, _TABLE_HTML)
    session2 = _make_session(resp2)
    results_other = await provider.async_list_stations(
        session2, lat=-33.87, lng=151.21, radius_km=5.0
    )

    # Both should return the same number of brands
    assert len(results_dk) == len(results_other)


# ---------------------------------------------------------------------------
# New tests targeting uncovered lines 153, 234, 237, 452
# ---------------------------------------------------------------------------


def test_table_parser_done_flag_ignores_second_table() -> None:
    """handle_starttag returns early (line 153) once _done is set after the first table closes."""
    html = """\
<html><body>
<table>
  <tr><th>Benzinselskab</th><th>Blyfri 95 (E10)</th></tr>
  <tr><td>Circle K</td><td>14,13</td></tr>
</table>
<table>
  <tr><th>Benzinselskab</th><th>Blyfri 95 (E10)</th></tr>
  <tr><td>ShouldNotAppear</td><td>99,99</td></tr>
</table>
</body></html>
"""
    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows
    brands = [r[0] for r in rows[1:]]
    assert "Circle K" in brands
    assert "ShouldNotAppear" not in brands


def test_parse_table_skips_empty_row() -> None:
    """_parse_table continues past an empty row (line 234) without raising."""
    from unittest.mock import patch

    fake_parser = MagicMock()
    fake_parser.rows = [
        ["Benzinselskab", "Blyfri 95 (E10)", "Diesel (B7)"],
        [],  # empty row — triggers line 234
        ["Circle K", "14,13", "13,89"],
    ]
    with patch(
        "custom_components.fuelcompare_ie.providers.dk_fuelfinder._TableParser",
        return_value=fake_parser,
    ):
        result = _parse_table("<ignored/>")
    assert "Circle K" in result


def test_parse_table_skips_blank_brand_row() -> None:
    """_parse_table continues past a row with a blank brand cell (line 237) without raising."""
    from unittest.mock import patch

    fake_parser = MagicMock()
    fake_parser.rows = [
        ["Benzinselskab", "Blyfri 95 (E10)", "Diesel (B7)"],
        ["   ", "14,00", "13,50"],  # whitespace-only brand — triggers line 237
        ["Q8", "14,05", "13,79"],
    ]
    with patch(
        "custom_components.fuelcompare_ie.providers.dk_fuelfinder._TableParser",
        return_value=fake_parser,
    ):
        result = _parse_table("<ignored/>")
    assert "Q8" in result
    assert "" not in result
    assert "   " not in result


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_list_when_brand_table_empty() -> None:
    """async_list_stations returns [] when _fetch_table returns {} (line 452)."""
    from unittest.mock import patch

    provider = DkFuelFinderProvider("Circle K")
    session = MagicMock()
    with patch.object(provider, "_fetch_table", new=AsyncMock(return_value={})):
        results = await provider.async_list_stations(
            session, lat=55.67, lng=12.57, radius_km=10.0
        )
    assert results == []
