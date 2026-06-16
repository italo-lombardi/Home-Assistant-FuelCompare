"""Device tracker platform for Fuel Compare — exposes station location on the map."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FuelCompareIECoordinator
from .helpers import _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fuel Compare device tracker based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    caps = coordinator.provider_capabilities

    if "latitude" in caps and "longitude" in caps:
        async_add_entities(
            [StationDeviceTracker(coordinator, coordinator.station_id, entry.title)]
        )


class StationDeviceTracker(CoordinatorEntity[FuelCompareIECoordinator], TrackerEntity):
    """Device tracker that pins a fuel station on the HA map."""

    _attr_has_entity_name = True
    _attr_translation_key = "station_tracker"
    _attr_icon = "mdi:gas-station"
    _attr_source_type = SourceType.GPS

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_tracker"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def latitude(self) -> float | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("latitude")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def longitude(self) -> float | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("longitude")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and self.latitude is not None
            and self.longitude is not None
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}
