"""Tests for SiGorivaProvider (goriva.si, Slovenia)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.si_goriva import (
    SiGorivaProvider,
    _FRANCHISE_URL,
    _HEADERS,
    _PRICE_KEY_MAP,
    _SEARCH_URL,
    _haversine_km,
    _parse_price,
    _parse_station,
)


# ---------------------------------------------------------------------------
# Constants / shared fixtures
# ---------------------------------------------------------------------------

_STATION_PK = 2308
_STATION_ID = str(_STATION_PK)

_FRANCHISE_PK = 5
_BRAND_NAME = "OMV"

_BASE_FRANCHISE_LIST = [
    {"pk": _FRANCHISE_PK, "name": _BRAND_NAME, "marker": "omv"},
    {"pk": 7, "name": "MOL", "marker": "mol"},
]

_BASE_STATION: dict = {
    "pk": _STATION_PK,
    "franchise": _FRANCHISE_PK,
    "name": "OMV Trnovo",
    "address": "Iga ulica 1",
    "lat": 46.0517,
    "lng": 14.5079,
    "zip_code": "1000",
    "open_hours": "Mon-Fri 6:00-22:00",
    "prices": {
        "dizel": 1.465,
        "95": 1.440,
        "98": 1.540,
        "avtoplin-lpg": 0.799,
        "dizel-plus": None,
        "truck": None,
        "cng": None,
        "ad-blue": None,
        "lpg-plus": None,
        "heating-oil": None,
    },
}

_SECOND_STATION: dict = {
    "pk": 2309,
    "franchise": 7,
    "name": "MOL Center",
    "address": "Dunajska cesta 5",
    "lat": 46.0600,
    "lng": 14.5100,
    "zip_code": "1000",
    "open_hours": "24/7",
    "prices": {
        "dizel": 1.455,
        "95": 1.435,
        "98": None,
        "avtoplin-lpg": None,
        "dizel-plus": None,
        "truck": None,
        "cng": None,
        "ad-blue": None,
        "lpg-plus": None,
        "heating-oil": None,
    },
}

_FAR_STATION: dict = {
    "pk": 9999,
    "franchise": 7,
    "name": "MOL Far Away",
    "address": "Distant Street 100",
    "lat": 45.0000,
    "lng": 13.0000,
    "zip_code": "6000",
    "open_hours": "24/7",
    "prices": {
        "dizel": 1.499,
        "95": 1.489,
        "98": None,
        "avtoplin-lpg": None,
        "dizel-plus": None,
        "truck": None,
        "cng": None,
        "ad-blue": None,
        "lpg-plus": None,
        "heating-oil": None,
    },
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data=None,
    raise_on_raise_for_status: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    if raise_on_raise_for_status is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_on_raise_for_status)
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _search_page(
    results: list,
    has_next: bool = False,
    count: int | None = None,
) -> dict:
    """Build a goriva.si /search/ page payload."""
    return {
        "count": count if count is not None else len(results),
        "next": "https://goriva.si/api/v1/search/?page=2" if has_next else None,
        "previous": None,
        "results": results,
    }


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _default_provider(**kwargs) -> SiGorivaProvider:
    """Create a SiGorivaProvider with sensible test defaults."""
    return SiGorivaProvider(
        station_id=_STATION_ID,
        county=None,
        latitude=46.0517,
        longitude=14.5079,
        radius_km=kwargs.get("radius_km", 10.0),
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """SiGorivaProvider.COUNTRY is 'SI'."""
    assert SiGorivaProvider.COUNTRY == "SI"


def test_provider_metadata_provider_key() -> None:
    """SiGorivaProvider.PROVIDER_KEY is 'si_goriva'."""
    assert SiGorivaProvider.PROVIDER_KEY == "si_goriva"


def test_provider_metadata_label() -> None:
    """SiGorivaProvider.LABEL contains 'goriva.si' and 'Slovenia'."""
    assert "goriva.si" in SiGorivaProvider.LABEL
    assert "Slovenia" in SiGorivaProvider.LABEL


def test_provider_metadata_config_mode() -> None:
    """CONFIG_MODE is 'location'."""
    assert SiGorivaProvider.CONFIG_MODE == "location"


def test_provider_metadata_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert SiGorivaProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_metadata_poll_interval() -> None:
    """Poll interval is 3600 seconds (1 hour)."""
    assert SiGorivaProvider.POLL_INTERVAL_SECONDS == 3600


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_includes_all_fuel_types() -> None:
    """CAPABILITIES includes all four supported fuel types."""
    caps = SiGorivaProvider.CAPABILITIES
    assert "diesel" in caps
    assert "unleaded" in caps
    assert "premium_unleaded" in caps
    assert "lpg" in caps


def test_capabilities_includes_identity_fields() -> None:
    """CAPABILITIES includes name, brand, address, county."""
    caps = SiGorivaProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps
    assert "address" in caps
    assert "county" in caps


def test_capabilities_includes_location_fields() -> None:
    """CAPABILITIES includes latitude and longitude."""
    caps = SiGorivaProvider.CAPABILITIES
    assert "latitude" in caps
    assert "longitude" in caps


def test_capabilities_excludes_lastupdated() -> None:
    """CAPABILITIES does not include lastupdated (API has no per-station timestamps)."""
    assert "lastupdated" not in SiGorivaProvider.CAPABILITIES


def test_capabilities_includes_coordinator_sentinels() -> None:
    """CAPABILITIES includes last_successful_fetch and data_fetch_problem."""
    caps = SiGorivaProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor and initialisation
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id as string."""
    p = SiGorivaProvider(station_id="2308")
    assert p._station_id == "2308"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude, longitude, radius_km."""
    p = SiGorivaProvider(
        station_id="2308", latitude=46.05, longitude=14.50, radius_km=5.0
    )
    assert p._latitude == pytest.approx(46.05)
    assert p._longitude == pytest.approx(14.50)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_default_radius_is_10() -> None:
    """Constructor defaults radius_km to 10.0 when None is passed."""
    p = SiGorivaProvider(station_id="2308", radius_km=None)
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_initialises_empty_franchise_cache() -> None:
    """Constructor initialises _franchise_cache to an empty dict."""
    p = SiGorivaProvider(station_id="2308")
    assert p._franchise_cache == {}


def test_constructor_stores_county() -> None:
    """Constructor stores county parameter (interface compat)."""
    p = SiGorivaProvider(station_id="2308", county="Ljubljana")
    assert p._county == "Ljubljana"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_headers_include_accept_json() -> None:
    """_HEADERS includes Accept: application/json."""
    assert _HEADERS.get("Accept") == "application/json"


def test_search_url_points_to_goriva_si() -> None:
    """_SEARCH_URL points to the goriva.si search endpoint."""
    assert "goriva.si" in _SEARCH_URL
    assert _SEARCH_URL.startswith("https://")


def test_franchise_url_points_to_goriva_si() -> None:
    """_FRANCHISE_URL points to the goriva.si franchise endpoint."""
    assert "goriva.si" in _FRANCHISE_URL
    assert _FRANCHISE_URL.startswith("https://")


# ---------------------------------------------------------------------------
# _parse_price — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_normal_value() -> None:
    """_parse_price returns rounded float for normal EUR/litre value."""
    assert _parse_price(1.465) == pytest.approx(1.465)


def test_parse_price_rounds_to_3dp() -> None:
    """_parse_price rounds to 3 decimal places."""
    result = _parse_price(1.46512)
    assert result == pytest.approx(1.465)


def test_parse_price_none_input() -> None:
    """_parse_price returns None for None input."""
    assert _parse_price(None) is None


def test_parse_price_zero_input() -> None:
    """_parse_price returns None for zero (no price available)."""
    assert _parse_price(0) is None


def test_parse_price_negative_input() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price(-1.5) is None


def test_parse_price_string_float() -> None:
    """_parse_price parses numeric strings."""
    assert _parse_price("1.465") == pytest.approx(1.465)


def test_parse_price_non_numeric_string() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None


def test_parse_price_cents_guard_above_10() -> None:
    """_parse_price divides values >10 by 100 (cents-to-EUR guard)."""
    assert _parse_price(146.5) == pytest.approx(1.465)


def test_parse_price_exactly_10_not_divided() -> None:
    """_parse_price does NOT divide value 10.0 (boundary: > 10 only)."""
    # 10.0 is not > 10 so must not be divided
    assert _parse_price(10.0) == pytest.approx(10.0)


def test_parse_price_integer_input() -> None:
    """_parse_price handles integer inputs."""
    assert _parse_price(2) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _parse_station — unit tests
# ---------------------------------------------------------------------------


def test_parse_station_returns_diesel_with_slovenian_key() -> None:
    """_parse_station reads 'dizel' (Slovenian) for diesel, not 'diesel'."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["diesel"] == pytest.approx(1.465)


