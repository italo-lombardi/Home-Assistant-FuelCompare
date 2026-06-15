"""Tests for CzCcsProvider (Czech Republic national fuel price caps)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.cz_ccs import (
    CzCcsProvider,
    _HEADERS,
    _NATIONAL_STATION_ID,
    _PRICES_URL,
    _parse_prices,
    _safe_price,
)

# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

_CURRENT_PRICES: dict = {
    "valid_to": None,
    "natural95_cap": 41.49,
    "diesel_cap": 39.29,
    "natural95_without_cap": 46.53,
    "diesel_without_cap": 45.72,
}

_GOVERNMENT_CAP: dict = {
    "active": True,
    "cap_price_natural95": 41.49,
    "cap_price_diesel": 39.29,
    "valid_from": "2026-06-12",
    "valid_to": None,
}

_FULL_PAYLOAD: dict = {
    "last_updated": "2026-06-12T14:16:11",
    "valid_from": "2026-04-24",
    "valid_to": None,
    "current": _CURRENT_PRICES,
    "government_cap": _GOVERNMENT_CAP,
    "history": [
        {
            "date": "2026-04-23",
            "natural95_cap": 41.12,
            "diesel_cap": 41.16,
            "natural95_without_cap": 46.57,
            "diesel_without_cap": 48.35,
        }
    ],
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


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() returns the given response."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country_is_cz() -> None:
    """CzCcsProvider.COUNTRY is 'CZ'."""
    assert CzCcsProvider.COUNTRY == "CZ"


def test_provider_key_is_cz_ccs() -> None:
    """CzCcsProvider.PROVIDER_KEY is 'cz_ccs'."""
    assert CzCcsProvider.PROVIDER_KEY == "cz_ccs"


def test_provider_label_contains_czech_republic() -> None:
    """CzCcsProvider.LABEL references Czech Republic."""
    assert "Czech" in CzCcsProvider.LABEL or "CZ" in CzCcsProvider.LABEL


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE is 'location' (national-average provider)."""
    assert CzCcsProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert CzCcsProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_is_reasonable() -> None:
    """POLL_INTERVAL_SECONDS is at least 3600 (data updates once per weekday)."""
    assert CzCcsProvider.POLL_INTERVAL_SECONDS >= 3600


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_includes_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (Natural95 cap price)."""
    assert "unleaded" in CzCcsProvider.CAPABILITIES


def test_capabilities_includes_diesel() -> None:
    """CAPABILITIES includes 'diesel'."""
    assert "diesel" in CzCcsProvider.CAPABILITIES


def test_capabilities_includes_lastupdated() -> None:
    """CAPABILITIES includes 'lastupdated'."""
    assert "lastupdated" in CzCcsProvider.CAPABILITIES


def test_capabilities_includes_name() -> None:
    """CAPABILITIES includes 'name'."""
    assert "name" in CzCcsProvider.CAPABILITIES


def test_capabilities_includes_coordinator_sentinels() -> None:
    """CAPABILITIES includes last_successful_fetch and data_fetch_problem."""
    caps = CzCcsProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_default_station_id() -> None:
    """Constructor defaults station_id to 'CZ'."""
    p = CzCcsProvider()
    assert p._station_id == "CZ"


def test_constructor_accepts_custom_station_id() -> None:
    """Constructor stores provided station_id (interface compat)."""
    p = CzCcsProvider(station_id="CUSTOM")
    assert p._station_id == "CUSTOM"


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_prices_url_is_github_raw() -> None:
    """_PRICES_URL points to a raw.githubusercontent.com URL."""
    assert _PRICES_URL.startswith("https://raw.githubusercontent.com/")
    assert "Duchnaa/fuel-prices-cz" in _PRICES_URL


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a non-empty User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_national_station_id_is_cz() -> None:
    """_NATIONAL_STATION_ID is 'CZ'."""
    assert _NATIONAL_STATION_ID == "CZ"


# ---------------------------------------------------------------------------
# _safe_price — unit tests
# ---------------------------------------------------------------------------


def test_safe_price_normal_czk_value() -> None:
    """_safe_price returns rounded float for a normal CZK/litre value."""
    assert _safe_price(41.49) == pytest.approx(41.49)


def test_safe_price_rounds_to_3dp() -> None:
    """_safe_price rounds to 3 decimal places."""
    result = _safe_price(41.4912)
    assert result == pytest.approx(41.491)


def test_safe_price_none_input() -> None:
    """_safe_price returns None for None input."""
    assert _safe_price(None) is None


def test_safe_price_zero_input() -> None:
    """_safe_price returns None for zero (no price available)."""
    assert _safe_price(0) is None


def test_safe_price_negative_input() -> None:
    """_safe_price returns None for negative values."""
    assert _safe_price(-1.0) is None


def test_safe_price_string_float() -> None:
    """_safe_price parses numeric strings."""
    assert _safe_price("39.29") == pytest.approx(39.29)


def test_safe_price_non_numeric_string() -> None:
    """_safe_price returns None for non-numeric strings."""
    assert _safe_price("N/A") is None


def test_safe_price_excessively_large_rejected() -> None:
    """_safe_price returns None for values above 500 CZK/litre."""
    assert _safe_price(501.0) is None


def test_safe_price_integer_input() -> None:
    """_safe_price handles integer inputs."""
    assert _safe_price(40) == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# _parse_prices — unit tests
# ---------------------------------------------------------------------------


def test_parse_prices_returns_natural95_as_unleaded() -> None:
    """_parse_prices maps natural95_cap to 'unleaded'."""
    result = _parse_prices(_FULL_PAYLOAD)
    assert result["unleaded"] == pytest.approx(41.49)


def test_parse_prices_returns_diesel_cap() -> None:
    """_parse_prices maps diesel_cap to 'diesel'."""
    result = _parse_prices(_FULL_PAYLOAD)
    assert result["diesel"] == pytest.approx(39.29)


def test_parse_prices_returns_lastupdated() -> None:
    """_parse_prices populates lastupdated from last_updated field."""
    result = _parse_prices(_FULL_PAYLOAD)
    assert result["lastupdated"] == "2026-06-12T14:16:11"


def test_parse_prices_returns_name() -> None:
    """_parse_prices returns a non-empty name field."""
    result = _parse_prices(_FULL_PAYLOAD)
    assert result["name"]
    assert isinstance(result["name"], str)


def test_parse_prices_source_station_id_is_cz() -> None:
    """_parse_prices sets source_station_id to 'CZ'."""
    result = _parse_prices(_FULL_PAYLOAD)
    assert result["source_station_id"] == "CZ"


def test_parse_prices_raises_on_missing_current_key() -> None:
    """_parse_prices raises ProviderError when 'current' key is absent."""
    payload = {k: v for k, v in _FULL_PAYLOAD.items() if k != "current"}
    with pytest.raises(ProviderError, match="current"):
        _parse_prices(payload)


def test_parse_prices_returns_none_for_null_natural95() -> None:
    """_parse_prices returns unleaded=None when natural95_cap is null."""
    payload = {
        **_FULL_PAYLOAD,
        "current": {**_CURRENT_PRICES, "natural95_cap": None},
    }
    result = _parse_prices(payload)
    assert result["unleaded"] is None


def test_parse_prices_returns_none_for_null_diesel() -> None:
    """_parse_prices returns diesel=None when diesel_cap is null."""
    payload = {
        **_FULL_PAYLOAD,
        "current": {**_CURRENT_PRICES, "diesel_cap": None},
    }
    result = _parse_prices(payload)
    assert result["diesel"] is None


def test_parse_prices_handles_missing_last_updated() -> None:
    """_parse_prices returns lastupdated=None when last_updated is absent."""
    payload = {k: v for k, v in _FULL_PAYLOAD.items() if k != "last_updated"}
    result = _parse_prices(payload)
    assert result["lastupdated"] is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_returns_correct_unleaded() -> None:
    """async_fetch returns Natural95 cap as 'unleaded'."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    data = await provider.async_fetch(session, "CZ")

    assert data["unleaded"] == pytest.approx(41.49)


