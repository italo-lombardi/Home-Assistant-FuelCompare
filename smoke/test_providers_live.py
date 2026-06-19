"""Live smoke tests for providers without prior end-to-end verification.

One test per provider key. Each spawns a real provider, queries the
upstream API for a capital-city or canonical sample, and asserts that
at least one station was returned with at least one fuel price.

Capital coordinates are the lowest-friction "this country exists"
probe. Real users will pick stations in their own town; if the capital
returns nothing the upstream is broken, geo-blocked, or has changed.

This file mixes two cohorts:
  - providers currently DISABLED in custom_components/.../providers/
    (ba_fuel, es_minetur, fi_tankille, lu_carbu) — kept here so a
    re-run after an upstream fix flips them green and the provider
    can be re-enabled.
  - providers requiring an API key the project does not have
    (au_qld, au_vic, de_tankerkoenig, no_drivstoff) — skipped unless
    the corresponding env var is supplied.
"""

from __future__ import annotations

import pytest

from custom_components.fuelcompare_ie.providers.au_qld import AuQldProvider
from custom_components.fuelcompare_ie.providers.au_vic import AuVicProvider
from custom_components.fuelcompare_ie.providers.ba_fuel import BaFuelProvider
from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
    DeTankerkoenigProvider,
)
from custom_components.fuelcompare_ie.providers.es_minetur import EsMineturProvider
from custom_components.fuelcompare_ie.providers.fi_tankille import FiTankilleProvider
from custom_components.fuelcompare_ie.providers.lu_carbu import LuCarbuProvider
from custom_components.fuelcompare_ie.providers.me_fuel import MeFuelProvider
from custom_components.fuelcompare_ie.providers.no_drivstoff import (
    NoDrivstoffProvider,
)


pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


# ── BA: cijenegoriva.ba — Sarajevo ──────────────────────────────────────────
async def test_ba_fuel_sarajevo(session) -> None:
    prov = BaFuelProvider("", latitude=43.8563, longitude=18.4131, radius_km=25.0)
    stations = await prov.async_list_stations(
        session, lat=43.8563, lng=18.4131, radius_km=25.0
    )
    assert stations, "BA: no stations near Sarajevo"


# ── DE: Tankerkoenig — Berlin (skipped without API key) ─────────────────────
@pytest.mark.skip(reason="Tankerkoenig requires an API key; supply via env if testing.")
async def test_de_tankerkoenig_berlin(session) -> None:
    import os

    api_key = os.environ.get("TANKERKOENIG_API_KEY")
    if not api_key:
        pytest.skip("Set TANKERKOENIG_API_KEY to smoke-test this provider.")
    prov = DeTankerkoenigProvider(
        "", api_key=api_key, latitude=52.52, longitude=13.405, radius_km=5.0
    )
    stations = await prov.async_list_stations(
        session, lat=52.52, lng=13.405, radius_km=5.0
    )
    assert stations, "DE Tankerkoenig: no Berlin stations"


# ── ES: MINETUR — Madrid ────────────────────────────────────────────────────
async def test_es_minetur_madrid(session) -> None:
    prov = EsMineturProvider("", latitude=40.4168, longitude=-3.7038, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=40.4168, lng=-3.7038, radius_km=5.0
    )
    assert stations, "ES MINETUR: no Madrid stations"


# ── FI: Statistics Finland — National average has no spatial filter ─────────
async def test_fi_tankille_national(session) -> None:
    prov = FiTankilleProvider("FI", latitude=60.1699, longitude=24.9384)
    data = await prov.async_fetch(session, "FI")
    assert any(
        data.get(k) is not None for k in ("unleaded", "diesel", "e10", "kerosene")
    ), f"FI Tankille: no fuel prices in national average ({data})"


# ── LU: carbu.com Luxembourg — Luxembourg City ──────────────────────────────
async def test_lu_carbu_luxembourg_city(session) -> None:
    prov = LuCarbuProvider("", latitude=49.6116, longitude=6.1319, radius_km=10.0)
    stations = await prov.async_list_stations(
        session, lat=49.6116, lng=6.1319, radius_km=10.0
    )
    assert stations, "LU carbu: no Luxembourg City stations"


# ── ME: Min. of Energy — Podgorica ──────────────────────────────────────────
async def test_me_fuel_podgorica(session) -> None:
    prov = MeFuelProvider("", latitude=42.4304, longitude=19.2594, radius_km=15.0)
    stations = await prov.async_list_stations(
        session, lat=42.4304, lng=19.2594, radius_km=15.0
    )
    assert stations, "ME: no Podgorica stations"


# ── NO: Drivstoffpriser — Oslo (requires API key) ──────────────────────────
@pytest.mark.skip(
    reason="Drivstoffpriser requires an API key; supply via env if testing."
)
async def test_no_drivstoff_oslo(session) -> None:
    import os

    api_key = os.environ.get("DRIVSTOFF_API_KEY")
    if not api_key:
        pytest.skip("Set DRIVSTOFF_API_KEY to smoke-test this provider.")
    prov = NoDrivstoffProvider(
        "", api_key=api_key, latitude=59.9139, longitude=10.7522, radius_km=5.0
    )
    stations = await prov.async_list_stations(
        session, lat=59.9139, lng=10.7522, radius_km=5.0
    )
    assert stations, "NO Drivstoffpriser: no Oslo stations"


# ── AU QLD — Brisbane (requires API key from AU FOI portal) ─────────────────
@pytest.mark.skip(reason="Fuel Prices QLD requires API key; supply via env if testing.")
async def test_au_qld_brisbane(session) -> None:
    import os

    api_key = os.environ.get("AU_QLD_API_KEY")
    if not api_key:
        pytest.skip("Set AU_QLD_API_KEY to smoke-test this provider.")
    prov = AuQldProvider(
        "", api_key=api_key, latitude=-27.4698, longitude=153.0251, radius_km=10.0
    )
    stations = await prov.async_list_stations(
        session, lat=-27.4698, lng=153.0251, radius_km=10.0
    )
    assert stations, "AU QLD: no Brisbane stations"


# ── AU VIC Servo Saver — Melbourne (requires API key) ──────────────────────
@pytest.mark.skip(reason="Servo Saver VIC requires API key; supply via env if testing.")
async def test_au_vic_melbourne(session) -> None:
    import os

    api_key = os.environ.get("AU_VIC_API_KEY")
    if not api_key:
        pytest.skip("Set AU_VIC_API_KEY to smoke-test this provider.")
    prov = AuVicProvider(
        "", api_key=api_key, latitude=-37.8136, longitude=144.9631, radius_km=10.0
    )
    stations = await prov.async_list_stations(
        session, lat=-37.8136, lng=144.9631, radius_km=10.0
    )
    assert stations, "AU VIC: no Melbourne stations"
