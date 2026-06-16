"""Tests for IEFuelFinderProvider (fuelfinder.ie Irish fuel prices).

Coverage areas:
  1. Provider metadata (COUNTRY, PROVIDER_KEY, LABEL, CAPABILITIES keys)
  2. async_fetch success — station found in national search, prices correctly
     mapped (diesel→diesel, petrol→unleaded)
  3. async_fetch success — county cache used on second call
  4. async_fetch station not found → raises ProviderError
  5. async_fetch HTTP 403 → _fetch_stations returns None; all fuels None;
     station not found → raises ProviderError (ClientError propagates for
     non-403 errors, but 403 is handled gracefully returning None)
  6. _build_station_data price mapping: diesel, petrol→unleaded, kerosene, cng
  7. _build_station_data None guards
  8. async_list_stations — returns sorted labels with county_search format
  9. async_fetch_station_name — returns name or None
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
    IEFuelFinderProvider,
    _find_station,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_STATION_ID = "7ec0dd4f-4322-4b4f-9de1-c8894a684626"
_STATION_NAME = "Circle K Mulhuddart"
_STATION_COUNTY = "Dublin"

# A minimal station record matching the fuelfinder.ie /stations API shape.
_BASE_RECORD: dict = {
    "id": _STATION_ID,
    "name": _STATION_NAME,
    "brand": "Circle K",
    "county": _STATION_COUNTY,
    "street": "Mulhuddart Road",
    "lat": 53.4050,
    "lng": -6.4090,
    "phone": "+353 1 820 0123",
    "website": "https://www.circlek.ie",
    "osm_id": "node/12345678",
    "slug": "circle-k-mulhuddart",
    "logo_url": "https://t2.gstatic.com/favicon",
    "confidence": "fresh",
    "has_price": True,
    "opening_hours": "Mo-Su 06:00-23:00",
    "updated_at": "2026-06-15T10:00:00Z",
}

_DIESEL_RECORD = {**_BASE_RECORD, "price": 1.839, "updated_at": "2026-06-15T10:00:00Z"}
_PETROL_RECORD = {**_BASE_RECORD, "price": 1.859}
_KEROSENE_RECORD = {**_BASE_RECORD, "price": 1.050}
_CNG_RECORD = {**_BASE_RECORD, "price": 1.100}

_OTHER_STATION: dict = {
    "id": "aaaaaaaa-0000-0000-0000-000000000000",
    "name": "Some Other Station",
    "brand": "Texaco",
    "county": "Cork",
    "street": "Patrick Street",
    "lat": 51.8985,
    "lng": -8.4756,
    "price": 1.870,
    "confidence": "likely",
    "has_price": True,
    "opening_hours": None,
    "updated_at": "2026-06-14T08:00:00Z",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stations_payload(station_list: list[dict]) -> dict:
    return {"stations": station_list}


def _make_json_response(status: int, payload: dict) -> AsyncMock:
    """Build a mock aiohttp response for a JSON endpoint."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session_with_responses(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


# ---------------------------------------------------------------------------
# 1. Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """IEFuelFinderProvider.COUNTRY is 'IE'."""
    assert IEFuelFinderProvider.COUNTRY == "IE"


def test_provider_metadata_provider_key() -> None:
    """IEFuelFinderProvider.PROVIDER_KEY is 'ie_fuelfinder'."""
    assert IEFuelFinderProvider.PROVIDER_KEY == "ie_fuelfinder"


