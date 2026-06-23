"""Plugin base for Fuel Compare data providers.

A provider is a self-contained plugin that:
  1. Declares metadata (COUNTRY, PROVIDER_KEY, LABEL, CONFIG_MODE).
  2. Declares CAPABILITIES — the exact set of StationData keys it populates.
     The sensor/binary_sensor platforms read CAPABILITIES and create exactly
     those entities. No changes to the core component are ever needed when a
     new provider is added.
  3. Implements async_fetch() → StationData.
  4. Implements async_fetch_station_name() for the config flow.

Adding a new country / provider:
  - Write a new file: providers/{country_code}_{provider_name}.py
  - Subclass BaseProvider, set class attrs, implement the two abstract methods.
  - Register it in providers/__init__.py (one line).
  - Done. No changes to sensor.py, binary_sensor.py, coordinator.py or
    config_flow.py are required.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Final, Literal, TypedDict

from aiohttp import ClientSession

MAX_STATION_URL_LEN = 255


class ProviderError(Exception):
    """Raised by a provider when it cannot retrieve station data.

    Contributor note: the message string passed to ProviderError is
    USER-VISIBLE — it surfaces in Home Assistant's repairs / diagnostics
    UI via UpdateFailed. Keep it actionable and free of credentials:

    - DO include: country/postal codes, station IDs, HTTP status, parser
      cause, endpoint URL (without query-string secrets).
    - DO NOT include: API keys, bearer tokens, Authorization headers,
      raw upstream response bodies that may carry session cookies.

    The coordinator caps the surfaced text at ~240 chars but does NOT
    redact — providers are responsible for keeping the message clean.
    """


class StationData(TypedDict, total=False):
    """Normalised station data dict returned by every provider.

    All values are optional (total=False). Providers set the keys listed in
    their CAPABILITIES and leave the rest absent or None. The sensor platform
    only creates entities for keys present in CAPABILITIES, so absent keys
    are simply not surfaced in HA — no errors, no unavailable entities.

    Price normalisation rule: values >10 are treated as cents and divided
    by 100 so all prices are stored as EUR/litre (or local currency/litre).
    """

    # ── Fuel prices ──────────────────────────────────────────────────────────
    unleaded: float | None
    """Standard unleaded / petrol (E5/E10 generic). Legacy fuelcompare.ie key."""

    petrol: float | None
    """Petrol price (FuelFinder.ie key for unleaded)."""

    diesel: float | None
    """Standard diesel."""

    kerosene: float | None
    """Kerosene / heating oil."""

    cng: float | None
    """Compressed natural gas."""

    lpg: float | None
    """Autogas / LPG."""

    e10: float | None
    """E10 petrol (10% ethanol blend)."""

    e85: float | None
    """E85 (85% ethanol flex-fuel)."""

    premium_unleaded: float | None
    """Super unleaded / premium petrol."""

    premium_diesel: float | None
    """Premium diesel (BP Ultimate, Shell V-Power Diesel, etc.)."""

    adblue: float | None
    """AdBlue / diesel exhaust fluid."""

    # ── Station identity ──────────────────────────────────────────────────────
    name: str | None
    """Full station name e.g. 'Circle K Mulhuddart'."""

    tablename: str | None
    """Brand as a slug e.g. 'circle_k'. Legacy fuelcompare.ie field."""

    brand: str | None
    """Normalised brand name e.g. 'Circle K'. Preferred over tablename."""

    address: str | None
    """Street address."""

    county: str | None
    """County or region e.g. 'Co. Dublin'."""

    latitude: float | None
    """WGS84 latitude."""

    longitude: float | None
    """WGS84 longitude."""

    phone: str | None
    """Station phone number."""

    website: str | None
    """Station or brand website URL."""

    # ── Timing ───────────────────────────────────────────────────────────────
    lastupdated: str | None
    """ISO 8601 timestamp when the data source last updated prices."""

    working_hours: str | None
    """JSON-encoded weekly schedule: {"Monday": "6a.m.-10p.m.", ...}. fuelcompare.ie format."""

    opening_hours: str | None
    """OSM opening_hours format string: "Mo-Su 07:00-23:00" or "24/7". FuelFinder.ie format."""

    # ── Facilities (structured dicts) ────────────────────────────────────────
    # The legacy fuelcompare.ie nested structure:
    about: dict | None
    """Legacy nested dict: {category: {feature: bool}}."""

    # Flat facility dicts (preferred for new providers):
    accessibility: dict | None
    """Dict of accessibility features: {feature_name: bool}."""

    offerings: dict | None
    """Dict of fuel/service offerings: {feature_name: bool}."""

    amenities: dict | None
    """Dict of amenities: {feature_name: bool}."""

    payments: dict | None
    """Dict of accepted payment methods: {method_name: bool}."""

    # ── Flat facility booleans ────────────────────────────────────────────────
    # Providers that return flat booleans instead of dicts use these.
    # The FacilityBinarySensor reads directly from these keys.
    has_car_wash: bool | None
    has_shop: bool | None
    has_toilet: bool | None
    has_atm: bool | None
    has_disabled_access: bool | None
    has_electric_charging: bool | None
    accepts_cash: bool | None
    accepts_cards: bool | None
    accepts_contactless: bool | None

    # ── Open status ───────────────────────────────────────────────────────────
    is_open: bool | None
    """Current open/closed status if provided directly by the source."""

    # ── Provider-specific extras ──────────────────────────────────────────────
    price_confidence: str | None
    """FuelFinder.ie freshness tier: 'fresh' | 'likely' | 'outdated'."""

    has_price: bool | None
    """FuelFinder.ie: whether any community price exists for this station."""

    location: str | None
    """Formatted GPS location string: '{lat},{lng}' (e.g. '53.345349,-6.2779')."""

    # ── Meta ─────────────────────────────────────────────────────────────────
    source_station_id: str | None
    """The provider's own station identifier (for attribute passthrough)."""


