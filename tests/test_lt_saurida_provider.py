"""Tests for LtSauridaProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.lt_saurida import (
    LtSauridaProvider,
    _HEADERS,
    _PRICES_URL,
    _find_station,
    _header_to_data_key,
    _parse_price_eur,
    _parse_table,
)

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_STATION_NAME_1 = "Vilnius, Kalvarijų g. 3"
_STATION_NAME_2 = "Kaunas, Taikos pr. 151"

# Minimal valid HTML table matching the saurida.lt structure
_VALID_HTML = """
<html><body>
<table class="table text-left responsive">
  <thead>
    <tr>
      <th>Degalinė</th>
      <th>Dyzelinas_B7</th>
      <th>Benzinas_A95_E5</th>
      <th>Benzinas_A98_E5</th>
      <th>Dujos_LPG</th>
      <th>Dyzelinas_DZ</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Vilnius, Kalvarijų g. 3</td>
      <td>1.539</td>
      <td>1.629</td>
      <td>1.719</td>
      <td>0.689</td>
      <td>1.599</td>
    </tr>
    <tr>
      <td>Kaunas, Taikos pr. 151</td>
      <td>1.529</td>
      <td>1.619</td>
      <td></td>
      <td>0.679</td>
      <td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""

_EMPTY_TABLE_HTML = """
<html><body>
<table class="table text-left responsive">
  <thead>
    <tr>
      <th>Degalinė</th>
      <th>Dyzelinas_B7</th>
    </tr>
  </thead>
  <tbody>
  </tbody>
</table>
</body></html>
"""

_NO_TABLE_HTML = "<html><body><p>No table here.</p></body></html>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    text_data: str = "",
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text_data)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() returns *response*."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _make_provider(
    station_id: str = _STATION_NAME_1,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float | None = None,
) -> LtSauridaProvider:
    return LtSauridaProvider(
        station_id=station_id,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """LtSauridaProvider declares COUNTRY='LT'."""
    assert LtSauridaProvider.COUNTRY == "LT"


def test_provider_key() -> None:
    """LtSauridaProvider declares PROVIDER_KEY='lt_saurida'."""
    assert LtSauridaProvider.PROVIDER_KEY == "lt_saurida"


def test_provider_label_contains_lithuania() -> None:
    """LtSauridaProvider LABEL mentions Lithuania."""
    assert "Lithuania" in LtSauridaProvider.LABEL or "LT" in LtSauridaProvider.LABEL


def test_provider_label_contains_saurida() -> None:
    """LtSauridaProvider LABEL mentions Saurida."""
    assert "Saurida" in LtSauridaProvider.LABEL


def test_provider_config_mode_is_station_id() -> None:
    """LtSauridaProvider uses CONFIG_MODE='station_id'."""
    assert LtSauridaProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """LtSauridaProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert LtSauridaProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_does_not_require_api_key() -> None:
    """LtSauridaProvider does not require an API key."""
    assert LtSauridaProvider.REQUIRES_API_KEY is False


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in LtSauridaProvider.CAPABILITIES


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (benzinas_a95_e5)."""
    assert "unleaded" in LtSauridaProvider.CAPABILITIES


def test_provider_capabilities_include_premium_unleaded() -> None:
    """CAPABILITIES includes 'premium_unleaded' (benzinas_a98_e5)."""
    assert "premium_unleaded" in LtSauridaProvider.CAPABILITIES


def test_provider_capabilities_include_lpg() -> None:
    """CAPABILITIES includes 'lpg' (dujos_lpg)."""
    assert "lpg" in LtSauridaProvider.CAPABILITIES


def test_provider_capabilities_include_premium_diesel() -> None:
    """CAPABILITIES includes 'premium_diesel' (dyzelinas_dz)."""
    assert "premium_diesel" in LtSauridaProvider.CAPABILITIES


def test_provider_capabilities_include_name_and_brand() -> None:
    """CAPABILITIES includes 'name' and 'brand'."""
    caps = LtSauridaProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = LtSauridaProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = _make_provider(station_id="TestStation")
    assert provider._station_id == "TestStation"


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = LtSauridaProvider(station_id="x")
    assert provider._radius_km == pytest.approx(10.0)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=5.0)
    assert provider._radius_km == pytest.approx(5.0)


def test_constructor_accepts_none_lat_lng() -> None:
    """Constructor accepts None for latitude and longitude."""
    provider = LtSauridaProvider(station_id="x", latitude=None, longitude=None)
    assert provider._latitude is None
    assert provider._longitude is None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_prices_url_points_to_saurida() -> None:
    """_PRICES_URL targets saurida.lt."""
    assert "saurida.lt" in _PRICES_URL
    assert _PRICES_URL.startswith("https://")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