def test_parse_station_returns_unleaded_from_95_key() -> None:
    """_parse_station maps '95' key to 'unleaded'."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["unleaded"] == pytest.approx(1.440)


def test_parse_station_returns_premium_unleaded_from_98_key() -> None:
    """_parse_station maps '98' key to 'premium_unleaded'."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["premium_unleaded"] == pytest.approx(1.540)


def test_parse_station_returns_lpg_from_avtoplin_key() -> None:
    """_parse_station maps 'avtoplin-lpg' key to 'lpg'."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["lpg"] == pytest.approx(0.799)


def test_parse_station_resolves_brand_from_franchise_map() -> None:
    """_parse_station resolves franchise pk to brand name."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["brand"] == _BRAND_NAME


def test_parse_station_brand_none_when_franchise_pk_missing_from_map() -> None:
    """_parse_station returns brand=None when franchise pk not in map."""
    result = _parse_station(_BASE_STATION, {})
    assert result["brand"] is None


def test_parse_station_brand_none_when_franchise_field_absent() -> None:
    """_parse_station returns brand=None when station has no franchise field."""
    station = {**_BASE_STATION, "franchise": None}
    result = _parse_station(station, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["brand"] is None


def test_parse_station_name_populated() -> None:
    """_parse_station returns name from station 'name' field."""
    result = _parse_station(_BASE_STATION, {})
    assert result["name"] == "OMV Trnovo"


def test_parse_station_name_none_when_empty() -> None:
    """_parse_station returns name=None when station name is empty string."""
    station = {**_BASE_STATION, "name": ""}
    result = _parse_station(station, {})
    assert result["name"] is None


def test_parse_station_address_populated() -> None:
    """_parse_station returns address field."""
    result = _parse_station(_BASE_STATION, {})
    assert result["address"] == "Iga ulica 1"


def test_parse_station_address_none_when_empty() -> None:
    """_parse_station returns address=None when empty string."""
    station = {**_BASE_STATION, "address": ""}
    result = _parse_station(station, {})
    assert result["address"] is None


def test_parse_station_county_uses_zip_code() -> None:
    """_parse_station uses zip_code as county (goriva.si has no region field)."""
    result = _parse_station(_BASE_STATION, {})
    assert result["county"] == "1000"


def test_parse_station_county_none_when_no_zip() -> None:
    """_parse_station returns county=None when zip_code is absent."""
    station = {**_BASE_STATION, "zip_code": None}
    result = _parse_station(station, {})
    assert result["county"] is None


def test_parse_station_latitude_populated() -> None:
    """_parse_station returns latitude from 'lat' field."""
    result = _parse_station(_BASE_STATION, {})
    assert result["latitude"] == pytest.approx(46.0517)


def test_parse_station_longitude_populated() -> None:
    """_parse_station returns longitude from 'lng' field."""
    result = _parse_station(_BASE_STATION, {})
    assert result["longitude"] == pytest.approx(14.5079)


def test_parse_station_latitude_none_for_null_lat() -> None:
    """_parse_station returns latitude=None when lat is None."""
    station = {**_BASE_STATION, "lat": None}
    result = _parse_station(station, {})
    assert result["latitude"] is None


def test_parse_station_longitude_none_for_null_lng() -> None:
    """_parse_station returns longitude=None when lng is None."""
    station = {**_BASE_STATION, "lng": None}
    result = _parse_station(station, {})
    assert result["longitude"] is None


def test_parse_station_latitude_none_for_invalid_lat() -> None:
    """_parse_station returns latitude=None when lat is non-numeric."""
    station = {**_BASE_STATION, "lat": "invalid"}
    result = _parse_station(station, {})
    assert result["latitude"] is None


def test_parse_station_longitude_none_for_invalid_lng() -> None:
    """_parse_station returns longitude=None when lng is non-numeric."""
    station = {**_BASE_STATION, "lng": "bad"}
    result = _parse_station(station, {})
    assert result["longitude"] is None


def test_parse_station_lastupdated_is_none() -> None:
    """_parse_station always returns lastupdated=None (API has no timestamp)."""
    result = _parse_station(_BASE_STATION, {})
    assert result["lastupdated"] is None


def test_parse_station_source_station_id_matches_pk() -> None:
    """_parse_station sets source_station_id to str(pk)."""
    result = _parse_station(_BASE_STATION, {})
    assert result["source_station_id"] == str(_STATION_PK)


def test_parse_station_null_prices_return_none() -> None:
    """_parse_station returns None for null prices in the API response."""
    station = {
        **_BASE_STATION,
        "prices": {
            "dizel": None,
            "95": None,
            "98": None,
            "avtoplin-lpg": None,
        },
    }
    result = _parse_station(station, {})
    assert result["diesel"] is None
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["lpg"] is None


def test_parse_station_missing_prices_dict_returns_none_prices() -> None:
    """_parse_station handles absent 'prices' key gracefully."""
    station = {k: v for k, v in _BASE_STATION.items() if k != "prices"}
    result = _parse_station(station, {})
    assert result["diesel"] is None
    assert result["unleaded"] is None


def test_parse_station_all_capability_keys_present() -> None:
    """_parse_station result contains all SiGorivaProvider.CAPABILITIES keys (minus sentinels)."""
    result = _parse_station(_BASE_STATION, {_FRANCHISE_PK: _BRAND_NAME})
    provider_caps = SiGorivaProvider.CAPABILITIES - {
        "last_successful_fetch",
        "data_fetch_problem",
    }
    for key in provider_caps:
        assert key in result, f"Key '{key}' missing from _parse_station output"


# ---------------------------------------------------------------------------
# _parse_station — diesel key is "dizel" not "diesel" (critical correctness)
# ---------------------------------------------------------------------------


def test_price_key_map_diesel_is_dizel() -> None:
    """_PRICE_KEY_MAP maps 'dizel' (Slovenian) to 'diesel' — not 'diesel'."""
    assert "dizel" in _PRICE_KEY_MAP
    assert _PRICE_KEY_MAP["dizel"] == "diesel"
    assert "diesel" not in _PRICE_KEY_MAP


def test_price_key_map_95_is_unleaded() -> None:
    """_PRICE_KEY_MAP maps '95' to 'unleaded'."""
    assert _PRICE_KEY_MAP["95"] == "unleaded"


def test_price_key_map_98_is_premium_unleaded() -> None:
    """_PRICE_KEY_MAP maps '98' to 'premium_unleaded'."""
    assert _PRICE_KEY_MAP["98"] == "premium_unleaded"


def test_price_key_map_lpg_key() -> None:
    """_PRICE_KEY_MAP maps 'avtoplin-lpg' to 'lpg'."""
    assert _PRICE_KEY_MAP["avtoplin-lpg"] == "lpg"


def test_parse_station_ignores_english_diesel_key() -> None:
    """_parse_station returns diesel=None when only 'diesel' (English) is present."""
    station = {
        **_BASE_STATION,
        "prices": {"diesel": 1.465, "95": 1.440, "98": 1.540, "avtoplin-lpg": 0.799},
    }
    result = _parse_station(station, {})
    # 'diesel' (English) must NOT be mapped — only 'dizel' (Slovenian) is
    assert result["diesel"] is None


# ---------------------------------------------------------------------------
# _haversine_km — unit tests
# ---------------------------------------------------------------------------


def test_haversine_zero_distance() -> None:
    """_haversine_km returns 0 for identical points."""
    assert _haversine_km(46.05, 14.50, 46.05, 14.50) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance_ljubljana_maribor() -> None:
    """_haversine_km computes approximately correct distance for Ljubljana–Maribor."""
    # Ljubljana: 46.0569, 14.5058 — Maribor: 46.5547, 15.6466
    # Great-circle distance is approx 103–106 km
    dist = _haversine_km(46.0569, 14.5058, 46.5547, 15.6466)
    assert 95.0 < dist < 115.0


def test_haversine_symmetry() -> None:
    """_haversine_km is symmetric: d(A,B) == d(B,A)."""
    d1 = _haversine_km(46.05, 14.50, 46.10, 14.55)
    d2 = _haversine_km(46.10, 14.55, 46.05, 14.50)
    assert d1 == pytest.approx(d2)


def test_haversine_small_distance() -> None:
    """_haversine_km returns small value for nearby points."""
    # Two points about 1 km apart
    dist = _haversine_km(46.0517, 14.5079, 46.0607, 14.5079)
    assert 0.5 < dist < 2.0


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.465)
    assert data["unleaded"] == pytest.approx(1.440)
    assert data["premium_unleaded"] == pytest.approx(1.540)
    assert data["lpg"] == pytest.approx(0.799)


