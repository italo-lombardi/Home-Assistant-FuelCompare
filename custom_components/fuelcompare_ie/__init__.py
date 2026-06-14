"""The Fuel Compare integration."""

from __future__ import annotations

import inspect

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PROVIDER,
    CONF_RADIUS_KM,
    CONF_STATION_COUNTY,
    CONF_STATION_ID,
    DEFAULT_PROVIDER,
    DOMAIN,
)
from .coordinator import FuelCompareIECoordinator
from .providers import PROVIDER_REGISTRY

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fuel Compare from a config entry."""
    station_id = entry.data.get(CONF_STATION_ID, "")

    # Existing entries have no CONF_PROVIDER key — default to ie_fuelcompare
    # so they continue working without any migration.
    provider_key = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
    provider_cls = PROVIDER_REGISTRY.get(provider_key)
    if provider_cls is None:
        provider_cls = PROVIDER_REGISTRY[DEFAULT_PROVIDER]

    # Pass county to providers that support county_search mode.
    # Pass api_key to providers that require authentication.
    # Pass lat/lng/radius to providers that support geo-filtering.
    # Use inspect to avoid TypeError on providers whose __init__ doesn't accept these params.
    county = entry.data.get(CONF_STATION_COUNTY)
    # API key is stored in entry.options (not entry.data) for security; fall back
    # to entry.data for entries created before this change was introduced.
    api_key = entry.options.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)
    latitude = entry.data.get(CONF_LATITUDE)
    longitude = entry.data.get(CONF_LONGITUDE)
    radius_km = entry.data.get(CONF_RADIUS_KM)
    sig = inspect.signature(provider_cls.__init__)
    kwargs: dict = {}
    if county and "county" in sig.parameters:
        kwargs["county"] = county
    if api_key and "api_key" in sig.parameters:
        kwargs["api_key"] = api_key
    if latitude and "latitude" in sig.parameters:
        kwargs["latitude"] = latitude
    if longitude and "longitude" in sig.parameters:
        kwargs["longitude"] = longitude
    if radius_km and "radius_km" in sig.parameters:
        kwargs["radius_km"] = radius_km
    if kwargs:
        provider = provider_cls(station_id, **kwargs)
    else:
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
