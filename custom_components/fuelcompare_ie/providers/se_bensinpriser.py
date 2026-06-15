"""SEBensinpriserProvider — Swedish fuel prices from Bensinpriser.nu.

Bensinpriser.nu is a community-driven fuel price tracker for Sweden.
Prices are crowd-sourced and shown for a maximum of 7 days per station;
after 7 days with no new reports the price is removed.

Endpoint
--------
GET https://bensinpriser.nu/karta/data
    Returns a JSON array of all registered Swedish fuel stations (~3 000).
    No API key required.  No documented rate limit; the site serves the
    full dataset to the Leaflet map on every page load.

    Fields per station:
      id           — int, internal station identifier
      lat          — float, WGS84 latitude
      lng          — float, WGS84 longitude
      company      — str, brand/operator name (e.g. "St1", "Preem")
      address      — str, street address
      commune      — str, municipality (e.g. "Göteborg")
      county       — str, county/region (e.g. "Västra Götalands län")
      link         — str, relative URL path to station detail page
      price95      — float|null, petrol 95 (E10) price in SEK/litre
      priceDiesel  — float|null, diesel price in SEK/litre
      priceEtanol  — float|null, E85/ethanol price in SEK/litre
      priceBiodiesel — float|null, biodiesel price in SEK/litre
      countyLink   — str, URL slug for the county
      communeLink  — str, URL slug for the municipality
      companyLink  — str, URL slug for the company

Discovered via the map page (https://bensinpriser.nu/karta/) which
loads station data with ``$.getJSON("/karta/data", ...)``.

API investigation notes
-----------------------
- GET /api/stations?lat={lat}&lng={lng}  → HTTP 404 (endpoint does not exist)
- https://api.bensinpriser.nu/v1/stations → DNS lookup failure (subdomain
  does not exist)
- The only working data endpoint is /karta/data — a full-dataset dump.

Strategy
--------
Because the API returns all stations in a single response (no proximity
filter), distance filtering is performed client-side using haversine_km
from .base.  The full payload (~3 000 items, typically ~200 KB) is
fetched once per poll cycle and cached on the session; subsequent calls
within the same poll window reuse the cached copy.

STATION_LOOKUP_MODE = "location_search":
  async_list_stations() downloads the full dataset, applies haversine
  filtering, and returns stations within the configured radius sorted
  cheapest-first by petrol 95 price.

CONFIG_MODE = "station_id":
  The station ``id`` integer (stored as a string) is used as station_id.
  async_fetch() downloads the full dataset and finds the matching record.

Price normalisation
-------------------
Bensinpriser.nu prices are already in SEK/litre (e.g. 17.54).  No cents
conversion is applied.  price95 maps to 'unleaded' (standard petrol E10).
priceDiesel maps to 'diesel'.  priceEtanol maps to 'e85' (E85 flex fuel).

POLL_INTERVAL_SECONDS = 3600 (1 hour) as specified.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

_DATA_URL = "https://bensinpriser.nu/karta/data"
_STATION_BASE_URL = "https://bensinpriser.nu"

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
    "Referer": "https://bensinpriser.nu/karta/",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)


def _parse_price(raw: Any) -> float | None:
    """Parse a Bensinpriser.nu price field.

    Returns None when the value is None, zero, or an unparseable value.
    Prices are already in SEK/litre — no cents conversion applied.

    Args:
        raw: Raw price value from the API (float, int, None, or string).

    Returns:
        Rounded float price in SEK/litre, or None if invalid/unavailable.
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


def _parse_station(station: dict[str, Any]) -> StationData:
    """Build a StationData dict from a Bensinpriser.nu station record.

    Args:
        station: Single station dict from the /karta/data array.

    Returns:
        Populated StationData dict.
    """
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

    link: str | None = station.get("link") or None
    website: str | None = f"{_STATION_BASE_URL}{link}" if link else None

    return {
        "unleaded": _parse_price(station.get("price95")),
        "diesel": _parse_price(station.get("priceDiesel")),
        "e85": _parse_price(station.get("priceEtanol")),
        "name": station.get("company") or None,
        "brand": station.get("company") or None,
        "address": station.get("address") or None,
        "county": station.get("county") or None,
        "latitude": latitude,
        "longitude": longitude,
        "website": website,
        "lastupdated": None,  # Bensinpriser.nu does not return per-price timestamps
        "source_station_id": str(station.get("id", "")),
    }