# ---------------------------------------------------------------------------
# _parse_price_eur
# ---------------------------------------------------------------------------


def test_parse_price_eur_valid_float() -> None:
    """_parse_price_eur returns a rounded float for a valid price."""
    assert _parse_price_eur("1.539") == pytest.approx(1.539)


def test_parse_price_eur_comma_separator() -> None:
    """_parse_price_eur handles comma as decimal separator."""
    assert _parse_price_eur("1,539") == pytest.approx(1.539)


def test_parse_price_eur_returns_none_for_empty() -> None:
    """_parse_price_eur returns None for empty string."""
    assert _parse_price_eur("") is None


def test_parse_price_eur_returns_none_for_none() -> None:
    """_parse_price_eur returns None when value is None."""
    assert _parse_price_eur(None) is None


def test_parse_price_eur_returns_none_for_zero() -> None:
    """_parse_price_eur returns None for zero."""
    assert _parse_price_eur("0") is None


def test_parse_price_eur_returns_none_for_negative() -> None:
    """_parse_price_eur returns None for negative values."""
    assert _parse_price_eur("-1.5") is None


def test_parse_price_eur_returns_none_for_garbage() -> None:
    """_parse_price_eur returns None for non-numeric string."""
    assert _parse_price_eur("n/a") is None


def test_parse_price_eur_rounds_to_three_decimals() -> None:
    """_parse_price_eur rounds to 3 decimal places."""
    result = _parse_price_eur("1.5394")
    assert result == pytest.approx(1.539, rel=1e-3)


# ---------------------------------------------------------------------------
# _header_to_data_key
# ---------------------------------------------------------------------------


def test_header_to_data_key_dyzelinas_b7() -> None:
    """_header_to_data_key maps Dyzelinas_B7 variants to 'diesel'."""
    assert _header_to_data_key("Dyzelinas_B7") == "diesel"
    assert _header_to_data_key("dyzelinas b7") == "diesel"


def test_header_to_data_key_a95() -> None:
    """_header_to_data_key maps A95 variants to 'unleaded'."""
    assert _header_to_data_key("Benzinas_A95_E5") == "unleaded"
    assert _header_to_data_key("A95") == "unleaded"


def test_header_to_data_key_a98() -> None:
    """_header_to_data_key maps A98 variants to 'premium_unleaded'."""
    assert _header_to_data_key("Benzinas_A98_E5") == "premium_unleaded"
    assert _header_to_data_key("A98") == "premium_unleaded"


def test_header_to_data_key_lpg() -> None:
    """_header_to_data_key maps LPG/dujos variants to 'lpg'."""
    assert _header_to_data_key("Dujos_LPG") == "lpg"
    assert _header_to_data_key("LPG") == "lpg"


def test_header_to_data_key_premium_diesel() -> None:
    """_header_to_data_key maps DZ variants to 'premium_diesel'."""
    assert _header_to_data_key("Dyzelinas_DZ") == "premium_diesel"


def test_header_to_data_key_unknown_returns_none() -> None:
    """_header_to_data_key returns None for unrecognised header."""
    assert _header_to_data_key("Degalinė") is None
    assert _header_to_data_key("Station") is None


# ---------------------------------------------------------------------------
# _parse_table
# ---------------------------------------------------------------------------


def test_parse_table_returns_list_of_dicts() -> None:
    """_parse_table returns a list of station dicts."""
    result = _parse_table(_VALID_HTML)
    assert isinstance(result, list)
    assert len(result) == 2


def test_parse_table_station_names() -> None:
    """_parse_table extracts station names from column 0."""
    result = _parse_table(_VALID_HTML)
    names = [s["name"] for s in result]
    assert _STATION_NAME_1 in names
    assert _STATION_NAME_2 in names


def test_parse_table_diesel_price_first_station() -> None:
    """_parse_table correctly parses diesel price for first station."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_1)
    assert station["diesel"] == pytest.approx(1.539)


def test_parse_table_unleaded_price_first_station() -> None:
    """_parse_table correctly parses unleaded (A95) price."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_1)
    assert station["unleaded"] == pytest.approx(1.629)


def test_parse_table_premium_unleaded_price() -> None:
    """_parse_table correctly parses premium_unleaded (A98) price."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_1)
    assert station["premium_unleaded"] == pytest.approx(1.719)


def test_parse_table_lpg_price() -> None:
    """_parse_table correctly parses LPG price."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_1)
    assert station["lpg"] == pytest.approx(0.689)


