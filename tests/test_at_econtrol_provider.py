"""Tests for AtEcontrolProvider (e-control Austria)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.at_econtrol import (
    AtEcontrolProvider,
    _BASE_URL,
    _FUEL_CODES,
    _HEADERS,
    _build_station_data,
    _extract_prices,
    _format_address,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Test fixtures / constants
# ---------------------------------------------------------------------------

_STATION_ID = "12345"
_LAT = 48.2082
_LNG = 16.3738
_RADIUS_KM = 5.0

_BASE_LOCATION: dict = {
    "address": "Mariahilfer Strasse 1",
    "postalCode": "1060",
    "city": "Wien",
    "latitude": _LAT,
    "longitude": _LNG,
}

_BASE_PRICES_RAW: list[dict] = [
    {"fuelType": "DIE", "amount": 1.599},
    {"fuelType": "SUP", "amount": 1.679},
    {"fuelType": "GAS", "amount": 1.299},
]

_BASE_STATION_RAW: dict = {
    "id": int(_STATION_ID),
    "name": "OMV Wien Mariahilfer",
    "location": _BASE_LOCATION,
    "open": True,
    "prices": _BASE_PRICES_RAW,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: list | dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else [])
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _three_fuel_responses(
    station: dict | None = None,
    die_extra: dict | None = None,
    sup_extra: dict | None = None,
    gas_extra: dict | None = None,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Return three mock responses (DIE, SUP, GAS) each containing the given station."""
    s = station or _BASE_STATION_RAW
    die_station = {
        **s,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
        **(die_extra or {}),
    }
    sup_station = {
        **s,
        "prices": [{"fuelType": "SUP", "amount": 1.679}],
        **(sup_extra or {}),
    }
    gas_station = {
        **s,
        "prices": [{"fuelType": "GAS", "amount": 1.299}],
        **(gas_extra or {}),
    }
    die_resp = _make_mock_response(200, json_data=[die_station])
    sup_resp = _make_mock_response(200, json_data=[sup_station])
    gas_resp = _make_mock_response(200, json_data=[gas_station])
    return die_resp, sup_resp, gas_resp


