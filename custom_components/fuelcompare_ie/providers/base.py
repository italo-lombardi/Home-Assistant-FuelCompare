"""BaseProvider ABC for Fuel Compare data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

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

    CONFIG_MODE: ClassVar[Literal["station_id", "location"]] = "station_id"
    """How the user identifies what to track.

    'station_id' — user enters a numeric/string station ID (current IE behaviour).
    'location'   — user enters lat/lng + radius; coordinator fetches all stations
                   in range and creates entities dynamically.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", None):
            for attr in ("COUNTRY", "PROVIDER_KEY", "LABEL"):
                if not hasattr(cls, attr):
                    raise TypeError(
                        f"{cls.__name__} must define class attribute '{attr}'"
                    )

    @abstractmethod
    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> dict[str, Any]:
        """Fetch and return normalised station data.

        For CONFIG_MODE='station_id': station_id is the user-entered identifier.
        For CONFIG_MODE='location': station_id is unused; the provider uses its
        own lat/lng/radius stored at construction time.

        The returned dict must contain at minimum:
        'unleaded', 'diesel', 'lastupdated', 'name', 'tablename',
        'working_hours', 'about', 'county'.  All values may be None.
        """

    @abstractmethod
    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a display name for the station, or None if unavailable.

        For CONFIG_MODE='location' providers this may return None; the config
        flow uses the entry title from the location step instead.
        """
