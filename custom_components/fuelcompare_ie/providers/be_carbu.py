"""BeCarbuProvider — Belgian fuel prices via carbu.com web scraping.

Source: carbu.com/belgie — a commercial Belgian fuel price aggregator that
publishes station-level prices scraped from Belgian pump displays and
crowdsourced submissions.  No official Belgian government open-data API for
retail fuel prices exists (data.gov.be and FOD Economie have no relevant
datasets).  The carbu.com scraping approach is the established community
standard; an actively maintained custom integration (github.com/myTselection/
Carbu_com) confirms the approach is robust.

Endpoints used
--------------
1. Location resolver (GET, JSON):
   https://carbu.com/commonFunctions/getlocation/
       controller.getlocation_JSON.php?location={postalcode}&SHRT=1
   Returns a list of location objects; each has ``location_id``, ``town``,
   ``postal_code``.  Used once to resolve a postal code to its canonical
   town name and location ID required by the station listing endpoint.

2. Station listing page (GET, HTML):
   https://carbu.com/belgie/liste-stations-service/
       {fueltype}/{town}/{postalcode}/{locationid}
   Returns an HTML page; station price cards live in ``div.station-content``
   elements.  Parsed with BeautifulSoup (html.parser, stdlib only — no lxml
   dependency).

Fuel type URL slugs
-------------------
carbu.com uses short slugs in the station listing URL path:

  StationData key   Carbu slug   Display name
  ---------------   ----------   ------------
  unleaded          E10          Super 95 (E10)
  premium_unleaded  E5           Super 98 (E5)
  diesel            D            Diesel (B7)
  diesel_b10        B10          Diesel (B10)   (extra field, not in base TypedDict)
  diesel_hvo        HVO          Diesel XTL/HVO (extra field)
  lpg               LPG          LPG / Autogas
  cng               CNG          CNG
  lng               LNG          LNG            (extra field)
  hydrogen          H2           Hydrogen       (extra field)
  electric          ELEC         Electricity    (extra field)

Bot protection
--------------
carbu.com returns HTTP 403 or a login page when the request carries a
non-browser User-Agent.  The header set below passes the check as of 2025.
IMPORTANT: carbu.com may also block requests that include an Accept-Encoding
of 'gzip, br' without a Brotli decoder — aiohttp handles decompression
transparently so this is safe.

Throttling
----------
The existing Carbu_com integration (github.com/myTselection/Carbu_com) uses
a 1-hour poll interval.  We follow that cadence: POLL_INTERVAL_SECONDS=3600.

StationData field mapping
-------------------------
HTML element          → StationData key   Notes
--------------------   ----------------   -----
station name div       → name             text content of .station-name (or similar)
price span             → <fuel key>       float EUR/litre; cleaned of currency symbols
address span           → address
brand logo alt/class   → brand
lat data attribute     → latitude
lng data attribute     → longitude

Station ID
----------
CONFIG_MODE = 'location': user supplies a postal code (or lat/lng + radius).
The config flow calls async_list_stations() which resolves the postal code to
a location ID, fetches stations, and returns a picker list.  The chosen
station is identified by a composite key: ``{fuelslug}:{internal_station_id}``.

For async_fetch the station_id passed in is the carbu.com numeric station ID
(extracted from the listing page HTML, e.g. ``data-station-id`` or the URL
of the detail anchor).

STATION_LOOKUP_MODE = 'location_search': the config flow passes
``lat``, ``lng``, ``radius_km`` kwargs OR a ``postal_code`` kwarg.

Because the carbu.com API is postal-code-centric (not coordinate-centric),
the provider derives a postal code from the nearest Belgian commune when only
lat/lng is supplied.  When a postal_code kwarg is present it is used directly.
"""

from __future__ import annotations

import asyncio

import logging
import re
from typing import Any

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Carbu.com endpoints
# ---------------------------------------------------------------------------

