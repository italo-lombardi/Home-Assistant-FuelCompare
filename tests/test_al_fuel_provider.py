"""Tests for AlFuelProvider (Albania national-average fuel prices)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.al_fuel import (
    AlFuelProvider,
    _COUNTRY_LABEL,
    _FALLBACK_URL,
    _HEADERS,
    _PRIMARY_URL,
    _STATION_ID,
    _build_station_data,
    _extract_price_from_cell,
    _parse_albania_row,
    _strip_tags,
)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_TABLE_HTML = """
<html>
<body>
<table>
  <thead>
    <tr><th>Country</th><th>Gasoline 95</th><th>Diesel</th><th>LPG</th></tr>
  </thead>
  <tbody>
    <tr><td>Albania</td><td>1.809</td><td>1.955</td><td>0.679</td></tr>
    <tr><td>Austria</td><td>1.450</td><td>1.380</td><td>0.850</td></tr>
    <tr><td>Germany</td><td>1.750</td><td>1.620</td><td>0.800</td></tr>
  </tbody>
</table>
</body>
</html>
"""

_TABLE_HTML_NO_LPG = """
<table>
  <tr><th>Country</th><th>Gasoline 95</th><th>Diesel</th></tr>
  <tr><td>Albania</td><td>1.809</td><td>1.955</td></tr>
</table>
"""

_TABLE_HTML_MISSING_ALBANIA = """
<table>
  <tr><th>Country</th><th>Gasoline 95</th><th>Diesel</th><th>LPG</th></tr>
  <tr><td>Austria</td><td>1.450</td><td>1.380</td><td>0.850</td></tr>
</table>
"""

_TABLE_HTML_INVALID_PRICES = """
<table>
  <tr><th>Country</th><th>Gasoline 95</th><th>Diesel</th><th>LPG</th></tr>
  <tr><td>Albania</td><td>N/A</td><td>—</td><td></td></tr>
</table>
"""

_TABLE_HTML_WITH_LINKS = """
<table>
  <tr>
    <td><a href="/albania">Albania</a></td>
    <td><span class="price">1.809</span></td>
    <td><span class="price">1.955</span></td>
    <td><span class="price">0.679</span></td>
  </tr>
</table>
"""


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    text_data: str | None = None,
    raise_on_raise_for_status: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text_data or "")
    if raise_on_raise_for_status is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_on_raise_for_status)
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _default_provider() -> AlFuelProvider:
    """Create an AlFuelProvider with default test parameters."""
    return AlFuelProvider(station_id=_STATION_ID)


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """AlFuelProvider.COUNTRY is 'AL'."""
    assert AlFuelProvider.COUNTRY == "AL"


def test_provider_metadata_provider_key() -> None:
    """AlFuelProvider.PROVIDER_KEY is 'al_fuel'."""
    assert AlFuelProvider.PROVIDER_KEY == "al_fuel"


def test_provider_metadata_label_contains_albania() -> None:
    """AlFuelProvider.LABEL mentions Albania."""
    assert "Albania" in AlFuelProvider.LABEL


def test_provider_metadata_config_mode_is_location() -> None:
    """CONFIG_MODE is 'location'."""
    assert AlFuelProvider.CONFIG_MODE == "location"


def test_provider_metadata_station_lookup_mode_is_location_search() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert AlFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_metadata_poll_interval_is_daily() -> None:
    """Poll interval is 86400 seconds (daily)."""
    assert AlFuelProvider.POLL_INTERVAL_SECONDS == 86400


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_includes_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (gasoline 95 mapped to StationData key)."""
    assert "unleaded" in AlFuelProvider.CAPABILITIES


def test_capabilities_includes_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in AlFuelProvider.CAPABILITIES


def test_capabilities_includes_lpg() -> None:
    """CAPABILITIES includes 'lpg'."""
    assert "lpg" in AlFuelProvider.CAPABILITIES


def test_capabilities_includes_name() -> None:
    """CAPABILITIES includes 'name'."""
    assert "name" in AlFuelProvider.CAPABILITIES


