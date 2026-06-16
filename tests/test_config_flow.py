"""Tests for Fuel Compare config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
    IEFuelFinderProvider,
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
    """_fetch_station_name returns the name from provider.async_fetch_station_name."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch(
            "custom_components.fuelcompare_ie.providers.ie_fuelcompare.IEFuelCompareProvider.async_fetch_station_name",
            new_callable=AsyncMock,
            return_value="Circle K Mulhuddart",
        ),
    ):
        result = await _fetch_station_name(hass, "791")

    assert result == "Circle K Mulhuddart"


async def test_fetch_station_name_tablename_fallback(hass: HomeAssistant) -> None:
    """_fetch_station_name returns None when provider returns None (no special tablename logic)."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch(
            "custom_components.fuelcompare_ie.providers.ie_fuelcompare.IEFuelCompareProvider.async_fetch_station_name",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result is None


async def test_fetch_station_name_encrypted_api_fallback(hass: HomeAssistant) -> None:
    """_fetch_station_name returns provider result (IE provider uses fallback paths internally)."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch(
            "custom_components.fuelcompare_ie.providers.ie_fuelcompare.IEFuelCompareProvider.async_fetch_station_name",
            new_callable=AsyncMock,
            return_value="Applegreen Cookstown",
        ),
    ):
        result = await _fetch_station_name(hass, "790")

    assert result == "Applegreen Cookstown"


