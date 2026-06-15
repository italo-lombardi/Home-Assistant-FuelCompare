"""AtEcontrolProvider — Austrian fuel price data (e-control.at).

Source: E-Control Austria (Energie-Control Austria).
Mandatory fuel price disclosure under Austrian law.
Endpoint: GET https://api.e-control.at/sprit/1.0/search/gas-stations/by-address
No authentication required — plain HTTPS GET.

API quirks:
  - Hard cap of 10 results per query.
  - Must make 3 separate calls (one per fuelType: DIE, SUP, GAS) and merge
    results by station integer `id`.
  - prices[] array may be empty for a given fuel type even when queried for it.
  - Fuel type codes: DIE=diesel, SUP=Super 95 (unleaded), GAS=CNG (compressed
    natural gas).  The integration maps GAS → cng key.
  - OPEN field is a boolean at the station top level.
  - Distance is in km (float).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://api.e-control.at/sprit/1.0/search/gas-stations/by-address"
_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
}
_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# e-control fuelType query codes → StationData keys
_FUEL_CODES: list[tuple[str, str]] = [
    ("DIE", "diesel"),
    ("SUP", "unleaded"),
    ("GAS", "cng"),  # GAS = CNG (compressed natural gas) per API
]

POLL_INTERVAL = 900  # 15 minutes


class AtEcontrolProvider(BaseProvider):
    """Fetch Austrian fuel prices from the e-control.at API.

    CONFIG_MODE='location': user provides lat/lng + radius; up to 10 nearest
    stations per fuel type are fetched and merged by station id.
    """

    COUNTRY = "AT"
    PROVIDER_KEY = "at_econtrol"
    LABEL = "e-control (Austria)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = POLL_INTERVAL
    REQUIRES_API_KEY = False

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "address",
            "cng",
            "county",
            "diesel",
            "is_open",
            "latitude",
            "longitude",
            "name",
            "unleaded",
        }
    )

    STATION_ID_HINT = (
        "Enter the e-control.at station ID (integer). "
        "Use the location search to browse nearby stations."
    )

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch merged station data for the configured location.

        For location-mode providers the station_id parameter is the e-control
        integer station id; lat/lng stored at construction time are used for
        the API query (they are required query params).
        """
        if self._latitude is None or self._longitude is None:
            raise ProviderError(
                "AtEcontrolProvider requires latitude and longitude. "
                "Reconfigure the integration."
            )

        merged = await self._fetch_all_fuel_types(
            session, self._latitude, self._longitude
        )

        station = merged.get(str(station_id))
        if station is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in e-control.at response "
                f"for coordinates ({self._latitude}, {self._longitude}). "
                "The station may be out of range (10-result hard cap)."
            )
        return _build_station_data(station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station name from e-control.at, or None."""
        if self._latitude is None or self._longitude is None:
            return None
        try:
            merged = await self._fetch_all_fuel_types(
                session, self._latitude, self._longitude
            )
            station = merged.get(str(station_id))
            if station:
                return station.get("name") or None
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
        """Return (station_id, display_label) for stations near the given location.

        Kwargs:
            lat (float): Latitude.
            lng (float): Longitude.
            radius_km (float): Search radius in km (informational; API ignores it).
        """
        raw_lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude
        raw_lng = (
            kwargs.get("lng") if kwargs.get("lng") is not None else self._longitude
        )
        if raw_lat is None or raw_lng is None:
            _LOGGER.debug("async_list_stations: no coordinates provided")
            return []
        lat = float(raw_lat)
        lng = float(raw_lng)

        try:
            merged = await self._fetch_all_fuel_types(session, lat, lng)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        result: list[tuple[str, str]] = []
        for sid, raw in merged.items():
            name = raw.get("name") or f"Station {sid}"
            location = raw.get("location") or {}
            address = _format_address(location)
            label = (
                f"{name}, {address} (#{sid[:8]})" if address else f"{name} (#{sid[:8]})"
            )
            result.append((sid, label))

        result.sort(key=lambda item: item[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_fuel_types(
        self,
        session: ClientSession,
        lat: float,
        lng: float,
    ) -> dict[str, dict[str, Any]]:
        """Make 3 API calls (one per fuel type) and merge by station id.

        Returns a dict keyed by station id (str) where each value is a raw
        station dict with a combined 'prices' list from all fuel type queries.
        """
        results = await asyncio.gather(
            *[self._fetch_fuel_type(session, lat, lng, code) for code, _ in _FUEL_CODES]
        )

        merged: dict[str, dict[str, Any]] = {}
        for stations in results:
            for station in stations:
                sid = str(station.get("id", ""))
                if not sid:
                    continue
                if sid not in merged:
                    # Deep copy the station, reset prices to build fresh
                    merged[sid] = {k: v for k, v in station.items() if k != "prices"}
                    merged[sid]["prices"] = []
                # Append prices from this fuel type query
                for price_entry in station.get("prices") or []:
                    merged[sid]["prices"].append(price_entry)

        return merged

    async def _fetch_fuel_type(
        self,
        session: ClientSession,
        lat: float,
        lng: float,
        fuel_code: str,
    ) -> list[dict[str, Any]]:
        """Fetch up to 10 nearest stations for a single fuel type."""
        params = {
            "latitude": str(lat),
            "longitude": str(lng),
            "fuelType": fuel_code,
            "includeClosed": "true",
        }
        async with session.get(
            _BASE_URL,
            params=params,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        if not isinstance(data, list):
            _LOGGER.warning(
                "Unexpected response format from e-control.at for fuelType=%s: %r",
                fuel_code,
                data,
            )
            return []

        return data


# ── Module-level helpers ──────────────────────────────────────────────────────


def _extract_prices(prices_list: list[dict[str, Any]]) -> dict[str, float | None]:
    """Extract normalised prices from the station prices[] array.

    Maps fuelType codes (DIE, SUP, GAS) to StationData keys.
    Handles empty array gracefully.
    """
    if not isinstance(prices_list, list):
        return {}
    fuel_code_map = {
        "DIE": "diesel",
        "SUP": "unleaded",
        "GAS": "cng",
    }
    result: dict[str, float | None] = {}
    for entry in prices_list or []:
        code = entry.get("fuelType")
        key = fuel_code_map.get(code or "")
        if key is None:
            continue
        amount = entry.get("amount")
        if amount is None:
            continue
        try:
            price = float(amount)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        # Normalise: values >10 treated as cents
        if price > 10:
            price = round(price / 100, 4)
        result[key] = round(price, 4)
    return result


def _format_address(location: dict[str, Any]) -> str:
    """Build a human-readable address string from a location dict."""
    parts: list[str] = []
    address = location.get("address") or ""
    postal = location.get("postalCode") or ""
    city = location.get("city") or ""
    if address:
        parts.append(address)
    city_part = " ".join(filter(None, [postal, city]))
    if city_part:
        parts.append(city_part)
    return ", ".join(parts)


def _build_station_data(raw: dict[str, Any]) -> StationData:
    """Build a StationData dict from a merged raw station dict."""
    location: dict[str, Any] = raw.get("location") or {}
    prices = _extract_prices(raw.get("prices") or [])

    try:
        latitude: float | None = (
            float(location["latitude"])
            if location.get("latitude") is not None
            else None
        )
    except (ValueError, TypeError):
        latitude = None
    try:
        longitude: float | None = (
            float(location["longitude"])
            if location.get("longitude") is not None
            else None
        )
    except (ValueError, TypeError):
        longitude = None

    address = _format_address(location)

    return {
        "diesel": prices.get("diesel"),
        "unleaded": prices.get("unleaded"),
        "cng": prices.get("cng"),
        "name": raw.get("name") or None,
        "county": location.get("city") or None,
        "address": address or None,
        "latitude": latitude,
        "longitude": longitude,
        "is_open": raw.get("open") if "open" in raw else None,
        "lastupdated": None,
        "source_station_id": str(raw.get("id") or ""),
    }
