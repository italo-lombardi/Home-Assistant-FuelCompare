"""Config flow for Fuel Compare integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_COUNTRY,
    CONF_PROVIDER,
    CONF_STATION_COUNTY,
    CONF_STATION_ID,
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DEFAULT_RADIUS_KM,
    DOMAIN,
)
from .coordinator import FuelCompareIECoordinator
from .providers import PROVIDER_REGISTRY

_LOGGER = logging.getLogger(__name__)

_COUNTRY_NAMES: dict[str, str] = {"IE": "Ireland", "HR": "Croatia"}


def _countries_from_registry() -> list[tuple[str, str]]:
    """Derive (ISO code, display label) list from PROVIDER_REGISTRY."""
    seen: dict[str, str] = {}
    for cls in PROVIDER_REGISTRY.values():
        if cls.COUNTRY not in seen:
            seen[cls.COUNTRY] = cls.COUNTRY
    return [(code, _COUNTRY_NAMES.get(code, code)) for code in seen]


def _providers_for_country(country: str) -> list[tuple[str, str]]:
    """Return (provider_key, label) pairs for a given country code."""
    return [
        (cls.PROVIDER_KEY, cls.LABEL)
        for cls in PROVIDER_REGISTRY.values()
        if cls.COUNTRY == country
    ]


# County lists per country — lowercase value stored in entry data, display label shown in UI
_IE_COUNTIES: dict[str, str] = {
    "carlow": "Carlow",
    "cavan": "Cavan",
    "clare": "Clare",
    "cork": "Cork",
    "donegal": "Donegal",
    "dublin": "Dublin",
    "galway": "Galway",
    "kerry": "Kerry",
    "kildare": "Kildare",
    "kilkenny": "Kilkenny",
    "laois": "Laois",
    "leitrim": "Leitrim",
    "limerick": "Limerick",
    "longford": "Longford",
    "louth": "Louth",
    "mayo": "Mayo",
    "meath": "Meath",
    "monaghan": "Monaghan",
    "offaly": "Offaly",
    "roscommon": "Roscommon",
    "sligo": "Sligo",
    "tipperary": "Tipperary",
    "waterford": "Waterford",
    "westmeath": "Westmeath",
    "wexford": "Wexford",
    "wicklow": "Wicklow",
}

_COUNTY_OPTIONS_BY_COUNTRY: dict[str, dict[str, str]] = {
    "IE": _IE_COUNTIES,
    "HR": {
        "bjelovarsko-bilogorska": "Bjelovarsko-bilogorska",
        "brodsko-posavska": "Brodsko-posavska",
        "dubrovačko-neretvanska": "Dubrovačko-neretvanska",
        "grad_zagreb": "Grad Zagreb",
        "istarska": "Istarska",
        "karlovačka": "Karlovačka",
        "koprivničko-križevačka": "Koprivničko-križevačka",
        "krapinsko-zagorska": "Krapinsko-zagorska",
        "ličko-senjska": "Ličko-senjska",
        "međimurska": "Međimurska",
        "osječko-baranjska": "Osječko-baranjska",
        "požeško-slavonska": "Požeško-slavonska",
        "primorsko-goranska": "Primorsko-goranska",
        "sisačko-moslavačka": "Sisačko-moslavačka",
        "splitsko-dalmatinska": "Splitsko-dalmatinska",
        "varaždinska": "Varaždinska",
        "virovitičko-podravska": "Virovitičko-podravska",
        "vukovarsko-srijemska": "Vukovarsko-srijemska",
        "zadarska": "Zadarska",
        "zagrebačka": "Zagrebačka",
        "šibensko-kninska": "Šibensko-kninska",
    },
}


def _counties_for_country(country: str) -> dict[str, str]:
    """Return {lowercase_key: display_label} county map for a country."""
    return _COUNTY_OPTIONS_BY_COUNTRY.get(country, {})


async def _fetch_station_name(
    hass, station_id: str, provider_key: str = DEFAULT_PROVIDER
) -> str | None:
    """Resolve station display name via the selected provider.

    Falls back to DEFAULT_PROVIDER if provider_key is not in the registry.
    Module-level so tests can patch coordinator._fetch_* methods.
    """
    provider_cls = PROVIDER_REGISTRY.get(provider_key) or PROVIDER_REGISTRY.get(
        DEFAULT_PROVIDER
    )
    if provider_cls is None:
        return None
    try:
        session = async_get_clientsession(hass)
        provider = provider_cls(station_id)
        coordinator = FuelCompareIECoordinator(hass, provider, station_id)
        await coordinator._fetch_page_assets(session)
        data = await coordinator._fetch_nextjs(session)
        if data is None:
            data = await coordinator._fetch_encrypted_api(session)
        if data:
            if data.get("name"):
                return data["name"]
            if data.get("tablename"):
                return data["tablename"].replace("_", " ").title()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
    return None


class FuelCompareIEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fuel Compare."""

    VERSION = 1

    def __init__(self) -> None:
        self._country: str = DEFAULT_COUNTRY
        self._provider_key: str = DEFAULT_PROVIDER
        self._station_id: str = ""
        self._station_county: str = ""  # stored for county_search providers
        self._station_list: list[tuple[str, str]] = []  # for station picker
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._radius_km: float = DEFAULT_RADIUS_KM
        self._suggested_name: str = ""

    # ---- Step 1: country --------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select country. Auto-advance when only one country is available."""
        countries = _countries_from_registry()

        if len(countries) == 1:
            self._country = countries[0][0]
            return await self._async_step_provider()

        if user_input is not None:
            self._country = user_input[CONF_COUNTRY]
            return await self._async_step_provider()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COUNTRY, default=DEFAULT_COUNTRY): vol.In(
                        {code: label for code, label in countries}
                    ),
                }
            ),
        )

    # ---- Step 2: provider (auto-skipped when single provider) -------------------

    async def _async_step_provider(self) -> ConfigFlowResult:
        """Resolve provider, then dispatch to the correct config step for its mode."""
        providers = _providers_for_country(self._country)
        if not providers:
            self._provider_key = DEFAULT_PROVIDER
        elif len(providers) == 1:
            self._provider_key = providers[0][0]
        else:
            return await self.async_step_provider()

        return await self._dispatch_after_provider()

    async def _dispatch_after_provider(self) -> ConfigFlowResult:
        """Route to the appropriate step based on provider's STATION_LOOKUP_MODE."""
        provider_cls = PROVIDER_REGISTRY.get(self._provider_key)
        if not provider_cls:
            return await self.async_step_station()
        mode = getattr(provider_cls, "STATION_LOOKUP_MODE", "manual_id")
        if mode == "county_search":
            return await self.async_step_county()
        if provider_cls.CONFIG_MODE == "location":
            return await self.async_step_location()
        return await self.async_step_station()

    async def async_step_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select data provider for the chosen country."""
        providers = _providers_for_country(self._country)

        if user_input is not None:
            self._provider_key = user_input[CONF_PROVIDER]
            return await self._dispatch_after_provider()

        return self.async_show_form(
            step_id="provider",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER, default=providers[0][0]): vol.In(
                        {key: label for key, label in providers}
                    ),
                }
            ),
        )

    # ---- Step 3a: station ID (manual_id mode) -----------------------------------

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate the station ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]

            try:
                station_id_int = int(station_id)
                if station_id_int <= 0:
                    errors[CONF_STATION_ID] = "invalid_station_id"
                else:
                    station_id = str(station_id_int)
            except (ValueError, TypeError):
                errors[CONF_STATION_ID] = "invalid_station_id"

            if not errors:
                await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
                self._abort_if_unique_id_configured()

                self._station_id = station_id
                fetched = await _fetch_station_name(
                    self.hass, station_id, self._provider_key
                )
                self._suggested_name = fetched or f"Station {station_id}"
                return await self.async_step_name()

        provider_cls = PROVIDER_REGISTRY.get(self._provider_key)
        hint = provider_cls.STATION_ID_HINT if provider_cls else "Enter the station ID."
        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            description_placeholders={"hint": hint},
            errors=errors,
        )

    # ---- Step 3b: county selector (county_search mode) --------------------------

    async def async_step_county(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select county — shown for providers with STATION_LOOKUP_MODE='county_search'."""
        if user_input is not None:
            self._station_county = user_input[CONF_STATION_COUNTY]
            return await self.async_step_station_picker()

        # Build county list from the country. For IE use the 26 counties + all-Ireland.
        county_options = _counties_for_country(self._country)
        return self.async_show_form(
            step_id="county",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_COUNTY): vol.In(county_options),
                }
            ),
        )

    # ---- Step 3c: station picker (county_search / location_search mode) ---------

    async def async_step_station_picker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a station from a live list — loaded from the provider."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input.get(CONF_STATION_ID, "")
            if not station_id:
                errors[CONF_STATION_ID] = "invalid_station_id"
            else:
                await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
                self._abort_if_unique_id_configured()
                self._station_id = station_id
                fetched = await _fetch_station_name(
                    self.hass, station_id, self._provider_key
                )
                self._suggested_name = fetched or f"Station {station_id}"
                return await self.async_step_name()

        # Load station list from provider
        provider_cls = PROVIDER_REGISTRY.get(self._provider_key)
        station_list: list[tuple[str, str]] = []
        if provider_cls:
            try:
                session = async_get_clientsession(self.hass)
                provider_instance = provider_cls("")
                station_list = await provider_instance.async_list_stations(
                    session, county=self._station_county
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Failed to load station list: %s", err)

        self._station_list = station_list

        if not station_list:
            errors["base"] = "no_stations_found"
            return self.async_show_form(
                step_id="station_picker",
                data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
                errors=errors,
            )

        return self.async_show_form(
            step_id="station_picker",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): vol.In(
                        {uid: label for uid, label in station_list}
                    ),
                }
            ),
            errors=errors,
        )

    # ---- Step 3d: location (location_search mode) --------------------------------

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect lat/lng and radius for location-based providers."""
        from .const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS_KM

        default_lat = self.hass.config.latitude
        default_lon = self.hass.config.longitude

        if user_input is not None:
            errors: dict[str, str] = {}
            try:
                self._latitude = float(user_input[CONF_LATITUDE])
                self._longitude = float(user_input[CONF_LONGITUDE])
                self._radius_km = float(
                    user_input.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
                )
            except (ValueError, TypeError):
                errors["base"] = "invalid_location"
                return self.async_show_form(
                    step_id="location",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_LATITUDE, default=default_lat
                            ): vol.Coerce(float),
                            vol.Required(
                                CONF_LONGITUDE, default=default_lon
                            ): vol.Coerce(float),
                            vol.Optional(
                                CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM
                            ): vol.Coerce(float),
                        }
                    ),
                    errors=errors,
                )
            self._station_id = ""
            self._suggested_name = (
                f"{_COUNTRY_NAMES.get(self._country, self._country)} "
                f"({self._latitude:.3f}, {self._longitude:.3f})"
            )
            return await self.async_step_name()

        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LATITUDE, default=default_lat): vol.Coerce(float),
                    vol.Required(CONF_LONGITUDE, default=default_lon): vol.Coerce(
                        float
                    ),
                    vol.Optional(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): vol.Coerce(
                        float
                    ),
                }
            ),
            errors={},
        )

    # ---- Step 4: confirm / edit name --------------------------------------------

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm or edit the entry display name."""
        if user_input is not None:
            title = user_input.get(CONF_NAME) or self._suggested_name
            data: dict[str, Any] = {
                CONF_COUNTRY: self._country,
                CONF_PROVIDER: self._provider_key,
            }
            if self._station_id:
                data[CONF_STATION_ID] = self._station_id
                if self._station_county:
                    data[CONF_STATION_COUNTY] = self._station_county
            else:
                from .const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS_KM

                data[CONF_LATITUDE] = self._latitude
                data[CONF_LONGITUDE] = self._longitude
                data[CONF_RADIUS_KM] = self._radius_km
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=self._suggested_name): str,
                }
            ),
        )
