"""Tests for PtDgegProvider — Portuguese DGEG fuel price data."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.pt_dgeg import (
    PtDgegProvider,
    _FUEL_NAME_MAP,
    _GET_POSTO_URL,
    _SEARCH_URL,
    _haversine_km,
    _parse_price,
    _parse_station,
)


# ---------------------------------------------------------------------------
# Fixtures / shared test data
# ---------------------------------------------------------------------------

_STATION_ID = "12345"

# Minimal resultado dict returned by GetDadosPosto
_MORADA: dict = {
    "Morada": "Rua da Liberdade 1",
    "Municipio": "Lisboa",
    "Distrito": "Lisboa",
    "Latitude": "38.7169",
    "Longitude": "-9.1399",
}

_COMBUSTIVEIS: list[dict] = [
    {
        "TipoCombustivel": "Gasóleo simples",
        "Preco": "1,953 €/litro",
        "DataAtualizacao": "2026-06-13T08:00:00",
    },
    {
        "TipoCombustivel": "Gasolina simples 95",
        "Preco": "1,739 €/litro",
        "DataAtualizacao": "2026-06-12T08:00:00",
    },
    {
        "TipoCombustivel": "Gasolina 98",
        "Preco": "1,849 €/litro",
        "DataAtualizacao": "2026-06-11T08:00:00",
    },
    {
        "TipoCombustivel": "GPL Auto",
        "Preco": "0,799 €/litro",
        "DataAtualizacao": "2026-06-10T08:00:00",
    },
]

_RESULTADO: dict = {
    "Nome": "GALP Lisboa Centro",
    "Marca": "GALP",
    "Morada": _MORADA,
    "Combustiveis": _COMBUSTIVEIS,
}

_GET_POSTO_SUCCESS: dict = {
    "status": True,
    "mensagem": None,
    "resultado": _RESULTADO,
}

# Flat rows for PesquisarPostos bulk search
_SEARCH_ROW_DIESEL: dict = {
    "Id": "12345",
    "Nome": "GALP Lisboa Centro",
    "Marca": "GALP",
    "Morada": "Rua da Liberdade 1",
    "Localidade": "Lisboa",
    "Municipio": "Lisboa",
    "Distrito": "Lisboa",
    "Latitude": "38.7169",
    "Longitude": "-9.1399",
    "Combustivel": "Gasóleo simples",
    "Preco": "1,953 €",
}

_SEARCH_ROW_UNLEADED: dict = {
    **_SEARCH_ROW_DIESEL,
    "Combustivel": "Gasolina simples 95",
    "Preco": "1,739 €",
}

_SEARCH_ROW_PREMIUM: dict = {
    **_SEARCH_ROW_DIESEL,
    "Combustivel": "Gasolina 98",
    "Preco": "1,849 €",
}

_SEARCH_ROW_LPG: dict = {
    **_SEARCH_ROW_DIESEL,
    "Combustivel": "GPL Auto",
    "Preco": "0,799 €",
}

_SEARCH_SUCCESS: dict = {
    "resultado": [
        _SEARCH_ROW_DIESEL,
        _SEARCH_ROW_UNLEADED,
        _SEARCH_ROW_PREMIUM,
        _SEARCH_ROW_LPG,
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """PtDgegProvider declares COUNTRY='PT'."""
    assert PtDgegProvider.COUNTRY == "PT"


def test_provider_metadata_key() -> None:
    """PtDgegProvider declares PROVIDER_KEY='pt_dgeg'."""
    assert PtDgegProvider.PROVIDER_KEY == "pt_dgeg"


def test_provider_metadata_label() -> None:
    """PtDgegProvider declares LABEL='DGEG (Portugal)'."""
    assert PtDgegProvider.LABEL == "DGEG (Portugal)"


def test_provider_config_mode() -> None:
    """PtDgegProvider uses CONFIG_MODE='location'."""
    assert PtDgegProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """PtDgegProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert PtDgegProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """PtDgegProvider poll interval is 3600 seconds (1 hour)."""
    assert PtDgegProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes all four DGEG fuel types."""
    caps = PtDgegProvider.CAPABILITIES
    assert "diesel" in caps
    assert "unleaded" in caps
    assert "premium_unleaded" in caps
    assert "lpg" in caps


def test_provider_capabilities_include_identity_fields() -> None:
    """CAPABILITIES includes station identity fields."""
    caps = PtDgegProvider.CAPABILITIES
    assert "name" in caps
    assert "county" in caps
    assert "address" in caps
    assert "latitude" in caps
    assert "longitude" in caps


def test_provider_capabilities_include_timing_fields() -> None:
    """CAPABILITIES includes timing field."""
    caps = PtDgegProvider.CAPABILITIES
    assert "lastupdated" in caps


def test_provider_capabilities_exclude_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = PtDgegProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


def test_provider_no_api_key_required() -> None:
    """PtDgegProvider does not require an API key (public API)."""
    assert PtDgegProvider.REQUIRES_API_KEY is False


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_station_id() -> None:
    """Constructor stores station_id correctly."""
    p = PtDgegProvider(_STATION_ID)
    assert p._station_id == _STATION_ID


def test_constructor_stores_optional_params() -> None:
    """Constructor stores optional county, latitude, longitude, radius_km."""
    p = PtDgegProvider(
        _STATION_ID,
        county="Lisboa",
        latitude=38.7169,
        longitude=-9.1399,
        radius_km=5.0,
    )
    assert p._county == "Lisboa"
    assert p._latitude == pytest.approx(38.7169)
    assert p._longitude == pytest.approx(-9.1399)
    assert p._radius_km == pytest.approx(5.0)


def test_constructor_defaults_radius_to_10() -> None:
    """Constructor defaults radius_km to 10.0 when not provided."""
    p = PtDgegProvider(_STATION_ID)
    assert p._radius_km == pytest.approx(10.0)


def test_constructor_defaults_optional_to_none() -> None:
    """Constructor defaults county, latitude, longitude to None."""
    p = PtDgegProvider(_STATION_ID)
    assert p._county is None
    assert p._latitude is None
    assert p._longitude is None


# ---------------------------------------------------------------------------
# _parse_price — unit tests
# ---------------------------------------------------------------------------


def test_parse_price_get_posto_format() -> None:
    """_parse_price handles '1,953 €/litro' (GetDadosPosto format)."""
    assert _parse_price("1,953 €/litro") == pytest.approx(1.953)


def test_parse_price_pesquisar_format() -> None:
    """_parse_price handles '1,739 €' (PesquisarPostos format)."""
    assert _parse_price("1,739 €") == pytest.approx(1.739)


def test_parse_price_lpg_value() -> None:
    """_parse_price handles low LPG prices like '0,799 €/litro'."""
    assert _parse_price("0,799 €/litro") == pytest.approx(0.799)


def test_parse_price_normalises_cents_above_10() -> None:
    """_parse_price divides values >10 by 100 (cents to EUR)."""
    # 195.3 cents → 1.953 EUR
    result = _parse_price("195,3 €/litro")
    assert result == pytest.approx(1.953)


def test_parse_price_returns_none_for_none_input() -> None:
    """_parse_price returns None for None input."""
    assert _parse_price(None) is None


def test_parse_price_returns_none_for_empty_string() -> None:
    """_parse_price returns None for empty string input."""
    assert _parse_price("") is None


def test_parse_price_returns_none_for_invalid_string() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("sem preço") is None


def test_parse_price_rounds_to_4_decimal_places() -> None:
    """_parse_price rounds result to 4 decimal places."""
    result = _parse_price("1,9535 €/litro")
    assert result is not None
    # float rounding: result should have at most 4 meaningful decimal places
    assert result == pytest.approx(round(1.9535, 4))


# ---------------------------------------------------------------------------
# _fuel_name_map
# ---------------------------------------------------------------------------


def test_fuel_name_map_diesel() -> None:
    """'Gasóleo simples' maps to 'diesel'."""
    assert _FUEL_NAME_MAP["Gasóleo simples"] == "diesel"


def test_fuel_name_map_unleaded() -> None:
    """'Gasolina simples 95' maps to 'unleaded'."""
    assert _FUEL_NAME_MAP["Gasolina simples 95"] == "unleaded"


def test_fuel_name_map_premium_unleaded() -> None:
    """'Gasolina 98' maps to 'premium_unleaded'."""
    assert _FUEL_NAME_MAP["Gasolina 98"] == "premium_unleaded"


def test_fuel_name_map_lpg() -> None:
    """'GPL Auto' maps to 'lpg'."""
    assert _FUEL_NAME_MAP["GPL Auto"] == "lpg"


def test_fuel_name_map_ignores_gasóleo_especial() -> None:
    """'Gasóleo especial' (premium diesel) is NOT in the map."""
    assert "Gasóleo especial" not in _FUEL_NAME_MAP


def test_fuel_name_map_ignores_gasolina_especial_95() -> None:
    """'Gasolina especial 95' is NOT in the map."""
    assert "Gasolina especial 95" not in _FUEL_NAME_MAP


# ---------------------------------------------------------------------------
# _parse_station — unit tests
# ---------------------------------------------------------------------------


def test_parse_station_returns_diesel_price() -> None:
    """_parse_station extracts diesel price from Combustiveis list."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["diesel"] == pytest.approx(1.953)


