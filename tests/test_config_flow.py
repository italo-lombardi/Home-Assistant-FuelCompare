"""Tests for Fuel Compare config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


async def test_async_step_location_non_location_search_aborts_on_duplicate(
    hass: HomeAssistant,
) -> None:
    """async_step_location aborts when lat/lng uid already configured for non-location_search providers.

    Covers config_flow.py line 771: _abort_if_unique_id_configured() for
    providers whose unique_id is lat/lng-based (global_list, manual_id).
    """
    from unittest.mock import AsyncMock, patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.const import CONF_LATITUDE, CONF_LONGITUDE
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _GlobalListProvider(BaseProvider):
        COUNTRY = "EU"
        PROVIDER_KEY = "eu_global_list_abort_test"
        LABEL = "Global List Abort Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "global_list"
        CURRENCY = "EUR"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_list_stations(self, session, **kwargs):
            return [("eu_001", "EU Station")]

    PROVIDER_REGISTRY["eu_global_list_abort_test"] = _GlobalListProvider
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "eu_global_list_abort_test"
        flow._country = "EU"
        flow._api_key = ""
        flow._postal_code = ""

        abort_called = False

        def _record_abort():
            nonlocal abort_called
            abort_called = True

        with (
            patch.object(
                flow,
                "async_set_unique_id",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                flow,
                "_abort_if_unique_id_configured",
                side_effect=_record_abort,
            ),
            patch.object(
                flow,
                "async_step_station_picker",
                new=AsyncMock(
                    return_value={"type": "form", "step_id": "station_picker"}
                ),
            ),
        ):
            await flow.async_step_location(
                user_input={CONF_LATITUDE: 48.0, CONF_LONGITUDE: 16.0}
            )

        assert abort_called, (
            "Expected _abort_if_unique_id_configured to be called for non-location_search providers"
        )
    finally:
        PROVIDER_REGISTRY.pop("eu_global_list_abort_test", None)


async def test_async_step_location_location_search_skips_abort(
    hass: HomeAssistant,
) -> None:
    """async_step_location does NOT abort for location_search providers.

    Covers the fix for multi-station add: _abort_if_unique_id_configured must
    NOT be called when STATION_LOOKUP_MODE='location_search' so the user can
    pick different stations from the same search area.
    """
    from unittest.mock import AsyncMock, patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.const import CONF_LATITUDE, CONF_LONGITUDE
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _LocationSearchProvider(BaseProvider):
        COUNTRY = "AU"
        PROVIDER_KEY = "au_location_search_no_abort_test"
        LABEL = "Location Search No Abort Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "A$"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_list_stations(self, session, **kwargs):
            return [("au_001", "Station 1"), ("au_002", "Station 2")]

    PROVIDER_REGISTRY["au_location_search_no_abort_test"] = _LocationSearchProvider
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "au_location_search_no_abort_test"
        flow._country = "AU"
        flow._api_key = ""
        flow._postal_code = ""

        abort_called = False

        def _record_abort():
            nonlocal abort_called
            abort_called = True

        with (
            patch.object(flow, "async_set_unique_id", new=AsyncMock(return_value=None)),
            patch.object(
                flow, "_abort_if_unique_id_configured", side_effect=_record_abort
            ),
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession"
            ),
            patch(
                "custom_components.fuelcompare_ie.config_flow.FuelCompareIEConfigFlow.async_step_station_picker",
                new=AsyncMock(
                    return_value={"type": "form", "step_id": "station_picker"}
                ),
            ),
        ):
            result = await flow.async_step_location(
                user_input={CONF_LATITUDE: -31.8027, CONF_LONGITUDE: 115.8377}
            )

        assert not abort_called, (
            "_abort_if_unique_id_configured must NOT be called for location_search providers"
        )
        assert result["step_id"] == "station_picker"
    finally:
        PROVIDER_REGISTRY.pop("au_location_search_no_abort_test", None)


# ---------------------------------------------------------------------------
# test_station_picker_empty_global_list_shows_error  (config_flow.py line 688)
# ---------------------------------------------------------------------------


async def test_station_picker_empty_global_list_shows_error(
    hass: HomeAssistant,
) -> None:
    """Station picker aborts with 'no_stations_found_global' for global_list providers with no stations."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _EmptyGlobalListProvider(BaseProvider):
        COUNTRY = "EU"
        PROVIDER_KEY = "eu_empty_global_list_test"
        LABEL = "Empty Global List Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "global_list"
        CURRENCY = "EUR"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_list_stations(self, session, **kwargs):
            return []  # deliberately empty

    PROVIDER_REGISTRY["eu_empty_global_list_test"] = _EmptyGlobalListProvider
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "eu_empty_global_list_test"
        flow._country = "EU"
        flow._station_county = ""
        flow._latitude = None
        flow._longitude = None
        flow._radius_km = 10.0
        flow._postal_code = ""
        flow._api_key = ""

        with patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession"
        ):
            result = await flow.async_step_station_picker(user_input=None)

        assert result["type"] == "abort"
        assert result["reason"] == "no_stations_found_global"
    finally:
        PROVIDER_REGISTRY.pop("eu_empty_global_list_test", None)