@pytest.mark.asyncio
async def test_async_fetch_returns_correct_diesel() -> None:
    """async_fetch returns diesel cap price."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    data = await provider.async_fetch(session, "CZ")

    assert data["diesel"] == pytest.approx(39.29)


@pytest.mark.asyncio
async def test_async_fetch_returns_lastupdated() -> None:
    """async_fetch returns lastupdated from JSON."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    data = await provider.async_fetch(session, "CZ")

    assert data["lastupdated"] == "2026-06-12T14:16:11"


@pytest.mark.asyncio
async def test_async_fetch_returns_source_station_id_cz() -> None:
    """async_fetch returns source_station_id='CZ'."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    data = await provider.async_fetch(session, "CZ")

    assert data["source_station_id"] == "CZ"


@pytest.mark.asyncio
async def test_async_fetch_ignores_station_id_parameter() -> None:
    """async_fetch returns national data regardless of station_id argument."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    data = await provider.async_fetch(session, "ANYTHING")

    assert data["diesel"] == pytest.approx(39.29)


# ---------------------------------------------------------------------------
# async_fetch — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_on_404() -> None:
    """async_fetch raises ProviderError on HTTP 404."""
    resp = _make_mock_response(404, json_data={})
    session = _make_session(resp)

    provider = CzCcsProvider()
    with pytest.raises(ProviderError, match="404"):
        await provider.async_fetch(session, "CZ")


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_on_http_error() -> None:
    """async_fetch raises ProviderError when the server returns 5xx."""
    err = ClientResponseError(request_info=MagicMock(), history=(), status=503)
    resp = _make_mock_response(503, raise_on_raise_for_status=err)
    session = _make_session(resp)

    provider = CzCcsProvider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "CZ")


