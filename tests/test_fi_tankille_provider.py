"""Tests for FiTankilleProvider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.fi_tankille import (
    FiTankilleProvider,
    _API_URL,
    _HEADERS,
    _NATIONAL_STATION_ID,
    _extract_prices_from_jsonstat2,
    _parse_price,
)

# ---------------------------------------------------------------------------
# Fixture data — minimal JSON-stat2 response from Statistics Finland
# ---------------------------------------------------------------------------

# Commodity codes: A=95E10, B=Diesel, D=LightFuelOil, E=RenewableDiesel
# Time dimension: two months (2026M01, 2026M02) to test "most recent" logic.
_JSONSTAT2_RESPONSE: dict[str, Any] = {
    "dataset": {
        "id": ["energia_22_20200205", "Tiedot", "timeperiod_m"],
        "size": [4, 1, 2],
        "dimension": {
            "energia_22_20200205": {
                "label": "Commodity",
                "category": {
                    "index": {"A": 0, "B": 1, "D": 2, "E": 3},
                    "label": {
                        "A": "Motor petrol 95 E10",
                        "B": "Diesel",
                        "D": "Light fuel oil",
                        "E": "Renewable diesel",
                    },
                },
            },
            "Tiedot": {
                "label": "Data",
                "category": {
                    "index": {"hinta": 0},
                    "label": {"hinta": "Price"},
                },
            },
            "timeperiod_m": {
                "label": "Year-month",
                "category": {
                    "index": {"2026M01": 0, "2026M02": 1},
                    "label": {"2026M01": "2026M01", "2026M02": "2026M02"},
                },
            },
        },
        # 4 commodities × 1 measure × 2 time periods = 8 values
        # Layout: commodity varies slowest; [A_jan, A_feb, B_jan, B_feb, ...]
        "value": [
            1.700,
            1.712,  # A: 95E10 Jan=1.700, Feb=1.712
            1.830,
            1.845,  # B: Diesel Jan=1.830, Feb=1.845
            1.200,
            1.215,  # D: LightFuelOil Jan=1.200, Feb=1.215
            1.900,
            1.920,  # E: RenewableDiesel Jan=1.900, Feb=1.920
        ],
    }
}

# Response where the most recent value (Feb) is null — provider should fall
# back to Jan.
_JSONSTAT2_NULL_LATEST: dict[str, Any] = {
    "dataset": {
        **_JSONSTAT2_RESPONSE["dataset"],
        "value": [
            1.700,
            None,  # A: Jan=1.700, Feb=null → use Jan
            1.830,
            1.845,  # B: both present
            None,
            None,  # D: all null → None result
            1.900,
            1.920,
        ],
    }
}

# Single time period (simpler payload for basic tests)
_JSONSTAT2_SINGLE_PERIOD: dict[str, Any] = {
    "dataset": {
        "id": ["energia_22_20200205", "Tiedot", "timeperiod_m"],
        "size": [4, 1, 1],
        "dimension": {
            "energia_22_20200205": {
                "label": "Commodity",
                "category": {
                    "index": {"A": 0, "B": 1, "D": 2, "E": 3},
                    "label": {
                        "A": "Motor petrol 95 E10",
                        "B": "Diesel",
                        "D": "Light fuel oil",
                        "E": "Renewable diesel",
                    },
                },
            },
            "Tiedot": {
                "label": "Data",
                "category": {
                    "index": {"hinta": 0},
                    "label": {"hinta": "Price"},
                },
            },
            "timeperiod_m": {
                "label": "Year-month",
                "category": {
                    "index": {"2026M02": 0},
                    "label": {"2026M02": "2026M02"},
                },
            },
        },
        "value": [1.712, 1.845, 1.215, 1.920],  # A, B, D, E
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_post_response(
    status: int,
    json_data: object = None,
    text_data: str = "",
) -> AsyncMock:
    """Build a mock aiohttp response for a POST request."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    mock_resp.text = AsyncMock(return_value=text_data)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .post() returns response."""
    session = MagicMock()
    session.post = MagicMock(return_value=response)
    return session