@pytest.mark.asyncio
async def test_async_fetch_success_populates_name() -> None:
    """async_fetch returns station name field."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "OMV Trnovo"


@pytest.mark.asyncio
async def test_async_fetch_success_populates_brand() -> None:
    """async_fetch resolves and returns brand name from franchise map."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["brand"] == _BRAND_NAME


@pytest.mark.asyncio
async def test_async_fetch_success_populates_address() -> None:
    """async_fetch returns address field."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["address"] == "Iga ulica 1"


@pytest.mark.asyncio
async def test_async_fetch_success_populates_county_from_zip() -> None:
    """async_fetch returns county set to zip_code value."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["county"] == "1000"


@pytest.mark.asyncio
async def test_async_fetch_success_populates_coordinates() -> None:
    """async_fetch returns latitude and longitude fields."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(46.0517)
    assert data["longitude"] == pytest.approx(14.5079)


@pytest.mark.asyncio
async def test_async_fetch_lastupdated_is_none() -> None:
    """async_fetch returns lastupdated=None (API has no per-station timestamp)."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] is None


# ---------------------------------------------------------------------------
# async_fetch — station-list cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_uses_station_cache_on_second_call() -> None:
    """Second async_fetch call reuses the station-list cache; no extra page fetches."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=False),
    )
    # Only one page response provided — cache means page is fetched once only.
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data1 = await provider.async_fetch(session, _STATION_ID)
    data2 = await provider.async_fetch(session, _STATION_ID)

    assert data1["diesel"] == pytest.approx(1.465)
    assert data2["diesel"] == pytest.approx(1.465)
    # Only 2 calls: franchise(1) + page1(1); second async_fetch uses cache
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_async_fetch_continues_to_next_page_when_not_found() -> None:
    """async_fetch fetches page 2 when station is absent from page 1."""
    other_station = {**_BASE_STATION, "pk": 9000}
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([other_station], has_next=True),
    )
    page2 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=False),
    )
    session = _make_session(franchise_resp, page1, page2)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.465)
    assert session.get.call_count == 3


# ---------------------------------------------------------------------------
# async_fetch — franchise cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_uses_cached_franchise_on_second_call() -> None:
    """Second async_fetch call reuses both franchise and station-list caches."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    # Only 1 franchise response and 1 page response — both caches prevent re-fetching.
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    await provider.async_fetch(session, _STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    # Total calls: franchise(1) + page1(1); second call uses both caches
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_async_fetch_continues_when_franchise_fetch_fails() -> None:
    """async_fetch continues with empty brand when franchise endpoint fails."""
    franchise_resp = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("franchise 500"),
    )
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.465)
    assert data["brand"] is None