def _provider(
    station_id: str = _STATION_ID,
    lat: float = _LAT,
    lng: float = _LNG,
    radius_km: float = _RADIUS_KM,
) -> AtEcontrolProvider:
    """Create an AtEcontrolProvider with default test coordinates."""
    return AtEcontrolProvider(
        station_id=station_id,
        latitude=lat,
        longitude=lng,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """AtEcontrolProvider declares COUNTRY='AT'."""
    assert AtEcontrolProvider.COUNTRY == "AT"


def test_provider_metadata_key() -> None:
    """AtEcontrolProvider declares PROVIDER_KEY='at_econtrol'."""
    assert AtEcontrolProvider.PROVIDER_KEY == "at_econtrol"


def test_provider_metadata_label() -> None:
    """AtEcontrolProvider declares a human-readable LABEL."""
    assert (
        "e-control" in AtEcontrolProvider.LABEL.lower()
        or "austria" in AtEcontrolProvider.LABEL.lower()
    )


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE must be 'location' for the location-based flow."""
    assert AtEcontrolProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE must be 'location_search'."""
    assert AtEcontrolProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_requires_no_api_key() -> None:
    """e-control API requires no authentication."""
    assert AtEcontrolProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_is_900() -> None:
    """POLL_INTERVAL_SECONDS should be 900 (15 minutes)."""
    assert AtEcontrolProvider.POLL_INTERVAL_SECONDS == 900


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------


def test_capabilities_include_diesel() -> None:
    assert "diesel" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_unleaded() -> None:
    assert "unleaded" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_cng() -> None:
    assert "cng" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_name() -> None:
    assert "name" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_county() -> None:
    assert "county" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_address() -> None:
    assert "address" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_latitude() -> None:
    assert "latitude" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_longitude() -> None:
    assert "longitude" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_is_open() -> None:
    assert "is_open" in AtEcontrolProvider.CAPABILITIES


def test_capabilities_exclude_lastupdated() -> None:
    assert "lastupdated" not in AtEcontrolProvider.CAPABILITIES


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = AtEcontrolProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_base_url_points_to_econtrol() -> None:
    """_BASE_URL must target the e-control.at API."""
    assert "e-control.at" in _BASE_URL
    assert _BASE_URL.startswith("https://")


def test_headers_include_accept_json() -> None:
    """_HEADERS must request JSON."""
    assert _HEADERS.get("Accept") == "application/json"


def test_headers_include_user_agent() -> None:
    """_HEADERS must include a non-blocked User-Agent."""
    ua = _HEADERS.get("User-Agent", "")
    assert ua, "User-Agent must not be empty"
    blocked = ("curl/", "python-requests/", "Wget/", "Go-http-client/")
    for prefix in blocked:
        assert not ua.startswith(prefix), (
            f"User-Agent '{ua}' uses blocked prefix '{prefix}'"
        )


def test_fuel_codes_map_die_to_diesel() -> None:
    """_FUEL_CODES must map DIE → diesel."""
    mapping = dict(_FUEL_CODES)
    assert mapping["DIE"] == "diesel"


def test_fuel_codes_map_sup_to_unleaded() -> None:
    """_FUEL_CODES must map SUP → unleaded."""
    mapping = dict(_FUEL_CODES)
    assert mapping["SUP"] == "unleaded"


def test_fuel_codes_map_gas_to_cng() -> None:
    """_FUEL_CODES must map GAS → cng (GAS is CNG per e-control API)."""
    mapping = dict(_FUEL_CODES)
    assert mapping["GAS"] == "cng"


def test_fuel_codes_has_exactly_three_entries() -> None:
    """_FUEL_CODES must contain exactly three entries."""
    assert len(_FUEL_CODES) == 3


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id."""
    p = AtEcontrolProvider("99999", latitude=48.0, longitude=16.0)
    assert p._station_id == "99999"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores lat/lng."""
    p = AtEcontrolProvider("1", latitude=47.5, longitude=15.5)
    assert p._latitude == pytest.approx(47.5)
    assert p._longitude == pytest.approx(15.5)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    p = AtEcontrolProvider("1", latitude=48.0, longitude=16.0, radius_km=7.5)
    assert p._radius_km == pytest.approx(7.5)


def test_constructor_stores_county() -> None:
    """Constructor stores optional county."""
    p = AtEcontrolProvider("1", county="Wien", latitude=48.0, longitude=16.0)
    assert p._county == "Wien"


def test_constructor_allows_none_coordinates() -> None:
    """Constructor accepts None for lat/lng (raises at fetch time, not construction)."""
    p = AtEcontrolProvider("1")
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# async_fetch — raises ProviderError when coordinates missing
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_when_latitude_none() -> None:
    """async_fetch raises ProviderError if latitude is not configured."""
    session = MagicMock()
    p = AtEcontrolProvider("1", longitude=16.0)
    with pytest.raises(ProviderError, match="latitude"):
        await p.async_fetch(session, "1")


async def test_async_fetch_raises_when_longitude_none() -> None:
    """async_fetch raises ProviderError if longitude is not configured."""
    session = MagicMock()
    p = AtEcontrolProvider("1", latitude=48.0)
    with pytest.raises(ProviderError, match="longitude"):
        await p.async_fetch(session, "1")


async def test_async_fetch_raises_when_both_coordinates_none() -> None:
    """async_fetch raises ProviderError if both lat and lng are not configured."""
    session = MagicMock()
    p = AtEcontrolProvider("1")
    with pytest.raises(ProviderError):
        await p.async_fetch(session, "1")


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a StationData dict on a successful 3-call merge."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_returns_diesel_price() -> None:
    """async_fetch returns diesel price from DIE call."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.599)


async def test_async_fetch_returns_unleaded_price() -> None:
    """async_fetch returns unleaded price from SUP call."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.679)


async def test_async_fetch_returns_cng_price() -> None:
    """async_fetch returns cng price from GAS call (GAS→cng mapping)."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["cng"] == pytest.approx(1.299)


async def test_async_fetch_returns_station_name() -> None:
    """async_fetch populates the 'name' field from API response."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["name"] == "OMV Wien Mariahilfer"


async def test_async_fetch_returns_county_from_location_city() -> None:
    """async_fetch maps location.city → county (no Bundesland from API)."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["county"] == "Wien"


async def test_async_fetch_returns_address() -> None:
    """async_fetch populates 'address' from location.address + postal + city."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["address"] is not None
    assert "Mariahilfer" in data["address"]


async def test_async_fetch_returns_latitude() -> None:
    """async_fetch populates latitude from location dict."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(_LAT)


async def test_async_fetch_returns_longitude() -> None:
    """async_fetch populates longitude from location dict."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["longitude"] == pytest.approx(_LNG)


async def test_async_fetch_returns_is_open_true() -> None:
    """async_fetch returns is_open=True when station open field is True."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["is_open"] is True


async def test_async_fetch_returns_is_open_false() -> None:
    """async_fetch returns is_open=False when station open field is False."""
    closed_station = {**_BASE_STATION_RAW, "open": False}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=closed_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["is_open"] is False


async def test_async_fetch_is_open_none_when_field_absent() -> None:
    """async_fetch returns is_open=None when 'open' key is absent from station."""
    no_open_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "open"}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=no_open_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["is_open"] is None


async def test_async_fetch_lastupdated_is_none() -> None:
    """async_fetch returns lastupdated=None (API provides no per-station timestamps)."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] is None


# ---------------------------------------------------------------------------
# async_fetch — all CAPABILITIES keys populated
# ---------------------------------------------------------------------------


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch returns a dict with all CAPABILITIES keys present."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    capability_data_keys = AtEcontrolProvider.CAPABILITIES - {
        "last_successful_fetch",
        "data_fetch_problem",
    }
    for key in capability_data_keys:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