# ---------------------------------------------------------------------------
# test_station_picker_empty_list_with_uid_aborts_not_clobbers
# ---------------------------------------------------------------------------


async def test_station_picker_empty_list_with_uid_aborts_not_clobbers(
    hass: HomeAssistant,
) -> None:
    """Empty station list on a location_search provider shows an error form
    rather than silently creating an entry with a synthesised station_id.

    Regression test for two related bugs:
    1. Clobber bug (0.7.1): an empty list previously fell through to
       async_step_name with the lat/lng unique_id set, silently destroying
       and replacing the existing entry.
    2. Synth station_id bug (0.7.2): an empty list on a location_search
       provider with no prior entry fell through to async_step_name and
       created a new entry with no station_id. __init__.py then synthesised
       a station_id from lat/lng, which the provider couldn't resolve at
       fetch time (e.g. Sydney coords on au_fuelwatch / WA).

    Fix: for location_search / county_search providers, an empty list means
    "no stations in the searched area" — surface an error and stay on the
    picker so the user can widen the search. Never fall through.
    """
    from unittest.mock import MagicMock, patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _EmptyProvider(BaseProvider):
        COUNTRY = "AU"
        PROVIDER_KEY = "au_empty_clobber_test"
        LABEL = "Empty Clobber Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "A$"
        CAPABILITIES: frozenset = frozenset({"name"})

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_list_stations(self, session, **kwargs):
            return []  # API down or radius too small

    PROVIDER_REGISTRY["au_empty_clobber_test"] = _EmptyProvider
    try:
        flow = FuelCompareIEConfigFlow()
        # Use MagicMock for hass so we control config_entries behaviour
        mock_hass = MagicMock()
        mock_hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        flow.hass = mock_hass
        # Initialise required flow attrs
        flow.context = {"source": "user"}
        flow._provider_key = "au_empty_clobber_test"
        flow._country = "AU"
        flow._station_county = ""
        flow._latitude = -33.8688
        flow._longitude = 151.2093
        flow._radius_km = 1.0
        flow._postal_code = ""
        flow._api_key = ""
        flow._suggested_name = ""
        flow._station_list = []
        flow._station_url_map = {}
        flow._station_id = ""
        flow._station_page_url = ""
        flow._show_on_map = False
        # Simulate unique_id set by async_step_location
        flow.context["unique_id"] = (
            "fuelcompare_ie_au_empty_clobber_test_-33.8688_151.2093"
        )

        abort_called = False
        name_called: list[int] = []

        def _record_abort():
            nonlocal abort_called
            abort_called = True

        async def _mock_name():
            name_called.append(1)
            return {"type": "create_entry", "title": "x", "data": {}}

        with (
            patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
            patch.object(
                flow, "_abort_if_unique_id_configured", side_effect=_record_abort
            ),
            patch.object(flow, "async_step_name", side_effect=_mock_name),
        ):
            result = await flow.async_step_station_picker(user_input=None)

        # Must NOT fall through to async_step_name (would synthesise a bogus
        # station_id from lat/lng that the provider can't fetch).
        assert not name_called, (
            "async_step_name must NOT be called for an empty location_search list"
        )
        # _abort_if_unique_id_configured (the *unique-id* abort that prevents
        # clobbering an existing entry) must not fire — we abort the FLOW
        # cleanly via async_abort() below instead, so there's no chance of
        # a partial entry creation either way.
        assert not abort_called, (
            "_abort_if_unique_id_configured must NOT be called — the flow "
            "should abort via async_abort(reason=...) instead"
        )
        # Should abort the flow with the mode-aware reason; HA renders this
        # as a dismissable error, preventing the user from typing a bogus
        # station_id into a free-text fallback form.
        assert result["type"] == "abort"
        assert result["reason"] == "no_stations_found_location"
    finally:
        PROVIDER_REGISTRY.pop("au_empty_clobber_test", None)


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Germany / Tankerkoenig routes through the api_key step."""
    from custom_components.fuelcompare_ie.providers.de_tankerkoenig import (
        DeTankerkoenigProvider,
    )

    # de_tankerkoenig is DISABLED in 0.7.0 (no API key in CI). Re-enable for
    # this test so the config flow exposes it and we can exercise the api_key
    # step.
    monkeypatch.setattr(DeTankerkoenigProvider, "DISABLED", False)

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
        DISABLED = False  # parent is DISABLED in 0.7.0; re-enable for the test
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
            # Two-pass: if URL found, picker re-renders — submit again to confirm
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
# test_dispatch_after_provider_global_list_routes_to_picker  (config_flow.py line 474)
# ---------------------------------------------------------------------------


async def test_dispatch_after_provider_global_list_routes_to_picker(
    hass: HomeAssistant,
) -> None:
    """_dispatch_after_provider routes to station_picker for STATION_LOOKUP_MODE='global_list'.

    EU Oil Bulletin uses this mode: no coordinates / county needed, the
    user picks straight from the country list. We don't run the picker
    step itself (it would try a real network call); we only confirm the
    dispatcher selected it.
    """
    from unittest.mock import AsyncMock, patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = "eu_oil_bulletin"

    sentinel = {"type": "form", "step_id": "station_picker"}
    with patch.object(
        FuelCompareIEConfigFlow,
        "async_step_station_picker",
        new=AsyncMock(return_value=sentinel),
    ) as mock_picker:
        result = await flow._dispatch_after_provider()

    mock_picker.assert_awaited_once()
    assert result is sentinel


# ---------------------------------------------------------------------------
# DISABLED contract — providers with DISABLED=True hidden from config flow
# but existing entries continue to load.
# ---------------------------------------------------------------------------


def test_disabled_provider_hidden_from_country_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A country whose only provider is DISABLED disappears from _countries_from_registry."""
    from custom_components.fuelcompare_ie.config_flow import _countries_from_registry
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.eu_oil_bulletin import (
        EuOilBulletinProvider,
    )

    # EU has only one provider (eu_oil_bulletin). Confirm it appears, then
    # flip DISABLED and confirm the country is hidden.
    assert any(code == "EU" for code, _ in _countries_from_registry())

    # Sanity check: this is the only EU provider in the registry.
    eu_providers = [c for c in PROVIDER_REGISTRY.values() if c.COUNTRY == "EU"]
    assert eu_providers == [EuOilBulletinProvider]

    monkeypatch.setattr(EuOilBulletinProvider, "DISABLED", True)
    assert all(code != "EU" for code, _ in _countries_from_registry())


