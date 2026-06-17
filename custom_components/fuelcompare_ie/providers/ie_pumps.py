"""IePumpsProvider — Irish fuel prices from pumps.ie (crowd-sourced).

pumps.ie is a crowd-sourced Irish fuel price tracker with no documented API
and no API key requirement.  The site exposes an undocumented PHP XML API
that returns per-station price data.

WARNING — DATA FRESHNESS
------------------------
pumps.ie is in maintenance-mode decline.  As of 2026-06:

- SSL certificate is expired — verification disabled via _SSL_UNVERIFIED SSLContext.
- The crowd-sourced price data is largely stale:
    * Only ~3 stations have prices updated in 2025.
    * ~961 stations last updated in 2024.
    * ~868 stations last updated before 2024, some dating to 2013.
- The ``dateupdated`` field is exposed in every StationData dict so users
  can verify data freshness for themselves.

If you need reliable Irish fuel prices, use the ``ie_fuelfinder`` provider
(fuelfinder.ie), which has fresh crowd-sourced data and a modern JSON API.

Endpoint strategy
-----------------
GET https://pumps.ie/api/getStationsByPriceAPI.php
  ?county={county}&minLat={minLat}&maxLat={maxLat}
  &minLng={minLng}&maxLng={maxLng}&fuel={petrol|diesel}
  &noCache={rand}

Returns XML (not JSON).  A wide bounding box covering all of Ireland is used
so that a single request returns all ~1832 stations nationally.  Two requests
are made per poll cycle — one for petrol, one for diesel — and merged by
station ID.

Bounding box for all of Ireland:
  minLat=50.0, maxLat=55.5, minLng=-11.0, maxLng=-5.5

XML element structure
---------------------
Each <station> element carries attributes (not child text nodes):
  ID          — string integer, e.g. "1234"
  Lat         — WGS84 latitude, e.g. "53.3498"
  Lng         — WGS84 longitude, e.g. "-6.2603"
  name        — station display name
  brand       — brand name (may be empty)
  addr1       — first address line
  addr2       — second address line / town
  price       — cents-per-litre, e.g. "173.9" = €1.739/litre
  fuel        — "petrol" or "diesel"
  trend       — "up", "down", or "stable"
  dateupdated — ISO-like date string, e.g. "2025-06-08 14:23:00"
  dateupdatedshort — short display date, e.g. "Jun 8 2025"
  Updater     — username of price submitter
  Zone        — zone/county name
  County      — county name

Price normalisation
-------------------
pumps.ie returns prices in cents-per-litre (e.g. 173.9 = €1.739/litre).
Divide by 100 to get EUR/litre.

STATION_LOOKUP_MODE = "location_search"
CONFIG_MODE = "station_id"

StationData field mapping
-------------------------
ID              → source_station_id
name            → name
brand           → brand, tablename
addr1 + addr2   → address
Zone/County     → county
Lat             → latitude
Lng             → longitude
price (diesel)  → diesel  (cents ÷ 100 → EUR/litre)
price (petrol)  → unleaded, petrol  (cents ÷ 100 → EUR/litre)
dateupdated     → lastupdated
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import ssl
from html import unescape as _html_unescape
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# pumps.ie has an expired TLS certificate; cert renewal is pending.
# Context created at import time (before the HA event loop starts) to avoid
# blocking-call-in-event-loop warnings from ssl.create_default_context().
_SSL_UNVERIFIED: ssl.SSLContext = _make_ssl_context()
_SSL_WARNING_EMITTED = False


def _warn_ssl_once() -> None:
    global _SSL_WARNING_EMITTED
    if _SSL_WARNING_EMITTED:
        return
    _SSL_WARNING_EMITTED = True
    _LOGGER.warning(
        "pumps.ie TLS certificate verification is disabled — the provider's "
        "SSL certificate is expired. This is a known issue. "
        "Data is still encrypted in transit."
    )


# Use HTTPS anyway so the connection is encrypted (just not verified).
_BASE_URL = "https://pumps.ie/api/getStationsByPriceAPI.php"

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "text/xml,application/xml,*/*",
}

# Generous timeout: XML response for ~1832 stations is larger than typical.
_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT, 30))

# Bounding box covering all of Ireland (Republic + Northern Ireland).
_IE_MIN_LAT = 50.0
_IE_MAX_LAT = 55.5
_IE_MIN_LNG = -11.0
_IE_MAX_LNG = -5.5

# Fuel types the API accepts.
_FUEL_TYPES: tuple[str, ...] = ("diesel", "petrol")


class IePumpsProvider(BaseProvider):
    """Fetch Irish fuel prices from pumps.ie (crowd-sourced, station-level).

    The station is identified by its pumps.ie integer station ID (the ``ID``
    XML attribute).  Prices are in cents-per-litre in the raw API and are
    converted to EUR/litre on return.

    IMPORTANT: pumps.ie data is largely stale (see module docstring).  The
    ``lastupdated`` attribute on every StationData exposes the crowd-sourced
    price submission timestamp so Home Assistant users can assess data age.

    Usage
    -----
    Construct with a station ID string.  The provider fetches a national
    bounding-box XML response for both fuel types and merges by station ID.
    """

    COUNTRY = "IE"
    PROVIDER_KEY = "ie_pumps"
    LABEL = "pumps.ie"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 3600
    STATION_PAGE_URL: ClassVar[str] = (
        "https://pumps.ie"  # 1 hour; data is crowd-sourced and mostly stale
    )

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "diesel",
            "unleaded",
            # Station identity
            "name",
            "brand",
            "address",
            "county",
            "latitude",
            "longitude",
            # Timing (crowd-sourced — may be months or years old)
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Enter the pumps.ie numeric station ID.  You can find it by browsing "
        "to pumps.ie and inspecting the station URL or page source for the "
        "'ID' field.  NOTE: pumps.ie price data is largely stale — "
        "fuelfinder.ie (provider key: ie_fuelfinder) is recommended instead."
    )

    def __init__(self, station_id: str) -> None:
        """Initialise the provider with the pumps.ie station ID.

        Args:
            station_id: pumps.ie integer station ID as a string (e.g. '1234').
        """
        self._station_id = station_id

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for the given station ID.

        Makes two requests per poll cycle (diesel + petrol) using a national
        bounding box, parses the XML responses, and merges the prices.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: pumps.ie integer station ID string.

        Returns:
            StationData dict with all CAPABILITIES keys populated (may be
            None when the API has no data for a field).

        Raises:
            ProviderError: Station ID not found in either API response, or
                           both responses returned errors.
        """
        tasks = [self._fetch_stations(session, fuel=fuel) for fuel in _FUEL_TYPES]
        results: list[list[dict] | None] = list(await asyncio.gather(*tasks))

        # Merge per-fuel results into a single dict keyed by station ID.
        prices_by_fuel: dict[str, dict] = {}
        station_meta: dict | None = None

        for fuel, stations in zip(_FUEL_TYPES, results):
            if not stations:
                continue
            record = _find_station(stations, station_id)
            if record is not None:
                prices_by_fuel[fuel] = record
                if station_meta is None:
                    station_meta = record

        if station_meta is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in pumps.ie national "
                "station list.  Verify the ID is correct by checking pumps.ie.  "
                "Note: pumps.ie has largely stale data — consider using the "
                "ie_fuelfinder provider instead."
            )

        return _build_station_data(station_id, station_meta, prices_by_fuel)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Makes a single national diesel fetch to resolve the station name.
        Returns None on any failure so the config flow falls back to
        'Station {id}'.

        Args:
            session:    aiohttp ClientSession.
            station_id: pumps.ie integer station ID string.
        """
        try:
            diesel_stations, petrol_stations = await asyncio.gather(
                self._fetch_stations(session, fuel="diesel"),
                self._fetch_stations(session, fuel="petrol"),
            )
            if diesel_stations:
                record = _find_station(diesel_stations, station_id)
                if record:
                    return record.get("name") or None
            if petrol_stations:
                record = _find_station(petrol_stations, station_id)
                if record:
                    return record.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to fetch station name for pumps.ie station %s: %s",
                station_id,
                err,
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the location-search picker.

        Fetches the national station list, filters by Haversine distance from
        (lat, lng), and returns stations within radius_km sorted alphabetically
        by label.

        Kwargs:
            lat (float):       Centre latitude for the search.
            lng (float):       Centre longitude for the search.
            radius_km (float): Search radius in kilometres (default: 10).

        Returns:
            List of (station_id, "{display_name}, {address} (#{sid[:8]})") tuples
            sorted alphabetically by label.  When no address is available the
            label is "{display_name} (#{sid[:8]})".
            Empty list on any failure or when no stations are within radius.
        """
        lat: float | None = kwargs.get("lat")  # type: ignore[assignment]
        lng: float | None = kwargs.get("lng")  # type: ignore[assignment]
        radius_km: float = float(kwargs.get("radius_km", 10.0))

        # is-not-None coord checks (not falsy — 0.0 is a valid coordinate)
        if lat is None or lng is None:
            _LOGGER.debug(
                "async_list_stations called without lat/lng; returning empty list"
            )
            return []

        try:
            diesel_resp, petrol_resp = await asyncio.gather(
                self._fetch_stations(session, fuel="diesel"),
                self._fetch_stations(session, fuel="petrol"),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations fetch failed: %s", err)
            return []

        # Merge per-fuel station lists keyed by station ID.
        merged: dict[str, dict] = {}

        if isinstance(diesel_resp, list):
            for s in diesel_resp:
                sid = s.get("ID")
                if sid:
                    merged[sid] = s

        if isinstance(petrol_resp, list):
            for s in petrol_resp:
                sid = s.get("ID")
                if sid and sid not in merged:
                    merged[sid] = s

        if not merged:
            return []

        nearby: list[tuple[str, str]] = []
        for sid, station in merged.items():
            s_lat = station.get("lat")
            s_lng = station.get("lng")
            # is-not-None coordinate checks
            if s_lat is None or s_lng is None:
                continue

            dist = haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            name = station.get("name") or "Unknown"
            brand = station.get("brand") or ""
            addr1 = (station.get("addr1") or "").strip()
            addr2 = (station.get("addr2") or "").strip()
            if addr1 and addr2:
                address = f"{addr1}, {addr2}"
            elif addr1:
                address = addr1
            elif addr2:
                address = addr2
            else:
                address = ""

            if brand and name.lower().startswith(brand.lower()):
                display_name = name.strip()
            else:
                display_name = f"{brand} {name}".strip() if brand else name

            if address:
                label = f"{display_name}, {address} (#{sid[:8]})"
            else:
                label = f"{display_name} (#{sid[:8]})"

            nearby.append((sid, label))

        nearby.sort(key=lambda x: x[1].lower())
        return nearby

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_stations(
        self,
        session: ClientSession,
        fuel: str,
    ) -> list[dict] | None:
        """Fetch the national station list for one fuel type.

        Uses a wide bounding box covering all of Ireland so a single request
        returns all stations.  The ``noCache`` parameter is randomised to
        bypass any server-side caching.

        Args:
            session: aiohttp ClientSession.
            fuel:    ``'diesel'`` or ``'petrol'``.

        Returns:
            List of station dicts on success (may be empty if API returned no stations).
            None on HTTP error or exception.
            Each dict has keys: ID, name, brand, addr1, addr2, lat, lng,
            price_eur, fuel, trend, dateupdated, Zone, County.
        """
        params = {
            "county": "Cork",  # required by API but ignored when bbox covers all IE
            "minLat": str(_IE_MIN_LAT),
            "maxLat": str(_IE_MAX_LAT),
            "minLng": str(_IE_MIN_LNG),
            "maxLng": str(_IE_MAX_LNG),
            "fuel": fuel,
            "noCache": str(random.randint(1, 999999)),
        }
        _warn_ssl_once()
        try:
            async with session.get(
                _BASE_URL,
                params=params,
                headers=_HEADERS,
                timeout=_TIMEOUT,
                ssl=_SSL_UNVERIFIED,
            ) as response:
                response.raise_for_status()
                xml_text: str = await response.text(encoding="utf-8", errors="replace")
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching pumps.ie stations fuel=%s: %s", fuel, err
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error fetching pumps.ie stations fuel=%s: %s", fuel, err
            )
            return None

        return _parse_xml(xml_text, fuel)


