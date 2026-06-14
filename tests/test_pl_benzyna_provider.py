"""Tests for PlBenzynaProvider (ORLEN wholesale prices, Poland)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.pl_benzyna import (
    PlBenzynaProvider,
    _find_product_price,
    _parse_price_pln_1000l,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# ORLEN confirmed live on 2026-06-13: Pb95=5228, Pb98=5739, ONEkodiesel=5597
_WHOLESALE_PAYLOAD: list[dict] = [
    {"productName": "Pb95", "value": 5228, "effectiveDate": "2026-06-13"},
    {"productName": "Pb98", "value": 5739, "effectiveDate": "2026-06-13"},
    {"productName": "ONEkodiesel", "value": 5597, "effectiveDate": "2026-06-13"},
    {"productName": "ONArctic2", "value": 5720, "effectiveDate": "2026-06-13"},
    {"productName": "OnEkoterm", "value": 4100, "effectiveDate": "2026-06-13"},
    {"productName": "BIO100", "value": 6200, "effectiveDate": "2026-06-13"},
    {"productName": "JETA1", "value": 7100, "effectiveDate": "2026-06-13"},
    {"productName": "AVGAS100LL", "value": 8900, "effectiveDate": "2026-06-13"},
]

_AUTOGAS_PAYLOAD: list[dict] = [
    {"voivodeship": "Mazowieckie", "value": 2.800, "effectiveDate": "2026-06-13"},
    {"voivodeship": "Małopolskie", "value": 2.750, "effectiveDate": "2026-06-13"},
    {"voivodeship": "Śląskie", "value": 2.790, "effectiveDate": "2026-06-13"},
]

_LAT = 52.2297
_LNG = 21.0122


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
    mock_resp.json = AsyncMock(return_value=json_data or [])

    if status >= 400:
        mock_resp.raise_for_status = MagicMock(
            side_effect=ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=status,
                message=f"HTTP {status}",
            )
        )
    else:
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


def _make_provider(
    latitude: float | None = _LAT,
    longitude: float | None = _LNG,
    radius_km: float = 10.0,
) -> PlBenzynaProvider:
    return PlBenzynaProvider(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """PlBenzynaProvider declares COUNTRY='PL'."""
    assert PlBenzynaProvider.COUNTRY == "PL"


def test_provider_key() -> None:
    """PlBenzynaProvider declares PROVIDER_KEY='pl_benzyna'."""
    assert PlBenzynaProvider.PROVIDER_KEY == "pl_benzyna"


def test_provider_label_contains_poland() -> None:
    """PlBenzynaProvider label mentions Poland."""
    assert "Poland" in PlBenzynaProvider.LABEL


def test_provider_label_contains_orlen() -> None:
    """PlBenzynaProvider label mentions ORLEN."""
    assert "ORLEN" in PlBenzynaProvider.LABEL


def test_provider_config_mode_is_location() -> None:
    """PlBenzynaProvider uses CONFIG_MODE='location'."""
    assert PlBenzynaProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """PlBenzynaProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert PlBenzynaProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_does_not_require_api_key() -> None:
    """PlBenzynaProvider does not require an API key."""
    assert PlBenzynaProvider.REQUIRES_API_KEY is False


def test_provider_poll_interval_is_daily() -> None:
    """POLL_INTERVAL_SECONDS is 86400 (daily) since ORLEN prices update at most once per day."""
    assert PlBenzynaProvider.POLL_INTERVAL_SECONDS == 86400


def test_provider_capabilities_include_unleaded() -> None:
    """CAPABILITIES includes 'unleaded' (Pb95)."""
    assert "unleaded" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_diesel() -> None:
    """CAPABILITIES includes 'diesel' (ONEkodiesel)."""
    assert "diesel" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_lpg() -> None:
    """CAPABILITIES includes 'lpg'."""
    assert "lpg" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_premium_unleaded() -> None:
    """CAPABILITIES includes 'premium_unleaded' (Pb98)."""
    assert "premium_unleaded" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_premium_diesel() -> None:
    """CAPABILITIES includes 'premium_diesel' (ONArctic2)."""
    assert "premium_diesel" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_kerosene() -> None:
    """CAPABILITIES includes 'kerosene' (OnEkoterm)."""
    assert "kerosene" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_e85() -> None:
    """CAPABILITIES includes 'e85' (BIO100)."""
    assert "e85" in PlBenzynaProvider.CAPABILITIES


def test_provider_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = PlBenzynaProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


def test_provider_capabilities_include_lastupdated() -> None:
    """CAPABILITIES includes 'lastupdated'."""
    assert "lastupdated" in PlBenzynaProvider.CAPABILITIES


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_station_id_is_always_pl() -> None:
    """Constructor always sets station_id to 'PL' regardless of argument."""
    provider = PlBenzynaProvider(station_id="whatever")
    assert provider._station_id == "PL"


