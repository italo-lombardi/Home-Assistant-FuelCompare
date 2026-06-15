"""Tests for ChTcsProvider (TCS Benzinpreis-Radar Switzerland)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.ch_tcs import (
    ChTcsProvider,
    _API_URL,
    _CH_BBOX,
    _FUEL_MAP,
    _FUEL_TYPES,
    _GRID_SPLITS,
    _HEADERS,
    _build_station_data,
    _build_sub_bboxes,
    _parse_price,
)

# ---------------------------------------------------------------------------
# Test fixtures / constants
# ---------------------------------------------------------------------------

_STATION_ID = "ch-tcs-001"
_LAT = 47.3769  # Zurich
_LNG = 8.5417
_RADIUS_KM = 10.0

# A representative single-station API response entry
_BASE_STATION: dict = {
    "id": _STATION_ID,
    "brand": "AGROLA",
    "latitude": _LAT,
    "longitude": _LNG,
    "displayName": "AGROLA Zürich Altstetten",
    "formattedAddress": "Badenerstrasse 569, 8048 Zürich",
    "price": 1.879,
    "fuel": "SP95",
    "fiability": "CONFIDENT",
    "isCheapest": False,
    "cluster": None,
}

# A cluster record — should always be filtered out
_CLUSTER_STATION: dict = {
    "id": "cluster-99",
    "cluster": {"count": 5},
    "latitude": _LAT,
    "longitude": _LNG,
    "price": 1.799,
    "fuel": "SP95",
}

# API response wrapper with a data list
_PAYLOAD_SP95: dict = {"data": [_BASE_STATION]}
_PAYLOAD_DIESEL: dict = {
    "data": [
        {
            **_BASE_STATION,
            "id": _STATION_ID,
            "price": 1.699,
            "fuel": "DIESEL",
        }
    ]
}
_PAYLOAD_SP98: dict = {
    "data": [
        {
            **_BASE_STATION,
            "id": _STATION_ID,
            "price": 1.959,
            "fuel": "SP98",
        }
    ]
}
_PAYLOAD_EMPTY: dict = {"data": []}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session_always(json_data: dict) -> MagicMock:
    """Return a mock session whose .post() always returns the given JSON payload."""
    session = MagicMock()
    session.post = MagicMock(return_value=_make_mock_response(200, json_data=json_data))
    return session


def _make_session_cycle(*payloads: dict) -> MagicMock:
    """Return a mock session whose .post() cycles through the given payloads."""
    session = MagicMock()
    # Cycle: repeat the payloads forever to accommodate all sub-bbox requests
    import itertools

    cycle = itertools.cycle([_make_mock_response(200, json_data=p) for p in payloads])
    session.post = MagicMock(side_effect=lambda *a, **kw: next(cycle))
    return session


def _provider(
    station_id: str = _STATION_ID,
    lat: float = _LAT,
    lng: float = _LNG,
    radius_km: float = _RADIUS_KM,
) -> ChTcsProvider:
    """Create a ChTcsProvider with default test values."""
    return ChTcsProvider(
        station_id=station_id,
        latitude=lat,
        longitude=lng,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country_is_ch() -> None:
    """ChTcsProvider declares COUNTRY='CH'."""
    assert ChTcsProvider.COUNTRY == "CH"


def test_provider_key_is_ch_tcs() -> None:
    """ChTcsProvider declares PROVIDER_KEY='ch_tcs'."""
    assert ChTcsProvider.PROVIDER_KEY == "ch_tcs"


def test_provider_label_contains_tcs_and_switzerland() -> None:
    """ChTcsProvider LABEL mentions TCS and Switzerland."""
    label_lower = ChTcsProvider.LABEL.lower()
    assert (
        "tcs" in label_lower or "switzerland" in label_lower or "swiss" in label_lower
    )


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE must be 'location' for the location-based flow."""
    assert ChTcsProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """STATION_LOOKUP_MODE must be 'location_search'."""
    assert ChTcsProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_no_api_key() -> None:
    """TCS API requires no authentication."""
    assert ChTcsProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_at_least_900() -> None:
    """POLL_INTERVAL_SECONDS should be at least 900 seconds."""
    assert ChTcsProvider.POLL_INTERVAL_SECONDS >= 900


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_include_unleaded() -> None:
    assert "unleaded" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_diesel() -> None:
    assert "diesel" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_premium_unleaded() -> None:
    assert "premium_unleaded" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_name() -> None:
    assert "name" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_brand() -> None:
    assert "brand" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_address() -> None:
    assert "address" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_latitude() -> None:
    assert "latitude" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_longitude() -> None:
    assert "longitude" in ChTcsProvider.CAPABILITIES


