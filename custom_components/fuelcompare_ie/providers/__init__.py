"""Provider registry for Fuel Compare integration."""

from __future__ import annotations

from .base import BaseProvider
from .ie_fuelcompare import IEFuelCompareProvider

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    IEFuelCompareProvider.PROVIDER_KEY: IEFuelCompareProvider,
}

__all__ = ["BaseProvider", "PROVIDER_REGISTRY"]