def test_capabilities_includes_county() -> None:
    """CAPABILITIES includes 'county'."""
    assert "county" in AlFuelProvider.CAPABILITIES


def test_capabilities_includes_coordinator_sentinels() -> None:
    """CAPABILITIES includes last_successful_fetch and data_fetch_problem."""
    caps = AlFuelProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id as _station_id."""
    p = AlFuelProvider(station_id="AL")
    assert p._station_id == "AL"


def test_constructor_accepts_lat_lng_for_interface_compat() -> None:
    """Constructor accepts latitude/longitude even though they are not used."""
    p = AlFuelProvider(station_id="AL", latitude=41.33, longitude=19.83)
    assert p._latitude == pytest.approx(41.33)
    assert p._longitude == pytest.approx(19.83)


def test_constructor_default_station_id_is_al() -> None:
    """Default station_id is 'AL'."""
    p = AlFuelProvider()
    assert p._station_id == "AL"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_primary_url_points_to_cargopedia() -> None:
    """_PRIMARY_URL points to cargopedia.net."""
    from urllib.parse import urlparse

    assert urlparse(_PRIMARY_URL).netloc in ("cargopedia.net", "www.cargopedia.net")
    assert _PRIMARY_URL.startswith("https://")


def test_fallback_url_points_to_tolls_eu() -> None:
    """_FALLBACK_URL points to tolls.eu."""
    from urllib.parse import urlparse

    assert urlparse(_FALLBACK_URL).netloc in ("tolls.eu", "www.tolls.eu")
    assert _FALLBACK_URL.startswith("https://")


def test_station_id_constant_is_al() -> None:
    """_STATION_ID module constant is 'AL'."""
    assert _STATION_ID == "AL"


def test_country_label_is_albania() -> None:
    """_COUNTRY_LABEL is 'Albania'."""
    assert _COUNTRY_LABEL == "Albania"


def test_headers_include_user_agent() -> None:
    """_HEADERS contains a User-Agent header."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


# ---------------------------------------------------------------------------
# _strip_tags — unit tests
# ---------------------------------------------------------------------------


def test_strip_tags_removes_simple_tag() -> None:
    """_strip_tags removes a simple tag."""
    assert "hello" in _strip_tags("<b>hello</b>")


def test_strip_tags_collapses_whitespace() -> None:
    """_strip_tags collapses multiple spaces into one."""
    result = _strip_tags("  foo   bar  ")
    assert "foo bar" in result


def test_strip_tags_removes_anchor_tag() -> None:
    """_strip_tags removes anchor tag, leaving link text."""
    result = _strip_tags('<a href="/albania">Albania</a>')
    assert "Albania" in result
    assert "<a" not in result


def test_strip_tags_empty_string() -> None:
    """_strip_tags handles empty string."""
    assert _strip_tags("") == ""


# ---------------------------------------------------------------------------
# _extract_price_from_cell — unit tests
# ---------------------------------------------------------------------------


def test_extract_price_normal_value() -> None:
    """_extract_price_from_cell returns float for normal price cell."""
    assert _extract_price_from_cell("<td>1.809</td>") == pytest.approx(1.809)


def test_extract_price_rounds_to_3dp() -> None:
    """_extract_price_from_cell rounds to 3 decimal places."""
    result = _extract_price_from_cell("<td>1.8095</td>")
    assert result == pytest.approx(1.810, abs=0.0005)


def test_extract_price_from_span_wrapped_value() -> None:
    """_extract_price_from_cell extracts price from nested span tag."""
    result = _extract_price_from_cell('<td><span class="p">1.955</span></td>')
    assert result == pytest.approx(1.955)


def test_extract_price_returns_none_for_dash() -> None:
    """_extract_price_from_cell returns None for '—' placeholder."""
    assert _extract_price_from_cell("<td>—</td>") is None


def test_extract_price_returns_none_for_na() -> None:
    """_extract_price_from_cell returns None for 'N/A' text."""
    assert _extract_price_from_cell("<td>N/A</td>") is None