_BASE_URL = "https://carbu.com"
_LOCATION_URL = (
    "https://carbu.com/commonFunctions/getlocation/controller.getlocation_JSON.php"
)
_STATION_LISTING_URL = (
    "https://carbu.com/belgie/liste-stations-service"
    "/{fueltype}/{town}/{postalcode}/{locationid}"
)

# ---------------------------------------------------------------------------
# Request headers
# ---------------------------------------------------------------------------
# carbu.com returns 403 for non-browser User-Agent strings.  The header set
# below mimics a modern browser and passes the bot check as of 2025.
_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-BE,fr;q=0.9,nl;q=0.8,en;q=0.7",
    "Referer": "https://carbu.com/belgie/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}

_JSON_HEADERS: dict[str, str] = {
    **_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)  # scraping can be slow

# ---------------------------------------------------------------------------
# Fuel type mapping
# ---------------------------------------------------------------------------
# Maps StationData keys to carbu.com URL slugs.
# Slug casing must match what carbu.com expects in the URL path.

_FUEL_KEY_TO_SLUG: dict[str, str] = {
    "unleaded": "E10",  # Super 95 (E10)
    "premium_unleaded": "E5",  # Super 98 (E5)
    "diesel": "GO",  # Diesel (Gasoil) — carbu.com uses "GO" not "D"
    "lpg": "LPG",  # LPG / Autogas
    "cng": "CNG",  # Compressed Natural Gas
}

# Extra fuel slugs not in the StationData TypedDict — stored as pass-through keys.
_EXTRA_SLUG_TO_KEY: dict[str, str] = {
    "B10": "diesel_b10",  # Diesel B10
    "HVO": "diesel_hvo",  # Diesel XTL/HVO
    "LNG": "lng_fuel",  # Liquefied Natural Gas (renamed from "lng" to avoid confusion with longitude)
    "H2": "hydrogen",  # Hydrogen
    "ELEC": "electric",  # Electric charging
}

# All slugs we ever fan-out to during a station listing
_ALL_SLUGS: tuple[str, ...] = (
    "E10",
    "E5",
    "GO",
    "LPG",
    "CNG",
    "B10",
    "HVO",
)

# Reverse lookup: slug → StationData key (for the keys that have one)
_SLUG_TO_FUEL_KEY: dict[str, str] = {v: k for k, v in _FUEL_KEY_TO_SLUG.items()}
_SLUG_TO_FUEL_KEY.update(_EXTRA_SLUG_TO_KEY)

# Primary fan-out slugs for async_list_stations display (cheaper + faster)
_DISPLAY_SLUGS: tuple[str, ...] = ("GO", "E10")


# ---------------------------------------------------------------------------
# HTML parsing helpers (no external deps — uses stdlib re only)
# ---------------------------------------------------------------------------


def _extract_price_from_text(text: str) -> float | None:
    """Extract a fuel price (EUR/litre) from a text string.

    Handles formats like '1,999', '1.999', '€ 1.999', '199,9 c/l'.
    Returns None on any parse failure or if the value is out of a
    plausible Belgian fuel price range (0.3 – 5.0 EUR/litre).
    """
    if not text:
        return None
    # Strip currency symbols and whitespace
    cleaned = text.replace("€", "").replace("c/l", "").strip()
    # Belgian sites often use comma as decimal separator
    cleaned = cleaned.replace(",", ".")
    # Take the first numeric token
    match = re.search(r"\d+\.\d+", cleaned)
    if match is None:
        # Try integer-only (unlikely for fuel prices but defensive)
        match = re.search(r"\d+", cleaned)
        if match is None:
            return None
        try:
            val = float(match.group())
        except ValueError:
            return None
    else:
        try:
            val = float(match.group())
        except ValueError:
            return None

    # Guard: carbu.com sometimes returns prices in cents (e.g. 199.9)
    # Apply the > 10 → / 100 normalisation to get EUR/litre
    if val > 10:
        val = val / 100.0

    if not (0.3 <= val <= 5.0):
        return None
    return round(val, 3)