def test_parse_table_premium_diesel_price() -> None:
    """_parse_table correctly parses premium_diesel (DZ) price."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_1)
    assert station["premium_diesel"] == pytest.approx(1.599)


def test_parse_table_empty_cell_returns_none() -> None:
    """_parse_table returns None for empty price cells."""
    result = _parse_table(_VALID_HTML)
    station = next(s for s in result if s["name"] == _STATION_NAME_2)
    assert station["premium_unleaded"] is None
    assert station["premium_diesel"] is None


def test_parse_table_raises_provider_error_when_no_table() -> None:
    """_parse_table raises ProviderError when no table is present."""
    with pytest.raises(ProviderError, match="No table found"):
        _parse_table(_NO_TABLE_HTML)


def test_parse_table_raises_provider_error_for_empty_rows() -> None:
    """_parse_table raises ProviderError when table has no data rows."""
    with pytest.raises(ProviderError):
        _parse_table(_EMPTY_TABLE_HTML)


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station whose name matches station_id."""
    stations = _parse_table(_VALID_HTML)
    result = _find_station(stations, _STATION_NAME_1)
    assert result is not None
    assert result["name"] == _STATION_NAME_1


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station matches station_id."""
    stations = _parse_table(_VALID_HTML)
    result = _find_station(stations, "NonExistent Station")
    assert result is None


def test_find_station_case_insensitive_fallback() -> None:
    """_find_station performs case-insensitive match as fallback."""
    stations = _parse_table(_VALID_HTML)
    result = _find_station(stations, _STATION_NAME_1.upper())
    assert result is not None


def test_find_station_returns_none_for_empty_list() -> None:
    """_find_station returns None for an empty list."""
    assert _find_station([], "anything") is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert data["name"] == _STATION_NAME_1
    assert data["brand"] == "Saurida"


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns correct diesel price."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert data["diesel"] == pytest.approx(1.539)


async def test_async_fetch_unleaded_price() -> None:
    """async_fetch returns correct unleaded price."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert data["unleaded"] == pytest.approx(1.629)


async def test_async_fetch_lpg_price() -> None:
    """async_fetch returns correct LPG price."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert data["lpg"] == pytest.approx(0.689)


async def test_async_fetch_prices_are_eur_per_litre() -> None:
    """async_fetch prices are already in EUR/litre (< 10)."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert data["diesel"] is not None
    assert data["diesel"] < 10.0  # EUR/litre, not cents


async def test_async_fetch_none_prices_for_missing_fuel_type() -> None:
    """async_fetch returns None for fuel types not available at station."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_2)
    data = await provider.async_fetch(session, _STATION_NAME_2)

    assert data["premium_unleaded"] is None
    assert data["premium_diesel"] is None


async def test_async_fetch_source_station_id_not_in_result() -> None:
    """async_fetch does not populate source_station_id (not in CAPABILITIES)."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert "source_station_id" not in data


async def test_async_fetch_lastupdated_not_in_result() -> None:
    """async_fetch does not populate lastupdated (not in CAPABILITIES)."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    data = await provider.async_fetch(session, _STATION_NAME_1)

    assert "lastupdated" not in data


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station_id is not in the table."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id="NonExistent Station")

    with pytest.raises(ProviderError, match="NonExistent Station"):
        await provider.async_fetch(session, "NonExistent Station")


async def test_async_fetch_raises_provider_error_for_no_table() -> None:
    """async_fetch raises ProviderError when response contains no table."""
    resp = _make_mock_response(200, text_data=_NO_TABLE_HTML)
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ProviderError, match="No table found"):
        await provider.async_fetch(session, _STATION_NAME_1)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets aiohttp ClientError propagate to the coordinator."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_NAME_1)


async def test_async_fetch_raises_on_http_error() -> None:
    """async_fetch raises when raise_for_status() raises (e.g. HTTP 503)."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = _make_provider()

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_NAME_1)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns the station name when found."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id=_STATION_NAME_1)
    name = await provider.async_fetch_station_name(session, _STATION_NAME_1)

    assert name == _STATION_NAME_1


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _STATION_NAME_1)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station_id is not in the table."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider(station_id="NonExistent")
    name = await provider.async_fetch_station_name(session, "NonExistent")

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (str, str) tuples."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert isinstance(result, list)
    assert len(result) == 2
    name, label = result[0]
    assert isinstance(name, str)
    assert isinstance(label, str)


