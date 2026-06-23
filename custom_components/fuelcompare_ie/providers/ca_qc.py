"""CaQcProvider — Quebec fuel prices from Régie de l'énergie (regieessencequebec.ca).

Source: Régie de l'énergie du Québec — official mandatory price-reporting system.
Data: https://regieessencequebec.ca/stations.geojson.gz
Format: gzip-compressed GeoJSON FeatureCollection.
Auth: None — CORS-open Azure Blob Storage endpoint (Access-Control-Allow-Origin: *).
Update cadence: Cache-Control: public, max-age=60 (refreshed every ~1 minute by the
Régie platform).  This provider polls at POLL=3600 (1 hour) to avoid hammering the
endpoint; the data is in practice useful at hourly granularity.

API structure (as of June 2026)
--------------------------------
The endpoint returns a GeoJSON FeatureCollection.  Each feature has:

  geometry:   {"type": "Point", "coordinates": [longitude, latitude]}
  properties: {
    "Name":       string  — station name (unique within province when combined with Address)
    "brand":      string  — brand/bannière (e.g. "Shell", "Petro-Canada", "Ultramar")
    "Status":     string  — "En opération" (only open stations are included)
    "Address":    string  — civic address + city (e.g. "230 av. Murdoch, Rouyn-Noranda")
    "PostalCode": string  — Canadian postal code (e.g. "H2X 1Y2")
    "Region":     string  — administrative region (e.g. "Montréal", "Laurentides")
    "Prices":     list[{
        "GasType":     "Régulier" | "Super" | "Diesel"
        "Price":       string like "189.9¢" or None when not available
        "IsAvailable": bool
    }]
  }

Features have no stable UUID field.  A deterministic station_id is generated as
MD5(Name + "|" + Address)[:16], which is stable across data refreshes.

Price normalisation
-------------------
Prices are expressed as "189.9¢" (cents per litre, CAD).  The provider strips
the "¢" suffix and divides by 100 to obtain CAD/litre (e.g. 1.899).

GasType → StationData key mapping
-----------------------------------
  Régulier → unleaded          (standard E10 gasoline)
  Super    → premium_unleaded  (premium gasoline)
  Diesel   → diesel

HACS reference
--------------
bennydiamond/regie_essence_ha v1.1.0 uses the same endpoint.

STATION_LOOKUP_MODE = "location_search"
CONFIG_MODE = "station_id"
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData
from ._geo import haversine_km

_LOGGER = logging.getLogger(__name__)

# The GeoJSON endpoint is a gzip-compressed Azure Blob hosted by the Régie.
# CORS is open (Access-Control-Allow-Origin: *).  The endpoint returns
# Content-Encoding: gzip so aiohttp will decompress automatically when
# reading via response.json().  However, the Content-Type is
# "application/geo+json" rather than "application/json", so we must pass
# content_type=None to avoid aiohttp's strict type check.
_GEOJSON_URL = "https://regieessencequebec.ca/stations.geojson.gz"

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/geo+json, application/json, */*",
    "Accept-Encoding": "gzip, deflate",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# GasType values in the API response → StationData keys
_GAS_TYPE_MAP: dict[str, str] = {
    "Régulier": "unleaded",
    "Super": "premium_unleaded",
    "Diesel": "diesel",
}

# Regex to strip trailing "¢" and any whitespace from price strings.
_CENTS_RE = re.compile(r"[^\d.]")


def _make_station_id(name: str, address: str) -> str:
    """Return a deterministic 16-char hex station ID from name and address.

    The GeoJSON data has no UUID or numeric ID per feature.  This hash is
    stable across data refreshes (the Name/Address fields do not change once
    a station is registered with the Régie).

    Args:
        name:    Station name from the ``Name`` property.
        address: Civic address string from the ``Address`` property.

    Returns:
        16-character lowercase hex string.
    """
    key = f"{name}|{address}".encode("utf-8")
    return hashlib.md5(key, usedforsecurity=False).hexdigest()[:16]  # noqa: S324


def _parse_price(price_raw: Any) -> float | None:
    """Parse a price string like '189.9¢' into a CAD/litre float.

    Args:
        price_raw: Raw price value from the API (string, None, or numeric).

    Returns:
        Price in CAD/litre, rounded to 4 decimal places.  None if the price
        cannot be parsed or is non-positive.
    """
    if price_raw is None:
        return None
    raw_str = str(price_raw).strip()
    if not raw_str:
        return None
    # Reject obviously negative strings before stripping symbols
    if raw_str.startswith("-"):
        return None
    # Strip non-numeric chars (e.g. the "¢" suffix, spaces)
    clean = _CENTS_RE.sub("", raw_str).strip()
    if not clean:
        return None
    try:
        cents = float(clean)
    except (ValueError, TypeError):
        return None
    if cents <= 0:
        return None
    # Prices are in cents/litre; convert to dollars/litre
    return round(cents / 100.0, 4)


def _build_station_data(feature: dict[str, Any], station_id: str) -> StationData:
    """Build a normalised StationData dict from a GeoJSON feature.

    Args:
        feature:    A single GeoJSON feature from the FeatureCollection.
        station_id: The pre-computed station ID for this feature.

    Returns:
        Populated StationData dict.
    """
    props: dict[str, Any] = feature.get("properties") or {}
    geometry: dict[str, Any] = feature.get("geometry") or {}

    # ── Coordinates (GeoJSON: [longitude, latitude]) ─────────────────────
    coords = geometry.get("coordinates")
    try:
        longitude: float | None = float(coords[0]) if coords else None
        latitude: float | None = float(coords[1]) if coords else None
    except (TypeError, IndexError, ValueError):
        latitude = None
        longitude = None

    # ── Identity ──────────────────────────────────────────────────────────
    name: str | None = props.get("Name") or None
    brand: str | None = props.get("brand") or None
    address: str | None = props.get("Address") or None
    postal_code: str | None = props.get("PostalCode") or None
    region: str | None = props.get("Region") or None

    # Build a full address string including postal code when available
    full_address: str | None = None
    if address:
        full_address = f"{address}, {postal_code}" if postal_code else address

    # ── Prices ────────────────────────────────────────────────────────────
    prices: dict[str, float | None] = {
        "unleaded": None,
        "premium_unleaded": None,
        "diesel": None,
    }

    for price_entry in props.get("Prices") or []:
        gas_type: str = price_entry.get("GasType", "")
        is_available: bool = bool(price_entry.get("IsAvailable", False))
        if not is_available:
            continue
        data_key = _GAS_TYPE_MAP.get(gas_type)
        if data_key is None:
            continue
        raw_price = price_entry.get("Price")
        parsed = _parse_price(raw_price)
        if parsed is not None:
            prices[data_key] = parsed

    # ── Assemble ──────────────────────────────────────────────────────────
    return {
        "unleaded": prices["unleaded"],
        "premium_unleaded": prices["premium_unleaded"],
        "diesel": prices["diesel"],
        "name": name,
        "brand": brand,
        "address": full_address,
        "county": region,
        "latitude": latitude,
        "longitude": longitude,
        "source_station_id": station_id,
    }


class CaQcProvider(BaseProvider):
    """Fetch Quebec fuel prices from regieessencequebec.ca GeoJSON feed.

    The Régie de l'énergie du Québec publishes a public GeoJSON feed at
    https://regieessencequebec.ca/stations.geojson.gz containing all open
    gas stations in Quebec with current prices.

    STATION_LOOKUP_MODE = 'location_search':
        The config flow calls ``async_list_stations(session, lat, lng, radius_km)``
        to populate a station picker for the user.  The user then selects a
        station whose ID is stored in the config entry.

    CONFIG_MODE = 'station_id':
        The station ID is a 16-char hex string derived from MD5(Name|Address).
        It is stable across data refreshes.

    Price units: CAD/litre (converted from the API's cents/litre format).
    """

    COUNTRY = "CA"
    PROVIDER_KEY = "ca_qc"
    LABEL = "Régie de l'énergie (Québec)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 3600
    STATION_PAGE_URL: ClassVar[str] = (
        "https://regieessencequebec.ca"  # 1 hour — data refreshes ~every minute but
    )
    CURRENCY: ClassVar[str] = "CA$"
    # hourly polling is sufficient and respectful of the CDN.

    _GEOJSON_CACHE_TTL: ClassVar[int] = 3600

    REQUIRES_API_KEY = False

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",
            "premium_unleaded",
            "diesel",
            "name",
            "brand",
            "address",
            "county",
            "latitude",
            "longitude",
        }
    )

    STATION_ID_HINT = (
        "Enter the Régie Essence Québec station ID (16-char hex).  "
        "Use the location search to browse nearby stations — the ID is "
        "generated automatically from the station name and address."
    )

    def __init__(
        self,
        station_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        county: str | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  16-char hex station ID.
            latitude:    User's latitude (for location_search mode).
            longitude:   User's longitude (for location_search mode).
            radius_km:   Search radius in km (for location_search mode).
            county:      Unused; accepted for compatibility with config entry data.
        """
        self._station_id = station_id
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km
        # Instance-level GeoJSON cache — avoids shared mutable state across
        # config entries.  Two entries with different radii/locations will not
        # race to populate or invalidate each other's cache.
        self._geojson_cache: list | None = None
        self._geojson_cache_ts: float = 0.0
        self._geojson_lock: asyncio.Lock = asyncio.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch current fuel prices for the configured station.

        Downloads the full GeoJSON feed, finds the station by its ID, and
        returns normalised station data.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: 16-char hex station ID (same as passed to ``__init__``).

        Returns:
            StationData dict with CAPABILITIES keys populated.

        Raises:
            ProviderError: Station ID not found in the current dataset.
            aiohttp.ClientError: Network/HTTP error (let propagate to coordinator).
        """
        features = await self._fetch_geojson(session)

        for feature in features:
            props = feature.get("properties") or {}
            name = props.get("Name", "")
            address = props.get("Address", "")
            fid = _make_station_id(name, address)
            if fid == station_id:
                return _build_station_data(feature, station_id)

        raise ProviderError(
            f"Station ID '{station_id}' not found in Régie Essence Québec dataset.  "
            "The station may have been closed or its name/address may have changed.  "
            "Use the location search to re-select the station."
        )

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name, or None on failure.

        Args:
            session:    aiohttp ClientSession.
            station_id: 16-char hex station ID.
        """
        try:
            features = await self._fetch_geojson(session)
            for feature in features:
                props = feature.get("properties") or {}
                name = props.get("Name", "")
                address = props.get("Address", "")
                fid = _make_station_id(name, address)
                if fid == station_id:
                    return name or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "async_fetch_station_name failed for station %s: %s", station_id, err
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for stations near a location.

        Called by the config flow location_search step.  Fetches the full
        GeoJSON dataset, filters to stations within ``radius_km`` of the
        supplied coordinates, and returns them sorted alphabetically by label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude (float).
            lng:       Centre longitude (float).
            radius_km: Search radius in km (float, default 10).

        Returns:
            List of (station_id, "Name, Address (#shortid)") tuples,
            sorted alphabetically by label.
            Empty list on any failure.
        """
        lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude
        lng = kwargs.get("lng") if kwargs.get("lng") is not None else self._longitude
        radius_km = float(kwargs.get("radius_km") or self._radius_km or 10.0)

        if lat is None or lng is None:
            _LOGGER.debug("async_list_stations: no coordinates provided")
            return []

        try:
            features = await self._fetch_geojson(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        result: list[tuple[str, str]] = []

        for feature in features:
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates")

            # Extract and validate coordinates
            try:
                station_lon = float(coords[0]) if coords else None
                station_lat = float(coords[1]) if coords else None
            except (TypeError, IndexError, ValueError):
                continue

            if station_lat is None or station_lon is None:
                continue

            # Distance filter
            dist = haversine_km(lat, lng, station_lat, station_lon)
            if dist > radius_km:
                continue

            name: str = props.get("Name") or "Unknown"
            address: str = props.get("Address") or ""
            station_id = _make_station_id(
                props.get("Name", ""), props.get("Address", "")
            )

            # Compose display label: "Name, Address (#shortid)"
            label = f"{name}, {address} (#{station_id[:8]})"

            result.append((station_id, label))

        result.sort(key=lambda x: x[1].casefold())
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_geojson(
        self,
        session: ClientSession,
    ) -> list[dict[str, Any]]:
        """Fetch and return the GeoJSON features list.

        Downloads the gzip-compressed GeoJSON from regieessencequebec.ca and
        returns the ``features`` list.  aiohttp automatically decompresses the
        gzip body when Accept-Encoding: gzip is set and the server returns
        Content-Encoding: gzip.

        Returns:
            List of GeoJSON feature dicts.  Empty list if the payload has no
            features key.

        Raises:
            aiohttp.ClientResponseError: On HTTP 4xx/5xx.
            aiohttp.ClientError: On network failure.
        """
        import time

        now = time.monotonic()
        async with self._geojson_lock:
            if (
                self._geojson_cache is not None
                and (now - self._geojson_cache_ts) < self._GEOJSON_CACHE_TTL
            ):
                _LOGGER.debug("Régie Essence Québec: serving GeoJSON from cache")
                return self._geojson_cache

            _LOGGER.debug("Fetching Régie Essence Québec GeoJSON feed")
            async with session.get(
                _GEOJSON_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload: dict[str, Any] = await response.json(content_type=None)

            features = payload.get("features") or []
            _LOGGER.debug(
                "Régie Essence Québec: received %d station features", len(features)
            )
            self._geojson_cache = features
            self._geojson_cache_ts = now
            return features
