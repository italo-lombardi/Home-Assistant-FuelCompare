"""Tests for fuelcompare_ie __init__.py — platform loading logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from custom_components.fuelcompare_ie.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
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


# ---------------------------------------------------------------------------
# Location-mode station_id derivation — collision avoidance
# ---------------------------------------------------------------------------


async def _run_setup_capturing_station_id(entry: MagicMock) -> tuple[MagicMock, str]:
    """Drive async_setup_entry far enough to capture the station_id passed to coordinator.

    Returns (hass, station_id_seen_by_coordinator).
    """
    from custom_components.fuelcompare_ie import async_setup_entry

    hass = MagicMock()
    hass.config = MagicMock()
    hass.data = {}

    coordinator_mock = MagicMock()
    coordinator_mock.async_config_entry_first_refresh = AsyncMock()

    async def capture_forward(e, platforms):
        return None

    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = capture_forward
    hass.config_entries.async_update_entry = MagicMock()

    captured: dict[str, str] = {}

    def _factory(_hass, _provider, station_id="", config_entry=None):
        captured["station_id"] = station_id
        return coordinator_mock

    with patch(
        "custom_components.fuelcompare_ie.FuelCompareIECoordinator",
        side_effect=_factory,
    ):
        await async_setup_entry(hass, entry)

    return hass, captured["station_id"]


async def test_location_mode_persisted_station_id_short_circuits_at_outer_guard() -> (
    None
):
    """An entry with any truthy persisted station_id (legacy .4f, .5f, manual ID) must keep it byte-for-byte.

    Backward compatibility is provided by the outer ``if not station_id:``
    guard, which short-circuits the whole derivation branch. The precision
    widening only ever applies to entries that have never persisted a
    station_id — for existing entries the persisted value is preserved
    without modification, regardless of its precision.
    """
    provider_key = "at_econtrol"
    persisted_id = f"{provider_key}_53.3454_-6.2480"

    entry = _make_entry(
        provider_key=provider_key,
        station_id=persisted_id,
        data_extra={CONF_LATITUDE: 53.34540123, CONF_LONGITUDE: -6.24801234},
    )

    hass, seen_station_id = await _run_setup_capturing_station_id(entry)

    assert seen_station_id == persisted_id
    # Outer guard short-circuited — no update_entry call.
    hass.config_entries.async_update_entry.assert_not_called()


async def test_location_mode_new_entry_uses_5f_precision_and_persists() -> None:
    """A fresh entry with no persisted station_id must be assigned a .5f-precision id.

    The computed value must be written back into entry.data via
    async_update_entry so subsequent restarts see the same id.
    """
    provider_key = "at_econtrol"

    entry = _make_entry(
        provider_key=provider_key,
        station_id="",
        data_extra={CONF_LATITUDE: 53.34540123, CONF_LONGITUDE: -6.24801234},
    )
    # Strip CONF_STATION_ID entirely so the precision-widening guard fires.
    entry.data = {
        CONF_PROVIDER: provider_key,
        CONF_LATITUDE: 53.34540123,
        CONF_LONGITUDE: -6.24801234,
    }

    hass, seen_station_id = await _run_setup_capturing_station_id(entry)

    expected = f"{provider_key}_53.34540_-6.24801"
    assert seen_station_id == expected
    # Persisted back into entry.data.
    hass.config_entries.async_update_entry.assert_called_once()
    _, kwargs = hass.config_entries.async_update_entry.call_args
    assert kwargs["data"][CONF_STATION_ID] == expected


async def test_location_mode_two_close_stations_get_distinct_ids() -> None:
    """Two new entries within ≈11 m of each other must produce distinct station_ids.

    Pre-fix .4f rounding (≈11 m) collided here; .5f (≈1.1 m) keeps them apart.
    """
    provider_key = "at_econtrol"

    entry_a = _make_entry(provider_key=provider_key, station_id="")
    entry_a.data = {
        CONF_PROVIDER: provider_key,
        CONF_LATITUDE: 53.34541,
        CONF_LONGITUDE: -6.24800,
    }
    entry_b = _make_entry(provider_key=provider_key, station_id="")
    entry_b.data = {
        CONF_PROVIDER: provider_key,
        CONF_LATITUDE: 53.34549,
        CONF_LONGITUDE: -6.24800,
    }

    _, id_a = await _run_setup_capturing_station_id(entry_a)
    _, id_b = await _run_setup_capturing_station_id(entry_b)

    assert id_a != id_b