def test_provider_metadata_label() -> None:
    """IEFuelFinderProvider.LABEL is 'FuelFinder.ie'."""
    assert IEFuelFinderProvider.LABEL == "FuelFinder.ie"


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes diesel, unleaded, kerosene, cng."""
    caps = IEFuelFinderProvider.CAPABILITIES
    for key in ("diesel", "unleaded", "kerosene", "cng"):
        assert key in caps, f"Key '{key}' missing from CAPABILITIES"


def test_provider_capabilities_include_identity_fields() -> None:
    """CAPABILITIES includes station identity and contact fields."""
    caps = IEFuelFinderProvider.CAPABILITIES
    for key in (
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "phone",
        "website",
    ):
        assert key in caps, f"Key '{key}' missing from CAPABILITIES"


def test_provider_capabilities_include_timing_fields() -> None:
    """CAPABILITIES includes lastupdated and opening_hours."""
    caps = IEFuelFinderProvider.CAPABILITIES
    assert "lastupdated" in caps
    assert "opening_hours" in caps


def test_provider_capabilities_include_fuelfinder_specific() -> None:
    """CAPABILITIES includes price_confidence and has_price."""
    caps = IEFuelFinderProvider.CAPABILITIES
    assert "price_confidence" in caps
    assert "has_price" in caps


def test_provider_config_mode() -> None:
    """CONFIG_MODE is 'station_id'."""
    assert IEFuelFinderProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'county_search'."""
    assert IEFuelFinderProvider.STATION_LOOKUP_MODE == "county_search"


# ---------------------------------------------------------------------------
# 2. async_fetch success — station found in national search
# ---------------------------------------------------------------------------


async def test_async_fetch_success_national_search() -> None:
    """async_fetch returns data when station is found in national search."""
    provider = IEFuelFinderProvider(_STATION_ID)

    # _fetch_stations is called 4 times (diesel, petrol, kerosene, cng)
    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [_DIESEL_RECORD, _OTHER_STATION]
        if fuel == "petrol":
            return [_PETROL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.839, abs=1e-4)
    assert data["unleaded"] == pytest.approx(1.859, abs=1e-4)
    assert data["name"] == _STATION_NAME
    assert data["brand"] == "Circle K"
    assert data["county"] == _STATION_COUNTY
    assert data["latitude"] == pytest.approx(53.4050)
    assert data["longitude"] == pytest.approx(-6.4090)
    assert data["lastupdated"] == "2026-06-15T10:00:00Z"
    assert data["price_confidence"] == "fresh"
    assert data["has_price"] is True