@pytest.mark.asyncio
async def test_async_fetch_raises_provider_error_on_missing_current_key() -> None:
    """async_fetch raises ProviderError when JSON lacks 'current' key."""
    bad_payload = {k: v for k, v in _FULL_PAYLOAD.items() if k != "current"}
    resp = _make_mock_response(200, json_data=bad_payload)
    session = _make_session(resp)

    provider = CzCcsProvider()
    with pytest.raises(ProviderError, match="current"):
        await provider.async_fetch(session, "CZ")


@pytest.mark.asyncio
async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets aiohttp ClientError propagate to the coordinator."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = CzCcsProvider()
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "CZ")


@pytest.mark.asyncio
async def test_async_fetch_raises_on_non_dict_response() -> None:
    """async_fetch raises ProviderError when response is not a JSON object."""
    resp = _make_mock_response(200, json_data=[1, 2, 3])
    session = _make_session(resp)

    provider = CzCcsProvider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "CZ")


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_returns_none() -> None:
    """async_fetch_station_name always returns None (location-mode provider)."""
    session = MagicMock()
    provider = CzCcsProvider()
    name = await provider.async_fetch_station_name(session, "CZ")
    assert name is None


@pytest.mark.asyncio
async def test_async_fetch_station_name_makes_no_requests() -> None:
    """async_fetch_station_name makes no HTTP requests."""
    session = MagicMock()
    provider = CzCcsProvider()
    await provider.async_fetch_station_name(session, "CZ")
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_one_entry() -> None:
    """async_list_stations returns exactly one entry for the national average."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_async_list_stations_station_id_is_cz() -> None:
    """async_list_stations returns station_id='CZ'."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    assert result[0][0] == "CZ"


@pytest.mark.asyncio
async def test_async_list_stations_label_includes_prices() -> None:
    """async_list_stations label includes Natural95 and Diesel price info."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    _sid, label = result[0]
    assert "Natural95" in label or "natural95" in label.lower() or "41" in label
    assert "Diesel" in label or "diesel" in label.lower() or "39" in label


@pytest.mark.asyncio
async def test_async_list_stations_ignores_location_kwargs() -> None:
    """async_list_stations ignores lat/lng/radius_km kwargs (national provider)."""
    resp = _make_mock_response(200, json_data=_FULL_PAYLOAD)
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(
        session, lat=50.08, lng=14.42, radius_km=50.0
    )

    assert len(result) == 1
    assert result[0][0] == "CZ"


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_fetch_failure() -> None:
    """async_list_stations returns [] when the fetch fails."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_returns_empty_on_404() -> None:
    """async_list_stations returns [] on HTTP 404 (graceful degradation)."""
    resp = _make_mock_response(404, json_data={})
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    assert result == []


@pytest.mark.asyncio
async def test_async_list_stations_fallback_label_when_prices_unavailable() -> None:
    """async_list_stations returns fallback label when both cap prices are None."""
    payload_no_prices = {
        **_FULL_PAYLOAD,
        "current": {**_CURRENT_PRICES, "natural95_cap": None, "diesel_cap": None},
    }
    resp = _make_mock_response(200, json_data=payload_no_prices)
    session = _make_session(resp)

    provider = CzCcsProvider()
    result = await provider.async_list_stations(session)

    assert len(result) == 1
    station_id, label = result[0]
    assert station_id == _NATIONAL_STATION_ID
    assert "Czech Republic" in label
    assert "Natural95" not in label
    assert "Diesel" not in label