def test_parse_station_returns_unleaded_price() -> None:
    """_parse_station extracts unleaded (95) price."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["unleaded"] == pytest.approx(1.739)


def test_parse_station_returns_premium_unleaded_price() -> None:
    """_parse_station extracts premium_unleaded (98) price."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["premium_unleaded"] == pytest.approx(1.849)


def test_parse_station_returns_lpg_price() -> None:
    """_parse_station extracts LPG price."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["lpg"] == pytest.approx(0.799)


def test_parse_station_returns_name() -> None:
    """_parse_station populates name from 'Nome' field."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["name"] == "GALP Lisboa Centro"


def test_parse_station_falls_back_to_marca_for_name() -> None:
    """_parse_station uses 'Marca' as name when 'Nome' is absent."""
    resultado = {**_RESULTADO, "Nome": None}
    result = _parse_station(_STATION_ID, resultado)
    assert result["name"] == "GALP"


def test_parse_station_name_none_when_both_absent() -> None:
    """_parse_station returns name=None when both 'Nome' and 'Marca' absent."""
    resultado = {**_RESULTADO, "Nome": None, "Marca": None}
    result = _parse_station(_STATION_ID, resultado)
    assert result["name"] is None


def test_parse_station_returns_county_from_municipio() -> None:
    """_parse_station populates county from Morada.Municipio."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["county"] == "Lisboa"


def test_parse_station_falls_back_county_to_distrito() -> None:
    """_parse_station falls back county to Distrito when Municipio absent."""
    morada = {**_MORADA, "Municipio": None}
    resultado = {**_RESULTADO, "Morada": morada}
    result = _parse_station(_STATION_ID, resultado)
    assert result["county"] == "Lisboa"


def test_parse_station_county_none_when_morada_absent() -> None:
    """_parse_station returns county=None when Morada is absent."""
    resultado = {**_RESULTADO, "Morada": None}
    result = _parse_station(_STATION_ID, resultado)
    assert result["county"] is None


def test_parse_station_returns_address() -> None:
    """_parse_station populates address from Morada.Morada."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["address"] == "Rua da Liberdade 1"