class SEBensinpriserProvider(BaseProvider):
    """Fetch Swedish fuel prices from Bensinpriser.nu.

    The full station dataset (~3 000 stations) is downloaded from
    /karta/data once per poll cycle.  Stations are identified by their
    integer ``id`` field (stored as a string in the config entry).

    Usage
    -----
    STATION_LOOKUP_MODE = 'location_search': the config flow supplies
    lat/lng + radius_km to async_list_stations(), which filters the full
    dataset by haversine distance and returns nearby stations.

    CONFIG_MODE = 'station_id': the user selects a station from the picker;
    async_fetch() looks up the selected station id in the full dataset.
    """

    COUNTRY = "SE"
    PROVIDER_KEY = "se_bensinpriser"
    LABEL = "Bensinpriser.nu (Sweden)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600  # 1 hour as specified
    CURRENCY: ClassVar[str] = "SEK/L"

    CAPABILITIES: frozenset[str] = frozenset(
        {
            # Fuel prices
            "unleaded",
            "diesel",
            "e85",
            # Station identity
            "name",
            "brand",
            "address",
            "county",
            "latitude",
            "longitude",
            "website",
            # Diagnostic / coordinator-managed
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the Bensinpriser.nu station ID (integer).  Use the station "
        "location search in the config flow to find your nearest stations "
        "and select one from the list."
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
            station_id:  Bensinpriser.nu station id integer as a string
                         (e.g. ``'13'``).
            latitude:    WGS84 latitude of the search centre used by
                         async_list_stations (stored for convenience).
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

        Downloads the full /karta/data dataset and finds the station whose
        ``id`` matches station_id.  The dataset is not cached between poll
        cycles (the coordinator manages polling cadence via POLL_INTERVAL_SECONDS).

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Bensinpriser.nu station id (integer as string).

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: station_id not found in the dataset, or the
                           dataset could not be parsed as a JSON array.
        """
        stations = await self._fetch_all_stations(session)

        station = _find_station(stations, station_id)
        if station is None:
            raise ProviderError(
                f"Bensinpriser.nu station id '{station_id}' not found in the "
                "full dataset.  Verify the id is correct by using the location "
                "search in the config flow, or check "
                "https://bensinpriser.nu/karta/ directly."
            )

        _LOGGER.debug(
            "Bensinpriser.nu parsed data for station %s: unleaded=%s diesel=%s e85=%s",
            station_id,
            station.get("price95"),
            station.get("priceDiesel"),
            station.get("priceEtanol"),
        )

        return _parse_station(station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Downloads the full dataset and returns the ``company`` field for
        the matching station.  Returns None on any failure so the config
        flow can fall back to ``'Station {id}'``.

        Args:
            session:    aiohttp ClientSession.
            station_id: Bensinpriser.nu station id (integer as string).
        """
        try:
            stations = await self._fetch_all_stations(session)
            record = _find_station(stations, station_id)
            if record:
                name: str | None = record.get("company") or None
                address: str | None = record.get("address") or None
                if name and address:
                    return f"{name} — {address}"
                return name or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the config flow station picker.

        Downloads the full /karta/data dataset, filters by haversine distance,
        and returns stations within radius_km sorted cheapest-first by petrol 95
        price (stations with no price sorted last).

        Args:
            session:   aiohttp ClientSession.
            lat:       Search centre latitude (overrides constructor value).
            lng:       Search centre longitude (overrides constructor value).
            radius_km: Search radius in kilometres (overrides constructor value).

        Returns:
            List of (str(id), "Company — Address — 95: 17.54 kr / Diesel: 19.84 kr")
            tuples sorted cheapest-first.  Empty list on any failure.
        """
        lat: float | None = (
            kwargs["lat"] if kwargs.get("lat") is not None else self._latitude
        )  # type: ignore[assignment]
        lng: float | None = (
            kwargs["lng"] if kwargs.get("lng") is not None else self._longitude
        )  # type: ignore[assignment]
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        if lat is None or lng is None:
            _LOGGER.debug(
                "async_list_stations called without lat/lng for Bensinpriser.nu; "
                "returning empty list"
            )
            return []

        try:
            stations = await self._fetch_all_stations(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "async_list_stations failed to fetch Bensinpriser.nu dataset: %s", err
            )
            return []

        result: list[tuple[str, str, float]] = []
        for station in stations:
            s_lat_raw = station.get("lat")
            s_lng_raw = station.get("lng")
            try:
                s_lat = float(s_lat_raw) if s_lat_raw is not None else None
                s_lng = float(s_lng_raw) if s_lng_raw is not None else None
            except (ValueError, TypeError):
                continue

            if s_lat is None or s_lng is None:
                continue

            dist = haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            sid = station.get("id")
            if sid is None:
                continue
            uid = str(sid)

            company: str = station.get("company") or ""
            address: str = station.get("address") or ""
            commune: str = station.get("commune") or ""

            # Build display name
            if company and address:
                display_name = f"{company} — {address}"
            elif company:
                display_name = company
            elif address:
                display_name = address
            else:
                display_name = uid

            if commune:
                display_name = f"{display_name} ({commune})"

            # Prices
            p95 = _parse_price(station.get("price95"))
            p_diesel = _parse_price(station.get("priceDiesel"))

            price_parts: list[str] = []
            if p95 is not None:
                price_parts.append(f"95: {p95:.2f} kr")
            if p_diesel is not None:
                price_parts.append(f"Diesel: {p_diesel:.2f} kr")

            sort_key: float = min(
                (p for p in (p95, p_diesel) if p is not None),
                default=float("inf"),
            )

            label = (
                f"{display_name} — {' / '.join(price_parts)}"
                if price_parts
                else display_name
            )

            result.append((uid, label, sort_key))

        result.sort(key=lambda x: x[2])
        return [(uid, label) for uid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_stations(
        self,
        session: ClientSession,
    ) -> list[dict[str, Any]]:
        """Download and return the full /karta/data station array.

        Args:
            session: aiohttp ClientSession.

        Returns:
            List of station dicts.

        Raises:
            ProviderError: Response was not a JSON array or the request
                           returned an HTTP error.
            aiohttp.ClientError: On network errors (propagates to coordinator).
        """
        _LOGGER.debug(
            "Fetching Bensinpriser.nu full station dataset from %s", _DATA_URL
        )

        async with session.get(
            _DATA_URL,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as response:
            response.raise_for_status()
            payload: Any = await response.json(content_type=None)

        if not isinstance(payload, list):
            raise ProviderError(
                f"Bensinpriser.nu /karta/data returned unexpected format "
                f"(expected JSON array, got {type(payload).__name__})"
            )

        return payload  # type: ignore[return-value]


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station(
    stations: list[dict[str, Any]], station_id: str
) -> dict[str, Any] | None:
    """Return the station record matching station_id, or None.

    Matches on the ``id`` field (integer in the API).  station_id may be
    passed as a string (config entry storage format) and is compared by
    string equality against str(station["id"]).

    Args:
        stations:   List of station dicts from /karta/data.
        station_id: Target id as a string.

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if str(station.get("id", "")) == station_id:
            return station
    return None
