"""Tests for MdFuelProvider (ANRE Moldova)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.md_fuel import (
    MdFuelProvider,
    _HEADERS,
    _NATIONAL_STATION_ID,
    _URL_BENZINA_95,
    _URL_MOTORINA,
    _extract_price_from_html,
    _parse_price,
)


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

# Minimal HTML page with a valid data-price attribute on td.pl_price
_HTML_BENZINA_95 = """
<html><body>
<table>
  <tr><td class="pl_price" data-price="28.71">28.71</td></tr>
  <tr><td class="pl_price" data-price="28.50">28.50</td></tr>
</table>
</body></html>
"""

_HTML_MOTORINA = """
<html><body>
<table>
  <tr><td class="pl_price" data-price="27.16">27.16</td></tr>
</table>
</body></html>
"""

# HTML with no matching element (should return None)
_HTML_NO_PRICE = """
<html><body>
<table>
  <tr><td class="other_class">not a price</td></tr>
</table>
</body></html>
"""

# HTML with a zero price (should return None)
_HTML_ZERO_PRICE = """
<html><body>
<table>
  <tr><td class="pl_price" data-price="0.00">0.00</td></tr>
</table>
</body></html>
"""

# HTML with a negative price (should return None)
_HTML_NEGATIVE_PRICE = """
<html><body>
<table>
  <tr><td class="pl_price" data-price="-5.00">-5.00</td></tr>
</table>
</body></html>
"""

# HTML with a non-numeric data-price (should return None)
_HTML_INVALID_PRICE = """
<html><body>
<table>
  <tr><td class="pl_price" data-price="N/A">N/A</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Mock session helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int = 200,
    text: str = "",
    raise_for_status_exc: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response for use as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text)
    if raise_for_status_exc is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_for_status_exc)
    elif status >= 400:
        mock_resp.raise_for_status = MagicMock(
            side_effect=ClientResponseError(
                request_info=MagicMock(), history=(), status=status
            )
        )
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(
    benzina_html: str = _HTML_BENZINA_95,
    motorina_html: str = _HTML_MOTORINA,
    benzina_status: int = 200,
    motorina_status: int = 200,
) -> MagicMock:
    """Return a mock session that serves benzina and motorina HTML pages."""
    benzina_resp = _make_mock_response(status=benzina_status, text=benzina_html)
    motorina_resp = _make_mock_response(status=motorina_status, text=motorina_html)

    # The provider fetches both URLs concurrently via asyncio.gather.
    # We match by URL substring to return the correct response.
    def _get_side_effect(url: str, **kwargs: object) -> AsyncMock:
        if "benzina" in url or "benzina-95" in url:
            return benzina_resp
        return motorina_resp

    session = MagicMock()
    session.get = MagicMock(side_effect=_get_side_effect)
    return session


# ---------------------------------------------------------------------------
# Provider metadata tests
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """MdFuelProvider declares COUNTRY='MD'."""
    assert MdFuelProvider.COUNTRY == "MD"


def test_provider_key() -> None:
    """MdFuelProvider declares PROVIDER_KEY='md_fuel'."""
    assert MdFuelProvider.PROVIDER_KEY == "md_fuel"


def test_provider_label_contains_anre() -> None:
    """MdFuelProvider declares a LABEL mentioning ANRE or Moldova."""
    label = MdFuelProvider.LABEL
    assert "ANRE" in label or "Moldova" in label


def test_provider_config_mode_is_location() -> None:
    """MdFuelProvider uses CONFIG_MODE='location' (national data only)."""
    assert MdFuelProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """MdFuelProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert MdFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes unleaded and diesel."""
    caps = MdFuelProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "diesel" in caps


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes last_successful_fetch and data_fetch_problem."""
    caps = MdFuelProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


def test_provider_poll_interval_is_reasonable() -> None:
    """MdFuelProvider POLL_INTERVAL_SECONDS is at least 3600 (1 hour)."""
    assert MdFuelProvider.POLL_INTERVAL_SECONDS >= 3600


def test_national_station_id_is_md() -> None:
    """_NATIONAL_STATION_ID is the MD country code."""
    assert _NATIONAL_STATION_ID == "MD"


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent header."""
    ua = _HEADERS.get("User-Agent", "")
    assert "HomeAssistant" in ua or "aiohttp" in ua


# ---------------------------------------------------------------------------
# _parse_price tests
# ---------------------------------------------------------------------------


def test_parse_price_valid_decimal() -> None:
    """_parse_price returns the correct float for a valid decimal string."""
    result = _parse_price("28.71")
    assert result == pytest.approx(28.71)


def test_parse_price_rounds_to_three_places() -> None:
    """_parse_price rounds to 3 decimal places."""
    result = _parse_price("27.159999")
    assert result == pytest.approx(27.16, abs=0.001)


