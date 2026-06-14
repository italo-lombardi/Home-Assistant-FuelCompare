"""NoDrivstoffProvider — Norwegian fuel prices from Drivstoffpriser.

Drivstoffpriser is a community-built, open-source fuel price tracking platform
for Norway.  Prices are crowd-sourced by app users and stored in a
FastAPI + PostgreSQL backend.

Source:         https://github.com/drivstoffpriser
App:            https://drivstoffpriser.github.io (Flutter, Android/iOS)
Licence:        GPLv3 (app + backend); station data ODC ODbL

Authentication
--------------
The backend API requires a Firebase Authentication Bearer token on every
request.  Tokens are issued to signed-in app users; there is no public API
key registration path.  This provider therefore targets a self-hosted or
community-provided instance whose base URL is supplied at config time.

If the user does not have access to a valid token the API returns HTTP 401
(invalid token) or HTTP 403 (not signed in).  Both are handled gracefully:
  - async_list_stations returns []
  - async_fetch raises ProviderError

Endpoint used
-------------
GET {base_url}/stations
  ?lat={lat}&lng={lng}&distance={metres}&sort=nearest

Response (camelCase JSON from FastAPI CamelCaseModel):
{
  "stations": [
    {
      "id":         "<uuid>",
      "externalId": "<string>",
      "name":       "<string>",
      "provider":   "CIRCLE_K" | "YX" | "UNO_X" | ...,
      "address":    "<string>",
      "city":       "<string>",
      "location":   {"lat": 59.9, "lng": 10.7},
      "prices": [
        {
          "fuelType":     "DIESEL" | "GASOLINE_95" | "GASOLINE_98",
          "price":        "20.90",          # decimal string, NOK/litre
          "registeredAt": "2026-06-14T..."  # ISO8601 or null for estimates
        },
        ...
      ]
    },
    ...
  ]
}

Fuel type mapping
-----------------
API fuelType      → StationData key   Notes
-----------          ----------------   -----
DIESEL            → diesel             NOK/litre, community price
GASOLINE_95       → unleaded           95 octane petrol, mapped to unleaded
GASOLINE_98       → premium_unleaded   98 octane premium petrol

Price normalisation
-------------------
Prices arrive as decimal strings (e.g. "20.90") representing NOK/litre.
Values are in the range 10–40 NOK/litre (validated by the backend).
The >10 → /100 cents guard used by the fuelcompare.ie provider must NOT be
applied — Norwegian petrol is genuinely above 10 NOK/litre.

Station identity field mapping
------------------------------
API field         → StationData key
---------           ----------------
id                → source_station_id
name              → name
provider          → brand, tablename  (enum string e.g. "CIRCLE_K" → display)
address           → address
city              → county
location.lat      → latitude
location.lng      → longitude
prices[latest].registeredAt → lastupdated (most recent across all fuel types)

Provider brand strings (from API ProviderType enum)
---------------------------------------------------
AUTOMAT_1, BEST, BUNKER_OIL, CIRCLE_K, DRIV, ESSO,
HALTBAKK_EXPRESS, OLJELEVERANDØREN, ST1, TANKEN,
TRONDER_OIL, UNO_X, YX, YX_TRUCK

These are stored as-is in 'brand'; the display name map is applied for the
'tablename' field used by StationBrandSensor.

Poll interval
-------------
POLL_INTERVAL_SECONDS = 3600 (1 hour) as recommended in the task spec.
The API has a rate limit of 10 requests per 10 seconds per user; a 1-hour
interval with a single request per poll is far below that threshold.

Configuration
-------------
CONFIG_MODE = 'station_id' — user selects a station UUID from the location
picker.  The UUID is the backend primary key (a proper UUIDv4).

STATION_LOOKUP_MODE = 'location_search' — the config flow calls
async_list_stations(session, lat=…, lng=…, radius_km=…) to populate the
station picker dropdown.

Required config entry data keys
--------------------------------
station_id  : UUIDv4 string of the selected station
api_key     : Firebase ID token (Bearer) for authenticated API access
latitude    : float, user's search latitude
longitude   : float, user's search longitude
radius_km   : float, search radius (converted to metres for the API)

REQUIRES_API_KEY = True because without a token the API returns 401.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

# ── API configuration ─────────────────────────────────────────────────────────

# Default base URL; can be overridden via api_key_registration_url at config time.
# The community app's backend is at this URL when deployed by the project team.
_DEFAULT_BASE_URL = "https://api.drivstoffpriser.no"

_STATIONS_PATH = "/stations"

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ── Fuel type mapping ─────────────────────────────────────────────────────────

# Maps the API's fuelType enum strings to StationData keys.
_FUEL_TYPE_TO_KEY: dict[str, str] = {
    "DIESEL": "diesel",
    "GASOLINE_95": "unleaded",
    "GASOLINE_98": "premium_unleaded",
}

# Human-readable display names for the ProviderType enum values returned by
# the API.  Used to build friendly station labels in async_list_stations.
_PROVIDER_DISPLAY: dict[str, str] = {
    "AUTOMAT_1": "Automat1",
    "BEST": "Best",
    "BUNKER_OIL": "Bunker Oil",
    "CIRCLE_K": "Circle K",
    "DRIV": "Driv",
    "ESSO": "Esso",
    "HALTBAKK_EXPRESS": "Haltbakk Express",
    "OLJELEVERANDØREN": "Oljeleverandøren",
    "ST1": "St1",
    "TANKEN": "Tanken",
    "TRONDER_OIL": "Trønder Oil",
    "UNO_X": "Uno-X",
    "YX": "YX",
    "YX_TRUCK": "YX Truck",
}


class NoDrivstoffProvider(BaseProvider):
    """Fetch Norwegian fuel prices from the Drivstoffpriser community backend.

    The Drivstoffpriser project is an open-source, crowd-sourced fuel price
    tracker for Norway.  Prices are submitted by community app users.  The
    backend requires a Firebase Authentication Bearer token; the token is
    supplied via the integration's 'api_key' config entry field.

    Station lookup
    --------------
    The config flow calls async_list_stations() with the user's coordinates
    and radius.  The method calls GET /stations with distance in metres and
    returns (uuid, label) tuples for the dropdown.

    Fetch
    -----
    async_fetch() calls GET /stations with the same coordinates and finds the
    target station by UUID in the response.  A dedicated single-station
    endpoint (GET /stations/{id}) would be more efficient but requires the
    same auth; using the same list endpoint avoids a second auth flow step.

    Usage
    -----
    provider = NoDrivstoffProvider(
        station_id="<uuid>",
        api_key="<firebase-id-token>",
        latitude=59.9,
        longitude=10.7,
        radius_km=10.0,
        base_url="https://api.drivstoffpriser.no",  # optional
    )
    """

    COUNTRY = "NO"
    PROVIDER_KEY = "no_drivstoff"
    LABEL = "Drivstoffpriser (Norway)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"
    REQUIRES_API_KEY = True
    API_KEY_REGISTRATION_URL = "https://github.com/drivstoffpriser"

    POLL_INTERVAL_SECONDS = 3600  # 1 hour

    CAPABILITIES: frozenset[str] = frozenset(
        {
            # Fuel prices
            "diesel",
            "unleaded",  # GASOLINE_95 (95 octane)
            "premium_unleaded",  # GASOLINE_98 (98 octane)
            # Station identity
            "name",
            "brand",
            "address",
            "county",  # maps from 'city'
            "latitude",
            "longitude",
            # Timing
            "lastupdated",
            # Coordinator-managed sentinels
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the Drivstoffpriser station UUID.  Find nearby stations by "
        "using the location search in the config flow, or look up the UUID "
        "in the Drivstoffpriser app."
    )

    def __init__(
        self,
        station_id: str,
        api_key: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float = 10.0,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id: UUIDv4 string of the target station.
            api_key:    Firebase ID token for Bearer auth.  May be empty
                        during initial config flow discovery but must be
                        set before async_fetch is called.
            latitude:   WGS84 latitude of the search origin.
            longitude:  WGS84 longitude of the search origin.
            radius_km:  Search radius in km (converted to metres for the API).
            base_url:   Base URL of the Drivstoffpriser backend instance.
        """
        self._station_id = station_id
        self._api_key = api_key
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km
        self._base_url = base_url.rstrip("/")

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for station_id.

        Calls GET /stations with the configured lat/lng/radius and locates
        the target station by UUID in the response.  Prices from all fuel
        types are merged into one StationData dict.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: UUIDv4 of the target station.

        Returns:
            StationData dict with CAPABILITIES keys populated.

        Raises:
            ProviderError: Station UUID not found in the API response, or
                           the API returns an authentication error.
        """
        if self._latitude is None or self._longitude is None:
            raise ProviderError(
                "NoDrivstoffProvider requires latitude and longitude to be "
                "configured.  Set them in the config entry options."
            )

        stations = await self._fetch_stations(
            session,
            lat=self._latitude,
            lng=self._longitude,
            radius_km=self._radius_km,
        )

        if stations is None:
            raise ProviderError(
                "Drivstoffpriser API request failed.  Check that the API key "
                "(Firebase token) is valid and that the backend is reachable."
            )

        station = _find_station(stations, station_id)
        if station is None:
            raise ProviderError(
                f"Station UUID '{station_id}' not found in Drivstoffpriser "
                f"response for lat={self._latitude}, lng={self._longitude}, "
                f"radius={self._radius_km} km.  The station may be outside "
                "the configured search radius."
            )

        return _parse_station(station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Makes a location search and resolves the station name from the UUID.
        Returns None on any failure so the config flow falls back to
        'Station {id}'.

        Args:
            session:    aiohttp ClientSession.
            station_id: UUIDv4 of the target station.
        """
        if self._latitude is None or self._longitude is None:
            return None
        try:
            stations = await self._fetch_stations(
                session,
                lat=self._latitude,
                lng=self._longitude,
                radius_km=self._radius_km,
            )
            if not stations:
                return None
            record = _find_station(stations, station_id)
            if record is None:
                return None
            return _display_name(record) or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
            return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return (station_uuid, display_label) pairs for the station picker.

        Called by the config flow location_search step.  Fetches all stations
        within the given radius, applies a client-side haversine distance
        filter, and returns a list sorted nearest-first.

        Args:
            session:   aiohttp ClientSession.
            lat:       Search latitude (overrides constructor value when supplied).
            lng:       Search longitude (overrides constructor value when supplied).
            radius_km: Search radius in km (overrides constructor value when
                       supplied).

        Returns:
            List of (uuid, "Name — Brand — Diesel kr20.90 / Bensin kr21.50")
            tuples sorted nearest-first.  Empty list on any failure.
        """
        raw_lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude
        raw_lng = (
            kwargs.get("lng") if kwargs.get("lng") is not None else self._longitude
        )

        if raw_lat is None or raw_lng is None:
            _LOGGER.debug(
                "async_list_stations called with no coordinates; returning []"
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
            stations = await self._fetch_stations(
                session, lat=lat, lng=lng, radius_km=radius_km
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        if not stations:
            return []

        result: list[tuple[str, str, float]] = []
        for station in stations:
            uid: str | None = station.get("id")
            if not uid:
                continue

            # Client-side distance filter using haversine for accuracy
            loc = station.get("location") or {}
            raw_s_lat = loc.get("lat")
            raw_s_lng = loc.get("lng")
            if raw_s_lat is None or raw_s_lng is None:
                continue  # skip stations with no GPS
            try:
                s_lat = float(raw_s_lat)
                s_lng = float(raw_s_lng)
            except (ValueError, TypeError):
                continue
            dist_km = haversine_km(lat, lng, s_lat, s_lng)
            if dist_km > radius_km:
                continue

            name = _display_name(station)
            prices = _extract_prices(station.get("prices") or [])

            price_parts: list[str] = []
            if prices.get("diesel") is not None:
                price_parts.append(f"Diesel kr{prices['diesel']:.2f}")
            if prices.get("unleaded") is not None:
                price_parts.append(f"Bensin 95 kr{prices['unleaded']:.2f}")
            if prices.get("premium_unleaded") is not None:
                price_parts.append(f"Bensin 98 kr{prices['premium_unleaded']:.2f}")

            if price_parts:
                label = f"{name} — {' / '.join(price_parts)}"
            else:
                label = name

            result.append((uid, label, dist_km))

        result.sort(key=lambda x: x[2])
        return [(uid, label) for uid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_stations(
        self,
        session: ClientSession,
        lat: float,
        lng: float,
        radius_km: float,
    ) -> list[dict] | None:
        """Call GET /stations and return the raw station list.

        Args:
            session:   aiohttp ClientSession.
            lat:       Search centre latitude.
            lng:       Search centre longitude.
            radius_km: Search radius in km (converted to metres for the API).

        Returns:
            List of station dicts on success, or None on HTTP/network error.
            HTTP 401/403 is logged at WARNING level (auth failure) and
            returns None so the coordinator's stale-retention triggers.
        """
        url = f"{self._base_url}{_STATIONS_PATH}"
        # The API 'distance' parameter is in metres
        distance_m = radius_km * 1000.0
        params: dict[str, str] = {
            "lat": str(lat),
            "lng": str(lng),
            "distance": str(distance_m),
            "sort": "nearest",
        }

        headers = dict(_HEADERS)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        _LOGGER.debug(
            "Fetching Drivstoffpriser stations: lat=%s lng=%s distance_m=%s",
            lat,
            lng,
            distance_m,
        )

        try:
            async with session.get(
                url,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    _LOGGER.warning(
                        "Drivstoffpriser API returned HTTP %s.  "
                        "The Firebase token (api_key) may be expired or invalid.  "
                        "Re-authenticate via the Drivstoffpriser app to obtain a "
                        "fresh token and update the integration config entry.",
                        response.status,
                    )
                    return None
                response.raise_for_status()
                payload: dict = await response.json()
        except ClientResponseError as err:
            _LOGGER.debug("HTTP error fetching Drivstoffpriser stations: %s", err)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unexpected error fetching Drivstoffpriser stations: %s", err)
            return None

        return payload.get("stations") or []


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station(stations: list[dict], station_id: str) -> dict | None:
    """Return the station record matching station_id by UUID, or None.

    Args:
        stations:   List of station dicts from the /stations response.
        station_id: Target UUIDv4 string.

    Returns:
        Matching station dict, or None if not found.
    """
    for station in stations:
        if station.get("id") == station_id:
            return station
    return None


def _parse_price(raw: object) -> float | None:
    """Parse a Drivstoffpriser price value.

    The API returns prices as decimal strings (e.g. "20.90") representing
    NOK/litre.  Valid range is 10–40 as enforced by the backend schema.

    Norwegian petrol is genuinely above 10 NOK/litre, so the >10 → /100
    cents guard used by the fuelcompare.ie provider is NOT applied here.

    Args:
        raw: Price value from the API (decimal string, number, or None).

    Returns:
        Rounded float (NOK/litre) or None if the value is absent or invalid.
    """
    if raw is None:
        return None
    try:
        val = float(Decimal(str(raw)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val, 2)


def _extract_prices(prices_list: list[dict]) -> dict[str, float | None]:
    """Extract the latest prices from a station's prices list.

    When multiple records exist for the same fuel type (which should not
    happen under the current API schema since is_latest deduplicates server-
    side), the first occurrence is used.

    Args:
        prices_list: List of price dicts from the API station record.

    Returns:
        Dict mapping StationData keys to float prices or None.
    """
    result: dict[str, float | None] = {}
    for price_record in prices_list:
        fuel_type: str | None = price_record.get("fuelType")
        if fuel_type is None:
            continue
        key = _FUEL_TYPE_TO_KEY.get(fuel_type)
        if key is None:
            continue
        if key in result:
            continue  # keep first occurrence (latest per API ordering)
        result[key] = _parse_price(price_record.get("price"))
    return result


def _latest_timestamp(prices_list: list[dict]) -> str | None:
    """Return the most recent registeredAt timestamp from a prices list.

    Args:
        prices_list: List of price dicts from the API station record.

    Returns:
        ISO 8601 timestamp string or None if no timestamps are present.
    """
    timestamps: list[str] = []
    for price_record in prices_list:
        ts = price_record.get("registeredAt")
        if ts and isinstance(ts, str):
            timestamps.append(ts)
    if not timestamps:
        return None
    # ISO 8601 strings sort lexicographically; max = most recent
    return max(timestamps)


def _display_name(station: dict) -> str:
    """Build a display name for a station.

    Combines the API 'provider' brand string with the station 'name', using
    the human-readable brand display map.  Falls back to the UUID when both
    are absent.

    Args:
        station: Raw station dict from the API.

    Returns:
        Non-empty display name string.
    """
    name: str = (station.get("name") or "").strip()
    provider_key: str = station.get("provider") or ""
    brand: str = _PROVIDER_DISPLAY.get(provider_key, provider_key).strip()

    if brand and name:
        # Avoid "Circle K Circle K Oslo S" style duplication
        if name.lower().startswith(brand.lower()):
            return name
        return f"{brand} {name}"
    if brand:
        return brand
    if name:
        return name
    return station.get("id") or "Unknown"


def _parse_station(station: dict) -> StationData:
    """Assemble a StationData dict from a raw Drivstoffpriser station record.

    Args:
        station: Raw station dict from the /stations API response.

    Returns:
        Populated StationData dict with CAPABILITIES-aligned keys.
    """
    station_id: str = station.get("id") or ""
    name: str | None = (station.get("name") or "").strip() or None
    provider_key: str = station.get("provider") or ""
    brand: str | None = _PROVIDER_DISPLAY.get(provider_key, provider_key) or None
    address: str | None = (station.get("address") or "").strip() or None
    city: str | None = (station.get("city") or "").strip() or None

    loc = station.get("location") or {}
    try:
        lat: float | None = (
            float(loc.get("lat")) if loc.get("lat") is not None else None
        )
    except (ValueError, TypeError):
        lat = None
    try:
        lng: float | None = (
            float(loc.get("lng")) if loc.get("lng") is not None else None
        )
    except (ValueError, TypeError):
        lng = None

    prices_list: list[dict] = station.get("prices") or []
    prices = _extract_prices(prices_list)
    updated_at = _latest_timestamp(prices_list)

    data: StationData = {
        # Fuel prices
        "diesel": prices.get("diesel"),
        "unleaded": prices.get("unleaded"),
        "premium_unleaded": prices.get("premium_unleaded"),
        # Station identity
        "name": name,
        "brand": brand,
        "tablename": brand,
        "address": address,
        "county": city,
        "latitude": lat,
        "longitude": lng,
        # Timing
        "lastupdated": updated_at,
        # Passthrough
        "source_station_id": station_id,
    }

    _LOGGER.debug(
        "Drivstoffpriser parsed data for station %s: "
        "diesel=%s unleaded=%s premium_unleaded=%s updated_at=%s",
        station_id,
        data.get("diesel"),
        data.get("unleaded"),
        data.get("premium_unleaded"),
        updated_at,
    )

    return data
