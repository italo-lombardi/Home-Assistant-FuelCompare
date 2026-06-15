"""Tests for CaQcProvider (Régie de l'énergie Québec).

Covers:
  - Provider metadata class attributes
  - async_fetch success path (station found, prices normalised)
  - async_fetch error paths (station not found, HTTP error, ClientError)
  - async_list_stations (location search, distance filter, sorting, graceful failure)
  - Price parsing helper (_parse_price)
  - Station ID generation (_make_station_id)
  - Station data builder (_build_station_data)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.ca_qc import (
    CaQcProvider,
    _GEOJSON_URL,
    _HEADERS,
    _GAS_TYPE_MAP,
    _build_station_data,
    _make_station_id,
    _parse_price,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Fixtures and sample data
# ---------------------------------------------------------------------------

_LAT = 45.5088  # Montréal
_LNG = -73.5878
_RADIUS_KM = 5.0

_STATION_NAME = "Petro-Canada Centre-Ville"
_STATION_ADDRESS = "123 Rue Principale, Montréal"
_STATION_ID = _make_station_id(_STATION_NAME, _STATION_ADDRESS)

_BASE_FEATURE: dict = {
    "type": "Feature",
    "geometry": {
        "type": "Point",
        "coordinates": [_LNG, _LAT],  # GeoJSON: [lon, lat]
    },
    "properties": {
        "Name": _STATION_NAME,
        "brand": "Petro-Canada",
        "Status": "En opération",
        "Address": _STATION_ADDRESS,
        "PostalCode": "H2X 1Y2",
        "Region": "Montréal",
        "Prices": [
            {"GasType": "Régulier", "Price": "179.9¢", "IsAvailable": True},
            {"GasType": "Super", "Price": "195.9¢", "IsAvailable": True},
            {"GasType": "Diesel", "Price": "185.4¢", "IsAvailable": True},
        ],
    },
}

_FAR_FEATURE: dict = {
    "type": "Feature",
    "geometry": {
        "type": "Point",
        "coordinates": [-71.1097, 46.8139],  # Québec city — far from Montréal
    },
    "properties": {
        "Name": "Shell Québec",
        "brand": "Shell",
        "Status": "En opération",
        "Address": "789 Boul. Laurier, Québec",
        "PostalCode": "G1V 2M2",
        "Region": "Capitale-Nationale",
        "Prices": [
            {"GasType": "Régulier", "Price": "182.9¢", "IsAvailable": True},
            {"GasType": "Diesel", "Price": "190.0¢", "IsAvailable": True},
            {"GasType": "Super", "Price": None, "IsAvailable": False},
        ],
    },
}

_BASE_GEOJSON: dict = {
    "type": "FeatureCollection",
    "features": [_BASE_FEATURE],
}


# ---------------------------------------------------------------------------
# Test helpers
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


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() returns *response*."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _provider(
    station_id: str = _STATION_ID,
    lat: float = _LAT,
    lng: float = _LNG,
    radius_km: float = _RADIUS_KM,
) -> CaQcProvider:
    """Create a CaQcProvider with default test parameters."""
    return CaQcProvider(
        station_id=station_id,
        latitude=lat,
        longitude=lng,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """CaQcProvider declares COUNTRY='CA'."""
    assert CaQcProvider.COUNTRY == "CA"


def test_provider_key() -> None:
    """CaQcProvider declares PROVIDER_KEY='ca_qc'."""
    assert CaQcProvider.PROVIDER_KEY == "ca_qc"


def test_provider_label_contains_regie() -> None:
    """CaQcProvider LABEL mentions Régie or Québec."""
    label_lower = CaQcProvider.LABEL.lower()
    assert "régie" in label_lower or "regie" in label_lower or "québec" in label_lower


def test_provider_config_mode_is_station_id() -> None:
    """CONFIG_MODE must be 'station_id'."""
    assert CaQcProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """STATION_LOOKUP_MODE must be 'location_search'."""
    assert CaQcProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_no_api_key() -> None:
    """Provider requires no API key."""
    assert CaQcProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_is_3600() -> None:
    """POLL_INTERVAL_SECONDS must be 3600 (1 hour)."""
    assert CaQcProvider.POLL_INTERVAL_SECONDS == 3600


def test_capabilities_include_unleaded() -> None:
    assert "unleaded" in CaQcProvider.CAPABILITIES


def test_capabilities_include_diesel() -> None:
    assert "diesel" in CaQcProvider.CAPABILITIES


def test_capabilities_include_premium_unleaded() -> None:
    assert "premium_unleaded" in CaQcProvider.CAPABILITIES


def test_capabilities_include_name() -> None:
    assert "name" in CaQcProvider.CAPABILITIES


def test_capabilities_include_brand() -> None:
    assert "brand" in CaQcProvider.CAPABILITIES


def test_capabilities_include_address() -> None:
    assert "address" in CaQcProvider.CAPABILITIES


def test_capabilities_include_county() -> None:
    assert "county" in CaQcProvider.CAPABILITIES


def test_capabilities_include_latitude() -> None:
    assert "latitude" in CaQcProvider.CAPABILITIES


def test_capabilities_include_longitude() -> None:
    assert "longitude" in CaQcProvider.CAPABILITIES


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    assert "last_successful_fetch" not in CaQcProvider.CAPABILITIES
    assert "data_fetch_problem" not in CaQcProvider.CAPABILITIES


def test_geojson_url_points_to_regieessence() -> None:
    """_GEOJSON_URL targets the official consumer portal."""
    assert "regieessencequebec.ca" in _GEOJSON_URL
    assert _GEOJSON_URL.startswith("https://")
    assert "stations.geojson" in _GEOJSON_URL


def test_headers_include_user_agent() -> None:
    """_HEADERS must include a non-blank User-Agent."""
    ua = _HEADERS.get("User-Agent", "")
    assert ua, "User-Agent must not be empty"


def test_gas_type_map_regulier_to_unleaded() -> None:
    """Régulier maps to unleaded."""
    assert _GAS_TYPE_MAP.get("Régulier") == "unleaded"


def test_gas_type_map_super_to_premium_unleaded() -> None:
    """Super maps to premium_unleaded."""
    assert _GAS_TYPE_MAP.get("Super") == "premium_unleaded"


def test_gas_type_map_diesel_to_diesel() -> None:
    """Diesel maps to diesel."""
    assert _GAS_TYPE_MAP.get("Diesel") == "diesel"


# ---------------------------------------------------------------------------
# _make_station_id
# ---------------------------------------------------------------------------


def test_make_station_id_returns_16_char_hex() -> None:
    """_make_station_id returns a 16-character hex string."""
    sid = _make_station_id("Station A", "123 Main St")
    assert len(sid) == 16
    assert all(c in "0123456789abcdef" for c in sid)


def test_make_station_id_is_deterministic() -> None:
    """Same name+address always produces the same ID."""
    sid1 = _make_station_id("Petro-Canada", "123 Rue Principale, Montréal")
    sid2 = _make_station_id("Petro-Canada", "123 Rue Principale, Montréal")
    assert sid1 == sid2


def test_make_station_id_differs_by_name() -> None:
    """Different names produce different IDs for the same address."""
    sid1 = _make_station_id("Shell", "100 Main St")
    sid2 = _make_station_id("Esso", "100 Main St")
    assert sid1 != sid2


def test_make_station_id_differs_by_address() -> None:
    """Different addresses produce different IDs for the same name."""
    sid1 = _make_station_id("Shell", "100 Main St")
    sid2 = _make_station_id("Shell", "200 Main St")
    assert sid1 != sid2


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_standard_cents_string() -> None:
    """'189.9¢' → 1.899 CAD/L."""
    assert _parse_price("189.9¢") == pytest.approx(1.899)


def test_parse_price_without_symbol() -> None:
    """'179.9' (no ¢) still parses as cents → 1.799 CAD/L."""
    assert _parse_price("179.9") == pytest.approx(1.799)


def test_parse_price_none_returns_none() -> None:
    """None input returns None."""
    assert _parse_price(None) is None


def test_parse_price_empty_string_returns_none() -> None:
    """Empty string returns None."""
    assert _parse_price("") is None


def test_parse_price_zero_returns_none() -> None:
    """Zero price is invalid and returns None."""
    assert _parse_price("0¢") is None


def test_parse_price_negative_returns_none() -> None:
    """Negative price is invalid and returns None."""
    assert _parse_price("-10¢") is None


def test_parse_price_non_numeric_returns_none() -> None:
    """Non-numeric string returns None."""
    assert _parse_price("n/a") is None


def test_parse_price_rounds_to_4_decimal_places() -> None:
    """Result is rounded to 4 decimal places."""
    result = _parse_price("189.123456¢")
    assert result is not None
    assert result == round(result, 4)


def test_parse_price_converts_cents_to_dollars() -> None:
    """200¢ = $2.00/L exactly."""
    assert _parse_price("200.0¢") == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _build_station_data
# ---------------------------------------------------------------------------


def test_build_station_data_returns_dict() -> None:
    """_build_station_data returns a dict."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert isinstance(result, dict)


