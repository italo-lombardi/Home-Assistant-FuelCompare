"""SiGorivaProvider — Slovenian fuel prices from goriva.si.

Source: goriva.si (community/commercial fuel price tracker, Slovenia).
Endpoint: GET https://goriva.si/api/v1/search/?page={n}
No authentication required.

Pagination: 25 stations per page, ~23 pages total (~551 stations).
The top-level ``count`` field gives the exact total station count.
Each page returns ``next`` (URL or null), ``previous`` (URL or null),
and ``results`` (list of station dicts).

Server-side cache: Cache-Control max-age=300 (5 minutes).  Recommended
poll interval: 3600 seconds (1 hour).

Station object fields (all inside the ``results`` array):
  pk           (int)   — station primary key / unique ID
  franchise    (int)   — FK to brand; resolved via separate /franchise/ call
  name         (str)   — station display name
  address      (str)   — street address
  lat          (float) — latitude
  lng          (float) — longitude
  zip_code     (str)   — postal code (string, e.g. "1290")
  open_hours   (str)   — free-text opening hours (mixed \\r\\n / \\n newlines)
  prices       (dict)  — all 10 fuel keys always present, value float or null

Fuel key mapping (Slovenian field names → StationData keys):
  "95"           → unleaded         (Eurosuper 95)
  "dizel"        → diesel           (NOTE: Slovenian spelling, not "diesel")
  "98"           → premium_unleaded (Eurosuper 98)
  "avtoplin-lpg" → lpg              (autogas / LPG)

Brand resolution: GET https://goriva.si/api/v1/franchise/ returns all
brands in a single non-paginated response: [{pk, name, marker, ...}].
This is fetched once per async_list_stations call and optionally cached
within a single coordinator run via async_fetch.

This is an undocumented private API with no SLA.  Field names or
pagination may change without notice.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

_SEARCH_URL = "https://goriva.si/api/v1/search/"
_FRANCHISE_URL = "https://goriva.si/api/v1/franchise/"

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 2)

MAX_PAGES = 100

# Franchise (brand) cache TTL — matches station cache
_FRANCHISE_CACHE_TTL = 3600

# Mapping from goriva.si price dict keys to StationData keys.
# IMPORTANT: Diesel is "dizel" (Slovenian), not "diesel".
_PRICE_KEY_MAP: dict[str, str] = {
    "95": "unleaded",
    "dizel": "diesel",
    "98": "premium_unleaded",
    "avtoplin-lpg": "lpg",
}


class SiGorivaProvider(BaseProvider):
    """Fetch Slovenian fuel prices from the goriva.si API.

    CONFIG_MODE is 'location' — the user supplies lat/lng + radius and
    the integration tracks all stations found within that radius.
    Station IDs are the integer ``pk`` values returned by the API,
    stored as strings.

    Usage
    -----
    Constructor takes lat/lng/radius from the config entry.  async_fetch
    fetches the full paginated dataset on each poll, finds the matching
    station by pk, and returns its normalised prices.  async_list_stations
    fetches all pages, filters by distance from the supplied coordinates,
    and returns a list sorted cheapest-first by diesel price.
    """

    COUNTRY = "SI"
    PROVIDER_KEY = "si_goriva"
    LABEL = "goriva.si (Slovenia)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600  # server cache is 300 s; 1-hour poll is sufficient

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "diesel",
            "unleaded",
            "premium_unleaded",
            "lpg",
            "name",
            "brand",
            "county",
            "address",
            "latitude",
            "longitude",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  goriva.si station pk as a string (e.g. "2308").
            county:      Not used by this provider; stored for interface compat.
            latitude:    WGS84 latitude of the tracked location.
            longitude:   WGS84 longitude of the tracked location.
            radius_km:   Search radius in kilometres (used for station listing).
        """
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

        # In-memory cache for franchise (brand) names, populated on first use.
        # Keyed by franchise pk (int) → brand name (str).
        self._franchise_cache: dict[int, str] = {}
        self._franchise_cache_ts: float = 0

        # In-memory cache for the full station list with TTL (seconds).
        self._station_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch the full paginated dataset and return data for one station.

        The goriva.si API has no single-station lookup that returns prices
        (the /search/{pk}/ detail endpoint exists but omits prices in some
        responses).  The full paginated list is small enough (~551 stations,
        ~23 pages) that a full scan is acceptable at 1-hour intervals.

        For CONFIG_MODE='location' this method is called by the coordinator
        with the station_id stored in the config entry.

        Args:
            session:    aiohttp ClientSession.
            station_id: goriva.si station pk as a string.

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: Station not found in the full dataset.
        """
        target_pk: int
        try:
            target_pk = int(station_id)
        except (ValueError, TypeError):
            raise ProviderError(
                f"Invalid goriva.si station ID '{station_id}': must be an integer pk."
            )
        franchise_map = await self._ensure_franchise_cache(session)
        all_stations = await self._get_all_stations_cached(session)
        station = next((s for s in all_stations if s.get("pk") == target_pk), None)
        if station is None:
            raise ProviderError(
                f"Station pk '{station_id}' not found in goriva.si dataset. "
                "Verify the station ID is correct."
            )
        return _parse_station(station, franchise_map)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        For CONFIG_MODE='location' providers the config flow uses a
        generated title, so this always returns None.

        Args:
            session:    aiohttp ClientSession.
            station_id: goriva.si station pk as a string.
        """
        # Location-mode providers do not need a station name for the config flow.
        # Return None so the flow uses the auto-generated "Country (lat, lon)" title.
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the location-based picker.

        Fetches all goriva.si stations, optionally filters by distance from
        the supplied coordinates, and returns a list sorted cheapest-first by
        diesel price (stations with no diesel price are sorted last, then by
        name).

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the search (float).
            lng:       Centre longitude for the search (float).
            radius_km: Search radius in kilometres (float, default 10).

        Returns:
            List of ("pk_string", "Name — Brand — Diesel €x.xxx") tuples,
            or an empty list on any failure.
        """
        lat: float | None = (
            kwargs["lat"] if kwargs.get("lat") is not None else self._latitude
        )
        lng: float | None = (
            kwargs["lng"] if kwargs.get("lng") is not None else self._longitude
        )
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        try:
            franchise_map = await self._ensure_franchise_cache(session)
            all_stations = await self._get_all_stations_cached(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        result: list[tuple[str, str, float]] = []

        for station in all_stations:
            pk = station.get("pk")
            if pk is None:
                continue
            sid = str(pk)

            # Distance filter when coordinates are provided.
            if lat is not None and lng is not None:
                s_lat = station.get("lat")
                s_lng = station.get("lng")
                if s_lat is not None and s_lng is not None:
                    try:
                        dist = _haversine_km(lat, lng, float(s_lat), float(s_lng))
                    except (ValueError, TypeError):
                        dist = None
                    if dist is None or dist > radius_km:
                        continue
                else:
                    continue

            franchise_pk = station.get("franchise")
            brand = franchise_map.get(franchise_pk, "") if franchise_pk else ""
            name = station.get("name") or "Unknown"
            address = station.get("address") or ""

            display_name = name
            if brand and brand.lower() not in name.lower():
                display_name = f"{brand} — {name}"
            if address:
                display_name = f"{display_name} ({address})"

            prices = station.get("prices") or {}
            diesel_val = _parse_price(prices.get("dizel"))

            price_parts: list[str] = []
            if diesel_val is not None:
                price_parts.append(f"Diesel €{diesel_val:.3f}")
            unleaded_val = _parse_price(prices.get("95"))
            if unleaded_val is not None:
                price_parts.append(f"95 €{unleaded_val:.3f}")

            label = (
                f"{display_name} — {' / '.join(price_parts)}"
                if price_parts
                else display_name
            )

            sort_key = diesel_val if diesel_val is not None else 9999.0
            result.append((sid, label, sort_key))

        result.sort(key=lambda x: (x[2], x[1]))
        return [(sid, label) for sid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    _STATION_CACHE_TTL = 3600  # seconds

    async def _get_all_stations_cached(
        self, session: ClientSession
    ) -> list[dict[str, Any]]:
        """Return all stations, using the instance cache when still fresh.

        The cache TTL matches POLL_INTERVAL_SECONDS (3600 s).  A full
        re-fetch (23 HTTP requests) happens at most once per hour.

        Returns:
            Flat list of all station dicts.
        """
        now = time.monotonic()
        if (
            self._station_cache is not None
            and (now - self._cache_timestamp) < self._STATION_CACHE_TTL
        ):
            _LOGGER.debug(
                "goriva.si: using cached station list (%d stations)",
                len(self._station_cache),
            )
            return self._station_cache

        stations, complete = await self._fetch_all_stations(session)
        if complete:
            self._station_cache = stations
            self._cache_timestamp = now
        return stations

    async def _fetch_all_stations(
        self, session: ClientSession
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch all paginated stations from the goriva.si search endpoint.

        Iterates pages starting at 1 until ``next`` is null or an HTTP 404
        is returned (which the API sends for out-of-range page numbers).

        Returns:
            Tuple of (stations, complete) where complete=True only when all
            pages were successfully fetched (safe to cache).
        """
        stations: list[dict[str, Any]] = []
        page = 1
        complete = False

        while True:
            params: dict[str, Any] = {"page": page}
            _LOGGER.debug("Fetching goriva.si page %d", page)
            try:
                async with session.get(
                    _SEARCH_URL,
                    params=params,
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status == 404:
                        # Past the last page — normal termination.
                        complete = True
                        break
                    resp.raise_for_status()
                    payload: dict[str, Any] = await resp.json()
            except Exception as err:
                if page == 1:
                    # Cannot fetch even the first page — propagate so the
                    # coordinator marks this as a data_fetch_problem.
                    raise
                _LOGGER.warning(
                    "goriva.si: error fetching page %d (stopping pagination): %s",
                    page,
                    err,
                )
                break

            page_results: list[dict[str, Any]] = payload.get("results") or []
            stations.extend(page_results)

            if not payload.get("next"):
                # Last page reached.
                complete = True
                break

            page += 1
            if page > MAX_PAGES:
                _LOGGER.error(
                    "SI goriva: exceeded %d page limit, pagination may be infinite",
                    MAX_PAGES,
                )
                break

        _LOGGER.debug(
            "goriva.si: fetched %d stations total (complete=%s)",
            len(stations),
            complete,
        )
        return stations, complete

    async def _ensure_franchise_cache(self, session: ClientSession) -> dict[int, str]:
        """Fetch and return the franchise (brand) map, using in-memory cache.

        The franchise endpoint returns all ~31 brands in one non-paginated
        response.  The result is cached on the instance for the lifetime of
        the coordinator's session.

        Returns:
            Dict mapping franchise pk (int) → brand name (str).
            Returns an empty dict on failure (brand will be absent from data).
        """
        now = time.monotonic()
        if (
            self._franchise_cache_ts > 0
            and (now - self._franchise_cache_ts) < _FRANCHISE_CACHE_TTL
        ):
            return self._franchise_cache

        try:
            async with session.get(
                _FRANCHISE_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                franchises: list[dict[str, Any]] = await resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "goriva.si: failed to fetch franchise list (brand names will be absent): %s",
                err,
            )
            # Stamp the timestamp even on failure so we don't retry on every poll.
            self._franchise_cache_ts = time.monotonic()
            return {}

        cache: dict[int, str] = {}
        for item in franchises:
            pk = item.get("pk")
            name = item.get("name")
            if pk is not None and name:
                cache[int(pk)] = str(name)

        self._franchise_cache = cache
        self._franchise_cache_ts = time.monotonic()
        _LOGGER.debug("goriva.si: loaded %d franchise entries", len(cache))
        return cache


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_price(raw: Any) -> float | None:
    """Parse a raw price value from the goriva.si prices dict.

    Prices are already in EUR/litre (e.g. 1.465).  Returns None for null,
    zero, or non-numeric values.  Applies the standard >10 → /100 guard
    in case the API ever switches to cents (currently not observed).

    Args:
        raw: Raw price value from the API (float, int, None, or string).

    Returns:
        Normalised price as EUR/litre, or None.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    # Normalise cents to EUR/litre (guard for unexpected unit change)
    if val > 10:
        val = val / 100.0
    return round(val, 3)


def _parse_station(
    station: dict[str, Any],
    franchise_map: dict[int, str],
) -> StationData:
    """Build a normalised StationData dict from a raw goriva.si station record.

    Args:
        station:       Raw station dict from the API ``results`` array.
        franchise_map: Franchise pk → brand name mapping.

    Returns:
        Populated StationData dict with all SiGorivaProvider.CAPABILITIES keys.
    """
    prices_raw: dict[str, Any] = station.get("prices") or {}

    diesel = _parse_price(prices_raw.get("dizel"))  # Slovenian spelling — critical
    unleaded = _parse_price(prices_raw.get("95"))
    premium_unleaded = _parse_price(prices_raw.get("98"))
    lpg = _parse_price(prices_raw.get("avtoplin-lpg"))

    franchise_pk = station.get("franchise")
    brand: str | None = None
    if franchise_pk is not None:
        try:
            brand = franchise_map.get(int(franchise_pk))
        except (ValueError, TypeError):
            pass

    name: str | None = station.get("name") or None
    address: str | None = station.get("address") or None
    zip_code: str | None = station.get("zip_code") or None

    # Compose a county-like value from the zip code when no explicit county
    # is available (goriva.si does not expose a county/region field directly).
    county: str | None = zip_code or None

    lat_raw = station.get("lat")
    lng_raw = station.get("lng")
    try:
        latitude: float | None = float(lat_raw) if lat_raw is not None else None
    except (ValueError, TypeError):
        latitude = None
    try:
        longitude: float | None = float(lng_raw) if lng_raw is not None else None
    except (ValueError, TypeError):
        longitude = None

    return {
        "diesel": diesel,
        "unleaded": unleaded,
        "premium_unleaded": premium_unleaded,
        "lpg": lpg,
        "name": name,
        "brand": brand,
        "address": address,
        "county": county,
        "latitude": latitude,
        "longitude": longitude,
        "lastupdated": None,  # goriva.si does not return per-station timestamps
        "source_station_id": str(station.get("pk", "")),
    }