# ---------------------------------------------------------------------------
# async_fetch — 3-call fan-out
# ---------------------------------------------------------------------------


async def test_async_fetch_skips_station_with_missing_id() -> None:
    """_fetch_all_fuel_types skips station entries whose 'id' field is absent or falsy."""
    # Station with no id alongside the valid station — no-id entry must be ignored
    no_id_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "id"}
    valid_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
    }
    die_resp = _make_mock_response(200, json_data=[no_id_station, valid_station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    # Should succeed with the valid station; no_id_station is skipped
    data = await p.async_fetch(session, _STATION_ID)

    assert data is not None
    assert data["diesel"] == pytest.approx(1.599)


async def test_async_fetch_makes_three_api_calls() -> None:
    """async_fetch issues exactly 3 GET requests (one per fuel code: DIE, SUP, GAS)."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    assert session.get.call_count == 3


async def test_async_fetch_passes_fueltype_die_in_first_call() -> None:
    """First API call must include fuelType=DIE in the query params."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    first_call = session.get.call_args_list[0]
    params = first_call.kwargs.get("params", {})
    assert params.get("fuelType") == "DIE"


async def test_async_fetch_passes_fueltype_sup_in_second_call() -> None:
    """Second API call must include fuelType=SUP in the query params."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    second_call = session.get.call_args_list[1]
    params = second_call.kwargs.get("params", {})
    assert params.get("fuelType") == "SUP"


async def test_async_fetch_passes_fueltype_gas_in_third_call() -> None:
    """Third API call must include fuelType=GAS in the query params."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    third_call = session.get.call_args_list[2]
    params = third_call.kwargs.get("params", {})
    assert params.get("fuelType") == "GAS"


async def test_async_fetch_passes_coordinates_in_params() -> None:
    """Every API call must pass latitude and longitude as query parameters."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert "latitude" in params, f"latitude missing from params: {params}"
        assert "longitude" in params, f"longitude missing from params: {params}"


async def test_async_fetch_passes_include_closed_true() -> None:
    """Every API call must include includeClosed=true."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("includeClosed") == "true", (
            f"includeClosed=true missing from call params: {params}"
        )


async def test_async_fetch_passes_headers_on_every_request() -> None:
    """Every API call must include the Accept: application/json header."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    await p.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert headers.get("Accept") == "application/json", (
            f"Accept header missing from call: {call}"
        )


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_absent() -> None:
    """async_fetch raises ProviderError when the requested station_id is not in results."""
    other_station = {**_BASE_STATION_RAW, "id": 99999}
    die_resp = _make_mock_response(200, json_data=[other_station])
    sup_resp = _make_mock_response(200, json_data=[other_station])
    gas_resp = _make_mock_response(200, json_data=[other_station])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    with pytest.raises(ProviderError, match=_STATION_ID):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_when_all_responses_empty() -> None:
    """async_fetch raises ProviderError when all three fuel queries return empty lists."""
    die_resp = _make_mock_response(200, json_data=[])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — HTTP error propagation
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """ClientError from aiohttp propagates out of async_fetch."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    p = _provider()
    with pytest.raises(ClientError):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_non_200_via_raise_for_status() -> None:
    """HTTP 500 causes raise_for_status() to propagate an error."""
    resp_500 = _make_mock_response(500, json_data=[])
    resp_500.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = MagicMock()
    session.get = MagicMock(return_value=resp_500)

    p = _provider()
    with pytest.raises((ClientError, ProviderError)):
        await p.async_fetch(session, _STATION_ID)


async def test_async_fetch_handles_non_list_response_gracefully() -> None:
    """When a fuel-type response is not a list (malformed), it is skipped."""
    die_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    sup_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    gas_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    # All three calls return dicts instead of lists — merged will be empty → ProviderError
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — price normalisation (cents → EUR guard)
# ---------------------------------------------------------------------------


async def test_async_fetch_price_already_in_eur_not_divided() -> None:
    """Prices ≤10 are stored as-is (already EUR/litre)."""
    station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
    }
    die_resp = _make_mock_response(200, json_data=[station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.599)


async def test_async_fetch_price_in_cents_divided_by_100() -> None:
    """Prices >10 are divided by 100 (cents → EUR)."""
    station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE", "amount": 159.9}],
    }
    die_resp = _make_mock_response(200, json_data=[station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.599)


async def test_async_fetch_price_none_when_no_price_entry() -> None:
    """Fuel type price is None when the station has no prices[] entry for it."""
    station = {**_BASE_STATION_RAW, "prices": []}
    die_resp = _make_mock_response(200, json_data=[station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] is None
    assert data["unleaded"] is None
    assert data["cng"] is None


async def test_async_fetch_price_none_when_amount_missing() -> None:
    station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE"}],  # no amount
    }
    die_resp = _make_mock_response(200, json_data=[station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch — field mapping edge cases
# ---------------------------------------------------------------------------


async def test_async_fetch_name_none_when_absent() -> None:
    """async_fetch returns name=None when API omits the name field."""
    no_name_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "name"}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=no_name_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["name"] is None


async def test_async_fetch_county_none_when_no_city_in_location() -> None:
    """async_fetch returns county=None when location has no city field."""
    location_no_city = {k: v for k, v in _BASE_LOCATION.items() if k != "city"}
    station = {**_BASE_STATION_RAW, "location": location_no_city}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["county"] is None


async def test_async_fetch_lat_lng_none_when_location_absent() -> None:
    """async_fetch returns latitude=None, longitude=None when location dict absent."""
    no_loc_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "location"}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=no_loc_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["latitude"] is None
    assert data["longitude"] is None


async def test_async_fetch_address_none_when_location_fields_absent() -> None:
    """async_fetch returns address=None when all location address parts are missing."""
    empty_location = {"latitude": _LAT, "longitude": _LNG}
    station = {**_BASE_STATION_RAW, "location": empty_location}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["address"] is None


async def test_async_fetch_merges_prices_from_all_three_calls() -> None:
    """When a station appears in all three fuel-type responses, all prices are populated."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data["diesel"] is not None
    assert data["unleaded"] is not None
    assert data["cng"] is not None


async def test_async_fetch_station_id_str_match() -> None:
    """async_fetch correctly matches station by string representation of integer id."""
    station = {**_BASE_STATION_RAW, "id": 12345}  # int in API response
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider(station_id="12345")
    data = await p.async_fetch(session, "12345")

    assert data is not None


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns the station name from API."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name == "OMV Wien Mariahilfer"


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None when a ClientError occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_not_found() -> None:
    """async_fetch_station_name returns None when station_id absent from results."""
    other_station = {**_BASE_STATION_RAW, "id": 99999}
    die_resp = _make_mock_response(200, json_data=[other_station])
    sup_resp = _make_mock_response(200, json_data=[other_station])
    gas_resp = _make_mock_response(200, json_data=[other_station])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_all_empty() -> None:
    """async_fetch_station_name returns None when all API responses are empty lists."""
    die_resp = _make_mock_response(200, json_data=[])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_no_coordinates() -> None:
    """async_fetch_station_name returns None immediately when no lat/lng configured."""
    session = MagicMock()
    p = AtEcontrolProvider("1")  # no coordinates

    name = await p.async_fetch_station_name(session, "1")

    assert name is None
    session.get.assert_not_called()


async def test_async_fetch_station_name_returns_none_for_nameless_station() -> None:
    """async_fetch_station_name returns None when station has no name field."""
    no_name_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "name"}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=no_name_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    name = await p.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (id, label) tuples."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert isinstance(result, list)
    assert len(result) == 1
    sid, label = result[0]
    assert sid == _STATION_ID
    assert isinstance(label, str)


async def test_async_list_stations_label_contains_station_name() -> None:
    """Each label in async_list_stations includes the station name."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "OMV Wien Mariahilfer" in label


async def test_async_list_stations_label_contains_diesel_price() -> None:
    """Each label includes a diesel price string."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Diesel" in label
    assert "1.599" in label


async def test_async_list_stations_label_contains_super95_price() -> None:
    """Each label includes a Super 95 price string."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "Super 95" in label
    assert "1.679" in label


async def test_async_list_stations_label_contains_cng_price() -> None:
    """Each label includes a CNG price string (GAS→cng mapped, displayed as 'CNG')."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert "CNG" in label
    assert "1.299" in label


async def test_async_list_stations_uses_constructor_coordinates_when_no_kwargs() -> (
    None
):
    """async_list_stations falls back to constructor lat/lng when not passed as kwargs."""
    die_resp, sup_resp, gas_resp = _three_fuel_responses()
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider(lat=_LAT, lng=_LNG)
    result = await p.async_list_stations(session)

    assert len(result) == 1


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when no coordinates are available at all."""
    session = MagicMock()
    p = AtEcontrolProvider("1")  # no lat/lng at construction

    result = await p.async_list_stations(session)

    assert result == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_returns_empty_when_api_empty() -> None:
    """async_list_stations returns [] when all API responses return empty lists."""
    die_resp = _make_mock_response(200, json_data=[])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_sorted_by_cheapest_diesel() -> None:
    """async_list_stations sorts results by cheapest diesel price ascending."""
    cheap_station = {
        "id": 11111,
        "name": "Cheap Station",
        "location": {**_BASE_LOCATION, "city": "Graz"},
        "open": True,
        "prices": [{"fuelType": "DIE", "amount": 1.499}],
    }
    expensive_station = {
        "id": 22222,
        "name": "Expensive Station",
        "location": {**_BASE_LOCATION, "city": "Salzburg"},
        "open": True,
        "prices": [{"fuelType": "DIE", "amount": 1.799}],
    }

    # DIE response has both stations; SUP and GAS return them without diesel prices
    die_resp = _make_mock_response(200, json_data=[expensive_station, cheap_station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 2
    first_id, _ = result[0]
    assert first_id == "11111", "Cheapest diesel station should be listed first"


async def test_async_list_stations_no_price_sorts_last() -> None:
    """Stations with no diesel price sort after priced stations."""
    priced_station = {
        "id": 11111,
        "name": "Priced Station",
        "location": _BASE_LOCATION,
        "open": True,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
    }
    no_price_station = {
        "id": 22222,
        "name": "No Price Station",
        "location": {**_BASE_LOCATION, "city": "Innsbruck"},
        "open": True,
        "prices": [],
    }

    die_resp = _make_mock_response(200, json_data=[no_price_station, priced_station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 2
    first_id, _ = result[0]
    assert first_id == "11111", "Priced station should appear before no-price station"


async def test_async_list_stations_fallback_label_when_name_missing() -> None:
    """Label falls back to 'Station {id}' when station name is missing."""
    no_name_station = {k: v for k, v in _BASE_STATION_RAW.items() if k != "name"}
    die_resp, sup_resp, gas_resp = _three_fuel_responses(station=no_name_station)
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    _, label = result[0]
    assert f"Station {_STATION_ID}" in label


async def test_async_list_stations_multiple_stations_merged() -> None:
    """Stations returned across multiple fuel-type calls are merged, not duplicated."""
    station_a = {**_BASE_STATION_RAW, "prices": [{"fuelType": "DIE", "amount": 1.599}]}
    station_b = {
        "id": 67890,
        "name": "BP Linz",
        "location": {**_BASE_LOCATION, "city": "Linz"},
        "open": True,
        "prices": [{"fuelType": "SUP", "amount": 1.689}],
    }

    die_resp = _make_mock_response(200, json_data=[station_a])
    sup_resp = _make_mock_response(200, json_data=[station_b])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    ids = {sid for sid, _ in result}
    assert _STATION_ID in ids
    assert "67890" in ids
    assert len(result) == 2, "Each station should appear exactly once"


# ---------------------------------------------------------------------------
# _extract_prices (module-level helper)
# ---------------------------------------------------------------------------


def test_extract_prices_die_mapped_to_diesel() -> None:
    """_extract_prices maps DIE fuelType to 'diesel' key."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": 1.599}])
    assert prices.get("diesel") == pytest.approx(1.599)


def test_extract_prices_sup_mapped_to_unleaded() -> None:
    """_extract_prices maps SUP fuelType to 'unleaded' key."""
    prices = _extract_prices([{"fuelType": "SUP", "amount": 1.679}])
    assert prices.get("unleaded") == pytest.approx(1.679)


def test_extract_prices_gas_mapped_to_cng() -> None:
    """_extract_prices maps GAS fuelType to 'cng' key."""
    prices = _extract_prices([{"fuelType": "GAS", "amount": 1.299}])
    assert prices.get("cng") == pytest.approx(1.299)


def test_extract_prices_empty_list_returns_empty_dict() -> None:
    """_extract_prices handles an empty prices list without error."""
    prices = _extract_prices([])
    assert prices == {}


def test_extract_prices_none_list_returns_empty_dict() -> None:
    """_extract_prices handles None gracefully (or [])."""
    prices = _extract_prices(None)  # type: ignore[arg-type]
    assert prices == {}


def test_extract_prices_skips_entry_without_fuel_type() -> None:
    """_extract_prices skips entries missing fuelType."""
    prices = _extract_prices([{"amount": 1.599}])
    assert prices == {}


def test_extract_prices_skips_entry_without_amount() -> None:
    """_extract_prices skips entries missing amount."""
    prices = _extract_prices([{"fuelType": "DIE"}])
    assert prices == {}


def test_extract_prices_skips_unknown_fuel_type() -> None:
    """_extract_prices skips entries with unrecognised fuelType codes."""
    prices = _extract_prices([{"fuelType": "HYDROGEN", "amount": 9.99}])
    assert prices == {}


def test_extract_prices_skips_zero_amount() -> None:
    """_extract_prices skips entries where amount is zero."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": 0}])
    assert "diesel" not in prices


def test_extract_prices_skips_negative_amount() -> None:
    """_extract_prices skips entries where amount is negative."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": -1.5}])
    assert "diesel" not in prices


def test_extract_prices_skips_non_numeric_amount() -> None:
    """_extract_prices skips entries where amount cannot be parsed as float."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": "not-a-number"}])
    assert "diesel" not in prices


