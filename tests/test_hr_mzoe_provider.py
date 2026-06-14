"""Tests for HRMzoeProvider (Croatian fuel prices)."""

from __future__ import annotations

import gzip
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fuelcompare_ie.providers.hr_mzoe import (
    HRMzoeProvider,
    _extract_prices,
    _find_station_in_data,
    _parse_station,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_RAW: dict[str, Any] = {
    "postajas": [
        {
            "id": 42,
            "naziv": "INA Sarajevska",
            "adresa": "Sarajevska 1",
            "lat": 16.15,
            "long": 45.79,
            "obveznik_id": 1,
            "zupanija_id": 1,
            "cjenici": [
                {"gorivo_id": 10, "cijena": "1.540"},
                {"gorivo_id": 20, "cijena": "1.720"},
            ],
        },
        {
            "id": 99,
            "naziv": "Petrol Zagreb",
            "adresa": None,
            "lat": None,
            "long": None,
            "obveznik_id": 2,
            "zupanija_id": 1,
            "cjenici": [],
        },
    ],
    "obvezniks": [{"id": 1, "naziv": "INA"}, {"id": 2, "naziv": "Petrol"}],
    "zupanijas": [{"id": 1, "naziv": "Grad Zagreb"}],
    "vrsta_gorivas": [{"id": 5, "tip_goriva_id": 1}, {"id": 6, "tip_goriva_id": 2}],
    "gorivos": [{"id": 10, "vrsta_goriva_id": 5}, {"id": 20, "vrsta_goriva_id": 6}],
}


def _make_provider(station_id: str = "42", county: str | None = None) -> HRMzoeProvider:
    return HRMzoeProvider(station_id, county)


def _make_session(raw: dict | None = None) -> MagicMock:
    """Return a mock aiohttp session that serves the given raw dict as gzip JSON."""
    data = raw if raw is not None else _RAW
    compressed = gzip.compress(json.dumps(data).encode())
    resp = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=compressed)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_key() -> None:
    assert HRMzoeProvider.PROVIDER_KEY == "hr_mzoe"


def test_country() -> None:
    assert HRMzoeProvider.COUNTRY == "HR"


def test_station_lookup_mode() -> None:
    assert HRMzoeProvider.STATION_LOOKUP_MODE == "county_search"


# ---------------------------------------------------------------------------
# async_fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_returns_station_data() -> None:
    provider = _make_provider("42")
    session = _make_session()
    data = await provider.async_fetch(session, "42")
    assert data["name"] == "INA Sarajevska"
    assert data["latitude"] == pytest.approx(45.79)
    assert data["longitude"] == pytest.approx(16.15)


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_for_unknown_station() -> None:
    from custom_components.fuelcompare_ie.providers.base import ProviderError

    provider = _make_provider("999")
    session = _make_session()
    with pytest.raises(ProviderError, match="999"):
        await provider.async_fetch(session, "999")


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_name() -> None:
    provider = _make_provider("42")
    session = _make_session()
    name = await provider.async_fetch_station_name(session, "42")
    assert name == "INA Sarajevska"


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_none_for_unknown() -> None:
    provider = _make_provider("999")
    session = _make_session()
    name = await provider.async_fetch_station_name(session, "999")
    assert name is None


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_none_on_exception() -> None:
    provider = _make_provider("42")
    session = MagicMock()
    session.get = MagicMock(side_effect=Exception("network error"))
    name = await provider.async_fetch_station_name(session, "42")
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_all_when_croatia() -> None:
    provider = _make_provider()
    session = _make_session()
    results = await provider.async_list_stations(session, county="croatia")
    ids = [r[0] for r in results]
    assert "42" in ids
    assert "99" in ids


@pytest.mark.asyncio
async def test_async_list_stations_filters_by_county() -> None:
    provider = _make_provider()
    session = _make_session()
    results = await provider.async_list_stations(session, county="grad_zagreb")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_async_list_stations_county_no_match_returns_empty() -> None:
    provider = _make_provider()
    session = _make_session()
    results = await provider.async_list_stations(session, county="split")
    assert results == []


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_error() -> None:
    provider = _make_provider()
    session = MagicMock()
    session.get = MagicMock(side_effect=Exception("network down"))
    results = await provider.async_list_stations(session, county="croatia")
    assert results == []


@pytest.mark.asyncio
async def test_async_list_stations_skips_stations_with_empty_id() -> None:
    """Station with missing id key should be skipped — covers line 154."""
    raw = dict(_RAW)
    raw["postajas"] = [
        # Station with no 'id' key → str(station.get("id", "")) == "" → skipped
        {
            "naziv": "No ID station",
            "lat": 16.0,
            "long": 45.0,
            "obveznik_id": 1,
            "zupanija_id": 1,
            "cjenici": [],
        },
        {
            "id": 42,
            "naziv": "Good station",
            "lat": 16.0,
            "long": 45.0,
            "obveznik_id": 1,
            "zupanija_id": 1,
            "cjenici": [],
        },
    ]
    provider = _make_provider()
    session = _make_session(raw)
    results = await provider.async_list_stations(session, county="croatia")
    result_ids = [r[0] for r in results]
    assert result_ids == ["42"]