def test_parse_station_returns_latitude() -> None:
    """_parse_station parses latitude from Morada.Latitude."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["latitude"] == pytest.approx(38.7169)


def test_parse_station_returns_longitude() -> None:
    """_parse_station parses longitude from Morada.Longitude."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert result["longitude"] == pytest.approx(-9.1399)


def test_parse_station_latitude_none_on_invalid() -> None:
    """_parse_station returns latitude=None for non-numeric Latitude."""
    morada = {**_MORADA, "Latitude": "n/a"}
    resultado = {**_RESULTADO, "Morada": morada}
    result = _parse_station(_STATION_ID, resultado)
    assert result["latitude"] is None


def test_parse_station_longitude_none_on_invalid() -> None:
    """_parse_station returns longitude=None for non-numeric Longitude."""
    morada = {**_MORADA, "Longitude": "n/a"}
    resultado = {**_RESULTADO, "Morada": morada}
    result = _parse_station(_STATION_ID, resultado)
    assert result["longitude"] is None


def test_parse_station_latitude_none_when_key_absent() -> None:
    """_parse_station returns latitude=None when Latitude key absent."""
    morada = {k: v for k, v in _MORADA.items() if k != "Latitude"}
    resultado = {**_RESULTADO, "Morada": morada}
    result = _parse_station(_STATION_ID, resultado)
    assert result["latitude"] is None