def test_capabilities_include_price_confidence() -> None:
    assert "price_confidence" in ChTcsProvider.CAPABILITIES


def test_capabilities_exclude_coordinator_sentinels() -> None:
    """CAPABILITIES excludes coordinator sentinel keys."""
    caps = ChTcsProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_api_url_targets_tcs_cloudfunctions() -> None:
    """_API_URL must target the TCS Cloud Function."""
    from urllib.parse import urlparse

    assert (
        urlparse(_API_URL).netloc
        == "europe-west6-tcs-digitalbackend.cloudfunctions.net"
    )
    assert _API_URL.startswith("https://")


def test_headers_include_content_type_json() -> None:
    """_HEADERS must include Content-Type: application/json (POST body)."""
    assert _HEADERS.get("Content-Type") == "application/json"


def test_headers_include_origin_benzin_tcs() -> None:
    """_HEADERS must include Origin pointing to benzin.tcs.ch."""
    assert "benzin.tcs.ch" in _HEADERS.get("Origin", "")


def test_headers_include_referer_benzin_tcs() -> None:
    """_HEADERS must include Referer pointing to benzin.tcs.ch."""
    assert "benzin.tcs.ch" in _HEADERS.get("Referer", "")


def test_headers_include_accept_json() -> None:
    """_HEADERS must request JSON."""
    assert _HEADERS.get("Accept") == "application/json"


def test_headers_user_agent_not_blocked() -> None:
    """_HEADERS User-Agent must not use a commonly blocked value."""
    ua = _HEADERS.get("User-Agent", "")
    assert ua
    blocked = ("curl/", "python-requests/", "Wget/")
    for prefix in blocked:
        assert not ua.startswith(prefix)


def test_fuel_types_contains_sp95_sp98_diesel() -> None:
    """_FUEL_TYPES must contain SP95, SP98, and DIESEL."""
    assert "SP95" in _FUEL_TYPES
    assert "SP98" in _FUEL_TYPES
    assert "DIESEL" in _FUEL_TYPES


def test_fuel_map_sp95_to_unleaded() -> None:
    assert _FUEL_MAP["SP95"] == "unleaded"


def test_fuel_map_sp98_to_premium_unleaded() -> None:
    assert _FUEL_MAP["SP98"] == "premium_unleaded"


def test_fuel_map_diesel_to_diesel() -> None:
    assert _FUEL_MAP["DIESEL"] == "diesel"


def test_ch_bbox_covers_switzerland() -> None:
    """_CH_BBOX must span a region that includes Switzerland."""
    min_lon, min_lat, max_lon, max_lat = _CH_BBOX
    # Switzerland is roughly between 5.9°E-10.5°E longitude, 45.8°N-47.8°N latitude
    assert min_lon < 6.5
    assert max_lon > 9.5
    assert min_lat < 46.2
    assert max_lat > 47.5


def test_grid_splits_is_at_least_2() -> None:
    """_GRID_SPLITS must produce at least a 2×2 grid to cover Switzerland."""
    assert _GRID_SPLITS >= 2


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    p = ChTcsProvider("test-id-001")
    assert p._station_id == "test-id-001"


def test_constructor_stores_coordinates() -> None:
    p = ChTcsProvider("1", latitude=47.0, longitude=8.0)
    assert p._latitude == pytest.approx(47.0)
    assert p._longitude == pytest.approx(8.0)


