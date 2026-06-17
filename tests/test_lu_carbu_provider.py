"""Tests for LuCarbuProvider (carbu.com Luxembourg)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.lu_carbu import (
    LuCarbuProvider,
    _BASE_URL,
    _FUEL_IDS,
    _HEADERS,
    _find_station,
    _parse_coord,
    _parse_price,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

_STATION_ID = "LU-12345"
_OTHER_ID = "LU-99999"

_STATION_STRASSEN: dict = {
    "id": "LU-12345",
    "name": "Total Strassen",
    "brand": "Total",
    "address": "5 Route d'Arlon",
    "city": "Strassen",
    "lat": "49.6170",
    "lng": "6.0760",
    "price": "1.753",
    "updated": "2026-06-10 14:32:00",
}

_STATION_KIRCHBERG: dict = {
    "id": "LU-99999",
    "name": "Shell Kirchberg",
    "brand": "Shell",
    "address": "Avenue J.F. Kennedy",
    "city": "Luxembourg",
    "lat": "49.6230",
    "lng": "6.1610",
    "price": "1.799",
    "updated": "2026-06-10 12:00:00",
}

_STATION_NO_PRICE: dict = {
    "id": "LU-11111",
    "name": "BP Gasperich",
    "brand": "BP",
    "address": "Route de Thionville",
    "city": "Gasperich",
    "lat": "49.5900",
    "lng": "6.1200",
    "price": None,
    "updated": None,
}


def _make_mock_response(
    status: int,
    body: list | dict | None = None,
    raise_on_status: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    if raise_on_status is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_on_status)
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=body if body is not None else [])
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns the given response."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _make_multi_fuel_session(
    stations_per_fuel: dict[str, list[dict]],
) -> MagicMock:
    """Return a mock session that returns different station lists per fuel type.

    Uses call_count to return responses in sequence: one response per call.
    The order of calls matches _FUEL_IDS.keys() order.
    """
    fuel_keys = list(_FUEL_IDS.keys())
    responses = []
    for fuel_key in fuel_keys:
        data = stations_per_fuel.get(fuel_key, [])
        resp = _make_mock_response(200, body=data)
        responses.append(resp)

    session = MagicMock()
    session.get = MagicMock(side_effect=responses)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """LuCarbuProvider declares COUNTRY='LU'."""
    assert LuCarbuProvider.COUNTRY == "LU"


def test_provider_metadata_provider_key() -> None:
    """LuCarbuProvider declares PROVIDER_KEY='lu_carbu'."""
    assert LuCarbuProvider.PROVIDER_KEY == "lu_carbu"


def test_provider_metadata_label() -> None:
    """LuCarbuProvider declares a human-readable label."""
    assert (
        "carbu" in LuCarbuProvider.LABEL.lower()
        or "luxembourg" in LuCarbuProvider.LABEL.lower()
    )


def test_provider_metadata_config_mode() -> None:
    """LuCarbuProvider uses station_id CONFIG_MODE."""
    assert LuCarbuProvider.CONFIG_MODE == "station_id"


def test_provider_metadata_station_lookup_mode() -> None:
    """LuCarbuProvider uses location_search STATION_LOOKUP_MODE."""
    assert LuCarbuProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_capabilities_fuel_types() -> None:
    """CAPABILITIES includes all five Luxembourg fuel types."""
    caps = LuCarbuProvider.CAPABILITIES
    for fuel in ("diesel", "unleaded", "premium_unleaded", "lpg", "cng"):
        assert fuel in caps, f"Fuel type '{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_station_fields() -> None:
    """CAPABILITIES includes standard station identity fields."""
    caps = LuCarbuProvider.CAPABILITIES
    for field in ("name", "brand", "address", "latitude", "longitude", "lastupdated"):
        assert field in caps, f"Field '{field}' missing from CAPABILITIES"


def test_provider_capabilities_exclude_coordinator_sentinels() -> None:
    """CAPABILITIES excludes coordinator sentinel keys."""
    caps = LuCarbuProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Provider constructor
# ---------------------------------------------------------------------------


def test_provider_init_stores_station_id() -> None:
    """Constructor stores station_id."""
    provider = LuCarbuProvider(_STATION_ID)
    assert provider._station_id == _STATION_ID


def test_provider_init_default_radius() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = LuCarbuProvider(_STATION_ID)
    assert provider._radius_km == 10.0


def test_provider_init_custom_radius() -> None:
    """Constructor accepts a custom radius_km value."""
    provider = LuCarbuProvider(_STATION_ID, radius_km=25.0)
    assert provider._radius_km == 25.0


def test_provider_init_none_radius_uses_default() -> None:
    """Constructor treats radius_km=None as 10.0 default."""
    provider = LuCarbuProvider(_STATION_ID, radius_km=None)
    assert provider._radius_km == 10.0


def test_provider_init_stores_coordinates() -> None:
    """Constructor stores lat/lng for async_list_stations."""
    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    assert provider._latitude == pytest.approx(49.617)
    assert provider._longitude == pytest.approx(6.076)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_base_url_points_to_carbu_luxembourg() -> None:
    """_BASE_URL points to the carbu.com Luxembourg endpoint."""
    from urllib.parse import urlparse

    assert urlparse(_BASE_URL).netloc == "carbu.com"
    assert "luxembourg" in _BASE_URL
    assert _BASE_URL.startswith("https://")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent string."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_fuel_ids_has_five_fuels() -> None:
    """_FUEL_IDS has entries for the five Luxembourg fuel types."""
    for key in ("unleaded", "premium_unleaded", "diesel", "lpg", "cng"):
        assert key in _FUEL_IDS, f"Fuel key '{key}' missing from _FUEL_IDS"


def test_fuel_ids_diesel_is_1() -> None:
    """_FUEL_IDS maps 'diesel' to carbu.com ID 1 (Gasoil)."""
    assert _FUEL_IDS["diesel"] == 1


def test_fuel_ids_unleaded_is_2() -> None:
    """_FUEL_IDS maps 'unleaded' (Super 95) to carbu.com ID 2."""
    assert _FUEL_IDS["unleaded"] == 2


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_standard_value() -> None:
    """_parse_price converts '1.753' to float 1.753."""
    assert _parse_price("1.753") == pytest.approx(1.753)


def test_parse_price_comma_separator() -> None:
    """_parse_price handles comma as decimal separator."""
    assert _parse_price("1,753") == pytest.approx(1.753)


def test_parse_price_none_input_returns_none() -> None:
    """_parse_price returns None when passed None."""
    assert _parse_price(None) is None


def test_parse_price_empty_string_returns_none() -> None:
    """_parse_price returns None for empty string."""
    assert _parse_price("") is None


def test_parse_price_zero_returns_none() -> None:
    """_parse_price returns None for zero price."""
    assert _parse_price("0") is None


def test_parse_price_negative_returns_none() -> None:
    """_parse_price returns None for negative price."""
    assert _parse_price("-1.5") is None


def test_parse_price_rounds_to_three_decimal_places() -> None:
    """_parse_price rounds to 3 decimal places."""
    result = _parse_price("1.7534")
    assert result == pytest.approx(1.753, abs=1e-3)


def test_parse_price_float_input() -> None:
    """_parse_price handles float input directly."""
    assert _parse_price(1.733) == pytest.approx(1.733)


def test_parse_price_invalid_string_returns_none() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None


# ---------------------------------------------------------------------------
# _parse_coord
# ---------------------------------------------------------------------------


def test_parse_coord_string_latitude() -> None:
    """_parse_coord converts '49.6170' to float 49.617."""
    assert _parse_coord("49.6170") == pytest.approx(49.617)


def test_parse_coord_string_longitude() -> None:
    """_parse_coord converts '6.0760' to float 6.076."""
    assert _parse_coord("6.0760") == pytest.approx(6.076)


def test_parse_coord_none_returns_none() -> None:
    """_parse_coord returns None for None input."""
    assert _parse_coord(None) is None


def test_parse_coord_invalid_returns_none() -> None:
    """_parse_coord returns None for non-numeric strings."""
    assert _parse_coord("not_a_coord") is None


def test_parse_coord_float_passthrough() -> None:
    """_parse_coord handles float input."""
    assert _parse_coord(49.617) == pytest.approx(49.617)


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the dict for the matching station ID."""
    stations = [_STATION_STRASSEN, _STATION_KIRCHBERG]
    result = _find_station(stations, _STATION_ID)
    assert result is not None
    assert result["id"] == _STATION_ID


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station matches the ID."""
    stations = [_STATION_STRASSEN]
    result = _find_station(stations, "LU-00000")
    assert result is None


def test_find_station_returns_none_on_empty_list() -> None:
    """_find_station returns None when station list is empty."""
    result = _find_station([], _STATION_ID)
    assert result is None


def test_find_station_matches_second_entry() -> None:
    """_find_station returns the correct record when it is not the first entry."""
    stations = [_STATION_STRASSEN, _STATION_KIRCHBERG]
    result = _find_station(stations, _OTHER_ID)
    assert result is not None
    assert result["id"] == _OTHER_ID
    assert result["name"] == "Shell Kirchberg"


def test_find_station_id_compared_as_string() -> None:
    """_find_station compares IDs as strings (handles int IDs from JSON)."""
    stations = [{"id": 12345, "name": "Test Station", "price": "1.750"}]
    result = _find_station(stations, "12345")
    assert result is not None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a StationData dict when the station is found."""
    # All fuel types return the same station list with a diesel price
    stations_with_diesel = [
        {**_STATION_STRASSEN, "price": "1.753"},
    ]
    stations_sp95 = [
        {**_STATION_STRASSEN, "price": "1.733"},
    ]
    stations_sp98 = [
        {**_STATION_STRASSEN, "price": "1.821"},
    ]
    stations_lpg = [
        {**_STATION_STRASSEN, "price": "0.750"},
    ]
    stations_cng: list = []

    fuel_map = {
        "unleaded": stations_sp95,
        "premium_unleaded": stations_sp98,
        "diesel": stations_with_diesel,
        "lpg": stations_lpg,
        "cng": stations_cng,
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None


async def test_async_fetch_diesel_price() -> None:
    """async_fetch populates diesel price from carbu.com response."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.753)


async def test_async_fetch_unleaded_price() -> None:
    """async_fetch populates unleaded (Super 95) price."""
    fuel_map = {
        "unleaded": [{**_STATION_STRASSEN, "price": "1.733"}],
        "premium_unleaded": [],
        "diesel": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.733)


async def test_async_fetch_premium_unleaded_price() -> None:
    """async_fetch populates premium_unleaded (Super 98) price."""
    fuel_map = {
        "unleaded": [],
        "premium_unleaded": [{**_STATION_STRASSEN, "price": "1.821"}],
        "diesel": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.821)


async def test_async_fetch_lpg_price() -> None:
    """async_fetch populates lpg price."""
    fuel_map = {
        "unleaded": [],
        "premium_unleaded": [],
        "diesel": [],
        "lpg": [{**_STATION_STRASSEN, "price": "0.750"}],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lpg"] == pytest.approx(0.750)


async def test_async_fetch_cng_price() -> None:
    """async_fetch populates cng price."""
    fuel_map = {
        "unleaded": [],
        "premium_unleaded": [],
        "diesel": [],
        "lpg": [],
        "cng": [{**_STATION_STRASSEN, "price": "1.200"}],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["cng"] == pytest.approx(1.200)


async def test_async_fetch_station_name_field() -> None:
    """async_fetch populates name field from API response."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "Total Strassen"


async def test_async_fetch_brand_field() -> None:
    """async_fetch populates brand field from API response."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["brand"] == "Total"


async def test_async_fetch_address_combines_street_and_city() -> None:
    """async_fetch combines address and city fields into address."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert "Route d'Arlon" in data["address"]
    assert "Strassen" in data["address"]


async def test_async_fetch_latitude_longitude_fields() -> None:
    """async_fetch populates latitude and longitude as decimal degrees."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(49.617, abs=0.01)
    assert data["longitude"] == pytest.approx(6.076, abs=0.01)


async def test_async_fetch_lastupdated_field() -> None:
    """async_fetch populates lastupdated from the API 'updated' field."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] == "2026-06-10 14:32:00"


async def test_async_fetch_source_station_id_populated() -> None:
    """async_fetch does not set source_station_id (injected by coordinator)."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert "source_station_id" not in data


async def test_async_fetch_missing_fuel_is_none() -> None:
    """async_fetch returns None for fuel types not in any response."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["lpg"] is None
    assert data["cng"] is None


async def test_async_fetch_prices_are_eur_litre_not_cents() -> None:
    """async_fetch returns prices as EUR/litre (1.x range), not cents (1xx range)."""
    fuel_map = {
        "diesel": [{**_STATION_STRASSEN, "price": "1.753"}],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] < 10.0
    assert data["diesel"] == pytest.approx(1.753)


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station ID absent from all responses."""
    # All fuel responses contain a different station ID
    other_station = {**_STATION_STRASSEN, "id": "LU-99999"}
    fuel_map = {
        "diesel": [other_station],
        "unleaded": [other_station],
        "premium_unleaded": [other_station],
        "lpg": [other_station],
        "cng": [other_station],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    with pytest.raises(ProviderError, match=_STATION_ID):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_empty_responses() -> None:
    """async_fetch raises ProviderError when all fuel type responses are empty."""
    fuel_map = {
        "diesel": [],
        "unleaded": [],
        "premium_unleaded": [],
        "lpg": [],
        "cng": [],
    }
    session = _make_multi_fuel_session(fuel_map)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_handles_connection_error_gracefully() -> None:
    """async_fetch raises ProviderError (not raw network exception) when all requests fail."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    # Network failures on all fuel types mean station is not found → ProviderError
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_http_error_returns_none_prices() -> None:
    """async_fetch handles per-fuel HTTP errors and still assembles partial data."""
    resp_ok = _make_mock_response(200, body=[{**_STATION_STRASSEN, "price": "1.733"}])

    fuel_responses = []
    for fuel_key in _FUEL_IDS.keys():
        if fuel_key == "unleaded":
            fuel_responses.append(resp_ok)
        else:
            fuel_responses.append(
                _make_mock_response(
                    500,
                    raise_on_status=ClientResponseError(MagicMock(), (), status=500),
                )
            )

    session = MagicMock()
    session.get = MagicMock(side_effect=fuel_responses)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.733)
    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_on_success() -> None:
    """async_fetch_station_name returns station name from diesel lookup."""
    resp = _make_mock_response(200, body=[_STATION_STRASSEN])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Total Strassen"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station absent from API."""
    # Both diesel and unleaded responses return a different station
    other = {**_STATION_STRASSEN, "id": "LU-OTHER"}
    resp = _make_mock_response(200, body=[other])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_http_error() -> None:
    """async_fetch_station_name returns None when HTTP error is raised."""
    resp = _make_mock_response(
        503,
        raise_on_status=ClientResponseError(MagicMock(), (), status=503),
    )
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_falls_back_to_unleaded() -> None:
    """async_fetch_station_name retries with unleaded when diesel call returns empty."""
    # First call (diesel) returns empty; second call (unleaded) returns the station
    resp_empty = _make_mock_response(200, body=[])
    resp_station = _make_mock_response(200, body=[_STATION_STRASSEN])

    session = MagicMock()
    session.get = MagicMock(side_effect=[resp_empty, resp_station])

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Total Strassen"


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (station_id, label) tuples."""
    resp = _make_mock_response(200, body=[_STATION_STRASSEN, _STATION_KIRCHBERG])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=20.0
    )

    assert isinstance(results, list)
    for item in results:
        assert len(item) == 2
        sid, label = item
        assert isinstance(sid, str)
        assert isinstance(label, str)


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes station identifier token (no price)."""
    resp = _make_mock_response(200, body=[_STATION_STRASSEN])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=20.0
    )

    assert len(results) >= 1
    _sid, label = results[0]
    assert "(#" in label


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations sorts results alphabetically by label."""
    cheap = {**_STATION_STRASSEN, "id": "LU-CHEAP", "price": "1.699"}
    expensive = {**_STATION_KIRCHBERG, "id": "LU-EXPENSIVE", "price": "1.899"}
    resp = _make_mock_response(200, body=[expensive, cheap])
    # Use same response for both diesel and sp95 calls
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=50.0
    )

    if len(results) >= 2:
        # "Shell Shell Kirchberg..." < "Total Total Strassen..." alphabetically
        first_sid = results[0][0]
        assert first_sid == "LU-EXPENSIVE"


async def test_async_list_stations_uses_kwargs_lat_lng() -> None:
    """async_list_stations uses lat/lng from kwargs, not stored coordinates."""
    resp = _make_mock_response(200, body=[_STATION_STRASSEN])
    session = _make_session(resp)

    # Provider initialised with wrong coords; kwargs provide correct ones
    provider = LuCarbuProvider(_STATION_ID, latitude=0.0, longitude=0.0)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=20.0
    )

    assert isinstance(results, list)


async def test_async_list_stations_station_without_price_sorts_last() -> None:
    """async_list_stations sorts stations alphabetically regardless of price."""
    with_price = {**_STATION_STRASSEN, "id": "LU-PRICED", "price": "1.753"}
    no_price = {**_STATION_KIRCHBERG, "id": "LU-NOPR", "price": None}
    resp = _make_mock_response(200, body=[no_price, with_price])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=50.0
    )

    if len(results) >= 2:
        # Both stations present; order is alphabetical
        station_ids = [sid for sid, _ in results]
        assert "LU-NOPR" in station_ids
        assert "LU-PRICED" in station_ids


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_lat_lng() -> None:
    """async_list_stations returns empty list when no lat/lng is available."""
    session = MagicMock()

    # Neither stored nor kwargs lat/lng
    provider = LuCarbuProvider(_STATION_ID)
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_network_error() -> None:
    """async_list_stations returns empty list when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=10.0
    )

    assert results == []


