"""Provider registry for Fuel Compare integration."""

from __future__ import annotations

from .al_fuel import AlFuelProvider
from .at_econtrol import AtEcontrolProvider
from .au_fuelwatch import AuFuelwatchProvider
from .au_nsw import AuNswProvider
from .au_qld import AuQldProvider
from .au_vic import AuVicProvider
from .ba_fuel import BaFuelProvider
from .base import BaseProvider, ProviderError, StationData
from .be_carbu import BeCarbuProvider
from .ca_qc import CaQcProvider
from .ch_tcs import ChTcsProvider
from .cz_ccs import CzCcsProvider
from .de_tankerkoenig import DeTankerkoenigProvider
from .dk_fuelfinder import DkFuelFinderProvider
from .es_minetur import EsMineturProvider
from .eu_oil_bulletin import EuOilBulletinProvider
from .fi_tankille import FiTankilleProvider
from .fr_carburants import FrCarburantsProvider
from .gb_fuelfinder import GbFuelfinderProvider
from .gr_fuelgov import GrFuelgovProvider
from .hr_mzoe import HRMzoeProvider
from .ie_fuelcompare import IEFuelCompareProvider
from .ie_fuelfinder import IEFuelFinderProvider
from .ie_pumps import IePumpsProvider
from .is_fuel import IsFuelProvider
from .it_mase import ItMaseProvider
from .lt_saurida import LtSauridaProvider
from .lu_carbu import LuCarbuProvider
from .md_fuel import MdFuelProvider
from .me_fuel import MeFuelProvider
from .mt_fuel import MtFuelProvider
from .nl_anwb import NlAnwbProvider
from .no_drivstoff import NoDrivstoffProvider
from .pl_benzyna import PlBenzynaProvider
from .pt_dgeg import PtDgegProvider
from .se_bensinpriser import SEBensinpriserProvider
from .si_goriva import SiGorivaProvider

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    IEFuelCompareProvider.PROVIDER_KEY: IEFuelCompareProvider,
    IEFuelFinderProvider.PROVIDER_KEY: IEFuelFinderProvider,
    IePumpsProvider.PROVIDER_KEY: IePumpsProvider,
    HRMzoeProvider.PROVIDER_KEY: HRMzoeProvider,
    FrCarburantsProvider.PROVIDER_KEY: FrCarburantsProvider,
    DeTankerkoenigProvider.PROVIDER_KEY: DeTankerkoenigProvider,
    EsMineturProvider.PROVIDER_KEY: EsMineturProvider,
    PtDgegProvider.PROVIDER_KEY: PtDgegProvider,
    AtEcontrolProvider.PROVIDER_KEY: AtEcontrolProvider,
    ItMaseProvider.PROVIDER_KEY: ItMaseProvider,
    SiGorivaProvider.PROVIDER_KEY: SiGorivaProvider,
    GbFuelfinderProvider.PROVIDER_KEY: GbFuelfinderProvider,
    AuFuelwatchProvider.PROVIDER_KEY: AuFuelwatchProvider,
    AuNswProvider.PROVIDER_KEY: AuNswProvider,
    AuQldProvider.PROVIDER_KEY: AuQldProvider,
    AuVicProvider.PROVIDER_KEY: AuVicProvider,
    CaQcProvider.PROVIDER_KEY: CaQcProvider,
    DkFuelFinderProvider.PROVIDER_KEY: DkFuelFinderProvider,
    SEBensinpriserProvider.PROVIDER_KEY: SEBensinpriserProvider,
    NoDrivstoffProvider.PROVIDER_KEY: NoDrivstoffProvider,
    BeCarbuProvider.PROVIDER_KEY: BeCarbuProvider,
    EuOilBulletinProvider.PROVIDER_KEY: EuOilBulletinProvider,
    MtFuelProvider.PROVIDER_KEY: MtFuelProvider,
    AlFuelProvider.PROVIDER_KEY: AlFuelProvider,
    BaFuelProvider.PROVIDER_KEY: BaFuelProvider,
    ChTcsProvider.PROVIDER_KEY: ChTcsProvider,
    CzCcsProvider.PROVIDER_KEY: CzCcsProvider,
    FiTankilleProvider.PROVIDER_KEY: FiTankilleProvider,
    GrFuelgovProvider.PROVIDER_KEY: GrFuelgovProvider,
    IsFuelProvider.PROVIDER_KEY: IsFuelProvider,
    LtSauridaProvider.PROVIDER_KEY: LtSauridaProvider,
    LuCarbuProvider.PROVIDER_KEY: LuCarbuProvider,
    MdFuelProvider.PROVIDER_KEY: MdFuelProvider,
    MeFuelProvider.PROVIDER_KEY: MeFuelProvider,
    NlAnwbProvider.PROVIDER_KEY: NlAnwbProvider,
    PlBenzynaProvider.PROVIDER_KEY: PlBenzynaProvider,
}


def get_provider_class(key: str) -> type[BaseProvider] | None:
    """Look up a provider class by key. Returns None if not found."""
    return PROVIDER_REGISTRY.get(key)


def get_provider_or_default(key: str, default_key: str) -> type[BaseProvider]:
    """Look up provider by key, fall back to default_key."""
    cls = PROVIDER_REGISTRY.get(key) or PROVIDER_REGISTRY.get(default_key)
    if cls is None:
        raise RuntimeError(
            f"No provider found for key '{key}' and default '{default_key}'"
        )
    return cls


__all__ = [
    "BaseProvider",
    "ProviderError",
    "StationData",
    "PROVIDER_REGISTRY",
    "get_provider_class",
    "get_provider_or_default",
]
