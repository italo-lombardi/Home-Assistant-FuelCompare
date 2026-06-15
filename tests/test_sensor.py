"""Tests for FuelCompare.ie sensor platform."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.fuelcompare_ie.sensor import (
    FuelPriceSensor,
    LastSuccessfulFetchSensor,
    StationAboutCategorySensor,
    StationBrandSensor,
    StationCountySensor,
    StationNameSensor,
    StationPriceLastUpdatedSensor,
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
    coord._provider.LABEL = "fuelcompare.ie"
    coord.provider_label = "fuelcompare.ie"
    coord.provider_currency = "€"
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

    with (
        patch("custom_components.fuelcompare_ie.sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0
        result = sensor.native_value

    assert result == "6a.m.-10p.m."


async def test_working_hours_from_dict() -> None:
    """Dict working_hours is used directly without JSON parsing."""
    hours = {"Monday": "6a.m.-10p.m."}
    data = {"working_hours": hours}
    sensor = _make_working_hours_sensor(data)

    with (
        patch("custom_components.fuelcompare_ie.sensor.dt_util.now") as mock_now,
        patch(
            "custom_components.fuelcompare_ie.sensor.dt_util.as_local",
            side_effect=lambda x: x,
        ),
    ):
        mock_now.return_value.weekday.return_value = 0
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


async def test_parse_lastupdated_whitespace_only() -> None:
    """Whitespace-only string returns None."""
    assert _parse_lastupdated("   ") is None


async def test_parse_lastupdated_fromisoformat_fallback() -> None:
    """ISO string with timezone offset falls through to fromisoformat path."""
    result = _parse_lastupdated("2024-06-01T12:00:00+01:00")
    assert result is not None
    assert result.tzinfo is not None


async def test_parse_lastupdated_fromisoformat_naive_gets_utc() -> None:
    """Space-separated datetime (no T) reaches fromisoformat and gets UTC attached."""
    result = _parse_lastupdated("2024-01-15 10:30:00")
    assert result is not None
    from datetime import timezone

    assert result.tzinfo == timezone.utc


async def test_fuel_price_sensor_extra_state_attributes_with_lastupdated() -> None:
    """extra_state_attributes includes price_last_updated when present."""
    data = {"unleaded": 1.85, "lastupdated": "2024-01-15T10:30:00.000Z"}
    sensor = _make_fuel_sensor("unleaded", data=data)
    attrs = sensor.extra_state_attributes
    assert attrs["station_id"] == "12345"
    assert attrs["fuel_type"] == "unleaded"
    assert attrs["source"] == "fuelcompare.ie"
    assert attrs["price_last_updated"] == "2024-01-15T10:30:00+00:00"


async def test_fuel_price_sensor_extra_state_attributes_no_lastupdated() -> None:
    """extra_state_attributes omits price_last_updated when absent."""
    data = {"unleaded": 1.85}
    sensor = _make_fuel_sensor("unleaded", data=data)
    attrs = sensor.extra_state_attributes
    assert "price_last_updated" not in attrs


async def test_fuel_price_sensor_extra_state_attributes_no_data() -> None:
    """extra_state_attributes works when coordinator.data is None."""
    sensor = _make_fuel_sensor("unleaded", data=None)
    attrs = sensor.extra_state_attributes
    assert attrs["station_id"] == "12345"
    assert "price_last_updated" not in attrs


def _make_last_updated_sensor(data: dict | None) -> StationPriceLastUpdatedSensor:
    """Return a StationPriceLastUpdatedSensor with a mocked coordinator."""
    coord = _make_coordinator(data)
    sensor = object.__new__(StationPriceLastUpdatedSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(
        sensor, "_attr_unique_id", "fuelcompare_ie_12345_price_last_updated"
    )
    return sensor


def _make_brand_sensor(data: dict | None) -> StationBrandSensor:
    coord = _make_coordinator(data)
    sensor = object.__new__(StationBrandSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_attr_unique_id", "fuelcompare_ie_12345_brand")
    return sensor


def _make_county_sensor(data: dict | None) -> StationCountySensor:
    coord = _make_coordinator(data)
    sensor = object.__new__(StationCountySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_attr_unique_id", "fuelcompare_ie_12345_county")
    return sensor


async def test_last_updated_sensor_with_data() -> None:
    """StationPriceLastUpdatedSensor returns parsed datetime."""
    sensor = _make_last_updated_sensor({"lastupdated": "2024-01-15T10:30:00.000Z"})
    from datetime import datetime, timezone

    assert sensor.native_value == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


async def test_last_updated_sensor_no_data() -> None:
    """StationPriceLastUpdatedSensor returns None when data is None."""
    sensor = _make_last_updated_sensor(None)
    assert sensor.native_value is None


async def test_brand_sensor_with_tablename() -> None:
    """StationBrandSensor formats tablename as title case."""
    sensor = _make_brand_sensor({"tablename": "circle_k"})
    assert sensor.native_value == "Circle K"


async def test_brand_sensor_no_tablename() -> None:
    """StationBrandSensor returns None when tablename absent."""
    sensor = _make_brand_sensor({"county": "Dublin"})
    assert sensor.native_value is None


async def test_brand_sensor_no_data() -> None:
    """StationBrandSensor returns None when data is None."""
    sensor = _make_brand_sensor(None)
    assert sensor.native_value is None


async def test_county_sensor_with_data() -> None:
    """StationCountySensor returns county string."""
    sensor = _make_county_sensor({"county": "Cork"})
    assert sensor.native_value == "Cork"


async def test_county_sensor_no_data() -> None:
    """StationCountySensor returns None when data is None."""
    sensor = _make_county_sensor(None)
    assert sensor.native_value is None


async def test_working_hours_invalid_json() -> None:
    """Invalid JSON in working_hours causes native_value to return None."""
    sensor = _make_working_hours_sensor({"working_hours": "not valid {"})
    assert sensor.native_value is None


async def test_working_hours_extra_attributes_full_schedule() -> None:
    """extra_state_attributes returns full week schedule plus station_id."""
    hours = {"Monday": "6a.m.-10p.m.", "Tuesday": "7a.m.-9p.m."}
    sensor = _make_working_hours_sensor({"working_hours": json.dumps(hours)})
    assert sensor.extra_state_attributes == {"station_id": "12345", **hours}


async def test_working_hours_extra_attributes_no_data() -> None:
    """extra_state_attributes returns station_id only when data is None."""
    sensor = _make_working_hours_sensor(None)
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_working_hours_extra_attributes_invalid_json() -> None:
    """extra_state_attributes returns station_id only on JSON parse error."""
    sensor = _make_working_hours_sensor({"working_hours": "bad json {"})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_about_category_invalid_json() -> None:
    """Invalid JSON in about causes native_value to return None."""
    sensor = _make_about_sensor({"about": "not valid {"}, category="Accessibility")
    assert sensor.native_value is None


async def test_about_category_no_active_features() -> None:
    """Category with all features disabled returns None for native_value."""
    about = {"Accessibility": {"Wheelchair ramp": False, "Elevator": False}}
    sensor = _make_about_sensor({"about": json.dumps(about)}, category="Accessibility")
    assert sensor.native_value is None


async def test_about_category_extra_attributes() -> None:
    """extra_state_attributes returns full feature dict plus station_id."""
    about = {"Accessibility": {"Wheelchair ramp": True, "Elevator": False}}
    sensor = _make_about_sensor({"about": json.dumps(about)}, category="Accessibility")
    result = sensor.extra_state_attributes
    assert result == {"station_id": "12345", "Wheelchair ramp": True, "Elevator": False}


async def test_about_category_flat_key_non_dict_returns_empty() -> None:
    """_get_category_data returns {} when flat key exists but is not a dict."""
    sensor = _make_about_sensor(
        {"Accessibility": "not_a_dict"}, category="Accessibility"
    )
    assert sensor.native_value is None
    assert sensor.available is False


async def test_about_category_flat_key_dict_returned() -> None:
    """_get_category_data returns flat key dict when about key is absent."""
    flat_data = {"Wheelchair ramp": True, "Elevator": False}
    sensor = _make_about_sensor({"Accessibility": flat_data}, category="Accessibility")
    assert sensor.available is True
    assert sensor.native_value == "Wheelchair ramp"


async def test_working_hours_extra_attributes_no_raw() -> None:
    """extra_state_attributes returns station_id only when working_hours is None in data."""
    sensor = _make_working_hours_sensor({"working_hours": None})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_about_category_get_data_no_about() -> None:
    """_get_category_data returns empty dict when about key is absent."""
    sensor = _make_about_sensor({"county": "Dublin"}, category="Accessibility")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"station_id": "12345"}


def _make_name_sensor(data: dict | None) -> StationNameSensor:
    coord = _make_coordinator(data)
    sensor = object.__new__(StationNameSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(sensor, "_attr_unique_id", "fuelcompare_ie_12345_station_name")
    return sensor


async def test_name_sensor_with_data() -> None:
    """StationNameSensor returns the name field."""
    sensor = _make_name_sensor({"name": "Circle K Mulhuddart"})
    assert sensor.native_value == "Circle K Mulhuddart"


async def test_name_sensor_no_name_field() -> None:
    """StationNameSensor returns None when name absent."""
    sensor = _make_name_sensor({"tablename": "circle_k"})
    assert sensor.native_value is None


async def test_name_sensor_no_data() -> None:
    """StationNameSensor returns None when data is None."""
    sensor = _make_name_sensor(None)
    assert sensor.native_value is None


async def test_name_sensor_extra_attributes() -> None:
    """StationNameSensor extra_state_attributes contains station_id."""
    sensor = _make_name_sensor({"name": "Circle K Mulhuddart"})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# Stale retention — entities stay available with last known data even when the
# coordinator's last update failed (last_update_success=False).
# ---------------------------------------------------------------------------


async def test_fuel_price_sensor_stale_retention_after_failure() -> None:
    """FuelPriceSensor stays available with last good price when last fetch failed."""
    sensor = _make_fuel_sensor(
        "unleaded",
        data={"unleaded": 1.85},
        last_update_success=False,
    )
    # Stale retention: entities stay available and keep last value during coordinator outages
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(1.85)


async def test_about_category_stale_retention_after_failure() -> None:
    """StationAboutCategorySensor stays available with last good data when last fetch failed."""
    about = {"Accessibility": {"Wheelchair ramp": True}}
    sensor = _make_about_sensor(
        {"about": json.dumps(about)},
        category="Accessibility",
        last_update_success=False,
    )
    assert sensor.available is True
    assert sensor.native_value == "Wheelchair ramp"


async def test_price_last_updated_stale_retention_after_failure() -> None:
    """StationPriceLastUpdatedSensor stays available with last good timestamp on failure."""
    coord = _make_coordinator(
        {"lastupdated": "2024-01-15T10:30:00.000Z"}, last_update_success=False
    )
    sensor = object.__new__(StationPriceLastUpdatedSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(
        sensor, "_attr_unique_id", "fuelcompare_ie_12345_price_last_updated"
    )

    assert sensor.available is True
    assert sensor.native_value == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


async def test_price_last_updated_unavailable_when_no_data() -> None:
    """StationPriceLastUpdatedSensor is unavailable when no data has ever been fetched."""
    coord = _make_coordinator(None, last_update_success=False)
    sensor = object.__new__(StationPriceLastUpdatedSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


async def test_name_sensor_stale_retention_after_failure() -> None:
    """StationNameSensor stays available with last known name on failure."""
    coord = _make_coordinator({"name": "Circle K"}, last_update_success=False)
    sensor = object.__new__(StationNameSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is True
    assert sensor.native_value == "Circle K"


async def test_name_sensor_unavailable_when_no_data() -> None:
    """StationNameSensor is unavailable when no data has ever been fetched."""
    coord = _make_coordinator(None, last_update_success=False)
    sensor = object.__new__(StationNameSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


async def test_brand_sensor_stale_retention_after_failure() -> None:
    """StationBrandSensor stays available with last known brand on failure."""
    coord = _make_coordinator({"tablename": "circle_k"}, last_update_success=False)
    sensor = object.__new__(StationBrandSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is True
    assert sensor.native_value == "Circle K"


async def test_brand_sensor_unavailable_when_no_data() -> None:
    """StationBrandSensor is unavailable when no data has ever been fetched."""
    coord = _make_coordinator(None, last_update_success=False)
    sensor = object.__new__(StationBrandSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


async def test_county_sensor_stale_retention_after_failure() -> None:
    """StationCountySensor stays available with last known county on failure."""
    coord = _make_coordinator({"county": "Dublin"}, last_update_success=False)
    sensor = object.__new__(StationCountySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is True
    assert sensor.native_value == "Dublin"


async def test_county_sensor_unavailable_when_no_data() -> None:
    """StationCountySensor is unavailable when no data has ever been fetched."""
    coord = _make_coordinator(None, last_update_success=False)
    sensor = object.__new__(StationCountySensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


async def test_working_hours_sensor_stale_retention_after_failure() -> None:
    """StationWorkingHoursSensor stays available with last known hours on failure."""
    hours = {"Monday": "6a.m.-10p.m."}
    coord = _make_coordinator(
        {"working_hours": json.dumps(hours)}, last_update_success=False
    )
    sensor = object.__new__(StationWorkingHoursSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is True


async def test_working_hours_sensor_unavailable_when_no_data() -> None:
    """StationWorkingHoursSensor is unavailable when no data has ever been fetched."""
    coord = _make_coordinator(None, last_update_success=False)
    sensor = object.__new__(StationWorkingHoursSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")

    assert sensor.available is False


# ---------------------------------------------------------------------------
# LastSuccessfulFetchSensor
# ---------------------------------------------------------------------------


def _make_last_fetch_sensor(
    last_successful_fetch: datetime | None,
) -> LastSuccessfulFetchSensor:
    """Return an LastSuccessfulFetchSensor with a mocked coordinator."""
    coord = MagicMock()
    coord.last_successful_fetch = last_successful_fetch
    sensor = object.__new__(LastSuccessfulFetchSensor)
    object.__setattr__(sensor, "coordinator", coord)
    object.__setattr__(sensor, "_station_id", "12345")
    object.__setattr__(
        sensor, "_attr_unique_id", "fuelcompare_ie_12345_last_successful_fetch"
    )
    return sensor


async def test_last_successful_fetch_native_value() -> None:
    """LastSuccessfulFetchSensor returns the coordinator's last_successful_fetch."""
    ts = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)
    sensor = _make_last_fetch_sensor(ts)
    assert sensor.native_value == ts