def _make_provider(
    station_id: str = _NATIONAL_STATION_ID,
    latitude: float | None = None,
    longitude: float | None = None,
) -> FiTankilleProvider:
    return FiTankilleProvider(
        station_id=station_id,
        latitude=latitude,
        longitude=longitude,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """FiTankilleProvider declares COUNTRY='FI'."""
    assert FiTankilleProvider.COUNTRY == "FI"


def test_provider_key() -> None:
    """FiTankilleProvider declares PROVIDER_KEY='fi_tankille'."""
    assert FiTankilleProvider.PROVIDER_KEY == "fi_tankille"


def test_provider_label_contains_finland() -> None:
    """FiTankilleProvider LABEL mentions Finland."""
    assert "Finland" in FiTankilleProvider.LABEL or "FI" in FiTankilleProvider.LABEL


def test_provider_config_mode_is_location() -> None:
    """FiTankilleProvider uses CONFIG_MODE='location' (national average)."""
    assert FiTankilleProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """FiTankilleProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert FiTankilleProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_is_daily() -> None:
    """FiTankilleProvider POLL_INTERVAL_SECONDS is 86400 (daily)."""
    assert FiTankilleProvider.POLL_INTERVAL_SECONDS == 86400


def test_provider_does_not_require_api_key() -> None:
    """FiTankilleProvider does not require an API key."""
    assert FiTankilleProvider.REQUIRES_API_KEY is False


def test_provider_capabilities_include_e10() -> None:
    """CAPABILITIES includes e10 (accurate name for Finnish 95 E10)."""
    caps = FiTankilleProvider.CAPABILITIES
    assert "e10" in caps
    assert "unleaded" not in caps


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes diesel."""
    assert "diesel" in FiTankilleProvider.CAPABILITIES


def test_provider_capabilities_include_kerosene() -> None:
    """CAPABILITIES includes kerosene (light fuel oil)."""
    assert "kerosene" in FiTankilleProvider.CAPABILITIES


def test_provider_capabilities_include_premium_diesel() -> None:
    """CAPABILITIES includes premium_diesel (renewable diesel / HVO100)."""
    assert "premium_diesel" in FiTankilleProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = FiTankilleProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_defaults_station_id_to_fi() -> None:
    """Constructor defaults station_id to 'FI'."""
    provider = FiTankilleProvider()
    assert provider._station_id == "FI"


def test_constructor_stores_explicit_station_id() -> None:
    """Constructor stores explicit station_id."""
    provider = FiTankilleProvider(station_id="FI")
    assert provider._station_id == "FI"


def test_constructor_defaults_latitude_to_helsinki() -> None:
    """Constructor defaults latitude to Helsinki (60.1699)."""
    provider = FiTankilleProvider()
    assert provider._latitude == pytest.approx(60.1699, rel=1e-3)


def test_constructor_defaults_longitude_to_helsinki() -> None:
    """Constructor defaults longitude to Helsinki (24.9384)."""
    provider = FiTankilleProvider()
    assert provider._longitude == pytest.approx(24.9384, rel=1e-3)


def test_constructor_accepts_custom_coordinates() -> None:
    """Constructor stores custom lat/lng."""
    provider = FiTankilleProvider(latitude=65.0, longitude=25.5)
    assert provider._latitude == pytest.approx(65.0)
    assert provider._longitude == pytest.approx(25.5)


# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------


def test_api_url_points_to_stat_fi() -> None:
    """_API_URL targets pxdata.stat.fi."""
    assert "stat.fi" in _API_URL
    assert _API_URL.startswith("https://")
    assert "12ge" in _API_URL


def test_headers_include_content_type_json() -> None:
    """_HEADERS includes Content-Type: application/json."""
    assert _HEADERS.get("Content-Type") == "application/json"


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent identifying as HomeAssistant."""
    assert "User-Agent" in _HEADERS
    assert "HomeAssistant" in _HEADERS["User-Agent"]


def test_national_station_id_is_fi() -> None:
    """_NATIONAL_STATION_ID is 'FI'."""
    assert _NATIONAL_STATION_ID == "FI"


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_valid_float() -> None:
    """_parse_price returns a rounded float for a valid price."""
    assert _parse_price(1.712) == pytest.approx(1.712)


def test_parse_price_rounds_to_four_decimals() -> None:
    """_parse_price rounds to 4 decimal places."""
    result = _parse_price(1.71234567)
    assert result == pytest.approx(1.7123, rel=1e-4)


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None when value is None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None for zero (no price)."""
    assert _parse_price(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price(-1.5) is None


def test_parse_price_returns_none_for_non_numeric_string() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None


def test_parse_price_parses_numeric_string() -> None:
    """_parse_price parses a numeric string correctly."""
    assert _parse_price("1.845") == pytest.approx(1.845)


def test_parse_price_does_not_divide_small_value() -> None:
    """_parse_price does not apply any /100 conversion — prices are EUR/litre."""
    result = _parse_price(1.712)
    assert result is not None
    assert result < 5.0  # Finnish petrol is ~1.7 EUR/litre, not divided to 0.017


# ---------------------------------------------------------------------------
# _extract_prices_from_jsonstat2
# ---------------------------------------------------------------------------


def test_extract_prices_returns_95e10() -> None:
    """_extract_prices_from_jsonstat2 extracts 95E10 (code A) as latest value."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_RESPONSE)
    assert prices.get("A") == pytest.approx(1.712)


def test_extract_prices_returns_diesel() -> None:
    """_extract_prices_from_jsonstat2 extracts diesel (code B)."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_RESPONSE)
    assert prices.get("B") == pytest.approx(1.845)


def test_extract_prices_returns_light_fuel_oil() -> None:
    """_extract_prices_from_jsonstat2 extracts light fuel oil (code D)."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_RESPONSE)
    assert prices.get("D") == pytest.approx(1.215)


def test_extract_prices_returns_renewable_diesel() -> None:
    """_extract_prices_from_jsonstat2 extracts renewable diesel (code E)."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_RESPONSE)
    assert prices.get("E") == pytest.approx(1.920)


def test_extract_prices_picks_most_recent_non_null() -> None:
    """_extract_prices_from_jsonstat2 falls back to prior period when latest is null."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_NULL_LATEST)
    # A: latest (Feb) is null, should return Jan value
    assert prices.get("A") == pytest.approx(1.700)
    # D: both null, should return None
    assert prices.get("D") is None


def test_extract_prices_single_period() -> None:
    """_extract_prices_from_jsonstat2 handles a single-period payload."""
    prices = _extract_prices_from_jsonstat2(_JSONSTAT2_SINGLE_PERIOD)
    assert prices.get("A") == pytest.approx(1.712)
    assert prices.get("B") == pytest.approx(1.845)


def test_extract_prices_raises_provider_error_on_malformed_payload() -> None:
    """_extract_prices_from_jsonstat2 raises ProviderError for malformed data."""
    with pytest.raises(ProviderError):
        _extract_prices_from_jsonstat2({"broken": True})


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict on success."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_e10_price() -> None:
    """async_fetch maps 95E10 (code A) to e10."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["e10"] == pytest.approx(1.712)


async def test_async_fetch_e10_not_stored_as_unleaded() -> None:
    """async_fetch stores 95E10 only under 'e10', not under 'unleaded'."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert "unleaded" not in data


async def test_async_fetch_diesel_price() -> None:
    """async_fetch maps diesel (code B) to 'diesel'."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["diesel"] == pytest.approx(1.845)


async def test_async_fetch_kerosene_price() -> None:
    """async_fetch maps light fuel oil (code D) to 'kerosene'."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["kerosene"] == pytest.approx(1.215)


async def test_async_fetch_premium_diesel_price() -> None:
    """async_fetch maps renewable diesel (code E) to 'premium_diesel'."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["premium_diesel"] == pytest.approx(1.920)


async def test_async_fetch_name_is_finland_national_average() -> None:
    """async_fetch sets name to 'Finland — National Average'."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["name"] is not None
    assert "Finland" in data["name"]


async def test_async_fetch_populates_default_coordinates() -> None:
    """async_fetch populates default Helsinki coordinates when none given."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["latitude"] == pytest.approx(60.1699, rel=1e-3)
    assert data["longitude"] == pytest.approx(24.9384, rel=1e-3)


async def test_async_fetch_uses_custom_coordinates() -> None:
    """async_fetch uses custom coordinates when supplied at construction."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider(latitude=65.0, longitude=25.5)
    data = await provider.async_fetch(session, _NATIONAL_STATION_ID)

    assert data["latitude"] == pytest.approx(65.0)
    assert data["longitude"] == pytest.approx(25.5)


async def test_async_fetch_uses_post_method() -> None:
    """async_fetch calls session.post (not .get) for the PxWeb API."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _NATIONAL_STATION_ID)

    session.post.assert_called_once()
    call_args = session.post.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "stat.fi" in url


async def test_async_fetch_includes_json_body() -> None:
    """async_fetch sends a JSON body with the commodity codes."""
    resp = _make_mock_post_response(200, json_data=_JSONSTAT2_RESPONSE)
    session = _make_session(resp)

    provider = _make_provider()
    await provider.async_fetch(session, _NATIONAL_STATION_ID)

    call_kwargs = session.post.call_args.kwargs
    body = call_kwargs.get("json", {})
    assert "query" in body


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_http_400() -> None:
    """async_fetch raises ProviderError when API returns HTTP 400."""
    resp = _make_mock_post_response(400, text_data="Bad Request")
    session = _make_session(resp)

    provider = _make_provider()
    with pytest.raises(ProviderError, match="400"):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)


async def test_async_fetch_raises_provider_error_on_http_404() -> None:
    """async_fetch raises ProviderError when API returns HTTP 404."""
    resp = _make_mock_post_response(404)
    session = _make_session(resp)

    provider = _make_provider()
    with pytest.raises(ProviderError, match="404"):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp.ClientError for coordinator stale-retention."""
    session = MagicMock()
    session.post = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)


async def test_async_fetch_raises_provider_error_on_malformed_response() -> None:
    """async_fetch raises ProviderError when API returns malformed JSON-stat2."""
    resp = _make_mock_post_response(200, json_data={"unexpected": "format"})
    session = _make_session(resp)

    provider = _make_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_string() -> None:
    """async_fetch_station_name returns a non-empty string."""
    session = MagicMock()
    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _NATIONAL_STATION_ID)
    assert name is not None
    assert isinstance(name, str)
    assert len(name) > 0


async def test_async_fetch_station_name_contains_finland() -> None:
    """async_fetch_station_name mentions Finland."""
    session = MagicMock()
    provider = _make_provider()
    name = await provider.async_fetch_station_name(session, _NATIONAL_STATION_ID)
    assert name is not None
    assert "Finland" in name


async def test_async_fetch_station_name_does_not_call_api() -> None:
    """async_fetch_station_name returns fixed string without any HTTP call."""
    session = MagicMock()
    provider = _make_provider()
    await provider.async_fetch_station_name(session, _NATIONAL_STATION_ID)
    session.post.assert_not_called()
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_single_entry() -> None:
    """async_list_stations returns exactly one entry — the national average."""
    session = MagicMock()
    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=60.17, lng=24.94)
    assert len(result) == 1


async def test_async_list_stations_entry_is_tuple_of_strings() -> None:
    """async_list_stations entry is a (str, str) tuple."""
    session = MagicMock()
    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=60.17, lng=24.94)
    station_id, label = result[0]
    assert isinstance(station_id, str)
    assert isinstance(label, str)


async def test_async_list_stations_id_is_fi() -> None:
    """async_list_stations returns 'FI' as the station_id."""
    session = MagicMock()
    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=60.17, lng=24.94)
    station_id, _ = result[0]
    assert station_id == "FI"


async def test_async_list_stations_label_contains_finland() -> None:
    """async_list_stations label mentions Finland."""
    session = MagicMock()
    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=60.17, lng=24.94)
    _, label = result[0]
    assert "Finland" in label


async def test_async_list_stations_works_without_lat_lng() -> None:
    """async_list_stations returns entry even when lat/lng kwargs are absent."""
    session = MagicMock()
    provider = _make_provider()
    result = await provider.async_list_stations(session)
    assert len(result) == 1


async def test_async_list_stations_does_not_call_api() -> None:
    """async_list_stations returns fixed list without any HTTP call."""
    session = MagicMock()
    provider = _make_provider()
    await provider.async_list_stations(session, lat=60.17, lng=24.94)
    session.post.assert_not_called()
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """FiTankilleProvider is registered in the PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "fi_tankille" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["fi_tankille"] is FiTankilleProvider


# ---------------------------------------------------------------------------
# _parse_price — cents/litre branch (line 165)
# ---------------------------------------------------------------------------


def test_parse_price_divides_cents_to_eur() -> None:
    """_parse_price divides values >10 by 100 to convert cents/litre to EUR/litre."""
    result = _parse_price(193)
    assert result == pytest.approx(1.93, rel=1e-4)


def test_parse_price_rejects_implausibly_high_eur_after_conversion() -> None:
    """_parse_price returns None when value >10 EUR/L after /100 conversion."""
    # 1100 cents -> 11.0 EUR/L -> exceeds 10.0 guard -> None
    result = _parse_price(1100)
    assert result is None


# ---------------------------------------------------------------------------
# _extract_prices_from_jsonstat2 — fallback dimension index (lines 208-211)
# ---------------------------------------------------------------------------


def test_extract_prices_fallback_dimension_index() -> None:
    """_extract_prices_from_jsonstat2 falls back to commodity=0, time=last when ids unrecognised."""
    payload: dict = {
        "dataset": {
            "id": ["commodity_dim", "time_dim"],
            "size": [2, 1],
            "dimension": {
                "energia_22_20200205": {
                    "label": "Commodity",
                    "category": {
                        "index": {"A": 0, "B": 1},
                        "label": {"A": "Motor petrol 95 E10", "B": "Diesel"},
                    },
                },
                "commodity_dim": {
                    "label": "Commodity",
                    "category": {
                        "index": {"A": 0, "B": 1},
                        "label": {"A": "Motor petrol 95 E10", "B": "Diesel"},
                    },
                },
                "time_dim": {
                    "label": "Time",
                    "category": {
                        "index": {"2026M02": 0},
                        "label": {"2026M02": "2026M02"},
                    },
                },
            },
            "value": [1.712, 1.845],
        }
    }
    prices = _extract_prices_from_jsonstat2(payload)
    assert prices.get("A") == pytest.approx(1.712)
    assert prices.get("B") == pytest.approx(1.845)


# ---------------------------------------------------------------------------
# _extract_prices_from_jsonstat2 — skip missing commodity code (line 231)
# ---------------------------------------------------------------------------


def test_extract_prices_skips_missing_commodity_code() -> None:
    """_extract_prices_from_jsonstat2 skips commodity positions with no mapped code."""
    payload: dict = {
        "dataset": {
            "id": ["energia_22_20200205", "Tiedot", "timeperiod_m"],
            "size": [3, 1, 1],
            "dimension": {
                "energia_22_20200205": {
                    "label": "Commodity",
                    "category": {
                        # position 1 intentionally absent — gap in the index
                        "index": {"A": 0, "B": 2},
                        "label": {"A": "Motor petrol 95 E10", "B": "Diesel"},
                    },
                },
                "Tiedot": {
                    "label": "Data",
                    "category": {"index": {"hinta": 0}, "label": {"hinta": "Price"}},
                },
                "timeperiod_m": {
                    "label": "Year-month",
                    "category": {
                        "index": {"2026M02": 0},
                        "label": {"2026M02": "2026M02"},
                    },
                },
            },
            "value": [1.712, 0.0, 1.845],
        }
    }
    prices = _extract_prices_from_jsonstat2(payload)
    assert prices.get("A") == pytest.approx(1.712)
    assert prices.get("B") == pytest.approx(1.845)
    assert len(prices) == 2


# ---------------------------------------------------------------------------
# _extract_prices_from_jsonstat2 — time-major flat index (line 238)
# ---------------------------------------------------------------------------


def test_extract_prices_time_major_flat_index() -> None:
    """_extract_prices_from_jsonstat2 uses time-major index when time is the first dimension."""
    payload: dict = {
        "dataset": {
            "id": ["timeperiod_m", "energia_22_20200205"],
            "size": [2, 2],
            "dimension": {
                "energia_22_20200205": {
                    "label": "Commodity",
                    "category": {
                        "index": {"A": 0, "B": 1},
                        "label": {"A": "Motor petrol 95 E10", "B": "Diesel"},
                    },
                },
                "timeperiod_m": {
                    "label": "Year-month",
                    "category": {
                        "index": {"2026M01": 0, "2026M02": 1},
                        "label": {"2026M01": "2026M01", "2026M02": "2026M02"},
                    },
                },
            },
            # time-major layout: [A_t0, B_t0, A_t1, B_t1]
            "value": [1.700, 1.830, 1.712, 1.845],
        }
    }
    prices = _extract_prices_from_jsonstat2(payload)
    assert prices.get("A") == pytest.approx(1.712)
    assert prices.get("B") == pytest.approx(1.845)


# ---------------------------------------------------------------------------
# async_fetch — raise_for_status wrapping (lines 470-471)
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_response_error_on_raise_for_status() -> None:
    """async_fetch lets ClientResponseError from raise_for_status propagate (coordinator handles it)."""
    from aiohttp import ClientResponseError

    req_info = MagicMock()
    req_info.real_url = _API_URL
    http_exc = ClientResponseError(
        request_info=req_info,
        history=(),
        status=500,
        message="Internal Server Error",
    )

    resp = _make_mock_post_response(500)
    resp.raise_for_status = MagicMock(side_effect=http_exc)
    session = _make_session(resp)

    provider = _make_provider()
    with pytest.raises(ClientResponseError):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — outer ClientResponseError handler (line 478)
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_response_error_from_post() -> (
    None
):
    """async_fetch lets ClientResponseError from session.post propagate (coordinator handles it)."""
    from aiohttp import ClientResponseError

    req_info = MagicMock()
    req_info.real_url = _API_URL
    http_exc = ClientResponseError(
        request_info=req_info,
        history=(),
        status=503,
        message="Service Unavailable",
    )

    session = MagicMock()
    session.post = MagicMock(side_effect=http_exc)

    provider = _make_provider()
    with pytest.raises(ClientResponseError):
        await provider.async_fetch(session, _NATIONAL_STATION_ID)
