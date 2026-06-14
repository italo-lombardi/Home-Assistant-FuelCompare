"""FrCarburantsProvider — French government fuel prices (Prix Carburants).

Source: Ministère de la Transition Énergétique — données.roulez-eco.fr
Endpoint: GET https://donnees.roulez-eco.fr/opendata/instantane
Returns a ZIP archive (~1 MB compressed, ~12 MB XML) containing a single
file ``PrixCarburants_instantane.xml`` with ~9 800 stations and ~33 000
price entries.  No authentication, no API key, fully open (CORS *).

Data is refreshed every ~10 minutes on the server side.  Licence Ouverte 2.0.

Parsing pipeline
----------------
1. GET the ZIP (Content-Type: application/zip).
2. Extract the single XML file from the archive.
3. Parse the XML as raw bytes — the document is ISO-8859-1 encoded and
   Python's xml.etree.ElementTree auto-detects the encoding from the XML
   declaration when fed bytes.  Decoding to UTF-8 str first would corrupt
   accented characters (é, è, â, etc.) in adresse/ville fields.
4. Iterate ``<pdv>`` children of the ``<pdv_liste>`` root element.

PDV element structure
---------------------
Attributes on ``<pdv>``:
  id         — station identifier string (integer, e.g. "34150003")
  latitude   — integer scaled by 100000 → divide by 100000 for decimal °
  longitude  — integer scaled by 100000 → divide by 100000 for decimal °
  cp         — postal code string (e.g. "34150")
  pop        — "R" (road/retail) or "A" (motorway)

Child elements:
  <adresse>                    — street address (text)
  <ville>                      — city name (may be ALL-CAPS, text)
  <horaires automate-24-24="">  — "1" if 24h unmanned; "" (empty) otherwise
  <prix nom="..." valeur="..."/> — one per stocked fuel type

Fuel nom → StationData key mapping:
  "Gazole"  → diesel
  "SP95"    → unleaded
  "SP98"    → premium_unleaded
  "E10"     → e10
  "E85"     → e85
  "GPLc"    → lpg
"""

from __future__ import annotations

import asyncio
import functools
import io
import logging
import time
import zipfile

# xml.etree.ElementTree is protected against billion-laughs since Python 3.8
# (https://docs.python.org/3/library/xml.etree.elementtree.html#xml-vulnerabilities).
# Home Assistant requires Python 3.12+, so that protection is always active.
# A 50 MB size check is added as defence-in-depth to prevent any amplification
# from an unexpectedly large or malicious response reaching the parser.
import xml.etree.ElementTree as ET
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

_DATA_URL = "https://donnees.roulez-eco.fr/opendata/instantane"

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
}

# Larger timeout: the ZIP is ~1 MB; on slow connections this can take a few
# seconds. API_TIMEOUT * 3 mirrors the approach used in hr_mzoe.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)

# Parsed-XML cache TTL — matches POLL_INTERVAL_SECONDS so a second call within
# the same coordinator cycle (e.g. async_fetch + async_list_stations) reuses
# the already-parsed root element without re-downloading or re-parsing.
_XML_CACHE_TTL = 600

# Mapping from XML ``nom`` attribute to StationData key
_NOM_TO_KEY: dict[str, str] = {
    "Gazole": "diesel",
    "SP95": "unleaded",
    "SP98": "premium_unleaded",
    "E10": "e10",
    "E85": "e85",
    "GPLc": "lpg",
}


def _parse_coord(raw: str | None) -> float | None:
    """Parse a scaled integer coordinate (divide by 100000) to float degrees."""
    if raw is None:
        return None
    try:
        return int(raw) / 100_000
    except (ValueError, TypeError):
        return None


def _parse_price(raw: str | None) -> float | None:
    """Parse a price string to float EUR/litre.  Returns None on any failure."""
    if not raw:
        return None
    try:
        val = float(raw.replace(",", "."))
        return round(val, 3) if val > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_pdv(element: ET.Element) -> dict[str, Any]:
    """Parse a single ``<pdv>`` element into a raw station dict."""
    attrib = element.attrib

    lat = _parse_coord(attrib.get("latitude"))
    lon = _parse_coord(attrib.get("longitude"))
    station_id: str = attrib.get("id", "")
    cp: str | None = attrib.get("cp") or None

    adresse: str | None = None
    ville: str | None = None
    is_open: bool | None = None
    prices: dict[str, float | None] = {}
    maj_values: list[str] = []

    for child in element:
        tag = child.tag

        if tag == "adresse":
            adresse = (child.text or "").strip() or None

        elif tag == "ville":
            ville = (child.text or "").strip() or None

        elif tag == "horaires":
            # automate-24-24 is "1" for true, "" (empty string) for false
            auto = child.attrib.get("automate-24-24", "")
            is_open = auto == "1"

        elif tag == "prix":
            nom = child.attrib.get("nom", "")
            key = _NOM_TO_KEY.get(nom)
            if key is not None:
                price = _parse_price(child.attrib.get("valeur"))
                prices[key] = price
                maj = child.attrib.get("maj")
                if maj:
                    maj_values.append(maj)

    # Use the most recent maj timestamp across all fuel types as lastupdated
    lastupdated: str | None = max(maj_values) if maj_values else None

    # Build county from postal code (département = first 2 digits, or 3 for DOM)
    county: str | None = None
    if cp:
        dept = cp[:2] if len(cp) >= 2 else cp
        if cp.startswith("97") or cp.startswith("98"):
            dept = cp[:3]
        county = f"Dept. {dept}"

    # Full name: combine ville + cp for a human-readable label
    if ville and cp:
        name: str | None = f"{ville} ({cp})"
    elif ville:
        name = ville
    elif cp:
        name = cp
    else:
        name = None

    # Build address string combining street + city
    if adresse and ville:
        address: str | None = f"{adresse}, {ville}"
    elif adresse:
        address = adresse
    elif ville:
        address = ville
    else:
        address = None

    return {
        "id": station_id,
        "latitude": lat,
        "longitude": lon,
        "cp": cp,
        "name": name,
        "county": county,
        "address": address,
        "is_open": is_open,
        "lastupdated": lastupdated,
        "prices": prices,
    }