async def test_last_successful_fetch_native_value_none() -> None:
    """LastSuccessfulFetchSensor returns None before first successful fetch."""
    sensor = _make_last_fetch_sensor(None)
    assert sensor.native_value is None


async def test_last_successful_fetch_always_available() -> None:
    """LastSuccessfulFetchSensor is always available, even before any fetch."""
    sensor = _make_last_fetch_sensor(None)
    assert sensor.available is True


async def test_last_successful_fetch_extra_attributes() -> None:
    """LastSuccessfulFetchSensor exposes station_id."""
    sensor = _make_last_fetch_sensor(None)
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# extra_state_attributes coverage for station-level sensors
# ---------------------------------------------------------------------------


async def test_price_last_updated_extra_attributes() -> None:
    """StationPriceLastUpdatedSensor extra_state_attributes contains station_id."""
    sensor = _make_last_updated_sensor({"lastupdated": "2024-01-15T10:30:00.000Z"})
    object.__setattr__(sensor, "_station_id", "12345")
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_brand_sensor_extra_attributes() -> None:
    """StationBrandSensor extra_state_attributes contains station_id."""
    sensor = _make_brand_sensor({"tablename": "circle_k"})
    object.__setattr__(sensor, "_station_id", "12345")
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_county_sensor_extra_attributes() -> None:
    """StationCountySensor extra_state_attributes contains station_id."""
    sensor = _make_county_sensor({"county": "Cork"})
    object.__setattr__(sensor, "_station_id", "12345")
    assert sensor.extra_state_attributes == {"station_id": "12345"}