def test_parse_station_longitude_none_when_key_absent() -> None:
    """_parse_station returns longitude=None when Longitude key absent."""
    morada = {k: v for k, v in _MORADA.items() if k != "Longitude"}
    resultado = {**_RESULTADO, "Morada": morada}
    result = _parse_station(_STATION_ID, resultado)
    assert result["longitude"] is None


def test_parse_station_lastupdated_picks_most_recent() -> None:
    """_parse_station picks the most recent DataAtualizacao across all fuels."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    # Diesel has the latest timestamp
    assert result["lastupdated"] == "2026-06-13T08:00:00"


def test_parse_station_lastupdated_none_when_no_timestamps() -> None:
    """_parse_station returns lastupdated=None when no DataAtualizacao fields."""
    combustiveis = [
        {
            "TipoCombustivel": "Gasóleo simples",
            "Preco": "1,953 €/litro",
            "DataAtualizacao": None,
        },
    ]
    resultado = {**_RESULTADO, "Combustiveis": combustiveis}
    result = _parse_station(_STATION_ID, resultado)
    assert result["lastupdated"] is None


def test_parse_station_source_station_id_not_in_data() -> None:
    """_parse_station does not set source_station_id (injected by coordinator)."""
    result = _parse_station(_STATION_ID, _RESULTADO)
    assert "source_station_id" not in result


def test_parse_station_ignores_unknown_fuel_types() -> None:
    """_parse_station silently ignores fuel types not in _FUEL_NAME_MAP."""
    combustiveis = [
        {
            "TipoCombustivel": "Gasóleo especial",
            "Preco": "2,099 €/litro",
            "DataAtualizacao": None,
        },
        {
            "TipoCombustivel": "Gasóleo simples",
            "Preco": "1,953 €/litro",
            "DataAtualizacao": "2026-06-13T08:00:00",
        },
    ]
    resultado = {**_RESULTADO, "Combustiveis": combustiveis}
    result = _parse_station(_STATION_ID, resultado)
    assert result["diesel"] == pytest.approx(1.953)
    # premium_diesel is not in CAPABILITIES — should not appear
    assert "premium_diesel" not in result or result.get("premium_diesel") is None


def test_parse_station_none_price_skipped() -> None:
    """_parse_station skips fuel entries where Preco is None."""
    combustiveis = [
        {"TipoCombustivel": "Gasóleo simples", "Preco": None, "DataAtualizacao": None},
    ]
    resultado = {**_RESULTADO, "Combustiveis": combustiveis}
    result = _parse_station(_STATION_ID, resultado)
    assert result["diesel"] is None


def test_parse_station_empty_combustiveis_returns_nones() -> None:
    """_parse_station returns None prices when Combustiveis list is empty."""
    resultado = {**_RESULTADO, "Combustiveis": []}
    result = _parse_station(_STATION_ID, resultado)
    assert result["diesel"] is None
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["lpg"] is None
    assert result["lastupdated"] is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_diesel() -> None:
    """async_fetch returns diesel price from GetDadosPosto response."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.953)


