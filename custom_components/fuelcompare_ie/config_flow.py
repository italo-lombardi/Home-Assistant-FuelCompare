"""Config flow for Fuel Compare integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_COUNTRY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_POSTAL_CODE,
    CONF_PROVIDER,
    CONF_RADIUS_KM,
    CONF_SHOW_ON_MAP,
    CONF_STATION_COUNTY,
    CONF_STATION_ID,
    CONF_STATION_PAGE_URL,
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DEFAULT_RADIUS_KM,
    DOMAIN,
)
from .providers import PROVIDER_REGISTRY

_LOGGER = logging.getLogger(__name__)

_PICKER_LABEL_SUFFIX_RE = re.compile(r"\s*\(#[0-9a-f-]+\)\s*$", re.IGNORECASE)


def _name_from_picker_label(label: str) -> str:
    """Strip the trailing '(#uuid)' suffix from a station picker label."""
    return _PICKER_LABEL_SUFFIX_RE.sub("", label).strip()


_COUNTRY_NAMES: dict[str, str] = {
    # Ireland — primary market
    "IE": "Ireland",
    # Europe — alphabetical by display name
    "AL": "Albania",
    "AT": "Austria",
    "BA": "Bosnia and Herzegovina",
    "BE": "Belgium",
    "HR": "Croatia",
    "CZ": "Czech Republic",
    "DK": "Denmark",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "IS": "Iceland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MT": "Malta",
    "MD": "Moldova",
    "ME": "Montenegro",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "SI": "Slovenia",
    "ES": "Spain",
    "SE": "Sweden",
    "CH": "Switzerland",
    "GB": "United Kingdom",
    # Oceania
    "AU": "Australia",
    # Americas
    "CA": "Canada",
    # Cross-country / aggregated
    "EU": "European Union (Oil Bulletin)",
}


def _countries_from_registry() -> list[tuple[str, str]]:
    """Derive (ISO code, display label) list from PROVIDER_REGISTRY.

    Hides countries whose every provider is DISABLED.
    """
    seen: set[str] = set()
    for cls in PROVIDER_REGISTRY.values():
        if getattr(cls, "DISABLED", False):
            continue
        seen.add(cls.COUNTRY)
    pairs = [(code, _COUNTRY_NAMES.get(code, code)) for code in seen]
    pairs.sort(key=lambda x: x[1])
    return pairs


def _providers_for_country(country: str) -> list[tuple[str, str]]:
    """Return (provider_key, label) pairs for a given country code.

    Hides any provider with DISABLED=True so the user can't pick a known-broken
    upstream. Existing entries keep loading because the registry still contains
    the class.
    """
    pairs = [
        (cls.PROVIDER_KEY, cls.LABEL)
        for cls in PROVIDER_REGISTRY.values()
        if cls.COUNTRY == country and not getattr(cls, "DISABLED", False)
    ]
    pairs.sort(key=lambda x: x[1])
    return pairs


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
    "AL": {
        "albania": "Albania (all)",
    },
    "AT": {
        "burgenland": "Burgenland",
        "carinthia": "Carinthia (Kärnten)",
        "lower_austria": "Lower Austria (Niederösterreich)",
        "salzburg": "Salzburg",
        "styria": "Styria (Steiermark)",
        "tyrol": "Tyrol (Tirol)",
        "upper_austria": "Upper Austria (Oberösterreich)",
        "vienna": "Vienna (Wien)",
        "vorarlberg": "Vorarlberg",
    },
    "BA": {
        "bosnia": "Bosnia and Herzegovina (all)",
    },
    "BE": {
        "belgium": "Belgium (all)",
    },
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
    "CZ": {
        "czech_republic": "Czech Republic (all)",
    },
    "DK": {
        "denmark": "Denmark (all)",
    },
    "FI": {
        "finland": "Finland (all)",
    },
    "FR": {
        "auvergne_rhone_alpes": "Auvergne-Rhône-Alpes",
        "bourgogne_franche_comte": "Bourgogne-Franche-Comté",
        "bretagne": "Bretagne",
        "centre_val_de_loire": "Centre-Val de Loire",
        "corse": "Corse",
        "grand_est": "Grand Est",
        "hauts_de_france": "Hauts-de-France",
        "ile_de_france": "Île-de-France",
        "normandie": "Normandie",
        "nouvelle_aquitaine": "Nouvelle-Aquitaine",
        "occitanie": "Occitanie",
        "pays_de_la_loire": "Pays de la Loire",
        "provence_alpes_cote_d_azur": "Provence-Alpes-Côte d'Azur",
    },
    "DE": {
        "berlin": "Berlin",
        "bavaria": "Bavaria (Bayern)",
        "north_rhine_westphalia": "North Rhine-Westphalia (Nordrhein-Westfalen)",
        "baden_wurttemberg": "Baden-Württemberg",
        "lower_saxony": "Lower Saxony (Niedersachsen)",
        "hesse": "Hesse (Hessen)",
        "saxony": "Saxony (Sachsen)",
        "rhineland_palatinate": "Rhineland-Palatinate (Rheinland-Pfalz)",
        "saxony_anhalt": "Saxony-Anhalt (Sachsen-Anhalt)",
        "thuringia": "Thuringia (Thüringen)",
        "brandenburg": "Brandenburg",
        "mecklenburg_vorpommern": "Mecklenburg-Vorpommern",
        "hamburg": "Hamburg",
        "saarland": "Saarland",
        "schleswig_holstein": "Schleswig-Holstein",
        "bremen": "Bremen",
    },
    "GR": {
        "greece": "Greece (all)",
    },
    "IS": {
        "iceland": "Iceland (all)",
    },
    "IT": {
        "abruzzo": "Abruzzo",
        "basilicata": "Basilicata",
        "calabria": "Calabria",
        "campania": "Campania",
        "emilia_romagna": "Emilia-Romagna",
        "friuli_venezia_giulia": "Friuli-Venezia Giulia",
        "lazio": "Lazio",
        "liguria": "Liguria",
        "lombardia": "Lombardia",
        "marche": "Marche",
        "molise": "Molise",
        "piemonte": "Piemonte",
        "puglia": "Puglia",
        "sardegna": "Sardegna",
        "sicilia": "Sicilia",
        "toscana": "Toscana",
        "trentino_alto_adige": "Trentino-Alto Adige",
        "umbria": "Umbria",
        "valle_d_aosta": "Valle d'Aosta",
        "veneto": "Veneto",
    },
    "LT": {
        "lithuania": "Lithuania (all)",
    },
    "LU": {
        "luxembourg": "Luxembourg (all)",
    },
    "MT": {
        "malta": "Malta (all)",
    },
    "MD": {
        "moldova": "Moldova (all)",
    },
    "ME": {
        "montenegro": "Montenegro (all)",
    },
    "NL": {
        "netherlands": "Netherlands (all)",
    },
    "NO": {
        "norway": "Norway (all)",
    },
    "PL": {
        "poland": "Poland (all)",
    },
    "PT": {
        "aveiro": "Aveiro",
        "beja": "Beja",
        "braga": "Braga",
        "braganca": "Bragança",
        "castelo_branco": "Castelo Branco",
        "coimbra": "Coimbra",
        "evora": "Évora",
        "faro": "Faro",
        "guarda": "Guarda",
        "leiria": "Leiria",
        "lisboa": "Lisboa",
        "portalegre": "Portalegre",
        "porto": "Porto",
        "santarem": "Santarém",
        "setubal": "Setúbal",
        "viana_do_castelo": "Viana do Castelo",
        "vila_real": "Vila Real",
        "viseu": "Viseu",
    },
    "SI": {
        "slovenia": "Slovenia (all)",
    },
    "ES": {
        "andalucia": "Andalucía",
        "aragon": "Aragón",
        "asturias": "Asturias",
        "balearic_islands": "Balearic Islands (Illes Balears)",
        "basque_country": "Basque Country (País Vasco)",
        "canary_islands": "Canary Islands (Canarias)",
        "cantabria": "Cantabria",
        "castilla_la_mancha": "Castilla-La Mancha",
        "castilla_y_leon": "Castilla y León",
        "catalonia": "Catalonia (Catalunya)",
        "extremadura": "Extremadura",
        "galicia": "Galicia",
        "la_rioja": "La Rioja",
        "madrid": "Community of Madrid",
        "murcia": "Region of Murcia",
        "navarre": "Navarre (Navarra)",
        "valencian_community": "Valencian Community",
    },
    "SE": {
        "sweden": "Sweden (all)",
    },
    "CH": {
        "switzerland": "Switzerland (all)",
    },
    "GB": {
        "england": "England",
        "scotland": "Scotland",
        "wales": "Wales",
        "northern_ireland": "Northern Ireland",
    },
    "AU": {
        "western_australia": "Western Australia",
        "nsw_tasmania": "NSW + Tasmania",
        "queensland": "Queensland",
        "victoria": "Victoria",
    },
    "CA": {
        "quebec": "Québec",
    },
    "EU": {
        "eu": "European Union (all member states)",
    },
}


def _counties_for_country(country: str) -> dict[str, str]:
    """Return {lowercase_key: display_label} county map for a country."""
    return _COUNTY_OPTIONS_BY_COUNTRY.get(country, {})


async def _fetch_station_name(
    hass, station_id: str, provider_key: str = DEFAULT_PROVIDER, api_key: str = ""
) -> str | None:
    """Resolve station display name via the selected provider.

    Calls provider.async_fetch_station_name() directly so all providers work,
    not just the IE fuelcompare.ie provider.
    """
    provider_cls = PROVIDER_REGISTRY.get(provider_key)
    if provider_cls is None:
        return None
    try:
        session = async_get_clientsession(hass)
        if api_key and getattr(provider_cls, "REQUIRES_API_KEY", False):
            provider = provider_cls(station_id, api_key=api_key)
        else:
            provider = provider_cls(station_id)
        return await provider.async_fetch_station_name(session, station_id)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to fetch station name for %s: %s", station_id, err)
    return None


class FuelCompareIEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fuel Compare."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for this config entry."""
        return FuelCompareIEOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._country: str = DEFAULT_COUNTRY
        self._provider_key: str = DEFAULT_PROVIDER
        self._station_id: str = ""
        self._station_county: str = ""  # stored for county_search providers
        self._postal_code: str = ""  # for postal-code-centric providers (e.g. be_carbu)
        self._station_list: list[tuple[str, str]] = []  # for station picker
        self._station_url_map: dict[str, str] = {}  # station_id → provider page URL
        self._station_page_url: str = (
            ""  # URL for selected station (shown on name step)
        )
        self._show_on_map: bool = False
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._radius_km: float = DEFAULT_RADIUS_KM
        self._suggested_name: str = ""
        self._api_key: str = ""  # optional API key for providers that require it

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

        if not countries:
            return self.async_abort(reason="no_providers_for_country")

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
            return self.async_abort(reason="no_providers_for_country")
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
        # Providers that require an API key get an extra step first
        if getattr(provider_cls, "REQUIRES_API_KEY", False) and not self._api_key:
            return await self.async_step_api_key()
        mode = getattr(provider_cls, "STATION_LOOKUP_MODE", "manual_id")
        if mode == "county_search":
            return await self.async_step_county()
        if mode == "global_list":
            # Provider returns a fixed global list of stations/regions
            # (e.g. EU Oil Bulletin: 27 member states + aggregates).
            # No coordinates or county needed — go straight to the picker.
            return await self.async_step_station_picker()
        if (
            getattr(provider_cls, "CONFIG_MODE", None) == "location"
            or mode == "location_search"
        ):
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
            description_placeholders={
                "deprecation_notice": (
                    "⚠️ fuelcompare.ie is shutting down on 30 June 2026. "
                    "If you select FuelCompare IE, data will stop updating after that date."
                )
                if any(key == "ie_fuelcompare" for key, _ in providers)
                else "",
            },
        )

    # ---- Step 3a: station ID (manual_id mode) -----------------------------------

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect an API key for providers that require authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if not api_key:
                errors[CONF_API_KEY] = "invalid_api_key"
            else:
                self._api_key = api_key
                return await self._dispatch_after_provider()

        provider_cls_for_key = PROVIDER_REGISTRY.get(self._provider_key)
        registration_url: str = getattr(
            provider_cls_for_key,
            "API_KEY_REGISTRATION_URL",
            "",
        )
        return self.async_show_form(
            step_id="api_key",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            description_placeholders={"registration_url": registration_url},
            errors=errors,
        )

    # ---- Step 3b: station ID (manual_id mode) -----------------------------------

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate the station ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID].strip()

            if not station_id:
                errors[CONF_STATION_ID] = "invalid_station_id"

            if not errors:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{self._provider_key}_{station_id}"
                )
                self._abort_if_unique_id_configured()

                self._station_id = station_id
                fetched = await _fetch_station_name(
                    self.hass, station_id, self._provider_key, self._api_key
                )
                self._suggested_name = fetched or f"Station {station_id[:8]}"
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
        if not county_options:
            return self.async_abort(reason="no_counties_for_country")
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
                await self.async_set_unique_id(
                    f"{DOMAIN}_{self._provider_key}_{station_id}"
                )
                self._abort_if_unique_id_configured()
                self._station_id = station_id
                self._station_page_url = self._station_url_map.get(station_id, "")
                if user_input.get(CONF_SHOW_ON_MAP):
                    self._show_on_map = True
                fetched = await _fetch_station_name(
                    self.hass, station_id, self._provider_key, self._api_key
                )
                if not fetched:
                    picker_label = next(
                        (lbl for uid, lbl in self._station_list if uid == station_id),
                        None,
                    )
                    fetched = (
                        _name_from_picker_label(picker_label) if picker_label else None
                    )
                self._suggested_name = fetched or f"Station {station_id[:8]}"
                return await self.async_step_name()

        # Load station list from provider
        provider_cls = PROVIDER_REGISTRY.get(self._provider_key)
        station_list: list[tuple[str, str]] = []
        if provider_cls:
            try:
                session = async_get_clientsession(self.hass)
                init_kwargs: dict[str, Any] = {}
                if self._api_key and getattr(provider_cls, "REQUIRES_API_KEY", False):
                    init_kwargs["api_key"] = self._api_key
                provider_instance = provider_cls("", **init_kwargs)
                list_kwargs: dict[str, Any] = {"county": self._station_county}
                if self._postal_code:
                    list_kwargs["postal_code"] = self._postal_code
                if self._latitude is not None:
                    list_kwargs["lat"] = self._latitude
                if self._longitude is not None:
                    list_kwargs["lng"] = self._longitude
                list_kwargs["radius_km"] = self._radius_km
                station_list = await provider_instance.async_list_stations(
                    session, **list_kwargs
                )
                self._station_url_map = {
                    uid: url
                    for uid, _ in station_list
                    if (url := provider_instance.get_station_page_url(uid))
                }
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to load station list: %s", err)

        station_list = sorted(station_list, key=lambda x: x[1].lower())
        self._station_list = station_list

        has_location_caps = (
            provider_cls is not None
            and {"latitude", "longitude"} <= provider_cls.CAPABILITIES
        )
        needs_station_id = (
            provider_cls is not None
            and getattr(provider_cls, "CONFIG_MODE", "station_id") == "station_id"
        )
        schema_dict: dict = {}
        if not station_list:
            if self.unique_id and not needs_station_id:
                return await self.async_step_name()
            mode = (
                getattr(provider_cls, "STATION_LOOKUP_MODE", "manual_id")
                if provider_cls
                else "manual_id"
            )
            # Mode-aware error message. Order matters: global_list always wins
            # over coordinates/county fallbacks because a global-list provider
            # may also carry stale lat/lng on the flow (e.g. EU Oil Bulletin
            # entries created before the global_list dispatch was added).
            if mode == "global_list":
                errors["base"] = "no_stations_found_global"
            elif mode in ("location_search",) or (
                self._latitude is not None and mode != "county_search"
            ):
                errors["base"] = "no_stations_found_location"
            else:
                errors["base"] = "no_stations_found"
            schema_dict[vol.Required(CONF_STATION_ID)] = str
        else:
            schema_dict[vol.Required(CONF_STATION_ID)] = vol.In(
                {uid: label for uid, label in station_list}
            )
        if has_location_caps:
            schema_dict[vol.Optional(CONF_SHOW_ON_MAP, default=False)] = bool

        return self.async_show_form(
            step_id="station_picker",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    # ---- Step 3d: location (location_search mode) --------------------------------

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect lat/lng and radius for location-based providers."""
        default_lat = self.hass.config.latitude
        default_lon = self.hass.config.longitude

        # Detect if the selected provider needs a postal code (e.g. be_carbu).
        provider_cls = PROVIDER_REGISTRY.get(self._provider_key)
        needs_postal = provider_cls is not None and getattr(
            provider_cls, "NEEDS_POSTAL_CODE", False
        )

        def _location_schema(lat: float, lon: float, needs_pc: bool) -> vol.Schema:
            schema_dict: dict = {
                vol.Required(CONF_LATITUDE, default=lat): vol.All(
                    vol.Coerce(float), vol.Range(min=-90, max=90)
                ),
                vol.Required(CONF_LONGITUDE, default=lon): vol.All(
                    vol.Coerce(float), vol.Range(min=-180, max=180)
                ),
                vol.Optional(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=500)
                ),
            }
            if needs_pc:
                schema_dict[vol.Optional(CONF_POSTAL_CODE, default="")] = str
            return vol.Schema(schema_dict)

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
                    data_schema=_location_schema(
                        default_lat, default_lon, needs_postal
                    ),
                    errors=errors,
                )
            if needs_postal:
                self._postal_code = str(user_input.get(CONF_POSTAL_CODE) or "").strip()
            self._station_id = ""
            # 4 decimal places ≈ 11 m precision; stations closer than ~11 m share an entry_id
            unique = f"{DOMAIN}_{self._provider_key}_{self._latitude:.4f}_{self._longitude:.4f}"
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            # Suggested name: set a location-based fallback here only for
            # providers that don't require a station ID (location-only).
            # Station-picker providers overwrite this after the user picks a station.
            provider_cls_loc = PROVIDER_REGISTRY.get(self._provider_key)
            _is_station_id_mode = (
                provider_cls_loc is not None
                and getattr(provider_cls_loc, "CONFIG_MODE", "station_id")
                == "station_id"
            )
            if not _is_station_id_mode:
                self._suggested_name = (
                    f"{_COUNTRY_NAMES.get(self._country, self._country)} "
                    f"({self._latitude:.3f}, {self._longitude:.3f})"
                )
            return await self.async_step_station_picker()

        return self.async_show_form(
            step_id="location",
            data_schema=_location_schema(default_lat, default_lon, needs_postal),
            errors={},
        )

    # ---- Step 4: confirm / edit name --------------------------------------------

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm or edit the entry display name."""
        if user_input is not None:
            title = (user_input.get(CONF_NAME) or "").strip() or self._suggested_name
            data: dict[str, Any] = {
                CONF_COUNTRY: self._country,
                CONF_PROVIDER: self._provider_key,
            }
            options: dict[str, Any] = {}
            if self._api_key:
                options[CONF_API_KEY] = self._api_key
            if self._show_on_map:
                options[CONF_SHOW_ON_MAP] = True
            if self._station_id:
                data[CONF_STATION_ID] = self._station_id
                if self._station_county:
                    data[CONF_STATION_COUNTY] = self._station_county
            if self._postal_code:
                data[CONF_POSTAL_CODE] = self._postal_code
            if self._station_page_url:
                data[CONF_STATION_PAGE_URL] = self._station_page_url
            if self._latitude is not None and self._longitude is not None:
                data[CONF_LATITUDE] = self._latitude
                data[CONF_LONGITUDE] = self._longitude
                options[CONF_RADIUS_KM] = self._radius_km
            return self.async_create_entry(title=title, data=data, options=options)

        schema_dict: dict = {
            vol.Optional(CONF_NAME, default=self._suggested_name): str,
        }

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(schema_dict),
        )