def test_constructor_stores_coordinates() -> None:
    """Constructor stores latitude and longitude."""
    provider = _make_provider(latitude=_LAT, longitude=_LNG)
    assert provider._latitude == pytest.approx(_LAT)
    assert provider._longitude == pytest.approx(_LNG)


def test_constructor_stores_radius_km() -> None:
    """Constructor stores radius_km."""
    provider = _make_provider(radius_km=25.0)
    assert provider._radius_km == pytest.approx(25.0)


def test_constructor_radius_defaults_to_ten() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = PlBenzynaProvider()
    assert provider._radius_km == pytest.approx(10.0)


def test_constructor_coordinates_default_to_none() -> None:
    """Constructor defaults lat/lng to None when not supplied."""
    provider = PlBenzynaProvider()
    assert provider._latitude is None
    assert provider._longitude is None


# ---------------------------------------------------------------------------
# _parse_price_pln_1000l
# ---------------------------------------------------------------------------


def test_parse_price_converts_1000l_to_per_litre() -> None:
    """_parse_price_pln_1000l divides PLN/1000L by 1000 to get PLN/litre."""
    result = _parse_price_pln_1000l(5228)
    assert result == pytest.approx(5.228, abs=1e-4)


def test_parse_price_rounds_to_four_decimals() -> None:
    """_parse_price_pln_1000l rounds result to 4 decimal places."""
    result = _parse_price_pln_1000l(5228)
    assert result is not None
    assert len(str(result).split(".")[-1]) <= 4


def test_parse_price_returns_none_for_none() -> None:
    """_parse_price_pln_1000l returns None for None input."""
    assert _parse_price_pln_1000l(None) is None


def test_parse_price_returns_none_for_zero() -> None:
    """_parse_price_pln_1000l returns None for zero."""
    assert _parse_price_pln_1000l(0) is None


def test_parse_price_returns_none_for_negative() -> None:
    """_parse_price_pln_1000l returns None for a negative value."""
    assert _parse_price_pln_1000l(-100) is None


def test_parse_price_returns_none_for_non_numeric_string() -> None:
    """_parse_price_pln_1000l returns None for a non-numeric string."""
    assert _parse_price_pln_1000l("n/a") is None


def test_parse_price_accepts_float_string() -> None:
    """_parse_price_pln_1000l accepts a numeric string."""
    result = _parse_price_pln_1000l("5739.0")
    assert result == pytest.approx(5.739, abs=1e-4)


def test_parse_price_accepts_float_value() -> None:
    """_parse_price_pln_1000l accepts a float value."""
    result = _parse_price_pln_1000l(5597.5)
    assert result is not None
    assert result > 0


# ---------------------------------------------------------------------------
# _find_product_price
# ---------------------------------------------------------------------------


def test_find_product_price_returns_price_for_existing_product() -> None:
    """_find_product_price returns the raw price for a known productCode."""
    assert _find_product_price(_WHOLESALE_PAYLOAD, "Pb95") == 5228


def test_find_product_price_returns_none_for_unknown_product() -> None:
    """_find_product_price returns None for a productCode not in the list."""
    assert _find_product_price(_WHOLESALE_PAYLOAD, "Hydrogen") is None


def test_find_product_price_returns_none_for_empty_list() -> None:
    """_find_product_price returns None for an empty list."""
    assert _find_product_price([], "Pb95") is None


def test_find_product_price_returns_first_match() -> None:
    """_find_product_price returns the price from the first matching record."""
    data = [
        {"productName": "Pb95", "value": 5228},
        {"productName": "Pb95", "value": 9999},
    ]
    assert _find_product_price(data, "Pb95") == 5228


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_returns_station_data() -> None:
    """async_fetch returns a StationData dict on a successful API response."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_pb95_price() -> None:
    """async_fetch returns correct Pb95 price as PLN/litre."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # 5228 PLN/1000L = 5.2280 PLN/L
    assert data["unleaded"] == pytest.approx(5.228, abs=1e-4)


async def test_async_fetch_diesel_price() -> None:
    """async_fetch returns correct ONEkodiesel price as PLN/litre."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # 5597 PLN/1000L = 5.5970 PLN/L
    assert data["diesel"] == pytest.approx(5.597, abs=1e-4)


async def test_async_fetch_pb98_price() -> None:
    """async_fetch returns correct Pb98 price as PLN/litre."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # 5739 PLN/1000L = 5.7390 PLN/L
    assert data["premium_unleaded"] == pytest.approx(5.739, abs=1e-4)


