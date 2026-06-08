"""Tests for FuelCompare.ie binary sensor platform."""

from __future__ import annotations

import json
from datetime import datetime, time as dt_time, timezone
from unittest.mock import MagicMock, patch


from custom_components.fuelcompare_ie.binary_sensor import (
    DataFetchProblemBinarySensor,
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
    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.time.return_value = dt_time(9, 0)
        result = _is_open("6a.m.-10p.m.")
    assert result is True


async def test_is_open_outside_range() -> None:
    """Time 23:00 is outside '6a.m.-10p.m.' → False."""
    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.time.return_value = dt_time(23, 0)
        result = _is_open("6a.m.-10p.m.")
    assert result is False


async def test_is_open_unparseable() -> None:
    """String with no recognisable time tokens returns None."""
    assert _is_open("some random text") is None


async def test_is_open_midnight_crossing() -> None:
    """Time 01:00 is within '10p.m.-2a.m.' (crosses midnight) → True."""
    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.time.return_value = dt_time(1, 0)
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

    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.strftime.return_value = "Monday"
        attrs = sensor.extra_state_attributes

    assert "today_hours" in attrs
    assert attrs["today_hours"] == "6a.m.-10p.m."


async def test_is_open_empty_string() -> None:
    """Empty string returns None."""
    assert _is_open("") is None


async def test_is_open_only_one_time_token() -> None:
    """String with only one recognisable time token returns None."""
    assert _is_open("opens at 6a.m.") is None


async def test_is_open_parse_time_failure() -> None:
    """If _parse_time returns None for either time, _is_open returns None."""
    # Force both findall matches to produce tokens that _parse_time cannot resolve
    # by patching _TIME_RE.findall to return tuples whose string form _parse_time
    # cannot match via its own regex (won't reach the fallback cleanly). Instead,
    # patch _parse_time directly.
    with patch(
        "custom_components.fuelcompare_ie.binary_sensor._parse_time", return_value=None
    ):
        result = _is_open("6a.m.-10p.m.")
    assert result is None


async def test_binary_sensor_today_not_in_hours() -> None:
    """When today's key is absent from the hours dict, is_on returns None."""
    hours = {"Sunday": "10a.m.-6p.m."}
    sensor = _make_binary_sensor({"working_hours": json.dumps(hours)})

    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.strftime.return_value = "Monday"
        result = sensor.is_on

    assert result is None


async def test_binary_sensor_is_on_from_dict_hours() -> None:
    """is_on works when working_hours is already a dict (not a JSON string)."""
    hours = {"Monday": "6a.m.-10p.m."}
    sensor = _make_binary_sensor({"working_hours": hours})

    with patch(
        "custom_components.fuelcompare_ie.binary_sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.strftime.return_value = "Monday"
        mock_now.return_value.time.return_value = dt_time(9, 0)
        result = sensor.is_on

    assert result is True


async def test_binary_sensor_is_on_invalid_json() -> None:
    """Invalid JSON in working_hours causes is_on to return None."""
    sensor = _make_binary_sensor({"working_hours": "not valid json {"})
    assert sensor.is_on is None


async def test_binary_sensor_extra_attributes_invalid_json() -> None:
    """Invalid JSON in working_hours causes extra_state_attributes to return station_id only."""
    sensor = _make_binary_sensor({"working_hours": "not valid json {"})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_binary_sensor_extra_attributes_no_raw() -> None:
    """extra_state_attributes returns station_id only when working_hours is None in data."""
    sensor = _make_binary_sensor({"working_hours": None})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# StationIsOpenBinarySensor — stale retention
# ---------------------------------------------------------------------------


async def test_is_open_available_with_data_after_failure() -> None:
    """is_open binary sensor stays available with last good working_hours after failure."""
    hours = {"Monday": "6a.m.-10p.m."}
    coord = MagicMock()
    coord.data = {"working_hours": json.dumps(hours)}
    coord.last_update_success = False
    sensor = object.__new__(StationIsOpenBinarySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is True


async def test_is_open_unavailable_when_no_data() -> None:
    """is_open binary sensor is unavailable when coordinator has no data at all."""
    coord = MagicMock()
    coord.data = None
    coord.last_update_success = False
    sensor = object.__new__(StationIsOpenBinarySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


async def test_is_open_unavailable_when_working_hours_missing() -> None:
    """is_open binary sensor is unavailable when working_hours field is missing."""
    coord = MagicMock()
    coord.data = {"unleaded": 1.85}
    coord.last_update_success = True
    sensor = object.__new__(StationIsOpenBinarySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


# ---------------------------------------------------------------------------
# DataFetchProblemBinarySensor
# ---------------------------------------------------------------------------


def _make_data_fetch_problem_sensor(
    last_update_success: bool,
    last_exception: Exception | None = None,
    last_successful_fetch: datetime | None = None,
) -> DataFetchProblemBinarySensor:
    """Return a DataFetchProblemBinarySensor with a mocked coordinator."""
    coord = MagicMock()
    coord.last_update_success = last_update_success
    coord.last_exception = last_exception
    coord.last_successful_fetch = last_successful_fetch
    sensor = object.__new__(DataFetchProblemBinarySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(
        sensor, "_attr_unique_id", "fuelcompare_ie_12345_data_fetch_problem"
    )
    return sensor


async def test_data_fetch_problem_is_off_when_success() -> None:
    """data_fetch_problem is_on=False when last update succeeded (no problem)."""
    sensor = _make_data_fetch_problem_sensor(last_update_success=True)
    assert sensor.is_on is False


async def test_data_fetch_problem_is_on_when_failure() -> None:
    """data_fetch_problem is_on=True when last update failed (problem present)."""
    sensor = _make_data_fetch_problem_sensor(last_update_success=False)
    assert sensor.is_on is True


async def test_data_fetch_problem_always_available() -> None:
    """data_fetch_problem is always available, even on first-fetch failure."""
    sensor = _make_data_fetch_problem_sensor(last_update_success=False)
    assert sensor.available is True


async def test_data_fetch_problem_attributes_with_exception_and_timestamp() -> None:
    """Attributes carry stringified last exception and ISO last_successful_fetch."""
    ts = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)
    sensor = _make_data_fetch_problem_sensor(
        last_update_success=False,
        last_exception=RuntimeError("boom"),
        last_successful_fetch=ts,
    )
    attrs = sensor.extra_state_attributes
    assert attrs["station_id"] == "12345"
    assert attrs["last_exception"] == "boom"
    assert attrs["last_successful_fetch"] == ts.isoformat()


async def test_data_fetch_problem_attributes_no_exception_no_timestamp() -> None:
    """Attributes are None for missing last exception and timestamp."""
    sensor = _make_data_fetch_problem_sensor(last_update_success=True)
    attrs = sensor.extra_state_attributes
    assert attrs == {
        "station_id": "12345",
        "last_exception": None,
        "last_successful_fetch": None,
    }


async def test_is_open_extra_attributes_no_data() -> None:
    """StationIsOpenBinarySensor extra_state_attributes returns base when data is None."""
    sensor = _make_binary_sensor(None)
    assert sensor.extra_state_attributes == {"station_id": "12345"}
