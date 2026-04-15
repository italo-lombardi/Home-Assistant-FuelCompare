"""Sensor platform for FuelCompare.ie integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_STATION_ID, DOMAIN, FUEL_TYPES
from .coordinator import FuelCompareIECoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FuelCompare.ie sensor based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id = entry.data[CONF_STATION_ID]
    station_name = entry.title  # Use the entry title (custom name or default)

    # Create a sensor for each fuel type
    entities = []
    for fuel_type in FUEL_TYPES:
        entities.append(
            FuelPriceSensor(
                coordinator=coordinator,
                station_id=station_id,
                station_name=station_name,
                fuel_type=fuel_type,
                entry_id=entry.entry_id,
            )
        )

    async_add_entities(entities)


class FuelPriceSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Representation of a FuelCompare.ie fuel price sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = CURRENCY_EURO

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
        fuel_type: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_name = station_name
        self._fuel_type = fuel_type
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{fuel_type}"

        # Create friendly name
        fuel_name = fuel_type.replace("_", " ").title()
        self._attr_name = f"{station_name} {fuel_name}"

        # Set standard MDI icon based on fuel type
        icon_map = {
            "unleaded": "mdi:gas-station",
            "diesel": "mdi:gas-station-outline",
        }
        self._attr_icon = icon_map.get(fuel_type, "mdi:gas-station")

        # Set up device info to group sensors
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=station_name,
            manufacturer="FuelCompare.ie",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data:
            value = self.coordinator.data.get(self._fuel_type)
            if value is not None:
                return round(value, 3)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self._fuel_type in self.coordinator.data
            and self.coordinator.data[self._fuel_type] is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return additional attributes."""
        attrs = {
            "station_id": self._station_id,
            "fuel_type": self._fuel_type,
            "source": "fuelcompare.ie",
        }
        if self.coordinator.data:
            lastupdated = self.coordinator.data.get("lastupdated")
            if lastupdated:
                attrs["price_last_updated"] = lastupdated
        return attrs