def test_extract_prices_cents_divided_by_100() -> None:
    """_extract_prices divides amounts >10 by 100 to convert cents to EUR."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": 159.9}])
    assert prices.get("diesel") == pytest.approx(1.599)


def test_extract_prices_exact_10_not_divided() -> None:
    """_extract_prices does NOT divide by 100 when amount is exactly 10."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": 10.0}])
    assert prices.get("diesel") == pytest.approx(10.0)


def test_extract_prices_multiple_fuel_types_in_one_call() -> None:
    """_extract_prices handles a list with multiple fuel type entries."""
    prices = _extract_prices(
        [
            {"fuelType": "DIE", "amount": 1.599},
            {"fuelType": "SUP", "amount": 1.679},
            {"fuelType": "GAS", "amount": 1.299},
        ]
    )
    assert prices.get("diesel") == pytest.approx(1.599)
    assert prices.get("unleaded") == pytest.approx(1.679)
    assert prices.get("cng") == pytest.approx(1.299)


def test_extract_prices_rounds_to_4_decimal_places() -> None:
    """_extract_prices rounds the result to 4 decimal places."""
    prices = _extract_prices([{"fuelType": "DIE", "amount": 1.59999999}])
    diesel = prices.get("diesel")
    assert diesel is not None
    # Result should be rounded — no more than 4 decimal places of precision
    assert diesel == round(diesel, 4)


