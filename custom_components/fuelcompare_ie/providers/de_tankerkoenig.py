"""DeTankerkoenigProvider — German fuel prices from Tankerkoenig (creativecommons.tankerkoenig.de).

Source: Tankerkoenig, operated under a Creative Commons licence.
API documentation: https://creativecommons.tankerkoenig.de/
See https://dev.tankerkoenig.de/ for API key registration.

Endpoints used
--------------
/list.php?lat={lat}&lng={lng}&rad={km}&type=all&apikey={key}
    Returns nearby stations.  Top-level key ``stations`` (array).
    Fields per station: id (UUID), name, brand, street, houseNumber, place,
    postCode (int), lat, lng, dist (km), e5, e10, diesel, isOpen.

/detail.php?id={uuid}&apikey={key}
    Returns a single station.  Top-level key ``station`` (object).
    Adds: openingTimes, overrides, wholeDay, state.

Price field notes
-----------------
CRITICAL: e5, e10, and diesel price fields can be JSON boolean ``false``
(not ``null``, not absent) when the station does not sell that fuel type.
The API schema documents this explicitly:  "e5": false — kein Super.
All price parsing must handle ``Union[float, bool]`` and treat ``False``
as unavailable (None).

CONFIG_MODE
-----------
CONFIG_MODE = 'location': the user configures a lat/lng + radius; all
stations within the radius are discovered via /list.php and presented in
the config flow station picker.  The selected station's UUID is then stored
as station_id in the config entry.

Authentication
--------------
API key is passed as query parameter ``apikey={key}``.  Missing or malformed
key returns ``{"ok": false, "message": "apikey nicht angegeben, falsch, oder
im falschen Format"}``.

Rate limit
----------
Official guidance: minimum 5-minute poll interval.  POLL_INTERVAL_SECONDS is
set to 1800 (30 minutes), which is safe and aligns with other providers.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://creativecommons.tankerkoenig.de/json"
_TIMEOUT = ClientTimeout(total=API_TIMEOUT)
_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/json",
}


def _parse_price(raw: Any) -> float | None:
    """Parse a Tankerkoenig price field.

    Returns None when the value is boolean False (station does not sell that
    fuel type), None, or an unparseable value.  Returns a rounded float
    otherwise.

    Tankerkoenig prices are already in EUR/litre (e.g. 1.789).
    The >10 → /100 cents guard is NOT applied here.
    """
    if raw is False or raw is None:
        return None
    if isinstance(raw, bool):
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val, 3)


def _build_address(station: dict[str, Any]) -> str | None:
    """Build a human-readable address string from Tankerkoenig station fields.

    Tankerkoenig splits the address into ``street``, ``houseNumber``,
    ``postCode`` (int), and ``place``.  We combine them into a single string.
    """
    parts: list[str] = []
    street = station.get("street") or ""
    house = station.get("houseNumber") or ""
    if street:
        parts.append(f"{street} {house}".strip())
    post_code = station.get("postCode")
    place = station.get("place") or ""
    if post_code is not None or place:
        location = f"{post_code} {place}".strip() if post_code else place
        if location:
            parts.append(location)
    return ", ".join(parts) if parts else None


def _parse_station(station: dict[str, Any]) -> StationData:
    """Build a StationData dict from a Tankerkoenig station record.

    Handles both /list.php station objects and /detail.php station objects;
    the field set is compatible between the two endpoints.
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

    return {
        "unleaded": _parse_price(station.get("e5")),
        "e10": _parse_price(station.get("e10")),
        "diesel": _parse_price(station.get("diesel")),
        "name": station.get("name") or None,
        "brand": station.get("brand") or None,
        "address": _build_address(station),
        "county": station.get("place") or None,
        "latitude": latitude,
        "longitude": longitude,
        "is_open": bool(station.get("isOpen"))
        if station.get("isOpen") is not None
        else None,
        "source_station_id": str(station.get("id", "")),
    }