async def test_working_hours_native_value_no_data() -> None:
    """StationWorkingHoursSensor native_value is None when coordinator.data is None."""
    sensor = _make_working_hours_sensor(None)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# StationSimpleStrSensor
# ---------------------------------------------------------------------------


def _make_simple_str_sensor(data_key: str, data: dict | None):
    from custom_components.fuelcompare_ie.sensor import StationSimpleStrSensor

    coord = _make_coordinator(data)
    sensor = object.__new__(StationSimpleStrSensor)
    sensor._station_id = "12345"
    sensor._data_key = data_key
    sensor._attr_icon = "mdi:test"
    sensor._attr_translation_key = data_key
    sensor._attr_unique_id = f"fuelcompare_ie_12345_{data_key}"
    sensor._attr_device_info = {}
    object.__setattr__(sensor, "coordinator", coord)
    return sensor


def test_simple_str_sensor_native_value() -> None:
    sensor = _make_simple_str_sensor("address", {"address": "Main St"})
    assert sensor.native_value == "Main St"


def test_simple_str_sensor_native_value_none_when_no_data() -> None:
    sensor = _make_simple_str_sensor("address", None)
    assert sensor.native_value is None


def test_simple_str_sensor_native_value_empty_string_returns_none() -> None:
    sensor = _make_simple_str_sensor("phone", {"phone": ""})
    assert sensor.native_value is None