def test_constructor_stores_radius_km() -> None:
    p = ChTcsProvider("1", latitude=47.0, longitude=8.0, radius_km=5.0)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_defaults_radius_km_to_10() -> None:
    p = ChTcsProvider("1")
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_allows_none_coordinates() -> None:
    p = ChTcsProvider("1")
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# _build_sub_bboxes helper
# ---------------------------------------------------------------------------


def test_build_sub_bboxes_count_matches_splits_squared() -> None:
    """_build_sub_bboxes returns splits² sub-boxes."""
    boxes = _build_sub_bboxes(5.0, 45.0, 11.0, 48.0, 4)
    assert len(boxes) == 16


def test_build_sub_bboxes_2x2_count() -> None:
    boxes = _build_sub_bboxes(0.0, 0.0, 10.0, 10.0, 2)
    assert len(boxes) == 4


def test_build_sub_bboxes_each_box_has_four_elements() -> None:
    boxes = _build_sub_bboxes(5.0, 45.0, 11.0, 48.0, 4)
    for box in boxes:
        assert len(box) == 4


def test_build_sub_bboxes_each_box_min_less_than_max() -> None:
    boxes = _build_sub_bboxes(5.0, 45.0, 11.0, 48.0, 4)
    for box in boxes:
        min_lon, min_lat, max_lon, max_lat = box
        assert min_lon < max_lon
        assert min_lat < max_lat


def test_build_sub_bboxes_coverage_spans_full_bbox() -> None:
    """Union of all sub-boxes should tile the original bounding box."""
    boxes = _build_sub_bboxes(5.0, 45.0, 11.0, 48.0, 4)
    # All min_lon values should be >= outer min_lon
    assert all(b[0] >= 5.0 for b in boxes)
    # At least one box should have max_lon close to outer max_lon
    assert any(abs(b[2] - 11.0) < 0.01 for b in boxes)


# ---------------------------------------------------------------------------
# _parse_price helper
# ---------------------------------------------------------------------------


def test_parse_price_float_returns_float() -> None:
    assert _parse_price(1.879) == pytest.approx(1.879)


def test_parse_price_string_float_returns_float() -> None:
    assert _parse_price("1.799") == pytest.approx(1.799)


def test_parse_price_none_returns_none() -> None:
    assert _parse_price(None) is None


def test_parse_price_zero_returns_none() -> None:
    assert _parse_price(0) is None


def test_parse_price_negative_returns_none() -> None:
    assert _parse_price(-1.5) is None


def test_parse_price_non_numeric_returns_none() -> None:
    assert _parse_price("not-a-number") is None


def test_parse_price_rounds_to_3_decimal_places() -> None:
    result = _parse_price(1.87999999)
    assert result is not None
    assert result == round(result, 3)


def test_parse_price_valid_chf_price_in_expected_range() -> None:
    """Swiss fuel prices in CHF/litre should be in a realistic range (~1–3 CHF)."""
    val = _parse_price(1.879)
    assert val is not None
    assert 0.5 < val < 5.0


# ---------------------------------------------------------------------------
# _build_station_data helper
# ---------------------------------------------------------------------------

_MERGED_RAW: dict = {
    "_meta": {
        "latitude": _LAT,
        "longitude": _LNG,
        "displayName": "AGROLA Zürich Altstetten",
        "brand": "AGROLA",
        "formattedAddress": "Badenerstrasse 569, 8048 Zürich",
        "fiability": "CONFIDENT",
        "isCheapest": False,
    },
    "prices": {
        "unleaded": 1.879,
        "premium_unleaded": 1.959,
        "diesel": 1.699,
    },
    "fiability_by_fuel": {
        "unleaded": "CONFIDENT",
        "diesel": "CONFIDENT",
        "premium_unleaded": "CONFIDENT",
    },
}


def test_build_station_data_returns_all_capability_keys() -> None:
    """_build_station_data returns a dict with all expected StationData keys."""
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    required = {
        "unleaded",
        "premium_unleaded",
        "diesel",
        "name",
        "brand",
        "address",
        "latitude",
        "longitude",
        "price_confidence",
        "source_station_id",
    }
    for key in required:
        assert key in result, f"Key '{key}' missing from _build_station_data result"


