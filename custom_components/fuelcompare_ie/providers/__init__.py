"""Provider registry for Fuel Compare integration."""

from __future__ import annotations

from .base import BaseProvider, ProviderError, StationData
from .ie_fuelcompare import IEFuelCompareProvider
from .ie_fuelfinder import IEFuelFinderProvider

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    IEFuelCompareProvider.PROVIDER_KEY: IEFuelCompareProvider,
    IEFuelFinderProvider.PROVIDER_KEY: IEFuelFinderProvider,
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