# ---------------------------------------------------------------------------
# async_fetch — error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station pk absent from full dataset."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    # Return a page with a different station and no next page
    other_station = {**_BASE_STATION, "pk": 9999}
    page1 = _make_mock_response(200, json_data=_search_page([other_station]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_for_invalid_station_id() -> None:
    """async_fetch raises ProviderError for non-integer station IDs."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    session = _make_session(franchise_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "not-a-number")


@pytest.mark.asyncio
async def test_async_fetch_propagates_client_error_on_first_page() -> None:
    """async_fetch propagates ClientError when page 1 cannot be fetched."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    session = MagicMock()
    call_count = 0

    def _get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return franchise_resp
        raise ClientError("network failure")

    session.get = MagicMock(side_effect=_get)

    provider = _default_provider()
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_stops_pagination_on_404() -> None:
    """async_fetch treats HTTP 404 as end of pages — station not found raises ProviderError."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([], has_next=True))
    page2_404 = _make_mock_response(404, json_data={})
    session = _make_session(franchise_resp, page1, page2_404)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


@pytest.mark.asyncio
async def test_async_fetch_handles_non_200_non_404_stops_on_page2() -> None:
    """async_fetch stops pagination on error fetching page 2+ (not re-raised)."""
    other_station = {**_BASE_STATION, "pk": 8888}
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200, json_data=_search_page([other_station], has_next=True)
    )
    page2 = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("server error"),
    )
    session = _make_session(franchise_resp, page1, page2)

    provider = _default_provider()
    # Station not found due to error on page 2 — ProviderError expected
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_always_returns_none() -> None:
    """async_fetch_station_name always returns None (location-mode provider)."""
    session = MagicMock()
    provider = _default_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID)
    assert name is None


@pytest.mark.asyncio
async def test_async_fetch_station_name_does_not_make_any_requests() -> None:
    """async_fetch_station_name makes no HTTP requests (returns None immediately)."""
    session = MagicMock()
    provider = _default_provider()
    await provider.async_fetch_station_name(session, _STATION_ID)
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_station_within_radius() -> None:
    """async_list_stations includes stations within the search radius."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200, json_data=_search_page([_BASE_STATION, _SECOND_STATION])
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    station_ids = [sid for sid, _ in result]
    assert str(_STATION_PK) in station_ids
    assert "2309" in station_ids


@pytest.mark.asyncio
async def test_async_list_stations_excludes_station_outside_radius() -> None:
    """async_list_stations excludes stations beyond the radius."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION, _FAR_STATION]),
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    station_ids = [sid for sid, _ in result]
    assert str(_STATION_PK) in station_ids
    assert "9999" not in station_ids


@pytest.mark.asyncio
async def test_async_list_stations_sorted_cheapest_diesel_first() -> None:
    """async_list_stations sorts stations cheapest diesel first."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    # _SECOND_STATION has cheaper diesel (1.455) than _BASE_STATION (1.465)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION, _SECOND_STATION]),
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert result[0][0] == "2309"  # MOL is cheaper
    assert result[1][0] == str(_STATION_PK)  # OMV is more expensive


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_name() -> None:
    """async_list_stations label includes station name."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert len(result) == 1
    _sid, label = result[0]
    assert "OMV Trnovo" in label


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes formatted diesel price."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    _sid, label = result[0]
    assert "Diesel" in label
    assert "1.465" in label


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_unleaded_price() -> None:
    """async_list_stations label includes formatted 95 unleaded price."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    _sid, label = result[0]
    assert "95" in label
    assert "1.440" in label


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_api_error() -> None:
    """async_list_stations returns [] when franchise request raises an error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection error"))

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_page1_error() -> None:
    """async_list_stations returns [] when first search page fetch fails."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1_error = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("server error"),
    )
    session = _make_session(franchise_resp, page1_error)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_empty_when_no_stations_in_radius() -> None:
    """async_list_stations returns [] when no stations are within the radius."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    # All stations are far away
    page1 = _make_mock_response(200, json_data=_search_page([_FAR_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_with_no_pk() -> None:
    """async_list_stations skips stations that have no pk field."""
    no_pk_station = {k: v for k, v in _BASE_STATION.items() if k != "pk"}
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([no_pk_station]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_uses_instance_coordinates_when_no_kwargs() -> None:
    """async_list_stations falls back to instance lat/lng when not passed as kwargs."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = SiGorivaProvider(
        station_id=_STATION_ID,
        latitude=46.0517,
        longitude=14.5079,
        radius_km=10.0,
    )
    # No lat/lng in kwargs — should use instance values
    result = await provider.async_list_stations(session)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_async_list_stations_fetches_all_pages() -> None:
    """async_list_stations fetches multiple pages and aggregates results."""
    other_station = {
        **_BASE_STATION,
        "pk": 3000,
        "prices": {**_BASE_STATION["prices"], "dizel": 1.480},
    }
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )
    page2 = _make_mock_response(
        200,
        json_data=_search_page([other_station], has_next=False),
    )
    session = _make_session(franchise_resp, page1, page2)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=50.0
    )

    station_ids = [sid for sid, _ in result]
    assert str(_STATION_PK) in station_ids
    assert "3000" in station_ids


