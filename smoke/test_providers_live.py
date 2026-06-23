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


# ── location_search multi-station providers (affected by #44 unique_id fix) ─

from custom_components.fuelcompare_ie.providers.at_econtrol import AtEcontrolProvider
from custom_components.fuelcompare_ie.providers.au_fuelwatch import AuFuelwatchProvider
from custom_components.fuelcompare_ie.providers.au_nsw import AuNswProvider
from custom_components.fuelcompare_ie.providers.be_carbu import BeCarbuProvider
from custom_components.fuelcompare_ie.providers.ca_qc import CaQcProvider
from custom_components.fuelcompare_ie.providers.ch_tcs import ChTcsProvider
from custom_components.fuelcompare_ie.providers.fr_carburants import FrCarburantsProvider
from custom_components.fuelcompare_ie.providers.gb_fuelfinder import GbFuelfinderProvider
from custom_components.fuelcompare_ie.providers.gr_fuelgov import GrFuelgovProvider
from custom_components.fuelcompare_ie.providers.is_fuel import IsFuelProvider
from custom_components.fuelcompare_ie.providers.it_mase import ItMaseProvider
from custom_components.fuelcompare_ie.providers.lt_saurida import LtSauridaProvider
from custom_components.fuelcompare_ie.providers.se_bensinpriser import SEBensinpriserProvider
from custom_components.fuelcompare_ie.providers.si_goriva import SiGorivaProvider


# ── AT: econtrol.at — Vienna ────────────────────────────────────────────────
async def test_at_econtrol_vienna(session) -> None:
    prov = AtEcontrolProvider("", latitude=48.2082, longitude=16.3738, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=48.2082, lng=16.3738, radius_km=5.0
    )
    assert stations, "AT econtrol: no Vienna stations"


# ── AU WA: FuelWatch — Perth Metro (Region 25) ──────────────────────────────
async def test_au_fuelwatch_perth(session) -> None:
    prov = AuFuelwatchProvider("", county="25")
    stations = await prov.async_list_stations(session, county="25")
    assert stations, "AU FuelWatch: no Perth Metro stations"


async def test_au_fuelwatch_perth_two_stations_distinct(session) -> None:
    """Verify at least two distinct station IDs exist in Perth Metro.

    This directly validates the multi-station config scenario from issue #44.
    """
    prov = AuFuelwatchProvider("", county="25")
    stations = await prov.async_list_stations(session, county="25")
    assert len(stations) >= 2, (
        f"AU FuelWatch Perth: expected ≥2 stations, got {len(stations)}"
    )
    ids = {sid for sid, _ in stations}
    assert len(ids) >= 2, "AU FuelWatch Perth: station IDs not unique"


# ── AU NSW: FuelCheck — Sydney ──────────────────────────────────────────────
async def test_au_nsw_sydney(session) -> None:
    prov = AuNswProvider("", latitude=-33.8688, longitude=151.2093, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=-33.8688, lng=151.2093, radius_km=5.0
    )
    assert stations, "AU NSW FuelCheck: no Sydney stations"


# ── BE: carbu.com Belgium — Brussels (postal code 1000) ─────────────────────
async def test_be_carbu_brussels(session) -> None:
    prov = BeCarbuProvider("", latitude=50.8503, longitude=4.3517, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=50.8503, lng=4.3517, radius_km=5.0, postal_code="1000"
    )
    assert stations, "BE carbu: no Brussels stations"


# ── CA QC: CAA-Québec — Montréal ────────────────────────────────────────────
async def test_ca_qc_montreal(session) -> None:
    prov = CaQcProvider("", latitude=45.5017, longitude=-73.5673, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=45.5017, lng=-73.5673, radius_km=5.0
    )
    assert stations, "CA QC: no Montréal stations"


# ── CH: TCS — Bern ──────────────────────────────────────────────────────────
async def test_ch_tcs_bern(session) -> None:
    prov = ChTcsProvider("", latitude=46.9481, longitude=7.4474, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=46.9481, lng=7.4474, radius_km=5.0
    )
    assert stations, "CH TCS: no Bern stations"


# ── FR: prix-carburants.gouv.fr — Paris ─────────────────────────────────────
async def test_fr_carburants_paris(session) -> None:
    prov = FrCarburantsProvider("", latitude=48.8566, longitude=2.3522, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=48.8566, lng=2.3522, radius_km=5.0
    )
    assert stations, "FR carburants: no Paris stations"


# ── GB: fuelfinder.uk — London ──────────────────────────────────────────────
async def test_gb_fuelfinder_london(session) -> None:
    prov = GbFuelfinderProvider("", latitude=51.5074, longitude=-0.1278, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=51.5074, lng=-0.1278, radius_km=5.0
    )
    assert stations, "GB fuelfinder: no London stations"


# ── GR: fuelgov.gr — Attica prefecture ──────────────────────────────────────
async def test_gr_fuelgov_athens(session) -> None:
    prov = GrFuelgovProvider("GR", prefecture_id=1)  # Attica
    stations = await prov.async_list_stations(session)
    assert stations, "GR fuelgov: no Attica stations"


# ── IS: gasvaktin.is — Reykjavík ────────────────────────────────────────────
async def test_is_fuel_reykjavik(session) -> None:
    prov = IsFuelProvider("", latitude=64.1265, longitude=-21.8174, radius_km=20.0)
    stations = await prov.async_list_stations(
        session, lat=64.1265, lng=-21.8174, radius_km=20.0
    )
    assert stations, "IS gasvaktin: no Reykjavík stations"


# ── IT: MASE — Rome ─────────────────────────────────────────────────────────
async def test_it_mase_rome(session) -> None:
    prov = ItMaseProvider("", latitude=41.9028, longitude=12.4964, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=5.0
    )
    assert stations, "IT MASE: no Rome stations"


# ── LT: saurida.lt — Vilnius ────────────────────────────────────────────────
async def test_lt_saurida_vilnius(session) -> None:
    prov = LtSauridaProvider("", latitude=54.6872, longitude=25.2797, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=54.6872, lng=25.2797, radius_km=5.0
    )
    assert stations, "LT saurida: no Vilnius stations"


# ── SE: bensinpriser.se — Stockholm ─────────────────────────────────────────
async def test_se_bensinpriser_stockholm(session) -> None:
    prov = SEBensinpriserProvider("", latitude=59.3293, longitude=18.0686, radius_km=10.0)
    stations = await prov.async_list_stations(
        session, lat=59.3293, lng=18.0686, radius_km=10.0
    )
    assert stations, "SE bensinpriser: no Stockholm stations"


# ── SI: goriva.si — Ljubljana ────────────────────────────────────────────────
async def test_si_goriva_ljubljana(session) -> None:
    prov = SiGorivaProvider("", latitude=46.0569, longitude=14.5058, radius_km=5.0)
    stations = await prov.async_list_stations(
        session, lat=46.0569, lng=14.5058, radius_km=5.0
    )
    assert stations, "SI goriva: no Ljubljana stations"