async def test_async_list_stations_returns_empty_when_api_returns_empty() -> None:
    """async_list_stations returns empty list when API has no stations."""
    resp = _make_mock_response(200, body=[])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=10.0
    )

    assert results == []


async def test_async_list_stations_label_includes_station_name() -> None:
    """async_list_stations label includes the station name."""
    resp = _make_mock_response(200, body=[_STATION_STRASSEN])
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=20.0
    )

    assert len(results) >= 1
    _sid, label = results[0]
    # Station name or brand should appear in label
    assert "Total" in label or "Strassen" in label


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """LuCarbuProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "lu_carbu" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["lu_carbu"] is LuCarbuProvider


# ---------------------------------------------------------------------------
# async_fetch_station_name — generic exception path (lines 354-355)
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None when _fetch_fuel_stations raises unexpectedly (lines 354-355)."""
    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)

    async def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unexpected internal error")

    with patch.object(provider, "_fetch_fuel_stations", side_effect=_raise):
        session = MagicMock()
        name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — generic exception from asyncio.gather (lines 412-414)
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_gather_exception() -> None:
    """async_list_stations returns [] when both gather coroutines raise (lines 412-414)."""
    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    session = MagicMock()

    # Patch _fetch_fuel_stations to return coroutines that raise, so gather
    # catches exceptions via return_exceptions=True and the provider returns [].
    with patch.object(
        provider,
        "_fetch_fuel_stations",
        side_effect=RuntimeError("fetch failure"),
    ):
        results = await provider.async_list_stations(
            session, lat=49.617, lng=6.076, radius_km=10.0
        )

    assert results == []