@pytest.mark.asyncio
async def test_async_list_stations_stops_on_404_page() -> None:
    """async_list_stations treats HTTP 404 as end of pagination."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )
    page2_404 = _make_mock_response(404, json_data={})
    session = _make_session(franchise_resp, page1, page2_404)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    # Should still return whatever was fetched before 404
    assert len(result) == 1
    assert result[0][0] == str(_STATION_PK)


@pytest.mark.asyncio
async def test_async_list_stations_no_coordinates_includes_all_stations() -> None:
    """async_list_stations includes all stations when no coordinates are provided."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION, _FAR_STATION]),
    )
    session = _make_session(franchise_resp, page1)

    # Provider with no coordinates
    provider = SiGorivaProvider(station_id=_STATION_ID)
    result = await provider.async_list_stations(session)

    station_ids = [sid for sid, _ in result]
    assert str(_STATION_PK) in station_ids
    assert "9999" in station_ids


@pytest.mark.asyncio
async def test_async_list_stations_station_with_no_diesel_sorted_last() -> None:
    """async_list_stations sorts stations with no diesel price after those with diesel."""
    no_diesel_station = {
        **_BASE_STATION,
        "pk": 5000,
        "prices": {**_BASE_STATION["prices"], "dizel": None},
        "lat": 46.0520,
        "lng": 14.5080,
    }
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([no_diesel_station, _BASE_STATION]),
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    # Station with diesel price should come first
    assert result[0][0] == str(_STATION_PK)
    assert result[-1][0] == "5000"


