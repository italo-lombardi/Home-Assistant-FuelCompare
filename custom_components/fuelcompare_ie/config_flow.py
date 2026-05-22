"""Config flow for FuelCompare.ie integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import CONF_STATION_ID, DOMAIN


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

            # Validate and normalize station ID before setting unique_id
            try:
                station_id_int = int(station_id)
                if station_id_int <= 0:
                    errors[CONF_STATION_ID] = "invalid_station_id"
                else:
                    station_id = str(station_id_int)  # normalize "007" → "7"
            except (ValueError, TypeError):
                errors[CONF_STATION_ID] = "invalid_station_id"

            if not errors:
                # Set unique ID after normalization so "007" and "7" map to same entry
                await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
                self._abort_if_unique_id_configured()

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
