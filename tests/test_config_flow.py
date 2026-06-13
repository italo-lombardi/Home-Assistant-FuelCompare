"""Tests for Fuel Compare config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelcompare_ie.config_flow import _fetch_station_name
from custom_components.fuelcompare_ie.const import (
    CONF_COUNTRY,
    CONF_PROVIDER,
    CONF_STATION_ID,
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DOMAIN,
)

_PATCH_FETCH_NAME = patch(
    "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
    new_callable=AsyncMock,
)
_PATCH_FIRST_REFRESH = patch(
    "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
    new_callable=AsyncMock,
)


# ---------------------------------------------------------------------------
# test_config_flow_valid_station_id
# ---------------------------------------------------------------------------


async def test_config_flow_valid_station_id(hass: HomeAssistant) -> None:
    """Submitting a valid station ID then confirming name creates a config entry."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = "Circle K"

        # Step 1: user (country) — auto-advances since only IE is available
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Single country auto-skips to station step
        assert result["type"] == "form"
        assert result["step_id"] == "station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "123"},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "name"
    assert result["data_schema"]({}) == {"name": "Circle K"}

    with _PATCH_FIRST_REFRESH:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"name": "Circle K"},
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_STATION_ID] == "123"
    assert result["data"][CONF_COUNTRY] == DEFAULT_COUNTRY
    assert result["data"][CONF_PROVIDER] == DEFAULT_PROVIDER
    assert result["title"] == "Circle K"


# ---------------------------------------------------------------------------
# test_config_flow_custom_name
# ---------------------------------------------------------------------------


async def test_config_flow_custom_name(hass: HomeAssistant) -> None:
    """User can override the pre-populated name in the name step."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = "Circle K"

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "station"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "123"},
        )

    assert result["step_id"] == "name"

    with _PATCH_FIRST_REFRESH:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"name": "My Station"},
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "My Station"


# ---------------------------------------------------------------------------
# test_config_flow_name_fetch_fails_uses_fallback
# ---------------------------------------------------------------------------


async def test_config_flow_name_fetch_fails_uses_fallback(
    hass: HomeAssistant,
) -> None:
    """When name fetch fails, name step defaults to 'Station {id}'."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = None

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "station"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "456"},
        )

    assert result["step_id"] == "name"
    assert result["data_schema"]({}) == {"name": "Station 456"}

    with _PATCH_FIRST_REFRESH:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"name": "Station 456"},
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Station 456"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_not_integer
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_not_integer(hass: HomeAssistant) -> None:
    """Non-integer station ID causes a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "station"

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
    assert result["step_id"] == "station"

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
    assert result["step_id"] == "station"

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
    assert result["step_id"] == "station"

    with _PATCH_FETCH_NAME:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "123"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# test_fetch_station_name_success
# ---------------------------------------------------------------------------


async def test_fetch_station_name_success(hass: HomeAssistant) -> None:
    """_fetch_station_name returns the name field when present."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_page_assets",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_nextjs",
            new_callable=AsyncMock,
            return_value={"name": "Circle K Mulhuddart", "tablename": "circle_k"},
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
    ):
        result = await _fetch_station_name(hass, "791")

    assert result == "Circle K Mulhuddart"


async def test_fetch_station_name_tablename_fallback(hass: HomeAssistant) -> None:
    """_fetch_station_name falls back to formatted tablename when name field absent."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_page_assets",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_nextjs",
            new_callable=AsyncMock,
            return_value={"tablename": "circle_k"},
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result == "Circle K"


async def test_fetch_station_name_encrypted_api_fallback(hass: HomeAssistant) -> None:
    """_fetch_station_name falls back to encrypted API when Next.js returns None."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_page_assets",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_nextjs",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_encrypted_api",
            new_callable=AsyncMock,
            return_value={"name": "Applegreen Cookstown", "tablename": "applegreen"},
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result == "Applegreen Cookstown"


async def test_fetch_station_name_no_name_no_tablename(hass: HomeAssistant) -> None:
    """_fetch_station_name returns None when station data has neither name nor tablename."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_page_assets",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_nextjs",
            new_callable=AsyncMock,
            return_value={"county": "Dublin"},
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result is None


async def test_fetch_station_name_exception_returns_none(hass: HomeAssistant) -> None:
    """_fetch_station_name returns None on any exception."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.FuelCompareIECoordinator._fetch_page_assets",
            new_callable=AsyncMock,
            side_effect=Exception("network error"),
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result is None


# ---------------------------------------------------------------------------
# test_unknown_provider_fallback
# ---------------------------------------------------------------------------


async def test_unknown_provider_key_falls_back_to_default(
    hass: HomeAssistant,
) -> None:
    """Entry with unknown provider key loads using the default provider."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_999",
        data={CONF_STATION_ID: "999", CONF_PROVIDER: "nonexistent_provider"},
        title="Station 999",
    )
    entry.add_to_hass(hass)

    with _PATCH_FIRST_REFRESH:
        assert await hass.config_entries.async_setup(entry.entry_id)

    # Entry loaded without error — default provider was used as fallback
    from custom_components.fuelcompare_ie.const import DOMAIN as _DOMAIN

    coordinator = hass.data[_DOMAIN][entry.entry_id]
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    assert isinstance(coordinator._provider, IEFuelCompareProvider)


# ---------------------------------------------------------------------------
# test_coordinator_2arg_compat
# ---------------------------------------------------------------------------


async def test_coordinator_2arg_compat(hass: HomeAssistant) -> None:
    """FuelCompareIECoordinator(hass, station_id_str) creates IEFuelCompareProvider."""
    from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    coordinator = FuelCompareIECoordinator(hass, "42")
    assert coordinator.station_id == "42"
    assert isinstance(coordinator._provider, IEFuelCompareProvider)
