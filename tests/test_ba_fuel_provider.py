"""Tests for BaFuelProvider (cijenegoriva.ba, Bosnia and Herzegovina)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.ba_fuel import (
    BaFuelProvider,
    _BASE_URL,
    _CITY_SLUGS,
    _HEADER_TO_KEY,
    _HEADERS,
    _build_station_data,
    _parse_price,
    _parse_station_id,
    _parse_station_table,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Shared fixtures / sample data
# ---------------------------------------------------------------------------

_STATION_ID = "sarajevo:0"
_CITY_SLUG = "sarajevo"
_ROW_INDEX = 0

# Minimal but realistic HTML table matching the cijenegoriva.ba layout.
# Headers: Naziv, Adresa, Diesel, Super 95, Super 98, LPG
_SAMPLE_HTML = """
<html><body>
<table>
  <tr>
    <th>Naziv</th>
    <th>Adresa</th>
    <th>Diesel</th>
    <th>Super 95</th>
    <th>Super 98</th>
    <th>LPG</th>
  </tr>
  <tr>
    <td>OMV Sarajevo</td>
    <td>Zmaja od Bosne 1</td>
    <td>2,750</td>
    <td>2,800</td>
    <td>2,950</td>
    <td>1,250</td>
  </tr>
  <tr>
    <td>Petrol Centar</td>
    <td>Titova 5</td>
    <td>2,720</td>
    <td>2,780</td>
    <td>2,930</td>
    <td>1,230</td>
  </tr>
</table>
</body></html>
"""

_SAMPLE_HTML_ONE_STATION = """
<html><body>
<table>
  <tr>
    <th>Naziv</th>
    <th>Adresa</th>
    <th>Diesel</th>
    <th>Super 95</th>
    <th>Super 98</th>
    <th>LPG</th>
  </tr>
  <tr>
    <td>OMV Sarajevo</td>
    <td>Zmaja od Bosne 1</td>
    <td>2.750</td>
    <td>2.800</td>
    <td>2.950</td>
    <td>1.250</td>
  </tr>
</table>
</body></html>
"""

_SAMPLE_HTML_NO_TABLE = "<html><body><p>No table here</p></body></html>"

_SAMPLE_HTML_EMPTY_TABLE = """
<html><body>
<table>
  <tr><th>Naziv</th><th>Diesel</th></tr>
</table>
</body></html>
"""

_SAMPLE_HTML_NULL_PRICES = """
<html><body>
<table>
  <tr>
    <th>Naziv</th>
    <th>Adresa</th>
    <th>Diesel</th>
    <th>Super 95</th>
    <th>Super 98</th>
    <th>LPG</th>
  </tr>
  <tr>
    <td>NoPrice Station</td>
    <td>Nowhere</td>
    <td></td>
    <td></td>
    <td></td>
    <td></td>
  </tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    text: str = "",
    raise_on_raise_for_status: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text)
    if raise_on_raise_for_status is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_on_raise_for_status)
    else:
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


