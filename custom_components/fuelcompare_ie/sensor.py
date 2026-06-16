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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DAYS, DOMAIN, CONF_STATION_PAGE_URL
from .coordinator import FuelCompareIECoordinator
from .helpers import _device_info

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
# Maps StationData capability key → factory: (coordinator, station_id, station_name) → SensorEntity.

_INFO_SENSOR_REGISTRY: dict[str, Any] = {
    "lastupdated": lambda c, s, n: StationPriceLastUpdatedSensor(c, s, n),
    "name": lambda c, s, n: StationNameSensor(c, s, n),
    "brand": lambda c, s, n: StationBrandSensor(c, s, n),
    "county": lambda c, s, n: StationCountySensor(c, s, n),
    "working_hours": lambda c, s, n: StationWorkingHoursSensor(c, s, n),
    "opening_hours": lambda c, s, n: StationOpeningHoursSensor(c, s, n),
    "address": lambda c, s, n: StationSimpleStrSensor(
        c, s, n, "address", "mdi:map-marker", "address"
    ),
    "latitude": lambda c, s, n: StationSimpleFloatSensor(
        c, s, n, "latitude", "mdi:crosshairs-gps", "latitude"
    ),
    "longitude": lambda c, s, n: StationSimpleFloatSensor(
        c, s, n, "longitude", "mdi:crosshairs-gps", "longitude"
    ),
    "phone": lambda c, s, n: StationSimpleStrSensor(
        c, s, n, "phone", "mdi:phone", "phone"
    ),
    "website": lambda c, s, n: StationSimpleStrSensor(
        c, s, n, "website", "mdi:web", "website"
    ),
    "location": lambda c, s, n: StationSimpleStrSensor(
        c, s, n, "location", "mdi:map-marker-radius", "location"
    ),
    "price_confidence": lambda c, s, n: StationSimpleStrSensor(
        c, s, n, "price_confidence", "mdi:shield-check-outline", "price_confidence"
    ),
    "accessibility": lambda c, s, n: StationAboutCategorySensor(
        c, s, n, "Accessibility", "accessibility", "mdi:wheelchair-accessibility"
    ),
    "offerings": lambda c, s, n: StationAboutCategorySensor(
        c, s, n, "Offerings", "offerings", "mdi:store"
    ),
    "amenities": lambda c, s, n: StationAboutCategorySensor(
        c, s, n, "Amenities", "amenities", "mdi:toilet"
    ),
    "payments": lambda c, s, n: StationAboutCategorySensor(
        c, s, n, "Payments", "payments", "mdi:credit-card"
    ),
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
    station_id = coordinator.station_id
    station_name = entry.title
    caps = coordinator.provider_capabilities

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

    # Always-on diagnostic sensor (no CAPABILITIES gate, mirrors data_fetch_problem)
    entities.append(LastSuccessfulFetchSensor(coordinator, station_id, station_name))

    # Always-on identity sensors
    entities.append(ProviderLabelSensor(coordinator, station_id, station_name))
    entities.append(CountrySensor(coordinator, station_id, station_name))
    station_page_url = entry.data.get(CONF_STATION_PAGE_URL, "")
    if station_page_url:
        entities.append(
            StationPageUrlSensor(
                coordinator, station_id, station_name, station_page_url
            )
        )

    async_add_entities(entities)


# ── Fuel price sensors ────────────────────────────────────────────────────────


class FuelPriceSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Representation of a Fuel Compare fuel price sensor."""

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
        self._attr_native_unit_of_measurement = coordinator.provider_currency
        self._station_id = station_id
        self._fuel_type = fuel_type
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{fuel_type}"
        self._attr_translation_key = translation_key or fuel_type
        self._attr_icon = icon
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def native_value(self) -> float | None:
        """Return the current fuel price."""
        if self.coordinator.data:
            value = self.coordinator.data.get(self._fuel_type)
            if value is not None:
                try:
                    return round(float(value), 3)
                except (ValueError, TypeError):
                    return None
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
            "source": self.coordinator.provider_label,
        }
        if self.coordinator.data:
            if self.coordinator.data.get("lastupdated") is not None:
                parsed = _parse_lastupdated(self.coordinator.data.get("lastupdated"))
                if parsed is not None:
                    attrs["price_last_updated"] = parsed.isoformat()
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
            station_id, station_name, coordinator.provider_label
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
            station_id, station_name, coordinator.provider_label
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
            station_id, station_name, coordinator.provider_label
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
            station_id, station_name, coordinator.provider_label
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
            station_id, station_name, coordinator.provider_label
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
            today = DAYS[dt_util.now().weekday()]
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
            station_id, station_name, coordinator.provider_label
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
            phone = self.coordinator.data.get("phone")
            website = self.coordinator.data.get("website")
            if phone is not None:
                base["phone"] = phone
            if website is not None:
                base["website"] = website
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
            station_id, station_name, coordinator.provider_label
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._data_key) or None
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and bool(
            self.coordinator.data.get(self._data_key)
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationSimpleFloatSensor(
    CoordinatorEntity[FuelCompareIECoordinator], SensorEntity
):
    """Generic float sensor reading one StationData key (e.g. latitude, longitude)."""

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
            station_id, station_name, coordinator.provider_label
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
            station_id, station_name, coordinator.provider_label
        )
        self._cached_category_data: dict | None = None

    def _handle_coordinator_update(self) -> None:
        """Invalidate the category-data cache on each coordinator update."""
        self._cached_category_data = None
        super()._handle_coordinator_update()

    def _get_category_data(self) -> dict:
        cached = getattr(self, "_cached_category_data", None)
        if cached is not None:
            return cached
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.get("about")
        if raw:
            try:
                about = json_lib.loads(raw) if isinstance(raw, str) else raw
                cat = about.get(self._category)
                if cat:
                    self._cached_category_data = cat
                    return cat
            except (ValueError, TypeError) as err:
                _LOGGER.debug(
                    "Failed to parse about data for category %s: %s",
                    self._category,
                    err,
                )
        # Fall back to flat key if about dict doesn't have the category
        flat = self.coordinator.data.get(self._category)
        if isinstance(flat, dict):
            self._cached_category_data = flat
            return flat
        self._cached_category_data = {}
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
            station_id, station_name, coordinator.provider_label
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


# ── Identity / diagnostic sensors ─────────────────────────────────────────────


class ProviderLabelSensor(SensorEntity):
    """Diagnostic sensor: name of the data provider (static, set at setup)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database"
    _attr_has_entity_name = True
    _attr_translation_key = "provider_label"
    _attr_should_poll = False

    def __init__(self, coordinator, station_id, station_name) -> None:
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_provider_label"
        self._attr_native_value = coordinator.provider_label
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class CountrySensor(SensorEntity):
    """Diagnostic sensor: ISO country code of the data provider (static, set at setup)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:earth"
    _attr_has_entity_name = True
    _attr_translation_key = "country_code"
    _attr_should_poll = False

    def __init__(self, coordinator, station_id, station_name) -> None:
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_country_code"
        self._attr_native_value = coordinator.provider_country
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}


class StationPageUrlSensor(CoordinatorEntity[FuelCompareIECoordinator], SensorEntity):
    """Diagnostic sensor: URL to the station page on the provider website."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:open-in-new"
    _attr_has_entity_name = True
    _attr_translation_key = "station_page_url"

    def __init__(
        self, coordinator, station_id, station_name, station_page_url: str
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_page_url = station_page_url
        self._attr_unique_id = f"{DOMAIN}_{station_id}_station_page_url"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def available(self) -> bool:
        return bool(self._station_page_url)

    @property
    def native_value(self) -> str | None:
        return self._station_page_url or None

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}