# ---------------------------------------------------------------------------
# _format_address (module-level helper)
# ---------------------------------------------------------------------------


def test_format_address_combines_street_postal_city() -> None:
    """_format_address returns 'street, postal city' when all parts are present."""
    result = _format_address(
        {"address": "Mariahilfer Str. 1", "postalCode": "1060", "city": "Wien"}
    )
    assert "Mariahilfer Str. 1" in result
    assert "1060" in result
    assert "Wien" in result


def test_format_address_omits_missing_street() -> None:
    """_format_address omits the street component when absent."""
    result = _format_address({"postalCode": "1060", "city": "Wien"})
    assert "1060 Wien" in result


def test_format_address_omits_missing_postal() -> None:
    """_format_address omits postal code when absent."""
    result = _format_address({"address": "Mariahilfer Str. 1", "city": "Wien"})
    assert "Wien" in result


def test_format_address_returns_empty_string_for_empty_dict() -> None:
    """_format_address returns empty string for an empty location dict."""
    result = _format_address({})
    assert result == ""


def test_format_address_handles_none_values() -> None:
    """_format_address handles None values for address fields without error."""
    result = _format_address({"address": None, "postalCode": None, "city": None})
    assert result == ""


def test_format_address_city_only() -> None:
    """_format_address returns just the city when only city is present."""
    result = _format_address({"city": "Graz"})
    assert "Graz" in result