def test_extract_price_returns_none_for_empty_cell() -> None:
    """_extract_price_from_cell returns None for empty cell."""
    assert _extract_price_from_cell("<td></td>") is None


def test_extract_price_rejects_value_above_10() -> None:
    """_extract_price_from_cell returns None for implausible prices > 10."""
    assert _extract_price_from_cell("<td>99.99</td>") is None


def test_extract_price_returns_none_for_zero() -> None:
    """_extract_price_from_cell returns None for 0.000."""
    assert _extract_price_from_cell("<td>0.000</td>") is None


def test_extract_price_lpg_typical_value() -> None:
    """_extract_price_from_cell handles LPG typical price 0.679."""
    assert _extract_price_from_cell("<td>0.679</td>") == pytest.approx(0.679)


# ---------------------------------------------------------------------------
# _parse_albania_row — unit tests
# ---------------------------------------------------------------------------


def test_parse_albania_row_returns_correct_prices() -> None:
    """_parse_albania_row extracts correct prices from standard table."""
    result = _parse_albania_row(_TABLE_HTML)
    assert result is not None
    assert result["unleaded"] == pytest.approx(1.809)
    assert result["diesel"] == pytest.approx(1.955)
    assert result["lpg"] == pytest.approx(0.679)


def test_parse_albania_row_returns_none_when_albania_absent() -> None:
    """_parse_albania_row returns None when Albania row is not in the table."""
    result = _parse_albania_row(_TABLE_HTML_MISSING_ALBANIA)
    assert result is None


def test_parse_albania_row_handles_no_lpg_column() -> None:
    """_parse_albania_row returns lpg=None when no LPG column is present."""
    result = _parse_albania_row(_TABLE_HTML_NO_LPG)
    assert result is not None
    assert result["unleaded"] == pytest.approx(1.809)
    assert result["diesel"] == pytest.approx(1.955)
    assert result["lpg"] is None


def test_parse_albania_row_handles_invalid_prices() -> None:
    """_parse_albania_row returns None prices when cells contain non-numeric text."""
    result = _parse_albania_row(_TABLE_HTML_INVALID_PRICES)
    assert result is not None
    assert result["unleaded"] is None
    assert result["diesel"] is None
    assert result["lpg"] is None


def test_parse_albania_row_handles_linked_country_name() -> None:
    """_parse_albania_row finds Albania row when country name is inside an <a> tag."""
    result = _parse_albania_row(_TABLE_HTML_WITH_LINKS)
    assert result is not None
    assert result["unleaded"] == pytest.approx(1.809)
    assert result["diesel"] == pytest.approx(1.955)
    assert result["lpg"] == pytest.approx(0.679)


def test_parse_albania_row_does_not_match_non_albania_rows() -> None:
    """_parse_albania_row does not return data for 'Austria' or other countries."""
    result = _parse_albania_row(_TABLE_HTML)
    assert result is not None
    # Albania values — should not get Austria's values (1.450 / 1.380)
    assert result["unleaded"] != pytest.approx(1.450)
    assert result["diesel"] != pytest.approx(1.380)


def test_parse_albania_row_empty_html_returns_none() -> None:
    """_parse_albania_row returns None for empty HTML string."""
    assert _parse_albania_row("") is None


# ---------------------------------------------------------------------------
# _build_station_data — unit tests
# ---------------------------------------------------------------------------


def test_build_station_data_sets_unleaded() -> None:
    """_build_station_data maps gasoline_95 to 'unleaded' key."""
    data = _build_station_data({"unleaded": 1.809, "diesel": 1.955, "lpg": 0.679})
    assert data["unleaded"] == pytest.approx(1.809)


def test_build_station_data_sets_diesel() -> None:
    """_build_station_data sets diesel price."""
    data = _build_station_data({"unleaded": 1.809, "diesel": 1.955, "lpg": 0.679})
    assert data["diesel"] == pytest.approx(1.955)


def test_build_station_data_sets_lpg() -> None:
    """_build_station_data sets lpg price."""
    data = _build_station_data({"unleaded": 1.809, "diesel": 1.955, "lpg": 0.679})
    assert data["lpg"] == pytest.approx(0.679)


