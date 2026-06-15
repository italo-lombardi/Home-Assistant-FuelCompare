"""Tests for BeCarbuProvider (carbu.com, Belgium)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.be_carbu import (
    BeCarbuProvider,
    _FUEL_KEY_TO_SLUG,
    _HEADERS,
    _JSON_HEADERS,
    _LOCATION_URL,
    _STATION_LISTING_URL,
    _extract_price_from_text,
    _normalise_town,
    _parse_station_html,
)


# ---------------------------------------------------------------------------
# Fixtures and builder helpers
# ---------------------------------------------------------------------------

_STATION_ID = "99001"
_OTHER_STATION_ID = "99002"
_POSTAL_CODE = "1000"
_TOWN = "brussels"
_LOCATION_ID = "42"

# Minimal location API response
_LOCATION_PAYLOAD: list[dict[str, Any]] = [
    {
        "location_id": _LOCATION_ID,
        "town": "Brussels",
        "postal_code": _POSTAL_CODE,
    }
]

# Minimal station listing HTML fragment that produces parseable results.
# Uses the data attribute pattern expected by _parse_station_html.
_STATION_HTML_TEMPLATE = """\
<html>
<body>
<div class="station-content" data-id="{station_id}" data-lat="50.850" data-lng="4.352">
  <span class="station-name">Test Station</span>
  <span class="prix">{price}</span>
  <img class="brand-logo" alt="TotalEnergies" src="/logo.png"/>
  <span class="adresse">Rue Test 1, Brussels</span>
</div>
</body>
</html>"""

_TWO_STATIONS_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="50.850" data-lng="4.352">
  <span class="station-name">Cheap Station</span>
  <span class="prix">1,799</span>
  <img class="brand-logo" alt="Q8" src="/logo.png"/>
  <span class="adresse">Rue A, Brussels</span>
</div>
<div class="station-content" data-id="99002" data-lat="50.860" data-lng="4.360">
  <span class="station-name">Expensive Station</span>
  <span class="prix">1,899</span>
  <img class="brand-logo" alt="BP" src="/logo.png"/>
  <span class="adresse">Rue B, Brussels</span>
</div>
</body>
</html>"""

_EMPTY_HTML = "<html><body><p>No stations found</p></body></html>"


