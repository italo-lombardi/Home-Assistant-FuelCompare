"""ChTcsProvider — Swiss fuel prices from the TCS Benzinpreis-Radar.

Source: Touring Club Schweiz (TCS), community-sourced crowd-reported prices.
Website: https://benzin.tcs.ch
Endpoint: POST https://europe-west6-tcs-digitalbackend.cloudfunctions.net/benzinGetStationByBbox

This is an unofficial/undocumented Google Cloud Function that backs the TCS
public-facing Benzinpreis-Radar website.  No API key is required.  The Cloud
Function validates the ``Origin`` and ``Referer`` headers against benzin.tcs.ch,
so those headers are sent on every request.

Request format
--------------
A JSON POST body is sent with these fields:

    {
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "zoom": 15,
        "pixelRatio": 1,
        "filters": {"fuel": "<FUEL_TYPE>"}
    }

Response format
---------------
A JSON object with a ``data`` key containing a list of station objects.
Each station object may represent an individual station or a cluster.
Cluster records have a non-null ``cluster`` field and are ignored.

Individual station field mapping
---------------------------------
API field        → StationData key   Notes
-----------        ----------------   -----
id               → source_station_id  string identifier
brand            → brand / tablename  string e.g. "AGROLA"
latitude         → latitude           float WGS84
longitude        → longitude          float WGS84
displayName      → name               string
formattedAddress → address            string
price            → fuel key           float CHF/litre (e.g. 1.879)
fiability        → price_confidence   "CONFIDENT" | other values
isCheapest       → (passthrough)      bool, not surfaced as entity

Coverage strategy — 4×4 sub-bbox grid
--------------------------------------
The API appears to apply server-side limits on the number of stations returned
per request for large bounding boxes.  To obtain full Swiss coverage, the
Switzerland bounding box (lon 5.96°–10.49°, lat 45.82°–47.81°) is split into
a 4×4 grid of 16 sub-boxes, and a separate POST is made for each sub-box.
Results are merged and de-duplicated by station ``id``.  The approach mirrors
the existing HA custom integration at https://github.com/froguinou/hass-tcs-carburant.

Fuel types
----------
TCS supports the following fuel type strings (passed in filters.fuel):
  SP95     → unleaded    (Super 95 / E5)
  SP98     → premium_unleaded  (Super Plus / SP98)
  DIESEL   → diesel

Note on prices
--------------
Prices are in CHF/litre (e.g. 1.879).  Switzerland uses the Swiss franc
(CHF), not EUR.  Prices are stored as CHF/litre in the StationData dict.
The ``price_confidence`` field mirrors the API ``fiability`` field; the
value "CONFIDENT" indicates a community-verified price.

Note on data freshness
----------------------
Prices are crowd-sourced via the TCS Benzinpreis-Radar app and website.
Individual prices may lag real pump updates by hours or days.  The
``fiability`` field from the API can be used to filter by reliability.

Rate limiting / stability
-------------------------
The API is an undocumented Google Cloud Function.  It could be changed or
rate-limited without notice.  The implementation catches all network errors
and returns None prices gracefully rather than raising to the coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

_API_URL = (
    "https://europe-west6-tcs-digitalbackend.cloudfunctions.net/benzinGetStationByBbox"
)

# Headers recommended to avoid rejection by the Cloud Function.
# The function validates Origin/Referer against benzin.tcs.ch.
_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://benzin.tcs.ch",
    "Referer": "https://benzin.tcs.ch/",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# Switzerland bounding box: [min_lon, min_lat, max_lon, max_lat]
# Covers the full Swiss territory (mainland + Ticino).
_CH_BBOX = (5.96, 45.82, 10.49, 47.81)

# Grid splits per axis.  4×4 = 16 sub-boxes per fuel type per poll cycle.
# This mirrors the approach used by froguinou/hass-tcs-carburant.
_GRID_SPLITS = 4

# TCS fuel type string → StationData key
_FUEL_MAP: dict[str, str] = {
    "SP95": "unleaded",
    "SP98": "premium_unleaded",
    "DIESEL": "diesel",
}

# Ordered list of TCS fuel type strings to fan out per poll cycle.
_FUEL_TYPES: tuple[str, ...] = ("SP95", "SP98", "DIESEL")


def _build_sub_bboxes(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    splits: int,
) -> list[list[float]]:
    """Divide a bounding box into a splits×splits grid of sub-boxes.

    Args:
        min_lon, min_lat: SW corner.
        max_lon, max_lat: NE corner.
        splits: Number of divisions per axis (e.g. 4 → 16 sub-boxes total).

    Returns:
        List of [min_lon, min_lat, max_lon, max_lat] sub-box lists, suitable
        for direct use in the API ``bbox`` field.
    """
    lon_step = (max_lon - min_lon) / splits
    lat_step = (max_lat - min_lat) / splits
    boxes: list[list[float]] = []
    for row in range(splits):
        for col in range(splits):
            boxes.append(
                [
                    min_lon + col * lon_step,
                    min_lat + row * lat_step,
                    min_lon + (col + 1) * lon_step,
                    min_lat + (row + 1) * lat_step,
                ]
            )
    return boxes


class ChTcsProvider(BaseProvider):
    """Fetch Swiss fuel prices from the TCS Benzinpreis-Radar API.

    The user configures a centre latitude/longitude and optional radius.
    On each poll cycle the provider fans out 3 fuel-type × 16 sub-bbox = 48
    POST requests concurrently, de-duplicates by station ``id``, filters to
    stations within the configured radius, and returns data for the configured
    station.

    CONFIG_MODE is 'location': the user supplies a lat/lng + radius_km and
    the config flow presents a list of nearby stations sorted by SP95 price.
    The chosen station's ``id`` is stored as station_id in the config entry.
    """

    COUNTRY = "CH"
    PROVIDER_KEY = "ch_tcs"
    LABEL = "TCS Benzinpreis-Radar (Switzerland)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600  # crowd-sourced data; 1 hour cadence is adequate
    REQUIRES_API_KEY = False

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "unleaded",
            "premium_unleaded",
            "diesel",
            "name",
            "brand",
            "address",
            "latitude",
            "longitude",
            "price_confidence",
            "lastupdated",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the TCS station ID.  Use the location search to browse stations "
        "near your home coordinates and select one from the list."
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
            station_id:  TCS station identifier (the ``id`` field from the API).
            county:      Not used for fetching (full-Switzerland grid); kept for
                         structural compatibility with other providers.
            latitude:    User's home latitude.  Used to filter stations by radius
                         during async_list_stations.
            longitude:   User's home longitude.
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
        """Fetch merged station data for the configured station.

        Fans out 3 fuel-type × 16 sub-bbox requests, de-dupes by station id,
        and returns a StationData dict for the requested station.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: TCS station id string.

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: Station not found anywhere in the Switzerland grid.
        """
        merged = await self._fetch_all_fuels(session)

        station = merged.get(station_id)
        if station is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in TCS Benzinpreis-Radar "
                "Switzerland data.  Verify the station ID is correct, or "
                "reconfigure using the location search to select a new station."
            )
        return _build_station_data(station_id, station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Args:
            session:    aiohttp ClientSession.
            station_id: TCS station id string.

        Returns:
            Station display name, or None on failure / not found.
        """
        try:
            merged = await self._fetch_all_fuels(session)
            station = merged.get(station_id)
            if station:
                return (
                    station.get("_meta", {}).get("displayName")
                    or station.get("_meta", {}).get("brand")
                    or None
                )
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
        """Return (station_id, display_label) pairs for stations near the user.

        Filters stations within radius_km of the supplied coordinates and
        returns them sorted cheapest-SP95-first (stations with no price last).

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude.
            lng:       Centre longitude.
            radius_km: Search radius in kilometres.

        Returns:
            Ordered list of (station_id, label) tuples.  Empty list on failure.
        """
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")
        # Use is-not-None checks (not falsy) so that 0.0 coordinates are valid
        if lat is None:
            lat = self._latitude
        if lng is None:
            lng = self._longitude

        if lat is None or lng is None:
            _LOGGER.debug(
                "async_list_stations: no coordinates provided — returning empty list"
            )
            return []

        lat = float(lat)
        lng = float(lng)
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        try:
            merged = await self._fetch_all_fuels(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        result: list[tuple[str, str, float]] = []

        for sid, raw in merged.items():
            meta = raw.get("_meta", {})
            slat = meta.get("latitude")
            slng = meta.get("longitude")
            if slat is None or slng is None:
                continue

            try:
                dist = haversine_km(lat, lng, float(slat), float(slng))
            except (ValueError, TypeError):
                continue

            if dist > radius_km:
                continue

            name = meta.get("displayName") or meta.get("brand") or f"Station {sid}"
            address = meta.get("formattedAddress") or ""
            label_name = f"{name} — {address}" if address else name

            prices = raw.get("prices", {})
            unleaded = prices.get("unleaded")
            diesel = prices.get("diesel")

            price_parts: list[str] = []
            if unleaded is not None:
                price_parts.append(f"SP95 CHF{unleaded:.3f}")
            if diesel is not None:
                price_parts.append(f"Diesel CHF{diesel:.3f}")

            if price_parts:
                label = f"{label_name} — {' / '.join(price_parts)}"
                # Sort by SP95 price; fall back to diesel; no price → end
                sort_key = (
                    unleaded
                    if unleaded is not None
                    else (diesel if diesel is not None else 9999.0)
                )
            else:
                label = label_name
                sort_key = 9999.0

            result.append((sid, label, sort_key))

        result.sort(key=lambda x: x[2])
        return [(sid, label) for sid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_fuels(
        self,
        session: ClientSession,
    ) -> dict[str, dict[str, Any]]:
        """Fan out requests for all fuel types over the Switzerland grid.

        Makes _GRID_SPLITS² × len(_FUEL_TYPES) concurrent POST requests,
        merges the station records de-duplicated by id.

        Returns:
            Dict keyed by station id (str).  Each value is a merged station
            record with a ``prices`` sub-dict and a ``_meta`` sub-dict.
        """
        sub_bboxes = _build_sub_bboxes(*_CH_BBOX, _GRID_SPLITS)
        tasks = [
            self._fetch_bbox(session, bbox=bbox, fuel=fuel)
            for fuel in _FUEL_TYPES
            for bbox in sub_bboxes
        ]
        results: list[list[dict] | None] = list(await asyncio.gather(*tasks))

        merged: dict[str, dict[str, Any]] = {}
        fuel_idx = 0
        bbox_count = len(sub_bboxes)

        for i, stations in enumerate(results):
            # Map result index back to fuel type
            fuel = _FUEL_TYPES[i // bbox_count]
            data_key = _FUEL_MAP[fuel]

            if not stations:
                continue

            for station in stations:
                sid = station.get("id")
                if not sid:
                    continue
                sid = str(sid)

                if sid not in merged:
                    merged[sid] = {
                        "_meta": {
                            "latitude": station.get("latitude"),
                            "longitude": station.get("longitude"),
                            "displayName": station.get("displayName"),
                            "brand": station.get("brand"),
                            "formattedAddress": station.get("formattedAddress"),
                            "fiability": station.get("fiability"),
                            "isCheapest": station.get("isCheapest"),
                        },
                        "prices": {},
                        "fiability_by_fuel": {},
                    }

                price_raw = station.get("price")
                price = _parse_price(price_raw)
                if price is not None:
                    merged[sid]["prices"][data_key] = price
                    merged[sid]["fiability_by_fuel"][data_key] = station.get(
                        "fiability"
                    )

        _ = fuel_idx  # silence unused-variable warning
        return merged

    async def _fetch_bbox(
        self,
        session: ClientSession,
        bbox: list[float],
        fuel: str,
    ) -> list[dict] | None:
        """POST a single bbox+fuel request to the TCS Cloud Function.

        Args:
            session: aiohttp ClientSession.
            bbox:    [min_lon, min_lat, max_lon, max_lat].
            fuel:    TCS fuel type string (e.g. 'SP95', 'DIESEL').

        Returns:
            List of individual station dicts (clusters excluded), or None on
            any network/HTTP error so the coordinator's stale-retention
            behaviour works correctly.
        """
        body = {
            "bbox": bbox,
            "zoom": 15,
            "pixelRatio": 1,
            "filters": {"fuel": fuel},
        }
        _LOGGER.debug(
            "Fetching TCS bbox %s fuel=%s",
            [round(v, 4) for v in bbox],
            fuel,
        )
        try:
            async with session.post(
                _API_URL,
                json=body,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload: dict = await response.json(content_type=None)
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching TCS bbox fuel=%s: %s",
                fuel,
                err,
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error fetching TCS bbox fuel=%s: %s",
                fuel,
                err,
            )
            return None

        data = payload.get("data") or []
        if not isinstance(data, list):
            return []

        # Filter out cluster records — only return individual stations
        return [s for s in data if s.get("cluster") is None]


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_price(raw: Any) -> float | None:
    """Parse a price value to a positive float CHF/litre, or None.

    Args:
        raw: Raw value from the API (float, int, str, or None).

    Returns:
        Float price rounded to 3 decimal places, or None when the value is
        absent, zero, negative, or not parseable as a number.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val, 3)


def _build_station_data(station_id: str, raw: dict[str, Any]) -> StationData:
    """Build a StationData dict from a merged TCS station record.

    Args:
        station_id: The TCS station id string.
        raw:        Merged station dict with ``_meta``, ``prices``, and
                    ``fiability_by_fuel`` sub-dicts (from ``_fetch_all_fuels``).

    Returns:
        Populated StationData dict.
    """
    meta: dict[str, Any] = raw.get("_meta", {})
    prices: dict[str, float | None] = raw.get("prices", {})
    fiability_by_fuel: dict[str, str | None] = raw.get("fiability_by_fuel", {})

    # Overall fiability: prefer SP95, then diesel, then SP98
    fiability: str | None = (
        fiability_by_fuel.get("unleaded")
        or fiability_by_fuel.get("diesel")
        or fiability_by_fuel.get("premium_unleaded")
        or meta.get("fiability")
        or None
    )

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

    return {
        "unleaded": prices.get("unleaded"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "diesel": prices.get("diesel"),
        "name": meta.get("displayName") or None,
        "brand": meta.get("brand") or None,
        "tablename": meta.get("brand") or None,
        "address": meta.get("formattedAddress") or None,
        "latitude": lat,
        "longitude": lng,
        "price_confidence": fiability,
        "lastupdated": None,  # TCS API does not expose per-station timestamps
        "source_station_id": station_id,
    }