async def test_async_fetch_caches_county_after_first_fetch() -> None:
    """async_fetch caches the county from the API response for future polls."""
    provider = IEFuelFinderProvider(_STATION_ID)
    assert provider._cached_county is None

    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [_DIESEL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        await provider.async_fetch(session, _STATION_ID)

    # County is lowercased from the title-cased API value
    assert provider._cached_county == _STATION_COUNTY.lower()


# ---------------------------------------------------------------------------
# 3. async_fetch success — county cache used on second call
# ---------------------------------------------------------------------------


async def test_async_fetch_uses_cached_county_on_second_call() -> None:
    """When _cached_county is set, async_fetch uses it instead of 'ireland'."""
    provider = IEFuelFinderProvider(_STATION_ID, county="dublin")
    assert provider._cached_county == "dublin"

    call_cities: list[str] = []

    async def _mock_fetch(session, city, fuel):
        call_cities.append(city)
        if fuel == "diesel":
            return [_DIESEL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        await provider.async_fetch(session, _STATION_ID)

    # All four fuel-type calls should use the cached county, not 'ireland'
    assert all(c == "dublin" for c in call_cities)


# ---------------------------------------------------------------------------
# 4. async_fetch station not found → raises ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_not_found() -> None:
    """async_fetch raises ProviderError when station UUID is absent from all responses."""
    provider = IEFuelFinderProvider("nonexistent-uuid")

    async def _mock_fetch(session, city, fuel):
        return [_OTHER_STATION]  # does not contain the target UUID

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        with pytest.raises(ProviderError, match="nonexistent-uuid"):
            await provider.async_fetch(session, "nonexistent-uuid")


async def test_async_fetch_raises_provider_error_when_all_responses_empty() -> None:
    """async_fetch raises ProviderError when all fuel-type responses are empty."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        with pytest.raises(ProviderError):
            await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# 5. async_fetch HTTP 403 → _fetch_stations returns None → station not found
# ---------------------------------------------------------------------------


async def test_async_fetch_403_returns_none_per_fuel_type() -> None:
    """_fetch_stations returns None on HTTP 403; async_fetch raises ProviderError."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        # Simulates HTTP 403 — the real _fetch_stations returns None in this case
        return None

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        with pytest.raises(ProviderError):
            await provider.async_fetch(session, _STATION_ID)


async def test_fetch_stations_returns_none_on_403_response() -> None:
    """_fetch_stations returns None when the server returns HTTP 403."""
    provider = IEFuelFinderProvider(_STATION_ID)

    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.json = AsyncMock(return_value={})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)

    result = await provider._fetch_stations(session, city="ireland", fuel="diesel")
    assert result is None


# ---------------------------------------------------------------------------
# 6. _build_station_data price mapping
# ---------------------------------------------------------------------------


def test_build_station_data_diesel_price() -> None:
    """_build_station_data maps diesel fuel → 'diesel' key."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel = {"diesel": _DIESEL_RECORD}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["diesel"] == pytest.approx(1.839, abs=1e-4)


def test_build_station_data_petrol_maps_to_unleaded() -> None:
    """_build_station_data maps petrol fuel → 'unleaded' key for compat."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel = {"petrol": _PETROL_RECORD}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["unleaded"] == pytest.approx(1.859, abs=1e-4)


def test_build_station_data_kerosene_price() -> None:
    """_build_station_data maps kerosene fuel → 'kerosene' key."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel = {"kerosene": _KEROSENE_RECORD}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["kerosene"] == pytest.approx(1.050, abs=1e-4)


def test_build_station_data_cng_price() -> None:
    """_build_station_data maps cng fuel → 'cng' key."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel = {"cng": _CNG_RECORD}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["cng"] == pytest.approx(1.100, abs=1e-4)


# ---------------------------------------------------------------------------
# 7. _build_station_data None guards
# ---------------------------------------------------------------------------


def test_build_station_data_missing_fuel_returns_none() -> None:
    """_build_station_data returns None for fuel types absent from prices_by_fuel."""
    provider = IEFuelFinderProvider(_STATION_ID)
    # Only diesel supplied — petrol/kerosene/cng should be None
    prices_by_fuel = {"diesel": _DIESEL_RECORD}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["unleaded"] is None
    assert data["kerosene"] is None
    assert data["cng"] is None


def test_build_station_data_zero_price_returns_none() -> None:
    """_build_station_data returns None when price is zero."""
    provider = IEFuelFinderProvider(_STATION_ID)
    zero_record = {**_DIESEL_RECORD, "price": 0.0}
    prices_by_fuel = {"diesel": zero_record}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["diesel"] is None


def test_build_station_data_none_price_returns_none() -> None:
    """_build_station_data returns None when price key is explicitly None."""
    provider = IEFuelFinderProvider(_STATION_ID)
    none_record = {**_DIESEL_RECORD, "price": None}
    prices_by_fuel = {"diesel": none_record}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["diesel"] is None


def test_build_station_data_missing_lat_lng_returns_none() -> None:
    """_build_station_data returns None for lat/lng when absent from meta."""
    provider = IEFuelFinderProvider(_STATION_ID)
    no_coords = {k: v for k, v in _BASE_RECORD.items() if k not in ("lat", "lng")}
    prices_by_fuel: dict = {}
    data = provider._build_station_data(_STATION_ID, no_coords, prices_by_fuel)
    assert data["latitude"] is None
    assert data["longitude"] is None


def test_build_station_data_source_station_id() -> None:
    """_build_station_data sets source_station_id to the passed station_id."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel: dict = {}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["source_station_id"] == _STATION_ID


def test_build_station_data_opening_hours_mapped() -> None:
    """_build_station_data maps 'opening_hours' key from meta record."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel: dict = {}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["opening_hours"] == "Mo-Su 06:00-23:00"


def test_build_station_data_identity_fields() -> None:
    """_build_station_data populates name, brand, address, county."""
    provider = IEFuelFinderProvider(_STATION_ID)
    prices_by_fuel: dict = {}
    data = provider._build_station_data(_STATION_ID, _BASE_RECORD, prices_by_fuel)
    assert data["name"] == _STATION_NAME
    assert data["brand"] == "Circle K"
    assert data["address"] == "Mulhuddart Road"
    assert data["county"] == _STATION_COUNTY


# ---------------------------------------------------------------------------
# 8. async_list_stations — county_search
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_sorted_results() -> None:
    """async_list_stations returns a sorted list of (id, label) tuples (sorted by UUID)."""
    provider = IEFuelFinderProvider(_STATION_ID)

    # Stations are sorted alphabetically by UUID string
    # "7ec0dd4f..." < "bbbb" alphabetically, so _STATION_ID comes first
    other = {**_OTHER_STATION, "id": "bbbb", "county": "Dublin", "price": 1.800}
    main_station = {**_DIESEL_RECORD}  # _STATION_ID

    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [main_station, other]
        if fuel == "petrol":
            return []
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        result = await provider.async_list_stations(session, county="dublin")

    assert len(result) >= 2
    station_ids = [sid for sid, _ in result]
    # Sorted alphabetically by UUID: "7ec0..." < "bbbb"
    assert station_ids.index(_STATION_ID) < station_ids.index("bbbb")


async def test_async_list_stations_label_includes_station_name() -> None:
    """async_list_stations label includes the station name and short UUID."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [_DIESEL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        result = await provider.async_list_stations(session, county="dublin")

    assert result
    _, label = result[0]
    # Label format: "{name}, {street} (#{uid[:8]})" or "{name} (#{uid[:8]})"
    assert "(#" in label
    assert _STATION_ID[:8] in label


async def test_async_list_stations_returns_empty_on_failure() -> None:
    """async_list_stations returns [] on any exception."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        raise RuntimeError("network error")

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        result = await provider.async_list_stations(session, county="dublin")

    assert result == []


async def test_async_list_stations_returns_empty_when_no_stations() -> None:
    """async_list_stations returns [] when both diesel and petrol lists are empty."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        result = await provider.async_list_stations(session, county="dublin")

    assert result == []


# ---------------------------------------------------------------------------
# 9. async_fetch_station_name — returns name or None
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns the station name from diesel response."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [_DIESEL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == _STATION_NAME


async def test_async_fetch_station_name_falls_back_to_petrol() -> None:
    """async_fetch_station_name falls back to petrol list when not found in diesel."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        if fuel == "diesel":
            return [_OTHER_STATION]  # station not present
        if fuel == "petrol":
            return [_PETROL_RECORD]
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == _STATION_NAME


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station is absent from all results."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        return [_OTHER_STATION]  # different station

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_exception() -> None:
    """async_fetch_station_name returns None on network failure."""
    provider = IEFuelFinderProvider(_STATION_ID)

    async def _mock_fetch(session, city, fuel):
        raise RuntimeError("connection refused")

    with patch.object(provider, "_fetch_stations", side_effect=_mock_fetch):
        session = MagicMock()
        name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# Helper function _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station dict with the matching 'id' field."""
    stations = [_BASE_RECORD, _OTHER_STATION]
    result = _find_station(stations, _STATION_ID)
    assert result is not None
    assert result["id"] == _STATION_ID
    assert result["name"] == _STATION_NAME


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station has the given id."""
    result = _find_station([_OTHER_STATION], _STATION_ID)
    assert result is None


def test_find_station_returns_none_for_empty_list() -> None:
    """_find_station returns None for an empty list."""
    assert _find_station([], _STATION_ID) is None


# ---------------------------------------------------------------------------
# Provider registry integration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """IEFuelFinderProvider is registered under 'ie_fuelfinder' in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "ie_fuelfinder" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["ie_fuelfinder"] is IEFuelFinderProvider


# ---------------------------------------------------------------------------
# get_station_page_url
# ---------------------------------------------------------------------------


def test_get_station_page_url_returns_url_when_slug_cached() -> None:
    """Returns fuelfinder.ie URL when slug is in cache."""
    provider = IEFuelFinderProvider("some-uuid")
    provider._slug_cache["some-uuid"] = "circle-k-taney"
    assert (
        provider.get_station_page_url("some-uuid")
        == "https://www.fuelfinder.ie/fuelfinder/station/circle-k-taney"
    )


def test_get_station_page_url_returns_homepage_when_slug_missing() -> None:
    """Returns homepage URL when station_id not in slug cache."""
    provider = IEFuelFinderProvider("some-uuid")
    assert provider.get_station_page_url("unknown-uuid") == "https://www.fuelfinder.ie"