# ---------------------------------------------------------------------------
# _build_station_data (module-level helper)
# ---------------------------------------------------------------------------


def test_build_station_data_returns_all_capability_keys() -> None:
    """_build_station_data returns a dict with all expected StationData keys."""
    result = _build_station_data(_BASE_STATION_RAW)

    required_keys = {
        "diesel",
        "unleaded",
        "cng",
        "name",
        "county",
        "address",
        "latitude",
        "longitude",
        "is_open",
        "lastupdated",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _build_station_data result"


def test_build_station_data_diesel_price() -> None:
    """_build_station_data correctly extracts diesel price."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["diesel"] == pytest.approx(1.599)


def test_build_station_data_unleaded_price() -> None:
    """_build_station_data correctly extracts unleaded price."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["unleaded"] == pytest.approx(1.679)


def test_build_station_data_cng_price() -> None:
    """_build_station_data correctly extracts cng price (GAS→cng)."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["cng"] == pytest.approx(1.299)


def test_build_station_data_name() -> None:
    """_build_station_data preserves station name."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["name"] == "OMV Wien Mariahilfer"


def test_build_station_data_county_from_location_city() -> None:
    """_build_station_data maps location.city → county."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["county"] == "Wien"


def test_build_station_data_latitude_and_longitude() -> None:
    """_build_station_data extracts latitude and longitude from location."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["latitude"] == pytest.approx(_LAT)
    assert result["longitude"] == pytest.approx(_LNG)


def test_build_station_data_is_open_true() -> None:
    """_build_station_data returns is_open=True when open=True."""
    result = _build_station_data({**_BASE_STATION_RAW, "open": True})
    assert result["is_open"] is True


def test_build_station_data_is_open_false() -> None:
    """_build_station_data returns is_open=False when open=False."""
    result = _build_station_data({**_BASE_STATION_RAW, "open": False})
    assert result["is_open"] is False


def test_build_station_data_is_open_none_when_absent() -> None:
    """_build_station_data returns is_open=None when 'open' key missing."""
    no_open = {k: v for k, v in _BASE_STATION_RAW.items() if k != "open"}
    result = _build_station_data(no_open)
    assert result["is_open"] is None


def test_build_station_data_lastupdated_is_none() -> None:
    """_build_station_data sets lastupdated=None (API has no price timestamps)."""
    result = _build_station_data(_BASE_STATION_RAW)
    assert result["lastupdated"] is None


def test_build_station_data_latitude_none_on_invalid_value() -> None:
    """_build_station_data returns latitude=None when location.latitude is invalid."""
    station = {
        **_BASE_STATION_RAW,
        "location": {**_BASE_LOCATION, "latitude": "not-a-float"},
    }
    result = _build_station_data(station)
    assert result["latitude"] is None


def test_build_station_data_longitude_none_on_invalid_value() -> None:
    """_build_station_data returns longitude=None when location.longitude is invalid."""
    station = {
        **_BASE_STATION_RAW,
        "location": {**_BASE_LOCATION, "longitude": "not-a-float"},
    }
    result = _build_station_data(station)
    assert result["longitude"] is None


def test_build_station_data_name_none_when_empty_string() -> None:
    """_build_station_data normalises empty string name to None."""
    station = {**_BASE_STATION_RAW, "name": ""}
    result = _build_station_data(station)
    assert result["name"] is None


def test_build_station_data_address_none_when_no_address_parts() -> None:
    """_build_station_data sets address=None when location has no address components."""
    station = {**_BASE_STATION_RAW, "location": {"latitude": _LAT, "longitude": _LNG}}
    result = _build_station_data(station)
    assert result["address"] is None


def test_build_station_data_county_none_when_no_city() -> None:
    """_build_station_data sets county=None when location has no city."""
    location = {k: v for k, v in _BASE_LOCATION.items() if k != "city"}
    station = {**_BASE_STATION_RAW, "location": location}
    result = _build_station_data(station)
    assert result["county"] is None


def test_build_station_data_empty_prices_all_none() -> None:
    """_build_station_data sets all fuel prices to None when prices list is empty."""
    station = {**_BASE_STATION_RAW, "prices": []}
    result = _build_station_data(station)
    assert result["diesel"] is None
    assert result["unleaded"] is None
    assert result["cng"] is None


def test_build_station_data_no_location_field() -> None:
    """_build_station_data handles absence of 'location' key without raising."""
    no_location = {k: v for k, v in _BASE_STATION_RAW.items() if k != "location"}
    result = _build_station_data(no_location)
    assert result["latitude"] is None
    assert result["longitude"] is None
    assert result["county"] is None
    assert result["address"] is None


# ---------------------------------------------------------------------------
# Partial timeout / partial fuel-type failure
# ---------------------------------------------------------------------------


async def test_async_fetch_station_populated_when_only_two_fuel_calls_return_data() -> (
    None
):
    """Station is still returned when only 2 of 3 fuel-type calls return the station.

    If GAS (cng) returns an empty list, diesel and unleaded prices should still
    be populated and the station should be retrievable.
    """
    die_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
    }
    sup_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "SUP", "amount": 1.679}],
    }
    die_resp = _make_mock_response(200, json_data=[die_station])
    sup_resp = _make_mock_response(200, json_data=[sup_station])
    gas_resp = _make_mock_response(200, json_data=[])  # GAS call returns nothing
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    # Station is found and prices from the two successful calls are populated
    assert data is not None
    assert data["diesel"] == pytest.approx(1.599)
    assert data["unleaded"] == pytest.approx(1.679)
    # cng is None because GAS call returned nothing
    assert data["cng"] is None