async def test_async_fetch_lpg_price_is_minimum_voivodeship() -> None:
    """async_fetch returns the minimum LPG price across all voivodeships."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # Minimum from _AUTOGAS_PAYLOAD is 2.750 PLN/L
    assert data["lpg"] == pytest.approx(2.750, abs=1e-4)


async def test_async_fetch_kerosene_price() -> None:
    """async_fetch returns correct OnEkoterm price mapped to 'kerosene'."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # 4100 PLN/1000L = 4.1000 PLN/L
    assert data["kerosene"] == pytest.approx(4.1, abs=1e-4)


async def test_async_fetch_e85_price() -> None:
    """async_fetch returns correct BIO100 price mapped to 'e85'."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # 6200 PLN/1000L = 6.2000 PLN/L
    assert data["e85"] == pytest.approx(6.2, abs=1e-4)


async def test_async_fetch_lastupdated_is_most_recent_date() -> None:
    """async_fetch sets lastupdated to the most recent date in the payload."""
    payload = [
        {"productName": "Pb95", "value": 5228, "effectiveDate": "2026-06-10"},
        {"productName": "ONEkodiesel", "value": 5597, "effectiveDate": "2026-06-13"},
    ]
    resp_wholesale = _make_mock_response(200, json_data=payload)
    resp_lpg = _make_mock_response(200, json_data=[])
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data["lastupdated"] == "2026-06-13"


async def test_async_fetch_source_station_id_is_pl() -> None:
    """async_fetch sets source_station_id to 'PL'."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data["source_station_id"] == "PL"


async def test_async_fetch_name_is_set() -> None:
    """async_fetch sets the 'name' field to a descriptive string."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("name") is not None
    assert "ORLEN" in data["name"] or "Poland" in data["name"]


async def test_async_fetch_county_is_pl() -> None:
    """async_fetch sets county to 'PL'."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("county") == "PL"


# ---------------------------------------------------------------------------
# async_fetch — LPG fallback
# ---------------------------------------------------------------------------


async def test_async_fetch_lpg_is_none_when_autogasprices_fails() -> None:
    """async_fetch sets lpg=None when /autogasprices returns an HTTP error."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(500)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # LPG failure is non-fatal; other prices must still be present
    assert data["lpg"] is None
    assert data["unleaded"] is not None


async def test_async_fetch_lpg_is_none_when_autogasprices_empty() -> None:
    """async_fetch sets lpg=None when /autogasprices returns an empty list."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=[])
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data["lpg"] is None


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_http_500() -> None:
    """async_fetch raises ProviderError when wholesale endpoint returns HTTP 500."""
    resp_wholesale = _make_mock_response(500)
    session = _make_session(resp_wholesale)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "PL")


async def test_async_fetch_raises_provider_error_on_non_array_response() -> None:
    """async_fetch raises ProviderError when API returns a JSON object instead of array."""
    resp_wholesale = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(resp_wholesale)

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "PL")


async def test_async_fetch_raises_provider_error_on_client_error() -> None:
    """async_fetch raises ProviderError on connection failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _make_provider()

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "PL")


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_string() -> None:
    """async_fetch_station_name returns a non-empty string without API call."""
    session = MagicMock()
    provider = _make_provider()

    name = await provider.async_fetch_station_name(session, "PL")

    assert name is not None
    assert len(name) > 0
    session.get.assert_not_called()


async def test_async_fetch_station_name_contains_orlen() -> None:
    """async_fetch_station_name mentions ORLEN in the name."""
    session = MagicMock()
    provider = _make_provider()

    name = await provider.async_fetch_station_name(session, "PL")

    assert name is not None
    assert "ORLEN" in name or "Poland" in name


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_single_entry() -> None:
    """async_list_stations returns exactly one entry (national wholesale record)."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert isinstance(result, list)
    assert len(result) == 1


async def test_async_list_stations_station_id_is_pl() -> None:
    """async_list_stations returns 'PL' as the station_id."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    uid, _label = result[0]
    assert uid == "PL"


async def test_async_list_stations_label_contains_orlen() -> None:
    """async_list_stations label mentions ORLEN."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _uid, label = result[0]
    assert "ORLEN" in label or "Poland" in label


async def test_async_list_stations_label_includes_pb95_price() -> None:
    """async_list_stations label includes the Pb95 price."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    _uid, label = result[0]
    assert "Pb95" in label or "PLN" in label


async def test_async_list_stations_without_coordinates_still_returns_entry() -> None:
    """async_list_stations returns the national record even without lat/lng."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = PlBenzynaProvider()  # no lat/lng
    result = await provider.async_list_stations(session)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0][0] == "PL"