def test_build_station_data_unleaded_price() -> None:
    """Régulier → unleaded price correctly parsed."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["unleaded"] == pytest.approx(1.799)


def test_build_station_data_premium_unleaded_price() -> None:
    """Super → premium_unleaded price correctly parsed."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["premium_unleaded"] == pytest.approx(1.959)


def test_build_station_data_diesel_price() -> None:
    """Diesel → diesel price correctly parsed."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["diesel"] == pytest.approx(1.854)


def test_build_station_data_name() -> None:
    """name field taken from 'Name' property."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["name"] == _STATION_NAME


def test_build_station_data_brand() -> None:
    """brand field taken from 'brand' property."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["brand"] == "Petro-Canada"


def test_build_station_data_address_includes_street() -> None:
    """address includes street portion from 'Address' property."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["address"] is not None
    assert "Rue Principale" in result["address"]


def test_build_station_data_address_includes_postal_code() -> None:
    """address includes postal code when available."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert "H2X 1Y2" in result["address"]


def test_build_station_data_county_from_region() -> None:
    """county taken from 'Region' property."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["county"] == "Montréal"


def test_build_station_data_latitude() -> None:
    """latitude extracted from GeoJSON coordinates [lon, lat]."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["latitude"] == pytest.approx(_LAT)


def test_build_station_data_longitude() -> None:
    """longitude extracted from GeoJSON coordinates [lon, lat]."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["longitude"] == pytest.approx(_LNG)