# ---------------------------------------------------------------------------
# async_list_stations — sp95 dedup: same station already in diesel (line 433)
# ---------------------------------------------------------------------------


async def test_async_list_stations_deduplicates_sp95_station_keeps_diesel_entry() -> (
    None
):
    """async_list_stations keeps diesel entry when same station also appears in sp95 (line 433)."""
    diesel_station = {**_STATION_STRASSEN, "price": "1.753"}
    sp95_station = {**_STATION_STRASSEN, "price": "1.733"}

    resp_diesel = _make_mock_response(200, body=[diesel_station])
    resp_sp95 = _make_mock_response(200, body=[sp95_station])

    session = MagicMock()
    session.get = MagicMock(side_effect=[resp_diesel, resp_sp95])

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=10.0
    )

    # Station should appear exactly once
    station_ids = [sid for sid, _label in results]
    assert station_ids.count(_STATION_ID) == 1
    # Label should contain station identifier token
    _sid, label = results[0]
    assert "(#" in label


async def test_async_list_stations_sp95_only_station_added_to_merged() -> None:
    """async_list_stations adds sp95-only station to merged dict (line 433)."""
    # Diesel returns a different station; sp95 returns _STATION_KIRCHBERG (unique)
    diesel_station = {**_STATION_STRASSEN, "price": "1.753"}
    sp95_only_station = {**_STATION_KIRCHBERG, "price": "1.733"}

    resp_diesel = _make_mock_response(200, body=[diesel_station])
    resp_sp95 = _make_mock_response(200, body=[sp95_only_station])

    session = MagicMock()
    session.get = MagicMock(side_effect=[resp_diesel, resp_sp95])

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    results = await provider.async_list_stations(
        session, lat=49.617, lng=6.076, radius_km=10.0
    )

    # Both stations should be present
    station_ids = [sid for sid, _label in results]
    assert _STATION_ID in station_ids
    assert _OTHER_ID in station_ids