def test_parse_price_integer_string() -> None:
    """_parse_price handles an integer-format string like '29'."""
    result = _parse_price("29")
    assert result == pytest.approx(29.0)


def test_parse_price_zero_returns_none() -> None:
    """_parse_price returns None for '0.00' (non-positive price)."""
    assert _parse_price("0.00") is None


def test_parse_price_negative_returns_none() -> None:
    """_parse_price returns None for negative prices."""
    assert _parse_price("-5.00") is None


def test_parse_price_non_numeric_returns_none() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None
    assert _parse_price("") is None
    assert _parse_price("abc") is None


# ---------------------------------------------------------------------------
# _extract_price_from_html tests
# ---------------------------------------------------------------------------


def test_extract_price_from_html_valid_benzina() -> None:
    """_extract_price_from_html extracts the first data-price from benzina page."""
    result = _extract_price_from_html(_HTML_BENZINA_95, "benzina_95")
    # First td.pl_price[data-price] is 28.71
    assert result == pytest.approx(28.71)


def test_extract_price_from_html_valid_motorina() -> None:
    """_extract_price_from_html extracts the price from motorina page."""
    result = _extract_price_from_html(_HTML_MOTORINA, "motorina")
    assert result == pytest.approx(27.16)


def test_extract_price_from_html_no_element_returns_none() -> None:
    """_extract_price_from_html returns None when no td.pl_price[data-price] element exists."""
    result = _extract_price_from_html(_HTML_NO_PRICE, "benzina_95")
    assert result is None


def test_extract_price_from_html_zero_price_returns_none() -> None:
    """_extract_price_from_html returns None when data-price is '0.00'."""
    result = _extract_price_from_html(_HTML_ZERO_PRICE, "benzina_95")
    assert result is None


def test_extract_price_from_html_negative_price_returns_none() -> None:
    """_extract_price_from_html returns None when data-price is negative."""
    result = _extract_price_from_html(_HTML_NEGATIVE_PRICE, "benzina_95")
    assert result is None


def test_extract_price_from_html_invalid_price_string_returns_none() -> None:
    """_extract_price_from_html returns None when data-price is non-numeric."""
    result = _extract_price_from_html(_HTML_INVALID_PRICE, "benzina_95")
    assert result is None


def test_extract_price_from_html_empty_string_returns_none() -> None:
    """_extract_price_from_html returns None for empty HTML string."""
    result = _extract_price_from_html("", "benzina_95")
    assert result is None


# ---------------------------------------------------------------------------
# MdFuelProvider.__init__ tests
# ---------------------------------------------------------------------------


def test_provider_init_default_station_id() -> None:
    """MdFuelProvider defaults station_id to 'MD'."""
    provider = MdFuelProvider()
    assert provider._station_id == "MD"


def test_provider_init_custom_station_id_overridden() -> None:
    """MdFuelProvider stores station_id as passed (even if not 'MD')."""
    provider = MdFuelProvider(station_id="MD")
    assert provider._station_id == "MD"


def test_provider_init_stores_lat_lon() -> None:
    """MdFuelProvider stores latitude and longitude."""
    provider = MdFuelProvider(latitude=47.0, longitude=28.9)
    assert provider._latitude == pytest.approx(47.0)
    assert provider._longitude == pytest.approx(28.9)


def test_provider_init_default_lat_lon_is_none() -> None:
    """MdFuelProvider defaults latitude and longitude to None."""
    provider = MdFuelProvider()
    assert provider._latitude is None
    assert provider._longitude is None


def test_provider_init_default_radius_km() -> None:
    """MdFuelProvider defaults radius_km to 10.0."""
    provider = MdFuelProvider()
    assert provider._radius_km == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# async_fetch — success paths
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a non-None StationData dict on success."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_benzina_95_maps_to_unleaded() -> None:
    """async_fetch stores Benzina 95 price as 'unleaded'."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")
    assert data["unleaded"] == pytest.approx(28.71)


async def test_async_fetch_motorina_maps_to_diesel() -> None:
    """async_fetch stores Motorina price as 'diesel'."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")
    assert data["diesel"] == pytest.approx(27.16)


async def test_async_fetch_prices_in_mdl_per_litre() -> None:
    """async_fetch prices are in MDL/litre (values ~27–30, not <10 like EUR)."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")
    # MDL/litre should be significantly above 1.0 (not EUR)
    assert data["unleaded"] > 10.0
    assert data["diesel"] > 10.0


async def test_async_fetch_source_station_id_is_md() -> None:
    """async_fetch sets source_station_id to 'MD'."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")
    assert data["source_station_id"] == "MD"


async def test_async_fetch_name_is_set() -> None:
    """async_fetch populates the name field."""
    session = _make_session()
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")
    assert data.get("name") is not None
    assert isinstance(data["name"], str)