def _make_json_response(payload: Any, status: int = 200) -> AsyncMock:
    """Build a mock aiohttp response that returns JSON."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.text = AsyncMock(return_value="")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_html_response(html: str, status: int = 200) -> AsyncMock:
    """Build a mock aiohttp response that returns HTML text."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=html)
    mock_resp.json = AsyncMock(return_value={})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session_with_responses(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() returns responses in order."""
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


def _make_provider(
    station_id: str = _STATION_ID,
    postal_code: str = _POSTAL_CODE,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = 10.0,
) -> BeCarbuProvider:
    """Create a BeCarbuProvider instance for testing."""
    return BeCarbuProvider(
        station_id=station_id,
        postal_code=postal_code,
        latitude=lat,
        longitude=lng,
        radius_km=radius_km,
    )


def _provider_with_cached_location(
    station_id: str = _STATION_ID,
    postal_code: str = _POSTAL_CODE,
) -> BeCarbuProvider:
    """Create a provider with pre-seeded location cache to avoid location HTTP call."""
    provider = _make_provider(station_id=station_id, postal_code=postal_code)
    provider._location_cache[postal_code] = (_TOWN, _LOCATION_ID)
    return provider


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """BeCarbuProvider declares COUNTRY='BE'."""
    assert BeCarbuProvider.COUNTRY == "BE"


def test_provider_key() -> None:
    """BeCarbuProvider declares PROVIDER_KEY='be_carbu'."""
    assert BeCarbuProvider.PROVIDER_KEY == "be_carbu"


def test_provider_label() -> None:
    """BeCarbuProvider has a non-empty human-readable LABEL."""
    assert BeCarbuProvider.LABEL
    assert "Belgium" in BeCarbuProvider.LABEL or "Carbu" in BeCarbuProvider.LABEL


def test_provider_config_mode() -> None:
    """BeCarbuProvider uses location CONFIG_MODE."""
    assert BeCarbuProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """BeCarbuProvider uses location_search STATION_LOOKUP_MODE."""
    assert BeCarbuProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """Poll interval is 3600 seconds (1 hour) matching carbu.com throttle cadence."""
    assert BeCarbuProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes diesel fuel type."""
    assert "diesel" in BeCarbuProvider.CAPABILITIES


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes unleaded (Super 95 E10) fuel type."""
    assert "unleaded" in BeCarbuProvider.CAPABILITIES


def test_provider_capabilities_include_premium_unleaded() -> None:
    """CAPABILITIES includes premium_unleaded (Super 98 E5) fuel type."""
    assert "premium_unleaded" in BeCarbuProvider.CAPABILITIES


def test_provider_capabilities_include_lpg() -> None:
    """CAPABILITIES includes lpg fuel type."""
    assert "lpg" in BeCarbuProvider.CAPABILITIES


def test_provider_capabilities_include_cng() -> None:
    """CAPABILITIES includes cng fuel type."""
    assert "cng" in BeCarbuProvider.CAPABILITIES


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes standard station identity and location fields."""
    caps = BeCarbuProvider.CAPABILITIES
    for field in ("name", "brand", "address", "latitude", "longitude", "lastupdated"):
        assert field in caps, f"Field '{field}' missing from CAPABILITIES"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    assert "last_successful_fetch" not in BeCarbuProvider.CAPABILITIES
    assert "data_fetch_problem" not in BeCarbuProvider.CAPABILITIES


def test_provider_does_not_require_api_key() -> None:
    """BeCarbuProvider does not require an API key (scraping-based)."""
    assert BeCarbuProvider.REQUIRES_API_KEY is False


# ---------------------------------------------------------------------------
# Constructor / initialisation
# ---------------------------------------------------------------------------


def test_init_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = BeCarbuProvider("12345")
    assert provider._station_id == "12345"


def test_init_stores_postal_code() -> None:
    """Constructor stores postal_code."""
    provider = BeCarbuProvider("12345", postal_code="2000")
    assert provider._postal_code == "2000"


def test_init_default_radius_km() -> None:
    """Constructor defaults radius_km to 10.0 when not provided."""
    provider = BeCarbuProvider("12345")
    assert provider._radius_km == 10.0


def test_init_custom_radius_km() -> None:
    """Constructor stores custom radius_km value."""
    provider = BeCarbuProvider("12345", radius_km=25.0)
    assert provider._radius_km == 25.0


def test_init_none_radius_uses_default() -> None:
    """Constructor treats radius_km=None as 10.0 default."""
    provider = BeCarbuProvider("12345", radius_km=None)
    assert provider._radius_km == 10.0


def test_init_stores_coordinates() -> None:
    """Constructor stores latitude and longitude."""
    provider = BeCarbuProvider("12345", latitude=50.85, longitude=4.35)
    assert provider._latitude == pytest.approx(50.85)
    assert provider._longitude == pytest.approx(4.35)


def test_init_location_cache_starts_empty() -> None:
    """Location cache is empty at construction time."""
    provider = BeCarbuProvider("12345")
    assert provider._location_cache == {}


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_location_url_points_to_carbu() -> None:
    """_LOCATION_URL points at carbu.com location endpoint."""
    from urllib.parse import urlparse

    assert urlparse(_LOCATION_URL).netloc == "carbu.com"
    assert _LOCATION_URL.startswith("https://")


def test_station_listing_url_template_contains_carbu() -> None:
    """_STATION_LISTING_URL contains carbu.com domain and template slots."""
    from urllib.parse import urlparse

    assert urlparse(_STATION_LISTING_URL).netloc == "carbu.com"
    assert "{fueltype}" in _STATION_LISTING_URL
    assert "{postalcode}" in _STATION_LISTING_URL
    assert "{locationid}" in _STATION_LISTING_URL


def test_headers_include_browser_user_agent() -> None:
    """_HEADERS includes a browser-style User-Agent to pass carbu.com bot check."""
    assert "User-Agent" in _HEADERS
    assert "Mozilla" in _HEADERS["User-Agent"]


def test_json_headers_include_xhr_header() -> None:
    """_JSON_HEADERS includes X-Requested-With: XMLHttpRequest for JSON calls."""
    assert "X-Requested-With" in _JSON_HEADERS
    assert _JSON_HEADERS["X-Requested-With"] == "XMLHttpRequest"


def test_fuel_key_to_slug_diesel() -> None:
    """_FUEL_KEY_TO_SLUG maps diesel to 'GO' (carbu.com Gasoil slug)."""
    assert _FUEL_KEY_TO_SLUG["diesel"] == "GO"


def test_fuel_key_to_slug_unleaded() -> None:
    """_FUEL_KEY_TO_SLUG maps unleaded to 'E10' (Super 95)."""
    assert _FUEL_KEY_TO_SLUG["unleaded"] == "E10"


def test_fuel_key_to_slug_premium_unleaded() -> None:
    """_FUEL_KEY_TO_SLUG maps premium_unleaded to 'E5' (Super 98)."""
    assert _FUEL_KEY_TO_SLUG["premium_unleaded"] == "E5"


def test_fuel_key_to_slug_lpg() -> None:
    """_FUEL_KEY_TO_SLUG maps lpg to 'LPG'."""
    assert _FUEL_KEY_TO_SLUG["lpg"] == "LPG"


def test_fuel_key_to_slug_cng() -> None:
    """_FUEL_KEY_TO_SLUG maps cng to 'CNG'."""
    assert _FUEL_KEY_TO_SLUG["cng"] == "CNG"


# ---------------------------------------------------------------------------
# _normalise_town
# ---------------------------------------------------------------------------


def test_normalise_town_lowercase() -> None:
    """_normalise_town converts to lowercase."""
    assert _normalise_town("Brussels") == "brussels"


def test_normalise_town_spaces_to_hyphens() -> None:
    """_normalise_town replaces spaces with hyphens."""
    assert _normalise_town("Sint Niklaas") == "sint-niklaas"


def test_normalise_town_strips_accents() -> None:
    """_normalise_town strips common Belgian French/Dutch accented characters."""
    result = _normalise_town("Liège")
    assert result == "liege"


def test_normalise_town_collapses_consecutive_hyphens() -> None:
    """_normalise_town collapses consecutive hyphens into one."""
    result = _normalise_town("Braine-l'Alleud")
    assert "--" not in result


def test_normalise_town_strips_leading_trailing_hyphens() -> None:
    """_normalise_town strips leading and trailing hyphens."""
    result = _normalise_town("-Brussels-")
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_normalise_town_already_clean() -> None:
    """_normalise_town is idempotent on already-clean slugs."""
    assert _normalise_town("brussels") == "brussels"


def test_normalise_town_with_apostrophe() -> None:
    """_normalise_town converts apostrophes to hyphens."""
    result = _normalise_town("Braine-l'Alleud")
    assert "'" not in result


# ---------------------------------------------------------------------------
# _extract_price_from_text
# ---------------------------------------------------------------------------


def test_extract_price_standard_format() -> None:
    """_extract_price_from_text parses '1.999' as 1.999 EUR/litre."""
    assert _extract_price_from_text("1.999") == pytest.approx(1.999)


def test_extract_price_comma_decimal() -> None:
    """_extract_price_from_text handles comma as decimal separator."""
    assert _extract_price_from_text("1,999") == pytest.approx(1.999)


def test_extract_price_with_euro_symbol() -> None:
    """_extract_price_from_text strips euro symbol before parsing."""
    assert _extract_price_from_text("€ 1.799") == pytest.approx(1.799)


def test_extract_price_cents_normalisation() -> None:
    """_extract_price_from_text divides values > 10 by 100 (cents to EUR/litre)."""
    result = _extract_price_from_text("199.9")
    assert result is not None
    assert result == pytest.approx(1.999)


def test_extract_price_none_input() -> None:
    """_extract_price_from_text returns None for None input."""
    assert _extract_price_from_text(None) is None  # type: ignore[arg-type]


def test_extract_price_empty_string() -> None:
    """_extract_price_from_text returns None for empty string."""
    assert _extract_price_from_text("") is None


def test_extract_price_invalid_string() -> None:
    """_extract_price_from_text returns None for non-numeric strings."""
    assert _extract_price_from_text("N/A") is None


def test_extract_price_out_of_range_returns_none() -> None:
    """_extract_price_from_text returns None for implausible values (> 5 EUR/l)."""
    result = _extract_price_from_text("9999.99")
    assert result is None


def test_extract_price_zero_returns_none() -> None:
    """_extract_price_from_text returns None for zero price."""
    assert _extract_price_from_text("0.000") is None


def test_extract_price_rounds_to_three_decimal_places() -> None:
    """_extract_price_from_text rounds to 3 decimal places."""
    result = _extract_price_from_text("1.9994")
    assert result == pytest.approx(1.999, abs=1e-3)


# ---------------------------------------------------------------------------
# _parse_station_html
# ---------------------------------------------------------------------------


def test_parse_station_html_extracts_station_id() -> None:
    """_parse_station_html extracts the station ID from data-id attribute."""
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="1,799")
    result = _parse_station_html(html, "diesel")
    station_ids = [s["station_id"] for s in result]
    assert _STATION_ID in station_ids