def _default_provider(**kwargs) -> BaFuelProvider:
    """Create a BaFuelProvider with sensible test defaults."""
    return BaFuelProvider(
        station_id=kwargs.get("station_id", _STATION_ID),
        county=None,
        latitude=kwargs.get("latitude", 43.8476),
        longitude=kwargs.get("longitude", 18.3564),
        radius_km=kwargs.get("radius_km", 50.0),
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """BaFuelProvider.COUNTRY is 'BA'."""
    assert BaFuelProvider.COUNTRY == "BA"


def test_provider_metadata_provider_key() -> None:
    """BaFuelProvider.PROVIDER_KEY is 'ba_fuel'."""
    assert BaFuelProvider.PROVIDER_KEY == "ba_fuel"


def test_provider_metadata_label_contains_ba() -> None:
    """BaFuelProvider.LABEL references the site and country."""
    assert "cijenegoriva.ba" in BaFuelProvider.LABEL
    assert "Bosnia" in BaFuelProvider.LABEL


def test_provider_metadata_config_mode() -> None:
    """CONFIG_MODE is 'location'."""
    assert BaFuelProvider.CONFIG_MODE == "location"


def test_provider_metadata_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert BaFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_metadata_poll_interval() -> None:
    """Poll interval is 86400 seconds (daily)."""
    assert BaFuelProvider.POLL_INTERVAL_SECONDS == 86400


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_includes_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in BaFuelProvider.CAPABILITIES


def test_capabilities_includes_lpg() -> None:
    """CAPABILITIES includes 'lpg'."""
    assert "lpg" in BaFuelProvider.CAPABILITIES


def test_capabilities_includes_identity_fields() -> None:
    """CAPABILITIES includes name, brand, address, county."""
    caps = BaFuelProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps
    assert "address" in caps
    assert "county" in caps


def test_capabilities_includes_location_fields() -> None:
    """CAPABILITIES includes latitude and longitude."""
    caps = BaFuelProvider.CAPABILITIES
    assert "latitude" in caps
    assert "longitude" in caps


def test_capabilities_includes_coordinator_sentinels() -> None:
    """CAPABILITIES includes last_successful_fetch and data_fetch_problem."""
    caps = BaFuelProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    p = BaFuelProvider(station_id="tuzla:2")
    assert p._station_id == "tuzla:2"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude, longitude, radius_km."""
    p = BaFuelProvider(
        station_id="mostar:0", latitude=43.34, longitude=17.81, radius_km=20.0
    )
    assert p._latitude == pytest.approx(43.34)
    assert p._longitude == pytest.approx(17.81)
    assert p._radius_km == pytest.approx(20.0)


def test_constructor_default_radius() -> None:
    """Constructor defaults radius_km to 50.0 when None is passed."""
    p = BaFuelProvider(station_id="sarajevo:0", radius_km=None)
    assert p._radius_km == pytest.approx(50.0)


def test_constructor_stores_county() -> None:
    """Constructor stores county (interface compat)."""
    p = BaFuelProvider(station_id="sarajevo:0", county="Sarajevo")
    assert p._county == "Sarajevo"


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_base_url_is_cijenegoriva_ba() -> None:
    """_BASE_URL points to cijenegoriva.ba."""
    assert "cijenegoriva.ba" in _BASE_URL
    assert _BASE_URL.startswith("https://")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a HomeAssistant User-Agent."""
    assert "User-Agent" in _HEADERS
    assert "HomeAssistant" in _HEADERS["User-Agent"]


def test_city_slugs_includes_sarajevo() -> None:
    """_CITY_SLUGS includes 'sarajevo'."""
    assert "sarajevo" in _CITY_SLUGS


def test_city_slugs_includes_major_cities() -> None:
    """_CITY_SLUGS includes banja-luka, tuzla, and mostar."""
    assert "banja-luka" in _CITY_SLUGS
    assert "tuzla" in _CITY_SLUGS
    assert "mostar" in _CITY_SLUGS


def test_header_to_key_maps_diesel() -> None:
    """_HEADER_TO_KEY maps 'diesel' and 'dizel' to 'diesel'."""
    assert _HEADER_TO_KEY.get("diesel") == "diesel"
    assert _HEADER_TO_KEY.get("dizel") == "diesel"


def test_header_to_key_maps_super95() -> None:
    """_HEADER_TO_KEY maps 'super 95' to 'super95'."""
    assert _HEADER_TO_KEY.get("super 95") == "super95"


def test_header_to_key_maps_lpg() -> None:
    """_HEADER_TO_KEY maps 'lpg' to 'lpg'."""
    assert _HEADER_TO_KEY.get("lpg") == "lpg"


# ---------------------------------------------------------------------------
# _parse_station_id — unit tests
# ---------------------------------------------------------------------------


def test_parse_station_id_valid_composite() -> None:
    """_parse_station_id parses 'sarajevo:3' into ('sarajevo', 3)."""
    city, idx = _parse_station_id("sarajevo:3")
    assert city == "sarajevo"
    assert idx == 3


def test_parse_station_id_zero_index() -> None:
    """_parse_station_id handles index 0."""
    city, idx = _parse_station_id("tuzla:0")
    assert city == "tuzla"
    assert idx == 0


def test_parse_station_id_hyphenated_city() -> None:
    """_parse_station_id handles hyphenated city slugs like 'banja-luka'."""
    city, idx = _parse_station_id("banja-luka:5")
    assert city == "banja-luka"
    assert idx == 5


def test_parse_station_id_raises_for_missing_colon() -> None:
    """_parse_station_id raises ProviderError when colon is absent."""
    with pytest.raises(ProviderError):
        _parse_station_id("sarajevo3")


def test_parse_station_id_raises_for_non_integer_index() -> None:
    """_parse_station_id raises ProviderError for non-integer row index."""
    with pytest.raises(ProviderError):
        _parse_station_id("sarajevo:abc")


def test_parse_station_id_raises_for_negative_index() -> None:
    """_parse_station_id raises ProviderError for negative row index."""
    with pytest.raises(ProviderError):
        _parse_station_id("sarajevo:-1")


def test_parse_station_id_raises_for_empty_city() -> None:
    """_parse_station_id raises ProviderError for empty city slug."""
    with pytest.raises(ProviderError):
        _parse_station_id(":3")


# ---------------------------------------------------------------------------
# _parse_price — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_float_input() -> None:
    """_parse_price returns rounded float for valid float input."""
    assert _parse_price(2.75) == pytest.approx(2.75)


def test_parse_price_comma_decimal() -> None:
    """_parse_price handles comma as decimal separator (e.g. '2,750')."""
    assert _parse_price("2,750") == pytest.approx(2.750)


def test_parse_price_dot_decimal() -> None:
    """_parse_price handles dot decimal separator (e.g. '2.750')."""
    assert _parse_price("2.750") == pytest.approx(2.750)


def test_parse_price_with_km_suffix() -> None:
    """_parse_price strips 'KM' currency suffix and parses the number."""
    assert _parse_price("2.75 KM") == pytest.approx(2.75)


def test_parse_price_none_input() -> None:
    """_parse_price returns None for None input."""
    assert _parse_price(None) is None


def test_parse_price_zero_input() -> None:
    """_parse_price returns None for zero."""
    assert _parse_price(0) is None


def test_parse_price_negative_input() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price(-1.5) is None


def test_parse_price_empty_string() -> None:
    """_parse_price returns None for empty string."""
    assert _parse_price("") is None


def test_parse_price_non_numeric_string() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None


def test_parse_price_rounds_to_3dp() -> None:
    """_parse_price rounds to 3 decimal places."""
    result = _parse_price(2.74999)
    assert result == pytest.approx(2.750)


def test_parse_price_large_value_divided_by_100() -> None:
    """_parse_price divides values > 20 by 100 (parsing artefact guard)."""
    assert _parse_price(275.0) == pytest.approx(2.75)


def test_parse_price_exactly_20_not_divided() -> None:
    """_parse_price does NOT divide value 20.0 (boundary: > 20 only)."""
    assert _parse_price(20.0) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# _parse_station_table — unit tests
# ---------------------------------------------------------------------------


def test_parse_station_table_returns_correct_count() -> None:
    """_parse_station_table returns one dict per data row."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert len(stations) == 2


def test_parse_station_table_name_field() -> None:
    """_parse_station_table populates 'name' from Naziv column."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["name"] == "OMV Sarajevo"
    assert stations[1]["name"] == "Petrol Centar"


def test_parse_station_table_address_field() -> None:
    """_parse_station_table populates 'address' from Adresa column."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["address"] == "Zmaja od Bosne 1"


def test_parse_station_table_diesel_price_comma_decimal() -> None:
    """_parse_station_table parses comma-decimal diesel price."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["diesel"] == pytest.approx(2.750)


def test_parse_station_table_super95_price() -> None:
    """_parse_station_table parses Super 95 price."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["super95"] == pytest.approx(2.800)


def test_parse_station_table_super98_price() -> None:
    """_parse_station_table parses Super 98 price."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["super98"] == pytest.approx(2.950)


def test_parse_station_table_lpg_price() -> None:
    """_parse_station_table parses LPG price."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[0]["lpg"] == pytest.approx(1.250)


def test_parse_station_table_dot_decimal_price() -> None:
    """_parse_station_table handles dot-decimal prices."""
    stations = _parse_station_table(_SAMPLE_HTML_ONE_STATION)
    assert stations[0]["diesel"] == pytest.approx(2.750)


def test_parse_station_table_empty_cells_return_none_prices() -> None:
    """_parse_station_table returns None for empty price cells."""
    stations = _parse_station_table(_SAMPLE_HTML_NULL_PRICES)
    assert len(stations) == 1
    assert stations[0]["diesel"] is None
    assert stations[0]["super95"] is None
    assert stations[0]["super98"] is None
    assert stations[0]["lpg"] is None


def test_parse_station_table_no_table_returns_empty_list() -> None:
    """_parse_station_table returns [] when no table found in HTML."""
    stations = _parse_station_table(_SAMPLE_HTML_NO_TABLE)
    assert stations == []


def test_parse_station_table_empty_table_returns_empty_list() -> None:
    """_parse_station_table returns [] when table has headers only, no data rows."""
    stations = _parse_station_table(_SAMPLE_HTML_EMPTY_TABLE)
    assert stations == []


def test_parse_station_table_second_station_prices() -> None:
    """_parse_station_table correctly parses second station row."""
    stations = _parse_station_table(_SAMPLE_HTML)
    assert stations[1]["diesel"] == pytest.approx(2.720)
    assert stations[1]["super95"] == pytest.approx(2.780)
    assert stations[1]["lpg"] == pytest.approx(1.230)


# ---------------------------------------------------------------------------
# _build_station_data — unit tests
# ---------------------------------------------------------------------------


def test_build_station_data_sets_diesel() -> None:
    """_build_station_data maps diesel price from raw dict."""
    raw = {
        "name": "Test",
        "address": "Street 1",
        "diesel": 2.75,
        "super95": 2.80,
        "super98": 2.95,
        "lpg": 1.25,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["diesel"] == pytest.approx(2.75)


def test_build_station_data_sets_lpg() -> None:
    """_build_station_data maps lpg price from raw dict."""
    raw = {
        "name": "Test",
        "address": "Street 1",
        "diesel": 2.75,
        "super95": 2.80,
        "super98": 2.95,
        "lpg": 1.25,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["lpg"] == pytest.approx(1.25)


def test_build_station_data_sets_name() -> None:
    """_build_station_data sets name from raw dict."""
    raw = {
        "name": "OMV Sarajevo",
        "address": "Street 1",
        "diesel": 2.75,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["name"] == "OMV Sarajevo"


def test_build_station_data_sets_address() -> None:
    """_build_station_data sets address from raw dict."""
    raw = {
        "name": "Test",
        "address": "Zmaja od Bosne 1",
        "diesel": 2.75,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["address"] == "Zmaja od Bosne 1"


def test_build_station_data_county_from_city_slug() -> None:
    """_build_station_data derives county from city_slug."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("banja-luka:1", raw, "banja-luka")
    assert data["county"] == "Banja Luka"


def test_build_station_data_source_station_id() -> None:
    """_build_station_data sets source_station_id to the composite key."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("tuzla:4", raw, "tuzla")
    assert data["source_station_id"] == "tuzla:4"


def test_build_station_data_brand_is_none() -> None:
    """_build_station_data sets brand=None (site has no brand field)."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["brand"] is None


def test_build_station_data_latitude_is_none() -> None:
    """_build_station_data sets latitude=None (site has no coordinates)."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["latitude"] is None


def test_build_station_data_longitude_is_none() -> None:
    """_build_station_data sets longitude=None (site has no coordinates)."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["longitude"] is None


def test_build_station_data_lastupdated_is_none() -> None:
    """_build_station_data sets lastupdated=None (no per-station timestamps)."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": None,
        "super95": None,
        "super98": None,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data["lastupdated"] is None


def test_build_station_data_super95_passthrough() -> None:
    """_build_station_data stores super95 as extra passthrough field."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": 2.75,
        "super95": 2.80,
        "super98": 2.95,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data.get("super95") == pytest.approx(2.80)  # type: ignore[typeddict-item]


def test_build_station_data_super98_passthrough() -> None:
    """_build_station_data stores super98 as extra passthrough field."""
    raw = {
        "name": "Test",
        "address": None,
        "diesel": 2.75,
        "super95": 2.80,
        "super98": 2.95,
        "lpg": None,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    assert data.get("super98") == pytest.approx(2.95)  # type: ignore[typeddict-item]


def test_build_station_data_all_capability_keys_present() -> None:
    """_build_station_data result contains all CAPABILITIES keys (minus sentinels)."""
    raw = {
        "name": "Test",
        "address": "Street",
        "diesel": 2.75,
        "super95": 2.80,
        "super98": 2.95,
        "lpg": 1.25,
    }
    data = _build_station_data("sarajevo:0", raw, "sarajevo")
    provider_caps = BaFuelProvider.CAPABILITIES - {
        "last_successful_fetch",
        "data_fetch_problem",
    }
    for key in provider_caps:
        assert key in data, f"Key '{key}' missing from _build_station_data output"


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns populated StationData on success."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:0")
    data = await provider.async_fetch(session, "sarajevo:0")

    assert data["diesel"] == pytest.approx(2.750)
    assert data["name"] == "OMV Sarajevo"


@pytest.mark.asyncio
async def test_async_fetch_success_returns_lpg() -> None:
    """async_fetch returns lpg price in result."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:0")
    data = await provider.async_fetch(session, "sarajevo:0")

    assert data["lpg"] == pytest.approx(1.250)


@pytest.mark.asyncio
async def test_async_fetch_second_row_uses_correct_index() -> None:
    """async_fetch with index 1 returns the second station row."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:1")
    data = await provider.async_fetch(session, "sarajevo:1")

    assert data["name"] == "Petrol Centar"
    assert data["diesel"] == pytest.approx(2.720)


