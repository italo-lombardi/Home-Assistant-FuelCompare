"""Sensor platform for Fuel Compare integration."""

from __future__ import annotations

import json as json_lib
import logging
from datetime import datetime, timezone
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import FuelCompareIECoordinator

_LOGGER = logging.getLogger(__name__)

# ── Price sensor registry ─────────────────────────────────────────────────────
#
# Maps StationData capability key → (translation_key, icon).
# async_setup_entry iterates this and creates a FuelPriceSensor for every key
# present in coordinator._provider.CAPABILITIES. All existing unique_ids are
# preserved because the translation_key matches the old sensor name.

_PRICE_SENSOR_REGISTRY: dict[str, tuple[str, str]] = {
    "unleaded": ("unleaded", "mdi:gas-station"),
    "petrol": ("petrol", "mdi:gas-station"),
    "diesel": ("diesel", "mdi:gas-station-outline"),
    "kerosene": ("kerosene", "mdi:fire"),
    "cng": ("cng", "mdi:molecule-co2"),
    "lpg": ("lpg", "mdi:propane-tank"),
    "e10": ("e10", "mdi:leaf"),
    "e85": ("e85", "mdi:leaf"),
    "premium_unleaded": ("premium_unleaded", "mdi:gas-station"),
    "premium_diesel": ("premium_diesel", "mdi:gas-station-outline"),
    "adblue": ("adblue", "mdi:water"),
}

# ── Info sensor registry ──────────────────────────────────────────────────────
#
# Maps StationData capability key → factory that returns a SensorEntity.
# Factories are callables: (coordinator, station_id, station_name) → SensorEntity.


def _make_price_last_updated(coord, sid, sname):
    return StationPriceLastUpdatedSensor(coord, sid, sname)


def _make_station_name(coord, sid, sname):
    return StationNameSensor(coord, sid, sname)


def _make_brand(coord, sid, sname):
    return StationBrandSensor(coord, sid, sname)


def _make_county(coord, sid, sname):
    return StationCountySensor(coord, sid, sname)


def _make_working_hours(coord, sid, sname):
    return StationWorkingHoursSensor(coord, sid, sname)


def _make_opening_hours(coord, sid, sname):
    return StationOpeningHoursSensor(coord, sid, sname)


def _make_address(coord, sid, sname):
    return StationSimpleStrSensor(
        coord, sid, sname, "address", "mdi:map-marker", "address"
    )


def _make_latitude(coord, sid, sname):
    return StationSimpleFloatSensor(
        coord, sid, sname, "latitude", "mdi:crosshairs-gps", "latitude"
    )


def _make_longitude(coord, sid, sname):
    return StationSimpleFloatSensor(
        coord, sid, sname, "longitude", "mdi:crosshairs-gps", "longitude"
    )


def _make_phone(coord, sid, sname):
    return StationSimpleStrSensor(coord, sid, sname, "phone", "mdi:phone", "phone")


def _make_website(coord, sid, sname):
    return StationSimpleStrSensor(coord, sid, sname, "website", "mdi:web", "website")


def _make_location(coord, sid, sname):
    return StationSimpleStrSensor(
        coord, sid, sname, "location", "mdi:map-marker-radius", "location"
    )


def _make_price_confidence(coord, sid, sname):
    return StationSimpleStrSensor(
        coord,
        sid,
        sname,
        "price_confidence",
        "mdi:shield-check-outline",
        "price_confidence",
    )


def _make_accessibility(coord, sid, sname):
    return StationAboutCategorySensor(
        coord,
        sid,
        sname,
        "Accessibility",
        "accessibility",
        "mdi:wheelchair-accessibility",
    )


def _make_offerings(coord, sid, sname):
    return StationAboutCategorySensor(
        coord, sid, sname, "Offerings", "offerings", "mdi:store"
    )


def _make_amenities(coord, sid, sname):
    return StationAboutCategorySensor(
        coord, sid, sname, "Amenities", "amenities", "mdi:toilet"
    )


