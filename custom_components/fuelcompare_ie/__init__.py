"""The Fuel Compare integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_PROVIDER, CONF_STATION_ID, DEFAULT_PROVIDER, DOMAIN
from .coordinator import FuelCompareIECoordinator
from .providers import PROVIDER_REGISTRY

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fuel Compare from a config entry."""
    station_id = entry.data[CONF_STATION_ID]

    # Existing entries have no CONF_PROVIDER key — default to ie_fuelcompare
    # so they continue working without any migration.
    provider_key = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
    provider_cls = PROVIDER_REGISTRY.get(provider_key)
    if provider_cls is None:
        provider_cls = PROVIDER_REGISTRY[DEFAULT_PROVIDER]

    provider = provider_cls(station_id)
    coordinator = FuelCompareIECoordinator(hass, provider, station_id)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