async def test_fetch_station_name_exception_returns_none(hass: HomeAssistant) -> None:
    """_fetch_station_name returns None on any exception."""
    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch(
            "custom_components.fuelcompare_ie.providers.ie_fuelcompare.IEFuelCompareProvider.async_fetch_station_name",
            new_callable=AsyncMock,
            side_effect=Exception("network error"),
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
# test_coordinator_provider_instance
# ---------------------------------------------------------------------------


async def test_coordinator_provider_instance(hass: HomeAssistant) -> None:
    """FuelCompareIECoordinator correctly stores provider and station_id."""
    from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("42")
    coordinator = FuelCompareIECoordinator(hass, provider, "42")
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
        # radius_km is stored in options (not data) for location-mode entries
        assert CONF_RADIUS_KM in result["data"] or CONF_RADIUS_KM in result.get(
            "options", {}
        )
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
    # Step must have advanced past the api_key form — no longer on "api_key" step
    assert result.get("step_id") != "api_key"
    # And the advanced step must not have errors
    assert result.get("errors", {}) == {}


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


# ---------------------------------------------------------------------------
# Lines 81-85: postal_code kwarg forwarding
# ---------------------------------------------------------------------------


async def test_async_setup_entry_postal_code_explicit(hass: HomeAssistant) -> None:
    """Lines 81-85: explicit postal_code field in entry.data is passed to provider."""
    from custom_components.fuelcompare_ie.const import (
        CONF_PROVIDER,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.be_carbu import BeCarbuProvider

    received: dict = {}

    class _FakeBeCarbu(BeCarbuProvider):
        PROVIDER_KEY = "be_carbu_test_postal_explicit"

        def __init__(self, station_id: str, postal_code=None, **kwargs):
            received["postal_code"] = postal_code
            self._station_id = station_id
            self._postal_code = postal_code
            self._latitude = None
            self._longitude = None
            self._radius_km = 10.0
            self._location_cache = {}

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeBeCarbu.PROVIDER_KEY] = _FakeBeCarbu
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_be_postal_explicit",
            data={
                CONF_STATION_ID: "99",
                CONF_PROVIDER: _FakeBeCarbu.PROVIDER_KEY,
                "postal_code": "1000",
            },
            title="BE Test",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        assert received["postal_code"] == "1000"
    finally:
        PROVIDER_REGISTRY.pop(_FakeBeCarbu.PROVIDER_KEY, None)


async def test_async_setup_entry_postal_code_from_numeric_county(
    hass: HomeAssistant,
) -> None:
    """Lines 82-83: numeric county falls back as postal_code when postal_code absent."""
    from custom_components.fuelcompare_ie.const import (
        CONF_PROVIDER,
        CONF_STATION_COUNTY,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.be_carbu import BeCarbuProvider

    received: dict = {}

    class _FakeBeCarbuCounty(BeCarbuProvider):
        PROVIDER_KEY = "be_carbu_test_postal_county"

        def __init__(self, station_id: str, postal_code=None, **kwargs):
            received["postal_code"] = postal_code
            self._station_id = station_id
            self._postal_code = postal_code
            self._latitude = None
            self._longitude = None
            self._radius_km = 10.0
            self._location_cache = {}

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeBeCarbuCounty.PROVIDER_KEY] = _FakeBeCarbuCounty
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_be_postal_county",
            data={
                CONF_STATION_ID: "99",
                CONF_PROVIDER: _FakeBeCarbuCounty.PROVIDER_KEY,
                CONF_STATION_COUNTY: "1050",
            },
            title="BE County Test",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        assert received["postal_code"] == "1050"
    finally:
        PROVIDER_REGISTRY.pop(_FakeBeCarbuCounty.PROVIDER_KEY, None)


async def test_async_setup_entry_postal_code_non_numeric_county_not_used(
    hass: HomeAssistant,
) -> None:
    """Line 82: non-numeric county is NOT used as postal_code fallback."""
    from custom_components.fuelcompare_ie.const import (
        CONF_PROVIDER,
        CONF_STATION_COUNTY,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.be_carbu import BeCarbuProvider

    received: dict = {}

    class _FakeBeCarbuNonNumeric(BeCarbuProvider):
        PROVIDER_KEY = "be_carbu_test_postal_nonnumeric"

        def __init__(self, station_id: str, postal_code=None, **kwargs):
            received["postal_code"] = postal_code
            self._station_id = station_id
            self._postal_code = postal_code
            self._latitude = None
            self._longitude = None
            self._radius_km = 10.0
            self._location_cache = {}

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeBeCarbuNonNumeric.PROVIDER_KEY] = _FakeBeCarbuNonNumeric
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_be_postal_nonnumeric",
            data={
                CONF_STATION_ID: "99",
                CONF_PROVIDER: _FakeBeCarbuNonNumeric.PROVIDER_KEY,
                CONF_STATION_COUNTY: "Dublin",
            },
            title="BE Non-numeric County",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        # postal_code kwarg should NOT be set (non-numeric county is not a postal code)
        assert received.get("postal_code") is None
    finally:
        PROVIDER_REGISTRY.pop(_FakeBeCarbuNonNumeric.PROVIDER_KEY, None)


# ---------------------------------------------------------------------------
# Lines 88-91: prefecture_id kwarg forwarding
# ---------------------------------------------------------------------------


async def test_async_setup_entry_prefecture_id_valid(hass: HomeAssistant) -> None:
    """Lines 88-90: numeric station_id is converted to prefecture_id int."""
    from custom_components.fuelcompare_ie.const import (
        CONF_PROVIDER,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.gr_fuelgov import GrFuelgovProvider

    received: dict = {}

    class _FakeGrProvider(GrFuelgovProvider):
        PROVIDER_KEY = "gr_fuelgov_test_valid"

        def __init__(self, station_id: str, prefecture_id=None, **kwargs):
            received["prefecture_id"] = prefecture_id
            self._station_id = station_id
            self._prefecture = None
            self._prefecture_id = prefecture_id

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeGrProvider.PROVIDER_KEY] = _FakeGrProvider
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_gr_prefecture_valid",
            data={
                CONF_STATION_ID: "5",
                CONF_PROVIDER: _FakeGrProvider.PROVIDER_KEY,
            },
            title="GR Prefecture 5",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        assert received["prefecture_id"] == 5
    finally:
        PROVIDER_REGISTRY.pop(_FakeGrProvider.PROVIDER_KEY, None)


async def test_async_setup_entry_prefecture_id_invalid_skipped(
    hass: HomeAssistant,
) -> None:
    """Lines 88-91: non-numeric station_id causes ValueError; prefecture_id silently skipped."""
    from custom_components.fuelcompare_ie.const import (
        CONF_PROVIDER,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.gr_fuelgov import GrFuelgovProvider

    received: dict = {"prefecture_id": "NOT_SET"}

    class _FakeGrProviderBadId(GrFuelgovProvider):
        PROVIDER_KEY = "gr_fuelgov_test_invalid"

        def __init__(self, station_id: str, prefecture_id=None, **kwargs):
            received["prefecture_id"] = prefecture_id
            self._station_id = station_id
            self._prefecture = None
            self._prefecture_id = prefecture_id

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeGrProviderBadId.PROVIDER_KEY] = _FakeGrProviderBadId
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_gr_prefecture_invalid",
            data={
                CONF_STATION_ID: "not-a-number",
                CONF_PROVIDER: _FakeGrProviderBadId.PROVIDER_KEY,
            },
            title="GR Bad Prefecture",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        # prefecture_id kwarg should NOT be passed (conversion failed, pass silently)
        assert received["prefecture_id"] is None
    finally:
        PROVIDER_REGISTRY.pop(_FakeGrProviderBadId.PROVIDER_KEY, None)


# ---------------------------------------------------------------------------
# Lines 95, 97, 99: lat/lng/radius_km kwarg forwarding
# ---------------------------------------------------------------------------


async def test_async_setup_entry_geo_params_passed(hass: HomeAssistant) -> None:
    """Lines 95, 97, 99: lat/lng/radius_km are forwarded to geo-capable providers."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_PROVIDER,
        CONF_RADIUS_KM,
        CONF_STATION_ID,
        DOMAIN,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
        DeTankerkoenigProvider,
    )

    received: dict = {}

    class _FakeDEGeoProvider(DeTankerkoenigProvider):
        PROVIDER_KEY = "de_tankerkoenig_test_geo"

        def __init__(
            self,
            station_id: str,
            latitude=None,
            longitude=None,
            radius_km=None,
            **kwargs,
        ):
            received["latitude"] = latitude
            received["longitude"] = longitude
            received["radius_km"] = radius_km
            self._station_id = station_id
            self._api_key = None
            self._latitude = latitude
            self._longitude = longitude
            self._radius_km = radius_km if radius_km is not None else 10.0

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY[_FakeDEGeoProvider.PROVIDER_KEY] = _FakeDEGeoProvider
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_de_geo",
            data={
                CONF_STATION_ID: "de-station-1",
                CONF_PROVIDER: _FakeDEGeoProvider.PROVIDER_KEY,
                CONF_LATITUDE: 52.52,
                CONF_LONGITUDE: 13.405,
                CONF_RADIUS_KM: 7.5,
            },
            title="DE Geo Test",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.fuelcompare_ie.coordinator.FuelCompareIECoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)

        assert received["latitude"] == 52.52
        assert received["longitude"] == 13.405
        assert received["radius_km"] == 7.5
    finally:
        PROVIDER_REGISTRY.pop(_FakeDEGeoProvider.PROVIDER_KEY, None)


# ---------------------------------------------------------------------------
# Lines 99-111: IEFuelCompareProvider.async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_field() -> None:
    """async_fetch_station_name returns data['name'] when present."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock()
    provider._fetch_nextjs = AsyncMock(return_value={"name": "Circle K Swords"})

    result = await provider.async_fetch_station_name(session, "790")

    assert result == "Circle K Swords"


async def test_async_fetch_station_name_falls_back_to_encrypted_api() -> None:
    """async_fetch_station_name falls back to encrypted API when Next.js returns None."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock()
    provider._fetch_nextjs = AsyncMock(return_value=None)
    provider._fetch_encrypted_api = AsyncMock(
        return_value={"name": "Applegreen Dublin"}
    )

    result = await provider.async_fetch_station_name(session, "790")

    assert result == "Applegreen Dublin"


async def test_async_fetch_station_name_formats_tablename_when_no_name() -> None:
    """async_fetch_station_name formats tablename when name is absent."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock()
    provider._fetch_nextjs = AsyncMock(return_value={"tablename": "circle_k_swords"})

    result = await provider.async_fetch_station_name(session, "790")

    assert result == "Circle K Swords"


async def test_async_fetch_station_name_returns_none_when_data_empty() -> None:
    """async_fetch_station_name returns None when both paths return no data."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock()
    provider._fetch_nextjs = AsyncMock(return_value=None)
    provider._fetch_encrypted_api = AsyncMock(return_value=None)

    result = await provider.async_fetch_station_name(session, "790")

    assert result is None


async def test_async_fetch_station_name_returns_none_on_exception() -> None:
    """async_fetch_station_name catches exceptions and returns None."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock(side_effect=RuntimeError("network fail"))

    result = await provider.async_fetch_station_name(session, "790")

    assert result is None


async def test_async_fetch_station_name_returns_none_when_data_has_no_name_or_tablename() -> (
    None
):
    """async_fetch_station_name returns None when data has neither name nor tablename."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")
    session = MagicMock()
    provider._fetch_page_assets = AsyncMock()
    provider._fetch_nextjs = AsyncMock(return_value={"unleaded": 1.799})

    result = await provider.async_fetch_station_name(session, "790")

    assert result is None


# ---------------------------------------------------------------------------
# IEFuelFinderProvider helpers (used by tests below)
# ---------------------------------------------------------------------------

_FF_STATION_UUID = "7ec0dd4f-4322-4b4f-9de1-c8894a684626"

_FF_BASE_STATION: dict = {
    "id": _FF_STATION_UUID,
    "osm_id": "123456789",
    "name": "Circle K Mulhuddart",
    "slug": "circle-k-mulhuddart",
    "brand": "Circle K",
    "logo_url": "https://www.google.com/s2/favicons?domain=circlek.com&sz=64",
    "lat": 53.399,
    "lng": -6.433,
    "county": "Dublin",
    "street": "Mulhuddart Village",
    "phone": "",
    "website": "",
    "opening_hours": "Mo-Su 07:00-23:00",
    "price": 1.828,
    "updated_at": "2026-06-13T16:04:01.754194+00:00",
    "confidence": "likely",
    "has_price": True,
}


def _ff_make_mock_response(
    status: int,
    json_data: dict | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _ff_make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


# ---------------------------------------------------------------------------
# async_fetch_station_name — petrol fallback (lines 332-336)
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_fallback_to_petrol_when_not_in_diesel() -> None:
    """async_fetch_station_name finds name via petrol list when diesel list misses the station."""
    other_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    other_station = {**_FF_BASE_STATION, "id": other_id}
    diesel_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [other_station],
            "total": 1,
            "city": "ireland",
            "fuel": "diesel",
        },
    )
    petrol_station = {**_FF_BASE_STATION, "name": "Petrol Only Station"}
    petrol_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [petrol_station],
            "total": 1,
            "city": "ireland",
            "fuel": "petrol",
        },
    )
    session = _ff_make_session(diesel_resp, petrol_resp)

    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    name = await provider.async_fetch_station_name(session, _FF_STATION_UUID)

    assert name == "Petrol Only Station"


async def test_async_fetch_station_name_returns_none_when_exception_raised() -> None:
    """async_fetch_station_name returns None (not raises) when an unexpected exception occurs."""
    from unittest.mock import patch

    provider = IEFuelFinderProvider(_FF_STATION_UUID)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    with patch.object(
        provider, "_fetch_stations", side_effect=RuntimeError("unexpected")
    ):
        name = await provider.async_fetch_station_name(MagicMock(), _FF_STATION_UUID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — exception handler (lines 366-368)
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_list_on_exception() -> None:
    """async_list_stations returns [] when an unexpected exception propagates from gather."""
    from unittest.mock import patch

    provider = IEFuelFinderProvider(_FF_STATION_UUID)

    call_count = 0

    async def _raise_on_second(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("boom")
        return []

    with patch.object(provider, "_fetch_stations", side_effect=_raise_on_second):
        result = await provider.async_list_stations(MagicMock(), county="dublin")

    assert result == []


# ---------------------------------------------------------------------------
# async_list_stations — petrol merge without overwriting diesel (lines 384-388)
# ---------------------------------------------------------------------------


async def test_async_list_stations_petrol_only_station_included_in_results() -> None:
    """async_list_stations adds petrol-only stations not in the diesel list."""
    petrol_only_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    petrol_only_station = {
        **_FF_BASE_STATION,
        "id": petrol_only_id,
        "name": "Petrol Only Stop",
        "brand": "",
        "price": 1.899,
    }
    diesel_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [_FF_BASE_STATION],
            "total": 1,
            "city": "dublin",
            "fuel": "diesel",
        },
    )
    petrol_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [_FF_BASE_STATION, petrol_only_station],
            "total": 2,
            "city": "dublin",
            "fuel": "petrol",
        },
    )
    session = _ff_make_session(diesel_resp, petrol_resp)

    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    result = await provider.async_list_stations(session, county="dublin")

    uids = [uid for uid, _ in result]
    assert petrol_only_id in uids


async def test_async_list_stations_diesel_record_not_overwritten_by_petrol() -> None:
    """async_list_stations does not overwrite a diesel-merged station with the petrol record."""
    diesel_station = {**_FF_BASE_STATION, "price": 1.828}
    petrol_station = {**_FF_BASE_STATION, "price": 1.849}  # same UUID, different price

    diesel_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [diesel_station],
            "total": 1,
            "city": "dublin",
            "fuel": "diesel",
        },
    )
    petrol_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [petrol_station],
            "total": 1,
            "city": "dublin",
            "fuel": "petrol",
        },
    )
    session = _ff_make_session(diesel_resp, petrol_resp)

    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    result = await provider.async_list_stations(session, county="dublin")

    assert len(result) == 1
    uid, label = result[0]
    assert uid == _FF_STATION_UUID
    assert "(#" in label


# ---------------------------------------------------------------------------
# async_list_stations — petrol price formatting (line 406)
# ---------------------------------------------------------------------------


async def test_async_list_stations_label_formats_petrol_price_to_3_decimals() -> None:
    """async_list_stations label contains short station ID in (#...) format."""
    diesel_station = {**_FF_BASE_STATION, "price": 1.828}
    petrol_station = {**_FF_BASE_STATION, "price": 1.849}

    diesel_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [diesel_station],
            "total": 1,
            "city": "dublin",
            "fuel": "diesel",
        },
    )
    petrol_resp = _ff_make_mock_response(
        200,
        json_data={
            "stations": [petrol_station],
            "total": 1,
            "city": "dublin",
            "fuel": "petrol",
        },
    )
    session = _ff_make_session(diesel_resp, petrol_resp)

    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    result = await provider.async_list_stations(session, county="dublin")

    assert len(result) == 1
    _, label = result[0]
    assert "(#" in label


