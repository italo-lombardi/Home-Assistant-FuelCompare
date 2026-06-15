"""DataUpdateCoordinator for Fuel Compare integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .providers.base import BaseProvider, ProviderError, StationData

if TYPE_CHECKING:  # pragma: no cover
    from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class FuelCompareIECoordinator(DataUpdateCoordinator[StationData]):
    """Coordinator that delegates data fetching to a BaseProvider."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider: BaseProvider,
        station_id: str = "",
        config_entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fuel Compare [{provider.PROVIDER_KEY}] Station {station_id}",
            update_interval=timedelta(seconds=provider.POLL_INTERVAL_SECONDS),
            config_entry=config_entry,
        )
        self.station_id = station_id
        self._provider = provider
        self.last_successful_fetch: datetime | None = None

    @property
    def provider_capabilities(self) -> frozenset[str]:
        return self._provider.CAPABILITIES

    @property
    def provider_label(self) -> str:
        return self._provider.LABEL

    @property
    def provider_currency(self) -> str:
        return self._provider.CURRENCY

    # ---- Update cycle -----------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Cancel pending tasks and release resources."""
        await super().async_shutdown()

    async def _async_update_data(self) -> StationData:
        try:
            session = async_get_clientsession(self.hass)
            _LOGGER.debug("Starting data update for station %s", self.station_id)
            data = await self._provider.async_fetch(session, self.station_id)
            self.last_successful_fetch = dt_util.utcnow()
            return data
        except ClientError as err:
            _LOGGER.warning(
                "HTTP error fetching station %s: %s",
                self.station_id,
                err,
            )
            raise UpdateFailed(
                f"Error communicating with API: {type(err).__name__}"
            ) from err
        except ProviderError as err:
            _LOGGER.warning("Provider error for station %s: %s", self.station_id, err)
            raise UpdateFailed(str(err)) from err
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error fetching station %s: %s", self.station_id, err
            )
            raise UpdateFailed(f"Unexpected error: {type(err).__name__}") from err