async def test_async_fetch_station_populated_when_first_fuel_call_empty() -> None:
    """Station is still returned when the first (DIE) fuel-type call returns empty.

    SUP and GAS results still populate unleaded and cng prices.
    """
    sup_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "SUP", "amount": 1.679}],
    }
    gas_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "GAS", "amount": 1.299}],
    }
    die_resp = _make_mock_response(200, json_data=[])  # DIE call empty
    sup_resp = _make_mock_response(200, json_data=[sup_station])
    gas_resp = _make_mock_response(200, json_data=[gas_station])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    data = await p.async_fetch(session, _STATION_ID)

    assert data is not None
    assert data["diesel"] is None
    assert data["unleaded"] == pytest.approx(1.679)
    assert data["cng"] == pytest.approx(1.299)


async def test_async_list_stations_partial_fuel_calls_still_returns_station() -> None:
    """async_list_stations returns a station even when only 1 of 3 fuel calls has it."""
    die_station = {
        **_BASE_STATION_RAW,
        "prices": [{"fuelType": "DIE", "amount": 1.599}],
    }
    die_resp = _make_mock_response(200, json_data=[die_station])
    sup_resp = _make_mock_response(200, json_data=[])
    gas_resp = _make_mock_response(200, json_data=[])
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert len(result) == 1
    sid, label = result[0]
    assert sid == _STATION_ID
    assert "Diesel" in label