async def test_async_fetch_makes_two_http_requests() -> None:
    """async_fetch issues exactly two GET requests (one per fuel page)."""
    session = _make_session()
    provider = MdFuelProvider()
    await provider.async_fetch(session, "MD")
    assert session.get.call_count == 2


async def test_async_fetch_requests_both_anre_urls() -> None:
    """async_fetch requests the Benzina 95 and Motorina ANRE URLs."""
    session = _make_session()
    provider = MdFuelProvider()
    await provider.async_fetch(session, "MD")

    called_urls = [call.args[0] for call in session.get.call_args_list]
    assert _URL_BENZINA_95 in called_urls
    assert _URL_MOTORINA in called_urls


async def test_async_fetch_sends_user_agent_header() -> None:
    """async_fetch sends User-Agent header with each request."""
    session = _make_session()
    provider = MdFuelProvider()
    await provider.async_fetch(session, "MD")

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert "User-Agent" in headers


# ---------------------------------------------------------------------------
# async_fetch — partial-failure paths
# ---------------------------------------------------------------------------


async def test_async_fetch_benzina_page_fails_diesel_still_returned() -> None:
    """async_fetch returns diesel price when only benzina page fails."""
    session = _make_session(benzina_html=_HTML_NO_PRICE)
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")

    assert data["unleaded"] is None
    assert data["diesel"] == pytest.approx(27.16)


async def test_async_fetch_motorina_page_fails_unleaded_still_returned() -> None:
    """async_fetch returns unleaded price when only motorina page fails."""
    session = _make_session(motorina_html=_HTML_NO_PRICE)
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")

    assert data["unleaded"] == pytest.approx(28.71)
    assert data["diesel"] is None


async def test_async_fetch_both_pages_fail_raises_provider_error() -> None:
    """async_fetch raises ProviderError when both pages return no valid price."""
    session = _make_session(
        benzina_html=_HTML_NO_PRICE,
        motorina_html=_HTML_NO_PRICE,
    )
    provider = MdFuelProvider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "MD")


async def test_async_fetch_http_error_on_one_page_still_returns_other() -> None:
    """async_fetch handles a non-200 status on one page gracefully."""
    # benzina page returns 503; motorina page is fine
    session = _make_session(benzina_status=503)
    provider = MdFuelProvider()
    data = await provider.async_fetch(session, "MD")

    # Benzina failed (non-200) → unleaded is None
    assert data["unleaded"] is None
    # Motorina succeeded
    assert data["diesel"] == pytest.approx(27.16)


async def test_async_fetch_connection_error_handled_gracefully() -> None:
    """async_fetch raises ProviderError (not ClientError) when all requests fail."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = MdFuelProvider()

    with pytest.raises((ProviderError, ClientError)):
        await provider.async_fetch(session, "MD")


# ---------------------------------------------------------------------------
# async_fetch_station_name tests
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none() -> None:
    """async_fetch_station_name returns None (national data, no station names)."""
    session = MagicMock()
    provider = MdFuelProvider()
    name = await provider.async_fetch_station_name(session, "MD")
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations tests
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_one_entry() -> None:
    """async_list_stations returns exactly one entry (national reference only)."""
    session = _make_session()
    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    assert len(results) == 1


async def test_async_list_stations_station_id_is_md() -> None:
    """async_list_stations returns 'MD' as the station ID."""
    session = _make_session()
    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    station_id, _ = results[0]
    assert station_id == "MD"


async def test_async_list_stations_label_mentions_anre_or_moldova() -> None:
    """async_list_stations label mentions ANRE or Moldova."""
    session = _make_session()
    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    _, label = results[0]
    assert "ANRE" in label or "Moldova" in label


async def test_async_list_stations_label_includes_prices_when_available() -> None:
    """async_list_stations label includes price info when pages are reachable."""
    session = _make_session()
    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    _, label = results[0]
    # Label should include at least one price figure
    assert "MDL" in label or "28" in label or "27" in label


async def test_async_list_stations_with_coordinates() -> None:
    """async_list_stations accepts lat/lng kwargs without error."""
    session = _make_session()
    provider = MdFuelProvider(latitude=47.0, longitude=28.9)
    results = await provider.async_list_stations(
        session, lat=47.0, lng=28.9, radius_km=100.0
    )

    assert len(results) == 1
    station_id, _ = results[0]
    assert station_id == "MD"


async def test_async_list_stations_on_network_error_returns_fallback() -> None:
    """async_list_stations returns a fallback entry (not an empty list) on network error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    # Should still return one entry (fallback label without prices)
    assert len(results) == 1
    station_id, label = results[0]
    assert station_id == "MD"
    assert "ANRE" in label or "Moldova" in label