class FuelCompareIEOptionsFlow(OptionsFlowWithConfigEntry):
    """Handle options for Fuel Compare — preserves the API key across re-opens."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options, pre-filling the existing API key and radius."""
        is_location_entry = CONF_LATITUDE in self.config_entry.data
        provider_key = self.config_entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
        provider_cls = PROVIDER_REGISTRY.get(provider_key)
        requires_api_key = getattr(provider_cls, "REQUIRES_API_KEY", False)
        has_location_caps = (
            provider_cls is not None
            and {
                "latitude",
                "longitude",
            }
            <= provider_cls.CAPABILITIES
        )

        existing_key = self.config_entry.options.get(CONF_API_KEY, "")
        current_show_on_map = self.config_entry.options.get(CONF_SHOW_ON_MAP, False)

        if is_location_entry:
            current_radius = self.config_entry.options.get(
                CONF_RADIUS_KM,
                self.config_entry.data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM),
            )
            schema_dict: dict = {}
            if requires_api_key:
                schema_dict[vol.Optional(CONF_API_KEY, default=existing_key)] = str
            schema_dict[vol.Optional(CONF_RADIUS_KM, default=current_radius)] = vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=500)
            )
            if has_location_caps:
                schema_dict[
                    vol.Optional(CONF_SHOW_ON_MAP, default=current_show_on_map)
                ] = bool
        else:
            schema_dict = {}
            if requires_api_key:
                schema_dict[vol.Optional(CONF_API_KEY, default=existing_key)] = str
            if has_location_caps:
                schema_dict[
                    vol.Optional(CONF_SHOW_ON_MAP, default=current_show_on_map)
                ] = bool
            # Non-location entries with no configurable options finalise immediately.
            if not schema_dict and user_input is None:
                return self.async_create_entry(data={})

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            errors: dict[str, str] = {}
            if (
                CONF_API_KEY in user_input
                and not (user_input[CONF_API_KEY] or "").strip()
            ):
                errors[CONF_API_KEY] = "invalid_api_key"
                return self.async_show_form(
                    step_id="init", data_schema=schema, errors=errors
                )
            return self.async_create_entry(data=user_input)

        return self.async_show_form(step_id="init", data_schema=schema)
