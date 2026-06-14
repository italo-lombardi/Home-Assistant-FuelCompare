"""Tests for AuQldProvider — Queensland Fuel Prices Scheme (Australia)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.au_qld import (
    AuQldProvider,
    _FUELID_MAP,
    _SITES_URL,
    _PRICES_URL,
    _build_index,
    _build_station_data,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

_SITE_ID = "61403154"
_SITE_ID_2 = "61403200"

_BASE_SITE: dict = {
    "S": int(_SITE_ID),
    "N": "BP Brisbane CBD",
    "A": "100 Queen Street",
    "B": "BP",
    "P": "4000",
    "Lat": -27.4698,
    "Lng": 153.0251,
}

# Price unit: tenths of a cent → divide by 10 = cents/L.
# e.g. 1799 → 179.9 c/L; coordinator then divides by 100 → 1.799 AUD/L.
_BASE_PRICES: list[dict] = [
    {"SiteId": int(_SITE_ID), "FuelId": 2, "Price": 1799},  # unleaded 179.9 c/L
    {"SiteId": int(_SITE_ID), "FuelId": 3, "Price": 1759},  # diesel 175.9 c/L
    {"SiteId": int(_SITE_ID), "FuelId": 12, "Price": 1699},  # e10 169.9 c/L
    {"SiteId": int(_SITE_ID), "FuelId": 5, "Price": 1899},  # premium_unleaded 189.9 c/L
    {"SiteId": int(_SITE_ID), "FuelId": 11, "Price": 1959},  # premium_diesel 195.9 c/L
]

_SITES_PAYLOAD: dict = {"S": [_BASE_SITE]}
_PRICES_PAYLOAD: dict = {"SitePrices": _BASE_PRICES}


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session_two_responses(
    sites_resp: AsyncMock,
    prices_resp: AsyncMock,
) -> MagicMock:
    """Return a mock session whose .get() alternates between two responses."""
    session = MagicMock()
    session.get = MagicMock(side_effect=[sites_resp, prices_resp])
    return session


def _make_session_both(
    sites_payload: dict = _SITES_PAYLOAD,
    prices_payload: dict = _PRICES_PAYLOAD,
    status: int = 200,
) -> MagicMock:
    """Return a mock session that returns both sites and prices payloads."""
    sites_resp = _make_mock_response(status, json_data=sites_payload)
    prices_resp = _make_mock_response(status, json_data=prices_payload)
    return _make_session_two_responses(sites_resp, prices_resp)


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """AuQldProvider declares COUNTRY='AU'."""
    assert AuQldProvider.COUNTRY == "AU"


def test_provider_key() -> None:
    """AuQldProvider declares PROVIDER_KEY='au_qld'."""
    assert AuQldProvider.PROVIDER_KEY == "au_qld"


def test_provider_label() -> None:
    """AuQldProvider has a descriptive label mentioning QLD."""
    assert "QLD" in AuQldProvider.LABEL


def test_provider_config_mode() -> None:
    """AuQldProvider uses station_id CONFIG_MODE."""
    assert AuQldProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """AuQldProvider uses location_search STATION_LOOKUP_MODE."""
    assert AuQldProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """AuQldProvider default poll interval is 3600 seconds."""
    assert AuQldProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_requires_api_key() -> None:
    """AuQldProvider declares REQUIRES_API_KEY=True."""
    assert AuQldProvider.REQUIRES_API_KEY is True


def test_provider_api_key_registration_url_set() -> None:
    """AuQldProvider has a non-empty API_KEY_REGISTRATION_URL."""
    assert AuQldProvider.API_KEY_REGISTRATION_URL.startswith("https://")


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all declared fuel types."""
    caps = AuQldProvider.CAPABILITIES
    for fuel in ("unleaded", "e10", "diesel", "premium_unleaded", "premium_diesel"):
        assert fuel in caps, f"'{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = AuQldProvider.CAPABILITIES
    for field in (
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
    ):
        assert field in caps, f"'{field}' missing from CAPABILITIES"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    assert "last_successful_fetch" in AuQldProvider.CAPABILITIES
    assert "data_fetch_problem" in AuQldProvider.CAPABILITIES


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores the station_id."""
    p = AuQldProvider(_SITE_ID, api_key="tok123")
    assert p._station_id == _SITE_ID


def test_constructor_stores_api_key() -> None:
    """Constructor stores the api_key."""
    p = AuQldProvider(_SITE_ID, api_key="my_token")
    assert p._api_key == "my_token"


def test_constructor_empty_api_key_defaults_to_empty_string() -> None:
    """Constructor defaults api_key to '' when not supplied."""
    p = AuQldProvider(_SITE_ID)
    assert p._api_key == ""


def test_constructor_stores_lat_lng_radius() -> None:
    """Constructor stores lat, lng, and radius_km."""
    p = AuQldProvider(_SITE_ID, latitude=-27.4, longitude=153.0, radius_km=5.0)
    assert p._latitude == pytest.approx(-27.4)
    assert p._longitude == pytest.approx(153.0)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_defaults_radius_to_10() -> None:
    """Constructor defaults radius_km to 10.0."""
    p = AuQldProvider(_SITE_ID)
    assert p._radius_km == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data_dict() -> None:
    """async_fetch returns a non-None dict on success."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_unleaded_price_is_cents() -> None:
    """async_fetch returns unleaded price in cents/L (> 10; coordinator /100 rule applies)."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    # FuelId=2, Price=1799 → 179.9 c/L
    assert data["unleaded"] == pytest.approx(179.9)
    assert data["unleaded"] > 10.0  # must be raw cents, not AUD


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns diesel price in cents/L."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["diesel"] == pytest.approx(175.9)


async def test_async_fetch_e10_price() -> None:
    """async_fetch maps FuelId=12 to e10 in cents/L."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["e10"] == pytest.approx(169.9)


