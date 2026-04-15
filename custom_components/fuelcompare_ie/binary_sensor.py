"""Binary sensor platform for FuelCompare.ie integration."""
from __future__ import annotations

import json as json_lib
import re
from datetime import datetime, time as dt_time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import FuelCompareIECoordinator

_TIME_RE = re.compile(r"(\d+)(?::(\d+))?\s*(a\.m\.|p\.m\.|am|pm)", re.IGNORECASE)


def _parse_time(s: str) -> dt_time | None:
    """Parse a time string like '6a.m.' or '10:30p.m.' into a time object."""
    m = _TIME_RE.search(s.strip())
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2) or 0)
    period = m.group(3).lower().replace(".", "")
    if period == "pm" and hours != 12:
        hours += 12
    elif period == "am" and hours == 12:
        hours = 0
    return dt_time(hours % 24, minutes)


def _is_open(hours_str: str) -> bool | None:
    """Return True if currently open, False if closed, None if unparseable."""
    if not hours_str:
        return None
    s = hours_str.strip().lower()
    if "24" in s:
        return True
    if "closed" in s:
        return False
    times = _TIME_RE.findall(s)
    if len(times) < 2:
        return None
    open_time = _parse_time(f"{times[0][0]}:{times[0][1] or '0'}{times[0][2]}")
    close_time = _parse_time(f"{times[1][0]}:{times[1][1] or '0'}{times[1][2]}")
    if open_time is None or close_time is None:
        return None
    now = datetime.now().time()
    if close_time <= open_time:  # crosses midnight
        return now >= open_time or now < close_time
    return open_time <= now < close_time


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FuelCompare.ie binary sensor based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id = entry.data[CONF_STATION_ID]
    station_name = entry.title
    async_add_entities([
        StationIsOpenBinarySensor(coordinator, station_id, station_name),
    ])


class StationIsOpenBinarySensor(CoordinatorEntity[FuelCompareIECoordinator], BinarySensorEntity):
    """Binary sensor indicating whether the station is currently open."""

    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_icon = "mdi:store-clock"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{station_id}_is_open"
        self._attr_name = f"{station_name} Is Open"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=station_name,
            manufacturer="FuelCompare.ie",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the station is currently open."""
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return None
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            today = datetime.now().strftime("%A")
            today_hours = hours.get(today)
            if today_hours is None:
                return None
            return _is_open(today_hours)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return today's hours as an attribute."""
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return {}
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            today = datetime.now().strftime("%A")
            return {"today_hours": hours.get(today)}
        except (ValueError, TypeError):
            return {}