def test_build_station_data_source_station_id() -> None:
    """source_station_id matches the passed station_id."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["source_station_id"] == _STATION_ID


def test_build_station_data_lastupdated_is_none() -> None:
    """lastupdated is None (API provides no per-station timestamp)."""
    result = _build_station_data(_BASE_FEATURE, _STATION_ID)
    assert result["lastupdated"] is None


def test_build_station_data_unavailable_price_is_none() -> None:
    """Fuel type with IsAvailable=False results in None price."""
    feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Prices": [
                {"GasType": "Régulier", "Price": "179.9¢", "IsAvailable": True},
                {"GasType": "Super", "Price": None, "IsAvailable": False},
                {"GasType": "Diesel", "Price": None, "IsAvailable": False},
            ],
        },
    }
    result = _build_station_data(feature, _STATION_ID)
    assert result["unleaded"] == pytest.approx(1.799)
    assert result["premium_unleaded"] is None
    assert result["diesel"] is None


def test_build_station_data_missing_geometry_coords() -> None:
    """Feature with missing geometry returns None for lat/lon."""
    feature = {**_BASE_FEATURE, "geometry": None}
    result = _build_station_data(feature, _STATION_ID)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_build_station_data_empty_prices_all_none() -> None:
    """Empty Prices list → all fuel prices None."""
    feature = {
        **_BASE_FEATURE,
        "properties": {**_BASE_FEATURE["properties"], "Prices": []},
    }
    result = _build_station_data(feature, _STATION_ID)
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["diesel"] is None


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    p = CaQcProvider("abc123def456abcd")
    assert p._station_id == "abc123def456abcd"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores lat/lng."""
    p = CaQcProvider("abc", latitude=45.5, longitude=-73.6)
    assert p._latitude == pytest.approx(45.5)
    assert p._longitude == pytest.approx(-73.6)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    p = CaQcProvider("abc", latitude=45.5, longitude=-73.6, radius_km=7.5)
    assert p._radius_km == pytest.approx(7.5)


def test_constructor_accepts_none_coordinates() -> None:
    """Constructor accepts None coordinates without raising."""
    p = CaQcProvider("abc")
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# async_fetch — success
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a StationData dict on success."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_returns_unleaded_price() -> None:
    """async_fetch returns correct unleaded price (Régulier 179.9¢ → 1.799)."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.799)


async def test_async_fetch_returns_diesel_price() -> None:
    """async_fetch returns correct diesel price (185.4¢ → 1.854)."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.854)


