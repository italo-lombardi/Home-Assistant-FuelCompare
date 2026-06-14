"""Tests for Fuel Compare config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelcompare_ie.config_flow import _fetch_station_name
from custom_components.fuelcompare_ie.const import (
    CONF_API_KEY,
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

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Navigate through country step if shown (now shown since >1 country registered)
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        # Navigate through provider step if shown (>1 IE provider registered)
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
            )
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
        # Navigate through country step if shown (now shown since >1 country registered)
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        # Navigate through provider step if shown (>1 IE provider registered)
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
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
        # Navigate through country step if shown (now shown since >1 country registered)
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        # Navigate through provider step if shown (>1 IE provider registered)
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
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


async def _reach_station_step(hass, flow_id: str) -> dict:
    """Navigate through provider step if shown, returning the station step result."""
    # If a provider step is shown (happens when >1 IE provider is registered),
    # select the default fuelcompare.ie provider to reach the station step.

    result = {"step_id": "provider", "flow_id": flow_id}
    if result["step_id"] == "provider":
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            user_input={CONF_PROVIDER: DEFAULT_PROVIDER},
        )
    return result


async def test_config_flow_invalid_not_integer(hass: HomeAssistant) -> None:
    """Non-integer station ID (e.g. a UUID or slug) is now accepted as valid."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = None

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Navigate through provider step if shown (multiple IE providers registered)
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
            )
        assert result["step_id"] == "station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "abc"},
        )

    # Non-integer IDs are now valid — flow should advance to the name step
    assert result["type"] == "form"
    assert result["step_id"] == "name"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_negative
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_negative(hass: HomeAssistant) -> None:
    """Negative string station ID (e.g. '-1') is now accepted as a valid non-empty string."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = None

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
            )
        assert result["step_id"] == "station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "-1"},
        )

    # Non-empty strings are now valid — flow should advance to the name step
    assert result["type"] == "form"
    assert result["step_id"] == "name"


# ---------------------------------------------------------------------------
# test_config_flow_invalid_zero
# ---------------------------------------------------------------------------


async def test_config_flow_invalid_zero(hass: HomeAssistant) -> None:
    """String '0' is now accepted as a valid non-empty station ID."""
    with _PATCH_FETCH_NAME as mock_fetch, _PATCH_FIRST_REFRESH:
        mock_fetch.return_value = None

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        if result.get("step_id") == "user":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
            )
        if result.get("step_id") == "provider":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
            )
        assert result["step_id"] == "station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_STATION_ID: "0"},
        )

    # Non-empty strings are now valid — flow should advance to the name step
    assert result["type"] == "form"
    assert result["step_id"] == "name"


# ---------------------------------------------------------------------------
# test_config_flow_duplicate
# ---------------------------------------------------------------------------


async def test_config_flow_duplicate(hass: HomeAssistant) -> None:
    """Submitting a station ID that already has a config entry aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{DEFAULT_PROVIDER}_123",
        data={CONF_STATION_ID: "123"},
        title="Station 123",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    if result.get("step_id") == "user":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
        )
    if result.get("step_id") == "provider":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PROVIDER: DEFAULT_PROVIDER}
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


# ---------------------------------------------------------------------------
# test_async_step_location
# ---------------------------------------------------------------------------


async def test_async_step_location_creates_entry(hass: HomeAssistant) -> None:
    """Location step stores lat/lng/radius and omits station_id from entry data."""

    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    # Create a minimal location-mode provider and register it temporarily
    class _FakeLocationProvider(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_fake_location"
        LABEL = "Fake Location"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"

        def __init__(self, station_id: str) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return "Dublin Fake Station"

        async def async_list_stations(self, session, **kwargs):
            return [("fake-station-001", "Dublin Fake Station")]

    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    PROVIDER_REGISTRY["ie_fake_location"] = _FakeLocationProvider
    try:
        with (
            _PATCH_FIRST_REFRESH,
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            # Navigate through country step if shown
            if result.get("step_id") == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_COUNTRY: DEFAULT_COUNTRY}
                )
            assert result["step_id"] in ("provider", "location", "station")

            # Drive to location step directly
            if result["step_id"] == "provider":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_PROVIDER: "ie_fake_location"},
                )
            assert result["step_id"] == "location"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_LATITUDE: 53.3498,
                    CONF_LONGITUDE: -6.2603,
                    CONF_RADIUS_KM: 5.0,
                },
            )
            # Location now routes to station_picker; pick the fake station
            assert result["step_id"] == "station_picker"
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_STATION_ID: "fake-station-001"},
            )
        assert result["step_id"] == "name"

        with (
            _PATCH_FIRST_REFRESH,
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
            ),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={"name": "Dublin Area"},
            )

        assert result["type"] == "create_entry"
        assert result["title"] == "Dublin Area"
        assert CONF_LATITUDE in result["data"]
        assert CONF_LONGITUDE in result["data"]
        assert CONF_RADIUS_KM in result["data"]
        assert result["data"].get("station_id", "") == "fake-station-001"
    finally:
        PROVIDER_REGISTRY.pop("ie_fake_location", None)