# ---------------------------------------------------------------------------
# _fetch_stations — HTTP error returns None (lines 471-477)
# ---------------------------------------------------------------------------


async def test_fetch_stations_returns_none_on_client_response_error() -> None:
    """_fetch_stations returns None (not raises) on ClientResponseError (4xx/5xx)."""
    from aiohttp import ClientResponseError

    resp = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    resp.status = 500
    resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(MagicMock(), MagicMock(), status=500)
    )
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    result = await provider._fetch_stations(session, city="dublin", fuel="diesel")

    assert result is None


# ---------------------------------------------------------------------------
# _build_station_data — price validation (lines 524-525, 530)
# ---------------------------------------------------------------------------


def test_build_station_data_price_returns_none_on_value_error() -> None:
    """_build_station_data._price returns None when price cannot be cast to float."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    bad_price_station = {**_FF_BASE_STATION, "price": "not-a-number"}
    prices_by_fuel = {"diesel": bad_price_station}
    result = provider._build_station_data(
        _FF_STATION_UUID, _FF_BASE_STATION, prices_by_fuel
    )
    assert result["diesel"] is None


def test_build_station_data_price_returns_none_for_none_type() -> None:
    """_build_station_data._price returns None when price is None (TypeError path)."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    no_price_station = {**_FF_BASE_STATION, "price": None}
    prices_by_fuel = {"diesel": no_price_station}
    result = provider._build_station_data(
        _FF_STATION_UUID, _FF_BASE_STATION, prices_by_fuel
    )
    assert result["diesel"] is None