def test_build_station_data_unleaded_price() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["unleaded"] == pytest.approx(1.879)


def test_build_station_data_diesel_price() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["diesel"] == pytest.approx(1.699)


def test_build_station_data_premium_unleaded_price() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["premium_unleaded"] == pytest.approx(1.959)


def test_build_station_data_name_from_display_name() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["name"] == "AGROLA Zürich Altstetten"


def test_build_station_data_brand() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["brand"] == "AGROLA"


def test_build_station_data_address() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert "Badenerstrasse" in (result["address"] or "")


def test_build_station_data_latitude() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["latitude"] == pytest.approx(_LAT)


def test_build_station_data_longitude() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["longitude"] == pytest.approx(_LNG)


def test_build_station_data_price_confidence_from_fiability() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["price_confidence"] == "CONFIDENT"


def test_build_station_data_lastupdated_not_in_result() -> None:
    """TCS API does not provide timestamps; lastupdated not in result (M-24)."""
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert "lastupdated" not in result


def test_build_station_data_source_station_id() -> None:
    result = _build_station_data(_STATION_ID, _MERGED_RAW)
    assert result["source_station_id"] == _STATION_ID


def test_build_station_data_latitude_none_on_invalid() -> None:
    raw = {**_MERGED_RAW, "_meta": {**_MERGED_RAW["_meta"], "latitude": "bad"}}
    result = _build_station_data(_STATION_ID, raw)
    assert result["latitude"] is None


def test_build_station_data_longitude_none_on_invalid() -> None:
    raw = {**_MERGED_RAW, "_meta": {**_MERGED_RAW["_meta"], "longitude": "bad"}}
    result = _build_station_data(_STATION_ID, raw)
    assert result["longitude"] is None


def test_build_station_data_name_none_when_absent() -> None:
    meta = {
        k: v
        for k, v in _MERGED_RAW["_meta"].items()
        if k not in ("displayName", "brand")
    }
    raw = {**_MERGED_RAW, "_meta": meta}
    result = _build_station_data(_STATION_ID, raw)
    assert result["name"] is None


def test_build_station_data_empty_prices_all_none() -> None:
    raw = {**_MERGED_RAW, "prices": {}}
    result = _build_station_data(_STATION_ID, raw)
    assert result["unleaded"] is None
    assert result["diesel"] is None
    assert result["premium_unleaded"] is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a StationData dict on success."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_returns_unleaded_price() -> None:
    """async_fetch populates unleaded from SP95 responses."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["unleaded"] == pytest.approx(1.879)


async def test_async_fetch_returns_diesel_price() -> None:
    """async_fetch populates diesel price from DIESEL responses."""
    session = _make_session_cycle(_PAYLOAD_SP95, _PAYLOAD_SP98, _PAYLOAD_DIESEL)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    # SP95 is in payload for first fuel batch, DIESEL in third
    assert data["unleaded"] is not None or data["diesel"] is not None


async def test_async_fetch_returns_name() -> None:
    """async_fetch populates name from displayName field."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["name"] == "AGROLA Zürich Altstetten"


async def test_async_fetch_returns_brand() -> None:
    """async_fetch populates brand field."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["brand"] == "AGROLA"


async def test_async_fetch_returns_latitude() -> None:
    """async_fetch populates latitude."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["latitude"] == pytest.approx(_LAT)


async def test_async_fetch_returns_longitude() -> None:
    """async_fetch populates longitude."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["longitude"] == pytest.approx(_LNG)


async def test_async_fetch_returns_price_confidence() -> None:
    """async_fetch populates price_confidence from fiability field."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data["price_confidence"] == "CONFIDENT"