def test_build_station_data_name_is_albania() -> None:
    """_build_station_data sets name to 'Albania'."""
    data = _build_station_data({"unleaded": None, "diesel": None, "lpg": None})
    assert data["name"] == "Albania"


def test_build_station_data_county_is_albania() -> None:
    """_build_station_data sets county to 'Albania'."""
    data = _build_station_data({"unleaded": None, "diesel": None, "lpg": None})
    assert data["county"] == "Albania"


def test_build_station_data_lastupdated_is_none() -> None:
    """_build_station_data sets lastupdated=None (no timestamp from scraping)."""
    data = _build_station_data({"unleaded": 1.809, "diesel": 1.955, "lpg": 0.679})
    assert data["lastupdated"] is None


def test_build_station_data_source_station_id_is_al() -> None:
    """_build_station_data sets source_station_id to 'AL'."""
    data = _build_station_data({"unleaded": 1.809, "diesel": 1.955, "lpg": 0.679})
    assert data["source_station_id"] == "AL"


def test_build_station_data_none_prices_propagate() -> None:
    """_build_station_data propagates None prices without error."""
    data = _build_station_data({"unleaded": None, "diesel": None, "lpg": None})
    assert data["unleaded"] is None
    assert data["diesel"] is None
    assert data["lpg"] is None


# ---------------------------------------------------------------------------
# async_fetch — success path (primary source)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_success_primary_returns_station_data() -> None:
    """async_fetch returns populated StationData on primary source success."""
    resp = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.809)
    assert data["diesel"] == pytest.approx(1.955)
    assert data["lpg"] == pytest.approx(0.679)


@pytest.mark.asyncio
async def test_async_fetch_success_populates_name() -> None:
    """async_fetch returns name='Albania'."""
    resp = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "Albania"


@pytest.mark.asyncio
async def test_async_fetch_success_populates_county() -> None:
    """async_fetch returns county='Albania'."""
    resp = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["county"] == "Albania"


@pytest.mark.asyncio
async def test_async_fetch_source_station_id_is_al() -> None:
    """async_fetch returns source_station_id='AL'."""
    resp = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["source_station_id"] == "AL"


@pytest.mark.asyncio
async def test_async_fetch_lastupdated_is_none() -> None:
    """async_fetch returns lastupdated=None (no timestamp available from scraping)."""
    resp = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] is None


# ---------------------------------------------------------------------------
# async_fetch — fallback behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_falls_back_to_secondary_on_primary_failure() -> None:
    """async_fetch uses fallback URL when primary returns HTTP error."""
    primary_err = _make_mock_response(
        503, raise_on_raise_for_status=ClientError("service unavailable")
    )
    fallback_ok = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(primary_err, fallback_ok)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.809)
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_async_fetch_falls_back_when_primary_has_no_albania_row() -> None:
    """async_fetch tries fallback when primary page has no Albania row."""
    primary_no_albania = _make_mock_response(200, text_data=_TABLE_HTML_MISSING_ALBANIA)
    fallback_ok = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(primary_no_albania, fallback_ok)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.955)
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_when_both_sources_fail() -> None:
    """async_fetch raises ProviderError when both primary and fallback fail."""
    primary_err = _make_mock_response(
        503, raise_on_raise_for_status=ClientError("unavailable")
    )
    fallback_err = _make_mock_response(
        503, raise_on_raise_for_status=ClientError("unavailable")
    )
    session = _make_session(primary_err, fallback_err)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_when_both_have_no_albania() -> None:
    """async_fetch raises ProviderError when Albania row absent from both sources."""
    primary_resp = _make_mock_response(200, text_data=_TABLE_HTML_MISSING_ALBANIA)
    fallback_resp = _make_mock_response(200, text_data=_TABLE_HTML_MISSING_ALBANIA)
    session = _make_session(primary_resp, fallback_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_404_on_primary_tries_fallback() -> None:
    """async_fetch treats HTTP 404 on primary as a failure and tries fallback."""
    primary_404 = _make_mock_response(404)
    fallback_ok = _make_mock_response(200, text_data=_TABLE_HTML)
    session = _make_session(primary_404, fallback_ok)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.809)