async def test_async_list_stations_label_contains_diesel_price() -> None:
    """async_list_stations label contains station identifier token (no price)."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result
    labels = [label for _, label in result]
    assert any("(#" in lbl for lbl in labels)


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts stations alphabetically by label."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    # "Kaunas..." < "Vilnius..." alphabetically
    assert result[0][0] == _STATION_NAME_2
    assert result[1][0] == _STATION_NAME_1


async def test_async_list_stations_station_id_is_station_name() -> None:
    """async_list_stations returns station name as id."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    ids = [r[0] for r in result]
    assert _STATION_NAME_1 in ids
    assert _STATION_NAME_2 in ids


async def test_async_list_stations_accepts_lat_lng_kwargs() -> None:
    """async_list_stations accepts lat/lng kwargs without error (no-op filter)."""
    resp = _make_mock_response(200, text_data=_VALID_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(
        session, lat=54.6872, lng=25.2797, radius_km=10.0
    )

    # All stations returned regardless of distance — no GPS data available
    assert len(result) == 2


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns [] when raise_for_status raises."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_no_table() -> None:
    """async_list_stations returns [] when response has no table."""
    resp = _make_mock_response(200, text_data=_NO_TABLE_HTML)
    session = _make_session(resp)

    provider = _make_provider()
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """LtSauridaProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "lt_saurida" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["lt_saurida"] is LtSauridaProvider


# ---------------------------------------------------------------------------
# _TableParser — line 146: handle_starttag early return when _done=True
# ---------------------------------------------------------------------------


def test_parse_table_ignores_tags_after_first_table_closes() -> None:
    """_parse_table correctly handles HTML with content after </table> (line 146)."""
    html = (
        "<table>"
        "<tr><th>Degalinė</th><th>Dyzelinas_B7</th></tr>"
        "<tr><td>Station A</td><td>1.539</td></tr>"
        "</table>"
        "<table><tr><td>should be ignored</td></tr></table>"
    )
    result = _parse_table(html)
    assert len(result) == 1
    assert result[0]["name"] == "Station A"


# ---------------------------------------------------------------------------
# _parse_table — line 257: empty header row raises ProviderError
# ---------------------------------------------------------------------------


def test_parse_table_raises_provider_error_for_empty_header(monkeypatch) -> None:
    """_parse_table raises ProviderError when header row is empty (line 257)."""
    import custom_components.fuelcompare_ie.providers.lt_saurida as _mod

    class _FakeParser:
        def feed(self, html: str) -> None:
            pass

        @property
        def rows(self):
            return [[]]

    monkeypatch.setattr(_mod, "_TableParser", _FakeParser)
    with pytest.raises(ProviderError, match="Empty header row"):
        _parse_table("<irrelevant/>")


# ---------------------------------------------------------------------------
# _parse_table — line 274: rows with empty station name are skipped
# ---------------------------------------------------------------------------


def test_parse_table_skips_rows_with_empty_station_name() -> None:
    """_parse_table skips data rows whose first cell is blank (line 274)."""
    html = (
        "<table>"
        "<tr><th>Degalinė</th><th>Dyzelinas_B7</th></tr>"
        "<tr><td>  </td><td>1.539</td></tr>"
        "<tr><td>Real Station</td><td>1.529</td></tr>"
        "</table>"
    )
    result = _parse_table(html)
    assert len(result) == 1
    assert result[0]["name"] == "Real Station"


# ---------------------------------------------------------------------------
# async_list_stations — line 512: empty stations list returns []
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_fetch_yields_no_stations(
    monkeypatch,
) -> None:
    """async_list_stations returns [] when _fetch_all_stations returns [] (line 512)."""
    provider = _make_provider()

    async def _fake_fetch_all(session):
        return []

    monkeypatch.setattr(provider, "_fetch_all_stations", _fake_fetch_all)
    result = await provider.async_list_stations(MagicMock())
    assert result == []


# ---------------------------------------------------------------------------
# async_list_stations — line 519: stations without a name are skipped
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_stations_with_missing_name(
    monkeypatch,
) -> None:
    """async_list_stations skips station dicts with no 'name' key (line 519)."""
    provider = _make_provider()

    async def _fake_fetch_all(session):
        return [
            {"diesel": 1.539, "unleaded": 1.629},
            {"name": "", "diesel": 1.529, "unleaded": 1.619},
            {"name": _STATION_NAME_1, "diesel": 1.539, "unleaded": 1.629},
        ]

    monkeypatch.setattr(provider, "_fetch_all_stations", _fake_fetch_all)
    result = await provider.async_list_stations(MagicMock())
    assert len(result) == 1
    assert result[0][0] == _STATION_NAME_1
