"""HRMzoeProvider — Croatian government fuel price data (mzoe-gor.hr).

Source: Ministry of Economy and Sustainable Development (MINGOR), Croatia.
Mandatory fuel price disclosure under Croatian law.
Endpoint: GET https://mzoe-gor.hr/data.gz — single hourly gzip JSON, no auth.
906 stations, all Croatian counties.

NOTE: The API has swapped lat/lng fields:
  - 'lat' field contains the LONGITUDE (e.g. 16.15)
  - 'long' field contains the LATITUDE (e.g. 45.79)
  This is corrected in _parse_station().

Fuel type mapping (vrsta_gorivas tip_goriva_id):
  1 = Benzinska goriva (petrol/unleaded)
  2 = Dizelska goriva (diesel)
  3 = Autoplin (LPG)
  7 = Struja (EV charging — not a fuel, excluded)
"""

from __future__ import annotations

import gzip
import json
import logging
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_DATA_URL = "https://mzoe-gor.hr/data.gz"
_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)  # larger timeout for 906-station gzip

# tip_goriva_id values we map to StationData keys
_TIP_TO_KEY: dict[int, str] = {
    1: "unleaded",  # Benzinska goriva → unleaded/petrol
    2: "diesel",  # Dizelska goriva → diesel
    3: "lpg",  # Autoplin → LPG
}


class HRMzoeProvider(BaseProvider):
    """Fetch Croatian fuel prices from the government mzoe-gor.hr API.

    All 906 stations are returned in a single gzip request, so CONFIG_MODE
    is 'location' — the user picks their station from a county-filtered list.
    Station ID is the numeric mzoe-gor.hr internal station ID as a string.
    """

    COUNTRY = "HR"
    PROVIDER_KEY = "hr_mzoe"
    LABEL = "MINGOR (Croatia)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "county_search"
    POLL_INTERVAL_SECONDS = 3600  # updated hourly; align poll to :05 past the hour

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",
            "diesel",
            "lpg",
            "lastupdated",
            "name",
            "brand",
            "county",
            "address",
            "latitude",
            "longitude",
        }
    )

    STATION_ID_HINT = (
        "Enter the station ID from the Croatian MINGOR fuel price register. "
        "Use the county selector to browse available stations."
    )

    def __init__(self, station_id: str, county: str | None = None) -> None:
        self._station_id = station_id
        self._county = county  # not used for fetching (national dump), stored for info

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch the full national dataset and return data for the configured station."""
        raw = await self._fetch_raw(session)
        station = _find_station_in_data(raw, station_id)
        if station is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in Croatian MINGOR dataset. "
                "Verify the station ID is correct."
            )
        return _parse_station(station, raw)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return station name for the config flow."""
        try:
            raw = await self._fetch_raw(session)
            station = _find_station_in_data(raw, station_id)
            if station:
                return station.get("naziv") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) for all stations, optionally filtered by county.

        Args:
            county: Croatian county name (e.g. 'Grad Zagreb') or 'croatia' for all.
        """
        county_filter = str(kwargs.get("county", "croatia")).lower()
        try:
            raw = await self._fetch_raw(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        # Build county name lookup from zupanijas
        county_map = {z["id"]: z.get("naziv", "") for z in raw.get("zupanijas", [])}

        # Build operator/brand lookup
        brand_map = {o["id"]: o.get("naziv", "") for o in raw.get("obvezniks", [])}

        result: list[tuple[str, str]] = []
        for station in raw.get("postajas", []):
            sid = str(station.get("id", ""))
            if not sid:
                continue

            # County filter — compare normalized (lowercase, underscores→spaces)
            if county_filter != "croatia":
                station_county = (
                    county_map.get(station.get("zupanija_id") or 0, "")
                    .lower()
                    .replace(" ", "_")
                )
                if county_filter.replace(
                    " ", "_"
                ) not in station_county and station_county not in county_filter.replace(
                    " ", "_"
                ):
                    continue

            name = station.get("naziv", "Unknown")
            brand = brand_map.get(station.get("obveznik_id"), "") or ""
            display_name = (
                f"{brand} — {name}"
                if brand and brand.lower() not in name.lower()
                else name
            )
            address = station.get("adresa", "") or ""
            label = f"{display_name}, {address} (#{sid[:8]})"
            result.append((sid, label))

        result.sort(key=lambda x: x[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_raw(self, session: ClientSession) -> dict[str, Any]:
        """Fetch and decompress the mzoe-gor.hr data.gz file."""
        async with session.get(_DATA_URL, headers=_HEADERS, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            compressed = await resp.read()

        try:
            return json.loads(gzip.decompress(compressed))
        except (gzip.BadGzipFile, OSError, ValueError) as err:
            raise ProviderError(
                f"Failed to decompress/parse mzoe-gor.hr response: {err}"
            ) from err


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station_in_data(raw: dict, station_id: str) -> dict | None:
    """Find a station by numeric string ID in the raw dataset."""
    for station in raw.get("postajas", []):
        if str(station.get("id", "")) == station_id:
            return station
    return None


def _extract_prices(
    station: dict,
    vrsta_tip: dict[int, int],
    gorivo_vrsta: dict[int, int],
) -> dict[str, float | None]:
    """Extract normalised fuel prices from a station's cjenici (price list)."""
    prices: dict[str, list[float]] = {}
    for entry in station.get("cjenici", []):
        gorivo_id = entry.get("gorivo_id")
        cijena = entry.get("cijena")
        if gorivo_id is None or cijena is None:
            continue
        vrsta_id = gorivo_vrsta.get(gorivo_id)
        if vrsta_id is None:
            continue
        tip_id = vrsta_tip.get(vrsta_id)
        key = _TIP_TO_KEY.get(tip_id or 0)
        if key is None:
            continue
        try:
            price = float(cijena)
            if price > 0:
                prices.setdefault(key, []).append(price)
        except (ValueError, TypeError):
            pass

    # Average multiple brands of same type, then return best (cheapest)
    return {k: round(min(v), 3) for k, v in prices.items()}