# ---------------------------------------------------------------------------
# _fetch_fuel_stations — dict payload with known / unknown keys (lines 529-534)
# ---------------------------------------------------------------------------


async def test_fetch_fuel_stations_returns_list_under_stations_key() -> None:
    """_fetch_fuel_stations unwraps payload dict with 'stations' key (lines 529-533)."""
    wrapped = {"stations": [_STATION_STRASSEN]}
    resp = _make_mock_response(200, body=wrapped)
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    result = await provider._fetch_fuel_stations(
        session,
        fuel_key="diesel",
        fuel_id=_FUEL_IDS["diesel"],
        lat=49.617,
        lng=6.076,
        radius_km=10.0,
    )

    assert result == [_STATION_STRASSEN]


async def test_fetch_fuel_stations_returns_list_under_data_key() -> None:
    """_fetch_fuel_stations unwraps payload dict with 'data' key (lines 529-533)."""
    wrapped = {"data": [_STATION_STRASSEN]}
    resp = _make_mock_response(200, body=wrapped)
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    result = await provider._fetch_fuel_stations(
        session,
        fuel_key="diesel",
        fuel_id=_FUEL_IDS["diesel"],
        lat=49.617,
        lng=6.076,
        radius_km=10.0,
    )

    assert result == [_STATION_STRASSEN]


