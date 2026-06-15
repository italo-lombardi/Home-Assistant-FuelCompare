"""Shared helper utilities for the Fuel Compare integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def _device_info(station_id: str, station_name: str, manufacturer: str) -> DeviceInfo:
    """Return shared device info for all sensors of a station."""
    return DeviceInfo(
        identifiers={(DOMAIN, station_id)},
        name=station_name,
        manufacturer=manufacturer,
        entry_type=DeviceEntryType.SERVICE,
    )
