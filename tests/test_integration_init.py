"""Tests for fuelcompare_ie __init__.py — platform loading logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from custom_components.fuelcompare_ie.const import (
    CONF_PROVIDER,
    CONF_SHOW_ON_MAP,
    CONF_STATION_ID,
)


def _make_entry(
    provider_key: str = "ie_fuelcompare",
    station_id: str = "123",
    options: dict | None = None,
    data_extra: dict | None = None,
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test Station"
    data = {CONF_PROVIDER: provider_key, CONF_STATION_ID: station_id}
    if data_extra:
        data.update(data_extra)
    entry.data = data
    entry.options = options or {}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=lambda: None)
    return entry


# ---------------------------------------------------------------------------
# async_setup_entry — platform list
# ---------------------------------------------------------------------------


async def test_setup_entry_device_tracker_loaded_when_show_on_map_and_lat_lon() -> None:
    """Platform.DEVICE_TRACKER included when show_on_map=True and provider has lat/lon."""
    from homeassistant.const import Platform
    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    # Use at_econtrol — has latitude + longitude in CAPABILITIES
    provider_key = "at_econtrol"
    assert {"latitude", "longitude"} <= PROVIDER_REGISTRY[provider_key].CAPABILITIES

    entry = _make_entry(
        provider_key=provider_key,
        station_id="1354901",
        options={CONF_SHOW_ON_MAP: True},
    )

    hass = MagicMock()
    hass.config = MagicMock()
    hass.data = {}

    coordinator_mock = MagicMock()
    coordinator_mock.async_config_entry_first_refresh = AsyncMock()

    forwarded_platforms: list = []

    async def capture_forward(e, platforms):
        forwarded_platforms.extend(platforms)

    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = capture_forward

    with patch(
        "custom_components.fuelcompare_ie.FuelCompareIECoordinator",
        return_value=coordinator_mock,
    ):
        await async_setup_entry(hass, entry)

    assert Platform.DEVICE_TRACKER in forwarded_platforms


async def test_setup_entry_device_tracker_not_loaded_when_show_on_map_false() -> None:
    """Platform.DEVICE_TRACKER NOT included when show_on_map=False."""
    from homeassistant.const import Platform
    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    provider_key = "at_econtrol"
    assert {"latitude", "longitude"} <= PROVIDER_REGISTRY[provider_key].CAPABILITIES

    entry = _make_entry(
        provider_key=provider_key,
        station_id="1354901",
        options={CONF_SHOW_ON_MAP: False},
    )

    hass = MagicMock()
    hass.config = MagicMock()
    hass.data = {}

    coordinator_mock = MagicMock()
    coordinator_mock.async_config_entry_first_refresh = AsyncMock()

    forwarded_platforms: list = []

    async def capture_forward(e, platforms):
        forwarded_platforms.extend(platforms)

    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = capture_forward

    with patch(
        "custom_components.fuelcompare_ie.FuelCompareIECoordinator",
        return_value=coordinator_mock,
    ):
        await async_setup_entry(hass, entry)

    assert Platform.DEVICE_TRACKER not in forwarded_platforms


async def test_setup_entry_device_tracker_not_loaded_without_lat_lon_caps() -> None:
    """Platform.DEVICE_TRACKER NOT included when provider lacks lat/lon CAPABILITIES."""
    from homeassistant.const import Platform
    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    # ie_fuelcompare has no latitude/longitude in CAPABILITIES
    provider_key = "ie_fuelcompare"
    assert "latitude" not in PROVIDER_REGISTRY[provider_key].CAPABILITIES

    entry = _make_entry(
        provider_key=provider_key,
        station_id="790",
        options={CONF_SHOW_ON_MAP: True},
    )

    hass = MagicMock()
    hass.config = MagicMock()
    hass.data = {}

    coordinator_mock = MagicMock()
    coordinator_mock.async_config_entry_first_refresh = AsyncMock()

    forwarded_platforms: list = []

    async def capture_forward(e, platforms):
        forwarded_platforms.extend(platforms)

    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = capture_forward

    with patch(
        "custom_components.fuelcompare_ie.FuelCompareIECoordinator",
        return_value=coordinator_mock,
    ):
        await async_setup_entry(hass, entry)

    assert Platform.DEVICE_TRACKER not in forwarded_platforms


async def test_unload_entry_device_tracker_unloaded_when_show_on_map() -> None:
    """async_unload_entry includes DEVICE_TRACKER when show_on_map=True + lat/lon caps."""
    from homeassistant.const import Platform
    from custom_components.fuelcompare_ie import async_unload_entry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.const import DOMAIN, CONF_SHOW_ON_MAP

    provider_key = "at_econtrol"
    entry = _make_entry(
        provider_key=provider_key,
        station_id="1354901",
        options={CONF_SHOW_ON_MAP: True},
    )

    coordinator_mock = MagicMock()
    coordinator_mock.provider_capabilities = PROVIDER_REGISTRY[
        provider_key
    ].CAPABILITIES

    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: coordinator_mock}}

    unloaded_platforms: list = []

    async def capture_unload(e, platforms):
        unloaded_platforms.extend(platforms)
        return True

    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = capture_unload

    await async_unload_entry(hass, entry)

    assert Platform.DEVICE_TRACKER in unloaded_platforms


async def test_unload_entry_coordinator_none_fallback() -> None:
    """async_unload_entry falls back to provider class caps when coordinator absent."""
    from homeassistant.const import Platform
    from custom_components.fuelcompare_ie import async_unload_entry
    from custom_components.fuelcompare_ie.const import DOMAIN, CONF_SHOW_ON_MAP

    entry = _make_entry(
        provider_key="at_econtrol",
        station_id="1354901",
        options={CONF_SHOW_ON_MAP: True},
    )

    hass = MagicMock()
    hass.data = {
        DOMAIN: {}
    }  # coordinator absent — triggers fallback to class CAPABILITIES

    unloaded_platforms: list = []

    async def capture_unload(e, platforms):
        unloaded_platforms.extend(platforms)
        return True

    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = capture_unload

    await async_unload_entry(hass, entry)

    assert Platform.DEVICE_TRACKER in unloaded_platforms