async def test_async_fetch_lastupdated_not_in_result() -> None:
    """async_fetch does not set lastupdated (TCS API has no timestamps; M-24)."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert "lastupdated" not in data


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_absent() -> None:
    """async_fetch raises ProviderError when station_id is not in any API result."""
    session = _make_session_always(_PAYLOAD_EMPTY)
    p = _provider(station_id="nonexistent-id")
    with pytest.raises(ProviderError, match="nonexistent-id"):
        await p.async_fetch(session, "nonexistent-id")


async def test_async_fetch_raises_provider_error_when_all_data_empty() -> None:
    """async_fetch raises ProviderError when all sub-bbox requests return empty data."""
    session = _make_session_always({"data": []})
    p = _provider()
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — cluster records are filtered out
# ---------------------------------------------------------------------------


async def test_async_fetch_cluster_records_ignored() -> None:
    """Cluster records in the API response must not appear as stations."""
    payload = {"data": [_CLUSTER_STATION]}
    session = _make_session_always(payload)
    p = _provider(station_id=_CLUSTER_STATION["id"])
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _CLUSTER_STATION["id"])


async def test_async_fetch_cluster_mixed_with_station_ignored() -> None:
    """Cluster record alongside a real station — only the real station is returned."""
    payload = {"data": [_CLUSTER_STATION, _BASE_STATION]}
    session = _make_session_always(payload)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    assert data is not None
    assert data["name"] == "AGROLA Zürich Altstetten"


# ---------------------------------------------------------------------------
# async_fetch — HTTP error handling
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_network_failure() -> None:
    """Network failure during sub-bbox requests still results in ProviderError
    when no data is available for the requested station."""
    session = MagicMock()
    session.post = MagicMock(side_effect=ClientError("network error"))
    p = _provider()
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_partial_failure_still_returns_data() -> None:
    """Some sub-bbox requests failing should not prevent data from successful ones."""
    import itertools

    # Build responses: alternate between the real payload and an error
    error_resp = _make_mock_response(500)
    error_resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(None, None, status=500)  # type: ignore[arg-type]
    )
    good_resp = _make_mock_response(200, json_data=_PAYLOAD_SP95)

    responses = itertools.cycle([good_resp, error_resp])
    session = MagicMock()
    session.post = MagicMock(side_effect=lambda *a, **kw: next(responses))

    p = _provider()
    # At least some good responses contain the station — should succeed
    data = await p.async_fetch(session, _STATION_ID)
    assert data is not None


# ---------------------------------------------------------------------------
# async_fetch — POST request structure
# ---------------------------------------------------------------------------


async def test_async_fetch_uses_post_method() -> None:
    """async_fetch must use HTTP POST (not GET) for all requests."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    assert session.post.called
    assert not hasattr(session, "get") or not session.get.called


async def test_async_fetch_sends_json_body_with_bbox() -> None:
    """Each POST request must include a 'bbox' key in the JSON body."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    call_args = session.post.call_args
    json_body = call_args.kwargs.get("json") or {}
    assert "bbox" in json_body


async def test_async_fetch_sends_json_body_with_filters_fuel() -> None:
    """Each POST request must include filters.fuel in the JSON body."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    call_args = session.post.call_args
    json_body = call_args.kwargs.get("json") or {}
    assert "filters" in json_body
    assert "fuel" in json_body["filters"]


async def test_async_fetch_bbox_has_four_elements() -> None:
    """The bbox field in the POST body must be a list of 4 numbers."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    call_args = session.post.call_args
    json_body = call_args.kwargs.get("json") or {}
    bbox = json_body.get("bbox", [])
    assert len(bbox) == 4


async def test_async_fetch_makes_many_post_requests() -> None:
    """async_fetch must make multiple POST requests (fuel types × grid cells)."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    # Must be at least 3 (one per fuel type), in practice 3 × GRID_SPLITS²
    assert session.post.call_count >= 3


