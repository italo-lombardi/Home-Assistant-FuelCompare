"""IEFuelFinderProvider — fetches Irish fuel prices from fuelfinder.ie.

FuelFinder.ie is a crowd-sourced fuel price tracker operated by Conjora
Limited (Ireland).  All read endpoints return plain JSON; no API key or
OAuth token is required.  The only gate is a Vercel edge-middleware
bot-detection check that inspects Fetch Metadata headers and the
User-Agent string.  Blocked UAs include curl/*, python-requests/*, and
Wget/*.  This provider sends the headers confirmed to bypass the check.

Endpoint strategy
-----------------
Two endpoints are used per poll cycle:

1. GET /api/fuelfinder/init
   Returns national-average stats (diesel, petrol, kerosene_500l) and the
   cheapest/most-expensive station cards.  Cached server-side at
   s-maxage=300, so polling more frequently than 5 min wastes requests.
   Not used for per-station prices but populates the ``source_station_id``
   attribute confirming the national context.

2. GET /api/fuelfinder/stations?city={county}&fuel={fuel_type}
   Returns all stations in a county sorted cheapest-first for one fuel
   type.  Cache-Control: no-store (live data).  Called once per fuel type
   per poll cycle.  The target station is found by matching its UUID
   (``id`` field) against the configured ``station_id``.

On the first fetch the county is unknown, so ``city=ireland`` is used to
search the full national dataset (~1000 stations).  The county is then
cached and used in subsequent polls to keep response payloads small
(county-level responses are ~100–200 stations).

Auth headers
------------
All requests carry:
  User-Agent:     HomeAssistant/<HA_VERSION> aiohttp/<AIOHTTP_VERSION>  (see const.UA_HEADER)
  Accept:         application/json
  Referer:        https://www.fuelfinder.ie/fuelfinder
  Sec-Fetch-Site: same-origin
  Sec-Fetch-Mode: cors
  Sec-Fetch-Dest: empty

Without these the server returns HTTP 403 text/plain "Forbidden".

Price normalisation
-------------------
FuelFinder returns ``price`` as a float in EUR/litre already (e.g. 1.838).
The ``> 10 → /100`` cents guard used by IEFuelCompareProvider must NOT be
applied here.

StationData key mapping
-----------------------
FuelFinder field  → StationData key   Notes
-----------------   ----------------   -----
price (diesel)    → diesel             float EUR/litre, no conversion
price (petrol)    → unleaded           renamed for StationData compat
price (kerosene)  → (extra attr only)  no StationData key for kerosene
price (cng)       → (extra attr only)  no StationData key for cng
updated_at        → lastupdated        ISO8601+TZ string
name              → name               clean display name, no transform
brand             → tablename + brand  brand is display string; tablename
                                        set to same value (sensor strips _)
county            → county             title-cased already from API
street            → address
phone             → phone
website           → website
lat               → latitude
lng               → longitude
opening_hours     → working_hours      raw OSM string (e.g. "Mo-Su 07:00-23:00")
                                        the existing StationWorkingHoursSensor
                                        tries json.loads() on this and returns
                                        None when it fails — expected behaviour
                                        for OSM format; sensor stays available

Extra fields returned (in StationData dict but not in CAPABILITIES, so they
travel as attribute passthrough only):
  confidence    — 'fresh'|'likely'|'outdated'|None
  osm_id        — OpenStreetMap node ID
  slug          — URL slug for the station detail page
  logo_url      — Google Favicon CDN URL
  has_price     — bool: any community price exists for this station
  kerosene      — float|None EUR/litre kerosene price
  cng           — float|None EUR/litre CNG price
  source_station_id — the UUID (mirrored into StationData.source_station_id)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://www.fuelfinder.ie/api/fuelfinder"

# Slugs ending in a long numeric ID are internal system IDs (observed: 7+ digits)
# that 404 on the public site — used by get_station_page_url to fall back to homepage.
_INTERNAL_ID_RE = re.compile(r"-(\d+)$")
_INTERNAL_ID_MIN_LEN = 7

# Headers required to pass the Vercel edge-middleware bot-detection gate.
# All read endpoints require these; sending a subset may work on cached
# responses but will fail on cache misses and no-store endpoints.
_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/json",
    "Referer": "https://www.fuelfinder.ie/fuelfinder",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# Fuel types to fan out per poll cycle.  CNG is last and treated as
# optional — sparse data means many stations will return None for it.
_FUEL_TYPES: tuple[str, ...] = ("diesel", "petrol", "kerosene", "cng")

# Mapping from FuelFinder fuel type string to StationData key.
# petrol maps to 'unleaded' for backwards compatibility with the sensor
# platform's existing translation strings and entity unique-id scheme.
_FUEL_TO_DATA_KEY: dict[str, str] = {
    "diesel": "diesel",
    "petrol": "unleaded",
    # kerosene and cng have no StationData key; stored as extra fields
}

# The city value used for a full national search when the station county
# is not yet known.
_NATIONAL_CITY = "ireland"


class IEFuelFinderProvider(BaseProvider):
    """Fetch Irish fuel prices from fuelfinder.ie.

    The station is identified by its FuelFinder internal UUID (the ``id``
    field from the /stations API response).  This UUID is the stable
    primary key; OSM IDs can change and user-submitted stations use a
    ``user/{uuid}`` format that contains a slash.

    Usage
    -----
    The constructor accepts the station UUID as ``station_id``.  On the
    first poll the county is derived from the API response and cached
    internally so subsequent polls use the smaller county-scoped query.
    """

    COUNTRY = "IE"
    PROVIDER_KEY = "ie_fuelfinder"
    LABEL = "FuelFinder.ie"
    CONFIG_MODE = "station_id"
    STATION_PAGE_URL: ClassVar[str] = "https://www.fuelfinder.ie"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "diesel",
            "kerosene",
            "cng",
            "unleaded",  # petrol maps to unleaded
            # Station identity
            "name",
            "brand",
            "address",
            "county",
            "latitude",
            "longitude",
            "phone",
            "website",
            # Timing
            "lastupdated",
            "opening_hours",
            # FuelFinder-specific
            "price_confidence",
            "has_price",
        }
    )

    STATION_ID_HINT = (
        "Enter the FuelFinder station UUID.  You can find it in the station "
        "page URL at fuelfinder.ie/fuelfinder/station/{slug} — inspect the "
        "page source for the 'id' field, or use the /api/fuelfinder/stations"
        "?city=ireland&fuel=diesel endpoint to look up your station."
    )

    STATION_LOOKUP_MODE = "county_search"

    POLL_INTERVAL_SECONDS = 1800  # 30 minutes; /init is cached at s-maxage=300

    def __init__(self, station_id: str, county: str | None = None) -> None:
        """Initialise the provider with the station UUID.

        Args:
            station_id: FuelFinder internal UUID for the target station
                        (e.g. ``7ec0dd4f-4322-4b4f-9de1-c8894a684626``).
            county:     Lowercase county name pre-seeded from config entry data
                        (e.g. ``'dublin'``).  When supplied, the first poll uses
                        this county-scoped query instead of a national search.
        """
        self._station_id = station_id
        # Cached county name (lowercase, as required by the /stations API).
        # Pre-seeded from config entry data when available; otherwise populated
        # after the first successful fetch.
        self._cached_county: str | None = county.lower() if county else None
        # Slug cache populated by async_list_stations; maps station UUID → slug.
        self._slug_cache: dict[str, str] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data.

        Makes one GET request per fuel type to /api/fuelfinder/stations,
        merging the results into a single StationData dict.  The first
        request that returns a matching station record also populates all
        non-price fields (name, county, lat/lng, etc.).

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: FuelFinder station UUID (same value as passed to
                        ``__init__``; included here for BaseProvider compat).

        Returns:
            StationData dict with all CAPABILITIES keys populated (may be
            None when the API has no data for a field).

        Raises:
            ProviderError: Station UUID not found in any API response after
                           fetching both the national and county-scoped lists.
        """
        # Determine search scope.  Use cached county when available to keep
        # the response payload small; fall back to national on first run.
        city = self._cached_county or _NATIONAL_CITY

        # Fan out: one request per fuel type.  Use gather so the four
        # requests run concurrently (they hit different cache keys on
        # Vercel's CDN).  The /stations endpoint is no-store so these all
        # hit origin, but four parallel requests on a 30-min interval is
        # well within the observed rate-limit threshold.
        tasks = [
            self._fetch_stations(session, city=city, fuel=fuel) for fuel in _FUEL_TYPES
        ]
        results: list[list[dict] | None] = list(await asyncio.gather(*tasks))

        # If the cached county returned no match (station moved county or
        # cache went stale), retry with a national search.
        found_in: dict[str, dict] = {}  # fuel_type -> station record
        station_meta: dict | None = None

        for fuel, stations in zip(_FUEL_TYPES, results):
            if not stations:
                continue
            record = _find_station(stations, station_id)
            if record is not None:
                found_in[fuel] = record
                if station_meta is None:
                    station_meta = record

        if station_meta is None and city != _NATIONAL_CITY:
            # County cache is stale — retry nationally and refresh cache.
            _LOGGER.debug(
                "Station %s not found in county '%s'; retrying with national search",
                station_id,
                city,
            )
            retry_tasks = [
                self._fetch_stations(session, city=_NATIONAL_CITY, fuel=fuel)
                for fuel in _FUEL_TYPES
            ]
            retry_results: list[list[dict] | None] = list(
                await asyncio.gather(*retry_tasks)
            )
            for fuel, stations in zip(_FUEL_TYPES, retry_results):
                if not stations:
                    continue
                record = _find_station(stations, station_id)
                if record is not None:
                    found_in[fuel] = record
                    if station_meta is None:
                        station_meta = record

        if station_meta is None:
            raise ProviderError(
                f"Station UUID '{station_id}' not found in FuelFinder.ie "
                "national station list.  Verify the UUID is correct by "
                "looking it up at fuelfinder.ie or via the /stations API."
            )

        # Cache the county for future polls (API returns title-case; convert
        # to lowercase for the ?city= query parameter).
        county_raw: str | None = station_meta.get("county")
        if county_raw:
            self._cached_county = county_raw.lower()

        return self._build_station_data(station_id, station_meta, found_in)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Makes a single national diesel search to resolve the station name.
        Returns None on any failure so the config flow can fall back to
        ``'Station {id}'``.

        Args:
            session:    aiohttp ClientSession.
            station_id: FuelFinder station UUID.
        """
        try:
            stations = await self._fetch_stations(
                session, city=_NATIONAL_CITY, fuel="diesel"
            )
            if stations:
                record = _find_station(stations, station_id)
                if record:
                    return record.get("name") or None
            # If not found in diesel list, try petrol (some stations only
            # have petrol prices submitted).
            stations_petrol = await self._fetch_stations(
                session, city=_NATIONAL_CITY, fuel="petrol"
            )
            if stations_petrol:
                record = _find_station(stations_petrol, station_id)
                if record:
                    return record.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return (station_uuid, display_label) pairs for the station picker.

        Called by the config flow county_search step.  Fetches stations for the
        supplied county for diesel AND petrol, merges results, de-dupes by UUID,
        and returns a list sorted alphabetically by label.

        Args:
            session: aiohttp ClientSession.
            county:  Lowercase county name (e.g. 'dublin').

        Returns:
            List of (uuid, "Display Name, Street (#abcd1234)") tuples sorted
            alphabetically.  Empty list on any failure.
        """
        county: str = str(kwargs.get("county", "ireland")).lower()
        try:
            # _fetch_stations catches all exceptions and returns None — no exceptions
            # will propagate, so return_exceptions is not needed here.
            diesel_resp, petrol_resp = await asyncio.gather(
                self._fetch_stations(session, city=county, fuel="diesel"),
                self._fetch_stations(session, city=county, fuel="petrol"),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed for county %s: %s", county, err)
            return []

        # Merge per-fuel results into one dict keyed by UUID.
        # Only include stations that have at least one price available.
        merged: dict[str, dict] = {}

        if isinstance(diesel_resp, list):
            for s in diesel_resp:
                uid = s.get("id")
                if uid and s.get("has_price"):
                    merged[uid] = s

        if isinstance(petrol_resp, list):
            for s in petrol_resp:
                uid = s.get("id")
                if uid and s.get("has_price") and uid not in merged:
                    merged[uid] = s

        if not merged:
            return []

        result: list[tuple[str, str]] = []
        for uid, station in merged.items():
            # Cache slug for later use (e.g. deep-linking to station page).
            self._slug_cache[uid] = station.get("slug", "")

            name = station.get("name") or "Unknown"
            brand = station.get("brand") or ""
            street = station.get("street") or ""

            # Avoid "Circle K Circle K Taney" when name already starts with brand.
            if brand and name.lower().startswith(brand.lower()):
                display_name = name.strip()
            else:
                display_name = f"{brand} {name}".strip() if brand else name

            # Build label: include street when present, always append short UUID.
            if street:
                label = f"{display_name}, {street} (#{uid[:12]})"
            else:
                label = f"{display_name} (#{uid[:12]})"

            result.append((uid, label))

        # Sort alphabetically by label (case-insensitive).
        result.sort(key=lambda x: x[1].lower())
        return result

    def get_station_page_url(self, station_id: str) -> str | None:
        """Return the FuelFinder.ie station page URL, or homepage if slug unknown."""
        slug = self._slug_cache.get(station_id)
        if not slug:
            return self.STATION_PAGE_URL or None
        # Slugs with a trailing numeric ID >= _INTERNAL_ID_MIN_LEN digits are internal
        # system IDs that have no corresponding page — fall back to homepage.
        match = _INTERNAL_ID_RE.search(slug)
        if match and len(match.group(1)) >= _INTERNAL_ID_MIN_LEN:
            _LOGGER.debug(
                "ie_fuelfinder: slug %r looks like internal ID, falling back to homepage",
                slug,
            )
            return self.STATION_PAGE_URL or None
        return f"https://www.fuelfinder.ie/fuelfinder/station/{slug}"

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_stations(
        self,
        session: ClientSession,
        city: str,
        fuel: str,
    ) -> list[dict] | None:
        """Fetch the stations list for a city/fuel combination.

        Args:
            session: aiohttp ClientSession.
            city:    Lowercase county name or ``'ireland'`` for national.
            fuel:    One of ``'diesel'``, ``'petrol'``, ``'kerosene'``,
                     ``'cng'``.

        Returns:
            List of station dicts on success, or None on HTTP/network error.
            HTTP 403 is treated as a retriable auth-header failure and logged
            at WARNING level so the bot-protection drift is visible.

        Note:
            This method deliberately returns None rather than raising on HTTP
            errors so the coordinator's stale-retention behaviour works
            correctly.  The coordinator converts UpdateFailed into a
            data_fetch_problem signal; individual fuel-type failures here are
            logged and result in None prices for that fuel type.
        """
        url = f"{_BASE_URL}/stations"
        params = {"city": city, "fuel": fuel}
        _LOGGER.debug("Fetching FuelFinder stations: city=%s fuel=%s", city, fuel)
        try:
            async with session.get(
                url,
                params=params,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status == 403:
                    _LOGGER.warning(
                        "FuelFinder.ie returned HTTP 403 for city=%s fuel=%s. "
                        "The bot-detection headers may have changed.  "
                        "Please open an issue at "
                        "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues",
                        city,
                        fuel,
                    )
                    return None
                response.raise_for_status()
                payload: dict = await response.json()
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching FuelFinder stations city=%s fuel=%s: %s",
                city,
                fuel,
                err,
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error fetching FuelFinder stations city=%s fuel=%s: %s",
                city,
                fuel,
                err,
            )
            return None

        return payload.get("stations") or []

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        station_id: str,
        meta: dict,
        prices_by_fuel: dict[str, dict],
    ) -> StationData:
        """Assemble a StationData dict from the raw API station records.

        Args:
            station_id:     The station UUID (used for source_station_id).
            meta:           The station record from whichever fuel type was
                            found first — used for all non-price fields.
            prices_by_fuel: Map of fuel_type → station record (the ``price``
                            field differs per fuel type; other fields are
                            identical across records for the same station).

        Returns:
            Populated StationData dict.  Price keys not in CAPABILITIES
            (``kerosene``, ``cng``) are included in the returned dict as
            extra fields so they travel through the coordinator.data dict
            and are accessible in extra_state_attributes on the price sensors.
        """

        def _price(fuel: str) -> float | None:
            """Extract and validate a price from the per-fuel record."""
            record = prices_by_fuel.get(fuel)
            if record is None:
                return None
            raw = record.get("price")
            if raw is None:
                return None
            try:
                val = float(raw)
            except (ValueError, TypeError):
                return None
            # FuelFinder prices are already EUR/litre (e.g. 1.838).
            # Do NOT apply the > 10 → /100 cents conversion used by the
            # fuelcompare.ie provider — it would corrupt valid prices.
            if val <= 0:
                return None
            return round(val, 3)

        # ── Non-price identity fields (from meta record) ──────────────────

        name: str | None = meta.get("name") or None
        brand: str | None = meta.get("brand") or None
        county: str | None = meta.get("county") or None
        street: str | None = meta.get("street") or None
        phone: str | None = meta.get("phone") or None
        website: str | None = meta.get("website") or None

        lat_raw = meta.get("lat")
        lng_raw = meta.get("lng")
        try:
            lat: float | None = float(lat_raw) if lat_raw is not None else None
        except (ValueError, TypeError):
            lat = None
        try:
            lng: float | None = float(lng_raw) if lng_raw is not None else None
        except (ValueError, TypeError):
            lng = None

        # ── Timing ────────────────────────────────────────────────────────

        # Take updated_at from whichever fuel record was found.
        # Use max() over all non-None timestamps so the lastupdated value reflects
        # the most recently crowd-sourced submission for this station across all fuel types.
        updated_at: str | None = None
        ts_candidates: list[str] = []
        for fuel in _FUEL_TYPES:
            record = prices_by_fuel.get(fuel)
            if record is not None:
                ts = record.get("updated_at")
                if ts:
                    ts_candidates.append(ts)
        if ts_candidates:
            updated_at = max(ts_candidates)

        # ── Confidence (freshness tier) ───────────────────────────────────

        confidence: str | None = meta.get("confidence") or None
        has_price: bool = bool(meta.get("has_price", False))

        # ── Opening hours ─────────────────────────────────────────────────

        # Store the raw OSM opening_hours string under the opening_hours key.
        # The binary_sensor platform reads opening_hours for the is_open sensor.
        opening_hours: str | None = meta.get("opening_hours") or None

        # ── Assemble dict ─────────────────────────────────────────────────

        data: StationData = {
            # Fuel prices mapped to StationData keys
            "diesel": _price("diesel"),
            "unleaded": _price(
                "petrol"
            ),  # petrol → unleaded for fuelcompare.ie sensor compat
            # Station identity
            "name": name,
            "brand": brand,
            "address": street,
            "county": county,
            "latitude": lat,
            "longitude": lng,
            "phone": phone,
            "website": website,
            # Timing
            "lastupdated": updated_at,
            "opening_hours": opening_hours,
            # Passthrough
            "source_station_id": station_id,
        }

        # ── Extra fields (not in CAPABILITIES, travel as passthrough) ─────
        # These are not declared in CAPABILITIES so no entities are created
        # for them by the sensor platform.  They are accessible from
        # coordinator.data in templates and custom extra_state_attributes.

        data["price_confidence"] = confidence
        data["has_price"] = has_price
        data["kerosene"] = _price("kerosene")
        data["cng"] = _price("cng")

        _LOGGER.debug(
            "FuelFinder parsed data for station %s: diesel=%s unleaded=%s "
            "kerosene=%s cng=%s confidence=%s updated_at=%s",
            station_id,
            data.get("diesel"),
            data.get("unleaded"),
            data.get("kerosene"),
            data.get("cng"),
            confidence,
            updated_at,
        )

        return data


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station(stations: list[dict], station_id: str) -> dict | None:
    """Return the station record matching station_id, or None.

    Searches by the ``id`` field (FuelFinder internal UUID).  Deliberately
    does NOT match on ``osm_id`` because user-submitted stations use a
    ``user/{uuid}`` format that can collide with similarly-named entries.

    Args:
        stations:   List of station dicts from the /stations API response.
        station_id: Target UUID string.

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if station.get("id") == station_id:
            return station
    return None


def _normalise_county(county: str | None) -> str | None:
    """Return the lowercase county name for use as the /stations ?city= param.

    The API accepts lowercase county names (e.g. ``'dublin'``, ``'cork'``).
    The response returns title-case (e.g. ``'Dublin'``).  This function
    converts title-case API responses back to the lowercase query form.

    Args:
        county: County name string, or None.

    Returns:
        Lowercase county string, or None if input is None or empty.
    """
    if not county:
        return None
    return county.strip().lower()
