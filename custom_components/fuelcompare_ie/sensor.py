"""Sensor platform for FuelCompare.ie integration."""
from __future__ import annotations

import json as json_lib
from datetime import datetime

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
    station_name = entry.title

    entities: list[SensorEntity] = []

    # One price sensor per fuel type
    for fuel_type in FUEL_TYPES:
        entities.append(
            FuelPriceSensor(
                coordinator=coordinator,
                station_id=station_id,
                station_name=station_name,
                fuel_type=fuel_type,
            )
        )

    # One set of station-level sensors per station
    entities.extend([
        StationBrandSensor(coordinator, station_id, station_name),
        StationCountySensor(coordinator, station_id, station_name),
        StationWorkingHoursSensor(coordinator, station_id, station_name),
        StationAboutCategorySensor(coordinator, station_id, station_name, "Accessibility", "mdi:wheelchair-accessibility"),
        StationAboutCategorySensor(coordinator, station_id, station_name, "Offerings", "mdi:store"),
        StationAboutCategorySensor(coordinator, station_id, station_name, "Amenities", "mdi:toilet"),
        StationAboutCategorySensor(coordinator, station_id, station_name, "Payments", "mdi:credit-card"),
    ])

    async_add_entities(entities)


def _device_info(station_id: str, station_name: str) -> DeviceInfo:
    """Return shared device info for all sensors of a station."""
    return DeviceInfo(
        identifiers={(DOMAIN, station_id)},
        name=station_name,
        manufacturer="FuelCompare.ie",
        entry_type=DeviceEntryType.SERVICE,
    )


# ── Fuel price sensors ────────────────────────────────────────────────────────

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
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._fuel_type = fuel_type
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{fuel_type}"
        fuel_name = fuel_type.replace("_", " ").title()
        self._attr_name = f"{station_name} {fuel_name}"
        icon_map = {
            "unleaded": "mdi:gas-station",
            "diesel": "mdi:gas-station-outline",
        }
        self._attr_icon = icon_map.get(fuel_type, "mdi:gas-station")
        self._attr_device_info = _device_info(station_id, station_name)

    @property
    def native_value(self) -> float | None:
        """Return the current fuel price."""
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
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        attrs: dict = {
            "station_id": self._station_id,
            "fuel_type": self._fuel_type,
            "source": "fuelcompare.ie",
        }
        if self.coordinator.data:
            if lastupdated := self.coordinator.data.get("lastupdated"):
                attrs["price_last_updated"] = lastupdated
        return attrs


# ── Station-level sensors ─────────────────────────────────────────────────────

class StationBrandSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station brand/chain name."""

    _attr_icon = "mdi:domain"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{station_id}_brand"
        self._attr_name = f"{station_name} Brand"
        self._attr_device_info = _device_info(station_id, station_name)

    @property
    def native_value(self) -> str | None:
        """Return the brand name."""
        if self.coordinator.data:
            tablename = self.coordinator.data.get("tablename")
            if tablename:
                return tablename.replace("_", " ").title()
        return None


class StationCountySensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station county/location."""

    _attr_icon = "mdi:map-marker"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{station_id}_county"
        self._attr_name = f"{station_name} County"
        self._attr_device_info = _device_info(station_id, station_name)

    @property
    def native_value(self) -> str | None:
        """Return the county name."""
        if self.coordinator.data:
            return self.coordinator.data.get("county")
        return None


class StationWorkingHoursSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing today's opening hours for the station."""

    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{station_id}_working_hours"
        self._attr_name = f"{station_name} Working Hours"
        self._attr_device_info = _device_info(station_id, station_name)

    @property
    def native_value(self) -> str | None:
        """Return today's opening hours."""
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return None
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            today = datetime.now().strftime("%A")
            return hours.get(today)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the full weekly schedule."""
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return {}
        try:
            return json_lib.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return {}


class StationAboutCategorySensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing one category of station facilities (e.g. Accessibility, Offerings)."""

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
        category: str,
        icon: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._category = category
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{station_id}_about_{category.lower()}"
        self._attr_name = f"{station_name} {category}"
        self._attr_device_info = _device_info(station_id, station_name)

    def _get_category_data(self) -> dict:
        """Return the parsed category dict, or empty dict on any failure."""
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.get("about")
        if not raw:
            return {}
        try:
            about = json_lib.loads(raw) if isinstance(raw, str) else raw
            return about.get(self._category) or {}
        except (ValueError, TypeError):
            return {}

    @property
    def available(self) -> bool:
        """Return True only if the category exists and has at least one entry."""
        return self.coordinator.last_update_success and bool(self._get_category_data())

    @property
    def native_value(self) -> str | None:
        """Return active features in this category as a comma-separated string."""
        data = self._get_category_data()
        if not data:
            return None
        active = [feature for feature, enabled in data.items() if enabled]
        return ", ".join(active) if active else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return all features in this category with their enabled state."""
        return self._get_category_data()