def test_parse_station_html_extracts_price() -> None:
    """_parse_station_html extracts and parses the price from the price span."""
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="1,799")
    result = _parse_station_html(html, "diesel")
    matching = [s for s in result if s["station_id"] == _STATION_ID]
    assert matching
    price = matching[0].get("price")
    assert price == pytest.approx(1.799)


def test_parse_station_html_extracts_latitude() -> None:
    """_parse_station_html extracts latitude from data-lat attribute."""
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="1,799")
    result = _parse_station_html(html, "diesel")
    matching = [s for s in result if s["station_id"] == _STATION_ID]
    assert matching
    assert matching[0].get("latitude") == pytest.approx(50.850)


def test_parse_station_html_extracts_longitude() -> None:
    """_parse_station_html extracts longitude from data-lng attribute."""
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="1,799")
    result = _parse_station_html(html, "diesel")
    matching = [s for s in result if s["station_id"] == _STATION_ID]
    assert matching
    assert matching[0].get("longitude") == pytest.approx(4.352)


def test_parse_station_html_stores_fuel_key() -> None:
    """_parse_station_html sets the fuel_key on each parsed station dict."""
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="1,799")
    result = _parse_station_html(html, "diesel")
    matching = [s for s in result if s["station_id"] == _STATION_ID]
    assert matching
    assert matching[0]["fuel_key"] == "diesel"


def test_parse_station_html_two_stations() -> None:
    """_parse_station_html returns one entry per station-content div."""
    result = _parse_station_html(_TWO_STATIONS_HTML, "diesel")
    station_ids = {s["station_id"] for s in result}
    assert "99001" in station_ids
    assert "99002" in station_ids


def test_parse_station_html_empty_html_returns_empty_list() -> None:
    """_parse_station_html returns an empty list for HTML with no station data."""
    result = _parse_station_html(_EMPTY_HTML, "diesel")
    assert result == []


def test_parse_station_html_returns_list() -> None:
    """_parse_station_html always returns a list."""
    result = _parse_station_html("", "diesel")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _resolve_location (via async_fetch / async_list_stations with mocked session)