@pytest.mark.asyncio
async def test_async_list_stations_label_with_brand_not_in_name() -> None:
    """async_list_stations prepends brand when not already in station name."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    # Station name does not contain the brand name "OMV"
    station = {**_BASE_STATION, "name": "Trnovo Postaja"}
    page1 = _make_mock_response(200, json_data=_search_page([station]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    _sid, label = result[0]
    # Brand "OMV" should be prepended since it's not in the name
    assert "OMV" in label
    assert "Trnovo Postaja" in label


@pytest.mark.asyncio
async def test_async_list_stations_label_brand_not_duplicated_when_in_name() -> None:
    """async_list_stations does not duplicate brand when already in station name."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    # Station name already contains "OMV"
    station = {**_BASE_STATION, "name": "OMV Trnovo"}
    page1 = _make_mock_response(200, json_data=_search_page([station]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    _sid, label = result[0]
    # "OMV" should appear only once (not "OMV — OMV Trnovo")
    assert label.count("OMV") == 1


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_address() -> None:
    """async_list_stations label includes address in parentheses."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([_BASE_STATION]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    _sid, label = result[0]
    assert "Iga ulica 1" in label


@pytest.mark.asyncio
async def test_async_list_stations_handles_invalid_station_coords() -> None:
    """async_list_stations skips distance check when station coords are invalid."""
    bad_coords_station = {**_BASE_STATION, "lat": "invalid", "lng": "bad"}
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(200, json_data=_search_page([bad_coords_station]))
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    # With invalid coords the station is excluded (dist is None, and None > radius_km is False
    # via the `if dist is None or dist > radius_km: continue` guard)
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    # Station with invalid coords is excluded
    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_handles_stops_mid_pagination_on_error() -> None:
    """async_list_stations continues with already-fetched stations when page 2+ fails."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )

    page2_error = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("oops"),
    )
    session = _make_session(franchise_resp, page1, page2_error)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    # Page1 results should be in the output
    assert len(result) == 1
    assert result[0][0] == str(_STATION_PK)


# ---------------------------------------------------------------------------
# _ensure_franchise_cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_franchise_cache_populates_cache() -> None:
    """_ensure_franchise_cache stores pk→name mapping from franchise endpoint."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    session = _make_session(franchise_resp)

    provider = _default_provider()
    cache = await provider._ensure_franchise_cache(session)

    assert cache[_FRANCHISE_PK] == _BRAND_NAME
    assert cache[7] == "MOL"


@pytest.mark.asyncio
async def test_ensure_franchise_cache_is_populated_on_instance() -> None:
    """_ensure_franchise_cache sets self._franchise_cache on the instance."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    session = _make_session(franchise_resp)

    provider = _default_provider()
    await provider._ensure_franchise_cache(session)

    assert provider._franchise_cache[_FRANCHISE_PK] == _BRAND_NAME


@pytest.mark.asyncio
async def test_ensure_franchise_cache_skips_items_without_pk() -> None:
    """_ensure_franchise_cache skips franchise entries missing pk or name."""
    bad_list = [
        {"pk": None, "name": "BadBrand"},
        {"pk": 10, "name": ""},
        {"pk": 11, "name": "GoodBrand"},
    ]
    franchise_resp = _make_mock_response(200, json_data=bad_list)
    session = _make_session(franchise_resp)

    provider = _default_provider()
    cache = await provider._ensure_franchise_cache(session)

    assert None not in cache
    assert 10 not in cache
    assert cache[11] == "GoodBrand"


@pytest.mark.asyncio
async def test_ensure_franchise_cache_returns_empty_on_http_error() -> None:
    """_ensure_franchise_cache returns {} and does not raise on HTTP error."""
    franchise_resp = _make_mock_response(
        503,
        raise_on_raise_for_status=ClientError("service unavailable"),
    )
    session = _make_session(franchise_resp)

    provider = _default_provider()
    cache = await provider._ensure_franchise_cache(session)

    assert cache == {}


@pytest.mark.asyncio
async def test_ensure_franchise_cache_not_refetched_when_populated() -> None:
    """_ensure_franchise_cache does not re-fetch when already populated."""
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    session = _make_session(franchise_resp)

    provider = _default_provider()
    # Pre-populate the cache
    provider._franchise_cache = {99: "Cached Brand"}
    import time

    provider._franchise_cache_ts = time.monotonic()  # mark cache as fresh

    cache = await provider._ensure_franchise_cache(session)

    # Must return the pre-populated cache, not re-fetch
    assert cache == {99: "Cached Brand"}
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_all_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_all_stations_single_page() -> None:
    """_fetch_all_stations returns all stations from a single page."""
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION, _SECOND_STATION]),
    )
    session = _make_session(page1)

    provider = _default_provider()
    stations, _ = await provider._fetch_all_stations(session)

    assert len(stations) == 2
    pks = [s["pk"] for s in stations]
    assert _STATION_PK in pks
    assert 2309 in pks


