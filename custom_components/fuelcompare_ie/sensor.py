"""Sensor platform for Fuel Compare integration."""

from __future__ import annotations

import json as json_lib
import logging
from datetime import datetime, timezone

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_ID, DOMAIN, FUEL_TYPES
from .coordinator import FuelCompareIECoordinator

_LOGGER = logging.getLogger(__name__)


def _parse_lastupdated(raw: str | None) -> datetime | None:
    """Parse lastupdated string into an aware datetime, or None on failure."""
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    # Try ISO 8601 with trailing Z (most common from the API)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            # Treat naive datetimes as UTC
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Last resort: fromisoformat (Python 3.11+ handles Z, older versions don't)
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
    entities.extend(
        [
            StationPriceLastUpdatedSensor(coordinator, station_id, station_name),
            StationNameSensor(coordinator, station_id, station_name),
            StationBrandSensor(coordinator, station_id, station_name),
            StationCountySensor(coordinator, station_id, station_name),
            StationWorkingHoursSensor(coordinator, station_id, station_name),
            StationAboutCategorySensor(
                coordinator,
                station_id,
                station_name,
                "Accessibility",
                "accessibility",
                "mdi:wheelchair-accessibility",
            ),
            StationAboutCategorySensor(
                coordinator,
                station_id,
                station_name,
                "Offerings",
                "offerings",
                "mdi:store",
            ),
            StationAboutCategorySensor(
                coordinator,
                station_id,
                station_name,
                "Amenities",
                "amenities",
                "mdi:toilet",
            ),
            StationAboutCategorySensor(
                coordinator,
                station_id,
                station_name,
                "Payments",
                "payments",
                "mdi:credit-card",
            ),
            LastSuccessfulFetchSensor(coordinator, station_id, station_name),
        ]
    )

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
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_has_entity_name = True

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
        self._attr_translation_key = fuel_type
        icon_map = {
            "unleaded": "mdi:gas-station",
            "diesel": "mdi:gas-station-outline",
        }
        self._attr_icon = icon_map.get(fuel_type, "mdi:gas-station")
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
        """Return if entity is available.

        Stale-retention behaviour: we deliberately drop the
        ``coordinator.last_update_success`` check here so the last good price
        survives transient fetch failures (site outage, throttling, network
        blips). Use ``binary_sensor.<station>_data_fetch_problem`` to detect failures.
        """
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

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_price_last_updated"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp when prices were last updated on the data source."""
        if not self.coordinator.data:
            return None
        return _parse_lastupdated(self.coordinator.data.get("lastupdated"))

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Return station_id attribute."""
        return {"station_id": self._station_id}


class StationNameSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the full station name (e.g. 'Circle K Mulhuddart')."""

    _attr_icon = "mdi:gas-station"
    _attr_has_entity_name = True
    _attr_translation_key = "station_name"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_station_name"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        """Return the full station name."""
        if self.coordinator.data:
            return self.coordinator.data.get("name")
        return None

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Return station_id attribute."""
        return {"station_id": self._station_id}


class StationBrandSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station brand/chain name."""

    _attr_icon = "mdi:domain"
    _attr_has_entity_name = True
    _attr_translation_key = "brand"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_brand"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        """Return the brand name."""
        if self.coordinator.data:
            tablename = self.coordinator.data.get("tablename")
            if tablename:
                return tablename.replace("_", " ").title()
        return None

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Return station_id attribute."""
        return {"station_id": self._station_id}


class StationCountySensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Sensor exposing the station county/location."""

    _attr_icon = "mdi:map-marker"
    _attr_has_entity_name = True
    _attr_translation_key = "county"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_county"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def native_value(self) -> str | None:
        """Return the county name."""
        if self.coordinator.data:
            return self.coordinator.data.get("county")
        return None

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Return station_id attribute."""
        return {"station_id": self._station_id}


class StationWorkingHoursSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing today's opening hours for the station."""

    _attr_icon = "mdi:clock-outline"
    _attr_has_entity_name = True
    _attr_translation_key = "working_hours"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_working_hours"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed."""
        return self.coordinator.data is not None

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
            today = dt_util.now().strftime("%A")
            return hours.get(today)
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Failed to parse working_hours for native_value: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the full weekly schedule plus station_id."""
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


class StationAboutCategorySensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Sensor exposing one category of station facilities (e.g. Accessibility, Offerings)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
        category: str,
        translation_key: str,
        icon: str,
    ) -> None:
        """Initialize the sensor."""
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
        """Return the parsed category dict, or empty dict on any failure."""
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
        """Return True only if the category exists and has at least one entry.

        Stale-retention: drop the ``last_update_success`` gate so the last
        known facility data survives transient fetch failures.
        """
        return bool(self._get_category_data())

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
        """Return all features in this category with their enabled state, plus station_id."""
        return {"station_id": self._station_id, **self._get_category_data()}


# ── Diagnostic sensors ────────────────────────────────────────────────────────


class LastSuccessfulFetchSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Diagnostic sensor exposing when the integration last fetched data successfully.

    Distinct from ``price_last_updated`` — that one reflects the data source's
    own server-side timestamp for the price record. This sensor reflects the
    integration's own poll cadence, so automations can detect "fetch loop
    has been failing for hours" independently of whether the site has refreshed
    its price record.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check"
    _attr_has_entity_name = True
    _attr_translation_key = "last_successful_fetch"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_last_successful_fetch"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        """Always available — value is None until first successful fetch."""
        return True

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last successful integration fetch."""
        return self.coordinator.last_successful_fetch

    @property
    def extra_state_attributes(self) -> dict:
        """Return station_id attribute."""
        return {"station_id": self._station_id}