def _make_payments(coord, sid, sname):
    return StationAboutCategorySensor(
        coord, sid, sname, "Payments", "payments", "mdi:credit-card"
    )


def _make_last_successful_fetch(coord, sid, sname):
    return LastSuccessfulFetchSensor(coord, sid, sname)


_INFO_SENSOR_REGISTRY: dict[str, Any] = {
    "lastupdated": _make_price_last_updated,
    "name": _make_station_name,
    "brand": _make_brand,
    "county": _make_county,
    "working_hours": _make_working_hours,
    "opening_hours": _make_opening_hours,
    "address": _make_address,
    "latitude": _make_latitude,
    "longitude": _make_longitude,
    "phone": _make_phone,
    "website": _make_website,
    "location": _make_location,
    "price_confidence": _make_price_confidence,
    "accessibility": _make_accessibility,
    "offerings": _make_offerings,
    "amenities": _make_amenities,
    "payments": _make_payments,
    "last_successful_fetch": _make_last_successful_fetch,
}


def _parse_lastupdated(raw: str | None) -> datetime | None:
    """Parse lastupdated string into an aware datetime, or None on failure."""
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        pass
    _LOGGER.debug("Could not parse lastupdated value: %r", raw)
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fuel Compare sensor based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id = entry.data.get(CONF_STATION_ID, "")
    station_name = entry.title
    caps = coordinator._provider.CAPABILITIES

    entities: list[SensorEntity] = []

    # Price sensors — one per fuel type declared in CAPABILITIES
    for fuel_key, (trans_key, icon) in _PRICE_SENSOR_REGISTRY.items():
        if fuel_key in caps:
            entities.append(
                FuelPriceSensor(
                    coordinator=coordinator,
                    station_id=station_id,
                    station_name=station_name,
                    fuel_type=fuel_key,
                    translation_key=trans_key,
                    icon=icon,
                )
            )

    # Info sensors — one per capability key declared in CAPABILITIES
    for cap_key, factory in _INFO_SENSOR_REGISTRY.items():
        if cap_key in caps:
            entities.append(factory(coordinator, station_id, station_name))

    async_add_entities(entities)


def _device_info(station_id: str, station_name: str, manufacturer: str) -> DeviceInfo:
    """Return shared device info for all sensors of a station."""
    return DeviceInfo(
        identifiers={(DOMAIN, station_id)},
        name=station_name,
        manufacturer=manufacturer,
        entry_type=DeviceEntryType.SERVICE,
    )


# ── Fuel price sensors ────────────────────────────────────────────────────────


class FuelPriceSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Representation of a Fuel Compare fuel price sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
        fuel_type: str,
        translation_key: str | None = None,
        icon: str = "mdi:gas-station",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_native_unit_of_measurement = coordinator._provider.CURRENCY
        self._station_id = station_id
        self._fuel_type = fuel_type
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{fuel_type}"
        self._attr_translation_key = translation_key or fuel_type
        self._attr_icon = icon
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

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
        """Stale-retention: stay available as long as we have any data."""
        return (
            self.coordinator.data is not None
            and self._fuel_type in self.coordinator.data
            and self.coordinator.data[self._fuel_type] is not None
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        attrs: dict = {
            "station_id": self._station_id,
            "fuel_type": self._fuel_type,
            "source": self.coordinator._provider.LABEL,
        }
        if self.coordinator.data:
            if lastupdated := self.coordinator.data.get("lastupdated"):
                attrs["price_last_updated"] = lastupdated
        return attrs


# ── Station-level sensors ─────────────────────────────────────────────────────


class StationPriceLastUpdatedSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing when fuel prices were last updated on the data source."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_has_entity_name = True
    _attr_translation_key = "price_last_updated"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_price_last_updated"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> datetime | None:
        if not self.coordinator.data:
            return None
        return _parse_lastupdated(self.coordinator.data.get("lastupdated"))

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationNameSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the full station name."""

    _attr_icon = "mdi:gas-station"
    _attr_has_entity_name = True
    _attr_translation_key = "station_name"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_station_name"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("name")
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationBrandSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station brand/chain name."""

    _attr_icon = "mdi:domain"
    _attr_has_entity_name = True
    _attr_translation_key = "brand"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_brand"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        # Prefer "brand" (FuelFinder.ie), fall back to "tablename" (fuelcompare.ie)
        brand = self.coordinator.data.get("brand")
        if brand:
            return brand
        tablename = self.coordinator.data.get("tablename")
        if tablename:
            return tablename.replace("_", " ").title()
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationCountySensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station county/location."""

    _attr_icon = "mdi:map-marker"
    _attr_has_entity_name = True
    _attr_translation_key = "county"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_county"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("county")
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationWorkingHoursSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing today's opening hours (fuelcompare.ie JSON dict format)."""

    _attr_icon = "mdi:clock-outline"
    _attr_has_entity_name = True
    _attr_translation_key = "working_hours"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_working_hours"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return None
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            today = dt_util.now().strftime("%A")
            return hours.get(today)
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Failed to parse working_hours for native_value: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {"station_id": self._station_id}
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return {"station_id": self._station_id}
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            return {"station_id": self._station_id, **hours}
        except (ValueError, TypeError) as err:
            _LOGGER.debug(
                "Failed to parse working_hours for extra_state_attributes: %s", err
            )
            return {"station_id": self._station_id}


class StationOpeningHoursSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing opening hours in OSM format (FuelFinder.ie string format)."""

    _attr_icon = "mdi:clock-outline"
    _attr_has_entity_name = True
    _attr_translation_key = "opening_hours"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_opening_hours"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("opening_hours") or None

    @property
    def extra_state_attributes(self) -> dict:
        base = {"station_id": self._station_id}
        if self.coordinator.data:
            base["phone"] = self.coordinator.data.get("phone")
            base["website"] = self.coordinator.data.get("website")
        return base


class StationSimpleStrSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Generic string sensor reading one StationData key."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        station_id: str,
        station_name: str,
        data_key: str,
        icon: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._data_key = data_key
        self._attr_icon = icon
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{data_key}"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._data_key) or None
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationSimpleFloatSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Generic float sensor reading one StationData key (e.g. latitude, longitude)."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator,
        station_id: str,
        station_name: str,
        data_key: str,
        icon: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._data_key = data_key
        self._attr_icon = icon
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{data_key}"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get(self._data_key)
            if val is not None:
                try:
                    return round(float(val), 6)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationAboutCategorySensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing one category of station facilities (legacy fuelcompare.ie format)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        station_id: str,
        station_name: str,
        category: str,
        translation_key: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._category = category
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{station_id}_about_{category.lower()}"
        self._attr_translation_key = translation_key
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    def _get_category_data(self) -> dict:
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.get("about")
        if not raw:
            return {}
        try:
            about = json_lib.loads(raw) if isinstance(raw, str) else raw
            return about.get(self._category) or {}
        except (ValueError, TypeError) as err:
            _LOGGER.debug(
                "Failed to parse about data for category %s: %s", self._category, err
            )
            return {}

    @property
    def available(self) -> bool:
        return bool(self._get_category_data())

    @property
    def native_value(self) -> str | None:
        data = self._get_category_data()
        if not data:
            return None
        active = [feature for feature, enabled in data.items() if enabled]
        return ", ".join(active) if active else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id, **self._get_category_data()}


# ── Diagnostic sensors ────────────────────────────────────────────────────────


class LastSuccessfulFetchSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Diagnostic sensor: timestamp of last successful integration fetch."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check"
    _attr_has_entity_name = True
    _attr_translation_key = "last_successful_fetch"

    def __init__(self, coordinator, station_id, station_name) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_last_successful_fetch"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_successful_fetch

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}