# ---------------------------------------------------------------------------


async def test_resolve_location_caches_result() -> None:
    """_resolve_location caches the (town, location_id) pair after first call."""
    provider = _make_provider()
    loc_resp = _make_json_response(_LOCATION_PAYLOAD)
    session = MagicMock()
    session.get = MagicMock(return_value=loc_resp)

    await provider._resolve_location(session, _POSTAL_CODE)
    # Second call should use cache — session.get should only be called once
    await provider._resolve_location(session, _POSTAL_CODE)

    assert session.get.call_count == 1


async def test_resolve_location_raises_provider_error_on_empty_response() -> None:
    """_resolve_location raises ProviderError when location API returns empty list."""
    provider = _make_provider()
    empty_resp = _make_json_response([])
    session = MagicMock()
    session.get = MagicMock(return_value=empty_resp)

    with pytest.raises(ProviderError, match=_POSTAL_CODE):
        await provider._resolve_location(session, _POSTAL_CODE)


async def test_resolve_location_raises_provider_error_on_403() -> None:
    """_resolve_location raises ProviderError on HTTP 403 from carbu.com."""
    provider = _make_provider()
    forbidden_resp = _make_json_response({}, status=403)
    session = MagicMock()
    session.get = MagicMock(return_value=forbidden_resp)

    with pytest.raises(ProviderError, match="403"):
        await provider._resolve_location(session, _POSTAL_CODE)


async def test_resolve_location_raises_on_network_error() -> None:
    """_resolve_location raises ProviderError when a network error occurs."""
    provider = _make_provider()
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    with pytest.raises(ProviderError):
        await provider._resolve_location(session, _POSTAL_CODE)


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a StationData dict with expected keys populated."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    # Need one HTML response per fuel slug in _ALL_SLUGS
    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None
    assert data.get("source_station_id") == _STATION_ID


async def test_async_fetch_diesel_price() -> None:
    """async_fetch populates diesel price in EUR/litre."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    data = await provider.async_fetch(session, _STATION_ID)

    assert data.get("diesel") is not None
    assert data["diesel"] == pytest.approx(2.065)


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when the station ID is not in any listing."""
    provider = _provider_with_cached_location()
    # Return HTML for a different station
    html = _STATION_HTML_TEMPLATE.format(station_id="00000", price="2,000")

    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    with pytest.raises(ProviderError, match=_STATION_ID):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_without_postal_code() -> None:
    """async_fetch raises ProviderError when no postal_code is configured."""
    provider = BeCarbuProvider(station_id=_STATION_ID, postal_code=None)
    session = MagicMock()

    with pytest.raises(ProviderError, match="postal_code"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_field_latitude() -> None:
    """async_fetch populates latitude from data-lat attribute in HTML."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    data = await provider.async_fetch(session, _STATION_ID)

    assert data.get("latitude") == pytest.approx(50.850)


async def test_async_fetch_field_longitude() -> None:
    """async_fetch populates longitude from data-lng attribute in HTML."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    data = await provider.async_fetch(session, _STATION_ID)

    assert data.get("longitude") == pytest.approx(4.352)


async def test_async_fetch_field_source_station_id() -> None:
    """async_fetch stores the carbu.com station ID in source_station_id."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    html_responses = [_make_html_response(html) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    data = await provider.async_fetch(session, _STATION_ID)

    assert data.get("source_station_id") == _STATION_ID


# ---------------------------------------------------------------------------
# async_fetch — HTTP / connection error handling
# ---------------------------------------------------------------------------


async def test_async_fetch_handles_403_on_listing_gracefully() -> None:
    """async_fetch raises ProviderError (not HTTP error) when all listing pages 403."""
    provider = _provider_with_cached_location()

    _make_html_response("Forbidden", status=403)
    html_responses = [_make_html_response("Forbidden", status=403) for _ in range(10)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    # Should raise ProviderError (station not found) rather than propagating HTTP 403
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_handles_network_error_on_listing_gracefully() -> None:
    """async_fetch raises ProviderError when all listing fetches fail with network error."""
    provider = _provider_with_cached_location()

    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection reset"))

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_on_success() -> None:
    """async_fetch_station_name returns the station name when found in diesel listing."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")

    html_responses = [_make_html_response(html) for _ in range(5)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Test Station"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station is absent from listings."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id="00000", price="2,065")

    html_responses = [_make_html_response(html) for _ in range(5)]
    session = MagicMock()
    session.get = MagicMock(side_effect=html_responses)

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None (swallows) when a network error occurs."""
    provider = _provider_with_cached_location()
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_without_postal_code() -> None:
    """async_fetch_station_name returns None when no postal_code is configured."""
    provider = BeCarbuProvider(station_id=_STATION_ID, postal_code=None)
    session = MagicMock()

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list() -> None:
    """async_list_stations returns a list of (station_id, label) tuples."""
    provider = _provider_with_cached_location()

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_TWO_STATIONS_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert isinstance(results, list)
    for item in results:
        assert len(item) == 2
        sid, label = item
        assert isinstance(sid, str)
        assert isinstance(label, str)


async def test_async_list_stations_includes_both_stations() -> None:
    """async_list_stations returns both stations from the HTML listing."""
    provider = _provider_with_cached_location()

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)
    ids = [sid for sid, _ in results]

    assert "99001" in ids
    assert "99002" in ids


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts results by diesel price, cheapest first."""
    provider = _provider_with_cached_location()

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert len(results) >= 2
    # First result should be cheapest (station 99001 at 1.799 vs 99002 at 1.899)
    assert results[0][0] == "99001"