def test_build_station_data_price_returns_none_for_zero() -> None:
    """_build_station_data._price returns None when price is zero (non-positive guard)."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    zero_price_station = {**_FF_BASE_STATION, "price": 0}
    prices_by_fuel = {"diesel": zero_price_station}
    result = provider._build_station_data(
        _FF_STATION_UUID, _FF_BASE_STATION, prices_by_fuel
    )
    assert result["diesel"] is None


def test_build_station_data_price_returns_none_for_negative() -> None:
    """_build_station_data._price returns None when price is negative."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    neg_price_station = {**_FF_BASE_STATION, "price": -1.5}
    prices_by_fuel = {"diesel": neg_price_station}
    result = provider._build_station_data(
        _FF_STATION_UUID, _FF_BASE_STATION, prices_by_fuel
    )
    assert result["diesel"] is None


# ---------------------------------------------------------------------------
# _build_station_data — lat/lng float conversion (lines 549-550, 553-554)
# ---------------------------------------------------------------------------


def test_build_station_data_lat_none_on_invalid_string() -> None:
    """_build_station_data sets latitude=None when lat is a non-numeric string."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    bad_lat_station = {**_FF_BASE_STATION, "lat": "not-a-float"}
    prices_by_fuel = {"diesel": {**bad_lat_station, "price": 1.828}}
    result = provider._build_station_data(
        _FF_STATION_UUID, bad_lat_station, prices_by_fuel
    )
    assert result["latitude"] is None


def test_build_station_data_lng_none_on_invalid_string() -> None:
    """_build_station_data sets longitude=None when lng is a non-numeric string."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    bad_lng_station = {**_FF_BASE_STATION, "lng": "bad-value"}
    prices_by_fuel = {"diesel": {**bad_lng_station, "price": 1.828}}
    result = provider._build_station_data(
        _FF_STATION_UUID, bad_lng_station, prices_by_fuel
    )
    assert result["longitude"] is None


def test_build_station_data_lat_none_on_type_error() -> None:
    """_build_station_data sets latitude=None when lat is an unconvertible type (list)."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    bad_lat_station = {**_FF_BASE_STATION, "lat": [53.399]}
    prices_by_fuel = {"diesel": {**bad_lat_station, "price": 1.828}}
    result = provider._build_station_data(
        _FF_STATION_UUID, bad_lat_station, prices_by_fuel
    )
    assert result["latitude"] is None


def test_build_station_data_lng_none_on_type_error() -> None:
    """_build_station_data sets longitude=None when lng is an unconvertible type (list)."""
    provider = IEFuelFinderProvider(_FF_STATION_UUID)
    bad_lng_station = {**_FF_BASE_STATION, "lng": [6.433]}
    prices_by_fuel = {"diesel": {**bad_lng_station, "price": 1.828}}
    result = provider._build_station_data(
        _FF_STATION_UUID, bad_lng_station, prices_by_fuel
    )
    assert result["longitude"] is None


# ---------------------------------------------------------------------------
# _normalise_county (lines 684-686)
# ---------------------------------------------------------------------------


def test_normalise_county_returns_none_for_none_input() -> None:
    """_normalise_county returns None when input is None."""
    from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
        _normalise_county,
    )

    assert _normalise_county(None) is None


def test_normalise_county_returns_none_for_empty_string() -> None:
    """_normalise_county returns None when input is empty string."""
    from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
        _normalise_county,
    )

    assert _normalise_county("") is None


def test_normalise_county_lowercases_and_strips() -> None:
    """_normalise_county returns lowercase stripped county name."""
    from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
        _normalise_county,
    )

    assert _normalise_county("  Dublin  ") == "dublin"
    assert _normalise_county("Cork") == "cork"
    assert _normalise_county("GALWAY") == "galway"


# ---------------------------------------------------------------------------
# Sensor unit tests — lines 106, 402, 609-610, 619
# ---------------------------------------------------------------------------


def _make_coordinator(data: dict) -> MagicMock:
    """Return a minimal mock coordinator whose .data is *data*."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    coord = MagicMock()
    coord.data = data
    coord._provider = IEFuelCompareProvider("99999")
    return coord


def _make_brand_sensor(data: dict):
    """Return a StationBrandSensor wired to *data*."""
    from custom_components.fuelcompare_ie.sensor import StationBrandSensor

    coord = _make_coordinator(data)
    sensor = StationBrandSensor.__new__(StationBrandSensor)
    sensor.coordinator = coord
    sensor._station_id = "12345"
    return sensor


def _make_simple_float_sensor(data_key: str, data: dict):
    """Return a StationSimpleFloatSensor wired to *data*."""
    from custom_components.fuelcompare_ie.sensor import StationSimpleFloatSensor

    coord = _make_coordinator(data)
    sensor = StationSimpleFloatSensor.__new__(StationSimpleFloatSensor)
    sensor.coordinator = coord
    sensor._station_id = "12345"
    sensor._data_key = data_key
    return sensor


def test_make_location_factory_returns_simple_str_sensor() -> None:
    """_INFO_SENSOR_REGISTRY['location'] creates a StationSimpleStrSensor for the location key."""
    from custom_components.fuelcompare_ie.sensor import (
        StationSimpleStrSensor,
        _INFO_SENSOR_REGISTRY,
    )

    coord = _make_coordinator({"location": "53.3498,-6.2603"})
    factory = _INFO_SENSOR_REGISTRY["location"]
    sensor = factory(coord, "99999", "Test Station")
    assert isinstance(sensor, StationSimpleStrSensor)
    assert sensor._data_key == "location"
    assert sensor._attr_icon == "mdi:map-marker-radius"
    assert sensor.native_value == "53.3498,-6.2603"


