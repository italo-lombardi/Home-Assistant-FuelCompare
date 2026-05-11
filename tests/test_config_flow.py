"""Tests for FuelCompare.ie config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelcompare_ie.const import CONF_STATION_ID, DOMAIN


# ---------------------------------------------------------------------------
# test_config_flow_valid_station_id
# ---------------------------------------------------------------------------


async def test_config_flow_valid_station_id(hass: HomeAssistant) -> None:
    """Submitting a valid station ID creates a config entry."""
    with patch(
        "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
        new_callable=AsyncMock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == "form"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "123"},
        )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_STATION_ID: "123"}
    assert result["title"] == "Station 123"


# ---------------------------------------------------------------------------
# test_config_flow_custom_name
# ---------------------------------------------------------------------------


async def test_config_flow_custom_name(hass: HomeAssistant) -> None:
    """Providing a custom name uses that name as the entry title."""
    from homeassistant.const import CONF_NAME

    with patch(
        "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
        new_callable=AsyncMock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "123", CONF_NAME: "My Station"},
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "My Station"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_not_integer
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_not_integer(hass: HomeAssistant) -> None:
    """Non-integer station ID causes a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_STATION_ID: "abc"},
    )

    assert result["type"] == "form"
    assert result["errors"].get(CONF_STATION_ID) == "invalid_station_id"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_negative
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_negative(hass: HomeAssistant) -> None:
    """Negative station ID causes a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_STATION_ID: "-1"},
    )

    assert result["type"] == "form"
    assert result["errors"].get(CONF_STATION_ID) == "invalid_station_id"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_zero
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_zero(hass: HomeAssistant) -> None:
    """Zero station ID causes a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_STATION_ID: "0"},
    )

    assert result["type"] == "form"
    assert result["errors"].get(CONF_STATION_ID) == "invalid_station_id"


# ---------------------------------------------------------------------------
# test_config_flow_duplicate
# ---------------------------------------------------------------------------


async def test_config_flow_duplicate(hass: HomeAssistant) -> None:
    """Submitting a station ID that already has a config entry aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_123",
        data={CONF_STATION_ID: "123"},
        title="Station 123",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_STATION_ID: "123"},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
