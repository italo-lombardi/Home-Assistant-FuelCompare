"""Config flow for FuelCompare.ie integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import FuelCompareIECoordinator

_LOGGER = logging.getLogger(__name__)


async def _fetch_station_name(hass, station_id: str) -> str | None:
    """Try to resolve the station's brand name from the API. Returns None on any failure."""
    try:
        session = async_get_clientsession(hass)
        coordinator = FuelCompareIECoordinator(hass, station_id)
        await coordinator._fetch_page_assets(session)
        data = await coordinator._fetch_nextjs(session)
        if data is None:
            data = await coordinator._fetch_encrypted_api(session)
        if data and (data.get("name") or data.get("tablename")):
            if data.get("name"):
                return data["name"]
            return data["tablename"].replace("_", " ").title()
    except Exception:  # noqa: BLE001
        pass
    return None


class FuelCompareIEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FuelCompare.ie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the config flow."""
        self._station_id: str = ""
        self._suggested_name: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect and validate station ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]

            try:
                station_id_int = int(station_id)
                if station_id_int <= 0:
                    errors[CONF_STATION_ID] = "invalid_station_id"
                else:
                    station_id = str(station_id_int)
            except (ValueError, TypeError):
                errors[CONF_STATION_ID] = "invalid_station_id"

            if not errors:
                await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
                self._abort_if_unique_id_configured()

                self._station_id = station_id
                # Try to pre-populate name from API; fall back to generic label
                fetched = await _fetch_station_name(self.hass, station_id)
                self._suggested_name = fetched or f"Station {station_id}"
                return await self.async_step_name()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the name confirmation step — user can accept or override."""
        if user_input is not None:
            title = user_input.get(CONF_NAME) or self._suggested_name
            return self.async_create_entry(
                title=title,
                data={CONF_STATION_ID: self._station_id},
            )

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=self._suggested_name): str,
                }
            ),
        )