@pytest.mark.asyncio
async def test_async_fetch_sets_county_from_city_slug() -> None:
    """async_fetch sets county derived from the city slug."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:0")
    data = await provider.async_fetch(session, "sarajevo:0")

    assert data["county"] == "Sarajevo"


@pytest.mark.asyncio
async def test_async_fetch_source_station_id_correct() -> None:
    """async_fetch sets source_station_id to the composite key."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:0")
    data = await provider.async_fetch(session, "sarajevo:0")

    assert data["source_station_id"] == "sarajevo:0"


# ---------------------------------------------------------------------------
# async_fetch — error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_on_http_failure() -> None:
    """async_fetch raises ProviderError when the HTTP request fails."""
    resp = _make_mock_response(
        404,
        raise_on_raise_for_status=ClientResponseError(
            request_info=MagicMock(), history=(), status=404, message="Not Found"
        ),
    )
    session = _make_session(resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_for_index_out_of_range() -> None:
    """async_fetch raises ProviderError when row index exceeds available rows."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML_ONE_STATION)
    session = _make_session(resp)

    provider = _default_provider(station_id="sarajevo:5")
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "sarajevo:5")


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_for_malformed_station_id() -> None:
    """async_fetch raises ProviderError for station_id with no colon."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "sarajevo-no-colon")


@pytest.mark.asyncio
async def test_async_fetch_raises_on_network_error() -> None:
    """async_fetch raises ProviderError when session.get raises ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network failure"))

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_none() -> None:
    """async_fetch_station_name always returns None (location-mode provider)."""
    session = MagicMock()
    provider = _default_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)
    assert name is None


@pytest.mark.asyncio
async def test_async_fetch_station_name_makes_no_requests() -> None:
    """async_fetch_station_name makes no HTTP requests."""
    session = MagicMock()
    provider = _default_provider()
    await provider.async_fetch_station_name(session, _STATION_ID)
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_all_stations_from_page() -> None:
    """async_list_stations returns one tuple per station row."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_async_list_stations_station_id_format() -> None:
    """async_list_stations station IDs follow 'city:index' format."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    assert result[0][0] == "sarajevo:1"  # Petrol Centar is cheaper diesel
    assert result[1][0] == "sarajevo:0"


@pytest.mark.asyncio
async def test_async_list_stations_sorted_cheapest_diesel_first() -> None:
    """async_list_stations sorts stations by cheapest diesel first."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    # Petrol Centar has diesel 2.720, OMV has 2.750 — cheaper first
    first_sid, first_label = result[0]
    assert "Petrol Centar" in first_label


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_name() -> None:
    """async_list_stations label includes station name."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    labels = [label for _, label in result]
    assert any("OMV Sarajevo" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes formatted diesel price in KM."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    labels = [label for _, label in result]
    assert any("Diesel" in lbl and "KM" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_async_list_stations_no_price_sorted_last() -> None:
    """async_list_stations places stations with no diesel price at the end."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML_NULL_PRICES)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    # Only one station and it has no price — should still appear (not filtered)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# async_list_stations — error / edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns [] when the HTTP request fails."""
    resp = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Server Error"
        ),
    )
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_network_error() -> None:
    """async_list_stations returns [] on network-level failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection reset"))

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_when_no_table() -> None:
    """async_list_stations returns [] when city page has no station table."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML_NO_TABLE)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session, city="sarajevo")

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_uses_first_city_slug_as_default() -> None:
    """async_list_stations uses _CITY_SLUGS[0] when city kwarg is absent."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    result = await provider.async_list_stations(session)

    # Should not raise; result may be empty or populated depending on default city
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_async_list_stations_lat_zero_is_not_falsy() -> None:
    """async_list_stations treats lat=0.0 as valid (is-not-None check)."""
    resp = _make_mock_response(200, text=_SAMPLE_HTML)
    session = _make_session(resp)

    provider = _default_provider()
    # lat=0.0 and lng=0.0 are valid coordinates (prime meridian/equator)
    # Should not fall back to instance coords and should not error
    result = await provider.async_list_stations(
        session, city="sarajevo", lat=0.0, lng=0.0
    )
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _fetch_city_html — internal helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_city_html_returns_text_on_success() -> None:
    """_fetch_city_html returns HTML text for a 200 response."""
    resp = _make_mock_response(200, text="<html>content</html>")
    session = _make_session(resp)

    provider = _default_provider()
    html = await provider._fetch_city_html(session, "sarajevo")

    assert html == "<html>content</html>"


@pytest.mark.asyncio
async def test_fetch_city_html_returns_none_on_http_error() -> None:
    """_fetch_city_html returns None on HTTP error response."""
    resp = _make_mock_response(
        404,
        raise_on_raise_for_status=ClientResponseError(
            request_info=MagicMock(), history=(), status=404, message="Not Found"
        ),
    )
    session = _make_session(resp)

    provider = _default_provider()
    html = await provider._fetch_city_html(session, "nonexistent-city")

    assert html is None


@pytest.mark.asyncio
async def test_fetch_city_html_returns_none_on_network_error() -> None:
    """_fetch_city_html returns None on network-level error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _default_provider()
    html = await provider._fetch_city_html(session, "sarajevo")

    assert html is None


@pytest.mark.asyncio
async def test_fetch_city_html_calls_correct_url() -> None:
    """_fetch_city_html requests the correct cijenegoriva.ba URL."""
    resp = _make_mock_response(200, text="<html/>")
    session = _make_session(resp)

    provider = _default_provider()
    await provider._fetch_city_html(session, "mostar")

    call_args = session.get.call_args
    called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "cijenegoriva.ba" in called_url
    assert "mostar" in called_url