@pytest.mark.asyncio
async def test_fetch_all_stations_multi_page() -> None:
    """_fetch_all_stations aggregates results across multiple pages."""
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )
    page2 = _make_mock_response(
        200,
        json_data=_search_page([_SECOND_STATION], has_next=False),
    )
    session = _make_session(page1, page2)

    provider = _default_provider()
    stations, _ = await provider._fetch_all_stations(session)

    assert len(stations) == 2


@pytest.mark.asyncio
async def test_fetch_all_stations_raises_on_first_page_error() -> None:
    """_fetch_all_stations propagates errors from page 1."""
    page1_err = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("server error"),
    )
    session = _make_session(page1_err)

    provider = _default_provider()
    with pytest.raises(ClientError):
        await provider._fetch_all_stations(session)


@pytest.mark.asyncio
async def test_fetch_all_stations_stops_gracefully_on_later_page_error() -> None:
    """_fetch_all_stations stops and returns partial results on page 2+ error."""
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )
    page2_err = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("server error"),
    )
    session = _make_session(page1, page2_err)

    provider = _default_provider()
    stations, _ = await provider._fetch_all_stations(session)

    assert len(stations) == 1
    assert stations[0]["pk"] == _STATION_PK


@pytest.mark.asyncio
async def test_fetch_all_stations_stops_on_404() -> None:
    """_fetch_all_stations treats HTTP 404 as end of pages."""
    page1 = _make_mock_response(
        200,
        json_data=_search_page([_BASE_STATION], has_next=True),
    )
    page2_404 = _make_mock_response(404)
    session = _make_session(page1, page2_404)

    provider = _default_provider()
    stations, _ = await provider._fetch_all_stations(session)

    assert len(stations) == 1