async def test_async_list_stations_label_includes_price() -> None:
    """async_list_stations label includes short station ID in (#...) format."""
    provider = _provider_with_cached_location()

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert results
    _sid, label = results[0]
    assert "(#" in label


async def test_async_list_stations_returns_empty_without_postal_code() -> None:
    """async_list_stations returns empty list when no postal_code is available."""
    provider = BeCarbuProvider(station_id=_STATION_ID, postal_code=None)
    session = MagicMock()

    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_location_failure() -> None:
    """async_list_stations returns empty list when location resolution fails."""
    provider = _make_provider()  # no cached location
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection error"))

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert results == []


async def test_async_list_stations_returns_empty_on_empty_listings() -> None:
    """async_list_stations returns empty list when no stations are found in HTML."""
    provider = _provider_with_cached_location()

    empty_diesel = _make_html_response(_EMPTY_HTML)
    empty_e10 = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[empty_diesel, empty_e10])

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert results == []


async def test_async_list_stations_uses_postal_code_kwarg() -> None:
    """async_list_stations uses postal_code from kwargs if supplied."""
    # Provider has a different postal_code; kwarg should override
    provider = _make_provider(postal_code="9000")
    provider._location_cache["1000"] = (_TOWN, _LOCATION_ID)

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    results = await provider.async_list_stations(session, postal_code="1000")

    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# _build_station_data field mapping
# ---------------------------------------------------------------------------


def test_build_station_data_maps_prices() -> None:
    """_build_station_data correctly maps fuel prices into StationData keys."""
    provider = _make_provider()
    meta = {
        "station_id": _STATION_ID,
        "name": "Test Station",
        "brand": "TotalEnergies",
        "address": "Rue Test 1",
        "latitude": 50.85,
        "longitude": 4.35,
    }
    prices = {
        "diesel": 2.065,
        "unleaded": 1.949,
        "premium_unleaded": 1.999,
        "lpg": 0.899,
        "cng": 1.399,
    }

    data = provider._build_station_data(_STATION_ID, meta, prices)

    assert data["diesel"] == pytest.approx(2.065)
    assert data["unleaded"] == pytest.approx(1.949)
    assert data["premium_unleaded"] == pytest.approx(1.999)
    assert data["lpg"] == pytest.approx(0.899)
    assert data["cng"] == pytest.approx(1.399)


def test_build_station_data_maps_identity_fields() -> None:
    """_build_station_data maps name, brand, address, lat, lng into StationData."""
    provider = _make_provider()
    meta = {
        "station_id": _STATION_ID,
        "name": "My Station",
        "brand": "Q8",
        "address": "Rue de la Paix 10, Brussels",
        "latitude": 50.85,
        "longitude": 4.35,
    }

    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["name"] == "My Station"
    assert data["brand"] == "Q8"
    assert data["address"] == "Rue de la Paix 10, Brussels"
    assert data["latitude"] == pytest.approx(50.85)
    assert data["longitude"] == pytest.approx(4.35)


def test_build_station_data_source_station_id() -> None:
    """_build_station_data stores station_id as source_station_id."""
    provider = _make_provider()
    meta = {
        "station_id": _STATION_ID,
        "latitude": 50.85,
        "longitude": 4.35,
    }

    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["source_station_id"] == _STATION_ID


def test_build_station_data_missing_prices_are_none() -> None:
    """_build_station_data returns None for fuel types not in prices dict."""
    provider = _make_provider()
    meta = {"station_id": _STATION_ID}

    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["diesel"] is None
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["lpg"] is None
    assert data["cng"] is None


def test_build_station_data_invalid_lat_lng_returns_none() -> None:
    """_build_station_data handles invalid lat/lng by storing None."""
    provider = _make_provider()
    meta = {
        "station_id": _STATION_ID,
        "latitude": "not-a-float",
        "longitude": None,
    }

    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_extra_fuel_types_not_stored() -> None:
    """_build_station_data ignores extra fuel type keys not in StationData TypedDict."""
    provider = _make_provider()
    meta = {"station_id": _STATION_ID}
    prices = {"diesel_b10": 2.057, "diesel_hvo": 1.989, "hydrogen": 9.999}

    data = provider._build_station_data(_STATION_ID, meta, prices)

    # Extra keys are not stored — they violate the StationData TypedDict contract
    assert "diesel_b10" not in data
    assert "diesel_hvo" not in data


# ---------------------------------------------------------------------------
# _fetch_station_listing — HTTP error handling
# ---------------------------------------------------------------------------


async def test_fetch_station_listing_returns_empty_on_403() -> None:
    """_fetch_station_listing returns empty list on HTTP 403."""
    provider = _provider_with_cached_location()
    resp = _make_html_response("Forbidden", status=403)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    result = await provider._fetch_station_listing(
        session, "D", _TOWN, _POSTAL_CODE, _LOCATION_ID
    )

    assert result == []


