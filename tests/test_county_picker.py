"""Tests for county/picker config flow steps and HRMzoeProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.fuelcompare_ie.const import (
    CONF_COUNTRY,
    CONF_PROVIDER,
    CONF_STATION_COUNTY,
    CONF_STATION_ID,
    DOMAIN,
)
from custom_components.fuelcompare_ie.providers.hr_mzoe import (
    HRMzoeProvider,
    _extract_prices,
    _find_station_in_data,
    _parse_station,
)
from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
    IEFuelFinderProvider,
)

_PATCH_FIRST_REFRESH = patch(
    "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
    new_callable=AsyncMock,
)

# ── Minimal raw dataset for Croatia tests ─────────────────────────────────────

_RAW = {
    "postajas": [
        {
            "id": 1001,
            "naziv": "Shell Zagreb",
            "adresa": "Test 1",
            "lat": 15.97,  # NOTE: swapped — this is actually longitude
            "long": 45.81,  # NOTE: swapped — this is actually latitude
            "obveznik_id": 10,
            "zupanija_id": 1,
            "cjenici": [
                {"id": 1, "gorivo_id": 101, "cijena": 1.45},  # diesel
                {"id": 2, "gorivo_id": 201, "cijena": 1.55},  # unleaded
            ],
        },
        {
            "id": 1002,
            "naziv": "INA Split",
            "adresa": "Main St",
            "lat": 16.44,
            "long": 43.51,
            "obveznik_id": 11,
            "zupanija_id": 2,
            "cjenici": [
                {"id": 3, "gorivo_id": 101, "cijena": 1.42},
            ],
        },
    ],
    "gorivos": [
        {"id": 101, "naziv": "Eurodizel", "vrsta_goriva_id": 8, "obveznik_id": 10},
        {"id": 201, "naziv": "Eurobenzin", "vrsta_goriva_id": 2, "obveznik_id": 10},
    ],
    "vrsta_gorivas": [
        {"id": 2, "tip_goriva_id": 1},  # benzin → unleaded
        {"id": 8, "tip_goriva_id": 2},  # dizel → diesel
    ],
    "obvezniks": [
        {"id": 10, "naziv": "Shell d.o.o."},
        {"id": 11, "naziv": "INA d.d."},
    ],
    "zupanijas": [
        {"id": 1, "naziv": "Grad Zagreb"},
        {"id": 2, "naziv": "Splitsko-dalmatinska"},
    ],
    "tip_gorivas": [
        {"id": 1, "tip_goriva": "Benzinska"},
        {"id": 2, "tip_goriva": "Dizelska"},
    ],
    "opcija": [],
    "opcinas": [],
    "naseljes": [],
}


# ── HRMzoeProvider metadata ───────────────────────────────────────────────────


def test_hr_provider_metadata() -> None:
    """HRMzoeProvider declares required class attributes."""
    assert HRMzoeProvider.COUNTRY == "HR"
    assert HRMzoeProvider.PROVIDER_KEY == "hr_mzoe"
    assert HRMzoeProvider.LABEL == "MINGOR (Croatia)"
    assert HRMzoeProvider.STATION_LOOKUP_MODE == "county_search"
    assert HRMzoeProvider.POLL_INTERVAL_SECONDS == 3600


def test_hr_provider_capabilities() -> None:
    """CAPABILITIES covers expected keys."""
    caps = HRMzoeProvider.CAPABILITIES
    assert "diesel" in caps
    assert "unleaded" in caps
    assert "lpg" in caps
    assert "latitude" in caps
    assert "longitude" in caps
    assert "address" in caps


# ── _find_station_in_data ─────────────────────────────────────────────────────


def test_find_station_found() -> None:
    assert _find_station_in_data(_RAW, "1001") is not None
    assert _find_station_in_data(_RAW, "1001")["naziv"] == "Shell Zagreb"


def test_find_station_not_found() -> None:
    assert _find_station_in_data(_RAW, "9999") is None


def test_find_station_empty_raw() -> None:
    assert _find_station_in_data({}, "1001") is None


# ── _extract_prices ───────────────────────────────────────────────────────────


def test_extract_prices_diesel_and_unleaded() -> None:
    vrsta_tip = {v["id"]: v["tip_goriva_id"] for v in _RAW["vrsta_gorivas"]}
    gorivo_vrsta = {g["id"]: g["vrsta_goriva_id"] for g in _RAW["gorivos"]}
    station = _RAW["postajas"][0]
    prices = _extract_prices(station, vrsta_tip, gorivo_vrsta)
    assert prices.get("diesel") == pytest.approx(1.45)
    assert prices.get("unleaded") == pytest.approx(1.55)


def test_extract_prices_no_cjenici() -> None:
    vrsta_tip = {v["id"]: v["tip_goriva_id"] for v in _RAW["vrsta_gorivas"]}
    gorivo_vrsta = {g["id"]: g["vrsta_goriva_id"] for g in _RAW["gorivos"]}
    prices = _extract_prices({"cjenici": []}, vrsta_tip, gorivo_vrsta)
    assert prices == {}


# ── _parse_station (lat/lng swap) ─────────────────────────────────────────────


def test_parse_station_lat_lng_corrected() -> None:
    """lat/lng fields are swapped in the source; parser must correct them."""
    station = _RAW["postajas"][0]  # lat=15.97 (actually lng), long=45.81 (actually lat)
    result = _parse_station(station, _RAW)
    # After correction: latitude should be ~45.81, longitude ~15.97
    assert result["latitude"] == pytest.approx(45.81)
    assert result["longitude"] == pytest.approx(15.97)


def test_parse_station_brand_and_county() -> None:
    station = _RAW["postajas"][0]
    result = _parse_station(station, _RAW)
    assert result["brand"] == "Shell d.o.o."
    assert result["county"] == "Grad Zagreb"
    assert result["name"] == "Shell Zagreb"
    assert result["address"] == "Test 1"


def test_parse_station_prices() -> None:
    station = _RAW["postajas"][0]
    result = _parse_station(station, _RAW)
    assert result["diesel"] == pytest.approx(1.45)
    assert result["unleaded"] == pytest.approx(1.55)


# ── HRMzoeProvider.async_fetch ────────────────────────────────────────────────


def _make_gzip_response(data: dict) -> AsyncMock:
    import gzip as _gzip
    import json as _json

    compressed = _gzip.compress(_json.dumps(data).encode())
    mock = AsyncMock()
    mock.status = 200
    mock.read = AsyncMock(return_value=compressed)
    mock.raise_for_status = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


def _make_session_with_raw(raw: dict) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=_make_gzip_response(raw))
    return session


async def test_hr_async_fetch_success() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("1001")
    data = await provider.async_fetch(session, "1001")
    assert data["diesel"] == pytest.approx(1.45)
    assert data["name"] == "Shell Zagreb"


async def test_hr_async_fetch_station_not_found() -> None:
    from custom_components.fuelcompare_ie.providers.base import ProviderError

    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("9999")
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "9999")


async def test_hr_async_fetch_network_error() -> None:
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))
    provider = HRMzoeProvider("1001")
    with pytest.raises(ClientError):
        await provider.async_fetch(session, "1001")


# ── HRMzoeProvider.async_fetch_station_name ───────────────────────────────────


async def test_hr_async_fetch_station_name_success() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("1001")
    name = await provider.async_fetch_station_name(session, "1001")
    assert name == "Shell Zagreb"


async def test_hr_async_fetch_station_name_not_found() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("9999")
    name = await provider.async_fetch_station_name(session, "9999")
    assert name is None


async def test_hr_async_fetch_station_name_network_error() -> None:
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))
    provider = HRMzoeProvider("1001")
    name = await provider.async_fetch_station_name(session, "1001")
    assert name is None


# ── HRMzoeProvider.async_list_stations ───────────────────────────────────────


async def test_hr_async_list_stations_all() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("")
    stations = await provider.async_list_stations(session, county="croatia")
    assert len(stations) == 2
    assert all(isinstance(uid, str) for uid, _ in stations)
    assert all(isinstance(label, str) for _, label in stations)


async def test_hr_async_list_stations_county_filter() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("")
    stations = await provider.async_list_stations(session, county="grad_zagreb")
    assert len(stations) == 1
    assert stations[0][0] == "1001"


async def test_hr_async_list_stations_no_match() -> None:
    session = _make_session_with_raw(_RAW)
    provider = HRMzoeProvider("")
    stations = await provider.async_list_stations(session, county="nonexistent_county")
    assert stations == []


async def test_hr_async_list_stations_network_error() -> None:
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))
    provider = HRMzoeProvider("")
    stations = await provider.async_list_stations(session, county="croatia")
    assert stations == []


# ── IEFuelFinderProvider.async_list_stations ──────────────────────────────────


def _ff_stations_response(stations: list[dict], fuel: str = "diesel") -> dict:
    return {
        "stations": stations,
        "city": "dublin",
        "fuel": fuel,
        "total": len(stations),
    }


def _ff_station(uid: str, name: str, price: float | None, brand: str = "") -> dict:
    return {
        "id": uid,
        "osm_id": "123",
        "name": name,
        "slug": name.lower(),
        "brand": brand,
        "logo_url": None,
        "lat": 53.3,
        "lng": -6.2,
        "county": "Dublin",
        "street": "",
        "phone": "",
        "website": "",
        "opening_hours": "",
        "price": price,
        "updated_at": "2026-06-14T12:00:00+00:00",
        "confidence": "likely",
        "has_price": price is not None,
    }


def _make_ff_session(*responses) -> MagicMock:
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_, **__):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _make_ff_response(data: dict) -> AsyncMock:
    mock = AsyncMock()
    mock.status = 200
    mock.json = AsyncMock(return_value=data)
    mock.raise_for_status = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


async def test_ff_async_list_stations_returns_sorted_cheapest_first() -> None:
    s1 = _ff_station("uuid-1", "Shell Dun Laoghaire", 1.90)
    s2 = _ff_station("uuid-2", "Circle K Swords", 1.83)
    s3 = _ff_station("uuid-3", "BP Tallaght", None)

    diesel_resp = _make_ff_response(_ff_stations_response([s1, s2, s3], "diesel"))
    petrol_resp = _make_ff_response(_ff_stations_response([], "petrol"))
    kerosene_resp = _make_ff_response(
        {"stations": [], "total": 0, "city": "dublin", "fuel": "kerosene"}
    )
    session = _make_ff_session(diesel_resp, petrol_resp, kerosene_resp)

    provider = IEFuelFinderProvider("")
    stations = await provider.async_list_stations(session, county="dublin")

    # Sorted alphabetically by label: BP < Circle K < Shell
    labels = [label for _, label in stations]
    assert labels[0].startswith("BP")
    assert labels[1].startswith("Circle K")
    assert labels[2].startswith("Shell")


async def test_ff_async_list_stations_empty_on_network_error() -> None:
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))
    provider = IEFuelFinderProvider("")
    stations = await provider.async_list_stations(session, county="dublin")
    assert stations == []


# ── Config flow county→picker integration path ────────────────────────────────


async def test_config_flow_fuelfinder_county_to_picker(hass: HomeAssistant) -> None:
    """Full county_search config flow: country→provider→county→picker→name→entry."""
    mock_stations = [("uuid-abc", "Circle K Donabate — Diesel €1.83")]

    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch.object(
            IEFuelFinderProvider,
            "async_list_stations",
            new_callable=AsyncMock,
            return_value=mock_stations,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
            new_callable=AsyncMock,
            return_value="Circle K Donabate",
        ),
        _PATCH_FIRST_REFRESH,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Country step — select Ireland
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_COUNTRY: "IE"}
        )
        # Provider step — select FuelFinder.ie
        assert result["step_id"] == "provider"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PROVIDER: "ie_fuelfinder"}
        )
        # County step
        assert result["step_id"] == "county"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_STATION_COUNTY: "dublin"}
        )
        # Station picker
        assert result["step_id"] == "station_picker"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_STATION_ID: "uuid-abc"}
        )
        # Two-pass: if URL found, picker re-renders with link — submit again to confirm
        if result.get("step_id") == "station_picker":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_STATION_ID: "uuid-abc"}
            )
        # Name confirmation
        assert result["step_id"] == "name"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"name": "My FuelFinder Station"}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "My FuelFinder Station"
    assert result["data"][CONF_STATION_ID] == "uuid-abc"
    assert result["data"][CONF_STATION_COUNTY] == "dublin"
    assert result["data"][CONF_PROVIDER] == "ie_fuelfinder"
    assert result["data"][CONF_COUNTRY] == "IE"


async def test_config_flow_croatia_county_picker(hass: HomeAssistant) -> None:
    """Full Croatia config flow path."""
    mock_stations = [("1001", "Shell Zagreb — Diesel €1.45")]

    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch.object(
            HRMzoeProvider,
            "async_list_stations",
            new_callable=AsyncMock,
            return_value=mock_stations,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
            new_callable=AsyncMock,
            return_value="Shell Zagreb",
        ),
        _PATCH_FIRST_REFRESH,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_COUNTRY: "HR"}
        )
        # Croatia has 1 provider → skips provider step, goes to county
        assert result["step_id"] == "county"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_STATION_COUNTY: "grad_zagreb"}
        )
        assert result["step_id"] == "station_picker"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_STATION_ID: "1001"}
        )
        # Two-pass: if URL found, picker re-renders with link — submit again to confirm
        if result.get("step_id") == "station_picker":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_STATION_ID: "1001"}
            )
        assert result["step_id"] == "name"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"name": "Shell Zagreb"}
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_COUNTRY] == "HR"
    assert result["data"][CONF_PROVIDER] == "hr_mzoe"
    assert result["data"][CONF_STATION_ID] == "1001"
    assert result["data"][CONF_STATION_COUNTY] == "grad_zagreb"


async def test_config_flow_station_picker_no_stations_shows_error(
    hass: HomeAssistant,
) -> None:
    """Empty station list shows form with base error."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch.object(
            IEFuelFinderProvider,
            "async_list_stations",
            new_callable=AsyncMock,
            return_value=[],
        ),
        _PATCH_FIRST_REFRESH,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: "IE"}
            )
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: "ie_fuelfinder"}
            )
        assert result["step_id"] == "county"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_STATION_COUNTY: "dublin"}
        )
        assert result["step_id"] == "station_picker"
        assert "base" in result.get("errors", {})