def _parse_station_html(html: str, fuel_key: str) -> list[dict[str, Any]]:
    """Parse the carbu.com station listing HTML.

    carbu.com encodes station data as:
      <div id="item_N" data-price="..." data-name="..." data-address="..."
           data-lat="..." data-lng="..." data-id="...">

    Falls back to the legacy data-id tag approach for backward compatibility.

    Returns a list of station dicts with keys:
        station_id, name, brand, address, latitude, longitude, price,
        fuel_key, postalcode
    """
    stations: list[dict[str, Any]] = []

    # Primary: modern data-* attribute approach (div id="item_N")
    item_pattern = re.compile(
        r'<div\s+id=["\']item_(\d+)["\'][^>]*>',
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(item_pattern.finditer(html))

    # Fallback: any tag with data-id attribute (legacy layout)
    if not matches:
        matches = list(
            re.compile(
                r"<[a-zA-Z][^>]*\bdata-id=[\"'](\d+)[\"'][^>]*>",
                re.IGNORECASE | re.DOTALL,
            ).finditer(html)
        )

    if not matches:
        return []

    for i, match in enumerate(matches):
        station_id = match.group(1)
        block_start = match.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        # Use the opening tag for data attributes
        tag_end = html.index(">", match.start()) + 1
        tag_text = html[match.start() : tag_end]
        section = html[block_start:block_end]

        def _attr(name: str, text: str = tag_text) -> str | None:
            m = re.search(rf'data-{name}=["\']([^"\']*)["\']', text, re.IGNORECASE)
            return m.group(1).strip() if m else None

        # Extract from data attributes first (modern layout)
        price_raw = _attr("price") or _attr("prix")
        name_raw = _attr("name") or _attr("nom")
        address_raw = _attr("address") or _attr("adresse")
        lat_raw = _attr("lat")
        lng_raw = _attr("lng") or _attr("lon")
        id_raw = _attr("id") or station_id

        # Price fallback: search block for class="prix" etc.
        if not price_raw:
            pm = re.search(
                r'class="[^"]*prix[^"]*"[^>]*>([^<]+)<', section, re.IGNORECASE
            )
            price_raw = pm.group(1).strip() if pm else None

        # Name fallback: search block for class="station-name"
        if not name_raw:
            nm = re.search(
                r'class="[^"]*(?:station-name|nom)[^"]*"[^>]*>([^<]+)<',
                section,
                re.IGNORECASE,
            )
            name_raw = nm.group(1).strip() if nm else None

        # Address fallback
        if not address_raw:
            am = re.search(
                r'class="[^"]*(?:adresse|address)[^"]*"[^>]*>([^<]+)<',
                section,
                re.IGNORECASE,
            )
            address_raw = am.group(1).strip() if am else None

        try:
            lat: float | None = float(lat_raw) if lat_raw else None
        except (ValueError, TypeError):
            lat = None
        try:
            lng: float | None = float(lng_raw) if lng_raw else None
        except (ValueError, TypeError):
            lng = None

        price: float | None = _extract_price_from_text(price_raw) if price_raw else None
        name: str | None = (
            re.sub(r"&[a-zA-Z]+;", " ", name_raw).strip() if name_raw else None
        )
        address: str | None = (
            re.sub(r"&[a-zA-Z]+;", " ", address_raw).strip() if address_raw else None
        )

        postal_match = re.search(
            r'data-(?:cp|postalcode|postal)["\']?\s*=\s*["\']?(\d{4})',
            section,
            re.IGNORECASE,
        )
        postalcode: str | None = postal_match.group(1) if postal_match else None

        stations.append(
            {
                "station_id": str(id_raw),
                "name": name,
                "brand": None,
                "address": address,
                "latitude": lat,
                "longitude": lng,
                "price": price,
                "fuel_key": fuel_key,
                "postalcode": postalcode,
            }
        )

    return stations


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class BeCarbuProvider(BaseProvider):
    """Fetch Belgian fuel prices by scraping carbu.com station pages.

    No official Belgian government API for retail fuel prices exists.
    carbu.com is the community-standard source for Belgium and is actively
    maintained.

    CONFIG_MODE = 'location': user enters a postal code (or lat/lng).
    The config flow calls async_list_stations() to resolve the postal code
    to a carbu.com location ID, fetches the station listing HTML, and
    presents a sorted picker.  The chosen station's numeric ID is stored
    in the config entry as station_id.

    Poll interval is 3 600 s (1 hour) matching the Carbu_com HA integration
    throttle.
    """

    COUNTRY = "BE"
    PROVIDER_KEY = "be_carbu"
    LABEL = "Carbu.com (Belgium)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600  # 1 hour — carbu.com rate-limit guidance

    CAPABILITIES: frozenset[str] = frozenset(
        {
            # Fuel prices
            "unleaded",  # Super 95 (E10)
            "premium_unleaded",  # Super 98 (E5)
            "diesel",  # Diesel (B7)
            "lpg",  # LPG / Autogas
            "cng",  # CNG
            # Station identity
            "name",
            "brand",
            "address",
            "latitude",
            "longitude",
            # Timing
            "lastupdated",
            # Coordinator sentinels
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the carbu.com numeric station ID.  You can find it by "
        "browsing https://carbu.com/belgie/ and inspecting the station "
        "card URL (e.g. '/belgie/station/12345') or using the location "
        "search in the config flow."
    )

    def __init__(
        self,
        station_id: str,
        postal_code: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  carbu.com numeric station ID.
            postal_code: Belgian 4-digit postal code for the search area.
            latitude:    WGS84 latitude for the search centre.
            longitude:   WGS84 longitude for the search centre.
            radius_km:   Search radius in km for async_list_stations.
        """
        self._station_id = station_id
        self._postal_code = postal_code
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0
        # Cache: postal_code → (town, location_id)
        self._location_cache: dict[str, tuple[str, str]] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for station_id.

        Scrapes each primary fuel type page for the station's postal code
        area, finds the matching station record by its numeric ID, and
        assembles a StationData dict.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: carbu.com numeric station ID (string).

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: Station ID not found after scanning all fuel pages,
                           or location resolution failed.
        """
        postal_code = self._postal_code
        if not postal_code:
            raise ProviderError(
                "BeCarbuProvider requires a postal_code to fetch station data. "
                "Configure a postal code in the integration options."
            )

        # Resolve postal code to town + location_id
        try:
            town, location_id = await self._resolve_location(session, postal_code)
        except ProviderError:
            raise
        except Exception as err:  # noqa: BLE001
            raise ProviderError(
                f"Failed to resolve Belgian postal code '{postal_code}': {err}"
            ) from err

        # Fan out across primary fuel types to find the station and collect prices
        prices: dict[str, float | None] = {}
        station_meta: dict[str, Any] | None = None

        for slug in _ALL_SLUGS:
            fuel_key = _SLUG_TO_FUEL_KEY.get(slug, slug.lower())
            try:
                stations = await self._fetch_station_listing(
                    session, slug, town, postal_code, location_id
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "BeCarbu: error fetching slug=%s for station %s: %s",
                    slug,
                    station_id,
                    err,
                )
                continue

            for s in stations:
                if s.get("station_id") == station_id:
                    prices[fuel_key] = s.get("price")
                    if station_meta is None:
                        station_meta = s
                    break

        if station_meta is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in carbu.com listings "
                f"for postal code '{postal_code}'. Verify the station ID and "
                "postal code are correct."
            )

        return self._build_station_data(station_id, station_meta, prices)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Fetches the diesel listing for the configured postal code and
        returns the name of the matching station.  Falls back to None
        on any failure.

        Args:
            session:    aiohttp ClientSession.
            station_id: carbu.com numeric station ID.
        """
        postal_code = self._postal_code
        if not postal_code:
            return None
        try:
            town, location_id = await self._resolve_location(session, postal_code)
            stations = await self._fetch_station_listing(
                session, _FUEL_KEY_TO_SLUG["diesel"], town, postal_code, location_id
            )
            for s in stations:
                if s.get("station_id") == station_id:
                    return s.get("name") or None
            # Not found in diesel; try unleaded
            stations_e10 = await self._fetch_station_listing(
                session, "E10", town, postal_code, location_id
            )
            for s in stations_e10:
                if s.get("station_id") == station_id:
                    return s.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "BeCarbu: failed to fetch station name for %s: %s", station_id, err
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the config flow picker.

        Called by the config flow location_search step.  Resolves the postal
        code to a carbu.com location, fetches diesel and E10 listings, merges
        them by station ID, and returns a list sorted cheapest-diesel-first.

        Accepts kwargs:
            postal_code: Belgian 4-digit postal code (preferred).
            lat:         WGS84 latitude — used to pick nearest postal code if
                         no postal_code kwarg is provided (not yet implemented;
                         returns empty list when only lat/lng is supplied
                         without a postal_code).
            lng:         WGS84 longitude (see lat note).
            radius_km:   Search radius (used only when lat/lng supplied).

        Returns:
            Ordered list of (station_id, label) tuples.  Empty list on failure.
        """
        # postal_code kwarg takes priority; fall back to county kwarg (config flow
        # passes the county field as "county" — for BE this should be a 4-digit
        # Belgian postal code); then fall back to instance postal_code.
        postal_code: str | None = (
            str(kwargs["postal_code"])
            if kwargs.get("postal_code")
            else (
                str(kwargs["county"])
                if kwargs.get("county") and str(kwargs.get("county", "")).isdigit()
                else self._postal_code
            )
        )
        lat: float | None = kwargs.get("lat")  # type: ignore[assignment]
        lng: float | None = kwargs.get("lng")  # type: ignore[assignment]

        if not postal_code:
            if lat is not None and lng is not None:
                # We don't have a reverse-geocoder; log and return empty.
                _LOGGER.debug(
                    "BeCarbu: async_list_stations received lat/lng but no postal_code; "
                    "cannot resolve Belgian postal code from coordinates alone"
                )
            return []

        try:
            town, location_id = await self._resolve_location(session, postal_code)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "BeCarbu: async_list_stations failed to resolve postal code %s: %s",
                postal_code,
                err,
            )
            return []

        # Fetch diesel and E10 listings concurrently
        try:
            diesel_result, e10_result = await asyncio.gather(
                self._fetch_station_listing(
                    session, _FUEL_KEY_TO_SLUG["diesel"], town, postal_code, location_id
                ),
                self._fetch_station_listing(
                    session, "E10", town, postal_code, location_id
                ),
                return_exceptions=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("BeCarbu: async_list_stations gather error: %s", err)
            return []

        # Merge results by station_id
        merged: dict[str, dict[str, Any]] = {}
        diesel_prices: dict[str, float | None] = {}
        e10_prices: dict[str, float | None] = {}

        if isinstance(diesel_result, list):
            for s in diesel_result:
                sid = s.get("station_id")
                if sid:
                    merged[sid] = s
                    diesel_prices[sid] = s.get("price")

        if isinstance(e10_result, list):
            for s in e10_result:
                sid = s.get("station_id")
                if sid:
                    if sid not in merged:
                        merged[sid] = s
                    e10_prices[sid] = s.get("price")

        if not merged:
            return []

        # Filter by radius if lat/lng supplied
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)
        if lat is not None and lng is not None:
            filtered: dict[str, dict[str, Any]] = {}
            for sid, station in merged.items():
                slat = station.get("latitude")
                slng = station.get("longitude")
                if slat is not None and slng is not None:
                    dist = haversine_km(lat, lng, slat, slng)
                    if dist <= radius_km:
                        filtered[sid] = station
                else:
                    filtered[sid] = station  # include if no coords
            merged = filtered

        if not merged:
            return []

        result: list[tuple[str, str, float]] = []
        for sid, station in merged.items():
            name = station.get("name") or "Unknown"
            brand = station.get("brand") or ""
            display_name = f"{brand} {name}".strip() if brand else name

            d_price = diesel_prices.get(sid)
            e_price = e10_prices.get(sid)

            price_parts: list[str] = []
            if d_price is not None:
                price_parts.append(f"Diesel €{d_price:.3f}")
            if e_price is not None:
                price_parts.append(f"E10 €{e_price:.3f}")

            if price_parts:
                label = f"{display_name} — {' / '.join(price_parts)}"
                sort_key = min(p for p in (d_price, e_price) if p is not None)
            else:
                label = display_name
                sort_key = 9999.0

            result.append((sid, label, sort_key))

        result.sort(key=lambda x: x[2])
        return [(sid, label) for sid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _resolve_location(
        self,
        session: ClientSession,
        postal_code: str,
    ) -> tuple[str, str]:
        """Resolve a Belgian postal code to (town, location_id) via carbu.com.

        Results are cached in ``self._location_cache`` so repeated calls
        within a poll cycle do not hit the network twice.

        Args:
            session:     aiohttp ClientSession.
            postal_code: 4-digit Belgian postal code string.

        Returns:
            (town_slug, location_id) tuple suitable for the listing URL.

        Raises:
            ProviderError: The postal code could not be resolved.
        """
        if postal_code in self._location_cache:
            return self._location_cache[postal_code]

        params = {"location": postal_code, "SHRT": "1"}
        _LOGGER.debug("BeCarbu: resolving location for postal code %s", postal_code)

        try:
            async with session.get(
                _LOCATION_URL,
                params=params,
                headers=_JSON_HEADERS,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 403:
                    raise ProviderError(
                        f"carbu.com returned HTTP 403 for location lookup of "
                        f"postal code '{postal_code}'.  The bot-detection headers "
                        "may have changed.  Please open an issue at "
                        "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues"
                    )
                resp.raise_for_status()
                payload: list[dict[str, Any]] = await resp.json(content_type=None)
        except ProviderError:
            raise
        except ClientResponseError as err:
            raise ProviderError(
                f"HTTP error resolving postal code '{postal_code}': {err}"
            ) from err
        except Exception as err:  # noqa: BLE001
            raise ProviderError(
                f"Unexpected error resolving postal code '{postal_code}': {err}"
            ) from err

        if not payload:
            raise ProviderError(
                f"carbu.com returned empty location list for postal code "
                f"'{postal_code}'.  Check the postal code is valid."
            )

        # Take the first result.  Prefer exact postal code match.
        # API returns abbreviated keys: n=name, id=location_id, pc=postal_code
        entry = payload[0]
        for item in payload:
            pc = str(item.get("pc") or item.get("postal_code", ""))
            if pc == str(postal_code):
                entry = item
                break

        town: str = str(
            entry.get("n") or entry.get("town") or entry.get("name") or postal_code
        )
        location_id: str = str(
            entry.get("id") or entry.get("location_id") or postal_code
        )

        # Normalise town for URL: lowercase, spaces to hyphens, strip accents
        town_slug = _normalise_town(town)

        self._location_cache[postal_code] = (town_slug, location_id)
        _LOGGER.debug(
            "BeCarbu: resolved %s → town=%s location_id=%s",
            postal_code,
            town_slug,
            location_id,
        )
        return town_slug, location_id

    async def _fetch_station_listing(
        self,
        session: ClientSession,
        fuel_slug: str,
        town: str,
        postal_code: str,
        location_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch and parse the carbu.com station listing page for one fuel type.

        Args:
            session:     aiohttp ClientSession.
            fuel_slug:   carbu.com fuel type slug (e.g. 'D', 'E10').
            town:        URL-safe town name (lowercase, hyphens).
            postal_code: Belgian postal code string.
            location_id: carbu.com location identifier.

        Returns:
            List of station dicts (may be empty on parse failure or no results).
            Returns an empty list rather than raising on HTTP/network errors.
        """
        url = _STATION_LISTING_URL.format(
            fueltype=fuel_slug,
            town=town,
            postalcode=postal_code,
            locationid=location_id,
        )
        fuel_key = _SLUG_TO_FUEL_KEY.get(fuel_slug, fuel_slug.lower())
        _LOGGER.debug("BeCarbu: fetching listing slug=%s url=%s", fuel_slug, url)

        try:
            async with session.get(
                url,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 403:
                    _LOGGER.warning(
                        "BeCarbu: carbu.com returned HTTP 403 for slug=%s. "
                        "Bot-detection headers may have changed.",
                        fuel_slug,
                    )
                    return []
                if resp.status == 404:
                    _LOGGER.debug(
                        "BeCarbu: no listing page for slug=%s (404)", fuel_slug
                    )
                    return []
                resp.raise_for_status()
                html: str = await resp.text()
        except ClientResponseError as err:
            _LOGGER.debug("BeCarbu: HTTP error fetching slug=%s: %s", fuel_slug, err)
            return []
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "BeCarbu: unexpected error fetching slug=%s: %s", fuel_slug, err
            )
            return []

        stations = _parse_station_html(html, fuel_key)
        _LOGGER.debug(
            "BeCarbu: parsed %d stations for slug=%s", len(stations), fuel_slug
        )
        return stations

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        station_id: str,
        meta: dict[str, Any],
        prices: dict[str, float | None],
    ) -> StationData:
        """Assemble a StationData dict from the parsed station meta and prices.

        Args:
            station_id: carbu.com numeric station ID.
            meta:       Station metadata dict from the first matching listing.
            prices:     Map of fuel_key → price (EUR/litre) gathered from all
                        fuel-type pages.

        Returns:
            Populated StationData dict.
        """
        lat_raw = meta.get("latitude")
        lng_raw = meta.get("longitude")
        try:
            lat: float | None = float(lat_raw) if lat_raw is not None else None
        except (ValueError, TypeError):
            lat = None
        try:
            lng: float | None = float(lng_raw) if lng_raw is not None else None
        except (ValueError, TypeError):
            lng = None

        name: str | None = meta.get("name") or None
        brand: str | None = meta.get("brand") or None
        address: str | None = meta.get("address") or None

        data: StationData = {
            "unleaded": prices.get("unleaded"),
            "premium_unleaded": prices.get("premium_unleaded"),
            "diesel": prices.get("diesel"),
            "lpg": prices.get("lpg"),
            "cng": prices.get("cng"),
            "name": name,
            "brand": brand,
            "tablename": brand,
            "address": address,
            "latitude": lat,
            "longitude": lng,
            "lastupdated": None,  # carbu.com does not expose per-station timestamps
            "source_station_id": station_id,
        }

        _LOGGER.debug(
            "BeCarbu: assembled data for station %s: diesel=%s unleaded=%s "
            "premium_unleaded=%s lpg=%s cng=%s",
            station_id,
            data.get("diesel"),
            data.get("unleaded"),
            data.get("premium_unleaded"),
            data.get("lpg"),
            data.get("cng"),
        )
        return data


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _normalise_town(town: str) -> str:
    """Normalise a Belgian town name for use in a carbu.com URL path segment.

    Converts to lowercase, replaces spaces and special characters with
    hyphens, and strips leading/trailing hyphens.

    Args:
        town: Raw town name string from the location JSON (e.g. 'Sint-Niklaas').

    Returns:
        URL-safe slug (e.g. 'sint-niklaas').
    """
    # Normalise common accented characters
    replacements = {
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "ä": "a",
        "î": "i",
        "ï": "i",
        "ô": "o",
        "ö": "o",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c",
        "É": "E",
        "È": "E",
        "Ê": "E",
        "À": "A",
        "Â": "A",
        "Î": "I",
        "Ï": "I",
        "Ô": "O",
        "Ù": "U",
        "Û": "U",
        "Ç": "C",
    }
    result = town
    for accented, plain in replacements.items():
        result = result.replace(accented, plain)

    result = result.lower()
    # Replace any non-alphanumeric character (except hyphens) with a hyphen
    result = re.sub(r"[^a-z0-9\-]", "-", result)
    # Collapse consecutive hyphens
    result = re.sub(r"-{2,}", "-", result)
    return result.strip("-")