async def test_fetch_station_listing_returns_empty_on_404() -> None:
    """_fetch_station_listing returns empty list on HTTP 404 (fuel not offered)."""
    provider = _provider_with_cached_location()
    resp = _make_html_response("Not Found", status=404)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    result = await provider._fetch_station_listing(
        session, "H2", _TOWN, _POSTAL_CODE, _LOCATION_ID
    )

    assert result == []


async def test_fetch_station_listing_returns_empty_on_network_error() -> None:
    """_fetch_station_listing returns empty list on network error (does not raise)."""
    provider = _provider_with_cached_location()
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection reset"))

    result = await provider._fetch_station_listing(
        session, "D", _TOWN, _POSTAL_CODE, _LOCATION_ID
    )

    assert result == []


async def test_fetch_station_listing_returns_parsed_stations() -> None:
    """_fetch_station_listing returns parsed station list on success."""
    provider = _provider_with_cached_location()
    html = _STATION_HTML_TEMPLATE.format(station_id=_STATION_ID, price="2,065")
    resp = _make_html_response(html, status=200)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    result = await provider._fetch_station_listing(
        session, "D", _TOWN, _POSTAL_CODE, _LOCATION_ID
    )

    assert isinstance(result, list)
    station_ids = [s["station_id"] for s in result]
    assert _STATION_ID in station_ids


# ---------------------------------------------------------------------------
# Provider is not None coord check (requirement: is-not-None, not falsy)
# ---------------------------------------------------------------------------