async def test_async_fetch_returns_premium_unleaded_price() -> None:
    """async_fetch returns correct premium_unleaded price (Super 195.9¢ → 1.959)."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.959)


async def test_async_fetch_returns_station_name() -> None:
    """async_fetch populates name field."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["name"] == _STATION_NAME


async def test_async_fetch_returns_brand() -> None:
    """async_fetch populates brand field."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["brand"] == "Petro-Canada"


async def test_async_fetch_returns_county_from_region() -> None:
    """async_fetch maps Region → county."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["county"] == "Montréal"


async def test_async_fetch_returns_latitude() -> None:
    """async_fetch populates latitude from GeoJSON coordinates."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(_LAT)


async def test_async_fetch_returns_longitude() -> None:
    """async_fetch populates longitude from GeoJSON coordinates."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["longitude"] == pytest.approx(_LNG)


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch result contains all CAPABILITIES keys (except sentinel keys)."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    capability_data_keys = CaQcProvider.CAPABILITIES - {
        "last_successful_fetch",
        "data_fetch_problem",
    }
    for key in capability_data_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


async def test_async_fetch_makes_exactly_one_api_call() -> None:
    """async_fetch issues exactly one GET request."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    assert session.get.call_count == 1


async def test_async_fetch_calls_correct_url() -> None:
    """async_fetch calls the GeoJSON endpoint URL."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    call_url = session.get.call_args[0][0]
    assert "regieessencequebec.ca" in call_url or call_url == _GEOJSON_URL


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when the station ID is not in the dataset."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider(station_id="0000000000000000")  # does not exist
    with pytest.raises(ProviderError, match="0000000000000000"):
        await p.async_fetch(session, "0000000000000000")


async def test_async_fetch_raises_provider_error_when_empty_features() -> None:
    """async_fetch raises ProviderError when the dataset has no features."""
    empty_geojson = {"type": "FeatureCollection", "features": []}
    resp = _make_mock_response(200, json_data=empty_geojson)
    session = _make_session(resp)

    p = _provider()
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_propagates_client_error() -> None:
    """ClientError from aiohttp propagates out of async_fetch."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    p = _provider()
    with pytest.raises(ClientError):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_http_500() -> None:
    """HTTP 500 causes raise_for_status() to propagate an error."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = _make_session(resp)

    p = _provider()
    with pytest.raises((ClientError, ProviderError)):
        await p.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns the station name on success."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name == _STATION_NAME


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station ID is absent."""
    resp = _make_mock_response(200, json_data=_BASE_GEOJSON)
    session = _make_session(resp)

    p = _provider(station_id="0000000000000000")
    name = await p.async_fetch_station_name(session, "0000000000000000")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a ClientError occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (station_id, label) tuples."""
    geojson = {"type": "FeatureCollection", "features": [_BASE_FEATURE]}
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert isinstance(result, list)
    assert len(result) == 1
    sid, label = result[0]
    assert sid == _STATION_ID
    assert isinstance(label, str)


async def test_async_list_stations_label_contains_station_name() -> None:
    """Each label in async_list_stations includes the station name or brand."""
    geojson = {"type": "FeatureCollection", "features": [_BASE_FEATURE]}
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    _, label = result[0]
    assert "Petro-Canada" in label or _STATION_NAME in label


async def test_async_list_stations_label_contains_price() -> None:
    """Each label includes the short station ID in (#...) format."""
    geojson = {"type": "FeatureCollection", "features": [_BASE_FEATURE]}
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    _, label = result[0]
    # Should contain the short station ID marker
    assert "(#" in label