def _parse_station(station: dict, raw: dict) -> StationData:
    """Build StationData from a raw station dict.

    IMPORTANT: lat/lng fields are swapped in the source data:
      station['lat']  → actual longitude
      station['long'] → actual latitude
    """
    vrsta_tip = {v["id"]: v["tip_goriva_id"] for v in raw.get("vrsta_gorivas", [])}
    gorivo_vrsta = {g["id"]: g["vrsta_goriva_id"] for g in raw.get("gorivos", [])}
    brand_map = {o["id"]: o.get("naziv", "") for o in raw.get("obvezniks", [])}
    county_map = {z["id"]: z.get("naziv", "") for z in raw.get("zupanijas", [])}

    prices = _extract_prices(station, vrsta_tip, gorivo_vrsta)

    # lat/lng fields are swapped — correct them
    raw_lat = station.get("long")  # 'long' actually holds latitude
    raw_lng = station.get("lat")  # 'lat' actually holds longitude
    try:
        latitude: float | None = float(raw_lat) if raw_lat is not None else None
    except (ValueError, TypeError):
        latitude = None
    try:
        longitude: float | None = float(raw_lng) if raw_lng is not None else None
    except (ValueError, TypeError):
        longitude = None

    brand = brand_map.get(station.get("obveznik_id"), "") or None
    county = county_map.get(station.get("zupanija_id") or 0, "") or None

    name = station.get("naziv") or None

    return {
        "unleaded": prices.get("unleaded"),
        "diesel": prices.get("diesel"),
        "lpg": prices.get("lpg"),
        "name": name,
        "brand": brand,
        "county": county,
        "address": station.get("adresa") or None,
        "latitude": latitude,
        "longitude": longitude,
        "lastupdated": None,  # API does not return per-station timestamp
        "source_station_id": str(station.get("id", "")),
    }
