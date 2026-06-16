"""Tests for EsMineturProvider (Spain — MINETUR government open-data)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.es_minetur import (
    EsMineturProvider,
    _HEADERS,
    _find_station,
    _haversine_km,
    _normalise_fecha,
    _parse_coord,
    _parse_price,
    _parse_station,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATION_ID = "4375"

# A realistic raw station record as returned by the MINETUR API.
_BASE_STATION: dict = {
    "IDEESS": "4375",
    "Rótulo": "REPSOL",
    "Dirección": "CALLE MAYOR, 10",
    "Municipio": "MADRID",
    "Provincia": "MADRID",
    "C.P.": "28013",
    "Latitud": "40,416775",
    "Longitud (WGS84)": "-3,703790",
    "Precio Gasolina 95 E5": "1,629",
    "Precio Gasoleo A": "1,549",
    "Precio Gasolina 98 E5": "1,789",
    "Precio Gases licuados del petróleo": "0,899",
    "Precio Gasoleo B": "",
    "Precio Gasoleo Premium": "1,619",
}

# Top-level Fecha timestamp as returned in the response envelope.
_FECHA = "14/06/2026 4:50:43"

# A minimal valid API envelope wrapping the station list.
_PAYLOAD: dict = {
    "Fecha": _FECHA,
    "Nota": "Precio de venta al público en euros/litro. IVA incluido. Precios actualizado cada 30 minutos.",
    "ResultadoConsulta": "OK",
    "ListaEESSPrecio": [_BASE_STATION],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    body_bytes: bytes | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager.

    The MINETUR provider reads raw bytes via resp.read(), so this helper
    exposes a read() coroutine rather than json()/text().
    """
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.raise_for_status = MagicMock()
    if body_bytes is None:
        body_bytes = json.dumps(_PAYLOAD).encode("utf-8")
    mock_resp.read = AsyncMock(return_value=body_bytes)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns *response*."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _payload_bytes(payload: dict) -> bytes:
    """Serialise *payload* to JSON bytes (UTF-8, no BOM)."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _payload_bytes_bom(payload: dict) -> bytes:
    """Serialise *payload* to JSON bytes with a UTF-8 BOM prefix."""
    return b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata() -> None:
    """EsMineturProvider declares required class attributes."""
    assert EsMineturProvider.COUNTRY == "ES"
    assert EsMineturProvider.PROVIDER_KEY == "es_minetur"
    assert EsMineturProvider.LABEL == "MINETUR (Spain)"


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE must be 'location' — user supplies lat/lng, not a station ID."""
    assert EsMineturProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert EsMineturProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """POLL_INTERVAL_SECONDS is 1800 (matches the API's 30-minute update cadence)."""
    assert EsMineturProvider.POLL_INTERVAL_SECONDS == 1800


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all MINETUR fuel types."""
    caps = EsMineturProvider.CAPABILITIES
    for fuel in ("unleaded", "diesel", "premium_unleaded", "lpg"):
        assert fuel in caps, f"'{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = EsMineturProvider.CAPABILITIES
    for field in ("name", "brand", "county", "address", "latitude", "longitude"):
        assert field in caps, f"'{field}' missing from CAPABILITIES"


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = EsMineturProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


def test_provider_capabilities_include_lastupdated() -> None:
    """CAPABILITIES includes lastupdated."""
    assert "lastupdated" in EsMineturProvider.CAPABILITIES


def test_provider_no_api_key_required() -> None:
    """MINETUR is unauthenticated — REQUIRES_API_KEY must not be set True."""
    # The attribute may be absent (default) or explicitly False; never True.
    assert getattr(EsMineturProvider, "REQUIRES_API_KEY", False) is False


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Provider stores station_id correctly."""
    p = EsMineturProvider("4375")
    assert p._station_id == "4375"


def test_constructor_optional_params_default_to_none() -> None:
    """County, latitude, longitude default to None when not supplied."""
    p = EsMineturProvider("4375")
    assert p._county is None
    assert p._latitude is None
    assert p._longitude is None


def test_constructor_radius_km_defaults_to_10() -> None:
    """radius_km defaults to 10.0 when not supplied."""
    p = EsMineturProvider("4375")
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_stores_all_params() -> None:
    """All constructor parameters are stored correctly."""
    p = EsMineturProvider(
        "4375",
        county="Madrid",
        latitude=40.4168,
        longitude=-3.7038,
        radius_km=5.0,
    )
    assert p._station_id == "4375"
    assert p._county == "Madrid"
    assert p._latitude == pytest.approx(40.4168)
    assert p._longitude == pytest.approx(-3.7038)
    assert p._radius_km == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Request headers