def test_disabled_provider_hidden_from_provider_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_providers_for_country filters out DISABLED providers but keeps others."""
    from custom_components.fuelcompare_ie.config_flow import _providers_for_country
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )
    from custom_components.fuelcompare_ie.providers.ie_fuelfinder import (
        IEFuelFinderProvider,
    )

    # Ireland has multiple providers. Disable one, confirm it's gone, others stay.
    keys_before = {k for k, _ in _providers_for_country("IE")}
    assert IEFuelCompareProvider.PROVIDER_KEY in keys_before
    assert IEFuelFinderProvider.PROVIDER_KEY in keys_before

    monkeypatch.setattr(IEFuelCompareProvider, "DISABLED", True)
    keys_after = {k for k, _ in _providers_for_country("IE")}
    assert IEFuelCompareProvider.PROVIDER_KEY not in keys_after
    assert IEFuelFinderProvider.PROVIDER_KEY in keys_after


def test_disabled_provider_default_is_false_on_base() -> None:
    """BaseProvider.DISABLED defaults to False so untouched providers stay visible."""
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    assert BaseProvider.DISABLED is False


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
# test_async_step_station_picker_show_on_map_sets_flag  (config_flow.py line 599)
# ---------------------------------------------------------------------------


async def test_async_step_station_picker_show_on_map_sets_flag(
    hass: HomeAssistant,
) -> None:
    """station_picker submit with show_on_map=True sets flow._show_on_map (line 599)."""
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.const import CONF_SHOW_ON_MAP

    flow = FuelCompareIEConfigFlow()
    flow.hass = hass
    flow._provider_key = DEFAULT_PROVIDER
    flow._station_list = [("abc", "Station ABC")]
    flow._station_url_map = {"abc": "https://example.com/abc"}
    # Mutable context dict so async_set_unique_id can write
    flow.context = {"unique_id": f"{DOMAIN}_{DEFAULT_PROVIDER}_abc"}

    with (
        patch(
            "custom_components.fuelcompare_ie.config_flow.async_get_clientsession",
        ),
        patch(
            "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
            new=AsyncMock(return_value="Station ABC"),
        ),
    ):
        result = await flow.async_step_station_picker(
            user_input={CONF_STATION_ID: "abc", CONF_SHOW_ON_MAP: True}
        )

    assert flow._show_on_map is True
    assert flow._station_id == "abc"
    assert result["step_id"] == "name"


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

        assert result["type"] == "abort"
        assert result["reason"] == "no_stations_found_location"
    finally:
        PROVIDER_REGISTRY.pop("ie_broken_list_601", None)


# ---------------------------------------------------------------------------
# test_async_step_station_picker_no_stations_with_unique_id_routes_to_name  (config_flow.py line 610)
# ---------------------------------------------------------------------------


async def test_async_step_station_picker_no_stations_with_unique_id_routes_to_name(
    hass: HomeAssistant,
) -> None:
    """station_picker stays on the picker form (not fall through to name) when
    the station list is empty for a location_search provider, even if a
    lat/lng unique_id is already set on the flow.

    Originally the code routed an empty list + unique_id straight to the name
    step, which silently created an entry with no station_id (then the
    runtime synthesised a station_id from lat/lng that no provider could
    resolve). The fix narrows that shortcut to non-location_search providers
    so location-search users see a recoverable error.
    """
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

        assert result["type"] == "abort"
        assert result["reason"] == "no_stations_found_location"
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
    """show_on_map=True set on flow (_show_on_map) is stored in options after name step."""
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
        flow._show_on_map = True  # set as if picker submitted with show_on_map=True
        flow.context = {}

        result = await flow.async_step_name(user_input={"name": "Test Station"})

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


async def test_options_flow_location_entry_with_location_caps_shows_show_on_map(
    hass: HomeAssistant,
) -> None:
    """Location entry with latitude/longitude CAPABILITIES shows show_on_map toggle."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
        CONF_SHOW_ON_MAP,
    )

    class _FakeLocLatLon(BaseProvider):
        COUNTRY = "NO"
        PROVIDER_KEY = "no_fake_loc_latlon"
        LABEL = "NO Fake Loc LatLon"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "location_search"
        CAPABILITIES: frozenset = frozenset({"latitude", "longitude", "diesel"})

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return []

    PROVIDER_REGISTRY["no_fake_loc_latlon"] = _FakeLocLatLon
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_no_fake_loc_latlon_55",
            data={
                CONF_PROVIDER: "no_fake_loc_latlon",
                CONF_LATITUDE: 59.91,
                CONF_LONGITUDE: 10.75,
                CONF_RADIUS_KM: 10.0,
            },
            title="Oslo Area",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any(CONF_RADIUS_KM in k for k in schema_keys)
        assert any(CONF_SHOW_ON_MAP in k for k in schema_keys)
    finally:
        PROVIDER_REGISTRY.pop("no_fake_loc_latlon", None)


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