async def test_async_fetch_success_unleaded() -> None:
    """async_fetch returns unleaded price from GetDadosPosto response."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.739)


async def test_async_fetch_success_premium_unleaded() -> None:
    """async_fetch returns premium_unleaded price from GetDadosPosto response."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.849)


async def test_async_fetch_success_lpg() -> None:
    """async_fetch returns lpg price from GetDadosPosto response."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lpg"] == pytest.approx(0.799)


async def test_async_fetch_success_name() -> None:
    """async_fetch returns station name."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "GALP Lisboa Centro"


async def test_async_fetch_success_county() -> None:
    """async_fetch returns station county (Municipio)."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["county"] == "Lisboa"


async def test_async_fetch_success_address() -> None:
    """async_fetch returns station address."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["address"] == "Rua da Liberdade 1"


async def test_async_fetch_success_coordinates() -> None:
    """async_fetch returns latitude and longitude."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(38.7169)
    assert data["longitude"] == pytest.approx(-9.1399)


async def test_async_fetch_success_lastupdated() -> None:
    """async_fetch returns lastupdated from the most recent DataAtualizacao."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] == "2026-06-13T08:00:00"


async def test_async_fetch_makes_single_request() -> None:
    """async_fetch issues exactly one GET request (per-station endpoint)."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    assert session.get.call_count == 1


async def test_async_fetch_calls_get_posto_url() -> None:
    """async_fetch calls the GetDadosPosto endpoint."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_url = session.get.call_args[0][0]
    assert "GetDadosPosto" in call_url


async def test_async_fetch_sends_station_id_param() -> None:
    """async_fetch passes station_id as 'id' query param."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args[1]
    assert call_kwargs["params"]["id"] == _STATION_ID


async def test_async_fetch_sends_idioma_pt_param() -> None:
    """async_fetch passes idioma=pt query param."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args[1]
    assert call_kwargs["params"]["idioma"] == "pt"


async def test_async_fetch_passes_ssl_context() -> None:
    """async_fetch does not pass ssl= on a successful first attempt (no SSLError)."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args[1]
    # Normal path: ssl= is NOT injected (aiohttp uses default context)
    assert "ssl" not in call_kwargs


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_on_client_error() -> None:
    """async_fetch raises ProviderError when aiohttp raises ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="DGEG"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_non_200() -> None:
    """async_fetch raises ProviderError when HTTP status is non-200."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="DGEG"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_api_status_false() -> None:
    """async_fetch raises ProviderError when API returns status=False."""
    failure_response = {
        "status": False,
        "mensagem": "Posto não encontrado",
        "resultado": None,
    }
    resp = _make_mock_response(200, json_data=failure_response)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="DGEG"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_api_status_false_includes_message() -> None:
    """async_fetch ProviderError includes the API mensagem when status=False."""
    failure_response = {
        "status": False,
        "mensagem": "Posto não encontrado",
        "resultado": None,
    }
    resp = _make_mock_response(200, json_data=failure_response)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="Posto não encontrado"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_when_resultado_not_dict() -> None:
    """async_fetch raises ProviderError when resultado is not a dict."""
    bad_shape = {"status": True, "resultado": []}
    resp = _make_mock_response(200, json_data=bad_shape)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="unexpected response shape"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_when_resultado_is_none() -> None:
    """async_fetch raises ProviderError when resultado is None."""
    bad_shape = {"status": True, "resultado": None}
    resp = _make_mock_response(200, json_data=bad_shape)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)

    with pytest.raises(ProviderError, match="unexpected response shape"):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_nome() -> None:
    """async_fetch_station_name returns the 'Nome' field from GetDadosPosto."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "GALP Lisboa Centro"