# ---------------------------------------------------------------------------


def test_headers_include_accept_json() -> None:
    """_HEADERS includes Accept: application/json."""
    assert _HEADERS.get("Accept") == "application/json"


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent that is not a blocked bot string."""
    ua = _HEADERS.get("User-Agent", "")
    assert ua, "User-Agent must not be empty"
    blocked = ("curl/", "python-requests/", "Wget/", "Go-http-client/")
    for prefix in blocked:
        assert not ua.startswith(prefix), (
            f"User-Agent '{ua}' starts with blocked prefix '{prefix}'"
        )


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns populated StationData when station is found."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch returns correct diesel price parsed from comma-decimal string."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.549)


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch returns correct unleaded price parsed from comma-decimal string."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.629)


async def test_async_fetch_success_premium_unleaded_price() -> None:
    """async_fetch returns correct premium_unleaded price."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.789)


async def test_async_fetch_success_lpg_price() -> None:
    """async_fetch returns correct LPG price."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lpg"] == pytest.approx(0.899)


async def test_async_fetch_station_identity_fields() -> None:
    """async_fetch populates brand, address, county from API response."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["brand"] == "REPSOL"
    assert data["name"] == "REPSOL"
    assert data["address"] == "CALLE MAYOR, 10"
    assert data["county"] == "Madrid"


async def test_async_fetch_location_fields() -> None:
    """async_fetch populates latitude and longitude from comma-decimal strings."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(40.416775)
    assert data["longitude"] == pytest.approx(-3.703790)


async def test_async_fetch_lastupdated_iso8601() -> None:
    """async_fetch converts Fecha 'DD/MM/YYYY H:MM:SS' to ISO 8601."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] == "2026-06-14T04:50:43"


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch result includes every key declared in CAPABILITIES."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    skipped = {"last_successful_fetch", "data_fetch_problem"}
    for key in EsMineturProvider.CAPABILITIES - skipped:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch result"


async def test_async_fetch_uses_get_with_correct_url() -> None:
    """async_fetch calls session.get with the MINETUR national endpoint."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_args = session.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "minetur.gob.es" in url or "sedeaplicaciones" in url


async def test_async_fetch_sends_accept_json_header() -> None:
    """async_fetch passes Accept: application/json header in every GET."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_headers = session.get.call_args.kwargs.get("headers", {})
    assert call_headers.get("Accept") == "application/json"


# ---------------------------------------------------------------------------
# async_fetch — UTF-8 BOM handling
# ---------------------------------------------------------------------------


async def test_async_fetch_handles_utf8_bom() -> None:
    """async_fetch decodes responses with a UTF-8 BOM without error."""
    resp = _make_mock_response(200, body_bytes=_payload_bytes_bom(_PAYLOAD))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.549)


