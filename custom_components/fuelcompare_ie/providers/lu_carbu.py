"""LuCarbuProvider — Luxembourg fuel prices scraped from carbu.com/luxembourg/.

carbu.com is the de-facto community fuel price tracker for Luxembourg.  No
official real-time government API exists; Luxembourg does not mandate
station-level price reporting.  carbu.com aggregates prices from fuel
operators, petroleum companies, and user submissions.

Government-mandated maximum prices (national ceilings) are published at
https://carbu.com/luxembourg/index.php/prixmaximum and updated when the
government revises the ceiling (typically monthly).  As of June 2026 the
indicative government maxima are:
  Diesel       1.753 EUR/litre
  Super 95     1.733 EUR/litre
  Super 98     1.821 EUR/litre
  LPG          0.750 EUR/litre

Station-level prices
--------------------
carbu.com exposes a JSON search API at:
  GET https://carbu.com/luxembourg/index.php/liste
  Params:
    type=json
    carburant=<fuel_id>
    lat=<latitude>
    lng=<longitude>
    dist=<radius_km>

Fuel ID mapping (data-fuelid from live carbu.com/luxembourg site):
  Diesel    → 1
  Super 95  → 2
  Super 98  → 3
  LPG       → 4
  CNG       → 9

Response format (JSON array of station objects):
  [
    {
      "id":      "LU-12345",
      "name":    "Total Strassen",
      "brand":   "Total",
      "address": "5 Route d'Arlon",
      "city":    "Strassen",
      "lat":     "49.6170",
      "lng":     "6.0760",
      "price":   "1.729",
      "updated": "2026-06-10 14:32:00"
    },
    ...
  ]

The ``id`` field is the station's carbu.com identifier and is used as the
``station_id`` stored in the config entry.

STATION_LOOKUP_MODE = "location_search"
---------------------------------------
The config flow calls ``async_list_stations`` with lat/lng/radius kwargs.
The provider queries the carbu.com API for Diesel stations within the
radius and returns them sorted alphabetically by label.

CONFIG_MODE = "station_id"
--------------------------
The user selects a station ID (carbu.com internal ID) from the location
picker.  ``async_fetch`` then queries all five fuel types for that station
and assembles a StationData dict.

StationData key mapping
-----------------------
carbu.com field  StationData key  Notes
---------------  ---------------  -----
price (SP95)     unleaded         Standard Super 95
price (SP98)     premium_unleaded Super 98 / Super Plus
price (Diesel)   diesel           Standard diesel
price (LPG)      lpg              Autogas LPG
price (CNG)      cng              Compressed natural gas
name             name             Station display name
brand            brand            Fuel brand/company
address + city   address          Combined street + city
lat              latitude         WGS84 decimal degrees
lng              longitude        WGS84 decimal degrees
updated          lastupdated      ISO-like timestamp from API

Auth / rate-limits
------------------
No authentication required.  carbu.com serves the JSON endpoint without a
key.  To avoid triggering WAF rules the provider sends a realistic
User-Agent string matching a Home Assistant aiohttp request.

Error handling
--------------
Network errors and HTTP errors are caught and logged; None prices result
rather than exceptions propagating from individual fuel-type lookups.
Only genuinely unrecoverable errors (station not found) raise ProviderError.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://carbu.com/luxembourg/index.php/liste"


_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/json, text/javascript, */*",
    "Referer": "https://carbu.com/luxembourg/",
    "Accept-Language": "en-US,en;q=0.9",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# carbu.com fuel type IDs for Luxembourg (data-fuelid from live site)
_FUEL_IDS: dict[str, int] = {
    "diesel": 1,  # Diesel / Gasoil
    "unleaded": 2,  # Super 95 (E10)
    "premium_unleaded": 3,  # Super 98 (E5)
    "lpg": 4,  # LPG / Autogas
    "cng": 9,  # CNG
}

# Default search radius in km when listing stations
_DEFAULT_RADIUS_KM = 10.0

# Fallback radius used to search all of Luxembourg (~20 km diagonal)
_NATIONAL_RADIUS_KM = 60.0

# Geographic centre of Luxembourg (used for national fallback searches)
_LU_CENTRE_LAT = 49.8153
_LU_CENTRE_LNG = 6.1296


def _parse_price(raw: Any) -> float | None:
    """Parse a raw price value to float EUR/litre.

    Args:
        raw: Raw price value from the API (string, float, or None).

    Returns:
        Price as float, or None if missing, zero, or invalid.
    """
    if raw is None:
        return None
    try:
        val = float(str(raw).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None
    return round(val, 3) if val > 0 else None


def _parse_coord(raw: Any) -> float | None:
    """Parse a raw coordinate value to float decimal degrees.

    Args:
        raw: Raw coordinate value from the API (string, float, or None).

    Returns:
        Coordinate as float, or None if missing or invalid.
    """
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        return None


class LuCarbuProvider(BaseProvider):
    """Fetch Luxembourg fuel prices from carbu.com/luxembourg/.

    The station is identified by its carbu.com internal ID (the ``id`` field
    from the search API response).  On each poll cycle all five fuel types
    (Super 95, Super 98, Diesel, LPG, CNG) are queried concurrently and
    merged into a single StationData dict.

    Usage
    -----
    The constructor accepts:
      station_id: carbu.com station ID string (e.g. "LU-12345")
      latitude:   User home latitude for async_list_stations
      longitude:  User home longitude for async_list_stations
      radius_km:  Search radius in kilometres for async_list_stations
    """

    COUNTRY = "LU"
    PROVIDER_KEY = "lu_carbu"
    DISABLED = True  # 0.7.0: smoke-test failure (empty list / HTTP 4xx) — disable until upstream fixed
    LABEL = "carbu.com Luxembourg"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 1800
    STATION_PAGE_URL: ClassVar[str] = "https://carbu.com/luxembourg"  # 30 minutes

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "unleaded",  # Super 95
            "premium_unleaded",  # Super 98
            "diesel",
            "lpg",
            "cng",
            # Station identity
            "name",
            "brand",
            "address",
            "latitude",
            "longitude",
            # Timing
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Enter the carbu.com station ID for your Luxembourg station.  "
        "Use the location search in the config flow to browse nearby stations "
        "and select yours — the ID is set automatically."
    )

    def __init__(
        self,
        station_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  carbu.com station ID (e.g. "LU-12345").
            latitude:    User home latitude for location-based station search.
            longitude:   User home longitude for location-based station search.
            radius_km:   Search radius in kilometres (defaults to 10.0).
        """
        self._station_id = station_id
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else _DEFAULT_RADIUS_KM

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for the configured station.

        Queries carbu.com for each fuel type concurrently, matching the station
        by its ID.  All non-price metadata is taken from the first matching
        record found.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: carbu.com station ID.

        Returns:
            Populated StationData dict with all CAPABILITIES keys.

        Raises:
            ProviderError: Station ID not found in any API response.
        """
        # Use stored lat/lng if available; otherwise fall back to Luxembourg centre
        search_lat = self._latitude if self._latitude is not None else _LU_CENTRE_LAT
        search_lng = self._longitude if self._longitude is not None else _LU_CENTRE_LNG
        # When using the national-centre fallback, widen the radius so edge
        # stations across the ~20 km diagonal of Luxembourg are found.
        search_radius = (
            _NATIONAL_RADIUS_KM if self._latitude is None else self._radius_km
        )

        tasks = [
            self._fetch_fuel_stations(
                session,
                fuel_key=fuel_key,
                fuel_id=fuel_id,
                lat=search_lat,
                lng=search_lng,
                radius_km=search_radius,
            )
            for fuel_key, fuel_id in _FUEL_IDS.items()
        ]
        results: list[list[dict] | None] = list(await asyncio.gather(*tasks))

        station_meta: dict | None = None
        prices_by_fuel: dict[str, float | None] = {}

        for fuel_key, stations in zip(_FUEL_IDS.keys(), results):
            if not stations:
                continue
            record = _find_station(stations, station_id)
            if record is not None:
                prices_by_fuel[fuel_key] = _parse_price(record.get("price"))
                if station_meta is None:
                    station_meta = record

        if station_meta is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in carbu.com Luxembourg "
                "station data.  Verify the station ID is correct, or re-run "
                "the location search to find your station."
            )

        return self._build_station_data(station_id, station_meta, prices_by_fuel)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Queries carbu.com for Diesel stations near the configured location
        and returns the name of the matching station.  Returns None on any
        failure so the config flow falls back to 'Station {id}'.

        Args:
            session:    aiohttp ClientSession.
            station_id: carbu.com station ID.
        """
        search_lat = self._latitude if self._latitude is not None else _LU_CENTRE_LAT
        search_lng = self._longitude if self._longitude is not None else _LU_CENTRE_LNG

        try:
            stations = await self._fetch_fuel_stations(
                session,
                fuel_key="diesel",
                fuel_id=_FUEL_IDS["diesel"],
                lat=search_lat,
                lng=search_lng,
                radius_km=_NATIONAL_RADIUS_KM,
            )
            if stations:
                record = _find_station(stations, station_id)
                if record:
                    return record.get("name") or None
            # Retry with unleaded if diesel failed to find the station
            stations_sp = await self._fetch_fuel_stations(
                session,
                fuel_key="unleaded",
                fuel_id=_FUEL_IDS["unleaded"],
                lat=search_lat,
                lng=search_lng,
                radius_km=_NATIONAL_RADIUS_KM,
            )
            if stations_sp:
                record = _find_station(stations_sp, station_id)
                if record:
                    return record.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the station picker.

        Called by the config flow location_search step.  Queries Diesel and
        Super 95 stations within the radius, de-dupes by ID, and returns
        a list sorted alphabetically by label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the radius search.
            lng:       Centre longitude for the radius search.
            radius_km: Search radius in kilometres.

        Returns:
            Ordered list of (station_id, label) tuples.  Empty on failure.
        """
        lat: float | None = kwargs.get("lat")
        if lat is None:
            lat = self._latitude
        lng: float | None = kwargs.get("lng")
        if lng is None:
            lng = self._longitude
        _rk = kwargs.get("radius_km")
        radius_km: float = float(_rk if _rk is not None else self._radius_km)

        if lat is None or lng is None:
            _LOGGER.debug(
                "async_list_stations called without lat/lng — returning empty list"
            )
            return []

        try:
            results = await asyncio.gather(
                self._fetch_fuel_stations(
                    session,
                    fuel_key="diesel",
                    fuel_id=_FUEL_IDS["diesel"],
                    lat=lat,
                    lng=lng,
                    radius_km=radius_km,
                ),
                self._fetch_fuel_stations(
                    session,
                    fuel_key="unleaded",
                    fuel_id=_FUEL_IDS["unleaded"],
                    lat=lat,
                    lng=lng,
                    radius_km=radius_km,
                ),
                return_exceptions=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        diesel_resp = results[0] if not isinstance(results[0], BaseException) else None
        sp95_resp = results[1] if not isinstance(results[1], BaseException) else None

        # Merge per-fuel results into one dict keyed by station ID
        merged: dict[str, dict] = {}

        if isinstance(diesel_resp, list):
            for s in diesel_resp:
                sid = s.get("id")
                if sid:
                    merged[sid] = s

        if isinstance(sp95_resp, list):
            for s in sp95_resp:
                sid = s.get("id")
                if sid and sid not in merged:
                    merged[sid] = s

        if not merged:
            return []

        result: list[tuple[str, str]] = []
        for sid, station in merged.items():
            name = station.get("name") or "Unknown"
            brand = station.get("brand") or ""
            street = station.get("address") or ""
            city = station.get("city") or ""

            # Prefer brand over name when they differ; combine street + city
            display_name = (
                f"{brand} {name}".strip() if brand and brand != name else name
            )
            address_parts = [p for p in (street, city) if p]
            address_str = ", ".join(address_parts) if address_parts else ""

            label = f"{display_name}, {address_str} (#{str(sid)[:8]})"

            result.append((sid, label))

        result.sort(key=lambda x: x[1].casefold())
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_fuel_stations(
        self,
        session: ClientSession,
        fuel_key: str,
        fuel_id: int,
        lat: float,
        lng: float,
        radius_km: float,
    ) -> list[dict] | None:
        """Fetch stations for a single fuel type from carbu.com.

        Args:
            session:   aiohttp ClientSession.
            fuel_key:  StationData key for this fuel (used only for logging).
            fuel_id:   carbu.com numeric fuel type ID.
            lat:       Centre latitude for the search.
            lng:       Centre longitude for the search.
            radius_km: Search radius in kilometres.

        Returns:
            List of station dicts on success, or None on HTTP/network error.
        """
        params = {
            "type": "json",
            "carburant": fuel_id,
            "lat": lat,
            "lng": lng,
            "dist": max(1, round(radius_km)),
        }
        _LOGGER.debug("Fetching carbu.com LU stations: fuel=%s", fuel_key)
        try:
            async with session.get(
                _BASE_URL,
                params=params,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching carbu.com LU stations fuel=%s: HTTP %s",
                fuel_key,
                err.status,
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Error fetching carbu.com LU stations fuel=%s: %s",
                fuel_key,
                type(err).__name__,
            )
            return None

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            # Some carbu.com responses wrap the list under a key
            for key in ("stations", "data", "results"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        station_id: str,
        meta: dict,
        prices_by_fuel: dict[str, float | None],
    ) -> StationData:
        """Assemble a StationData dict from the raw carbu.com records.

        Args:
            station_id:     The carbu.com station ID (used for source_station_id).
            meta:           The station record used for non-price fields.
            prices_by_fuel: Map of fuel_key → parsed price float or None.

        Returns:
            Populated StationData dict.
        """
        name: str | None = meta.get("name") or None
        brand: str | None = meta.get("brand") or None
        city: str | None = meta.get("city") or None
        street: str | None = meta.get("address") or None

        # Build a combined address string
        if street and city:
            address: str | None = f"{street}, {city}"
        elif street:
            address = street
        elif city:
            address = city
        else:
            address = None

        lat = _parse_coord(meta.get("lat"))
        lng = _parse_coord(meta.get("lng"))

        lastupdated: str | None = meta.get("updated") or meta.get("lastupdated") or None

        data: StationData = {
            "unleaded": prices_by_fuel.get("unleaded"),
            "premium_unleaded": prices_by_fuel.get("premium_unleaded"),
            "diesel": prices_by_fuel.get("diesel"),
            "lpg": prices_by_fuel.get("lpg"),
            "cng": prices_by_fuel.get("cng"),
            "name": name,
            "brand": brand,
            "address": address,
            "latitude": lat,
            "longitude": lng,
            "lastupdated": lastupdated,
        }

        _LOGGER.debug(
            "carbu.com LU parsed data for station %s: "
            "diesel=%s sp95=%s sp98=%s lpg=%s cng=%s updated=%s",
            station_id,
            data.get("diesel"),
            data.get("unleaded"),
            data.get("premium_unleaded"),
            data.get("lpg"),
            data.get("cng"),
            lastupdated,
        )

        return data


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station(stations: list[dict], station_id: str) -> dict | None:
    """Return the station record matching station_id, or None.

    Searches by the ``id`` field (carbu.com internal station identifier).

    Args:
        stations:   List of station dicts from the carbu.com API response.
        station_id: Target station ID string.

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if str(station.get("id", "")) == str(station_id):
            return station
    return None