# All keys that the sensor platform knows how to handle.
# Used as a reference — providers declare a subset in CAPABILITIES.
ALL_SENSOR_KEYS: Final[frozenset[str]] = frozenset(StationData.__optional_keys__)


class BaseProvider(ABC):
    """Abstract base class for a Fuel Compare data provider plugin.

    Subclass this, set the class attributes, implement the two abstract
    methods, and register in providers/__init__.py. That is all.
    """

    # ── Required class attributes (enforced by __init_subclass__) ────────────

    COUNTRY: ClassVar[str]
    """ISO 3166-1 alpha-2 country code, e.g. 'IE', 'DE', 'FR'."""

    PROVIDER_KEY: ClassVar[str]
    """Unique machine key stored in config entry data, e.g. 'ie_fuelfinder'."""

    LABEL: ClassVar[str]
    """Human-readable label shown in the config flow provider picker."""

    # ── Optional class attributes (with defaults) ─────────────────────────────

    CONFIG_MODE: ClassVar[Literal["station_id", "location"]] = "station_id"
    """How the user selects what to track.

    'station_id' — user enters a numeric/string identifier (default).
    'location'   — user enters lat/lng + radius; coordinator fetches all
                   stations in the radius and creates entities dynamically.

    NOTE: This attribute is read by config_flow._dispatch_after_provider() to
    determine whether to show the location step.  Keep it even for providers
    that also set STATION_LOOKUP_MODE='location_search' — the config flow
    checks CONFIG_MODE == 'location' as a fallback route to async_step_location.
    """

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Default: the 12 entities created by the original fuelcompare.ie provider.
            # Override in subclasses to declare exactly what your provider populates.
            "unleaded",
            "diesel",
            "lastupdated",
            "name",
            "brand",
            "county",
            "working_hours",
            "accessibility",
            "offerings",
            "amenities",
            "payments",
            "is_open",
        }
    )
    """Set of StationData keys this provider populates.

    The sensor and binary_sensor platforms iterate CAPABILITIES to decide
    which entities to create. Only keys listed here get entities. Unknown
    keys are silently ignored.

    'data_fetch_problem' (binary_sensor) — must NOT be listed here; it is
    always created unconditionally by the coordinator and is never gated
    by CAPABILITIES.

    'last_successful_fetch' (sensor) — must NOT be listed here; it is
    always created unconditionally in sensor.async_setup_entry (same
    pattern as data_fetch_problem). Listing it would cause a duplicate
    entity with the same unique_id.

    Keys 'source_station_id' and 'tablename' must NOT be listed here either.
    A provider may still populate them in the dict it returns from
    async_fetch — the sensor platform reads them directly from coordinator
    data (source_station_id surfaces as a device attribute on the diagnostic
    Station ID sensor; tablename acts as a fallback brand label when 'brand'
    is absent). Because they bypass the CAPABILITIES gating mechanism a
    declaration here is rejected by __init_subclass__ (see FORBIDDEN_CAPS).
    """

    STATION_ID_HINT: ClassVar[str] = "Enter the station ID from the station URL."
    """Short description shown in the config flow station-ID input field.

    Override to give provider-specific guidance e.g.:
    'For fuelfinder.ie/station/123, enter 123.'
    Only used when STATION_LOOKUP_MODE='manual_id'.
    """

    POLL_INTERVAL_SECONDS: ClassVar[int] = 1800
    """Default polling interval in seconds. Coordinator uses this value.

    Override to set a provider-appropriate cadence. Government open-data
    APIs that update every 30 min should use 1800. Real-time APIs can use
    600. Daily (FuelWatch WA) should use 86400.
    """

    STATION_LOOKUP_MODE: ClassVar[
        Literal["manual_id", "county_search", "location_search"]
    ] = "manual_id"
    """How the config flow helps the user identify their station.

    'manual_id'      — user types a station ID directly (default, fuelcompare.ie).
    'county_search'  — user picks a county then selects from a live station list.
    'location_search'— user enters lat/lng + radius, then selects from a live list.
    """

    REQUIRES_API_KEY: ClassVar[bool] = False
    """Whether this provider requires an API key to function.

    Set to True in subclasses that require the user to supply an API key.
    The config flow uses this flag to conditionally show the API key input step.
    """

    NEEDS_POSTAL_CODE: ClassVar[bool] = False
    """Whether this provider requires a postal code for location-based lookups.

    Set to True in subclasses whose __init__ accepts a postal_code parameter.
    The config flow uses this flag instead of inspect.signature() detection.
    """

    DISABLED: ClassVar[bool] = False
    """If True, the provider is hidden from the config flow country/provider list.

    Use this to soft-disable a provider whose upstream is broken, has changed
    its API contract, or is otherwise known-failing. The provider class stays
    importable and registered (so existing config entries keep loading and
    don't blow up with KeyError) but new entries cannot be created. Flip back
    to False once the provider is fixed.

    UX note: if every provider for a given country has DISABLED=True, the
    country itself disappears from the config flow's country picker — the
    user is never offered a country with no working providers.
    """

    API_KEY_REGISTRATION_URL: ClassVar[str] = ""
    """URL where the user can register for an API key.

    Only meaningful when REQUIRES_API_KEY is True. Shown in the config flow
    to guide the user to the correct registration page.
    """

    STATION_PAGE_URL: ClassVar[str] = ""
    """Homepage / data source URL for this provider.

    Shown as the Station Page URL sensor when no per-station URL is available.
    Always set this so every entry gets a clickable URL.
    Example: 'https://www.tankerkoenig.de'
    """

    STATION_PAGE_URL_TEMPLATE: ClassVar[str] = ""
    """URL template for a per-station detail page.

    Use '{station_id}' as the placeholder for the station identifier.
    When set, get_station_page_url() substitutes {station_id} automatically.
    When empty, falls back to STATION_PAGE_URL (the provider homepage).
    Example: 'https://www.tankerkoenig.de/?page=details&id={station_id}'
    """

    CURRENCY: ClassVar[str] = "€"
    """Currency unit for fuel price sensors (unit_of_measurement).

    Use "€" for EUR providers to maintain backward compatibility with existing
    HA long-term statistics (prior releases used homeassistant.const.CURRENCY_EURO).
    Override in subclasses for non-EUR providers:
      £      — United Kingdom (GBP)
      A$     — Australia (AUD)
      kr     — Norway (NOK) / Denmark (DKK) / Sweden (SEK) / Iceland (ISK)
      Fr.    — Switzerland (CHF)
      Kč     — Czech Republic (CZK)
      zł     — Poland (PLN)
      CA$    — Canada (CAD)
      KM     — Bosnia and Herzegovina (BAM)
    """

    # ── Enforcement ──────────────────────────────────────────────────────────

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls):
            for attr in ("COUNTRY", "PROVIDER_KEY", "LABEL"):
                if not hasattr(cls, attr):
                    raise TypeError(
                        f"{cls.__name__} must define class attribute '{attr}'"
                    )
            unknown = cls.CAPABILITIES - ALL_SENSOR_KEYS - {"data_fetch_problem"}
            if unknown:
                raise TypeError(
                    f"{cls.__name__}.CAPABILITIES contains unknown keys: {unknown}. "
                    f"Add them to StationData first."
                )
            FORBIDDEN_CAPS = {
                "source_station_id",
                "tablename",
                "data_fetch_problem",  # always created by coordinator, never gated
                "last_successful_fetch",  # always created by sensor platform, never gated
            }
            forbidden = cls.CAPABILITIES & FORBIDDEN_CAPS
            if forbidden:
                raise TypeError(
                    f"{cls.__name__}.CAPABILITIES contains forbidden keys: {forbidden}"
                )
            # Enforce that non-manual_id providers define async_list_stations
            # directly in their own class dict (cls.__dict__).  Checking only
            # cls.__dict__ (rather than the full MRO) prevents a mixin or parent
            # that incidentally defines the method from silently satisfying the
            # contract for a concrete class that forgot to override it.
            # Subclasses of concrete providers (e.g. thin test-fakes) must
            # define their own pass-through override.
            if (
                cls.STATION_LOOKUP_MODE != "manual_id"
                and "async_list_stations" not in cls.__dict__
            ):
                raise TypeError(cls.__name__ + " must override async_list_stations")
            if (
                cls.STATION_PAGE_URL_TEMPLATE
                and "{station_id}" not in cls.STATION_PAGE_URL_TEMPLATE
            ):
                raise TypeError(
                    f"{cls.__name__}.STATION_PAGE_URL_TEMPLATE must contain "
                    "'{station_id}' placeholder, got: "
                    f"{cls.STATION_PAGE_URL_TEMPLATE!r}"
                )

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data.

        Return a StationData dict with all keys declared in CAPABILITIES
        populated (values may be None if the source has no data for that
        field). Keys not in CAPABILITIES should be absent from the dict.

        For CONFIG_MODE='station_id': use station_id parameter.
        For CONFIG_MODE='location': ignore station_id; use coordinates
          stored at construction time.

        Raise ProviderError on unrecoverable data errors.
        Let aiohttp ClientError propagate (coordinator converts to UpdateFailed).
        """

    @abstractmethod
    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a human-readable station name for the config flow, or None.

        Called once during setup to pre-populate the name confirmation step.
        May return None — the config flow falls back to 'Station {id}'.

        For CONFIG_MODE='location' providers, returning None is correct;
        the config flow uses the auto-generated 'Country (lat, lon)' title.
        """

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return a list of (station_id, display_label) tuples for the station picker.

        Called by the config flow when STATION_LOOKUP_MODE is 'county_search' or
        'location_search'. The config flow passes appropriate keyword arguments:
          - county_search:   county='dublin'
          - location_search: lat=53.3498, lng=-6.2603, radius_km=10

        Returns an ordered list suitable for a vol.In dropdown:
          [("uuid-1", "Circle K Taney — Diesel €1.83/L"), ...]

        Providers that set STATION_LOOKUP_MODE != 'manual_id' must override this
        method. The default implementation returns an empty list (safe fallback).
        """
        return []

    def get_station_page_url(self, station_id: str) -> str | None:
        """Return a URL for the station's page on the provider website, or None.

        Called by the config flow after async_list_stations returns. Providers
        that populate an internal cache during async_list_stations (e.g. a slug
        cache) can use that cache here. Must only be called after
        async_list_stations has returned for the same provider instance —
        calling it before will yield None even for providers that support URLs.

        Default behaviour:
        - If STATION_PAGE_URL_TEMPLATE is set, substitutes {station_id} and returns it.
        - Else if STATION_PAGE_URL is set, returns the homepage URL.
        - Else returns None.

        URLs longer than MAX_STATION_URL_LEN characters (HA state value limit) fall back to
        the provider homepage (STATION_PAGE_URL), or None if no homepage is set.

        Override in providers that need dynamic URL construction (e.g. slug cache).
        """
        if self.STATION_PAGE_URL_TEMPLATE:
            url = self.STATION_PAGE_URL_TEMPLATE.format(station_id=station_id)
            if len(url) > MAX_STATION_URL_LEN:
                return self.STATION_PAGE_URL or None
            return url
        return self.STATION_PAGE_URL or None