class DeTankerkoenigProvider(BaseProvider):
    """Fetch German fuel prices from the Tankerkoenig Creative Commons API.

    Users configure a lat/lng + radius (CONFIG_MODE='location').  The config
    flow calls async_list_stations() to show nearby stations sorted cheapest-
    first by diesel price.  The selected station UUID is stored as station_id.

    Requires a free API key from https://onboarding.tankerkoenig.de/
    """

    COUNTRY = "DE"
    PROVIDER_KEY = "de_tankerkoenig"
    LABEL = "Tankerkoenig (Germany)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 1800
    STATION_PAGE_URL: ClassVar[str] = "https://www.tankerkoenig.de"
    STATION_PAGE_URL_TEMPLATE: ClassVar[str] = (
        "https://www.tankerkoenig.de/?page=details&id={station_id}"
    )
    REQUIRES_API_KEY: ClassVar[bool] = True
    API_KEY_REGISTRATION_URL: ClassVar[str] = "https://onboarding.tankerkoenig.de/"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",
            "diesel",
            "e10",
            "name",
            "brand",
            "county",
            "address",
            "latitude",
            "longitude",
            "is_open",
        }
    )

    def __init__(
        self,
        station_id: str,
        api_key: str = "",
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  Tankerkoenig station UUID (stored after config flow
                         station selection).
            api_key:     Tankerkoenig API key.
            county:      Not used; accepted for constructor-signature
                         compatibility with location-mode providers.
            latitude:    WGS84 latitude of the search centre.
            longitude:   WGS84 longitude of the search centre.
            radius_km:   Search radius in kilometres (used by async_list_stations).
        """
        self._station_id = station_id
        self._api_key = api_key
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
        """Fetch and return normalised station data for station_id.

        Uses the /detail.php endpoint for a single station lookup.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Tankerkoenig station UUID.

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: API returned ok=false or station not found.
        """
        url = f"{_BASE_URL}/detail.php"
        params: dict[str, str] = {
            "id": station_id,
            "apikey": self._api_key,
        }
        _LOGGER.debug("Fetching station %s (api_key redacted)", station_id)

        try:
            async with session.get(
                url, params=params, headers=_HEADERS, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload: dict[str, Any] = await resp.json(content_type=None)
        except ClientError:
            raise
        except Exception as err:
            raise ProviderError(
                f"Tankerkoenig API request failed for station '{station_id}': "
                f"{type(err).__name__}"
            ) from err

        if not payload.get("ok"):
            message = payload.get("message", "unknown error")
            raise ProviderError(
                f"Tankerkoenig API returned ok=false for station '{station_id}': {message}"
            )

        station = payload.get("station")
        if not station:
            raise ProviderError(
                f"Tankerkoenig API returned no station data for station '{station_id}'"
            )

        _LOGGER.debug("Tankerkoenig detail fetched for station %s", station_id)
        return _parse_station(station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Uses the /detail.php endpoint to retrieve the station name.

        Args:
            session:    aiohttp ClientSession.
            station_id: Tankerkoenig station UUID.
        """
        try:
            url = f"{_BASE_URL}/detail.php"
            params: dict[str, str] = {
                "id": station_id,
                "apikey": self._api_key,
            }
            async with session.get(
                url, params=params, headers=_HEADERS, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload: dict[str, Any] = await resp.json(content_type=None)

            if not payload.get("ok"):
                return None

            station = payload.get("station")
            if station:
                name: str | None = station.get("name") or None
                brand: str | None = station.get("brand") or None
                if name:
                    return name
                if brand:
                    return brand
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to fetch station name for %s: %s",
                station_id,
                type(err).__name__,
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_uuid, display_label) pairs for the config flow station picker.

        Calls /list.php with the configured or supplied lat/lng + radius, then
        returns all results sorted alphabetically by label.

        Label format: "{brand} {name}, {address} (#{uuid[:8]})"
        - If name already starts with brand (case-insensitive), brand prefix is omitted.
        - Address part is omitted when no address is available.
        - No price information is included in the label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Search centre latitude (overrides constructor value).
            lng:       Search centre longitude (overrides constructor value).
            radius_km: Search radius in kilometres (overrides constructor value).

        Returns:
            List of (uuid, label) tuples sorted alphabetically by label.
            Empty list on any failure.
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
                "async_list_stations called without lat/lng for Tankerkoenig; "
                "returning empty list"
            )
            return []

        url = f"{_BASE_URL}/list.php"
        params: dict[str, str] = {
            "lat": str(lat),
            "lng": str(lng),
            "rad": str(radius_km),
            "type": "all",
            "apikey": self._api_key,
        }
        _LOGGER.debug("Fetching Tankerkoenig station list")

        try:
            async with session.get(
                url, params=params, headers=_HEADERS, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload: dict[str, Any] = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations HTTP error: %s", type(err).__name__)
            return []

        if not payload.get("ok"):
            message = payload.get("message", "unknown error")
            _LOGGER.debug("Tankerkoenig list API returned ok=false: %s", message)
            return []

        stations: list[dict[str, Any]] = payload.get("stations") or []
        if not stations:
            return []

        result: list[tuple[str, str]] = []
        for station in stations:
            uid: str = str(station["id"]) if station.get("id") is not None else ""
            if not uid:
                continue

            name: str = station.get("name") or ""
            brand: str = station.get("brand") or ""

            # Build display name: prepend brand only when name does not already
            # start with it (case-insensitive comparison).
            if brand and not name.lower().startswith(brand.lower()):
                display_name = f"{brand} {name}".strip()
            else:
                display_name = name or brand or uid

            # Build address from _build_address helper (returns None when absent).
            address: str | None = _build_address(station)

            # Compose label: "Display Name, Address (#uuid[:8])"
            short_id = uid[:8]
            if address:
                label = f"{display_name}, {address} (#{short_id})"
            else:
                label = f"{display_name} (#{short_id})"

            result.append((uid, label))

        result.sort(key=lambda x: x[1].lower())
        return result