def test_simple_str_sensor_available_true_with_data() -> None:
    sensor = _make_simple_str_sensor("website", {"website": "http://test.ie"})
    assert sensor.available is True


def test_simple_str_sensor_available_false_without_data() -> None:
    sensor = _make_simple_str_sensor("website", None)
    assert sensor.available is False


def test_simple_str_sensor_extra_state_attributes() -> None:
    sensor = _make_simple_str_sensor("phone", {"phone": "+353-1-123"})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# StationSimpleFloatSensor
# ---------------------------------------------------------------------------


def _make_simple_float_sensor(data_key: str, data: dict | None):
    from custom_components.fuelcompare_ie.sensor import StationSimpleFloatSensor

    coord = _make_coordinator(data)
    sensor = object.__new__(StationSimpleFloatSensor)
    sensor._station_id = "12345"
    sensor._data_key = data_key
    sensor._attr_icon = "mdi:test"
    sensor._attr_translation_key = data_key
    sensor._attr_unique_id = f"fuelcompare_ie_12345_{data_key}"
    sensor._attr_device_info = {}
    object.__setattr__(sensor, "coordinator", coord)
    return sensor


def test_simple_float_sensor_native_value() -> None:
    sensor = _make_simple_float_sensor("latitude", {"latitude": 53.3498})
    assert sensor.native_value == pytest.approx(53.3498)