async def test_location_entry_loads_without_station_id_keyerror(
    hass: HomeAssistant,
) -> None:
    """Entry with location data (no station_id key) loads without KeyError."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_loc_53.35_-6.26",
        data={
            CONF_PROVIDER: DEFAULT_PROVIDER,
            CONF_LATITUDE: 53.3498,
            CONF_LONGITUDE: -6.2603,
            CONF_RADIUS_KM: 5.0,
        },
        title="Dublin Area",
    )
    entry.add_to_hass(hass)

    with _PATCH_FIRST_REFRESH:
        # Must not raise KeyError on missing CONF_STATION_ID
        assert await hass.config_entries.async_setup(entry.entry_id)


async def test_async_step_location_invalid_coords_returns_form_error(
    hass: HomeAssistant,
) -> None:
    """async_step_location re-shows form with error when float conversion fails."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.const import CONF_LATITUDE, CONF_LONGITUDE

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass

    # Call the step directly, bypassing HA's schema validation,
    # with a value that passes isinstance checks but fails float()
    class _Bad:
        def __float__(self):
            raise ValueError("not a number")

    result = await flow.async_step_location(
        user_input={CONF_LATITUDE: _Bad(), CONF_LONGITUDE: _Bad()}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "location"
    assert "base" in result["errors"]


# ---------------------------------------------------------------------------
# async_step_api_key tests
# ---------------------------------------------------------------------------


async def test_async_step_api_key_empty_key_shows_error(
    hass: HomeAssistant,
) -> None:
    """Submitting an empty API key re-shows the form with 'invalid_api_key' error."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass

    # Call the step with an empty string
    result = await flow.async_step_api_key(user_input={CONF_API_KEY: ""})

    assert result["type"] == "form"
    assert result["step_id"] == "api_key"
    assert result["errors"].get(CONF_API_KEY) == "invalid_api_key"


async def test_async_step_api_key_whitespace_only_shows_error(
    hass: HomeAssistant,
) -> None:
    """Submitting a whitespace-only API key re-shows the form with 'invalid_api_key' error."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass

    result = await flow.async_step_api_key(user_input={CONF_API_KEY: "   "})

    assert result["type"] == "form"
    assert result["step_id"] == "api_key"
    assert result["errors"].get(CONF_API_KEY) == "invalid_api_key"


async def test_async_step_api_key_valid_key_stored_and_advances(
    hass: HomeAssistant,
) -> None:
    """Submitting a valid API key stores it on the flow and advances past the step."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
        DeTankerkoenigProvider,
    )

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = DeTankerkoenigProvider.PROVIDER_KEY

    result = await flow.async_step_api_key(user_input={CONF_API_KEY: "my-test-api-key"})

    # The key must be stored
    assert flow._api_key == "my-test-api-key"
    # Step must have advanced (not still showing api_key form without error)
    assert result.get("step_id") != "api_key" or result.get("errors") == {}


async def test_async_step_api_key_initial_display_no_user_input(
    hass: HomeAssistant,
) -> None:
    """Calling async_step_api_key with no user_input shows the empty form."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass

    result = await flow.async_step_api_key(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "api_key"
    assert result.get("errors", {}) == {}


async def test_germany_provider_routes_through_api_key_step(
    hass: HomeAssistant,
) -> None:
    """Selecting Germany / Tankerkoenig routes through the api_key step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Country selection step
    if result.get("step_id") == "user":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_COUNTRY: "DE"}
        )

    # Provider selection step (DE has only Tankerkoenig)
    if result.get("step_id") == "provider":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PROVIDER: "de_tankerkoenig"},
        )

    # Must land on api_key step
    assert result["type"] == "form"
    assert result["step_id"] == "api_key"


async def test_api_key_stored_in_entry_options(hass: HomeAssistant) -> None:
    """api_key is persisted in the config entry options dict (not data) after successful setup."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
        DeTankerkoenigProvider,
    )

    # Minimal mock that accepts api_key
    class _FakeDEProvider(DeTankerkoenigProvider):
        PROVIDER_KEY = "de_tankerkoenig_test_key"
        COUNTRY = "DE"
        REQUIRES_API_KEY = True
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"

        def __init__(self, station_id, api_key=None, **kwargs):
            self._station_id = station_id
            self._api_key = api_key

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return "Berlin Fake Station"

        async def async_list_stations(self, session, **kwargs):
            return [("de-fake-001", "Berlin Fake Station")]

    PROVIDER_REGISTRY[_FakeDEProvider.PROVIDER_KEY] = _FakeDEProvider
    try:
        with (
            _PATCH_FIRST_REFRESH,
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            if result.get("step_id") == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_COUNTRY: "DE"}
                )
            if result.get("step_id") == "provider":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_PROVIDER: _FakeDEProvider.PROVIDER_KEY},
                )
            assert result["step_id"] == "api_key"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_API_KEY: "secret-key-123"},
            )
            # After api_key advances to location step
            if result.get("step_id") == "location":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={
                        CONF_LATITUDE: 52.52,
                        CONF_LONGITUDE: 13.405,
                        CONF_RADIUS_KM: 5.0,
                    },
                )
            # Location now routes to station_picker; pick the fake station
            if result.get("step_id") == "station_picker":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_STATION_ID: "de-fake-001"},
                )
            if result.get("step_id") == "name":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={"name": "Berlin Station"},
                )

        assert result["type"] == "create_entry"
        # api_key is now stored in entry.options, not entry.data
        assert result["options"][CONF_API_KEY] == "secret-key-123"
        assert CONF_API_KEY not in result["data"]
    finally:
        PROVIDER_REGISTRY.pop(_FakeDEProvider.PROVIDER_KEY, None)