async def test_async_list_stations_with_zero_coordinates_returns_entry() -> None:
    """Stations at lat=0.0, lng=0.0 must not be dropped by falsy coord check."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    # Pass lat=0.0, lng=0.0 explicitly — should still return the national entry
    result = await provider.async_list_stations(session, lat=0.0, lng=0.0)

    assert len(result) == 1
    assert result[0][0] == "PL"


# ---------------------------------------------------------------------------
# async_list_stations — error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


async def test_async_list_stations_returns_empty_on_http_500() -> None:
    """async_list_stations returns [] when wholesale endpoint returns HTTP 500."""
    resp_wholesale = _make_mock_response(500)
    session = _make_session(resp_wholesale)

    provider = _make_provider()
    result = await provider.async_list_stations(session, lat=_LAT, lng=_LNG)

    assert result == []


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """PlBenzynaProvider is registered in PROVIDER_REGISTRY."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "pl_benzyna" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["pl_benzyna"] is PlBenzynaProvider


# ---------------------------------------------------------------------------
# Field mapping coverage
# ---------------------------------------------------------------------------


async def test_async_fetch_all_product_codes_mapped() -> None:
    """async_fetch correctly maps all known ORLEN productCodes to StationData keys."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # Core fuel types
    assert data.get("unleaded") is not None  # Pb95
    assert data.get("premium_unleaded") is not None  # Pb98
    assert data.get("diesel") is not None  # ONEkodiesel
    assert data.get("premium_diesel") is not None  # ONArctic2
    assert data.get("kerosene") is not None  # OnEkoterm
    assert data.get("e85") is not None  # BIO100
    assert data.get("lpg") is not None  # from autogasprices


async def test_async_fetch_aviation_fuels_as_extra_attrs() -> None:
    """async_fetch stores JETA1 and AVGAS100LL as extra passthrough attributes."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # Aviation fuels stored as lowercase keys
    assert "jeta1" in data
    assert "avgas100ll" in data
    assert data["jeta1"] == pytest.approx(7.1, abs=1e-4)
    assert data["avgas100ll"] == pytest.approx(8.9, abs=1e-4)


async def test_async_fetch_prices_in_pln_per_litre_not_per_1000l() -> None:
    """async_fetch returns prices in PLN/litre (not PLN/1000L)."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data=_AUTOGAS_PAYLOAD)
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    # All prices should be < 100 PLN/litre (wholesale prices are ~5 PLN/L)
    for key in ("unleaded", "diesel", "premium_unleaded", "premium_diesel", "kerosene"):
        price = data.get(key)
        if price is not None:
            assert price < 100.0, (
                f"Price for '{key}' is {price} — looks like PLN/1000L was not converted"
            )
            assert price > 0.5, (
                f"Price for '{key}' is {price} — unexpectedly low, possible over-division"
            )


async def test_async_fetch_missing_products_return_none() -> None:
    """async_fetch returns None for products absent from the API response."""
    minimal_payload = [
        {"productName": "Pb95", "value": 5228, "effectiveDate": "2026-06-13"},
    ]
    resp_wholesale = _make_mock_response(200, json_data=minimal_payload)
    resp_lpg = _make_mock_response(200, json_data=[])
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("unleaded") is not None
    assert data.get("diesel") is None
    assert data.get("premium_unleaded") is None
    assert data.get("lpg") is None


# ---------------------------------------------------------------------------
# _parse_price_pln / _fetch_lpg_price edge-cases (lines 142, 145-146, 148,
# 406-410)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_lpg_non_list_payload_returns_none_for_lpg() -> None:
    """_fetch_lpg_price returns None when /autogasprices returns a non-list JSON value."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(200, json_data={"error": "unexpected"})
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("lpg") is None


@pytest.mark.asyncio
async def test_async_fetch_lpg_none_value_skipped() -> None:
    """_parse_price_pln returns None for a None value; record is skipped in LPG min."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(
        200,
        json_data=[{"voivodeship": "Mazowieckie", "value": None}],
    )
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("lpg") is None


@pytest.mark.asyncio
async def test_async_fetch_lpg_non_numeric_value_skipped() -> None:
    """_parse_price_pln returns None for a non-numeric string; record is skipped in LPG min."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(
        200,
        json_data=[{"voivodeship": "Mazowieckie", "value": "not-a-number"}],
    )
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("lpg") is None


@pytest.mark.asyncio
async def test_async_fetch_lpg_non_positive_value_skipped() -> None:
    """_parse_price_pln returns None for a non-positive value; record is skipped in LPG min."""
    resp_wholesale = _make_mock_response(200, json_data=_WHOLESALE_PAYLOAD)
    resp_lpg = _make_mock_response(
        200,
        json_data=[{"voivodeship": "Mazowieckie", "value": 0}],
    )
    session = _make_session(resp_wholesale, resp_lpg)

    provider = _make_provider()
    data = await provider.async_fetch(session, "PL")

    assert data.get("lpg") is None
