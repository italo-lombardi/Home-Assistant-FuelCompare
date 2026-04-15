"""Config flow for FuelCompare.ie integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import CONF_STATION_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)


class FuelCompareIEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FuelCompare.ie."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]

            # Set unique ID based on station ID
            await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
            self._abort_if_unique_id_configured()

            # Validate station ID (basic check - just ensure it's a positive integer)
            try:
                station_id_int = int(station_id)
                if station_id_int <= 0:
                    errors[CONF_STATION_ID] = "invalid_station_id"
            except (ValueError, TypeError):
                errors[CONF_STATION_ID] = "invalid_station_id"

            if not errors:
                # Create the entry with a title based on station ID
                title = user_input.get(CONF_NAME, f"Station {station_id}")
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_STATION_ID: station_id,
                    },
                )

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID): str,
                vol.Optional(CONF_NAME): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