@pytest.mark.asyncio
async def test_async_fetch_network_error_falls_back() -> None:
    """async_fetch handles a network-level exception on primary and tries fallback."""
    session = MagicMock()
    call_count = 0

    def _get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ClientError("connection refused")
        return _make_mock_response(200, text_data=_TABLE_HTML)

    session.get = MagicMock(side_effect=_get)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.955)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_albania() -> None:
    """async_fetch_station_name returns 'Albania' without making HTTP requests."""
    session = MagicMock()
    provider = _default_provider()

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Albania"


@pytest.mark.asyncio
async def test_async_fetch_station_name_makes_no_requests() -> None:
    """async_fetch_station_name makes no HTTP requests."""
    session = MagicMock()
    provider = _default_provider()

    await provider.async_fetch_station_name(session, _STATION_ID)

    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_single_entry() -> None:
    """async_list_stations returns exactly one entry (national average)."""
    session = MagicMock()
    provider = _default_provider()

    result = await provider.async_list_stations(session)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_async_list_stations_station_id_is_al() -> None:
    """async_list_stations returns station_id 'AL'."""
    session = MagicMock()
    provider = _default_provider()

    result = await provider.async_list_stations(session)
    station_id, _label = result[0]

    assert station_id == "AL"


@pytest.mark.asyncio
async def test_async_list_stations_label_contains_albania() -> None:
    """async_list_stations label mentions Albania."""
    session = MagicMock()
    provider = _default_provider()

    result = await provider.async_list_stations(session)
    _sid, label = result[0]

    assert "Albania" in label


@pytest.mark.asyncio
async def test_async_list_stations_accepts_lat_lng_kwargs() -> None:
    """async_list_stations accepts lat/lng kwargs without error (is-not-None check)."""
    session = MagicMock()
    provider = _default_provider()

    # lat=0.0 and lng=0.0 are valid coordinates (not falsy in the is-not-None check)
    result = await provider.async_list_stations(session, lat=0.0, lng=0.0)

    assert len(result) == 1
    assert result[0][0] == "AL"


@pytest.mark.asyncio
async def test_async_list_stations_accepts_none_coords() -> None:
    """async_list_stations handles None lat/lng kwargs gracefully."""
    session = MagicMock()
    provider = _default_provider()

    result = await provider.async_list_stations(session, lat=None, lng=None)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_async_list_stations_makes_no_requests() -> None:
    """async_list_stations makes no HTTP requests (no station lookup needed)."""
    session = MagicMock()
    provider = _default_provider()

    await provider.async_list_stations(session, lat=41.33, lng=19.83)

    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_html — internal helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_html_returns_text_on_200() -> None:
    """_fetch_html returns HTML string on HTTP 200."""
    resp = _make_mock_response(200, text_data="<html>some content</html>")
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider._fetch_html(session, _PRIMARY_URL)

    assert result == "<html>some content</html>"


@pytest.mark.asyncio
async def test_fetch_html_returns_none_on_404() -> None:
    """_fetch_html returns None for HTTP 404 without raising."""
    resp = _make_mock_response(404)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider._fetch_html(session, _PRIMARY_URL)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_html_returns_none_on_http_error() -> None:
    """_fetch_html returns None for HTTP 5xx error without raising."""
    resp = _make_mock_response(
        500, raise_on_raise_for_status=ClientError("server error")
    )
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider._fetch_html(session, _PRIMARY_URL)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_html_returns_none_on_network_exception() -> None:
    """_fetch_html returns None when session.get raises a network exception."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection timeout"))

    provider = _default_provider()
    result = await provider._fetch_html(session, _PRIMARY_URL)

    assert result is None


# ---------------------------------------------------------------------------
# Integration: provider registered in PROVIDER_REGISTRY
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """AlFuelProvider is registered in the PROVIDER_REGISTRY under 'al_fuel'."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "al_fuel" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["al_fuel"] is AlFuelProvider