async def test_async_list_stations_filters_by_radius() -> None:
    """Stations outside the radius are excluded."""
    geojson = {
        "type": "FeatureCollection",
        "features": [_BASE_FEATURE, _FAR_FEATURE],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    # _FAR_FEATURE is in Québec city (~250 km away) and should be excluded
    ids = {sid for sid, _ in result}
    assert _STATION_ID in ids
    assert _make_station_id("Shell Québec", "789 Boul. Laurier, Québec") not in ids


async def test_async_list_stations_includes_station_within_radius() -> None:
    """Station within the radius appears in results."""
    geojson = {"type": "FeatureCollection", "features": [_BASE_FEATURE]}
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    ids = {sid for sid, _ in result}
    assert _STATION_ID in ids


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when no coordinates are available."""
    session = MagicMock()
    p = CaQcProvider("abc")  # no lat/lng

    result = await p.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_returns_empty_when_no_features() -> None:
    """async_list_stations returns [] when the dataset has no features."""
    empty_geojson = {"type": "FeatureCollection", "features": []}
    resp = _make_mock_response(200, json_data=empty_geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert result == []


async def test_async_list_stations_uses_constructor_coordinates_as_fallback() -> None:
    """async_list_stations uses constructor lat/lng when not passed as kwargs."""
    geojson = {"type": "FeatureCollection", "features": [_BASE_FEATURE]}
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider(lat=_LAT, lng=_LNG)
    result = await p.async_list_stations(session)  # no lat/lng kwargs

    assert len(result) == 1


async def test_async_list_stations_sorted_by_distance() -> None:
    """async_list_stations sorts results alphabetically by label."""
    # Two stations, both near Montréal but at different distances
    near_feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Name": "Near Station",
            "Address": "1 Rue Near, Montréal",
        },
        "geometry": {"type": "Point", "coordinates": [_LNG + 0.001, _LAT + 0.001]},
    }
    far_feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Name": "Far Station",
            "Address": "2 Rue Far, Montréal",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [_LNG + 0.03, _LAT + 0.03],
        },
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [far_feature, near_feature],  # far first in data
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG, radius_km=20.0)

    assert len(result) == 2
    first_sid, _ = result[0]
    far_id = _make_station_id("Far Station", "2 Rue Far, Montréal")
    # Alphabetical: "Far Station" < "Near Station"
    assert first_sid == far_id, "Alphabetically first station should be listed first"


async def test_async_list_stations_multiple_stations_all_returned() -> None:
    """async_list_stations returns all stations within radius."""
    second_feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Name": "Shell Ste-Catherine",
            "Address": "456 Rue Ste-Catherine, Montréal",
        },
        "geometry": {"type": "Point", "coordinates": [_LNG + 0.01, _LAT - 0.01]},
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [_BASE_FEATURE, second_feature],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert len(result) == 2


async def test_async_list_stations_station_with_no_prices_included() -> None:
    """Stations with no available prices still appear in list, sorted alphabetically."""
    no_price_feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Name": "Closed Pump",
            "Address": "999 Rue Fermée, Montréal",
            "Prices": [
                {"GasType": "Régulier", "Price": None, "IsAvailable": False},
            ],
        },
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [no_price_feature, _BASE_FEATURE],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    # Both stations are within radius and should appear
    assert len(result) == 2
    # Alphabetical: "Closed Pump" < "Petro-Canada Centre-Ville" → no-price station is first
    first_sid, _ = result[0]
    assert first_sid == _make_station_id("Closed Pump", "999 Rue Fermée, Montréal")


async def test_async_list_stations_skips_features_with_missing_coords() -> None:
    """Features with null geometry are skipped silently."""
    null_geo_feature = {**_BASE_FEATURE, "geometry": None}
    geojson = {
        "type": "FeatureCollection",
        "features": [null_geo_feature, _BASE_FEATURE],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    # Only the valid feature should be returned
    assert len(result) == 1
    sid, _ = result[0]
    assert sid == _STATION_ID


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """CaQcProvider is registered in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "ca_qc" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["ca_qc"] is CaQcProvider


@pytest.fixture(autouse=True)
def reset_ca_qc_cache():
    """Reset class-level GeoJSON cache between tests."""
    from custom_components.fuelcompare_ie.providers.ca_qc import CaQcProvider

    CaQcProvider._geojson_cache = None
    CaQcProvider._geojson_cache_ts = 0
    yield
    CaQcProvider._geojson_cache = None
    CaQcProvider._geojson_cache_ts = 0


# ---------------------------------------------------------------------------
# _parse_price — ValueError/TypeError path (lines 135–136)
# ---------------------------------------------------------------------------


def test_parse_price_malformed_numeric_returns_none() -> None:
    """String with multiple decimal points passes the empty-check but fails float()."""
    # After _CENTS_RE strips non-digits/dots, '1.2.3' remains, which raises ValueError
    assert _parse_price("1.2.3¢") is None


# ---------------------------------------------------------------------------
# _build_station_data — malformed coordinates (lines 161–163)
# ---------------------------------------------------------------------------


def test_build_station_data_non_numeric_coords_returns_none_latlon() -> None:
    """Non-numeric coordinate values cause lat/lon to fall back to None."""
    feature = {
        **_BASE_FEATURE,
        "geometry": {
            "type": "Point",
            "coordinates": ["not-a-number", "also-not-a-number"],
        },
    }
    result = _build_station_data(feature, _STATION_ID)
    assert result["latitude"] is None
    assert result["longitude"] is None


# ---------------------------------------------------------------------------
# _build_station_data — unknown gas type (line 191)
# ---------------------------------------------------------------------------


def test_build_station_data_unknown_gas_type_is_skipped() -> None:
    """Unmapped GasType (e.g. 'Premium Diesel') is skipped; known prices unaffected."""
    feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Prices": [
                {"GasType": "Régulier", "Price": "179.9¢", "IsAvailable": True},
                {"GasType": "Premium Diesel", "Price": "200.0¢", "IsAvailable": True},
            ],
        },
    }
    result = _build_station_data(feature, _STATION_ID)
    assert result["unleaded"] == pytest.approx(1.799)
    # Premium Diesel is not in _GAS_TYPE_MAP — none of the mapped keys get its value
    assert result["diesel"] is None
    assert result["premium_unleaded"] is None


