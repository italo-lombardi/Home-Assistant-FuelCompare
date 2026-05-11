"""Tests for FuelCompare.ie binary sensor platform."""

from __future__ import annotations

import json
from datetime import time as dt_time
from unittest.mock import MagicMock, patch


from custom_components.fuelcompare_ie.binary_sensor import (
    StationIsOpenBinarySensor,
    _is_open,
    _parse_time,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binary_sensor(data: dict | None) -> StationIsOpenBinarySensor:
    """Return a StationIsOpenBinarySensor with a mocked coordinator."""
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    sensor = object.__new__(StationIsOpenBinarySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(sensor, "_attr_name", "Test Station Is Open")
    object.__setattr__(sensor, "_attr_unique_id", "fuelcompare_ie_12345_is_open")
    return sensor


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------


async def test_parse_time_am() -> None:
    """'6a.m.' parses to time(6, 0)."""
    assert _parse_time("6a.m.") == dt_time(6, 0)


async def test_parse_time_pm() -> None:
    """'10p.m.' parses to time(22, 0)."""
    assert _parse_time("10p.m.") == dt_time(22, 0)


async def test_parse_time_with_minutes() -> None:
    """'10:30p.m.' parses to time(22, 30)."""
    assert _parse_time("10:30p.m.") == dt_time(22, 30)


async def test_parse_time_noon() -> None:
    """'12p.m.' parses to time(12, 0)."""
    assert _parse_time("12p.m.") == dt_time(12, 0)


async def test_parse_time_midnight() -> None:
    """'12a.m.' parses to time(0, 0)."""
    assert _parse_time("12a.m.") == dt_time(0, 0)


async def test_parse_time_invalid() -> None:
    """Garbage string returns None."""
    assert _parse_time("garbage") is None


# ---------------------------------------------------------------------------
# _is_open
# ---------------------------------------------------------------------------


async def test_is_open_24h() -> None:
    """'Open 24 hours' always returns True."""
    assert _is_open("Open 24 hours") is True


async def test_is_open_closed() -> None:
    """'Closed' returns False."""
    assert _is_open("Closed") is False


async def test_is_open_within_range() -> None:
    """Time 09:00 is within '6a.m.-10p.m.' → True."""
    with patch("custom_components.fuelcompare_ie.binary_sensor.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = dt_time(9, 0)
        result = _is_open("6a.m.-10p.m.")
    assert result is True


async def test_is_open_outside_range() -> None:
    """Time 23:00 is outside '6a.m.-10p.m.' → False."""
    with patch("custom_components.fuelcompare_ie.binary_sensor.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = dt_time(23, 0)
        result = _is_open("6a.m.-10p.m.")
    assert result is False


async def test_is_open_unparseable() -> None:
    """String with no recognisable time tokens returns None."""
    assert _is_open("some random text") is None


async def test_is_open_midnight_crossing() -> None:
    """Time 01:00 is within '10p.m.-2a.m.' (crosses midnight) → True."""
    with patch("custom_components.fuelcompare_ie.binary_sensor.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = dt_time(1, 0)
        result = _is_open("10p.m.-2a.m.")
    assert result is True


# ---------------------------------------------------------------------------
# StationIsOpenBinarySensor
# ---------------------------------------------------------------------------


async def test_binary_sensor_no_data() -> None:
    """When coordinator.data is None, is_on returns None."""
    sensor = _make_binary_sensor(None)
    assert sensor.is_on is None


async def test_binary_sensor_no_working_hours() -> None:
    """When working_hours key is absent, is_on returns None."""
    sensor = _make_binary_sensor({"unleaded": 1.85})
    assert sensor.is_on is None


async def test_binary_sensor_today_hours_attribute() -> None:
    """extra_state_attributes contains 'today_hours' when working_hours is present."""
    hours = {"Monday": "6a.m.-10p.m.", "Tuesday": "7a.m.-9p.m."}
    sensor = _make_binary_sensor({"working_hours": json.dumps(hours)})

    with patch("custom_components.fuelcompare_ie.binary_sensor.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "Monday"
        attrs = sensor.extra_state_attributes

    assert "today_hours" in attrs
    assert attrs["today_hours"] == "6a.m.-10p.m."
