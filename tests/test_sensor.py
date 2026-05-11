"""Tests for FuelCompare.ie sensor platform."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.fuelcompare_ie.sensor import (
    FuelPriceSensor,
    StationAboutCategorySensor,
    StationWorkingHoursSensor,
    _parse_lastupdated,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(data: dict | None, last_update_success: bool = True) -> MagicMock:
    """Return a minimal MagicMock that looks enough like a coordinator."""
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = last_update_success
    return coord


def _make_fuel_sensor(
    fuel_type: str = "unleaded",
    data: dict | None = None,
    last_update_success: bool = True,
) -> FuelPriceSensor:
    """Return a FuelPriceSensor with a mocked coordinator, bypassing HA init."""
    coord = _make_coordinator(data, last_update_success)
    # CoordinatorEntity.__init__ requires a real HA object; bypass with object.__setattr__.
    sensor = object.__new__(FuelPriceSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(sensor, "_fuel_type", fuel_type)
    object.__setattr__(sensor, "_attr_name", f"Test Station {fuel_type}")
    object.__setattr__(sensor, "_attr_unique_id", f"fuelcompare_ie_12345_{fuel_type}")
    return sensor


def _make_working_hours_sensor(data: dict | None) -> StationWorkingHoursSensor:
    """Return a StationWorkingHoursSensor with a mocked coordinator."""
    coord = _make_coordinator(data)
    sensor = object.__new__(StationWorkingHoursSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(sensor, "_attr_name", "Test Station Working Hours")
    object.__setattr__(sensor, "_attr_unique_id", "fuelcompare_ie_12345_working_hours")
    return sensor


def _make_about_sensor(
    data: dict | None,
    category: str = "Accessibility",
    last_update_success: bool = True,
) -> StationAboutCategorySensor:
    """Return a StationAboutCategorySensor with a mocked coordinator."""
    coord = _make_coordinator(data, last_update_success)
    sensor = object.__new__(StationAboutCategorySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(sensor, "_category", category)
    object.__setattr__(sensor, "_attr_name", f"Test Station {category}")
    object.__setattr__(
        sensor, "_attr_unique_id", f"fuelcompare_ie_12345_about_{category.lower()}"
    )
    return sensor


# ---------------------------------------------------------------------------
# _parse_lastupdated
# ---------------------------------------------------------------------------


async def test_parse_lastupdated_iso_z() -> None:
    """ISO 8601 string with trailing Z is parsed to UTC-aware datetime."""
    result = _parse_lastupdated("2024-01-15T10:30:00.000Z")
    assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


async def test_parse_lastupdated_iso_no_z() -> None:
    """ISO 8601 string without Z is treated as UTC."""
    result = _parse_lastupdated("2024-01-15T10:30:00")
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


async def test_parse_lastupdated_date_only() -> None:
    """Date-only string produces a midnight UTC datetime."""
    result = _parse_lastupdated("2024-01-15")
    assert result == datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)


async def test_parse_lastupdated_none() -> None:
    """None input returns None."""
    assert _parse_lastupdated(None) is None


async def test_parse_lastupdated_empty() -> None:
    """Empty string returns None."""
    assert _parse_lastupdated("") is None


async def test_parse_lastupdated_invalid() -> None:
    """Unparseable string returns None."""
    assert _parse_lastupdated("not-a-date") is None


# ---------------------------------------------------------------------------
# FuelPriceSensor — native_value
# ---------------------------------------------------------------------------


async def test_fuel_price_sensor_native_value() -> None:
    """Sensor returns the price from coordinator data."""
    sensor = _make_fuel_sensor("unleaded", data={"unleaded": 1.85, "diesel": 1.75})
    assert sensor.native_value == pytest.approx(1.85)


async def test_fuel_price_sensor_none_when_no_data() -> None:
    """When coordinator.data is None the sensor returns None."""
    sensor = _make_fuel_sensor("unleaded", data=None)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# FuelPriceSensor — available
# ---------------------------------------------------------------------------


async def test_fuel_price_sensor_available_true() -> None:
    """Sensor is available when data contains a non-None value for the fuel type."""
    sensor = _make_fuel_sensor(
        "unleaded", data={"unleaded": 1.85}, last_update_success=True
    )
    assert sensor.available is True


async def test_fuel_price_sensor_available_false() -> None:
    """Sensor is unavailable when the fuel type value is None."""
    sensor = _make_fuel_sensor(
        "unleaded", data={"unleaded": None}, last_update_success=True
    )
    assert sensor.available is False


# ---------------------------------------------------------------------------
# StationWorkingHoursSensor
# ---------------------------------------------------------------------------


async def test_working_hours_from_json_string() -> None:
    """JSON string for working_hours is parsed; today's value is returned."""
    hours = {"Monday": "6a.m.-10p.m.", "Tuesday": "7a.m.-9p.m."}
    data = {"working_hours": json.dumps(hours)}
    sensor = _make_working_hours_sensor(data)

    with patch("custom_components.fuelcompare_ie.sensor.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "Monday"
        result = sensor.native_value

    assert result == "6a.m.-10p.m."


async def test_working_hours_from_dict() -> None:
    """Dict working_hours is used directly without JSON parsing."""
    hours = {"Monday": "6a.m.-10p.m."}
    data = {"working_hours": hours}
    sensor = _make_working_hours_sensor(data)

    with patch("custom_components.fuelcompare_ie.sensor.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "Monday"
        result = sensor.native_value

    assert result == "6a.m.-10p.m."


async def test_working_hours_none_when_absent() -> None:
    """When working_hours key is missing, native_value is None."""
    sensor = _make_working_hours_sensor({"unleaded": 1.85})
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# StationAboutCategorySensor
# ---------------------------------------------------------------------------


async def test_about_category_native_value() -> None:
    """Active (True) features are returned as a comma-separated string."""
    about = {"Accessibility": {"Wheelchair ramp": True, "Elevator": False}}
    data = {"about": json.dumps(about)}
    sensor = _make_about_sensor(data, category="Accessibility")

    result = sensor.native_value
    assert result == "Wheelchair ramp"


async def test_about_category_available_false_when_empty() -> None:
    """Sensor is unavailable when the category dict is empty."""
    about = {"Accessibility": {}}
    data = {"about": json.dumps(about)}
    sensor = _make_about_sensor(
        data, category="Accessibility", last_update_success=True
    )

    assert sensor.available is False