async def test_async_fetch_premium_unleaded_price() -> None:
    """async_fetch maps FuelId=5 to premium_unleaded in cents/L."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["premium_unleaded"] == pytest.approx(189.9)


async def test_async_fetch_premium_diesel_price() -> None:
    """async_fetch maps FuelId=11 to premium_diesel in cents/L."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["premium_diesel"] == pytest.approx(195.9)


async def test_async_fetch_name_field() -> None:
    """async_fetch populates name from the site record."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["name"] == "BP Brisbane CBD"


async def test_async_fetch_brand_field() -> None:
    """async_fetch populates brand from the site record."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["brand"] == "BP"


async def test_async_fetch_address_includes_postcode() -> None:
    """async_fetch appends postcode to address when not already present."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert "4000" in data["address"]


async def test_async_fetch_county_is_qld() -> None:
    """async_fetch sets county='QLD' (implicit for all QLD stations)."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["county"] == "QLD"


async def test_async_fetch_latitude_field() -> None:
    """async_fetch populates latitude from site Lat field."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["latitude"] == pytest.approx(-27.4698)


async def test_async_fetch_longitude_field() -> None:
    """async_fetch populates longitude from site Lng field."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["longitude"] == pytest.approx(153.0251)


async def test_async_fetch_source_station_id_field() -> None:
    """async_fetch sets source_station_id to the station ID string."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["source_station_id"] == _SITE_ID


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch populates every non-sentinel key declared in CAPABILITIES."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    for key in AuQldProvider.CAPABILITIES - sentinel_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


async def test_async_fetch_missing_fuel_prices_are_none() -> None:
    """async_fetch returns None for fuel types absent from the API response."""
    prices_no_lpg: dict = {"SitePrices": _BASE_PRICES}  # no LPG entry
    session = _make_session_both(prices_payload=prices_no_lpg)
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["lpg"] is None
    assert data["e85"] is None


# ---------------------------------------------------------------------------
# async_fetch — FuelId 8 (PULP 98) merges into premium_unleaded
# ---------------------------------------------------------------------------


async def test_async_fetch_fuelid8_maps_to_premium_unleaded() -> None:
    """FuelId=8 (98 RON) also maps to premium_unleaded; lower price wins."""
    prices_with_98: list[dict] = _BASE_PRICES + [
        {
            "SiteId": int(_SITE_ID),
            "FuelId": 8,
            "Price": 1999,
        },  # 199.9 c/L (more expensive)
    ]
    session = _make_session_both(prices_payload={"SitePrices": prices_with_98})
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    # FuelId=5 (189.9 c/L) is cheaper than FuelId=8 (199.9 c/L) — keep the lower
    assert data["premium_unleaded"] == pytest.approx(189.9)


async def test_async_fetch_fuelid8_wins_when_cheaper() -> None:
    """FuelId=8 price wins when it is lower than FuelId=5."""
    prices_98_cheaper: list[dict] = [
        {"SiteId": int(_SITE_ID), "FuelId": 5, "Price": 1999},  # 199.9 c/L
        {"SiteId": int(_SITE_ID), "FuelId": 8, "Price": 1889},  # 188.9 c/L — cheaper
    ]
    session = _make_session_both(prices_payload={"SitePrices": prices_98_cheaper})
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    data = await provider.async_fetch(session, _SITE_ID)

    assert data["premium_unleaded"] == pytest.approx(188.9)


# ---------------------------------------------------------------------------
# async_fetch — error cases
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when SiteId is absent from dataset."""
    session = _make_session_both()
    provider = AuQldProvider("NONEXISTENT", api_key="tok")

    with pytest.raises(ProviderError, match="not found"):
        await provider.async_fetch(session, "NONEXISTENT")