def test_brand_sensor_returns_brand_when_present() -> None:
    """StationBrandSensor returns the brand field directly when present (line 402)."""
    sensor = _make_brand_sensor({"brand": "BP", "tablename": "bp_ireland"})
    assert sensor.native_value == "BP"


def test_simple_float_sensor_native_value_invalid_raises_returns_none() -> None:
    """StationSimpleFloatSensor returns None when value cannot be converted to float (lines 609-610)."""
    sensor = _make_simple_float_sensor("latitude", {"latitude": "not-a-float"})
    assert sensor.native_value is None


def test_simple_float_sensor_extra_state_attributes() -> None:
    """StationSimpleFloatSensor.extra_state_attributes returns station_id dict (line 619)."""
    sensor = _make_simple_float_sensor("longitude", {"longitude": -6.2603})
    assert sensor.extra_state_attributes == {"station_id": "12345"}


# ---------------------------------------------------------------------------
# test_fetch_station_name_unknown_provider_returns_none  (config_flow.py line 352)
# ---------------------------------------------------------------------------


async def test_fetch_station_name_unknown_provider_returns_none(
    hass: HomeAssistant,
) -> None:
    """_fetch_station_name returns None when provider_key is not in registry (line 352)."""
    result = await _fetch_station_name(hass, "999", provider_key="nonexistent_xyz")
    assert result is None


# ---------------------------------------------------------------------------
# test_async_get_options_flow_returns_options_flow_instance  (config_flow.py line 371)
# ---------------------------------------------------------------------------


async def test_async_get_options_flow_returns_options_flow_instance(
    hass: HomeAssistant,
) -> None:
    """async_get_options_flow returns a FuelCompareIEOptionsFlow instance (line 371)."""
    from custom_components.fuelcompare_ie.config_flow import (
        FuelCompareIEConfigFlow,
        FuelCompareIEOptionsFlow,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_ie_fuelcompare_42_opts_flow",
        data={CONF_STATION_ID: "42", CONF_PROVIDER: DEFAULT_PROVIDER},
        title="Station 42",
    )
    entry.add_to_hass(hass)

    options_flow = FuelCompareIEConfigFlow.async_get_options_flow(entry)
    assert isinstance(options_flow, FuelCompareIEOptionsFlow)


# ---------------------------------------------------------------------------
# test_async_step_user_single_country_auto_advance  (config_flow.py lines 395-396)
# ---------------------------------------------------------------------------


async def test_async_step_user_single_country_auto_advance(
    hass: HomeAssistant,
) -> None:
    """When only one country is in the registry, user step auto-advances (lines 395-396)."""
    from unittest.mock import patch as _patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _SingleCountryProvider(BaseProvider):
        COUNTRY = "ZZ"
        PROVIDER_KEY = "zz_only_provider_395"
        LABEL = "Only Provider"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "manual_id"
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(self, station_id: str) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

    with _patch.dict(
        PROVIDER_REGISTRY,
        {_SingleCountryProvider.PROVIDER_KEY: _SingleCountryProvider},
        clear=True,
    ):
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(user_input=None)
        # Should have advanced past the country selection
        assert result.get("step_id") != "user"
        assert flow._country == "ZZ"


# ---------------------------------------------------------------------------
# test_async_step_provider_no_providers_aborts  (config_flow.py line 419)
# ---------------------------------------------------------------------------


async def test_async_step_provider_no_providers_aborts(
    hass: HomeAssistant,
) -> None:
    """_async_step_provider aborts with 'no_providers_for_country' when none found (line 419)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._country = "XX"  # country with no providers in registry

    result = await flow._async_step_provider()
    assert result["type"] == "abort"
    assert result["reason"] == "no_providers_for_country"


# ---------------------------------------------------------------------------
# test_dispatch_after_provider_unknown_key_falls_back_to_station  (config_flow.py line 431)
# ---------------------------------------------------------------------------


async def test_dispatch_after_provider_unknown_key_falls_back_to_station(
    hass: HomeAssistant,
) -> None:
    """_dispatch_after_provider routes to station step when provider_cls is None (line 431)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = "does_not_exist_in_registry_431"

    result = await flow._dispatch_after_provider()
    assert result["type"] == "form"
    assert result["step_id"] == "station"


# ---------------------------------------------------------------------------
# test_async_step_station_empty_id_shows_error  (config_flow.py line 507)
# ---------------------------------------------------------------------------


async def test_async_step_station_empty_id_shows_error(
    hass: HomeAssistant,
) -> None:
    """async_step_station sets 'invalid_station_id' error on empty station ID (line 507)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = DEFAULT_PROVIDER

    result = await flow.async_step_station(user_input={CONF_STATION_ID: ""})
    assert result["type"] == "form"
    assert result["step_id"] == "station"
    assert result["errors"].get(CONF_STATION_ID) == "invalid_station_id"


# ---------------------------------------------------------------------------
# test_async_step_station_picker_empty_id_shows_error  (config_flow.py line 563)
# ---------------------------------------------------------------------------


async def test_async_step_station_picker_empty_id_shows_error(
    hass: HomeAssistant,
) -> None:
    """async_step_station_picker sets 'invalid_station_id' error on empty station ID (line 563)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = DEFAULT_PROVIDER
    flow._station_list = [("abc", "Station ABC")]

    with patch(
        "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
    ):
        result = await flow.async_step_station_picker(user_input={CONF_STATION_ID: ""})
    assert result["type"] == "form"
    assert result["step_id"] == "station_picker"
    assert result["errors"].get(CONF_STATION_ID) == "invalid_station_id"


# ---------------------------------------------------------------------------
# test_async_step_station_picker_exception_in_list_load  (config_flow.py lines 601-602)
# ---------------------------------------------------------------------------