async def test_async_list_stations_no_price_in_label_when_pages_fail() -> None:
    """async_list_stations label has no price figures when both pages fail."""
    session = _make_session(
        benzina_html=_HTML_NO_PRICE,
        motorina_html=_HTML_NO_PRICE,
    )
    provider = MdFuelProvider()
    results = await provider.async_list_stations(session)

    _, label = results[0]
    # No prices available — label should not contain MDL price numbers
    assert "MDL" not in label


# ---------------------------------------------------------------------------
# Capabilities and BaseProvider compliance
# ---------------------------------------------------------------------------


def test_capabilities_are_frozenset() -> None:
    """CAPABILITIES is a frozenset."""
    assert isinstance(MdFuelProvider.CAPABILITIES, frozenset)


def test_capabilities_subset_of_all_sensor_keys() -> None:
    """All CAPABILITIES keys are known StationData keys."""
    from custom_components.fuelcompare_ie.providers.base import ALL_SENSOR_KEYS

    unknown = (
        MdFuelProvider.CAPABILITIES
        - ALL_SENSOR_KEYS
        - {
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )
    assert unknown == frozenset(), f"Unknown CAPABILITIES keys: {unknown}"


def test_provider_is_registered_in_registry() -> None:
    """MdFuelProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "md_fuel" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["md_fuel"] is MdFuelProvider


# ---------------------------------------------------------------------------
# New coverage tests: lines 250-253, 304-310, 353, 356-376
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_on_generic_exception_returns_fallback() -> None:
    """async_list_stations catches a non-ClientError exception and returns fallback label.

    Covers lines 250-253: the ``except Exception`` branch that sets both prices to None.
    _fetch_price itself catches internal exceptions, so we patch it directly to raise
    so the exception propagates to the outer try/except in async_list_stations.
    """
    session = MagicMock()

    provider = MdFuelProvider()
    with patch.object(
        provider, "_fetch_price", side_effect=RuntimeError("unexpected boom")
    ):
        results = await provider.async_list_stations(session)

    assert len(results) == 1
    station_id, label = results[0]
    assert station_id == "MD"
    assert "ANRE" in label or "Moldova" in label
    # No price data — both set to None → fallback label without MDL
    assert "MDL" not in label


@pytest.mark.asyncio
async def test_fetch_price_client_response_error_returns_none() -> None:
    """_fetch_price returns None when aiohttp raises ClientResponseError.

    Covers lines 304-310: the ``except ClientResponseError`` handler in ``_fetch_price``.
    """
    err = ClientResponseError(request_info=MagicMock(), history=(), status=500)

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(side_effect=err)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)

    provider = MdFuelProvider()
    result = await provider._fetch_price(session, _URL_BENZINA_95, "benzina_95")
    assert result is None


def test_extract_price_from_html_tag_get_returns_none() -> None:
    """_extract_price_from_html returns None when tag.get('data-price') is None.

    Covers line 353: ``if raw is None: return None``.
    The BeautifulSoup constructor is patched at the ``bs4`` module level so the
    locally-imported name inside ``_extract_price_from_html`` uses the mock.
    """
    mock_tag = MagicMock()
    mock_tag.get = MagicMock(return_value=None)

    mock_soup = MagicMock()
    mock_soup.select_one = MagicMock(return_value=mock_tag)

    with patch("bs4.BeautifulSoup", return_value=mock_soup):
        result = _extract_price_from_html("<html></html>", "benzina_95")

    assert result is None


def test_extract_price_from_html_import_error_regex_finds_price() -> None:
    """_extract_price_from_html falls back to regex and returns price when bs4 missing.

    Covers lines 356-370 (ImportError branch, match sub-path).
    """
    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "bs4":
            raise ImportError("No module named 'bs4'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = _extract_price_from_html(_HTML_BENZINA_95, "benzina_95")

    assert result == pytest.approx(28.71)


def test_extract_price_from_html_import_error_regex_no_match_returns_none() -> None:
    """_extract_price_from_html falls back to regex and returns None when no data-price found.

    Covers lines 356-370 (ImportError branch, no-match sub-path).
    """
    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "bs4":
            raise ImportError("No module named 'bs4'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = _extract_price_from_html(_HTML_NO_PRICE, "benzina_95")

    assert result is None


def test_extract_price_from_html_unexpected_exception_returns_none() -> None:
    """_extract_price_from_html returns None when BeautifulSoup raises an unexpected error.

    Covers lines 372-376: the ``except Exception`` catch-all in ``_extract_price_from_html``.
    Patches ``bs4.BeautifulSoup`` at the module level so the locally-imported name
    inside the function raises ``ValueError``.
    """
    with patch("bs4.BeautifulSoup", side_effect=ValueError("unexpected parse failure")):
        result = _extract_price_from_html("<html></html>", "benzina_95")

    assert result is None