# ---------------------------------------------------------------------------
# _find_station_in_data
# ---------------------------------------------------------------------------


def test_find_station_in_data_found() -> None:
    station = _find_station_in_data(_RAW, "42")
    assert station is not None
    assert station["naziv"] == "INA Sarajevska"


def test_find_station_in_data_not_found() -> None:
    assert _find_station_in_data(_RAW, "999") is None


# ---------------------------------------------------------------------------
# _extract_prices — edge cases
# ---------------------------------------------------------------------------


def test_extract_prices_skips_missing_gorivo_id() -> None:
    """Line 230: gorivo_id is None → continue."""
    vrsta_tip = {5: 1}
    gorivo_vrsta = {10: 5}
    station = {"cjenici": [{"gorivo_id": None, "cijena": "1.5"}]}
    result = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert result == {}


def test_extract_prices_skips_missing_cijena() -> None:
    """Line 230: cijena is None → continue."""
    vrsta_tip = {5: 1}
    gorivo_vrsta = {10: 5}
    station = {"cjenici": [{"gorivo_id": 10, "cijena": None}]}
    result = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert result == {}


def test_extract_prices_skips_unknown_gorivo_vrsta() -> None:
    """Line 233: vrsta_id is None → continue."""
    vrsta_tip = {5: 1}
    gorivo_vrsta = {}  # gorivo_id 10 not in map
    station = {"cjenici": [{"gorivo_id": 10, "cijena": "1.5"}]}
    result = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert result == {}


def test_extract_prices_skips_unknown_tip_key() -> None:
    """Line 237: key is None (tip_id not in _TIP_TO_KEY) → continue."""
    vrsta_tip = {5: 99}  # tip 99 not in _TIP_TO_KEY
    gorivo_vrsta = {10: 5}
    station = {"cjenici": [{"gorivo_id": 10, "cijena": "1.5"}]}
    result = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert result == {}


def test_extract_prices_handles_invalid_cijena() -> None:
    """Lines 242-243: ValueError/TypeError on float(cijena) → pass."""
    vrsta_tip = {5: 1}
    gorivo_vrsta = {10: 5}
    station = {"cjenici": [{"gorivo_id": 10, "cijena": "bad_value"}]}
    result = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert result == {}


# ---------------------------------------------------------------------------
# _parse_station — lat/lng ValueError/TypeError paths
# ---------------------------------------------------------------------------


def test_parse_station_handles_invalid_lat() -> None:
    """Lines 268-269: ValueError on float(raw_lat) → latitude=None."""
    raw = dict(_RAW)
    station = {
        "id": 1,
        "naziv": "Test",
        "adresa": "Addr",
        "lat": "bad",
        "long": "also_bad",
        "obveznik_id": None,
        "zupanija_id": None,
        "cjenici": [],
    }
    result = _parse_station(station, raw)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_station_handles_none_lat() -> None:
    """lat/lng None → latitude/longitude None."""
    station = {
        "id": 1,
        "naziv": "Test",
        "adresa": None,
        "lat": None,
        "long": None,
        "obveznik_id": None,
        "zupanija_id": None,
        "cjenici": [],
    }
    result = _parse_station(station, _RAW)
    assert result["latitude"] is None
    assert result["longitude"] is None


# ---------------------------------------------------------------------------
# Additional line 154 coverage: missing id key and empty-string id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_missing_id_key() -> None:
    """Line 154: station with no 'id' key → sid='' → falsy → continue."""
    raw = {
        **_RAW,
        "postajas": [
            {
                "naziv": "No ID station",
                "lat": 16.0,
                "long": 45.0,
                "obveznik_id": 1,
                "zupanija_id": 1,
                "cjenici": [],
            },
            {
                "id": 42,
                "naziv": "Valid station",
                "lat": 16.0,
                "long": 45.0,
                "obveznik_id": 1,
                "zupanija_id": 1,
                "cjenici": [],
            },
        ],
    }
    provider = _make_provider()
    session = _make_session(raw)
    results = await provider.async_list_stations(session, county="croatia")
    assert len(results) == 1
    assert results[0][0] == "42"


@pytest.mark.asyncio
async def test_async_list_stations_skips_station_with_empty_string_id() -> None:
    """Line 154: station with id='' → sid='' → falsy → continue."""
    raw = {
        **_RAW,
        "postajas": [
            {
                "id": "",
                "naziv": "Empty ID station",
                "lat": 16.0,
                "long": 45.0,
                "obveznik_id": 1,
                "zupanija_id": 1,
                "cjenici": [],
            },
            {
                "id": 42,
                "naziv": "Valid station",
                "lat": 16.0,
                "long": 45.0,
                "obveznik_id": 1,
                "zupanija_id": 1,
                "cjenici": [],
            },
        ],
    }
    provider = _make_provider()
    session = _make_session(raw)
    results = await provider.async_list_stations(session, county="croatia")
    assert len(results) == 1
    assert results[0][0] == "42"