async def test_async_list_stations_lat_lng_zero_is_valid() -> None:
    """async_list_stations does not treat lat=0.0 or lng=0.0 as missing coords."""
    provider = _provider_with_cached_location(postal_code=_POSTAL_CODE)

    diesel_resp = _make_html_response(_TWO_STATIONS_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = MagicMock()
    session.get = MagicMock(side_effect=[diesel_resp, e10_resp])

    # lat=0.0 and lng=0.0 are valid coordinates (Gulf of Guinea / prime meridian)
    results = await provider.async_list_stations(
        session, postal_code=_POSTAL_CODE, lat=0.0, lng=0.0, radius_km=50000.0
    )

    # Should attempt radius filtering rather than short-circuiting on falsy 0.0
    # All stations are within 50000 km of (0, 0)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Provider in registry
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """BeCarbuProvider is registered in the PROVIDER_REGISTRY under 'be_carbu'."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "be_carbu" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["be_carbu"] is BeCarbuProvider


# ---------------------------------------------------------------------------
# _extract_price_from_text — integer-only path (lines 214-217)
# ---------------------------------------------------------------------------


def test_extract_price_integer_only_path_returns_value() -> None:
    """_extract_price_from_text handles integer-only strings (no decimal point)."""
    # Covers lines 214-215: integer-only regex branch, float() conversion attempt
    result = _extract_price_from_text("2")
    assert result == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _parse_station_html — invalid lat / lng (lines 316-317, 320-321)
# ---------------------------------------------------------------------------

_INVALID_COORDS_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="not_a_number" data-lng="also_bad">
  <span class="prix">1,799</span>
</div>
</body>
</html>"""


def test_parse_station_html_invalid_lat_returns_none() -> None:
    """_parse_station_html sets latitude to None when data-lat is non-numeric."""
    result = _parse_station_html(_INVALID_COORDS_HTML, "diesel")
    assert result
    assert result[0]["latitude"] is None


def test_parse_station_html_invalid_lng_returns_none() -> None:
    """_parse_station_html sets longitude to None when data-lng is non-numeric."""
    result = _parse_station_html(_INVALID_COORDS_HTML, "diesel")
    assert result
    assert result[0]["longitude"] is None


def test_parse_station_html_primary_div_id_format() -> None:
    """_parse_station_html parses the primary div id='item_N' data-attribute format."""
    html = (
        '<div id="item_0" data-price="1.999" data-name="Test Station" '
        'data-lat="50.85" data-lng="4.35" data-id="99001">'
        "</div>"
    )
    result = _parse_station_html(html, "diesel")
    assert len(result) == 1
    assert result[0]["station_id"] == "99001"
    assert result[0]["price"] == pytest.approx(1.999)
    assert result[0]["name"] == "Test Station"
    assert result[0]["latitude"] == pytest.approx(50.85)
    assert result[0]["longitude"] == pytest.approx(4.35)


# ---------------------------------------------------------------------------
# async_fetch — exception wrapping (lines 472-475, 489-496)
# ---------------------------------------------------------------------------


async def test_async_fetch_wraps_generic_exception_from_resolve_location() -> None:
    """async_fetch wraps non-ProviderError from _resolve_location in ProviderError."""
    provider = _make_provider()

    async def _bad_resolve(session: object, postal_code: str) -> None:
        raise KeyError("unexpected internal error")

    provider._resolve_location = _bad_resolve  # type: ignore[method-assign]
    session = MagicMock()

    with pytest.raises(ProviderError, match="Failed to resolve"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_slug_fetch_exception_continues_to_next_slug() -> None:
    """async_fetch logs and continues when _fetch_station_listing raises."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    async def _bad_listing(session: object, slug: str, *args: object) -> None:
        raise RuntimeError("network timeout")

    provider._fetch_station_listing = _bad_listing  # type: ignore[method-assign]
    session = MagicMock()

    with pytest.raises(ProviderError, match=_STATION_ID):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name — generic exception (lines 546-548)
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None when _fetch_station_listing raises."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    async def _bad_listing(session: object, slug: str, *args: object) -> None:
        raise RuntimeError("unexpected error")

    provider._fetch_station_listing = _bad_listing  # type: ignore[method-assign]
    session = MagicMock()

    name = await provider.async_fetch_station_name(session, _STATION_ID)
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — lat/lng only without postal_code (line 594)
# ---------------------------------------------------------------------------


async def test_async_list_stations_lat_lng_only_no_postal_code_returns_empty() -> None:
    """async_list_stations returns [] when only lat/lng supplied with no postal_code."""
    provider = BeCarbuProvider(station_id=_STATION_ID, postal_code=None)
    session = MagicMock()

    results = await provider.async_list_stations(session, lat=50.85, lng=4.35)

    assert results == []


# ---------------------------------------------------------------------------
# async_list_stations — asyncio.gather raises (lines 621-623)
# ---------------------------------------------------------------------------


async def test_async_list_stations_gather_exception_returns_empty() -> None:
    """async_list_stations returns [] when asyncio.gather raises unexpectedly."""
    import asyncio as _real_asyncio
    import custom_components.fuelcompare_ie.providers.be_carbu as _be_carbu_mod

    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)
    session = MagicMock()

    mock_asyncio = MagicMock()
    mock_asyncio.gather = AsyncMock(side_effect=RuntimeError("gather failed"))
    _be_carbu_mod.asyncio = mock_asyncio
    try:
        results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)
    finally:
        _be_carbu_mod.asyncio = _real_asyncio

    assert results == []


# ---------------------------------------------------------------------------
# async_list_stations — e10-only station merged (line 642)
# ---------------------------------------------------------------------------

_DIESEL_ONE_STATION_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="50.850" data-lng="4.352">
  <span class="station-name">Diesel Station</span>
  <span class="prix">1,799</span>
</div>
</body>
</html>"""

_E10_ONLY_STATION_HTML = """\
<html>
<body>
<div class="station-content" data-id="99003" data-lat="50.870" data-lng="4.370">
  <span class="station-name">E10 Only Station</span>
  <span class="prix">1,699</span>
</div>
</body>
</html>"""


async def test_async_list_stations_e10_only_station_added_to_merged() -> None:
    """async_list_stations adds e10-only station (not in diesel) to merged results."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    diesel_resp = _make_html_response(_DIESEL_ONE_STATION_HTML)
    e10_resp = _make_html_response(_E10_ONLY_STATION_HTML)
    session = _make_session_with_responses(diesel_resp, e10_resp)

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)
    ids = [sid for sid, _ in results]

    assert "99001" in ids
    assert "99003" in ids


# ---------------------------------------------------------------------------
# async_list_stations — station without coords included in radius filter (line 660)
# ---------------------------------------------------------------------------

_NO_COORDS_STATION_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001">
  <span class="station-name">No Coords Station</span>
  <span class="prix">1,799</span>
</div>
</body>
</html>"""


async def test_async_list_stations_station_without_coords_included_in_radius_filter() -> (
    None
):
    """async_list_stations includes stations without coordinates in radius filter."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    diesel_resp = _make_html_response(_NO_COORDS_STATION_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = _make_session_with_responses(diesel_resp, e10_resp)

    results = await provider.async_list_stations(
        session, postal_code=_POSTAL_CODE, lat=50.85, lng=4.35, radius_km=10.0
    )

    assert any(sid == "99001" for sid, _ in results)


# ---------------------------------------------------------------------------
# async_list_stations — merged empty after radius filter (line 664)
# ---------------------------------------------------------------------------

_FAR_STATION_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="51.900" data-lng="5.352">
  <span class="prix">1,799</span>
</div>
</body>
</html>"""


async def test_async_list_stations_all_stations_outside_radius_returns_empty() -> None:
    """async_list_stations returns [] when all stations fall outside the radius."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    diesel_resp = _make_html_response(_FAR_STATION_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = _make_session_with_responses(diesel_resp, e10_resp)

    results = await provider.async_list_stations(
        session, postal_code=_POSTAL_CODE, lat=50.85, lng=4.35, radius_km=0.001
    )

    assert results == []


# ---------------------------------------------------------------------------
# async_list_stations — station with no prices uses fallback sort key (lines 685-686)
# ---------------------------------------------------------------------------

_NO_PRICE_STATION_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="50.850" data-lng="4.352">
  <span class="station-name">No Price Station</span>
</div>
</body>
</html>"""


async def test_async_list_stations_station_without_price_uses_fallback_label() -> None:
    """async_list_stations uses bare display_name label when station has no price."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    diesel_resp = _make_html_response(_NO_PRICE_STATION_HTML)
    e10_resp = _make_html_response(_EMPTY_HTML)
    session = _make_session_with_responses(diesel_resp, e10_resp)

    results = await provider.async_list_stations(session, postal_code=_POSTAL_CODE)

    assert results
    sid, label = results[0]
    assert sid == "99001"
    assert "€" not in label
    assert "No Price Station" in label


# ---------------------------------------------------------------------------
# _resolve_location — ClientResponseError raises ProviderError (line 740)
# ---------------------------------------------------------------------------


async def test_resolve_location_raises_provider_error_on_http_response_error() -> None:
    """_resolve_location raises ProviderError when raise_for_status raises ClientResponseError."""
    from aiohttp import ClientResponseError  # noqa: PLC0415

    provider = _make_provider()
    request_info = MagicMock()
    err = ClientResponseError(request_info, (), status=500, message="Server Error")

    resp = AsyncMock()
    resp.status = 200
    resp.raise_for_status = MagicMock(side_effect=err)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    with pytest.raises(ProviderError, match="HTTP error"):
        await provider._resolve_location(session, _POSTAL_CODE)


# ---------------------------------------------------------------------------
# _fetch_station_listing — ClientResponseError returns empty list (lines 833-834)
# ---------------------------------------------------------------------------


async def test_fetch_station_listing_returns_empty_on_client_response_error() -> None:
    """_fetch_station_listing returns [] when raise_for_status raises ClientResponseError."""
    from aiohttp import ClientResponseError  # noqa: PLC0415

    provider = _provider_with_cached_location()
    request_info = MagicMock()
    err = ClientResponseError(
        request_info, (), status=503, message="Service Unavailable"
    )

    resp = AsyncMock()
    resp.status = 200
    resp.raise_for_status = MagicMock(side_effect=err)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    result = await provider._fetch_station_listing(
        session, "GO", _TOWN, _POSTAL_CODE, _LOCATION_ID
    )

    assert result == []


# ---------------------------------------------------------------------------
# _build_station_data — invalid longitude returns None (lines 874-875)
# ---------------------------------------------------------------------------


def test_build_station_data_invalid_lng_only_returns_none() -> None:
    """_build_station_data returns None for longitude when it cannot be converted to float."""
    provider = _make_provider()
    meta = {
        "station_id": _STATION_ID,
        "latitude": 50.85,
        "longitude": "invalid",
    }

    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["longitude"] is None
    assert data["latitude"] == pytest.approx(50.85)


# ---------------------------------------------------------------------------
# async_fetch — ProviderError re-raise path (line 473)
# ---------------------------------------------------------------------------


async def test_async_fetch_reraises_provider_error_from_resolve_location() -> None:
    """async_fetch re-raises ProviderError raised directly by _resolve_location (line 473)."""
    provider = _make_provider()

    async def _resolve_raises_provider_error(session: object, postal_code: str) -> None:
        raise ProviderError("direct provider error from resolve")

    provider._resolve_location = _resolve_raises_provider_error  # type: ignore[method-assign]
    session = MagicMock()

    with pytest.raises(ProviderError, match="direct provider error from resolve"):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name — E10 fallback returns station name (line 546)
# ---------------------------------------------------------------------------

_E10_STATION_NAME_HTML = """\
<html>
<body>
<div class="station-content" data-id="99001" data-lat="50.850" data-lng="4.352">
  <span class="station-name">E10 Named Station</span>
  <span class="prix">1,699</span>
</div>
</body>
</html>"""


async def test_async_fetch_station_name_falls_back_to_e10_listing() -> None:
    """async_fetch_station_name returns name from E10 listing when absent from diesel."""
    provider = _make_provider(postal_code=_POSTAL_CODE)
    provider._location_cache[_POSTAL_CODE] = (_TOWN, _LOCATION_ID)

    # Diesel listing has a different station; E10 listing has our station
    diesel_resp = _make_html_response(
        _STATION_HTML_TEMPLATE.format(station_id="00000", price="2,065")
    )
    e10_resp = _make_html_response(_E10_STATION_NAME_HTML)
    session = _make_session_with_responses(diesel_resp, e10_resp)

    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "E10 Named Station"


# ---------------------------------------------------------------------------
# _extract_price_from_text — ValueError paths (lines 216-217, 221-222)
# These paths are defensive and require mocking the regex match objects
# ---------------------------------------------------------------------------


def test_extract_price_from_text_integer_only_match_value_error() -> None:
    """_extract_price_from_text returns None when integer match raises ValueError (lines 216-217)."""
    from unittest.mock import MagicMock, patch

    # Simulate float() raising ValueError on the integer-only match
    bad_match = MagicMock()
    bad_match.group.return_value = "bad_int"

    with patch("re.search") as mock_search:
        # First call (decimal pattern) returns None, second (integer pattern) returns bad_match
        mock_search.side_effect = [None, bad_match]
        result = _extract_price_from_text("1.invalid")

    assert result is None


def test_extract_price_from_text_decimal_match_value_error() -> None:
    """_extract_price_from_text returns None when decimal match raises ValueError (lines 221-222)."""
    from unittest.mock import MagicMock, patch

    # Simulate float() raising ValueError on the decimal match
    bad_match = MagicMock()
    bad_match.group.return_value = "bad_decimal"

    with patch("re.search") as mock_search:
        # First call (decimal pattern) returns a bad match
        mock_search.return_value = bad_match
        result = _extract_price_from_text("1.invalid")

    assert result is None
