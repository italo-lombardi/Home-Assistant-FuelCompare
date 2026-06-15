"""IsFuelProvider — Icelandic fuel prices from Gasvaktin.

Gasvaktin (https://gasvaktin.is / https://github.com/gasvaktin/gasvaktin) is an
open community project that scrapes all major Icelandic fuel retailers and
publishes station-level prices as a JSON file on GitHub.  The data is updated
automatically every 15 minutes when prices change, via git commits.  It has been
continuously active for many years and covers all 6 major retailers: Atlantsolía,
Costco Iceland, N1, Olís, Orkan, and ÓB.

Endpoint
--------
GET https://raw.githubusercontent.com/gasvaktin/gasvaktin/master/vaktin/gas.min.json

Returns a JSON object with a top-level ``stations`` array.  No authentication
or API key is required.  The raw GitHub URL is publicly accessible.

Response shape (one station object)::

    {
      "key":                   "AT_001",
      "name":                  "Atlantsolía Álftanes",
      "company":               "Atlantsolía",
      "bensin95":              191.3,
      "bensin95_discount":     188.3,
      "diesel":                227.6,
      "diesel_discount":       224.6,
      "geo": {
        "lat": 64.0328,
        "lon": -22.0328
      }
    }

Confirmed live data as of 2026-06-13: 246 stations, bensin95 range 191.3–230.9 ISK,
diesel range 227.6–265.9 ISK.

Fuel type mapping
-----------------
API field            → StationData key   Notes
---------              ----------------   -----
bensin95             → unleaded           Standard 95 octane petrol, ISK/litre
bensin95_discount    → premium_unleaded   Discounted 95 (loyalty-card price)
diesel               → diesel             Standard diesel, ISK/litre
diesel_discount      → premium_diesel     Discounted diesel (loyalty-card price)

The "discount" fields are the loyalty-card / fleet-card prices available at
many Icelandic stations.  They are mapped to the closest StationData keys with
the understanding that sensor labels clarify the distinction.

Price values
------------
All prices are in ISK/litre (Icelandic króna).  The values are typically in
the range 150–300 ISK/litre, so the >10 → /100 cents guard used by the
fuelcompare.ie provider must NOT be applied.

Station identity field mapping
------------------------------
API field      → StationData key
---------        ----------------
key            → source_station_id
name           → name
company        → brand, tablename
geo.lat        → latitude
geo.lon        → longitude

CONFIG_MODE = 'station_id'
  The station ``key`` string (e.g. ``'AT_001'``) is used as station_id.

STATION_LOOKUP_MODE = 'location_search'
  async_list_stations() downloads the full dataset, applies haversine
  distance filtering, and returns nearby stations sorted nearest-first.

Poll interval
-------------
POLL_INTERVAL_SECONDS = 900 (15 minutes) to match the upstream commit cadence.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

_DATA_URL = (
    "https://raw.githubusercontent.com/gasvaktin/gasvaktin/master/vaktin/gas.min.json"
)

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)


def _parse_price(raw: Any) -> float | None:
    """Parse a Gasvaktin price field.

    Prices are ISK/litre floats (e.g. 191.3).  Zero and negative values are
    treated as absent.  The >10 → /100 cents guard is NOT applied — Icelandic
    fuel prices are genuinely in the range 150–300 ISK/litre.

    Args:
        raw: Price value from the API (float, int, None, or string).

    Returns:
        Rounded float price in ISK/litre, or None if invalid/unavailable.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val, 2)


def _parse_station(station: dict[str, Any]) -> StationData:
    """Build a StationData dict from a Gasvaktin station record.

    Args:
        station: Single station dict from the ``stations`` array.

    Returns:
        Populated StationData dict.
    """
    geo: dict[str, Any] = station.get("geo") or {}
    lat_raw = geo.get("lat")
    lon_raw = geo.get("lon")
    try:
        latitude: float | None = float(lat_raw) if lat_raw is not None else None
    except (ValueError, TypeError):
        latitude = None
    try:
        longitude: float | None = float(lon_raw) if lon_raw is not None else None
    except (ValueError, TypeError):
        longitude = None

    company: str | None = station.get("company") or None

    return {
        # Fuel prices (ISK/litre)
        "unleaded": _parse_price(station.get("bensin95")),
        "premium_unleaded": _parse_price(station.get("bensin95_discount")),
        "diesel": _parse_price(station.get("diesel")),
        "premium_diesel": _parse_price(station.get("diesel_discount")),
        # Station identity
        "name": station.get("name") or None,
        "brand": company,
        "tablename": company,
        "latitude": latitude,
        "longitude": longitude,
        # Passthrough
        "source_station_id": station.get("key") or "",
    }