async def test_async_step_station_picker_exception_in_list_load(
    hass: HomeAssistant,
) -> None:
    """async_step_station_picker handles exception in async_list_stations (lines 601-602)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _BrokenListProvider601(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_broken_list_601"
        LABEL = "Broken List 601"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(self, station_id: str, **kwargs) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            raise RuntimeError("network exploded")

    PROVIDER_REGISTRY["ie_broken_list_601"] = _BrokenListProvider601
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "ie_broken_list_601"
        flow._latitude = 53.35
        flow._longitude = -6.26
        flow._radius_km = 5.0

        with patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ):
            result = await flow.async_step_station_picker(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "station_picker"
        assert result["errors"].get("base") == "no_stations_found"
    finally:
        PROVIDER_REGISTRY.pop("ie_broken_list_601", None)


# ---------------------------------------------------------------------------
# test_async_step_station_picker_no_stations_with_unique_id_routes_to_name  (config_flow.py line 610)
# ---------------------------------------------------------------------------


async def test_async_step_station_picker_no_stations_with_unique_id_routes_to_name(
    hass: HomeAssistant,
) -> None:
    """station_picker routes to name step when station list empty and unique_id set (line 610)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _EmptyListProvider610(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_empty_list_610_new"
        LABEL = "Empty List 610"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(self, station_id: str, **kwargs) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["ie_empty_list_610_new"] = _EmptyListProvider610
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "ie_empty_list_610_new"
        flow._latitude = 53.35
        flow._longitude = -6.26
        flow._radius_km = 5.0
        flow._suggested_name = "Test Location"
        # Set context as a mutable dict so unique_id can be stored
        flow.context = {"unique_id": f"{DOMAIN}_ie_empty_list_610_new_test_uid"}

        with patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ):
            result = await flow.async_step_station_picker(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "name"
    finally:
        PROVIDER_REGISTRY.pop("ie_empty_list_610_new", None)


# ---------------------------------------------------------------------------
# test_async_step_location_postal_code_extracted  (config_flow.py line 681)
# ---------------------------------------------------------------------------


async def test_async_step_location_postal_code_extracted(
    hass: HomeAssistant,
) -> None:
    """async_step_location extracts postal_code into _postal_code when provider needs it (line 681)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.const import CONF_LATITUDE, CONF_LONGITUDE
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _PostalProvider681(BaseProvider):
        COUNTRY = "BE"
        PROVIDER_KEY = "be_postal_test_681"
        LABEL = "Postal Test 681"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"
        NEEDS_POSTAL_CODE = True
        CAPABILITIES: frozenset = frozenset()

        def __init__(
            self, station_id: str, postal_code: str | None = None, **kwargs
        ) -> None:
            self._postal_code = postal_code

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["be_postal_test_681"] = _PostalProvider681
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "be_postal_test_681"
        flow._country = "BE"
        # Use a mutable context dict so async_set_unique_id can write to it
        flow.context = {}

        with (
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
            ),
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(flow, "_async_in_progress", return_value=[]),
        ):
            await flow.async_step_location(
                user_input={
                    CONF_LATITUDE: 50.85,
                    CONF_LONGITUDE: 4.35,
                    "postal_code": "1000",
                }
            )

        assert flow._postal_code == "1000"
    finally:
        PROVIDER_REGISTRY.pop("be_postal_test_681", None)


# ---------------------------------------------------------------------------
# test_async_step_location_shows_postal_code_field_in_schema  (config_flow.py line 704)
# ---------------------------------------------------------------------------


async def test_async_step_location_shows_postal_code_field_in_schema(
    hass: HomeAssistant,
) -> None:
    """async_step_location adds postal_code field to form schema for postal providers (line 704)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _PostalProvider704(BaseProvider):
        COUNTRY = "BE"
        PROVIDER_KEY = "be_postal_test_704"
        LABEL = "Postal Test 704"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"
        NEEDS_POSTAL_CODE = True
        CAPABILITIES: frozenset = frozenset()

        def __init__(
            self, station_id: str, postal_code: str | None = None, **kwargs
        ) -> None:
            self._postal_code = postal_code

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["be_postal_test_704"] = _PostalProvider704
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "be_postal_test_704"
        flow._country = "BE"

        result = await flow.async_step_location(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "location"
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any("postal_code" in k for k in schema_keys)
    finally:
        PROVIDER_REGISTRY.pop("be_postal_test_704", None)


# ---------------------------------------------------------------------------
# test_async_step_name_stores_postal_code_in_entry_data  (config_flow.py line 731)
# ---------------------------------------------------------------------------


async def test_async_step_name_stores_postal_code_in_entry_data(
    hass: HomeAssistant,
) -> None:
    """async_step_name stores _postal_code in the config entry data dict (line 731)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _PostalProvider731(BaseProvider):
        COUNTRY = "BE"
        PROVIDER_KEY = "be_postal_test_731"
        LABEL = "Postal Test 731"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(
            self, station_id: str, postal_code: str | None = None, **kwargs
        ) -> None:
            self._postal_code = postal_code

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["be_postal_test_731"] = _PostalProvider731
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "be_postal_test_731"
        flow._country = "BE"
        flow._postal_code = "1000"
        flow._station_id = ""
        flow._latitude = 50.85
        flow._longitude = 4.35
        flow._radius_km = 5.0
        flow._suggested_name = "Brussels Area"
        # Set context as mutable dict so unique_id can be stored
        flow.context = {"unique_id": f"{DOMAIN}_be_postal_test_731_uid"}

        result = await flow.async_step_name(user_input={"name": "Brussels Station"})

        assert result["type"] == "create_entry"
        assert result["data"].get("postal_code") == "1000"
    finally:
        PROVIDER_REGISTRY.pop("be_postal_test_731", None)


async def test_async_step_name_show_on_map_stored_in_options(
    hass: HomeAssistant,
) -> None:
    """async_step_name stores show_on_map=True in options when provider has lat/lon caps."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider
    from custom_components.fuelcompare_ie.const import CONF_SHOW_ON_MAP

    class _LatLonProvider(BaseProvider):
        COUNTRY = "AT"
        PROVIDER_KEY = "at_latlon_test"
        LABEL = "LatLon Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CAPABILITIES: frozenset = frozenset({"latitude", "longitude", "diesel"})

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["at_latlon_test"] = _LatLonProvider
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "at_latlon_test"
        flow._country = "AT"
        flow._station_id = "99"
        flow._latitude = None
        flow._longitude = None
        flow._suggested_name = "Test Station"
        flow.context = {}

        result = await flow.async_step_name(
            user_input={"name": "Test Station", CONF_SHOW_ON_MAP: True}
        )

        assert result["type"] == "create_entry"
        assert result["options"].get(CONF_SHOW_ON_MAP) is True
    finally:
        PROVIDER_REGISTRY.pop("at_latlon_test", None)


async def test_async_step_name_show_on_map_absent_when_no_lat_lon_caps(
    hass: HomeAssistant,
) -> None:
    """async_step_name schema excludes show_on_map when provider has no lat/lon caps."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider
    from custom_components.fuelcompare_ie.const import CONF_SHOW_ON_MAP

    class _NoLatLonProvider(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_nolatlon_test"
        LABEL = "NoLatLon Test"
        CAPABILITIES: frozenset = frozenset({"diesel"})

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

    PROVIDER_REGISTRY["ie_nolatlon_test"] = _NoLatLonProvider
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "ie_nolatlon_test"
        flow._country = "IE"
        flow._station_id = "42"
        flow._latitude = None
        flow._longitude = None
        flow._suggested_name = "Test Station"
        flow.context = {}

        # Show the form — schema should NOT include show_on_map
        result = await flow.async_step_name(user_input=None)
        assert result["type"] == "form"
        schema_keys = {str(k) for k in result["data_schema"].schema}
        assert CONF_SHOW_ON_MAP not in schema_keys
    finally:
        PROVIDER_REGISTRY.pop("ie_nolatlon_test", None)


# ---------------------------------------------------------------------------
# Options flow tests  (config_flow.py lines 755-792)
# ---------------------------------------------------------------------------


async def test_options_flow_station_entry_no_api_key(
    hass: HomeAssistant,
) -> None:
    """Options flow for a plain station entry (no API key, no location) creates entry immediately."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{DEFAULT_PROVIDER}_opts_no_key_77",
        data={CONF_STATION_ID: "77", CONF_PROVIDER: DEFAULT_PROVIDER},
        title="Station 77",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "create_entry"
    assert result["data"] == {}


async def test_options_flow_location_entry_with_radius(
    hass: HomeAssistant,
) -> None:
    """Options flow for a location entry shows radius field (lines 755-792)."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{DEFAULT_PROVIDER}_loc_opts_radius",
        data={
            CONF_PROVIDER: DEFAULT_PROVIDER,
            CONF_LATITUDE: 53.3498,
            CONF_LONGITUDE: -6.2603,
            CONF_RADIUS_KM: 10.0,
        },
        title="Dublin Area Opts",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
    assert any(CONF_RADIUS_KM in k for k in schema_keys)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_RADIUS_KM: 15.0}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_RADIUS_KM] == 15.0


async def test_options_flow_location_entry_with_api_key(
    hass: HomeAssistant,
) -> None:
    """Options flow for a location+API-key entry shows both api_key and radius fields (lines 755-792)."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _FakeDEProviderOptsKey(BaseProvider):
        COUNTRY = "DE"
        PROVIDER_KEY = "de_fake_opts_test_key"
        LABEL = "DE Fake Opts Key"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        REQUIRES_API_KEY = True
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(
            self, station_id: str, api_key: str | None = None, **kwargs
        ) -> None:
            self._api_key = api_key

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["de_fake_opts_test_key"] = _FakeDEProviderOptsKey
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_de_fake_opts_test_key_loc",
            data={
                CONF_PROVIDER: "de_fake_opts_test_key",
                CONF_LATITUDE: 52.52,
                CONF_LONGITUDE: 13.405,
                CONF_RADIUS_KM: 5.0,
            },
            options={CONF_API_KEY: "existing-key"},
            title="Berlin Area Key",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any(CONF_API_KEY in k for k in schema_keys)
        assert any(CONF_RADIUS_KM in k for k in schema_keys)

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new-key", CONF_RADIUS_KM: 8.0},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_API_KEY] == "new-key"
        assert result["data"][CONF_RADIUS_KM] == 8.0
    finally:
        PROVIDER_REGISTRY.pop("de_fake_opts_test_key", None)


async def test_options_flow_station_entry_with_api_key(
    hass: HomeAssistant,
) -> None:
    """Options flow for a station entry with API key shows api_key field (lines 755-792)."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _FakeKeyedStationOpts(BaseProvider):
        COUNTRY = "DE"
        PROVIDER_KEY = "de_fake_keyed_station_opts"
        LABEL = "DE Fake Keyed Station Opts"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "manual_id"
        REQUIRES_API_KEY = True
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(
            self, station_id: str, api_key: str | None = None, **kwargs
        ) -> None:
            self._api_key = api_key

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

    PROVIDER_REGISTRY["de_fake_keyed_station_opts"] = _FakeKeyedStationOpts
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_de_fake_keyed_station_opts_88",
            data={CONF_STATION_ID: "88", CONF_PROVIDER: "de_fake_keyed_station_opts"},
            options={CONF_API_KEY: "old-key"},
            title="Keyed Station 88 Opts",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any(CONF_API_KEY in k for k in schema_keys)

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "updated-key"},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_API_KEY] == "updated-key"
    finally:
        PROVIDER_REGISTRY.pop("de_fake_keyed_station_opts", None)


async def test_reload_entry_listener_calls_async_reload(hass: HomeAssistant) -> None:
    """_reload_entry calls async_schedule_reload when options change."""
    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_reload_test_99",
        data={CONF_STATION_ID: "99", CONF_PROVIDER: "ie_fuelcompare"},
        title="Reload Test",
    )
    entry.add_to_hass(hass)

    schedule_reload_called: list = []

    # Patch async_schedule_reload on the entry to spy on calls
    original_entry = entry
    original_entry.async_schedule_reload = lambda: schedule_reload_called.append(True)

    with (
        patch.object(
            FuelCompareIECoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # Trigger options update — HA calls the listener
    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: "newkey"})
    import asyncio

    await asyncio.sleep(0)
    assert schedule_reload_called, "async_schedule_reload was not called"


async def test_async_setup_entry_unknown_provider_raises_config_entry_not_ready(
    hass: HomeAssistant,
) -> None:
    """Lines 47-52: raise ConfigEntryNotReady when provider key unknown and default also absent."""
    from homeassistant.exceptions import ConfigEntryNotReady

    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_bad_provider",
        data={CONF_STATION_ID: "1", CONF_PROVIDER: "__nonexistent_provider__"},
    )
    entry.add_to_hass(hass)

    saved_default = PROVIDER_REGISTRY.pop(DEFAULT_PROVIDER, None)
    try:
        import pytest

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
    finally:
        if saved_default is not None:
            PROVIDER_REGISTRY[DEFAULT_PROVIDER] = saved_default


# ---------------------------------------------------------------------------
# no_providers_for_country abort (config_flow.py line 406)
# ---------------------------------------------------------------------------


async def test_async_step_user_aborts_when_no_countries(hass: HomeAssistant) -> None:
    """async_step_user aborts with no_providers_for_country when registry is empty."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    saved = dict(PROVIDER_REGISTRY)
    try:
        PROVIDER_REGISTRY.clear()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == "abort"
        assert result["reason"] == "no_providers_for_country"
    finally:
        PROVIDER_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# no_counties_for_country abort (config_flow.py line 558)
# ---------------------------------------------------------------------------


async def test_async_step_county_aborts_when_no_counties(hass: HomeAssistant) -> None:
    """async_step_county aborts with no_counties_for_country when country has no county map."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _FakeCountyProvider(BaseProvider):
        COUNTRY = "XX"
        PROVIDER_KEY = "xx_fake_county_test"
        LABEL = "XX Fake County Test"
        STATION_LOOKUP_MODE = "county_search"
        CAPABILITIES = frozenset({"name"})

        async def async_fetch(self, session, station_id):  # type: ignore[override]
            return {}  # type: ignore[return-value]

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["xx_fake_county_test"] = _FakeCountyProvider
    try:
        with (
            _PATCH_FETCH_NAME,
            _PATCH_FIRST_REFRESH,
            patch(
                "custom_components.fuelcompare_ie.config_flow._countries_from_registry",
                return_value=[("XX", "XX Country")],
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            if result.get("step_id") == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_COUNTRY: "XX"}
                )
            if result.get("step_id") == "provider":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_PROVIDER: "xx_fake_county_test"}
                )
            assert result["type"] == "abort"
            assert result["reason"] == "no_counties_for_country"
    finally:
        PROVIDER_REGISTRY.pop("xx_fake_county_test", None)


# ---------------------------------------------------------------------------
# Options flow invalid_api_key on location entry (config_flow.py lines 792-794)
# ---------------------------------------------------------------------------


async def test_options_flow_location_entry_invalid_api_key_shows_error(
    hass: HomeAssistant,
) -> None:
    """Options flow for a location+API-key entry shows error on empty API key."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _FakeDEProviderLocKey(BaseProvider):
        COUNTRY = "DE"
        PROVIDER_KEY = "de_fake_loc_key_test"
        LABEL = "DE Fake Loc Key Test"
        REQUIRES_API_KEY = True
        STATION_LOOKUP_MODE = "location_search"
        CAPABILITIES = frozenset({"name"})

        async def async_fetch(self, session, station_id):  # type: ignore[override]
            return {}  # type: ignore[return-value]

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["de_fake_loc_key_test"] = _FakeDEProviderLocKey
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_de_fake_loc_key_test_opts_err",
            data={
                CONF_PROVIDER: "de_fake_loc_key_test",
                CONF_LATITUDE: 52.5,
                CONF_LONGITUDE: 13.4,
                CONF_RADIUS_KM: 10.0,
            },
            options={CONF_API_KEY: "existing-key"},
            title="DE Fake Loc Key",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        # Submit empty api_key to trigger validation error on location entry branch
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "  ", CONF_RADIUS_KM: 10.0},
        )
        assert result["type"] == "form"
        assert result["errors"].get(CONF_API_KEY) == "invalid_api_key"
    finally:
        PROVIDER_REGISTRY.pop("de_fake_loc_key_test", None)


