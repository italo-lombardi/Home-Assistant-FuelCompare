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

from .const import DAYS, DOMAIN
from .coordinator import FuelCompareIECoordinator
from .helpers import _device_info

_LOGGER = logging.getLogger(__name__)
_TIME_RE = re.compile(r"(\d+)(?::(\d+))?\s*(a\.m\.|p\.m\.|am|pm)", re.IGNORECASE)
_OSM_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_OSM_DAY_MAP = {
    "mo": 0,
    "tu": 1,
    "we": 2,
    "th": 3,
    "fr": 4,
    "sa": 5,
    "su": 6,
}

# ── Facility binary sensor registry ──────────────────────────────────────────
#
# Maps StationData capability key → (translation_key, icon, device_class|None).
# async_setup_entry creates a FacilityBinarySensor for every key present in
# coordinator._provider.CAPABILITIES. is_open and data_fetch_problem are
# always created regardless of CAPABILITIES (coordinator-managed).

_FACILITY_BINARY_SENSOR_REGISTRY: dict[
    str, tuple[str, str, BinarySensorDeviceClass | None]
] = {
    "has_price": ("has_price", "mdi:currency-eur", None),
    "has_car_wash": ("has_car_wash", "mdi:car-wash", None),
    "has_shop": ("has_shop", "mdi:shopping", None),
    "has_toilet": ("has_toilet", "mdi:toilet", None),
    "has_atm": ("has_atm", "mdi:atm", None),
    "has_disabled_access": (
        "has_disabled_access",
        "mdi:wheelchair-accessibility",
        None,
    ),
    "has_electric_charging": ("has_electric_charging", "mdi:ev-station", None),
    "accepts_cash": ("accepts_cash", "mdi:cash", None),
    "accepts_cards": ("accepts_cards", "mdi:credit-card", None),
    "accepts_contactless": ("accepts_contactless", "mdi:contactless-payment", None),
}


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
    if hours > 23:
        return None
    return dt_time(hours, minutes)


def _is_open(hours_str: str) -> bool | None:
    """Return True if currently open, False if closed, None if unparseable.

    Handles two formats:
    - fuelcompare.ie: '6a.m.-10p.m.' (am/pm style)
    - FuelFinder.ie / OSM: 'Mo-Su 07:00-23:00' or '24/7'
    """
    if not hours_str:
        return None
    s = hours_str.strip().lower()
    if "24/7" in s or "24 hours" in s:
        return True
    if s == "closed":
        return False

    # Try OSM format first: 'Mo-Su 07:00-23:00', 'Mo-Fr 08:00-20:00; Sa 09:00-18:00'
    osm_result = _is_open_osm(s)
    if osm_result is not None:
        return osm_result

    # Fall back to am/pm format
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


def _is_open_osm(hours_str: str) -> bool | None:
    """Parse OSM opening_hours string and return open/closed status, or None."""
    # OSM format: 'mo-su 07:00-23:00' or 'mo-fr 08:00-20:00; sa 09:00-18:00'
    if not _OSM_TIME_RE.search(hours_str):
        return None

    now = dt_util.now()
    today_idx = now.weekday()  # 0=Monday ... 6=Sunday

    any_valid_window_for_today = False
    for rule in hours_str.split(";"):
        rule = rule.strip()
        # Check if today is covered by this rule's day range
        day_part = rule.split()[0] if rule.split() else ""
        # Handle explicit "closed" or "off" keyword (e.g. "Mo closed", "Sa-Su off")
        if "closed" in rule.lower() or re.search(r"\boff\b", rule.lower()):
            if _day_matches(day_part, today_idx):
                return False
            continue
        # Extract time range
        times = _OSM_TIME_RE.findall(rule)
        if len(times) < 2:
            continue

        # Check if today is covered by this rule's day range
        if _day_matches(day_part, today_idx):
            now_time = now.time()
            # Iterate over ALL HH:MM pairs in the rule (handles multiple windows)
            for i in range(0, len(times) - 1, 2):
                try:
                    open_h, open_m = int(times[i][0]), int(times[i][1])
                    close_h, close_m = int(times[i + 1][0]), int(times[i + 1][1])
                    was_24_close = close_h == 24
                    if open_h == 24:
                        open_h = 0
                    if close_h == 24:
                        close_h = 0
                    open_time = dt_time(open_h, open_m)
                    close_time = dt_time(close_h, close_m)
                except ValueError:
                    continue
                # Successfully parsed a time window for today
                any_valid_window_for_today = True
                # Guard fires only for the literal "00:00-24:00" pattern (was_24_close),
                # which normalises to 00:00-00:00 and means "open all day". A genuine
                # "00:00-00:00" window (zero-duration) must NOT be treated as always-open.
                if open_time == close_time == dt_time(0, 0) and was_24_close:
                    return True
                if close_time <= open_time:  # crosses midnight
                    if now_time >= open_time or now_time < close_time:
                        return True
                elif open_time <= now_time < close_time:
                    return True

    if any_valid_window_for_today:
        return (
            False  # day matched, valid windows parsed, but none contained now → closed
        )
    return None  # no matching rule for today (or all windows had parse errors)