# ---------------------------------------------------------------------------
# async_list_stations — malformed coordinates (lines 406–407)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_features_with_non_numeric_coords() -> None:
    """Features whose coordinates are non-numeric strings are skipped via continue."""
    bad_coords_feature = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Name": "Bad Coords Station",
            "Address": "1 Rue Invalide, Montréal",
        },
        "geometry": {
            "type": "Point",
            "coordinates": ["bad", "coords"],
        },
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [bad_coords_feature, _BASE_FEATURE],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    # Bad-coords feature must be skipped; only _BASE_FEATURE is returned
    assert len(result) == 1
    sid, _ = result[0]
    assert sid == _STATION_ID


# ---------------------------------------------------------------------------
# async_list_stations — unknown gas type (line 432)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_unknown_gas_type_in_price_summary() -> None:
    """Unmapped GasType entries are skipped; label still contains short station ID."""
    feature_with_unknown = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Prices": [
                {"GasType": "E85", "Price": "150.0¢", "IsAvailable": True},
                {"GasType": "Régulier", "Price": "179.9¢", "IsAvailable": True},
            ],
        },
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [feature_with_unknown],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert len(result) == 1
    _, label = result[0]
    # Label contains short station ID marker; no price in label
    assert "(#" in label


# ---------------------------------------------------------------------------
# async_list_stations — missing price (line 435)
# ---------------------------------------------------------------------------


async def test_async_list_stations_skips_unparseable_price_in_summary() -> None:
    """Price entries where _parse_price returns None are skipped; label still has short ID."""
    feature_bad_price = {
        **_BASE_FEATURE,
        "properties": {
            **_BASE_FEATURE["properties"],
            "Prices": [
                {"GasType": "Régulier", "Price": None, "IsAvailable": True},
                {"GasType": "Diesel", "Price": "185.4¢", "IsAvailable": True},
            ],
        },
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [feature_bad_price],
    }
    resp = _make_mock_response(200, json_data=geojson)
    session = _make_session(resp)

    p = _provider()
    result = await p.async_list_stations(
        session, lat=_LAT, lng=_LNG, radius_km=_RADIUS_KM
    )

    assert len(result) == 1
    _, label = result[0]
    # Label contains short station ID marker; no price strings in label
    assert "(#" in label


# ---------------------------------------------------------------------------
# _fetch_geojson — cache hit path (lines 487–488)
# ---------------------------------------------------------------------------


async def test_fetch_geojson_returns_cached_data_without_http_call() -> None:
    """_fetch_geojson serves from cache when data is fresh (within TTL)."""
    import time

    cached_features = [_BASE_FEATURE]
    CaQcProvider._geojson_cache = cached_features
    CaQcProvider._geojson_cache_ts = time.monotonic()  # just set → within TTL

    session = MagicMock()

    p = _provider()
    result = await p._fetch_geojson(session)

    assert result is cached_features
    session.get.assert_not_called()
