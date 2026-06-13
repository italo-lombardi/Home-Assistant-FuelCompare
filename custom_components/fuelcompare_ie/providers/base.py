"""BaseProvider ABC for Fuel Compare data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from aiohttp import ClientSession


class ProviderError(Exception):
    """Raised by a provider when it cannot retrieve station data."""


class BaseProvider(ABC):
    """Abstract base class for a fuel price data provider."""

    COUNTRY: ClassVar[str]
    """ISO 3166-1 alpha-2 country code this provider serves, e.g. 'IE'."""

    PROVIDER_KEY: ClassVar[str]
    """Unique machine key stored in config entry data, e.g. 'ie_fuelcompare'."""

    LABEL: ClassVar[str]
    """Human-readable label shown in the config flow provider picker."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", None):
            for attr in ("COUNTRY", "PROVIDER_KEY", "LABEL"):
                if not hasattr(cls, attr):
                    raise TypeError(f"{cls.__name__} must define class attribute '{attr}'")

    @abstractmethod
    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> dict[str, Any]:
        """Fetch and return normalised station data.

        The returned dict must contain at minimum the keys consumed by
        coordinator._parse_station and by the sensor/binary_sensor platforms:
        'unleaded', 'diesel', 'lastupdated', 'name', 'tablename',
        'working_hours', 'about', 'county'.  All values may be None.
        """

    @abstractmethod
    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a display name for the station, or None if unavailable."""