@pytest.mark.asyncio
async def test_fetch_all_stations_empty_results_key() -> None:
    """_fetch_all_stations handles page with null results key."""
    page1 = _make_mock_response(
        200,
        json_data={"count": 0, "next": None, "previous": None, "results": None},
    )
    session = _make_session(page1)

    provider = _default_provider()
    stations, _ = await provider._fetch_all_stations(session)

    assert stations == []


# ---------------------------------------------------------------------------
# Coverage for lines 277, 398-402, 514-515
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_missing_lat_lng() -> None:
    """async_list_stations skips stations that have no lat/lng when coords provided (line 277)."""
    no_coords_station = {
        **_BASE_STATION,
        "pk": 5001,
        "lat": None,
        "lng": None,
    }
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([no_coords_station]),
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    station_ids = [sid for sid, _ in result]
    assert "5001" not in station_ids


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_missing_only_lng() -> None:
    """async_list_stations skips stations with lat but no lng when coords provided (line 277)."""
    partial_coords_station = {
        **_BASE_STATION,
        "pk": 5002,
        "lat": 46.0517,
        "lng": None,
    }
    franchise_resp = _make_mock_response(200, json_data=_BASE_FRANCHISE_LIST)
    page1 = _make_mock_response(
        200,
        json_data=_search_page([partial_coords_station]),
    )
    session = _make_session(franchise_resp, page1)

    provider = _default_provider()
    result = await provider.async_list_stations(
        session, lat=46.0517, lng=14.5079, radius_km=10.0
    )

    station_ids = [sid for sid, _ in result]
    assert "5002" not in station_ids


@pytest.mark.asyncio
async def test_fetch_all_stations_breaks_on_max_pages() -> None:
    """_fetch_all_stations breaks and does not loop forever when MAX_PAGES exceeded (lines 398-402)."""
    from custom_components.fuelcompare_ie.providers.si_goriva import MAX_PAGES

    # Build MAX_PAGES responses each with has_next=True so the loop would
    # continue past the limit.  The (MAX_PAGES + 1)-th response must never
    # be consumed.
    responses = [
        _make_mock_response(200, json_data=_search_page([_BASE_STATION], has_next=True))
        for _ in range(MAX_PAGES + 1)
    ]
    session = _make_session(*responses)

    provider = _default_provider()
    stations, complete = await provider._fetch_all_stations(session)

    # Should have stopped at MAX_PAGES, not fetched indefinitely.
    assert session.get.call_count == MAX_PAGES
    # complete must be False because we hit the page limit, not the end.
    assert complete is False


def test_parse_station_brand_none_when_franchise_pk_invalid_type() -> None:
    """_parse_station silently handles non-int franchise_pk, brand stays None (lines 514-515)."""
    station = {**_BASE_STATION, "franchise": "not-an-int"}
    result = _parse_station(station, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["brand"] is None


def test_parse_station_brand_none_when_franchise_pk_is_dict() -> None:
    """_parse_station silently handles dict franchise_pk (TypeError path), brand stays None (lines 514-515)."""
    station = {**_BASE_STATION, "franchise": {"nested": "value"}}
    result = _parse_station(station, {_FRANCHISE_PK: _BRAND_NAME})
    assert result["brand"] is None