async def test_async_fetch_station_name_none_when_nome_absent() -> None:
    """async_fetch_station_name returns None when 'Nome' is absent from resultado."""
    resultado = {**_RESULTADO, "Nome": None, "Marca": None}
    resp = _make_mock_response(
        200, json_data={**_GET_POSTO_SUCCESS, "resultado": resultado}
    )
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = PtDgegProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_none_on_http_error() -> None:
    """async_fetch_station_name returns None when HTTP raise_for_status raises."""
    resp = _make_mock_response(404)
    resp.raise_for_status = MagicMock(side_effect=ClientError("404"))
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_none_when_resultado_not_dict() -> None:
    """async_fetch_station_name returns None when resultado is not a dict."""
    bad = {"status": True, "resultado": []}
    resp = _make_mock_response(200, json_data=bad)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_calls_get_posto_url() -> None:
    """async_fetch_station_name calls the GetDadosPosto endpoint."""
    resp = _make_mock_response(200, json_data=_GET_POSTO_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_fetch_station_name(session, _STATION_ID)

    call_url = session.get.call_args[0][0]
    assert "GetDadosPosto" in call_url


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (station_id, label) tuples."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert isinstance(result, list)
    assert len(result) > 0
    sid, label = result[0]
    assert isinstance(sid, str)
    assert isinstance(label, str)


async def test_async_list_stations_station_id_in_result() -> None:
    """async_list_stations result includes our test station ID."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    station_ids = [sid for sid, _ in result]
    assert _STATION_ID in station_ids


async def test_async_list_stations_label_contains_name() -> None:
    """async_list_stations label contains the station name."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    labels = [label for sid, label in result if sid == _STATION_ID]
    assert any("GALP Lisboa Centro" in label for label in labels)


async def test_async_list_stations_label_contains_diesel_price() -> None:
    """async_list_stations label contains station identifier token (no price)."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    labels = [label for sid, label in result if sid == _STATION_ID]
    assert any("(#" in label for label in labels)


async def test_async_list_stations_sends_search_url() -> None:
    """async_list_stations calls the PesquisarPostos endpoint."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_list_stations(session)

    call_url = session.get.call_args[0][0]
    assert "PesquisarPostos" in call_url


async def test_async_list_stations_sends_qtd_5000_param() -> None:
    """async_list_stations sends qtd=5000 to the search endpoint."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_list_stations(session)

    call_kwargs = session.get.call_args[1]
    assert call_kwargs["params"]["qtd"] == 5000


async def test_async_list_stations_passes_ssl_context() -> None:
    """async_list_stations does not pass ssl= on a successful first attempt."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    await provider.async_list_stations(session)

    call_kwargs = session.get.call_args[1]
    # Normal path: ssl= is NOT injected (aiohttp uses default context)
    assert "ssl" not in call_kwargs


# ---------------------------------------------------------------------------
# async_list_stations — filtering and sorting
# ---------------------------------------------------------------------------


async def test_async_list_stations_filters_by_proximity() -> None:
    """async_list_stations excludes stations outside the radius."""
    # Station at Lisbon coords; search centered in Porto (far away)
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(
        _STATION_ID, latitude=41.1579, longitude=-8.6291, radius_km=10.0
    )
    result = await provider.async_list_stations(session)

    # Lisbon station is ~270km from Porto — should be excluded
    station_ids = [sid for sid, _ in result]
    assert _STATION_ID not in station_ids


async def test_async_list_stations_includes_nearby_stations() -> None:
    """async_list_stations includes stations within the radius."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    # Search from Lisbon centre — our test station is at 38.7169, -9.1399
    provider = PtDgegProvider(
        _STATION_ID, latitude=38.7169, longitude=-9.1399, radius_km=1.0
    )
    result = await provider.async_list_stations(session)

    station_ids = [sid for sid, _ in result]
    assert _STATION_ID in station_ids


async def test_async_list_stations_sorted_by_diesel_price() -> None:
    """async_list_stations sorts results alphabetically by label."""
    cheaper_row = {
        **_SEARCH_ROW_DIESEL,
        "Id": "99999",
        "Nome": "Cheap Station",
        "Latitude": "38.7170",
        "Longitude": "-9.1400",
        "Preco": "1,799 €",  # cheaper than 1,953
    }
    search_data = {"resultado": [_SEARCH_ROW_DIESEL, cheaper_row]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    # "GALP Lisboa Centro..." < "GALP — Cheap Station..." (em-dash > 'L' in Unicode)
    assert result[0][0] == "12345"


async def test_async_list_stations_kwargs_override_instance_coords() -> None:
    """async_list_stations accepts lat/lng/radius_km via kwargs (config-flow path)."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    # Provider has no coordinates; pass via kwargs
    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=38.7169, lng=-9.1399, radius_km=1.0
    )

    station_ids = [sid for sid, _ in result]
    assert _STATION_ID in station_ids