def test_simple_float_sensor_native_value_none_when_no_data() -> None:
    sensor = _make_simple_float_sensor("longitude", None)
    assert sensor.native_value is None


def test_simple_float_sensor_rounds_to_6_decimals() -> None:
    sensor = _make_simple_float_sensor("latitude", {"latitude": 53.349812345678})
    assert sensor.native_value == pytest.approx(53.349812)


# ---------------------------------------------------------------------------
# StationOpeningHoursSensor
# ---------------------------------------------------------------------------


def _make_opening_hours_sensor(data: dict | None):
    from custom_components.fuelcompare_ie.sensor import StationOpeningHoursSensor

    coord = _make_coordinator(data)
    sensor = object.__new__(StationOpeningHoursSensor)
    sensor._station_id = "12345"
    sensor._attr_unique_id = "fuelcompare_ie_12345_opening_hours"
    sensor._attr_device_info = {}
    object.__setattr__(sensor, "coordinator", coord)
    return sensor


def test_opening_hours_sensor_native_value() -> None:
    sensor = _make_opening_hours_sensor({"opening_hours": "Mo-Su 07:00-23:00"})
    assert sensor.native_value == "Mo-Su 07:00-23:00"


def test_opening_hours_sensor_none_when_no_data() -> None:
    sensor = _make_opening_hours_sensor(None)
    assert sensor.native_value is None


def test_opening_hours_sensor_none_when_empty() -> None:
    sensor = _make_opening_hours_sensor({"opening_hours": ""})
    assert sensor.native_value is None


def test_opening_hours_sensor_extra_attrs_include_phone_website() -> None:
    sensor = _make_opening_hours_sensor(
        {"opening_hours": "24/7", "phone": "+353", "website": "http://bp.com"}
    )
    attrs = sensor.extra_state_attributes
    assert attrs["phone"] == "+353"
    assert attrs["website"] == "http://bp.com"