async def test_async_fetch_raises_provider_error_on_403() -> None:
    """async_fetch raises ProviderError on HTTP 403 (bad token)."""
    sites_resp = _make_mock_response(403)
    prices_resp = _make_mock_response(403)
    session = _make_session_two_responses(sites_resp, prices_resp)
    provider = AuQldProvider(_SITE_ID, api_key="bad_token")

    with pytest.raises(ProviderError, match="403"):
        await provider.async_fetch(session, _SITE_ID)


async def test_async_fetch_raises_provider_error_on_http_error() -> None:
    """async_fetch raises ProviderError on non-200/403 HTTP error."""
    from aiohttp import ClientResponseError

    sites_resp = _make_mock_response(500)
    sites_resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(MagicMock(), MagicMock(), status=500)
    )
    prices_resp = _make_mock_response(500)
    prices_resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(MagicMock(), MagicMock(), status=500)
    )
    session = _make_session_two_responses(sites_resp, prices_resp)
    provider = AuQldProvider(_SITE_ID, api_key="tok")

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _SITE_ID)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp ClientError on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = AuQldProvider(_SITE_ID, api_key="tok")

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _SITE_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns the station name on success."""
    # Only sites are needed; prices can be empty for name lookup.
    sites_resp = _make_mock_response(200, json_data=_SITES_PAYLOAD)
    prices_resp = _make_mock_response(200, json_data={"SitePrices": []})
    session = _make_session_two_responses(sites_resp, prices_resp)
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    name = await provider.async_fetch_station_name(session, _SITE_ID)

    assert name == "BP Brisbane CBD"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when SiteId not in dataset."""
    session = _make_session_both()
    provider = AuQldProvider("UNKNOWN", api_key="tok")
    name = await provider.async_fetch_station_name(session, "UNKNOWN")

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None on network failure (swallows exception)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    name = await provider.async_fetch_station_name(session, _SITE_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_403() -> None:
    """async_fetch_station_name returns None when API returns 403."""
    sites_resp = _make_mock_response(403)
    prices_resp = _make_mock_response(403)
    session = _make_session_two_responses(sites_resp, prices_resp)
    provider = AuQldProvider(_SITE_ID, api_key="bad")
    name = await provider.async_fetch_station_name(session, _SITE_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------

_SITE_NEARBY: dict = {
    "S": int(_SITE_ID_2),
    "N": "Caltex South Brisbane",
    "A": "50 Melbourne Street",
    "B": "Caltex",
    "P": "4101",
    "Lat": -27.480,
    "Lng": 153.013,
}

_PRICES_NEARBY: list[dict] = [
    {"SiteId": int(_SITE_ID_2), "FuelId": 2, "Price": 1779},  # unleaded 177.9 c/L
    {"SiteId": int(_SITE_ID_2), "FuelId": 3, "Price": 1739},  # diesel 173.9 c/L
]


async def test_async_list_stations_returns_stations_in_radius() -> None:
    """async_list_stations returns stations within the specified radius."""
    sites = {"S": [_BASE_SITE, _SITE_NEARBY]}
    prices = {"SitePrices": _BASE_PRICES + _PRICES_NEARBY}
    session = _make_session_both(sites_payload=sites, prices_payload=prices)
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.47, longitude=153.02, radius_km=20.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.02, radius_km=20.0
    )

    site_ids = [sid for sid, _ in result]
    assert _SITE_ID in site_ids
    assert _SITE_ID_2 in site_ids


async def test_async_list_stations_excludes_out_of_radius_stations() -> None:
    """async_list_stations excludes stations outside the radius."""
    far_site = {
        **_BASE_SITE,
        "S": 99999,
        "N": "Far Station",
        "Lat": -30.0,
        "Lng": 153.0,
    }
    sites = {"S": [far_site, _SITE_NEARBY]}
    prices = {"SitePrices": _PRICES_NEARBY}
    session = _make_session_both(sites_payload=sites, prices_payload=prices)
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.47, longitude=153.02, radius_km=5.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.02, radius_km=5.0
    )

    site_ids = [sid for sid, _ in result]
    assert "99999" not in site_ids


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns stations sorted cheapest-first."""
    expensive_site = {
        **_BASE_SITE,
        "S": 77777,
        "N": "Expensive",
        "Lat": -27.471,
        "Lng": 153.026,
    }
    expensive_prices = [
        {"SiteId": 77777, "FuelId": 3, "Price": 2100}
    ]  # 210.0 c/L diesel
    sites = {"S": [_BASE_SITE, expensive_site]}
    prices = {"SitePrices": _BASE_PRICES + expensive_prices}
    session = _make_session_both(sites_payload=sites, prices_payload=prices)
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.47, longitude=153.025, radius_km=5.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.025, radius_km=5.0
    )

    ids = [sid for sid, _ in result]
    # _SITE_ID diesel=175.9 is cheaper than 77777 diesel=210.0
    assert ids.index(_SITE_ID) < ids.index("77777")


async def test_async_list_stations_label_includes_a_dollar() -> None:
    """async_list_stations label includes 'A$' currency marker."""
    session = _make_session_both()
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.4698, longitude=153.0251, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.4698, lng=153.0251, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "A$" in label


async def test_async_list_stations_label_includes_diesel() -> None:
    """async_list_stations label includes 'Diesel' when diesel price available."""
    session = _make_session_both()
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.4698, longitude=153.0251, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.4698, lng=153.0251, radius_km=1.0
    )

    _, label = result[0]
    assert "Diesel" in label


async def test_async_list_stations_label_includes_unleaded() -> None:
    """async_list_stations label includes 'Unleaded' when unleaded price available."""
    session = _make_session_both()
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.4698, longitude=153.0251, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.4698, lng=153.0251, radius_km=1.0
    )

    _, label = result[0]
    assert "Unleaded" in label


async def test_async_list_stations_returns_empty_without_lat_lng() -> None:
    """async_list_stations returns [] when lat/lng not provided."""
    provider = AuQldProvider(_SITE_ID, api_key="tok")
    session = MagicMock()
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] on network failure (swallows exception)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.47, longitude=153.02, radius_km=10.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.02, radius_km=10.0
    )

    assert result == []


async def test_async_list_stations_returns_empty_when_all_out_of_radius() -> None:
    """async_list_stations returns [] when no stations within radius."""
    session = _make_session_both()
    # Centre is far from Brisbane
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-35.0, longitude=149.0, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-35.0, lng=149.0, radius_km=1.0
    )

    assert result == []


async def test_async_list_stations_skips_sites_missing_lat_lng() -> None:
    """async_list_stations silently skips sites with missing coordinates."""
    no_coords_site = {k: v for k, v in _BASE_SITE.items() if k not in ("Lat", "Lng")}
    sites = {"S": [no_coords_site]}
    session = _make_session_both(sites_payload=sites)
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.47, longitude=153.02, radius_km=5.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.02, radius_km=5.0
    )

    assert result == []


async def test_async_list_stations_returns_empty_on_403() -> None:
    """async_list_stations returns [] when API returns 403 (swallows ProviderError)."""
    sites_resp = _make_mock_response(403)
    prices_resp = _make_mock_response(403)
    session = _make_session_two_responses(sites_resp, prices_resp)
    provider = AuQldProvider(
        _SITE_ID, api_key="bad", latitude=-27.47, longitude=153.02, radius_km=10.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.47, lng=153.02, radius_km=10.0
    )

    assert result == []


async def test_async_list_stations_uses_stored_lat_lng_when_not_in_kwargs() -> None:
    """async_list_stations falls back to constructor lat/lng when not passed as kwargs."""
    session = _make_session_both()
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.4698, longitude=153.0251, radius_km=1.0
    )
    result = await provider.async_list_stations(session)  # no kwargs

    assert len(result) == 1
    assert result[0][0] == _SITE_ID


async def test_async_list_stations_no_price_shows_name_only() -> None:
    """async_list_stations label shows name only when no prices available."""
    sites = {"S": [_BASE_SITE]}
    session = _make_session_both(sites_payload=sites, prices_payload={"SitePrices": []})
    provider = AuQldProvider(
        _SITE_ID, api_key="tok", latitude=-27.4698, longitude=153.0251, radius_km=1.0
    )
    result = await provider.async_list_stations(
        session, lat=-27.4698, lng=153.0251, radius_km=1.0
    )

    assert len(result) == 1
    _, label = result[0]
    assert "BP Brisbane CBD" in label
    assert "A$" not in label


# ---------------------------------------------------------------------------
# _build_index (module-level helper)
# ---------------------------------------------------------------------------


def test_build_index_builds_site_map() -> None:
    """_build_index returns site_map keyed by SiteId string."""
    site_map, _ = _build_index([_BASE_SITE], [])
    assert _SITE_ID in site_map
    assert site_map[_SITE_ID]["N"] == "BP Brisbane CBD"


def test_build_index_builds_prices_map() -> None:
    """_build_index returns prices_map with StationData keys."""
    _, prices_map = _build_index([], _BASE_PRICES)
    assert _SITE_ID in prices_map
    assert "unleaded" in prices_map[_SITE_ID]
    assert "diesel" in prices_map[_SITE_ID]


def test_build_index_price_converted_from_tenths_of_cent() -> None:
    """_build_index divides Price by 10 to get cents/L."""
    _, prices_map = _build_index(
        [], [{"SiteId": int(_SITE_ID), "FuelId": 2, "Price": 1799}]
    )
    # 1799 / 10 = 179.9 c/L
    assert prices_map[_SITE_ID]["unleaded"] == pytest.approx(179.9)


def test_build_index_skips_missing_site_id() -> None:
    """_build_index skips price entries with no SiteId."""
    _, prices_map = _build_index([], [{"FuelId": 2, "Price": 1799}])
    assert prices_map == {}


def test_build_index_skips_unknown_fuel_id() -> None:
    """_build_index silently skips unknown FuelId values."""
    _, prices_map = _build_index(
        [], [{"SiteId": int(_SITE_ID), "FuelId": 999, "Price": 1799}]
    )
    assert _SITE_ID not in prices_map


def test_build_index_skips_zero_and_negative_prices() -> None:
    """_build_index discards price entries with zero or negative values."""
    entries = [
        {"SiteId": int(_SITE_ID), "FuelId": 2, "Price": 0},
        {"SiteId": int(_SITE_ID), "FuelId": 3, "Price": -100},
    ]
    _, prices_map = _build_index([], entries)
    assert _SITE_ID not in prices_map or "unleaded" not in prices_map.get(_SITE_ID, {})


def test_build_index_skips_null_prices() -> None:
    """_build_index discards entries where Price is None."""
    entries = [{"SiteId": int(_SITE_ID), "FuelId": 2, "Price": None}]
    _, prices_map = _build_index([], entries)
    station_prices = prices_map.get(_SITE_ID, {})
    assert "unleaded" not in station_prices


def test_build_index_skips_unparseable_prices() -> None:
    """_build_index discards entries where Price cannot be cast to float."""
    entries = [{"SiteId": int(_SITE_ID), "FuelId": 2, "Price": "N/A"}]
    _, prices_map = _build_index([], entries)
    station_prices = prices_map.get(_SITE_ID, {})
    assert "unleaded" not in station_prices


def test_build_index_keeps_lower_price_for_same_key() -> None:
    """_build_index keeps the lower price when two FuelIds map to the same key."""
    entries = [
        {"SiteId": int(_SITE_ID), "FuelId": 5, "Price": 1890},  # 189.0 c/L
        {"SiteId": int(_SITE_ID), "FuelId": 8, "Price": 1990},  # 199.0 c/L
    ]
    _, prices_map = _build_index([], entries)
    assert prices_map[_SITE_ID]["premium_unleaded"] == pytest.approx(189.0)


def test_build_index_keeps_lower_price_when_second_is_cheaper() -> None:
    """_build_index keeps the second price when it is lower."""
    entries = [
        {"SiteId": int(_SITE_ID), "FuelId": 5, "Price": 1990},  # 199.0 c/L
        {"SiteId": int(_SITE_ID), "FuelId": 8, "Price": 1870},  # 187.0 c/L — cheaper
    ]
    _, prices_map = _build_index([], entries)
    assert prices_map[_SITE_ID]["premium_unleaded"] == pytest.approx(187.0)


def test_build_index_handles_empty_input() -> None:
    """_build_index handles completely empty input gracefully."""
    site_map, prices_map = _build_index([], [])
    assert site_map == {}
    assert prices_map == {}


def test_build_index_site_id_coerced_to_string() -> None:
    """_build_index coerces integer SiteId keys to strings."""
    site_map, prices_map = _build_index(
        [_BASE_SITE],
        [{"SiteId": int(_SITE_ID), "FuelId": 2, "Price": 1799}],
    )
    assert _SITE_ID in site_map  # _SITE_ID is already a string
    assert _SITE_ID in prices_map


# ---------------------------------------------------------------------------
# _build_station_data (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_data_returns_expected_keys() -> None:
    """_build_station_data returns a dict with all key StationData fields."""
    _, prices_map = _build_index([], _BASE_PRICES)
    prices = prices_map.get(_SITE_ID, {})
    data = _build_station_data(_BASE_SITE, prices, _SITE_ID)

    for key in (
        "unleaded",
        "e10",
        "diesel",
        "premium_unleaded",
        "premium_diesel",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
    ):
        assert key in data, f"Key '{key}' missing from _build_station_data result"


def test_build_station_data_county_always_qld() -> None:
    """_build_station_data sets county='QLD'."""
    data = _build_station_data(_BASE_SITE, {}, _SITE_ID)
    assert data["county"] == "QLD"


def test_build_station_data_lastupdated_is_none() -> None:
    """_build_station_data sets lastupdated=None (not surfaced from FPPS)."""
    data = _build_station_data(_BASE_SITE, {}, _SITE_ID)
    assert data["lastupdated"] is None


def test_build_station_data_source_station_id() -> None:
    """_build_station_data sets source_station_id to the passed station_id string."""
    data = _build_station_data(_BASE_SITE, {}, _SITE_ID)
    assert data["source_station_id"] == _SITE_ID


def test_build_station_data_missing_lat_lng_gives_none() -> None:
    """_build_station_data returns None for latitude/longitude when absent."""
    site_no_coords = {k: v for k, v in _BASE_SITE.items() if k not in ("Lat", "Lng")}
    data = _build_station_data(site_no_coords, {}, _SITE_ID)
    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_invalid_lat_gives_none() -> None:
    """_build_station_data handles non-numeric Lat gracefully."""
    site_bad = {**_BASE_SITE, "Lat": "bad"}
    data = _build_station_data(site_bad, {}, _SITE_ID)
    assert data["latitude"] is None


def test_build_station_data_empty_name_becomes_none() -> None:
    """_build_station_data converts empty string name to None."""
    site = {**_BASE_SITE, "N": ""}
    data = _build_station_data(site, {}, _SITE_ID)
    assert data["name"] is None


def test_build_station_data_empty_brand_becomes_none() -> None:
    """_build_station_data converts empty string brand to None."""
    site = {**_BASE_SITE, "B": ""}
    data = _build_station_data(site, {}, _SITE_ID)
    assert data["brand"] is None


def test_build_station_data_postcode_appended_to_address() -> None:
    """_build_station_data appends postcode to address when not already present."""
    data = _build_station_data(_BASE_SITE, {}, _SITE_ID)
    assert "4000" in data["address"]


def test_build_station_data_postcode_not_duplicated() -> None:
    """_build_station_data does not double-append postcode already in address."""
    site_with_postcode = {**_BASE_SITE, "A": "100 Queen Street 4000"}
    data = _build_station_data(site_with_postcode, {}, _SITE_ID)
    assert data["address"].count("4000") == 1


# ---------------------------------------------------------------------------
# _FUELID_MAP (module-level constant)
# ---------------------------------------------------------------------------


def test_fuelid_map_unleaded() -> None:
    """FuelId 2 maps to 'unleaded'."""
    assert _FUELID_MAP[2] == "unleaded"


def test_fuelid_map_diesel() -> None:
    """FuelId 3 maps to 'diesel'."""
    assert _FUELID_MAP[3] == "diesel"


def test_fuelid_map_lpg() -> None:
    """FuelId 4 maps to 'lpg'."""
    assert _FUELID_MAP[4] == "lpg"


def test_fuelid_map_premium_95() -> None:
    """FuelId 5 maps to 'premium_unleaded'."""
    assert _FUELID_MAP[5] == "premium_unleaded"


def test_fuelid_map_premium_98() -> None:
    """FuelId 8 maps to 'premium_unleaded' (same key as 95 RON)."""
    assert _FUELID_MAP[8] == "premium_unleaded"


def test_fuelid_map_e10() -> None:
    """FuelId 12 maps to 'e10'."""
    assert _FUELID_MAP[12] == "e10"


def test_fuelid_map_e85() -> None:
    """FuelId 10 maps to 'e85'."""
    assert _FUELID_MAP[10] == "e85"


def test_fuelid_map_premium_diesel_11() -> None:
    """FuelId 11 maps to 'premium_diesel'."""
    assert _FUELID_MAP[11] == "premium_diesel"


def test_fuelid_map_premium_diesel_14() -> None:
    """FuelId 14 maps to 'premium_diesel' (alternate code)."""
    assert _FUELID_MAP[14] == "premium_diesel"


# ---------------------------------------------------------------------------
# API URL constants
# ---------------------------------------------------------------------------


def test_sites_url_targets_fpps_domain() -> None:
    """_SITES_URL targets the FPPS production domain."""
    assert "fppdirectapi-prod.fuelpricesqld.com.au" in _SITES_URL
    assert _SITES_URL.startswith("https://")


def test_prices_url_targets_fpps_domain() -> None:
    """_PRICES_URL targets the FPPS production domain."""
    assert "fppdirectapi-prod.fuelpricesqld.com.au" in _PRICES_URL
    assert _PRICES_URL.startswith("https://")


def test_sites_url_includes_required_query_params() -> None:
    """_SITES_URL includes countryId and geoRegion query parameters."""
    assert "countryId=21" in _SITES_URL
    assert "geoRegionLevel=3" in _SITES_URL


def test_prices_url_includes_required_query_params() -> None:
    """_PRICES_URL includes countryId and geoRegion query parameters."""
    assert "countryId=21" in _PRICES_URL
    assert "geoRegionLevel=3" in _PRICES_URL


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


async def test_async_fetch_sends_authorization_header() -> None:
    """async_fetch sends the Authorization header with the subscriber token."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="my_secret_token")
    await provider.async_fetch(session, _SITE_ID)

    # Check at least the first call (sites)
    first_call = session.get.call_args_list[0]
    headers = first_call.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert "my_secret_token" in headers["Authorization"]


async def test_async_fetch_authorization_header_format() -> None:
    """Authorization header follows 'FPDAPI SubscriberToken={token}' format."""
    session = _make_session_both()
    provider = AuQldProvider(_SITE_ID, api_key="tok_abc123")
    await provider.async_fetch(session, _SITE_ID)

    first_call = session.get.call_args_list[0]
    headers = first_call.kwargs.get("headers", {})
    assert headers["Authorization"] == "FPDAPI SubscriberToken=tok_abc123"