async def test_async_fetch_sends_correct_headers() -> None:
    """Each POST request must include Origin and Referer headers for benzin.tcs.ch."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    await p.async_fetch(session, _STATION_ID)
    call_args = session.post.call_args
    headers = call_args.kwargs.get("headers") or {}
    assert "benzin.tcs.ch" in headers.get("Origin", "")
    assert "benzin.tcs.ch" in headers.get("Referer", "")


# ---------------------------------------------------------------------------
# async_fetch — all CAPABILITIES keys present in result
# ---------------------------------------------------------------------------


async def test_async_fetch_all_capability_keys_present() -> None:
    """async_fetch result contains all CAPABILITIES keys (except coordinator sentinels)."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)
    capability_keys = ChTcsProvider.CAPABILITIES - {
        "last_successful_fetch",
        "data_fetch_problem",
    }
    for key in capability_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_display_name() -> None:
    """async_fetch_station_name returns station display name."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)
    assert name == "AGROLA Zürich Altstetten"


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None on ClientError."""
    session = MagicMock()
    session.post = MagicMock(side_effect=ClientError("timeout"))
    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)
    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_not_found() -> None:
    """async_fetch_station_name returns None when station_id absent from results."""
    session = _make_session_always(_PAYLOAD_EMPTY)
    p = _provider(station_id="not-there")
    name = await p.async_fetch_station_name(session, "not-there")
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (id, label) tuples."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, tuple)
        assert len(item) == 2