def _day_matches(day_spec: str, today_idx: int) -> bool:
    """Return True if today_idx (0=Mon..6=Sun) is in the OSM day spec."""
    if not day_spec:
        return True  # no day spec → applies all days
    # Handle comma-separated lists like 'Tu-Th,Sa'
    for segment in day_spec.lower().split(","):
        segment = segment.strip()
        if not segment:
            continue
        # L-06: segment looks like a time token (e.g. "07:30") → no day restriction
        if re.match(r"^\d{1,2}:\d{2}", segment):
            return True
        # Handle ranges like 'mo-su', 'mo-fr'
        parts = segment.split("-")
        if len(parts) == 2:
            start = _OSM_DAY_MAP.get(parts[0][:2])
            end = _OSM_DAY_MAP.get(parts[1][:2])
            if start is not None and end is not None:
                if start <= end:
                    if start <= today_idx <= end:
                        return True
                else:
                    # wraps: e.g. 'fr-mo'
                    if today_idx >= start or today_idx <= end:
                        return True
        elif len(parts) == 1:
            day = _OSM_DAY_MAP.get(parts[0][:2])
            if day is not None and today_idx == day:
                return True
    return False  # unparseable or no matching segment


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fuel Compare binary sensor based on a config entry."""
    coordinator: FuelCompareIECoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id = coordinator.station_id
    station_name = entry.title
    caps = coordinator.provider_capabilities

    # is_open: created when provider declares "is_open", "working_hours", or
    # "opening_hours" — any hours capability implies we can derive open/closed.
    # data_fetch_problem: always created (coordinator-managed).
    entities: list[BinarySensorEntity] = [
        DataFetchProblemBinarySensor(coordinator, station_id, station_name),
    ]
    _HOURS_CAPS = {"is_open", "working_hours", "opening_hours"}
    if caps & _HOURS_CAPS:
        entities.insert(
            0, StationIsOpenBinarySensor(coordinator, station_id, station_name)
        )

    # Facility binary sensors — one per capability key in registry
    for cap_key, (
        trans_key,
        icon,
        device_class,
    ) in _FACILITY_BINARY_SENSOR_REGISTRY.items():
        if cap_key in caps:
            entities.append(
                FacilityBinarySensor(
                    coordinator,
                    station_id,
                    station_name,
                    cap_key,
                    trans_key,
                    icon,
                    device_class,
                )
            )

    async_add_entities(entities)


class StationIsOpenBinarySensor(
    CoordinatorEntity[FuelCompareIECoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether the station is currently open."""

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
            station_id, station_name, coordinator.provider_label
        )

    @property
    def available(self) -> bool:
        """Available when coordinator has data and any hours field is populated."""
        if not self.coordinator.data:
            return False
        return bool(
            self.coordinator.data.get("working_hours")
            or self.coordinator.data.get("opening_hours")
            or self.coordinator.data.get("is_open") is not None
        )

    def _get_today_hours_str(self) -> str | None:
        """Return today's hours string from whichever format the provider uses."""
        if not self.coordinator.data:
            return None
        # Try OSM opening_hours first (plain string, e.g. "Mo-Su 07:00-23:00")
        osm = self.coordinator.data.get("opening_hours")
        if osm and isinstance(osm, str):
            return osm
        # Fall back to fuelcompare.ie working_hours (JSON dict)
        raw = self.coordinator.data.get("working_hours")
        if not raw:
            return None
        try:
            hours = json_lib.loads(raw) if isinstance(raw, str) else raw
            return hours.get(DAYS[dt_util.now().weekday()])
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Failed to parse working_hours: %s", err)
            return None

    @property
    def is_on(self) -> bool | None:
        """Return True if the station is currently open."""
        direct = self.coordinator.data.get("is_open") if self.coordinator.data else None
        if direct is not None:
            return bool(direct)
        today_hours = self._get_today_hours_str()
        if today_hours is None:
            return None
        return _is_open(today_hours)

    @property
    def extra_state_attributes(self) -> dict:
        """Return today's hours and station_id as attributes."""
        base = {"station_id": self._station_id}
        today_hours = self._get_today_hours_str()
        if today_hours is not None:
            base["today_hours"] = today_hours
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
            station_id, station_name, coordinator.provider_label
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


# ── Facility binary sensors ───────────────────────────────────────────────────


class FacilityBinarySensor(
    CoordinatorEntity[FuelCompareIECoordinator], BinarySensorEntity
):
    """Generic binary sensor for flat boolean facility capabilities.

    Reads one StationData key (e.g. 'has_car_wash') and exposes it as a
    binary sensor. Created only when the key is present in the provider's
    CAPABILITIES frozenset.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FuelCompareIECoordinator,
        station_id: str,
        station_name: str,
        cap_key: str,
        translation_key: str,
        icon: str,
        device_class: BinarySensorDeviceClass | None,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._cap_key = cap_key
        self._attr_icon = icon
        self._attr_translation_key = translation_key
        self._attr_device_class = device_class
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{cap_key}"
        self._attr_device_info = _device_info(
            station_id, station_name, coordinator.provider_label
        )

    @property
    def available(self) -> bool:
        """Available when coordinator has data and key is present and non-None."""
        return (
            self.coordinator.data is not None
            and self.coordinator.data.get(self._cap_key) is not None
        )

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get(self._cap_key)
        if val is None:
            return None
        return bool(val)

    @property
    def extra_state_attributes(self) -> dict:
        return {"station_id": self._station_id}
