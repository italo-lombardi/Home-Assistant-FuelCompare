"""The Fuel Compare integration."""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any

from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_POSTAL_CODE,
    CONF_PROVIDER,
    CONF_RADIUS_KM,
    CONF_SHOW_ON_MAP,
    CONF_STATION_COUNTY,
    CONF_STATION_ID,
    DEFAULT_PROVIDER,
    DOMAIN,
)
from .coordinator import FuelCompareIECoordinator
from .providers import PROVIDER_REGISTRY

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fuel Compare from a config entry."""
    station_id = entry.data.get(CONF_STATION_ID, "")

    # Existing entries have no CONF_PROVIDER key — default to ie_fuelcompare
    # so they continue working without any migration.
    provider_key = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)

    # Location-mode providers (DE, FR, ES, PT, AT, IT, SI, GB, AU) have no
    # station picker in config_flow, so station_id is always "".  An empty
    # station_id produces an invalid HA entity unique_id.  Generate a stable
    # substitute from the rounded lat/lng stored in entry.data so the device
    # registry entry is stable across restarts.  Include provider_key to
    # prevent ID collisions when two providers share the same coordinates.
    #
    # Existing entries created before this code path was introduced wrote
    # ``.4f``-rounded IDs (≈11 m).  Two stations within that radius using the
    # same provider would collide, disabling the second entry's entities.
    # Widen precision to ``.5f`` (≈1.1 m) for entries that have NOT yet
    # persisted CONF_STATION_ID, then write the computed value back into
    # entry.data so subsequent restarts see the same ID.  Existing entries
    # already carrying a ``.4f`` id keep that id byte-for-byte (the
    # ``CONF_STATION_ID not in entry.data`` guard).
    if not station_id:
        _lat = entry.data.get(CONF_LATITUDE)
        _lng = entry.data.get(CONF_LONGITUDE)
        if _lat is not None and _lng is not None:
            precision = 4 if CONF_STATION_ID in entry.data else 5
            station_id = f"{provider_key}_{_lat:.{precision}f}_{_lng:.{precision}f}"
            if entry.data.get(CONF_STATION_ID) != station_id:
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_STATION_ID: station_id},
                )
    if not station_id:
        station_id = entry.entry_id

    provider_cls = PROVIDER_REGISTRY.get(provider_key)
    if provider_cls is None:
        _LOGGER.warning(
            "Unknown provider key %r, falling back to %r",
            provider_key,
            DEFAULT_PROVIDER,
        )
        provider_cls = PROVIDER_REGISTRY.get(DEFAULT_PROVIDER)
        if provider_cls is None:
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
    # Use is-not-None check — empty string ("") is a deliberate key clear and must NOT
    # fall back to entry.data.  A truthy check would revert a cleared key to the old value.
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
    kwargs: dict[str, Any] = {}
    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if county and (has_var_kwargs or "county" in sig.parameters):
        kwargs["county"] = county
    # postal_code: use NEEDS_POSTAL_CODE ClassVar (inspect.signature breaks for **kwargs).
    if getattr(provider_cls, "NEEDS_POSTAL_CODE", False):
        postal_code = entry.data.get(CONF_POSTAL_CODE)
        if (
            not postal_code
            and county
            and bool(re.fullmatch(r"\d+", str(county)))
            and CONF_POSTAL_CODE
            not in entry.data  # county key holds a numeric postal code (old entries pre-CONF_POSTAL_CODE); don't double-treat it
        ):
            postal_code = county
        if postal_code:
            kwargs["postal_code"] = postal_code
    # prefecture_id: for gr_fuelgov — station_id stores the prefecture numeric id.
    # Capture the raw entry value BEFORE any lat/lng fallback overwrites station_id.
    raw_station_id = entry.data.get(CONF_STATION_ID, "")
    if ("prefecture_id" in sig.parameters or has_var_kwargs) and raw_station_id:
        try:
            kwargs["prefecture_id"] = int(raw_station_id)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Could not parse prefecture_id from station_id %r for provider %r",
                raw_station_id,
                provider_key,
            )
    # Use is-not-None check: empty string ("") is a deliberate key clear and
    # must still be passed through rather than silently skipped.
    if api_key is not None and getattr(provider_cls, "REQUIRES_API_KEY", False):
        kwargs["api_key"] = api_key
    if latitude is not None and (has_var_kwargs or "latitude" in sig.parameters):
        kwargs["latitude"] = latitude
    if longitude is not None and (has_var_kwargs or "longitude" in sig.parameters):
        kwargs["longitude"] = longitude
    if radius_km is not None and (has_var_kwargs or "radius_km" in sig.parameters):
        kwargs["radius_km"] = radius_km
    if kwargs:
        provider = provider_cls(station_id, **kwargs)
    else:
        provider = provider_cls(station_id)
    coordinator = FuelCompareIECoordinator(
        hass, provider, station_id, config_entry=entry
    )

    hass.data.setdefault(DOMAIN, {})

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Warn users of ie_fuelcompare (fuelcompare.ie) that the service is ending.
    # These calls are placed AFTER the coordinator is stored so that if issue
    # creation triggers any synchronous listeners they can safely access the entry.
    if provider_key == "ie_fuelcompare":
        async_create_issue(
            hass,
            DOMAIN,
            f"fuelcompare_ie_deprecation_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="fuelcompare_ie_deprecation",
            translation_placeholders={"entry_title": entry.title},
        )

    # Warn users of ie_pumps that TLS certificate verification is disabled.
    if provider_key == "ie_pumps":
        async_create_issue(
            hass,
            DOMAIN,
            f"ie_pumps_tls_disabled_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="ie_pumps_tls_disabled",
            translation_placeholders={"entry_title": entry.title},
        )

    # Build platform list — always sensor + binary_sensor; add device_tracker
    # when show_on_map is enabled and the provider exposes lat/lon data.
    caps = provider.CAPABILITIES
    platforms = list(PLATFORMS)
    if (
        entry.options.get(CONF_SHOW_ON_MAP)
        and "latitude" in caps
        and "longitude" in caps
    ):
        platforms.append(Platform.DEVICE_TRACKER)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Reload when options change (e.g. radius, api_key) so the new values take effect.
    async def _reload_entry(h: HomeAssistant, e: ConfigEntry) -> None:
        e.async_schedule_reload()

    entry.async_on_unload(entry.add_update_listener(_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms_to_unload = list(PLATFORMS)
    # Use the live coordinator's caps — same source as async_setup_entry used,
    # avoids a stale re-lookup when provider_key is unknown/removed.
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        live_caps = coordinator.provider_capabilities
    else:
        provider_key = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
        provider_cls = PROVIDER_REGISTRY.get(provider_key)
        live_caps = provider_cls.CAPABILITIES if provider_cls else frozenset()
    if (
        entry.options.get(CONF_SHOW_ON_MAP)
        and "latitude" in live_caps
        and "longitude" in live_caps
    ):
        platforms_to_unload.append(Platform.DEVICE_TRACKER)
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, platforms_to_unload
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Only delete repair issues when the entry actually unloaded; if unload
        # failed the entry stays loaded and the issue should remain visible.
        async_delete_issue(hass, DOMAIN, f"fuelcompare_ie_deprecation_{entry.entry_id}")
        async_delete_issue(hass, DOMAIN, f"ie_pumps_tls_disabled_{entry.entry_id}")

    return unload_ok
