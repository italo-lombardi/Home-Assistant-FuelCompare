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
    CONF_POSTAL_CODE,
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

    # Location-mode providers (DE, FR, ES, PT, AT, IT, SI, GB, AU) have no
    # station picker in config_flow, so station_id is always "".  An empty
    # station_id produces an invalid HA entity unique_id.  Generate a stable
    # substitute from the rounded lat/lng stored in entry.data so the device
    # registry entry is stable across restarts.
    if not station_id:
        _lat = entry.data.get(CONF_LATITUDE)
        _lng = entry.data.get(CONF_LONGITUDE)
        if _lat is not None and _lng is not None:
            station_id = f"{round(_lat, 4)}_{round(_lng, 4)}"

    # Existing entries have no CONF_PROVIDER key — default to ie_fuelcompare
    # so they continue working without any migration.
    provider_key = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
    provider_cls = PROVIDER_REGISTRY.get(provider_key)
    if provider_cls is None:
        provider_cls = PROVIDER_REGISTRY.get(DEFAULT_PROVIDER)
        if provider_cls is None:
            from homeassistant.exceptions import ConfigEntryNotReady

            raise ConfigEntryNotReady(
                f"Provider '{provider_key}' not found and default provider '{DEFAULT_PROVIDER}' is also missing."
            )

    # Pass county to providers that support county_search mode.
    # Pass api_key to providers that require authentication.
    # Pass lat/lng/radius to providers that support geo-filtering.
    # Use inspect to avoid TypeError on providers whose __init__ doesn't accept these params.
    county = entry.data.get(CONF_STATION_COUNTY)
    # API key is stored in entry.options (not entry.data) for security; fall back
    # to entry.data for entries created before this change was introduced.
    # Use explicit is not None checks — empty string is a valid (invalid) key,
    # not a signal to fall through to entry.data.
    _api_key_options = entry.options.get(CONF_API_KEY)
    api_key = (
        _api_key_options
        if _api_key_options is not None
        else entry.data.get(CONF_API_KEY)
    )
    latitude = entry.data.get(CONF_LATITUDE)
    longitude = entry.data.get(CONF_LONGITUDE)
    _radius_options = entry.options.get(CONF_RADIUS_KM)
    radius_km = (
        _radius_options
        if _radius_options is not None
        else entry.data.get(CONF_RADIUS_KM)
    )
    sig = inspect.signature(provider_cls.__init__)
    kwargs: dict = {}
    if county and "county" in sig.parameters:
        kwargs["county"] = county
    # postal_code: use NEEDS_POSTAL_CODE ClassVar (inspect.signature breaks for **kwargs).
    if getattr(provider_cls, "NEEDS_POSTAL_CODE", False):
        postal_code = entry.data.get(CONF_POSTAL_CODE)
        if not postal_code and county and str(county).isdigit():
            postal_code = county
        if postal_code:
            kwargs["postal_code"] = postal_code
    # prefecture_id: for gr_fuelgov — station_id stores the prefecture numeric id.
    if "prefecture_id" in sig.parameters and station_id:
        try:
            kwargs["prefecture_id"] = int(station_id)
        except (ValueError, TypeError):
            pass
    if api_key and "api_key" in sig.parameters:
        kwargs["api_key"] = api_key
    if latitude is not None and "latitude" in sig.parameters:
        kwargs["latitude"] = latitude
    if longitude is not None and "longitude" in sig.parameters:
        kwargs["longitude"] = longitude
    if radius_km is not None and "radius_km" in sig.parameters:
        kwargs["radius_km"] = radius_km
    if kwargs:
        provider = provider_cls(station_id, **kwargs)
    else:
        provider = provider_cls(station_id)
    coordinator = FuelCompareIECoordinator(hass, provider, station_id)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when options change (e.g. radius, api_key) so the new values take effect.
    async def _reload_entry(h: HomeAssistant, e: ConfigEntry) -> None:
        await h.config_entries.async_reload(e.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
