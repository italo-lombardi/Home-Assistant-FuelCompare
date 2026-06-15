"""GbFuelfinderProvider — UK Fuel Finder CSV mirror (OGL v3.0).

Source: Official UK Government Fuel Finder data mirrored as a CSV by
Matthew Gall at https://github.com/matthewgall/fuelfinder-archive.
No authentication or API key required.  Plain HTTPS GET returns the
full dataset (~7,975 stations, ~7.7 MB uncompressed) as CSV.

The underlying data is Crown copyright, Open Government Licence v3.0.
The official OAuth2 API is at fuel-finder.service.gov.uk; this provider
uses the zero-friction CSV mirror which is refreshed approximately twice
daily (~09:00 and ~15:30 UTC).

CSV column layout (exact names as of 2026-06-13)
-------------------------------------------------
  forecourt_update_timestamp               JS Date.toString() UTC timestamp
  forecourts.node_id                       64-char lowercase hex SHA-256 ID
  forecourts.trading_name                  Station name
  forecourts.brand_name                    Brand / retailer
  forecourts.is_motorway_service_station   "true"/"false"
  forecourts.is_supermarket_service_station "true"/"false"
  forecourts.public_phone_number           E.164 or empty
  forecourts.temporary_closure             "true"/"false"
  forecourts.permanent_closure             "true"/"false"
  forecourts.permanent_closure_date        ISO date or empty
  forecourts.location.postcode
  forecourts.location.address_line_1
  forecourts.location.address_line_2
  forecourts.location.city
  forecourts.location.county
  forecourts.location.country
  forecourts.location.latitude             decimal degrees string
  forecourts.location.longitude            decimal degrees string
  forecourts.fuel_price.E10               pence/litre string or empty
  forecourts.price_submission_timestamp.E10
  forecourts.price_change_effective_timestamp.E10
  (same three columns repeated for E5, B7S, B7P, B10, HVO)

Fuel mapping
------------
  E10  → unleaded          (standard petrol, E10 ethanol blend)
  E5   → premium_unleaded  (95 E5 super unleaded)
  B7S  → diesel            (standard diesel B7)
  B7P  → premium_diesel    (premium diesel, e.g. Shell V-Power Diesel)

Price units
-----------
Raw CSV values are pence per litre (e.g. "169.9000").  This provider
converts to pounds per litre (divide by 100) before storing so that
values are consistent with the EUR/litre convention used by other
providers in this integration (1 GBP/litre ≈ 1.17 EUR/litre; no
currency conversion is applied — the sensor will display GBP/L).

Timestamp format
----------------
All timestamps use the JS Date.toString() representation:
  "Thu Jun 11 2026 18:10:41 GMT+0000 (Coordinated Universal Time)"
Parsed with strptime using the exact pattern documented in the research
findings.  The parsed datetime is converted to ISO 8601 for lastupdated.

STATION_LOOKUP_MODE = location_search
--------------------------------------
The full CSV is fetched once per poll; stations within radius_km of the
configured (latitude, longitude) are returned sorted cheapest first by
the primary unleaded/diesel price.  The station node_id is used as the
stable station identifier.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time as _time
from datetime import datetime, timezone
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

_CSV_URL = (
    "https://raw.githubusercontent.com/matthewgall/fuelfinder-archive"
    "/refs/heads/main/data.csv"
)

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Encoding": "gzip, deflate",
}

# Generous timeout: ~7.7 MB CSV, GitHub CDN should serve compressed (~1–2 MB).
_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT * 6, 60))

# Timestamp format used by the JS Date.toString() representation in the CSV.
_TS_FORMAT = "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"

# Maximum price in pence/litre to accept as valid (outlier guard).
# Anything above 300 p/L (~£3.00/L) is almost certainly a data error.
_MAX_PENCE_PER_LITRE: float = 300.0

# CSV column names for each fuel type (price column only; timestamp columns
# are read but only used for lastupdated, not exposed individually).
_FUEL_COL: dict[str, str] = {
    "unleaded": "forecourts.fuel_price.E10",
    "premium_unleaded": "forecourts.fuel_price.E5",
    "diesel": "forecourts.fuel_price.B7S",
    "premium_diesel": "forecourts.fuel_price.B7P",
}

# Price submission timestamp columns — used to derive lastupdated.
_TS_COL: dict[str, str] = {
    "unleaded": "forecourts.price_submission_timestamp.E10",
    "premium_unleaded": "forecourts.price_submission_timestamp.E5",
    "diesel": "forecourts.price_submission_timestamp.B7S",
    "premium_diesel": "forecourts.price_submission_timestamp.B7P",
}


class GbFuelfinderProvider(BaseProvider):
    """Fetch UK fuel prices from the Fuel Finder CSV mirror (OGL v3.0).

    All ~7,975 stations are returned in a single CSV download, so the
    user selects their station by GPS location and radius.  STATION_LOOKUP_MODE
    is 'location_search'; async_list_stations() filters and returns the stations
    within the configured radius sorted cheapest-first.

    The stable station identifier is the ``forecourts.node_id`` 64-char hex
    SHA-256 string.

    Constructor
    -----------
    station_id:  node_id hex string (stored for the async_fetch path)
    county:      not used; accepted for API symmetry with other providers
    latitude:    WGS84 latitude of the home/reference location
    longitude:   WGS84 longitude of the home/reference location
    radius_km:   search radius in kilometres (default 10)
    """

    COUNTRY = "GB"
    PROVIDER_KEY = "gb_fuelfinder"
    LABEL = "Fuel Finder (UK)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"

    # Updated approximately twice daily; 6-hour poll matches the source refresh cadence.
    POLL_INTERVAL_SECONDS = 21600
    CURRENCY: ClassVar[str] = "£"

    # Class-level CSV cache shared across all instances (avoids re-downloading 7.7 MB).
    _csv_cache: ClassVar[str | None] = None
    _csv_cache_ts: ClassVar[float] = 0.0
    _csv_etag: ClassVar[str | None] = (
        None  # ETag for conditional GET (304 Not Modified)
    )
    _CSV_CACHE_TTL: ClassVar[int] = 21600

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "unleaded",
            "premium_unleaded",
            "diesel",
            "premium_diesel",
            "lastupdated",
            "name",
            "brand",
            "address",
            "latitude",
            "longitude",
            "is_open",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    REQUIRES_API_KEY = False

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        self._station_id = station_id
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch the CSV and return normalised data for the configured station.

        For CONFIG_MODE='location' the station_id parameter is the node_id
        selected during config flow setup (stored in the config entry).  The
        full CSV is fetched and the matching row is extracted.

        Raises:
            ProviderError: station node_id not found in the CSV dataset.
        """
        rows = await self._fetch_csv(session)
        row = _find_row_by_id(rows, station_id)
        if row is None:
            raise ProviderError(
                f"Station node_id '{station_id}' not found in UK Fuel Finder CSV. "
                "The station may have been removed from the dataset."
            )
        if row.get("forecourts.permanent_closure", "").strip().lower() == "true":
            raise ProviderError(f"Station {station_id} is permanently closed")
        data = _parse_row(row)
        if row.get("forecourts.temporary_closure", "").strip().lower() == "true":
            _LOGGER.warning("GB station %s is temporarily closed", station_id)
            data["is_open"] = False
        return data

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station trading name for the config flow, or None."""
        try:
            rows = await self._fetch_csv(session)
            row = _find_row_by_id(rows, station_id)
            if row:
                return row.get("forecourts.trading_name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (node_id, display_label) pairs for stations within radius.

        Expected kwargs:
            lat       — float, reference latitude
            lng       — float, reference longitude
            radius_km — float, search radius in km (default: self._radius_km)

        Returns a list sorted cheapest-first by the best available fuel price
        (diesel or unleaded).  Stations with no prices are appended at the end.
        """
        lat = float(kwargs.get("lat", self._latitude or 0.0))
        lng = float(kwargs.get("lng", self._longitude or 0.0))
        radius_km = float(kwargs.get("radius_km", self._radius_km))

        try:
            rows = await self._fetch_csv(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        candidates: list[tuple[str, str, float]] = []

        for row in rows:
            node_id = row.get("forecourts.node_id", "").strip()
            if not node_id:
                continue

            # Skip permanently closed stations.
            if row.get("forecourts.permanent_closure", "").strip().lower() == "true":
                continue

            row_lat = _safe_float(row.get("forecourts.location.latitude"))
            row_lng = _safe_float(row.get("forecourts.location.longitude"))
            if row_lat is None or row_lng is None:
                continue

            dist = _haversine_km(lat, lng, row_lat, row_lng)
            if dist > radius_km:
                continue

            name = (row.get("forecourts.trading_name") or "").strip()
            brand = (row.get("forecourts.brand_name") or "").strip()
            display_name = f"{brand} {name}".strip() if brand else name
            if not display_name:
                display_name = node_id[:8]

            diesel_pence = _parse_price_pence(row.get("forecourts.fuel_price.B7S"))
            petrol_pence = _parse_price_pence(row.get("forecourts.fuel_price.E10"))

            price_parts: list[str] = []
            if diesel_pence is not None:
                price_parts.append(f"Diesel {diesel_pence:.1f}p")
            if petrol_pence is not None:
                price_parts.append(f"Unleaded {petrol_pence:.1f}p")

            best_price = min(
                (p for p in [diesel_pence, petrol_pence] if p is not None),
                default=None,
            )
            sort_key = best_price if best_price is not None else 99999.0

            if price_parts:
                label = f"{display_name} — {' / '.join(price_parts)} ({dist:.1f} km)"
            else:
                label = f"{display_name} ({dist:.1f} km)"

            candidates.append((node_id, label, sort_key))

        candidates.sort(key=lambda x: x[2])
        return [(node_id, label) for node_id, label, _ in candidates]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_csv(self, session: ClientSession) -> list[dict[str, str]]:
        """Fetch the CSV from the GitHub mirror and parse it into a list of dicts.

        Returns a list of row dicts keyed by CSV column name.  Results are cached
        in-process for _CSV_CACHE_TTL seconds to avoid re-downloading the 7.7 MB
        file on every coordinator refresh.
        Raises aiohttp ClientError on network failure (coordinator converts to
        UpdateFailed).
        """
        now = _time.monotonic()
        if (
            GbFuelfinderProvider._csv_cache is not None
            and (now - GbFuelfinderProvider._csv_cache_ts)
            < GbFuelfinderProvider._CSV_CACHE_TTL
        ):
            _LOGGER.debug("UK Fuel Finder CSV: serving from in-process cache")
            text = GbFuelfinderProvider._csv_cache
        else:
            _LOGGER.debug("Fetching UK Fuel Finder CSV from %s", _CSV_URL)
            req_headers = dict(_HEADERS)
            if GbFuelfinderProvider._csv_etag:
                req_headers["If-None-Match"] = GbFuelfinderProvider._csv_etag
            async with session.get(
                _CSV_URL, headers=req_headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status == 304 and GbFuelfinderProvider._csv_cache is not None:
                    _LOGGER.debug(
                        "UK Fuel Finder CSV: 304 Not Modified — using cached version"
                    )
                    text = GbFuelfinderProvider._csv_cache
                    GbFuelfinderProvider._csv_cache_ts = now
                else:
                    resp.raise_for_status()
                    raw_bytes = await resp.read()
                    etag = resp.headers.get("ETag")
                    if etag:
                        GbFuelfinderProvider._csv_etag = etag
                    # Decode; GitHub raw CDN serves UTF-8 text.
                    text = raw_bytes.decode("utf-8", errors="replace")
                    GbFuelfinderProvider._csv_cache = text
                    GbFuelfinderProvider._csv_cache_ts = now

        def _parse():
            return list(csv.DictReader(io.StringIO(text)))

        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, _parse)
        _LOGGER.debug("UK Fuel Finder CSV: %d station rows loaded", len(rows))
        return rows


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_row_by_id(rows: list[dict[str, str]], node_id: str) -> dict[str, str] | None:
    """Return the CSV row whose node_id matches, or None."""
    for row in rows:
        if row.get("forecourts.node_id", "").strip() == node_id:
            return row
    return None


def _parse_price_pence(raw: str | None) -> float | None:
    """Parse a raw CSV price string (pence/litre) and return float pence, or None.

    Empty strings and prices outside the valid range are rejected.
    """
    if not raw or not raw.strip():
        return None
    try:
        val = float(raw.strip())
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > _MAX_PENCE_PER_LITRE:
        return None
    return val


def _pence_to_gbp(pence: float | None) -> float | None:
    """Convert pence/litre to GBP/litre, rounding to 4 decimal places."""
    if pence is None:
        return None
    return round(pence / 100.0, 4)


def _parse_js_timestamp(ts: str | None) -> str | None:
    """Parse a JS Date.toString() UTC timestamp to ISO 8601, or return None.

    Expected format: "Thu Jun 11 2026 18:10:41 GMT+0000 (Coordinated Universal Time)"
    Returns an ISO 8601 string with UTC timezone, e.g. "2026-06-11T18:10:41+00:00".
    """
    if not ts or not ts.strip():
        return None
    ts = ts.strip()
    try:
        dt = datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        _LOGGER.debug("Could not parse timestamp: %r", ts)
        return None


def _safe_float(value: str | None) -> float | None:
    """Safely convert a string to float, returning None on failure."""
    if value is None or not value.strip():
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_row(row: dict[str, str]) -> StationData:
    """Build a StationData dict from a single CSV row.

    Prices are converted from pence/litre to GBP/litre (divide by 100).
    lastupdated is set to the most recent price submission timestamp found
    across all four mapped fuel types.
    """
    # ── Fuel prices (pence → GBP/litre) ──────────────────────────────────────
    prices: dict[str, float | None] = {}
    for fuel_key, col in _FUEL_COL.items():
        pence = _parse_price_pence(row.get(col))
        prices[fuel_key] = _pence_to_gbp(pence)

    # ── lastupdated: most recent submission timestamp across fuel types ────────
    latest_ts: str | None = None
    latest_dt: datetime | None = None
    for fuel_key, ts_col in _TS_COL.items():
        raw_ts = row.get(ts_col)
        iso = _parse_js_timestamp(raw_ts)
        if iso:
            try:
                dt = datetime.fromisoformat(iso)
                if latest_dt is None or dt > latest_dt:
                    latest_dt = dt
                    latest_ts = iso
            except ValueError:
                pass

    # Fall back to the row-level forecourt_update_timestamp if no price ts found.
    if latest_ts is None:
        latest_ts = _parse_js_timestamp(row.get("forecourt_update_timestamp"))

    # ── Location ──────────────────────────────────────────────────────────────
    lat = _safe_float(row.get("forecourts.location.latitude"))
    lng = _safe_float(row.get("forecourts.location.longitude"))

    # ── Identity ──────────────────────────────────────────────────────────────
    name = (row.get("forecourts.trading_name") or "").strip() or None
    brand = (row.get("forecourts.brand_name") or "").strip() or None

    # Build address from available address components.
    addr_parts = [
        (row.get("forecourts.location.address_line_1") or "").strip(),
        (row.get("forecourts.location.address_line_2") or "").strip(),
        (row.get("forecourts.location.city") or "").strip(),
        (row.get("forecourts.location.postcode") or "").strip(),
    ]
    address: str | None = ", ".join(p for p in addr_parts if p) or None

    node_id = (row.get("forecourts.node_id") or "").strip() or None

    return {
        "unleaded": prices.get("unleaded"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "diesel": prices.get("diesel"),
        "premium_diesel": prices.get("premium_diesel"),
        "lastupdated": latest_ts,
        "name": name,
        "brand": brand,
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "is_open": True,  # default open; async_fetch overrides for closures
        "source_station_id": node_id,
    }