# ── Module-level helpers ──────────────────────────────────────────────────────


# pumps.ie XML uses only self-closing <station ... /> tags. Open/close form
# not emitted by current API; if it ever changes, update this regex.
_STATION_TAG_RE = re.compile(r"<station\s+([^>]+?)/>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_xml(xml_text: str, fuel: str) -> list[dict]:
    """Parse the pumps.ie XML response and return a list of station dicts.

    Uses regex extraction rather than an XML parser because pumps.ie returns
    malformed XML (HTML entities like &aacute;, double-quoted attributes like
    Updater="foo"") that breaks standard parsers.

    Args:
        xml_text: Raw XML string from the API response.
        fuel:     ``'diesel'`` or ``'petrol'`` — stored on each returned dict.

    Returns:
        List of station dicts, or empty list when no <station/> tags found.
    """
    decoded = _html_unescape(xml_text)
    station_tags = _STATION_TAG_RE.findall(decoded)
    if not station_tags:
        _LOGGER.debug("pumps.ie XML: no <station .../> tags found in response")
        return []

    stations: list[dict] = []

    for tag_attrs in station_tags:
        attrib = dict(_ATTR_RE.findall(tag_attrs))

        station_id = attrib.get("ID") or attrib.get("id") or None
        if not station_id:
            continue

        lat = _parse_float(attrib.get("Lat") or attrib.get("lat"))
        lng = _parse_float(attrib.get("Lng") or attrib.get("lng"))

        price_raw = _parse_float(attrib.get("price"))
        price_eur = round(price_raw / 100.0, 4) if price_raw and price_raw > 0 else None

        addr1 = (attrib.get("addr1") or "").strip()
        addr2 = (attrib.get("addr2") or "").strip()
        if addr1 and addr2:
            address = f"{addr1}, {addr2}"
        elif addr1:
            address = addr1
        else:
            address = addr2 or None

        county = (attrib.get("County") or attrib.get("Zone") or "").strip() or None

        stations.append(
            {
                "ID": str(station_id),
                "name": (attrib.get("name") or "").strip() or None,
                "brand": (attrib.get("brand") or "").strip() or None,
                "addr1": addr1,
                "addr2": addr2,
                "address": address,
                "county": county,
                "lat": lat,
                "lng": lng,
                "price_eur": price_eur,
                "fuel": fuel,
                "trend": attrib.get("trend") or None,
                "dateupdated": attrib.get("dateupdated") or None,
                "Zone": attrib.get("Zone") or None,
            }
        )

    _LOGGER.debug("pumps.ie parsed %d stations for fuel=%s", len(stations), fuel)
    return stations


def _find_station(stations: list[dict], station_id: str) -> dict | None:
    """Return the station record matching station_id, or None.

    Args:
        stations:   List of station dicts from _parse_xml.
        station_id: Target pumps.ie integer station ID string.

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if station.get("ID") == station_id:
            return station
    return None


def _parse_float(value: str | None) -> float | None:
    """Parse a string to float, returning None on failure or if value is None.

    Args:
        value: String to parse, e.g. '53.3498' or '173.9'.

    Returns:
        Parsed float, or None.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _build_station_data(
    station_id: str,
    meta: dict,
    prices_by_fuel: dict[str, dict],
) -> StationData:
    """Assemble a StationData dict from parsed pumps.ie station records.

    Args:
        station_id:     The pumps.ie station ID string (used for source_station_id).
        meta:           Station record from whichever fuel type was found first.
                        Used for all non-price fields.
        prices_by_fuel: Map of fuel_type → station record.  The ``price_eur``
                        field differs per fuel type; other fields are the same.

    Returns:
        Populated StationData dict with all CAPABILITIES keys.
    """

    def _price(fuel: str) -> float | None:
        """Extract the EUR/litre price for the given fuel type."""
        record = prices_by_fuel.get(fuel)
        if record is None:
            return None
        val = record.get("price_eur")
        if val is None:
            return None
        if val <= 0:
            return None
        return round(val, 4)

    # ── Location coordinates ──────────────────────────────────────────────
    lat: float | None = meta.get("lat")
    lng: float | None = meta.get("lng")

    # ── Non-price identity fields ────────────────────────────────────────
    name: str | None = meta.get("name") or None
    brand: str | None = meta.get("brand") or None
    address: str | None = meta.get("address") or None
    county: str | None = meta.get("county") or None

    # ── Timing: use the most recent dateupdated across fuel types ─────────
    # pumps.ie uses "YYYY-MM-DD HH:MM:SS" — lexical max == chronological max.
    ts_candidates = [
        record.get("dateupdated")
        for record in (prices_by_fuel.get(f) for f in _FUEL_TYPES)
        if record is not None and record.get("dateupdated")
    ]
    dateupdated: str | None = max(ts_candidates) if ts_candidates else None

    # ── Assemble dict ────────────────────────────────────────────────────

    diesel_price = _price("diesel")
    petrol_price = _price("petrol")

    data: StationData = {
        # Fuel prices
        "diesel": diesel_price,
        "unleaded": petrol_price,
        # Station identity
        "name": name,
        "brand": brand,
        "address": address,
        "county": county,
        "latitude": lat,
        "longitude": lng,
        # Station page
        "source_station_id": station_id,
        # Timing
        "lastupdated": dateupdated,
    }

    _LOGGER.debug(
        "pumps.ie parsed data for station %s: diesel=%s petrol=%s "
        "dateupdated=%s county=%s",
        station_id,
        data.get("diesel"),
        data.get("unleaded"),
        dateupdated,
        county,
    )

    return data