async def test_fetch_fuel_stations_returns_empty_for_unwrappable_dict() -> None:
    """_fetch_fuel_stations returns [] when dict payload has no known list key (line 534)."""
    wrapped = {"unknown_key": [_STATION_STRASSEN]}
    resp = _make_mock_response(200, body=wrapped)
    session = _make_session(resp)

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    result = await provider._fetch_fuel_stations(
        session,
        fuel_key="diesel",
        fuel_id=_FUEL_IDS["diesel"],
        lat=49.617,
        lng=6.076,
        radius_km=10.0,
    )

    assert result == []


# ---------------------------------------------------------------------------
# _build_station_data — address assembly branches (lines 562-567)
# ---------------------------------------------------------------------------


def test_build_station_data_address_street_only() -> None:
    """_build_station_data uses street alone when city is absent (lines 562-563)."""
    meta = {
        "name": "Test Station",
        "brand": "BP",
        "address": "123 Rue de la Paix",
        "city": None,
        "lat": "49.617",
        "lng": "6.076",
        "updated": None,
    }
    provider = LuCarbuProvider(_STATION_ID)
    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["address"] == "123 Rue de la Paix"


def test_build_station_data_address_city_only() -> None:
    """_build_station_data uses city alone when street is absent (lines 564-565)."""
    meta = {
        "name": "Test Station",
        "brand": "BP",
        "address": None,
        "city": "Luxembourg",
        "lat": "49.617",
        "lng": "6.076",
        "updated": None,
    }
    provider = LuCarbuProvider(_STATION_ID)
    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["address"] == "Luxembourg"


