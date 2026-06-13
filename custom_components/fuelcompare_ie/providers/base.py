"""BaseProvider ABC for Fuel Compare data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aiohttp import ClientSession


class BaseProvider(ABC):
    """Abstract base class for a fuel price data provider."""

    COUNTRY: str
    """ISO 3166-1 alpha-2 country code this provider serves, e.g. 'IE'."""

    PROVIDER_KEY: str
    """Unique machine key stored in config entry data, e.g. 'ie_fuelcompare'."""

    LABEL: str
    """Human-readable label shown in the config flow provider picker."""

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