# ---------------------------------------------------------------------------
# Non-JSON / unexpected response to _fetch_fuel_type
# ---------------------------------------------------------------------------


async def test_fetch_fuel_type_non_list_json_returns_empty_list() -> None:
    """_fetch_fuel_type returns [] when API sends a non-list JSON body (e.g. a dict).

    The provider's internal guard ensures non-list responses are silently
    ignored rather than causing a crash or wrong type propagation.
    """
    # Return a dict instead of a list — _fetch_fuel_type should return []
    die_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    sup_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    gas_resp = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    # All three fuel calls return dicts; merged dict will be empty → ProviderError
    with pytest.raises(ProviderError):
        await p.async_fetch(session, _STATION_ID)


async def test_async_list_stations_non_list_json_response_returns_empty() -> None:
    """async_list_stations returns [] when all fuel-type calls return non-list JSON.

    Even though _fetch_fuel_type converts the non-list response to [], the
    merged dict will be empty, resulting in an empty stations list.
    """
    die_resp = _make_mock_response(200, json_data={"status": "error"})
    sup_resp = _make_mock_response(200, json_data={"status": "error"})
    gas_resp = _make_mock_response(200, json_data={"status": "error"})
    session = _make_session(die_resp, sup_resp, gas_resp)

    p = _provider()
    result = await p.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []
