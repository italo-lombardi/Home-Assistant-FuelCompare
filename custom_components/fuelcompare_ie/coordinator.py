"""DataUpdateCoordinator for Fuel Compare integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .providers.base import BaseProvider, ProviderError, StationData

if TYPE_CHECKING:
    from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class FuelCompareIECoordinator(DataUpdateCoordinator[StationData]):
    """Coordinator that delegates data fetching to a BaseProvider."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider_or_station_id: BaseProvider | str,
        station_id: str | None = None,
    ) -> None:
        # Support old 2-arg call: FuelCompareIECoordinator(hass, station_id_str)
        if isinstance(provider_or_station_id, str):
            from .providers.ie_fuelcompare import IEFuelCompareProvider

            _station_id = provider_or_station_id
            provider: BaseProvider = IEFuelCompareProvider(_station_id)
        else:
            provider = provider_or_station_id
            _station_id = station_id or ""

        super().__init__(
            hass,
            _LOGGER,
            name=f"Fuel Compare [{provider.PROVIDER_KEY}] Station {_station_id}",
            update_interval=timedelta(seconds=provider.POLL_INTERVAL_SECONDS),
        )
        self.station_id = _station_id
        self._provider = provider
        self.last_successful_fetch: datetime | None = None

    # ---- Backwards-compatible accessors used by tests ---------------------------

    @property
    def _build_id(self) -> str | None:
        return getattr(self._provider, "_build_id", None)

    @_build_id.setter
    def _build_id(self, value: str | None) -> None:
        if hasattr(self._provider, "_build_id"):
            self._provider._build_id = value
        else:
            _LOGGER.debug(
                "Coordinator proxy setter: provider has no _build_id attribute, write discarded"
            )

    @property
    def _decrypt_key(self) -> str | None:
        return getattr(self._provider, "_decrypt_key", None)

    @_decrypt_key.setter
    def _decrypt_key(self, value: str | None) -> None:
        if hasattr(self._provider, "_decrypt_key"):
            self._provider._decrypt_key = value
        else:
            _LOGGER.debug(
                "Coordinator proxy setter: provider has no _decrypt_key attribute, write discarded"
            )

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