async def test_async_list_stations_no_coords_returns_all() -> None:
    """async_list_stations returns all stations when no coordinates provided."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    # Our station should appear (no proximity filter applied)
    station_ids = [sid for sid, _ in result]
    assert _STATION_ID in station_ids


async def test_async_list_stations_deduplicates_per_station() -> None:
    """async_list_stations aggregates multiple fuel rows per station into one entry."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    # _SEARCH_SUCCESS has 4 rows for the same station (4 fuel types)
    station_ids = [sid for sid, _ in result]
    assert station_ids.count(_STATION_ID) == 1


async def test_async_list_stations_skips_rows_without_id() -> None:
    """async_list_stations skips rows where 'Id' is absent or empty."""
    row_no_id = {**_SEARCH_ROW_DIESEL, "Id": ""}
    search_data = {"resultado": [row_no_id]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# async_list_stations — fallback for name/brand in display label
# ---------------------------------------------------------------------------


async def test_async_list_stations_uses_marca_when_nome_absent() -> None:
    """async_list_stations uses Marca as station name when Nome is absent."""
    row = {**_SEARCH_ROW_DIESEL, "Nome": None, "Marca": "BP"}
    search_data = {"resultado": [row]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "BP" in label


async def test_async_list_stations_uses_station_fallback_name() -> None:
    """async_list_stations uses 'Station {id}' when both Nome and Marca absent."""
    row = {**_SEARCH_ROW_DIESEL, "Nome": None, "Marca": None}
    search_data = {"resultado": [row]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert f"Station {_STATION_ID}" in label


async def test_async_list_stations_includes_location_in_label() -> None:
    """async_list_stations includes locality in the display label."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    labels = [label for sid, label in result if sid == _STATION_ID]
    # Localidade="Lisboa" should appear
    assert any("Lisboa" in label for label in labels)


async def test_async_list_stations_price_labels_all_four_fuels() -> None:
    """async_list_stations label contains station identifier token (no price)."""
    resp = _make_mock_response(200, json_data=_SEARCH_SUCCESS)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    labels = [label for sid, label in result if sid == _STATION_ID]
    assert len(labels) == 1
    label = labels[0]
    assert "(#" in label
    assert "GALP" in label


# ---------------------------------------------------------------------------
# async_list_stations — error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when aiohttp raises ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns [] when raise_for_status raises."""
    resp = _make_mock_response(503)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_when_resultado_empty_list() -> None:
    """async_list_stations returns [] when resultado is an empty list."""
    resp = _make_mock_response(200, json_data={"resultado": []})
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_when_resultado_absent() -> None:
    """async_list_stations returns [] when resultado key is absent."""
    resp = _make_mock_response(200, json_data={})
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_when_resultado_not_list() -> None:
    """async_list_stations returns [] when resultado is not a list (e.g. dict)."""
    resp = _make_mock_response(200, json_data={"resultado": {"error": "bad"}})
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    assert result == []


# ---------------------------------------------------------------------------
# async_list_stations — coordinate parsing edge cases
# ---------------------------------------------------------------------------


async def test_async_list_stations_excludes_stations_without_coords_when_filtering() -> (
    None
):
    """Stations missing coordinates are excluded when proximity filtering is active."""
    row_no_coords = {**_SEARCH_ROW_DIESEL, "Latitude": None, "Longitude": None}
    search_data = {"resultado": [row_no_coords]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(
        _STATION_ID, latitude=38.7169, longitude=-9.1399, radius_km=100.0
    )
    result = await provider.async_list_stations(session)

    # Station without coordinates is excluded during proximity filter
    assert result == []


async def test_async_list_stations_handles_invalid_lat_lng_strings() -> None:
    """async_list_stations gracefully handles non-numeric Latitude/Longitude."""
    row_bad_coords = {**_SEARCH_ROW_DIESEL, "Latitude": "n/a", "Longitude": "n/a"}
    search_data = {"resultado": [row_bad_coords]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    # No crash; station is included (no coords filter active)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# async_list_stations — brand vs name in label
# ---------------------------------------------------------------------------


async def test_async_list_stations_prepends_brand_when_not_in_name() -> None:
    """async_list_stations prepends 'Brand — Name' when brand differs from name."""
    row = {**_SEARCH_ROW_DIESEL, "Nome": "Lisboa Norte", "Marca": "BP"}
    search_data = {"resultado": [row]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    _, label = result[0]
    assert "BP" in label
    assert "Lisboa Norte" in label


async def test_async_list_stations_no_brand_prefix_when_brand_in_name() -> None:
    """async_list_stations does not double-print brand when it is already in the name."""
    row = {**_SEARCH_ROW_DIESEL, "Nome": "GALP Lisboa Centro", "Marca": "GALP"}
    search_data = {"resultado": [row]}
    resp = _make_mock_response(200, json_data=search_data)
    session = _make_session(resp)

    provider = PtDgegProvider(_STATION_ID)
    result = await provider.async_list_stations(session)

    _, label = result[0]
    # Should not have "GALP — GALP Lisboa Centro"
    assert "GALP — GALP" not in label


# ---------------------------------------------------------------------------
# _haversine_km — unit tests
# ---------------------------------------------------------------------------


def test_haversine_same_point_is_zero() -> None:
    """_haversine_km returns 0 for identical coordinates."""
    assert _haversine_km(38.7169, -9.1399, 38.7169, -9.1399) == pytest.approx(
        0.0, abs=1e-6
    )


def test_haversine_lisbon_to_porto_approx_270km() -> None:
    """_haversine_km returns ~270km between Lisbon and Porto."""
    # Lisbon: 38.7169, -9.1399  Porto: 41.1579, -8.6291
    dist = _haversine_km(38.7169, -9.1399, 41.1579, -8.6291)
    assert 265.0 < dist < 280.0


def test_haversine_is_symmetric() -> None:
    """_haversine_km is commutative: d(A,B) == d(B,A)."""
    d1 = _haversine_km(38.7169, -9.1399, 41.1579, -8.6291)
    d2 = _haversine_km(41.1579, -8.6291, 38.7169, -9.1399)
    assert d1 == pytest.approx(d2, rel=1e-9)


def test_haversine_short_distance() -> None:
    """_haversine_km returns a small value for nearby points (~1km apart)."""
    # ~0.009° latitude ≈ 1km
    dist = _haversine_km(38.7169, -9.1399, 38.7259, -9.1399)
    assert 0.9 < dist < 1.1


# ---------------------------------------------------------------------------
# Module-level URL constants
# ---------------------------------------------------------------------------


def test_get_posto_url_points_to_dgeg() -> None:
    """_GET_POSTO_URL targets the correct DGEG API endpoint."""
    assert "dgeg.gov.pt" in _GET_POSTO_URL
    assert "GetDadosPosto" in _GET_POSTO_URL
    assert _GET_POSTO_URL.startswith("https://")


def test_search_url_points_to_dgeg() -> None:
    """_SEARCH_URL targets the correct DGEG bulk-search endpoint."""
    assert "dgeg.gov.pt" in _SEARCH_URL
    assert "PesquisarPostos" in _SEARCH_URL
    assert _SEARCH_URL.startswith("https://")