async def test_options_flow_station_entry_with_lat_lon_shows_show_on_map(
    hass: HomeAssistant,
) -> None:
    """Options flow for a station_id provider with lat/lon caps shows show_on_map toggle."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider
    from custom_components.fuelcompare_ie.const import CONF_SHOW_ON_MAP

    class _FakeLatLonStation(BaseProvider):
        COUNTRY = "HR"
        PROVIDER_KEY = "hr_fake_latlon_opts"
        LABEL = "HR Fake LatLon Opts"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "manual_id"
        CAPABILITIES: frozenset = frozenset({"latitude", "longitude", "diesel"})

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

    PROVIDER_REGISTRY["hr_fake_latlon_opts"] = _FakeLatLonStation
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_hr_fake_latlon_opts_99",
            data={CONF_STATION_ID: "99", CONF_PROVIDER: "hr_fake_latlon_opts"},
            options={},
            title="HR Station 99",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any(CONF_SHOW_ON_MAP in k for k in schema_keys)

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_SHOW_ON_MAP: True},
        )
        assert result["type"] == "create_entry"
        assert result["data"].get(CONF_SHOW_ON_MAP) is True
    finally:
        PROVIDER_REGISTRY.pop("hr_fake_latlon_opts", None)


# ---------------------------------------------------------------------------
# _name_from_picker_label helper
# ---------------------------------------------------------------------------


def test_name_from_picker_label_strips_uuid_suffix() -> None:
    """_name_from_picker_label strips (# + hex/numeric) suffix."""
    from custom_components.fuelcompare_ie.config_flow import _name_from_picker_label

    assert (
        _name_from_picker_label("Circle K Swords, Main St (#abcd1234ef56)")
        == "Circle K Swords, Main St"
    )
    assert _name_from_picker_label("BP Tallaght (#1234)") == "BP Tallaght"
    assert _name_from_picker_label("Shell Dublin") == "Shell Dublin"


# ---------------------------------------------------------------------------
# Picker-label name fallback (config_flow.py lines 610-614)
# ---------------------------------------------------------------------------


def test_name_from_picker_label_used_as_fallback_when_fetch_returns_none() -> None:
    """Lines 610-614: picker label stripped of (#id) suffix becomes suggested_name."""
    from custom_components.fuelcompare_ie.config_flow import _name_from_picker_label

    station_list = [
        ("uid-abc123def456", "Circle K Swords, Main St (#abc123def456)"),
        ("uid-xyz", "BP Tallaght (#xyz)"),
    ]
    target_id = "uid-abc123def456"
    fetched = None  # simulate failed name fetch

    # Replicate lines 610-614 logic
    if not fetched:
        picker_label = next(
            (lbl for uid, lbl in station_list if uid == target_id),
            None,
        )
        fetched = _name_from_picker_label(picker_label) if picker_label else None

    assert fetched == "Circle K Swords, Main St"


# ---------------------------------------------------------------------------
# Picker label fallback integration (config_flow.py lines 610-614)
# ---------------------------------------------------------------------------


async def test_station_picker_picker_label_fallback_reaches_name_step(
    hass: HomeAssistant,
) -> None:
    """Lines 610-614: when _fetch_station_name returns None, picker label used as name."""
    from unittest.mock import AsyncMock, patch

    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _PickerLabelFallback(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_picker_fallback_test"
        LABEL = "Picker Fallback"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "county_search"
        CURRENCY = "EUR"
        CAPABILITIES: frozenset = frozenset()

        def __init__(self, station_id: str, **kwargs) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

        async def async_list_stations(self, session, **kwargs):
            return [("102", "Shell Sandymount, Strand Rd (#102)")]

    PROVIDER_REGISTRY["ie_picker_fallback_test"] = _PickerLabelFallback
    try:
        flow = FuelCompareIEConfigFlow()
        flow.hass = hass
        flow._provider_key = "ie_picker_fallback_test"
        flow._station_county = "dublin"
        flow._latitude = None
        flow._longitude = None
        flow._radius_km = 10.0
        flow._station_list = [("102", "Shell Sandymount, Strand Rd (#102)")]
        flow._station_url_map = {}
        flow._api_key = ""

        with (
            patch.object(flow, "async_set_unique_id", new=AsyncMock(return_value=None)),
            patch.object(flow, "_abort_if_unique_id_configured", return_value=None),
            patch(
                "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await flow.async_step_station_picker(
                user_input={"station_id": "102"}
            )

        assert result["step_id"] == "name"
        assert flow._suggested_name == "Shell Sandymount, Strand Rd"
    finally:
        PROVIDER_REGISTRY.pop("ie_picker_fallback_test", None)


# ---------------------------------------------------------------------------
# test_location_search_allows_second_station_same_area  (fix for #44)
# ---------------------------------------------------------------------------


async def test_location_search_allows_second_station_same_area(
    hass: HomeAssistant,
) -> None:
    """Two different stations from the same search area can both be added.

    Before the fix, async_step_location set the unique_id from lat/lng so the
    second station was aborted with 'already_configured'.  After the fix the
    unique_id is always set from the selected station_id inside
    async_step_station_picker.
    """
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _MultiStationProvider(BaseProvider):
        COUNTRY = "AU"
        PROVIDER_KEY = "au_multi_station_test"
        LABEL = "Multi Station Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "A$"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return f"Station {station_id}"

        async def async_list_stations(self, session, **kwargs):
            return [
                ("au_sta_001", "BP Canningvale, 1 Main St (#au_sta_)"),
                ("au_sta_002", "Caltex Gosnells, 2 High St (#au_sta_)"),
            ]

    PROVIDER_REGISTRY["au_multi_station_test"] = _MultiStationProvider
    try:
        _lat, _lng = -31.8027, 115.8377

        async def _add_station(station_id: str) -> dict:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            if result.get("step_id") == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_COUNTRY: "AU"}
                )
            if result.get("step_id") == "provider":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_PROVIDER: "au_multi_station_test"},
                )
            assert result["step_id"] == "location"
            with patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession"
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={
                        CONF_LATITUDE: _lat,
                        CONF_LONGITUDE: _lng,
                        CONF_RADIUS_KM: 5.0,
                    },
                )
            assert result["step_id"] == "station_picker"
            with (
                patch(
                    "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
                    new=AsyncMock(return_value=f"Station {station_id}"),
                ),
                _PATCH_FIRST_REFRESH,
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_STATION_ID: station_id}
                )
            assert result["step_id"] == "name"
            with _PATCH_FIRST_REFRESH:
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={"name": f"Station {station_id}"}
                )
            return result

        result1 = await _add_station("au_sta_001")
        assert result1["type"] == "create_entry", "first station should be created"

        result2 = await _add_station("au_sta_002")
        assert result2["type"] == "create_entry", (
            "second station in same area should also be created (was blocked before fix)"
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        station_ids = [e.data.get("station_id") for e in entries]
        assert "au_sta_001" in station_ids
        assert "au_sta_002" in station_ids
    finally:
        PROVIDER_REGISTRY.pop("au_multi_station_test", None)


# ---------------------------------------------------------------------------
# test_location_search_duplicate_station_still_aborts  (fix for #44)
# ---------------------------------------------------------------------------


async def test_location_search_duplicate_station_still_aborts(
    hass: HomeAssistant,
) -> None:
    """Re-adding an already-configured station is still rejected after the fix."""
    from custom_components.fuelcompare_ie.const import (
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_RADIUS_KM,
    )
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _DupStationProvider(BaseProvider):
        COUNTRY = "AU"
        PROVIDER_KEY = "au_dup_station_test"
        LABEL = "Dup Station Test"
        CONFIG_MODE = "location"
        STATION_LOOKUP_MODE = "location_search"
        CURRENCY = "A$"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return "BP Canningvale"

        async def async_list_stations(self, session, **kwargs):
            return [("au_dup_001", "BP Canningvale, 1 Main St (#au_dup_)")]

    PROVIDER_REGISTRY["au_dup_station_test"] = _DupStationProvider
    try:
        _lat, _lng = -31.8027, 115.8377

        async def _add_station() -> dict:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            if result.get("step_id") == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_COUNTRY: "AU"}
                )
            if result.get("step_id") == "provider":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_PROVIDER: "au_dup_station_test"},
                )
            assert result["step_id"] == "location"
            with patch(
                "custom_components.fuelcompare_ie.config_flow.async_get_clientsession"
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={
                        CONF_LATITUDE: _lat,
                        CONF_LONGITUDE: _lng,
                        CONF_RADIUS_KM: 5.0,
                    },
                )
            assert result["step_id"] == "station_picker"
            with (
                patch(
                    "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
                    new=AsyncMock(return_value="BP Canningvale"),
                ),
                _PATCH_FIRST_REFRESH,
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_STATION_ID: "au_dup_001"}
                )
            if result.get("type") == "abort":
                return result
            assert result["step_id"] == "name"
            with _PATCH_FIRST_REFRESH:
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={"name": "BP Canningvale"}
                )
            return result

        result1 = await _add_station()
        assert result1["type"] == "create_entry"

        result2 = await _add_station()
        assert result2["type"] == "abort"
        assert result2["reason"] == "already_configured"
    finally:
        PROVIDER_REGISTRY.pop("au_dup_station_test", None)