def test_build_station_data_address_none_when_both_missing() -> None:
    """_build_station_data sets address to None when both street and city are absent (lines 566-567)."""
    meta = {
        "name": "Test Station",
        "brand": "BP",
        "address": None,
        "city": None,
        "lat": "49.617",
        "lng": "6.076",
        "updated": None,
    }
    provider = LuCarbuProvider(_STATION_ID)
    data = provider._build_station_data(_STATION_ID, meta, {})

    assert data["address"] is None


# ---------------------------------------------------------------------------
# lu_carbu.py lines 413-415 — entire asyncio.gather raises unexpected exception
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_ble001_gather_exception() -> None:
    """Lines 413-415: async_list_stations returns [] when asyncio.gather itself raises."""
    from unittest.mock import patch

    provider = LuCarbuProvider(_STATION_ID, latitude=49.617, longitude=6.076)
    session = MagicMock()

    def _gather_boom(*coros, **_kwargs):
        # Close the un-awaited input coroutines so pytest doesn't emit a
        # "coroutine was never awaited" RuntimeWarning when the gather
        # mock raises before scheduling them.
        for c in coros:
            if hasattr(c, "close"):
                c.close()
        raise RuntimeError("gather boom")

    with patch("asyncio.gather", side_effect=_gather_boom):
        result = await provider.async_list_stations(
            session, lat=49.617, lng=6.076, radius_km=10.0
        )

    assert result == []
