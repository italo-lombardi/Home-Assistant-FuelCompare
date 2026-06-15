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
from tests.test_sensor import _make_coordinator


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


async def test_parse_time_hours_over_23_returns_none() -> None:
    """_parse_time returns None when computed hours exceed 23."""
    # "13pm" → hours=13+12=25 → > 23 → None
    assert _parse_time("13p.m.") is None


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

    with (
        patch("custom_components.fuelcompare_ie.binary_sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.binary_sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0
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

    with (
        patch("custom_components.fuelcompare_ie.binary_sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.binary_sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0
        result = sensor.is_on

    assert result is None


async def test_binary_sensor_is_on_from_dict_hours() -> None:
    """is_on works when working_hours is already a dict (not a JSON string)."""
    hours = {"Monday": "6a.m.-10p.m."}
    sensor = _make_binary_sensor({"working_hours": hours})

    with (
        patch("custom_components.fuelcompare_ie.binary_sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.binary_sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0
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

    # Stale retention: entities stay available and keep last value during coordinator outages
    assert sensor.available is True
    with (
        patch("custom_components.fuelcompare_ie.binary_sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.binary_sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0  # Monday
        mock_now.return_value.time.return_value = dt_time(9, 0)
        result = sensor.is_on
    assert result is not None  # retains last known open/closed state


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


# ---------------------------------------------------------------------------
# FacilityBinarySensor
# ---------------------------------------------------------------------------


def _make_facility_sensor(cap_key: str, data: dict | None):
    from custom_components.fuelcompare_ie.binary_sensor import FacilityBinarySensor

    coord = _make_coordinator(data)
    sensor = object.__new__(FacilityBinarySensor)
    sensor._station_id = "12345"
    sensor._cap_key = cap_key
    sensor._attr_icon = "mdi:test"
    sensor._attr_translation_key = cap_key
    sensor._attr_device_class = None
    sensor._attr_unique_id = f"fuelcompare_ie_12345_{cap_key}"
    sensor._attr_device_info = {}
    object.__setattr__(sensor, "coordinator", coord)
    return sensor


def test_facility_sensor_is_on_true() -> None:
    sensor = _make_facility_sensor("has_car_wash", {"has_car_wash": True})
    assert sensor.is_on is True


def test_facility_sensor_is_on_false() -> None:
    sensor = _make_facility_sensor("has_shop", {"has_shop": False})
    assert sensor.is_on is False


def test_facility_sensor_is_on_none_when_key_absent() -> None:
    sensor = _make_facility_sensor("has_toilet", {})
    assert sensor.is_on is None


def test_facility_sensor_is_on_none_when_no_data() -> None:
    sensor = _make_facility_sensor("accepts_cash", None)
    assert sensor.is_on is None


def test_facility_sensor_available_true_when_key_present_and_not_none() -> None:
    sensor = _make_facility_sensor("has_atm", {"has_atm": True})
    assert sensor.available is True


def test_facility_sensor_available_false_when_key_absent() -> None:
    sensor = _make_facility_sensor("has_atm", {})
    assert sensor.available is False


def test_facility_sensor_available_false_when_no_data() -> None:
    sensor = _make_facility_sensor("accepts_cards", None)
    assert sensor.available is False


def test_facility_sensor_extra_state_attributes() -> None:
    sensor = _make_facility_sensor("has_car_wash", {"has_car_wash": True})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# OSM opening_hours parsing (_is_open_osm / _is_open)
# ---------------------------------------------------------------------------


def test_is_open_osm_standard_range() -> None:
    """'Mo-Su 07:00-23:00' correctly identifies open/closed state."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open
    from unittest.mock import patch
    import homeassistant.util.dt as _dt

    # Simulate Monday 10:00 (open)
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        assert _is_open("Mo-Su 07:00-23:00") is True

    # Simulate Monday 06:00 (before opening)
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(6, 0)
        assert _is_open("Mo-Su 07:00-23:00") is False


def test_is_open_osm_247() -> None:
    from custom_components.fuelcompare_ie.binary_sensor import _is_open

    assert _is_open("24/7") is True


def test_is_open_legacy_24_hours() -> None:
    from custom_components.fuelcompare_ie.binary_sensor import _is_open

    assert _is_open("Open 24 hours") is True


# ---------------------------------------------------------------------------
# _is_open_osm — OSM parser robustness (lines 127-128, 132-133, 143)
# ---------------------------------------------------------------------------


def test_is_open_osm_skips_rule_with_fewer_than_two_times() -> None:
    """Rule with only one time token is skipped; returns None when no valid rule (line 128)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    # 'mo-su 07:00' has only one HH:MM token — rule is skipped, None returned
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        result = _is_open_osm("mo-su 07:00")
    assert result is None


def test_is_open_osm_skips_rule_with_invalid_hour_value() -> None:
    """Rule with hour=25 raises ValueError, rule is skipped (lines 132-133)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    # 25:00-23:00 — hours[0] = '25' → dt_time(25, 0) raises ValueError → continue
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        result = _is_open_osm("mo-su 25:00-23:00")
    assert result is None


def test_is_open_osm_returns_none_when_no_rule_matches() -> None:
    """Returns None when day does not match any rule (line 143)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    # Only Saturday rule; simulate Monday (idx=0) -> no match -> None
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        result = _is_open_osm("sa 09:00-18:00")
    assert result is None


# ---------------------------------------------------------------------------
# _is_open_osm — midnight crossing (line 140)
# ---------------------------------------------------------------------------


def test_is_open_osm_midnight_crossing_returns_true_after_open() -> None:
    """Overnight rule (22:00-06:00) returns True at 23:30 (line 140)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(23, 30)
        result = _is_open_osm("mo-su 22:00-06:00")
    assert result is True


def test_is_open_osm_midnight_crossing_returns_true_before_close() -> None:
    """Overnight rule (22:00-06:00) returns True at 03:00 (line 140)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(3, 0)
        result = _is_open_osm("mo-su 22:00-06:00")
    assert result is True


def test_is_open_osm_midnight_crossing_returns_false_between_close_and_open() -> None:
    """Overnight rule (22:00-06:00) returns False at 10:00 (line 140)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        result = _is_open_osm("mo-su 22:00-06:00")
    assert result is False


def test_is_open_osm_normalizes_2400_closing_time() -> None:
    """OSM '24:00' closing time is normalized to 00:00 (not a ValueError)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    # Station open Mo-Su 07:00-24:00 — at 10:00 should be open
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(10, 0)
        result = _is_open_osm("mo-su 07:00-24:00")
    assert result is True


def test_is_open_osm_normalizes_2400_opening_time() -> None:
    """OSM '24:00' opening time is normalized to 00:00 (treats as midnight open)."""
    from custom_components.fuelcompare_ie.binary_sensor import _is_open_osm
    import homeassistant.util.dt as _dt

    # 24:00-06:00 → 00:00-06:00 crossing midnight; at 01:00 should be open
    with patch.object(_dt, "now") as mock_now:
        mock_now.return_value.weekday.return_value = 0
        mock_now.return_value.time.return_value = dt_time(1, 0)
        result = _is_open_osm("mo-su 24:00-06:00")
    assert result is True


# ---------------------------------------------------------------------------
# _day_matches — empty day spec, wrapped range, single day (lines 149, 159-164)
# ---------------------------------------------------------------------------


def test_day_matches_empty_spec_returns_true() -> None:
    """Empty day_spec returns True for any day index (line 149)."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    assert _day_matches("", 0) is True
    assert _day_matches("", 6) is True


def test_day_matches_wrapped_range_fri_to_mon() -> None:
    """Wrapped range 'fr-mo' (4-0) matches Friday and Monday (line 159)."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    assert _day_matches("fr-mo", 4) is True  # Friday (idx=4)
    assert _day_matches("fr-mo", 0) is True  # Monday (idx=0)
    assert _day_matches("fr-mo", 2) is False  # Wednesday (idx=2) outside wrap


def test_day_matches_single_day_spec_matches_exact_day() -> None:
    """Single day 'sa' matches only Saturday (idx=5) (lines 160-163)."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    assert _day_matches("sa", 5) is True
    assert _day_matches("sa", 0) is False


def test_day_matches_unparseable_spec_returns_false() -> None:
    """Unparseable day spec returns False (safe fallback — don't assume open)."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    assert _day_matches("xx-yy", 3) is False


def test_day_matches_comma_separated_list() -> None:
    """Comma-separated OSM ranges like 'Tu-Th,Sa' match correctly."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    assert _day_matches("tu-th,sa", 1) is True  # Tuesday (idx=1)
    assert _day_matches("tu-th,sa", 3) is True  # Thursday (idx=3)
    assert _day_matches("tu-th,sa", 5) is True  # Saturday (idx=5)
    assert _day_matches("tu-th,sa", 4) is False  # Friday not in list


def test_day_matches_trailing_comma_skips_empty_segment() -> None:
    """Trailing comma in day spec produces empty segment, which is skipped."""
    from custom_components.fuelcompare_ie.binary_sensor import _day_matches

    # "mo," splits to ["mo", ""] — empty segment should be skipped, not match
    assert _day_matches("mo,", 0) is True  # Monday matches
    assert _day_matches("mo,", 2) is False  # Wednesday doesn't match


# ---------------------------------------------------------------------------
# StationIsOpenBinarySensor — OSM string priority (line 252)
# ---------------------------------------------------------------------------


def test_get_today_hours_str_returns_osm_string_directly() -> None:
    """_get_today_hours_str returns raw OSM string when opening_hours present (line 252)."""
    sensor = _make_binary_sensor({"opening_hours": "Mo-Su 07:00-23:00"})
    result = sensor._get_today_hours_str()
    assert result == "Mo-Su 07:00-23:00"


# ---------------------------------------------------------------------------
# StationIsOpenBinarySensor — pre-computed is_open bool priority (line 269)
# ---------------------------------------------------------------------------


def test_is_on_returns_direct_bool_true_from_data() -> None:
    """is_on returns True directly when data['is_open'] is True (line 269)."""
    sensor = _make_binary_sensor({"is_open": True})
    assert sensor.is_on is True


def test_is_on_returns_direct_bool_false_from_data() -> None:
    """is_on returns False directly when data['is_open'] is False (line 269)."""
    sensor = _make_binary_sensor({"is_open": False})
    assert sensor.is_on is False


# ---------------------------------------------------------------------------
# FacilityBinarySensor — None key returns None not False (line 392)
# ---------------------------------------------------------------------------


def test_facility_sensor_is_on_none_when_key_is_none_value() -> None:
    """is_on returns None (not False) when cap_key maps to None in data (line 392)."""
    sensor = _make_facility_sensor("has_atm", {"has_atm": None})
    assert sensor.is_on is None