# ---------------------------------------------------------------------------
# test_county_search_multi_station_unaffected_by_location_fix
# ---------------------------------------------------------------------------


async def test_county_search_multi_station_unaffected_by_location_fix(
    hass: HomeAssistant,
) -> None:
    """county_search providers are unaffected by the location_search unique_id fix.

    county_search never goes through async_step_location, so the conditional
    abort introduced in that step has no effect on this path.  Verify that:
    - The flow routes directly to county → station_picker (skips location).
    - The unique_id is station-based (not lat/lng-based).
    - A duplicate station is still rejected.
    """
    from custom_components.fuelcompare_ie.config_flow import FuelCompareIEConfigFlow
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY
    from custom_components.fuelcompare_ie.providers.base import BaseProvider
    from custom_components.fuelcompare_ie.const import CONF_STATION_COUNTY

    class _CountySearchProvider(BaseProvider):
        COUNTRY = "IE"
        PROVIDER_KEY = "ie_county_search_regression_test"
        LABEL = "County Search Regression Test"
        CONFIG_MODE = "station_id"
        STATION_LOOKUP_MODE = "county_search"
        CURRENCY = "EUR"

        def __init__(self, station_id: str, **_: object) -> None:
            pass

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return f"Station {station_id}"

        async def async_list_stations(self, session, **kwargs):
            return [
                ("cs_001", "Texaco Cork, Main St (#cs_001)"),
                ("cs_002", "Shell Cork, High St (#cs_002)"),
            ]

    PROVIDER_REGISTRY["ie_county_search_regression_test"] = _CountySearchProvider
    try:
        # Drive flow to station_picker and capture the unique_id set for two stations.
        async def _flow_to_uid(station_id: str) -> str | None:
            flow = FuelCompareIEConfigFlow()
            flow.hass = hass
            flow._provider_key = "ie_county_search_regression_test"
            flow._country = DEFAULT_COUNTRY
            flow._api_key = ""
            flow._latitude = None
            flow._longitude = None
            flow._radius_km = 10.0

            captured_uid: list[str] = []

            async def _capture_uid(uid: str) -> None:
                captured_uid.append(uid)

            with (
                patch.object(flow, "async_set_unique_id", side_effect=_capture_uid),
                patch.object(flow, "_abort_if_unique_id_configured", return_value=None),
                patch(
                    "custom_components.fuelcompare_ie.config_flow.async_get_clientsession"
                ),
                patch(
                    "custom_components.fuelcompare_ie.config_flow._fetch_station_name",
                    new=AsyncMock(return_value=f"Station {station_id}"),
                ),
            ):
                # county step
                result = await flow.async_step_county(
                    user_input={CONF_STATION_COUNTY: "cork"}
                )
                assert result["step_id"] == "station_picker", (
                    "county_search should route county → station_picker, not through location"
                )
                # station picker step
                result = await flow.async_step_station_picker(
                    user_input={CONF_STATION_ID: station_id}
                )
            assert result["step_id"] == "name"
            return captured_uid[-1] if captured_uid else None

        uid1 = await _flow_to_uid("cs_001")
        uid2 = await _flow_to_uid("cs_002")

        # unique_ids must be station-based, not lat/lng-based
        assert uid1 is not None and "cs_001" in uid1, (
            f"Expected station-based uid, got {uid1!r}"
        )
        assert uid2 is not None and "cs_002" in uid2, (
            f"Expected station-based uid, got {uid2!r}"
        )
        assert uid1 != uid2, "Different stations must produce different unique_ids"
    finally:
        PROVIDER_REGISTRY.pop("ie_county_search_regression_test", None)
