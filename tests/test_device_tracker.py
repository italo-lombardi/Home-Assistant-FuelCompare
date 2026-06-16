"""Tests for the Fuel Compare device_tracker platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.fuelcompare_ie.device_tracker import (
    StationDeviceTracker,
    async_setup_entry,
)
from custom_components.fuelcompare_ie.const import DOMAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(data: dict | None, caps: frozenset = frozenset()) -> MagicMock:
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    coord.provider_label = "TestProvider"
    coord.provider_capabilities = caps
    coord.station_id = "abc"
    return coord


def _make_tracker(data: dict | None) -> StationDeviceTracker:
    coord = _make_coordinator(data)
    tracker = object.__new__(StationDeviceTracker)
    object.__setattr__(tracker, "coordinator", coord)
    object.__setattr__(tracker, "_station_id", "abc")
    return tracker


# ---------------------------------------------------------------------------
# StationDeviceTracker properties
# ---------------------------------------------------------------------------


def test_latitude_returns_float() -> None:
    """latitude property returns float from coordinator data."""
    tracker = _make_tracker({"latitude": 53.345, "longitude": -6.278})
    assert tracker.latitude == 53.345


def test_longitude_returns_float() -> None:
    """longitude property returns float from coordinator data."""
    tracker = _make_tracker({"latitude": 53.345, "longitude": -6.278})
    assert tracker.longitude == -6.278


def test_latitude_none_when_no_data() -> None:
    """latitude returns None when coordinator.data is None."""
    tracker = _make_tracker(None)
    assert tracker.latitude is None


def test_longitude_none_when_no_data() -> None:
    """longitude returns None when coordinator.data is None."""
    tracker = _make_tracker(None)
    assert tracker.longitude is None


def test_latitude_none_when_key_missing() -> None:
    """latitude returns None when key absent from data."""
    tracker = _make_tracker({"diesel": 1.65})
    assert tracker.latitude is None


def test_latitude_none_on_invalid_value() -> None:
    """latitude returns None when value cannot be coerced to float."""
    tracker = _make_tracker({"latitude": "not_a_number", "longitude": 1.0})
    assert tracker.latitude is None


def test_longitude_none_on_invalid_value() -> None:
    """longitude returns None when value cannot be coerced to float."""
    tracker = _make_tracker({"latitude": 1.0, "longitude": "bad"})
    assert tracker.longitude is None


def test_available_true_when_coords_present() -> None:
    """available is True when both lat and lon are present."""
    tracker = _make_tracker({"latitude": 53.0, "longitude": -6.0})
    assert tracker.available is True


def test_available_false_when_no_data() -> None:
    """available is False when coordinator.data is None."""
    tracker = _make_tracker(None)
    assert tracker.available is False


def test_available_false_when_lat_missing() -> None:
    """available is False when latitude is absent."""
    tracker = _make_tracker({"longitude": -6.0})
    assert tracker.available is False


def test_extra_state_attributes_contains_station_id() -> None:
    """extra_state_attributes includes station_id."""
    tracker = _make_tracker({"latitude": 1.0, "longitude": 2.0})
    assert tracker.extra_state_attributes == {"station_id": "abc"}


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_setup_entry_creates_tracker_when_lat_lon_in_caps() -> None:
    """async_setup_entry creates StationDeviceTracker when lat+lon in CAPABILITIES."""
    coord = _make_coordinator(
        {"latitude": 53.0, "longitude": -6.0},
        caps=frozenset({"latitude", "longitude", "diesel"}),
    )
    entry = MagicMock()
    entry.title = "Test Station"
    entry.entry_id = "x1"

    hass = MagicMock()
    hass.data = {DOMAIN: {"x1": coord}}

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 1
    assert isinstance(added[0], StationDeviceTracker)


async def test_setup_entry_no_tracker_without_lat_lon_caps() -> None:
    """async_setup_entry creates no entities when lat+lon not in CAPABILITIES."""
    coord = _make_coordinator(
        {"diesel": 1.65},
        caps=frozenset({"diesel"}),
    )
    entry = MagicMock()
    entry.title = "Test Station"
    entry.entry_id = "x2"

    hass = MagicMock()
    hass.data = {DOMAIN: {"x2": coord}}

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert added == []


async def test_setup_entry_no_tracker_when_only_lat_in_caps() -> None:
    """async_setup_entry creates no tracker when only latitude (not longitude) in caps."""
    coord = _make_coordinator(
        {"latitude": 53.0},
        caps=frozenset({"latitude"}),
    )
    entry = MagicMock()
    entry.title = "Test Station"
    entry.entry_id = "x3"

    hass = MagicMock()
    hass.data = {DOMAIN: {"x3": coord}}

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert added == []