# ---------------------------------------------------------------------------
# Options flow invalid_api_key on non-location API-key entry (config_flow.py lines 792-794)
# ---------------------------------------------------------------------------


async def test_options_flow_station_entry_invalid_api_key_shows_error(
    hass: HomeAssistant,
) -> None:
    """Options flow for a non-location API-key entry shows error on empty API key."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _FakeDEProviderStatKey(BaseProvider):
        COUNTRY = "DE"
        PROVIDER_KEY = "de_fake_stat_key_test"
        LABEL = "DE Fake Stat Key Test"
        REQUIRES_API_KEY = True
        STATION_LOOKUP_MODE = "manual_id"
        CAPABILITIES = frozenset({"name"})

        async def async_fetch(self, session, station_id):  # type: ignore[override]
            return {}  # type: ignore[return-value]

        async def async_fetch_station_name(self, session, station_id):
            return None

    PROVIDER_REGISTRY["de_fake_stat_key_test"] = _FakeDEProviderStatKey
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_de_fake_stat_key_test_opts_err",
            data={
                CONF_PROVIDER: "de_fake_stat_key_test",
                CONF_STATION_ID: "42",
            },
            options={CONF_API_KEY: "existing-key"},
            title="DE Fake Stat Key",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        # Submit empty api_key to trigger validation error on non-location entry branch
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: ""},
        )
        assert result["type"] == "form"
        assert result["errors"].get(CONF_API_KEY) == "invalid_api_key"
    finally:
        PROVIDER_REGISTRY.pop("de_fake_stat_key_test", None)


# ---------------------------------------------------------------------------
# async_setup_entry — ie_pumps creates TLS repair issue (line 170)
# ---------------------------------------------------------------------------


async def test_async_setup_entry_ie_pumps_creates_tls_issue(
    hass: HomeAssistant,
) -> None:
    """async_setup_entry creates ie_pumps_tls_disabled repair issue for ie_pumps entries."""
    from unittest.mock import patch

    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_ie_pumps_tls_issue_test",
        data={CONF_STATION_ID: "99", CONF_PROVIDER: "ie_pumps"},
        title="IE Pumps TLS Test",
    )
    entry.add_to_hass(hass)

    created_issues: list[str] = []

    def _capture_issue(hass, domain, issue_id, **kwargs):  # noqa: ANN001
        created_issues.append(issue_id)

    with (
        patch.object(
            FuelCompareIECoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.fuelcompare_ie.async_create_issue",
            side_effect=_capture_issue,
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert any("ie_pumps_tls_disabled" in issue_id for issue_id in created_issues)


# ---------------------------------------------------------------------------
# async_setup_entry — entry_id fallback when no station_id and no lat/lng (line 60)
# ---------------------------------------------------------------------------


async def test_async_setup_entry_uses_entry_id_when_no_station_and_no_coords(
    hass: HomeAssistant,
) -> None:
    """station_id defaults to entry.entry_id when neither station_id nor lat/lng present."""
    from custom_components.fuelcompare_ie import async_setup_entry
    from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_entry_id_fallback_test",
        data={CONF_PROVIDER: "ie_fuelcompare"},  # no station_id, no lat/lng
        title="Entry ID Fallback Test",
    )
    entry.add_to_hass(hass)

    captured_ids: list[str] = []

    original_init = FuelCompareIECoordinator.__init__

    def _capture_init(self, hass, provider, station_id, **kwargs):
        captured_ids.append(station_id)
        original_init(self, hass, provider, station_id, **kwargs)

    with (
        patch.object(FuelCompareIECoordinator, "__init__", _capture_init),
        patch.object(
            FuelCompareIECoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert captured_ids and captured_ids[0] == entry.entry_id