def _find_station(
    stations: list[dict[str, Any]], station_id: str
) -> dict[str, Any] | None:
    """Return the station record with the matching ``key``, or None.

    Args:
        stations:   List of station dicts from the ``stations`` array.
        station_id: Target key string (e.g. ``'AT_001'``).

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if station.get("key") == station_id:
            return station
    return None


class IsFuelProvider(BaseProvider):
    """Fetch Icelandic fuel prices from the Gasvaktin community project.

    Gasvaktin publishes station-level prices for all major Icelandic fuel
    retailers as a JSON file on GitHub, updated every 15 minutes via
    automated git commits.  246 stations are currently tracked.

    The station ``key`` field (e.g. ``'AT_001'``) is used as station_id.

    Station lookup
    --------------
    STATION_LOOKUP_MODE = 'location_search': the config flow supplies
    lat/lng + radius_km to async_list_stations(), which downloads the full
    dataset, applies haversine filtering, and returns (key, label) tuples.

    Fetch
    -----
    async_fetch() downloads the full dataset and finds the matching station
    by key.  At ~246 stations the full payload is small (~50 KB) so a full
    download per poll cycle is acceptable.

    Usage
    -----
    provider = IsFuelProvider(
        station_id="AT_001",
        latitude=64.0328,
        longitude=-22.0328,
        radius_km=10.0,
    )
    """

    COUNTRY = "IS"
    PROVIDER_KEY = "is_fuel"
    LABEL = "Gasvaktin (Iceland)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 900  # 15 minutes — matches upstream commit cadence
    CURRENCY: ClassVar[str] = "kr"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices (ISK/litre)
            "unleaded",  # bensin95 — standard 95 octane petrol
            "premium_unleaded",  # bensin95_discount — loyalty-card petrol price
            "diesel",  # diesel — standard diesel
            "premium_diesel",  # diesel_discount — loyalty-card diesel price
            # Station identity
            "name",
            "brand",
            "latitude",
            "longitude",
        }
    )

    STATION_ID_HINT = (
        "Enter the Gasvaktin station key (e.g. 'AT_001' for Atlantsolía).  "
        "Use the location search in the config flow to find your nearest "
        "stations, or browse https://github.com/gasvaktin/gasvaktin to look "
        "up station keys directly."
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
            station_id:  Gasvaktin station key string (e.g. ``'AT_001'``).
            latitude:    WGS84 latitude of the search centre used by
                         async_list_stations.
            longitude:   WGS84 longitude of the search centre.
            radius_km:   Search radius in kilometres for async_list_stations.
        """
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
        """Fetch and return normalised station data for station_id.

        Downloads the full Gasvaktin snapshot and finds the station whose
        ``key`` matches station_id.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Gasvaktin station key (e.g. ``'AT_001'``).

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: station_id not found in the dataset, or the
                           response could not be parsed as expected JSON.
        """
        stations = await self._fetch_all_stations(session)

        station = _find_station(stations, station_id)
        if station is None:
            raise ProviderError(
                f"Gasvaktin station key '{station_id}' not found in the "
                "snapshot.  Verify the key is correct by using the location "
                "search in the config flow, or browse "
                "https://github.com/gasvaktin/gasvaktin."
            )

        _LOGGER.debug(
            "Gasvaktin parsed data for station %s: "
            "bensin95=%s diesel=%s bensin95_discount=%s diesel_discount=%s",
            station_id,
            station.get("bensin95"),
            station.get("diesel"),
            station.get("bensin95_discount"),
            station.get("diesel_discount"),
        )

        return _parse_station(station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Downloads the full dataset and returns the ``name`` field for the
        matching station.  Returns None on any failure so the config flow
        can fall back to ``'Station {id}'``.

        Args:
            session:    aiohttp ClientSession.
            station_id: Gasvaktin station key.
        """
        try:
            stations = await self._fetch_all_stations(session)
            record = _find_station(stations, station_id)
            if record:
                name: str | None = record.get("name") or None
                company: str | None = record.get("company") or None
                if company and name:
                    return f"{company} — {name}"
                return name or company or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_key, display_label) pairs for the config flow station picker.

        Downloads the full Gasvaktin snapshot, filters by haversine distance,
        and returns stations within radius_km sorted alphabetically by label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Search centre latitude (overrides constructor value).
            lng:       Search centre longitude (overrides constructor value).
            radius_km: Search radius in kilometres (overrides constructor value).

        Returns:
            List of (key, "Name (#key[:8])") tuples sorted alphabetically by
            label.  Empty list on any failure.
        """
        raw_lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude
        raw_lng = (
            kwargs.get("lng") if kwargs.get("lng") is not None else self._longitude
        )

        if raw_lat is None or raw_lng is None:
            _LOGGER.debug(
                "async_list_stations called without lat/lng for Gasvaktin; "
                "returning empty list"
            )
            return []

        lat = float(raw_lat)
        lng = float(raw_lng)
        radius_km = float(
            kwargs.get("radius_km")
            if kwargs.get("radius_km") is not None
            else self._radius_km
        )

        try:
            stations = await self._fetch_all_stations(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "async_list_stations failed to fetch Gasvaktin dataset: %s", err
            )
            return []

        result: list[tuple[str, str]] = []
        for station in stations:
            s_lat_raw = (station.get("geo") or {}).get("lat")
            s_lon_raw = (station.get("geo") or {}).get("lon")
            try:
                s_lat = float(s_lat_raw) if s_lat_raw is not None else None
                s_lng = float(s_lon_raw) if s_lon_raw is not None else None
            except (ValueError, TypeError):
                continue

            if s_lat is None or s_lng is None:
                continue

            dist = haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            key: str | None = station.get("key")
            if not key:
                continue

            name: str = station.get("name") or key

            label = f"{name} (#{key[:8]})"

            result.append((key, label))

        result.sort(key=lambda x: x[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_stations(
        self,
        session: ClientSession,
    ) -> list[dict[str, Any]]:
        """Download and return the Gasvaktin station array.

        Args:
            session: aiohttp ClientSession.

        Returns:
            List of station dicts from the ``stations`` key.

        Raises:
            ProviderError: Response was not the expected JSON structure.
            aiohttp.ClientError: On network errors (propagates to coordinator).
        """
        _LOGGER.debug("Fetching Gasvaktin station snapshot from %s", _DATA_URL)

        async with session.get(
            _DATA_URL,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as response:
            response.raise_for_status()
            payload: Any = await response.json(content_type=None)

        if not isinstance(payload, dict):
            raise ProviderError(
                f"Gasvaktin API returned unexpected format "
                f"(expected JSON object, got {type(payload).__name__})"
            )

        stations = payload.get("stations")
        if not isinstance(stations, list):
            raise ProviderError(
                "Gasvaktin API response missing 'stations' array.  "
                "The upstream data format may have changed."
            )

        return stations