async def test_async_fetch_handles_no_bom() -> None:
    """async_fetch decodes responses without a BOM correctly."""
    resp = _make_mock_response(200, body_bytes=_payload_bytes(_PAYLOAD))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.549)


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_station_not_found() -> None:
    """async_fetch raises ProviderError when IDEESS not in the dataset."""
    payload = {**_PAYLOAD, "ListaEESSPrecio": [{**_BASE_STATION, "IDEESS": "9999"}]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="4375"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_empty_list() -> None:
    """async_fetch raises ProviderError when ListaEESSPrecio is empty."""
    payload = {**_PAYLOAD, "ListaEESSPrecio": []}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_missing_list_key() -> None:
    """async_fetch raises ProviderError when ListaEESSPrecio key is absent."""
    payload = {"Fecha": _FECHA, "ResultadoConsulta": "OK"}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — API-level errors
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_bad_resultado() -> None:
    """async_fetch raises ProviderError when ResultadoConsulta is not 'OK'."""
    payload = {**_PAYLOAD, "ResultadoConsulta": "ERROR"}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="ResultadoConsulta"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_invalid_json() -> None:
    """async_fetch raises ProviderError when response body is not valid JSON."""
    resp = _make_mock_response(200, body_bytes=b"NOT JSON {{{")
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="JSON"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_invalid_utf8() -> None:
    """async_fetch raises ProviderError when bytes cannot be decoded as UTF-8."""
    # Lone continuation byte is invalid in UTF-8
    resp = _make_mock_response(200, body_bytes=b"\x80\x81\x82 invalid utf-8 bytes")
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="UTF-8"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch lets ClientError propagate (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_http_500() -> None:
    """async_fetch surfaces HTTP 500 via raise_for_status."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(side_effect=ClientError("500 Server Error"))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)

    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch — empty price fields
# ---------------------------------------------------------------------------


async def test_async_fetch_empty_price_field_returns_none() -> None:
    """async_fetch returns None for fuel types with empty string price fields."""
    station_no_lpg = {**_BASE_STATION, "Precio Gases licuados del petróleo": ""}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [station_no_lpg]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lpg"] is None


async def test_async_fetch_zero_price_field_returns_none() -> None:
    """async_fetch returns None when price parses to 0 (sentinel for 'no price')."""
    station_zero = {**_BASE_STATION, "Precio Gasoleo A": "0,000"}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [station_zero]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# async_fetch — county title-case normalisation
# ---------------------------------------------------------------------------


async def test_async_fetch_county_title_cased() -> None:
    """async_fetch title-cases the Provincia field."""
    station_caps = {**_BASE_STATION, "Provincia": "BARCELONA"}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [station_caps]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider("4375")
    data = await provider.async_fetch(session, "4375")

    assert data["county"] == "Barcelona"


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none() -> None:
    """async_fetch_station_name always returns None for location-mode providers."""
    session = MagicMock()
    provider = EsMineturProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)
    assert name is None


async def test_async_fetch_station_name_does_not_call_session() -> None:
    """async_fetch_station_name makes no network calls (returns None immediately)."""
    session = MagicMock()
    session.get = MagicMock()

    provider = EsMineturProvider(_STATION_ID)
    await provider.async_fetch_station_name(session, _STATION_ID)

    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_nearby_stations() -> None:
    """async_list_stations returns (ideess, label) tuples for stations within radius."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert len(results) == 1
    ideess, label = results[0]
    assert ideess == "4375"
    assert "REPSOL" in label


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """Station label includes short station ID suffix (no price)."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    _, label = results[0]
    assert "(#" in label


async def test_async_list_stations_label_includes_unleaded_price() -> None:
    """Station label includes station address (no price)."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    _, label = results[0]
    assert "CALLE MAYOR" in label or "REPSOL" in label


async def test_async_list_stations_excludes_stations_outside_radius() -> None:
    """async_list_stations excludes stations beyond the specified radius."""
    # Station is in Madrid (~40.4N, ~3.7W); search from Seville (~37.4N, ~6.0W)
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=37.3891, longitude=-5.9845)
    results = await provider.async_list_stations(
        session, lat=37.3891, lng=-5.9845, radius_km=10.0
    )

    # Madrid station is ~360 km from Seville — must not appear in 10 km radius
    assert len(results) == 0


async def test_async_list_stations_sorted_cheapest_diesel_first() -> None:
    """Stations are sorted cheapest diesel first."""
    cheap = {**_BASE_STATION, "IDEESS": "1001", "Precio Gasoleo A": "1,399"}
    expensive = {**_BASE_STATION, "IDEESS": "1002", "Precio Gasoleo A": "1,699"}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [expensive, cheap]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider("1001", latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert len(results) == 2
    assert results[0][0] == "1001"  # cheap diesel first
    assert results[1][0] == "1002"


async def test_async_list_stations_falls_back_to_constructor_coords() -> None:
    """async_list_stations uses constructor lat/lng when not passed as kwargs."""
    resp = _make_mock_response(200)
    session = _make_session(resp)

    provider = EsMineturProvider(
        _STATION_ID,
        latitude=40.416775,
        longitude=-3.703790,
        radius_km=5.0,
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 1


async def test_async_list_stations_returns_empty_when_no_lat_lng() -> None:
    """async_list_stations returns [] when no lat/lng is available."""
    session = MagicMock()
    session.get = MagicMock()

    provider = EsMineturProvider(_STATION_ID)
    results = await provider.async_list_stations(session)

    assert results == []
    session.get.assert_not_called()


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns empty list on network error (does not raise)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert results == []


async def test_async_list_stations_returns_empty_on_provider_error() -> None:
    """async_list_stations returns empty list when the API returns an error payload."""
    payload = {**_PAYLOAD, "ResultadoConsulta": "ERROR"}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert results == []


async def test_async_list_stations_skips_stations_with_missing_coords() -> None:
    """async_list_stations skips stations that have no lat/lng coordinates."""
    no_coords = {
        **_BASE_STATION,
        "IDEESS": "5555",
        "Latitud": "",
        "Longitud (WGS84)": "",
    }
    payload = {**_PAYLOAD, "ListaEESSPrecio": [_BASE_STATION, no_coords]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    # Only _BASE_STATION (which has coords) should appear
    ids = [r[0] for r in results]
    assert "5555" not in ids
    assert "4375" in ids


async def test_async_list_stations_skips_station_without_ideess() -> None:
    """async_list_stations skips stations that have no IDEESS identifier."""
    no_id = {**_BASE_STATION, "IDEESS": ""}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [no_id]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert results == []


async def test_async_list_stations_returns_empty_list_when_lista_absent() -> None:
    """async_list_stations returns [] when API payload has no ListaEESSPrecio."""
    payload = {"Fecha": _FECHA, "ResultadoConsulta": "OK"}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert results == []


async def test_async_list_stations_label_uses_unknown_brand_fallback() -> None:
    """Station label uses 'Unknown' when brand (Rótulo) is absent."""
    no_brand = {**_BASE_STATION, "Rótulo": ""}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [no_brand]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider(_STATION_ID, latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert len(results) == 1
    _, label = results[0]
    assert "Unknown" in label


async def test_async_list_stations_sorts_no_price_stations_last() -> None:
    """Stations with no diesel price sort after stations with a price."""
    with_price = {**_BASE_STATION, "IDEESS": "1001", "Precio Gasoleo A": "1,499"}
    no_price = {**_BASE_STATION, "IDEESS": "1002", "Precio Gasoleo A": ""}
    payload = {**_PAYLOAD, "ListaEESSPrecio": [no_price, with_price]}
    resp = _make_mock_response(200, body_bytes=_payload_bytes(payload))
    session = _make_session(resp)

    provider = EsMineturProvider("1001", latitude=40.416775, longitude=-3.703790)
    results = await provider.async_list_stations(
        session, lat=40.416775, lng=-3.703790, radius_km=5.0
    )

    assert len(results) == 2
    # Station with price comes first
    assert results[0][0] == "1001"


# ---------------------------------------------------------------------------
# _find_station
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the dict whose IDEESS matches station_id."""
    other = {**_BASE_STATION, "IDEESS": "9999", "Rótulo": "CEPSA"}
    result = _find_station([other, _BASE_STATION], "4375")
    assert result is not None
    assert result["IDEESS"] == "4375"
    assert result["Rótulo"] == "REPSOL"


def test_find_station_returns_none_when_absent() -> None:
    """_find_station returns None when no station has the given IDEESS."""
    result = _find_station([_BASE_STATION], "9999")
    assert result is None


def test_find_station_returns_none_on_empty_list() -> None:
    """_find_station returns None on an empty station list."""
    result = _find_station([], "4375")
    assert result is None


def test_find_station_strips_whitespace_from_ideess() -> None:
    """_find_station strips whitespace from IDEESS before comparing."""
    padded = {**_BASE_STATION, "IDEESS": "  4375  "}
    result = _find_station([padded], "4375")
    assert result is not None


# ---------------------------------------------------------------------------
# _parse_coord
# ---------------------------------------------------------------------------


def test_parse_coord_comma_decimal() -> None:
    """_parse_coord converts a Spanish-locale comma-decimal coordinate to float."""
    assert _parse_coord("40,416775") == pytest.approx(40.416775)


def test_parse_coord_negative_comma_decimal() -> None:
    """_parse_coord handles negative comma-decimal coordinates."""
    assert _parse_coord("-3,703790") == pytest.approx(-3.703790)


def test_parse_coord_none_returns_none() -> None:
    """_parse_coord returns None when input is None."""
    assert _parse_coord(None) is None


def test_parse_coord_empty_string_returns_none() -> None:
    """_parse_coord returns None when input is an empty string."""
    assert _parse_coord("") is None


def test_parse_coord_non_numeric_returns_none() -> None:
    """_parse_coord returns None when input cannot be parsed as a number."""
    assert _parse_coord("not-a-number") is None


def test_parse_coord_already_period_decimal() -> None:
    """_parse_coord also handles period-decimal strings (safe passthrough)."""
    assert _parse_coord("40.416775") == pytest.approx(40.416775)


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_comma_decimal() -> None:
    """_parse_price converts a Spanish-locale price string to EUR/litre float."""
    assert _parse_price("1,629") == pytest.approx(1.629)


def test_parse_price_returns_none_for_empty_string() -> None:
    """_parse_price returns None for empty string (no price published)."""
    assert _parse_price("") is None


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price returns None when input is None."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price returns None when price is zero (sentinel value)."""
    assert _parse_price("0,000") is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price returns None for negative values."""
    assert _parse_price("-1,500") is None


def test_parse_price_normalises_cents_to_eur_per_litre() -> None:
    """_parse_price divides values >10 by 100 (treats them as cents)."""
    result = _parse_price("162,9")
    assert result == pytest.approx(1.629)
    assert result is not None and result < 10


def test_parse_price_returns_none_for_non_numeric() -> None:
    """_parse_price returns None for non-numeric input."""
    assert _parse_price("N/A") is None


def test_parse_price_rounds_to_three_decimal_places() -> None:
    """_parse_price rounds result to 3 decimal places."""
    result = _parse_price("1,6291234")
    assert result is not None
    assert result == round(result, 3)


# ---------------------------------------------------------------------------
# _parse_station
# ---------------------------------------------------------------------------


def test_parse_station_returns_all_expected_keys() -> None:
    """_parse_station returns a dict containing all expected StationData keys."""
    result = _parse_station(_BASE_STATION, _FECHA)
    expected_keys = {
        "unleaded",
        "diesel",
        "premium_unleaded",
        "lpg",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
        "source_station_id",
    }
    for key in expected_keys:
        assert key in result, f"Key '{key}' missing from _parse_station output"


def test_parse_station_fuel_prices() -> None:
    """_parse_station correctly maps all four fuel price fields."""
    result = _parse_station(_BASE_STATION, _FECHA)
    assert result["unleaded"] == pytest.approx(1.629)
    assert result["diesel"] == pytest.approx(1.549)
    assert result["premium_unleaded"] == pytest.approx(1.789)
    assert result["lpg"] == pytest.approx(0.899)


def test_parse_station_name_equals_brand() -> None:
    """_parse_station sets name equal to brand (MINETUR has no distinct name field)."""
    result = _parse_station(_BASE_STATION, _FECHA)
    assert result["name"] == result["brand"]
    assert result["name"] == "REPSOL"


def test_parse_station_empty_rotulo_becomes_none() -> None:
    """_parse_station normalises empty Rótulo to None for brand and name."""
    result = _parse_station({**_BASE_STATION, "Rótulo": ""}, _FECHA)
    assert result["brand"] is None
    assert result["name"] is None


def test_parse_station_county_title_cased() -> None:
    """_parse_station title-cases the Provincia field."""
    result = _parse_station({**_BASE_STATION, "Provincia": "BARCELONA"}, _FECHA)
    assert result["county"] == "Barcelona"


def test_parse_station_empty_provincia_becomes_none() -> None:
    """_parse_station returns county=None when Provincia is empty."""
    result = _parse_station({**_BASE_STATION, "Provincia": ""}, _FECHA)
    assert result["county"] is None


def test_parse_station_source_station_id() -> None:
    """_parse_station stores IDEESS as source_station_id."""
    result = _parse_station(_BASE_STATION, _FECHA)
    assert result["source_station_id"] == "4375"


def test_parse_station_lastupdated_from_fecha() -> None:
    """_parse_station converts Fecha string to ISO 8601 timestamp."""
    result = _parse_station(_BASE_STATION, _FECHA)
    assert result["lastupdated"] == "2026-06-14T04:50:43"


def test_parse_station_lastupdated_none_when_fecha_absent() -> None:
    """_parse_station sets lastupdated=None when fecha is None."""
    result = _parse_station(_BASE_STATION, None)
    assert result["lastupdated"] is None


def test_parse_station_latitude_longitude_from_comma_strings() -> None:
    """_parse_station parses comma-decimal lat/lng correctly."""
    result = _parse_station(_BASE_STATION, _FECHA)
    assert result["latitude"] == pytest.approx(40.416775)
    assert result["longitude"] == pytest.approx(-3.703790)


def test_parse_station_empty_lat_becomes_none() -> None:
    """_parse_station returns latitude=None when Latitud is empty."""
    result = _parse_station({**_BASE_STATION, "Latitud": ""}, _FECHA)
    assert result["latitude"] is None


def test_parse_station_empty_lng_becomes_none() -> None:
    """_parse_station returns longitude=None when Longitud (WGS84) is empty."""
    result = _parse_station({**_BASE_STATION, "Longitud (WGS84)": ""}, _FECHA)
    assert result["longitude"] is None


def test_parse_station_uses_parenthetical_longitud_key() -> None:
    """_parse_station reads 'Longitud (WGS84)' — the exact key used in the live API."""
    # Provide only the correct key; the incorrect variant should not be read.
    station = {
        **_BASE_STATION,
        "Longitud (WGS84)": "-3,703790",
        "Longitud": "0",  # wrong key — must be ignored
    }
    result = _parse_station(station, _FECHA)
    assert result["longitude"] == pytest.approx(-3.703790)


# ---------------------------------------------------------------------------
# _normalise_fecha
# ---------------------------------------------------------------------------


def test_normalise_fecha_valid_timestamp() -> None:
    """_normalise_fecha converts 'DD/MM/YYYY H:MM:SS' to ISO 8601."""
    assert _normalise_fecha("14/06/2026 4:50:43") == "2026-06-14T04:50:43"


def test_normalise_fecha_zero_padded_hour() -> None:
    """_normalise_fecha handles single-digit hours correctly."""
    assert _normalise_fecha("01/01/2026 9:05:00") == "2026-01-01T09:05:00"


def test_normalise_fecha_midnight() -> None:
    """_normalise_fecha handles midnight (0:00:00) correctly."""
    assert _normalise_fecha("15/06/2026 0:00:00") == "2026-06-15T00:00:00"


def test_normalise_fecha_none_returns_none() -> None:
    """_normalise_fecha returns None when input is None."""
    assert _normalise_fecha(None) is None


def test_normalise_fecha_empty_string_returns_none() -> None:
    """_normalise_fecha returns None for empty string."""
    assert _normalise_fecha("") is None


def test_normalise_fecha_invalid_format_returns_raw() -> None:
    """_normalise_fecha returns the raw string when format is unrecognised."""
    raw = "2026-06-14 some-nonsense"
    result = _normalise_fecha(raw)
    assert result == raw


def test_normalise_fecha_strips_whitespace() -> None:
    """_normalise_fecha strips leading/trailing whitespace before parsing."""
    assert _normalise_fecha("  14/06/2026 4:50:43  ") == "2026-06-14T04:50:43"


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------


def test_haversine_km_same_point_is_zero() -> None:
    """_haversine_km returns 0.0 for identical origin and destination."""
    assert _haversine_km(40.416775, -3.703790, 40.416775, -3.703790) == pytest.approx(
        0.0
    )


def test_haversine_km_madrid_to_barcelona_approximately_correct() -> None:
    """_haversine_km returns ~504 km between Madrid and Barcelona."""
    # Madrid: 40.4168N, 3.7038W; Barcelona: 41.3851N, 2.1734E
    dist = _haversine_km(40.4168, -3.7038, 41.3851, 2.1734)
    assert 490 < dist < 520, f"Expected ~504 km, got {dist:.1f} km"


def test_haversine_km_returns_positive_float() -> None:
    """_haversine_km always returns a non-negative distance."""
    dist = _haversine_km(51.5074, -0.1278, 48.8566, 2.3522)  # London to Paris
    assert dist > 0


def test_haversine_km_is_symmetric() -> None:
    """_haversine_km(A→B) == _haversine_km(B→A)."""
    d1 = _haversine_km(40.4168, -3.7038, 41.3851, 2.1734)
    d2 = _haversine_km(41.3851, 2.1734, 40.4168, -3.7038)
    assert d1 == pytest.approx(d2)


# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------


def test_base_url_points_to_minetur() -> None:
    """The provider targets the MINETUR government API endpoint."""
    from custom_components.fuelcompare_ie.providers.es_minetur import _BASE_URL

    assert "sedeaplicaciones.minetur.gob.es" in _BASE_URL
    assert _BASE_URL.startswith("https://")


# ---------------------------------------------------------------------------
# es_minetur.py line 284 — label without address in async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_label_omits_address_when_absent() -> None:
    """Line 284: when address ('Dirección') is absent/empty, label uses '{brand} (#{id_suffix})' format."""
    import json

    station_no_addr = {
        **_BASE_STATION,
        "Dirección": "",
    }
    payload = {**_PAYLOAD, "ListaEESSPrecio": [station_no_addr]}
    resp = _make_mock_response(200, body_bytes=json.dumps(payload).encode())
    session = _make_session(resp)

    provider = EsMineturProvider("4375", latitude=40.416775, longitude=-3.703790)
    result = await provider.async_list_stations(session, lat=40.416775, lng=-3.703790)

    assert len(result) >= 1
    sid, label = result[0]
    # No comma before the short ID; label is brand + short ID only
    assert "(#" in label
    assert "REPSOL" in label
    # Address part should be absent (no ", " before "(#")
    assert ", " + "(#" not in label