def _build_station_data(raw: dict[str, Any]) -> StationData:
    """Convert a raw station dict into a StationData dict."""
    prices: dict[str, float | None] = raw.get("prices", {})
    data: StationData = {
        "diesel": prices.get("diesel"),
        "unleaded": prices.get("unleaded"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "e10": prices.get("e10"),
        "e85": prices.get("e85"),
        "lpg": prices.get("lpg"),
        "name": raw.get("name"),
        "county": raw.get("county"),
        "address": raw.get("address"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "is_open": raw.get("is_open"),
        "lastupdated": raw.get("lastupdated"),
        "source_station_id": raw.get("id"),
    }
    return data


class FrCarburantsProvider(BaseProvider):
    """Fetch French fuel prices from the Prix Carburants open-data service.

    The government publishes a single ZIP archive containing prices for all
    ~9 800 French service stations, updated every ~10 minutes.  This provider
    downloads the full archive on each poll cycle, parses the XML, and returns
    data for the configured station.

    CONFIG_MODE is 'location': the user supplies a lat/lng + radius_km and
    the config flow presents a list of nearby stations sorted by diesel price.
    The chosen station's ID (pdv/@id) is then stored in the config entry and
    used for subsequent polls.

    Station ID format: integer string, e.g. "34150003" or "6110002".
    """

    COUNTRY = "FR"
    PROVIDER_KEY = "fr_carburants"
    LABEL = "Prix Carburants (France)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 600  # data refreshed every ~10 minutes

    # Class-level XML cache — shared across all instances to avoid re-downloading
    # the ~12 MB ZIP on every coordinator refresh when multiple FR stations track.
    _xml_cache: ClassVar[ET.Element | None] = None
    _xml_cache_ts: ClassVar[float] = 0

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "diesel",
            "unleaded",
            "premium_unleaded",
            "e10",
            "e85",
            "lpg",
            "lastupdated",
            "name",
            "county",
            "address",
            "latitude",
            "longitude",
            "is_open",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the Prix Carburants station ID (the numeric id attribute "
        "from the XML, e.g. '34150003'). Use the location search to "
        "browse stations near your home."
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
            station_id:  Prix Carburants station ID (pdv/@id integer string).
            county:      Not used for fetching (full-national dump); kept for
                         structural compatibility with other providers.
            latitude:    User's home latitude for radius search.
            longitude:   User's home longitude for radius search.
            radius_km:   Search radius in kilometres for async_list_stations.
        """
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch the national dataset and return data for the configured station.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Prix Carburants station ID (pdv/@id).

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: Station not found in the national dataset.
        """
        root = await self._fetch_and_parse_xml(session)
        raw_station = _find_station_in_root(root, station_id)
        if raw_station is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in the Prix Carburants "
                "national dataset. Verify the station ID is correct."
            )
        return _build_station_data(raw_station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station name for the config flow, or None.

        For CONFIG_MODE='location' providers the config flow auto-generates a
        title from country + coordinates, so returning None is appropriate.
        This implementation attempts a full lookup to provide a better name
        when available.

        Args:
            session:    aiohttp ClientSession.
            station_id: Prix Carburants station ID.

        Returns:
            Human-readable station name string, or None on failure.
        """
        try:
            root = await self._fetch_and_parse_xml(session)
            raw_station = _find_station_in_root(root, station_id)
            if raw_station:
                return raw_station.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for stations near the user.

        Called by the config flow location_search step.  Filters stations
        within radius_km of the supplied coordinates and returns them sorted
        cheapest-diesel-first (stations with no diesel price last).

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the radius search.
            lng:       Centre longitude for the radius search.
            radius_km: Search radius in kilometres.

        Returns:
            Ordered list of (station_id, label) tuples.  Empty list on failure.
        """
        lat: float | None = (
            kwargs["lat"]
            if "lat" in kwargs and kwargs["lat"] is not None
            else self._latitude
        )
        lng: float | None = (
            kwargs["lng"]
            if "lng" in kwargs and kwargs["lng"] is not None
            else self._longitude
        )
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        if lat is None or lng is None:
            _LOGGER.debug(
                "async_list_stations called without lat/lng — returning empty list"
            )
            return []

        try:
            root = await self._fetch_and_parse_xml(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed to fetch XML: %s", err)
            return []

        result: list[tuple[str, str, float]] = []

        for pdv in root.iter("pdv"):
            raw = _parse_pdv(pdv)
            slat = raw.get("latitude")
            slon = raw.get("longitude")
            if slat is None or slon is None:
                continue

            dist_km = _haversine_km(lat, lng, slat, slon)
            if dist_km > radius_km:
                continue

            sid: str = raw.get("id", "")
            if not sid:
                continue

            prices: dict[str, float | None] = raw.get("prices", {})
            name: str = raw.get("name") or sid
            address: str | None = raw.get("address")

            label_name = f"{name} — {address}" if address else name

            price_parts: list[str] = []
            diesel = prices.get("diesel")
            if diesel is not None:
                price_parts.append(f"Diesel €{diesel:.3f}")
            unleaded = prices.get("unleaded") or prices.get("e10")
            if unleaded is not None:
                price_parts.append(f"SP €{unleaded:.3f}")

            if price_parts:
                label = f"{label_name} — {' / '.join(price_parts)}"
                sort_key = diesel if diesel is not None else 9999.0
            else:
                label = label_name
                sort_key = 9999.0

            result.append((sid, label, sort_key))

        result.sort(key=lambda x: x[2])
        return [(sid, label) for sid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_and_parse_xml(self, session: ClientSession) -> ET.Element:
        """Return the parsed XML root element, using a TTL cache.

        The cache is keyed per-instance with a TTL of ``_XML_CACHE_TTL`` seconds
        (600 s, matching ``POLL_INTERVAL_SECONDS``).  Within a single coordinator
        cycle both ``async_fetch`` and ``async_list_stations`` call this method;
        only the first call downloads and parses the ~12 MB XML — the second call
        returns the cached root element immediately.

        Returns:
            The ``<pdv_liste>`` root ``ET.Element``.

        Raises:
            ProviderError: XML could not be fetched or parsed.
        """
        now = time.monotonic()
        if (
            FrCarburantsProvider._xml_cache is not None
            and (now - FrCarburantsProvider._xml_cache_ts) < _XML_CACHE_TTL
        ):
            _LOGGER.debug(
                "Prix Carburants XML cache hit (age %.1fs)",
                now - FrCarburantsProvider._xml_cache_ts,
            )
            return FrCarburantsProvider._xml_cache

        xml_bytes = await self._fetch_xml(session)
        try:
            loop = asyncio.get_running_loop()
            root = await loop.run_in_executor(
                None, functools.partial(ET.fromstring, xml_bytes)
            )
        except ET.ParseError as err:
            raise ProviderError(f"Failed to parse Prix Carburants XML: {err}") from err

        FrCarburantsProvider._xml_cache = root
        FrCarburantsProvider._xml_cache_ts = now
        _LOGGER.debug("Prix Carburants XML parsed and cached")
        return root

    async def _fetch_xml(self, session: ClientSession) -> bytes:
        """Download the ZIP from roulez-eco.fr and return the XML bytes.

        The ZIP always contains exactly one file; this implementation takes
        index 0 of the namelist.  The XML is returned as raw bytes so that
        ElementTree can auto-detect the ISO-8859-1 encoding from the XML
        declaration — decoding to UTF-8 str first would corrupt accented
        characters.

        Returns:
            Raw XML bytes.

        Raises:
            ProviderError: The ZIP archive appears empty or malformed.
            aiohttp.ClientError: Network/HTTP errors (propagated to coordinator).
        """
        _LOGGER.debug("Fetching Prix Carburants ZIP from %s", _DATA_URL)
        async with session.get(_DATA_URL, headers=_HEADERS, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            compressed = await resp.read()

        try:
            with zipfile.ZipFile(io.BytesIO(compressed)) as zf:
                names = zf.namelist()
                if not names:
                    raise ProviderError(
                        "Prix Carburants ZIP archive is empty — "
                        "cannot extract XML data."
                    )
                # Check uncompressed size before reading to guard against ZIP bombs
                if zf.getinfo(names[0]).file_size > 50_000_000:
                    raise ProviderError("FR carburants XML response exceeds size limit")
                xml_bytes: bytes = zf.read(names[0])
        except zipfile.BadZipFile as err:
            raise ProviderError(
                f"Prix Carburants response is not a valid ZIP archive: {err}"
            ) from err

        return xml_bytes


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station_in_root(root: ET.Element, station_id: str) -> dict[str, Any] | None:
    """Search an already-parsed XML root for station_id and return its raw dict.

    Args:
        root:       Parsed ``<pdv_liste>`` root element.
        station_id: Target station ID string.

    Returns:
        Parsed station dict (from ``_parse_pdv``), or None if not found.
    """
    for pdv in root.iter("pdv"):
        if pdv.attrib.get("id") == station_id:
            return _parse_pdv(pdv)
    return None