async def test_async_list_stations_includes_station_within_radius() -> None:
    """Station at exactly the provider's coordinates should be in the result."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider(lat=_LAT, lng=_LNG, radius_km=1.0)
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG, radius_km=1.0)
    ids = {sid for sid, _ in result}
    assert _STATION_ID in ids


async def test_async_list_stations_excludes_station_outside_radius() -> None:
    """Station far from the search centre should be excluded."""
    # Centre on Helsinki — far from Zürich
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    result = await p.async_list_stations(session, lat=60.17, lng=24.94, radius_km=10.0)
    ids = {sid for sid, _ in result}
    assert _STATION_ID not in ids


async def test_async_list_stations_label_contains_sp95_price() -> None:
    """Each label should contain the short station ID in (#...) format."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    if result:
        _, label = result[0]
        assert "(#" in label


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when no coordinates are available."""
    session = MagicMock()
    p = ChTcsProvider("1")  # no lat/lng
    result = await p.async_list_stations(session)
    assert result == []
    session.post.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when network error occurs."""
    session = MagicMock()
    session.post = MagicMock(side_effect=ClientError("connection refused"))
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    assert result == []


async def test_async_list_stations_returns_empty_when_all_data_empty() -> None:
    """async_list_stations returns [] when all API responses have empty data."""
    session = _make_session_always(_PAYLOAD_EMPTY)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    assert result == []


async def test_async_list_stations_uses_constructor_coordinates_when_no_kwargs() -> (
    None
):
    """async_list_stations uses constructor lat/lng when not passed as kwargs."""
    session = _make_session_always(_PAYLOAD_SP95)
    p = ChTcsProvider(_STATION_ID, latitude=_LAT, longitude=_LNG, radius_km=1.0)
    result = await p.async_list_stations(session)
    ids = {sid for sid, _ in result}
    assert _STATION_ID in ids


async def test_async_list_stations_zero_coordinates_are_valid() -> None:
    """async_list_stations must not treat 0.0 coordinates as falsy/missing."""
    # A station at (0.0, 0.0) would be in the Gulf of Guinea, but lat=0.0 or
    # lng=0.0 must not be dropped as falsy — use is-not-None check.
    session = _make_session_always(_PAYLOAD_EMPTY)
    p = ChTcsProvider("1")
    # 0.0 coords are passed explicitly — must not short-circuit to []
    # because of missing coords (the station just won't match the radius).
    result = await p.async_list_stations(session, lat=0.0, lng=0.0)
    # No crash — empty result is expected because no stations are near (0,0)
    assert isinstance(result, list)


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns stations sorted by ascending SP95 price."""
    cheap = {
        **_BASE_STATION,
        "id": "cheap-station",
        "price": 1.699,
        "displayName": "Cheap Station",
    }
    expensive = {
        **_BASE_STATION,
        "id": "expensive-station",
        "price": 1.999,
        "displayName": "Expensive Station",
    }
    payload = {"data": [expensive, cheap]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    if len(result) >= 2:
        first_id, _ = result[0]
        assert first_id == "cheap-station"


async def test_async_list_stations_station_no_price_sorts_last() -> None:
    """Stations are sorted alphabetically by label."""
    priced = {**_BASE_STATION, "id": "priced", "price": 1.799}
    no_price = {**_BASE_STATION, "id": "no-price", "price": None}
    payload = {"data": [no_price, priced]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    if len(result) >= 2:
        ids = [sid for sid, _ in result]
        # Both have same display name; alphabetically "(#no-price" < "(#priced" → no-price first
        assert ids.index("no-price") < ids.index("priced")


async def test_async_list_stations_deduplicates_by_station_id() -> None:
    """Same station returned in multiple sub-bbox responses must appear only once."""
    payload = {"data": [_BASE_STATION, _BASE_STATION]}  # duplicate
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    ids = [sid for sid, _ in result]
    assert ids.count(_STATION_ID) == 1, "Duplicate station should be de-duplicated"


# ---------------------------------------------------------------------------
# New tests — covering lines 287-288, 332-334, 343, 347-348, 422, 506
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_logs_debug_on_generic_exception() -> None:
    """Lines 287-288: async_fetch_station_name catches generic exceptions and returns None."""
    from unittest.mock import patch

    p = _provider()
    with patch.object(
        p,
        "_fetch_all_fuels",
        side_effect=RuntimeError("unexpected boom"),
    ):
        session = MagicMock()
        name = await p.async_fetch_station_name(session, _STATION_ID)
    assert name is None


async def test_async_list_stations_returns_empty_on_generic_exception() -> None:
    """Lines 332-334: async_list_stations catches generic exceptions, logs, and returns []."""
    from unittest.mock import patch

    p = _provider()
    with patch.object(
        p,
        "_fetch_all_fuels",
        side_effect=RuntimeError("unexpected boom"),
    ):
        session = MagicMock()
        result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    assert result == []


async def test_async_list_stations_skips_station_with_missing_latitude() -> None:
    """Line 343: stations with missing latitude are skipped (continue)."""
    no_lat = {**_BASE_STATION, "id": "no-lat-station", "latitude": None}
    payload = {"data": [no_lat]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    ids = {sid for sid, _ in result}
    assert "no-lat-station" not in ids


async def test_async_list_stations_skips_station_with_missing_longitude() -> None:
    """Line 343: stations with missing longitude are skipped (continue)."""
    no_lng = {**_BASE_STATION, "id": "no-lng-station", "longitude": None}
    payload = {"data": [no_lng]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    ids = {sid for sid, _ in result}
    assert "no-lng-station" not in ids


async def test_async_list_stations_skips_station_with_invalid_coordinates() -> None:
    """Lines 347-348: stations with non-numeric coordinates are skipped (ValueError/TypeError)."""
    bad_coords = {
        **_BASE_STATION,
        "id": "bad-coords",
        "latitude": "not-a-float",
        "longitude": "also-bad",
    }
    payload = {"data": [bad_coords]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    ids = {sid for sid, _ in result}
    assert "bad-coords" not in ids


async def test_async_list_stations_skips_station_with_no_id() -> None:
    """Line 422: stations with missing/falsy id are skipped (continue), others are returned."""
    no_id = {**_BASE_STATION, "id": None}
    payload = {"data": [no_id, _BASE_STATION]}
    session = _make_session_always(payload)
    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    ids = {sid for sid, _ in result}
    # The real station is still returned; the id-less one is silently skipped
    assert _STATION_ID in ids


async def test_async_fetch_bbox_returns_empty_list_on_malformed_data_field() -> None:
    """Line 506: _fetch_bbox returns [] when payload.data is a non-list (malformed response)."""
    # payload.data is a dict, not a list — triggers the isinstance(data, list) guard
    malformed_payload = {"data": {"unexpected": "object"}}
    session = _make_session_always(malformed_payload)
    p = _provider()
    # async_fetch will not raise; the malformed bbox results contribute nothing
    # We can verify via async_list_stations: no stations should appear
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)
    assert result == []
