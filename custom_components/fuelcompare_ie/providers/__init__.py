"""Provider registry for Fuel Compare integration."""

from __future__ import annotations

from .at_econtrol import AtEcontrolProvider
from .au_fuelwatch import AuFuelwatchProvider
from .au_nsw import AuNswProvider
from .base import BaseProvider, ProviderError, StationData
from .de_tankerkoenig import DeTankerkoenigProvider
from .es_minetur import EsMineturProvider
from .fr_carburants import FrCarburantsProvider
from .gb_fuelfinder import GbFuelfinderProvider
from .hr_mzoe import HRMzoeProvider
from .ie_fuelcompare import IEFuelCompareProvider
from .ie_fuelfinder import IEFuelFinderProvider
from .it_mase import ItMaseProvider
from .pt_dgeg import PtDgegProvider
from .si_goriva import SiGorivaProvider

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    IEFuelCompareProvider.PROVIDER_KEY: IEFuelCompareProvider,
    IEFuelFinderProvider.PROVIDER_KEY: IEFuelFinderProvider,
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
