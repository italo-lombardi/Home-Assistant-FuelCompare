"""Config flow for Fuel Compare integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_COUNTRY,
    CONF_PROVIDER,
    CONF_STATION_ID,
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DOMAIN,
)
from .coordinator import FuelCompareIECoordinator
from .providers import PROVIDER_REGISTRY
from .providers.ie_fuelcompare import IEFuelCompareProvider

_LOGGER = logging.getLogger(__name__)


def _countries_from_registry() -> list[tuple[str, str]]:
    """Derive (ISO code, display label) list from PROVIDER_REGISTRY.

    Uses the first LABEL seen per country as the country display label.
    Keeps insertion order so the list is stable across runs.
    """
    seen: dict[str, str] = {}
    for cls in PROVIDER_REGISTRY.values():
        if cls.COUNTRY not in seen:
            seen[cls.COUNTRY] = cls.COUNTRY  # fallback: use code as label
    # Override with human-readable names for known codes
    _COUNTRY_NAMES = {"IE": "Ireland"}
    return [(code, _COUNTRY_NAMES.get(code, code)) for code in seen]


def _providers_for_country(country: str) -> list[tuple[str, str]]:
    """Return (provider_key, label) pairs for a given country code."""
    return [
        (cls.PROVIDER_KEY, cls.LABEL)
        for cls in PROVIDER_REGISTRY.values()
        if cls.COUNTRY == country
    ]


async def _fetch_station_name(hass, station_id: str) -> str | None:
    """Resolve station display name. Module-level so tests can patch it."""
    try:
        session = async_get_clientsession(hass)
        provider = IEFuelCompareProvider(station_id)
        # Build a minimal coordinator so tests can patch coordinator methods
        coordinator = FuelCompareIECoordinator(hass, provider, station_id)
        await coordinator._fetch_page_assets(session)
        data = await coordinator._fetch_nextjs(session)
        if data is None:
            data = await coordinator._fetch_encrypted_api(session)
        if data:
            if data.get("name"):
                return data["name"]
            if data.get("tablename"):
                return data["tablename"].replace("_", " ").title()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
    return None


class FuelCompareIEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fuel Compare."""

    VERSION = 1

    def __init__(self) -> None:
        self._country: str = DEFAULT_COUNTRY
        self._provider_key: str = DEFAULT_PROVIDER
        self._station_id: str = ""
        self._suggested_name: str = ""

    # ---- Step 1: country --------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select country. Auto-advance when only one country is available."""
        countries = _countries_from_registry()

        if len(countries) == 1:
            self._country = countries[0][0]
            return await self._async_step_provider()

        if user_input is not None:
            self._country = user_input[CONF_COUNTRY]
            return await self._async_step_provider()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COUNTRY, default=DEFAULT_COUNTRY): vol.In(
                        {code: label for code, label in countries}
                    ),
                }
            ),
        )

    # ---- Step 2: provider (auto-skipped when single provider) -------------------

    async def _async_step_provider(self) -> ConfigFlowResult:
        """Advance to provider selection or skip directly to station ID."""
        providers = _providers_for_country(self._country)
        if not providers:
            # No providers registered for this country — fall back to default
            self._provider_key = DEFAULT_PROVIDER
            return await self.async_step_station()
        if len(providers) == 1:
            self._provider_key = providers[0][0]
            return await self.async_step_station()
        return await self.async_step_provider()

    async def async_step_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select data provider for the chosen country."""
        providers = _providers_for_country(self._country)

        if user_input is not None:
            self._provider_key = user_input[CONF_PROVIDER]
            return await self.async_step_station()

        return self.async_show_form(
            step_id="provider",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER, default=providers[0][0]): vol.In(
                        {key: label for key, label in providers}
                    ),
                }
            ),
        )

    # ---- Step 3: station ID -----------------------------------------------------

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate the station ID."""
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
                fetched = await _fetch_station_name(self.hass, station_id)
                self._suggested_name = fetched or f"Station {station_id}"
                return await self.async_step_name()

        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    # ---- Step 4: confirm / edit name --------------------------------------------

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm or edit the station display name."""
        if user_input is not None:
            title = user_input.get(CONF_NAME) or self._suggested_name
            return self.async_create_entry(
                title=title,
                data={
                    CONF_STATION_ID: self._station_id,
                    CONF_COUNTRY: self._country,
                    CONF_PROVIDER: self._provider_key,
                },
            )

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=self._suggested_name): str,
                }
            ),
        )


