"""Binary sensor platform for Fuel Compare integration."""

from __future__ import annotations

import json as json_lib
import logging
import re
from datetime import time as dt_time

import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import FuelCompareIECoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)
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
    now = dt_util.now().time()
    if close_time < open_time:  # crosses midnight
        return now >= open_time or now < close_time
    return open_time <= now < close_time


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fuel Compare binary sensor based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id = entry.data.get(CONF_STATION_ID, "")
    station_name = entry.title
    async_add_entities(
        [
            StationIsOpenBinarySensor(coordinator, station_id, station_name),
            DataFetchProblemBinarySensor(coordinator, station_id, station_name),
        ]
    )


class StationIsOpenBinarySensor(
    CoordinatorEntity[FuelCompareIECoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether the station is currently open."""

    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_icon = "mdi:store-clock"
    _attr_has_entity_name = True
    _attr_translation_key = "is_open"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_is_open"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        """Stay available with last known data even when last fetch failed.

        Drops the ``coordinator.last_update_success`` gate so the open/closed
        state survives transient site outages — see data_fetch_problem binary sensor for
        live fetch health.
        """
        return self.coordinator.data is not None and bool(
            self.coordinator.data.get("working_hours")
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
            today = dt_util.now().strftime("%A")
            today_hours = hours.get(today)
            if today_hours is None:
                _LOGGER.debug("No working hours entry for %s", today)
                return None
            return _is_open(today_hours)
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Failed to parse working_hours for is_on: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return today's hours and station_id as attributes."""
        base = {"station_id": self._station_id}
        if not self.coordinator.data:
            return base
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return base
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            today = dt_util.now().strftime("%A")
            return {**base, "today_hours": hours.get(today)}
        except (ValueError, TypeError) as err:
            _LOGGER.debug(
                "Failed to parse working_hours for extra_state_attributes: %s", err
            )
            return base


class DataFetchProblemBinarySensor(
    CoordinatorEntity[FuelCompareIECoordinator], BinarySensorEntity
):
    """Diagnostic binary sensor exposing whether the last data fetch failed.

    State is ``on`` when there is a problem (last poll failed), ``off`` when
    the last poll succeeded. Always reports as available so automations can
    rely on it being a deterministic on/off signal — even before the first
    successful fetch (no fetch yet ⇒ problem ⇒ on).

    Pair with the stale-retention behaviour of the price/info sensors:
    those keep their last known value during outages, this one tells you
    whether the last refresh actually worked.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "data_fetch_problem"

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{DOMAIN}_{station_id}_data_fetch_problem"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator._provider.LABEL
        )

    @property
    def available(self) -> bool:
        """Always available — we want a deterministic on/off signal."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the last coordinator update FAILED (problem present)."""
        return not bool(self.coordinator.last_update_success)

    @property
    def extra_state_attributes(self) -> dict:
        """Return diagnostic context: last exception and last successful fetch."""
        c = self.coordinator
        last_exc = getattr(c, "last_exception", None)
        last_success = getattr(c, "last_successful_fetch", None)
        return {
            "station_id": self._station_id,
            "last_exception": str(last_exc) if last_exc else None,
            "last_successful_fetch": last_success.isoformat() if last_success else None,
        }
