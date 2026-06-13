"""DataUpdateCoordinator for Fuel Compare integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import homeassistant.util.dt as dt_util
from aiohttp import ClientError, ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL
from .crypto import cryptojs_decrypt as _cryptojs_decrypt  # noqa: F401  # re-exported for test compat
from .providers.base import BaseProvider, ProviderError

_LOGGER = logging.getLogger(__name__)


class FuelCompareIECoordinator(DataUpdateCoordinator[dict]):
    """Coordinator that delegates data fetching to a BaseProvider."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider_or_station_id: "BaseProvider | str",
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
            name=f"Fuel Compare Station {_station_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
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

    @property
    def _decrypt_key(self) -> str | None:
        return getattr(self._provider, "_decrypt_key", None)

    @_decrypt_key.setter
    def _decrypt_key(self, value: str | None) -> None:
        if hasattr(self._provider, "_decrypt_key"):
            self._provider._decrypt_key = value

    async def _fetch_page_assets(
        self, session: ClientSession, broad: bool = False
    ) -> None:
        if hasattr(self._provider, "_fetch_page_assets"):
            await self._provider._fetch_page_assets(session, broad=broad)

    async def _fetch_nextjs(self, session: ClientSession) -> dict | None:
        if hasattr(self._provider, "_fetch_nextjs"):
            return await self._provider._fetch_nextjs(session)
        return None

    async def _fetch_encrypted_api(self, session: ClientSession) -> dict | None:
        if hasattr(self._provider, "_fetch_encrypted_api"):
            return await self._provider._fetch_encrypted_api(session)
        return None

    def _parse_station(self, station: dict) -> dict:
        if hasattr(self._provider, "_parse_station"):
            return self._provider._parse_station(station)
        return station

    # ---- Update cycle -----------------------------------------------------------

    async def _async_update_data(self) -> dict:
        try:
            session = async_get_clientsession(self.hass)
            _LOGGER.debug("Starting data update for station %s", self.station_id)
            data = await self._provider.async_fetch(session, self.station_id)
            self.last_successful_fetch = dt_util.utcnow()
            return data
        except ClientError as err:
            _LOGGER.debug("HTTP error fetching station %s: %s", self.station_id, err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except ProviderError as err:
            _LOGGER.debug("Provider error for station %s: %s", self.station_id, err)
            raise UpdateFailed(str(err)) from err
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.debug(
                "Unexpected error fetching station %s: %s", self.station_id, err
            )
            raise UpdateFailed(f"Unexpected error: {err}") from err
