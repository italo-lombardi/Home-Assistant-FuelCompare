"""EsMineturProvider — Spanish government fuel price data (MINETUR).

Source: Ministry of Industry, Commerce and Tourism (MINETUR), Spain.
Endpoint: GET https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/
          PreciosCarburantes/EstacionesTerrestres/
Returns all ~11,477 Spanish fuel stations as a single unauthenticated JSON
response.  No API key, no rate-limit headers; the Nota field confirms prices
are updated every 30 minutes.

Province-filtered endpoint:
  /EstacionesTerrestres/FiltroProvincia/{IDProvincia}

Key data-formatting quirks
--------------------------
1. All numeric fields (Latitud, Longitud, prices) use a COMMA as the decimal
   separator (Spanish locale).  Must str.replace(',', '.') before float().
2. Longitude is stored under the key 'Longitud (WGS84)' — the parenthetical
   is part of the key name.
3. Empty price fields are '' (empty string), not null — check truthiness
   before parsing.
4. Response is decoded with utf-8-sig to strip any BOM defensively.
5. Station ID is the 'IDEESS' field — a string integer (e.g. '4375').

StationData field mapping
-------------------------
IDEESS                            → source_station_id
Rótulo                            → brand (also name when no distinct name)
Dirección                         → address
Municipio                         → city (used in display labels)
Provincia                         → county
C.P.                              → postcode (passthrough only)
Latitud                           → latitude  (comma→period, then float)
Longitud (WGS84)                  → longitude (comma→period, then float)
Precio Gasolina 95 E5             → unleaded
Precio Gasoleo A                  → diesel
Precio Gasolina 98 E5             → premium_unleaded
Precio Gases licuados del petróleo→ lpg
Precio Gasoleo B                  → (ignored, off-road diesel)
Precio Gasoleo Premium            → premium_diesel (passthrough only)
Fecha                             → lastupdated  (top-level response field)

E85 / AdBlue: MINETUR does not publish these in the open API; the keys are
included in CAPABILITIES because the integration spec lists them, but they
will always be None.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

_BASE_URL = (
    "https://sedeaplicaciones.minetur.gob.es"
    "/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"
)
_PROVINCE_URL = _BASE_URL + "FiltroProvincia/{province_id}"

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
}

# Larger timeout: single response is ~4–5 MB for the full national dataset.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 6)

# Raw API field names → StationData keys for fuel prices.
_PRICE_FIELDS: dict[str, str] = {
    "Precio Gasolina 95 E5": "unleaded",
    "Precio Gasoleo A": "diesel",
    "Precio Gasolina 98 E5": "premium_unleaded",
    "Precio Gases licuados del petróleo": "lpg",
}

# Earth radius in km (WGS84 mean radius)


class EsMineturProvider(BaseProvider):
    """Fetch Spanish fuel prices from the MINETUR government open-data API.

    CONFIG_MODE is 'location': the user supplies lat/lng + radius_km and the
    provider returns all stations within that radius, sorted cheapest-first
    by diesel price (falling back to unleaded, then alphabetical by brand).

    The full national dataset (~11,477 stations, ~4–5 MB) is fetched on every
    poll and filtered client-side by Haversine distance.  The API supports a
    province filter (/FiltroProvincia/{id}) but province IDs are not surfaced
    in the HA config flow, so we use the national endpoint and filter locally.
    """

    COUNTRY = "ES"
    PROVIDER_KEY = "es_minetur"
    LABEL = "MINETUR (Spain)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 1800  # API updates every 30 minutes per Nota field

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "unleaded",
            "diesel",
            "premium_unleaded",
            "lpg",
            "lastupdated",
            "name",
            "brand",
            "county",
            "address",
            "latitude",
            "longitude",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the IDEESS station identifier from the MINETUR open-data register. "
        "Use the location selector to browse stations near your coordinates."
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
            station_id:  IDEESS identifier (string integer, e.g. '4375').
                         For location mode this is used as the primary key
                         when async_fetch is called for a specific station.
            county:      Optional province name hint (not used for filtering
                         in location mode; stored for informational purposes).
            latitude:    WGS84 latitude of the user's location.
            longitude:   WGS84 longitude of the user's location.
            radius_km:   Search radius in kilometres for station discovery.
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
        """Fetch and return normalised data for the configured station.

        For CONFIG_MODE='location' the station is identified by its IDEESS
        string.  The full national dataset is fetched and the matching station
        is extracted.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: IDEESS string identifier for the target station.

        Returns:
            StationData with all CAPABILITIES keys populated (None when the
            API has no data for that field).

        Raises:
            ProviderError: Station not found in the API response, or the
                           response is malformed.
        """
        payload = await self._fetch_raw(session)
        fecha = payload.get("Fecha") or None
        stations: list[dict] = payload.get("ListaEESSPrecio") or []

        station = _find_station(stations, station_id)
        if station is None:
            raise ProviderError(
                f"Station IDEESS '{station_id}' not found in MINETUR dataset. "
                "Verify the station ID is correct."
            )
        return _parse_station(station, fecha)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a human-readable station name for the config flow, or None.

        For location-based providers the config flow uses the auto-generated
        'Country (lat, lon)' title instead, so this always returns None.

        Args:
            session:    aiohttp ClientSession.
            station_id: IDEESS string identifier (unused).
        """
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the location-search picker.

        Fetches the full national dataset, filters by Haversine distance from
        (lat, lng), and returns stations within radius_km sorted cheapest-first
        by diesel price (then unleaded, then alphabetically by brand).

        Kwargs:
            lat (float):       Centre latitude for the search.
            lng (float):       Centre longitude for the search.
            radius_km (float): Search radius.  Falls back to self._radius_km.

        Returns:
            List of (IDEESS, "Brand — City — Diesel €1.67/L") tuples.
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
                "async_list_stations called without lat/lng; returning empty list"
            )
            return []

        try:
            payload = await self._fetch_raw(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations fetch failed: %s", err)
            return []

        fecha = payload.get("Fecha") or None
        stations: list[dict] = payload.get("ListaEESSPrecio") or []

        nearby: list[tuple[str, str, float]] = []
        for raw in stations:
            station_lat = _parse_coord(raw.get("Latitud"))
            station_lng = _parse_coord(raw.get("Longitud (WGS84)"))
            if station_lat is None or station_lng is None:
                continue

            dist = _haversine_km(lat, lng, station_lat, station_lng)
            if dist > radius_km:
                continue

            ideess = str(raw.get("IDEESS", "")).strip()
            if not ideess:
                continue

            data = _parse_station(raw, fecha)
            brand = data.get("brand") or "Unknown"
            city = str(raw.get("Municipio", "")).strip().title() or ""
            county = data.get("county") or ""

            location_parts = [p for p in (city, county) if p]
            location_str = ", ".join(location_parts) if location_parts else ""

            price_parts: list[str] = []
            diesel = data.get("diesel")
            unleaded = data.get("unleaded")
            if diesel is not None:
                price_parts.append(f"Diesel €{diesel:.3f}")
            if unleaded is not None:
                price_parts.append(f"95 €{unleaded:.3f}")

            label_parts = [brand]
            if location_str:
                label_parts.append(location_str)
            if price_parts:
                label_parts.append(" / ".join(price_parts))
            label = " — ".join(label_parts)

            # Sort key: cheapest diesel first, then unleaded, then distance
            sort_key = (
                diesel
                if diesel is not None
                else (unleaded if unleaded is not None else 9999.0)
            )
            nearby.append((ideess, label, sort_key))

        nearby.sort(key=lambda x: (x[2], x[1]))
        return [(ideess, label) for ideess, label, _ in nearby]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_raw(self, session: ClientSession) -> dict[str, Any]:
        """Fetch the full national MINETUR station list and return parsed JSON.

        Decodes with utf-8-sig to strip any BOM defensively (safe even when
        the BOM is absent).

        Raises:
            aiohttp.ClientError: on network / HTTP errors (let propagate).
            ProviderError: on malformed JSON or unexpected response structure.
        """
        _LOGGER.debug("Fetching MINETUR full station list from %s", _BASE_URL)
        async with session.get(
            _BASE_URL,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            # Read bytes and decode with utf-8-sig to safely strip any BOM.
            raw_bytes = await resp.read()

        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as err:
            raise ProviderError(
                f"MINETUR response could not be decoded as UTF-8: {err}"
            ) from err

        import json as _json

        try:
            payload: dict[str, Any] = _json.loads(text)
        except _json.JSONDecodeError as err:
            raise ProviderError(f"MINETUR response is not valid JSON: {err}") from err

        if payload.get("ResultadoConsulta") != "OK":
            raise ProviderError(
                f"MINETUR API returned unexpected ResultadoConsulta: "
                f"{payload.get('ResultadoConsulta')!r}"
            )

        return payload


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_station(stations: list[dict], station_id: str) -> dict | None:
    """Return the station dict with matching IDEESS, or None."""
    for station in stations:
        if str(station.get("IDEESS", "")).strip() == station_id:
            return station
    return None


def _parse_coord(raw: str | None) -> float | None:
    """Parse a Spanish-locale coordinate string (comma decimal) to float.

    Args:
        raw: String like '38,999722' or '-1,854556', or None / empty string.

    Returns:
        float, or None if the value is absent or unparseable.
    """
    if not raw:
        return None
    try:
        return float(str(raw).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _parse_price(raw: str | None) -> float | None:
    """Parse a Spanish-locale price string (comma decimal) to float.

    Args:
        raw: String like '1,529' or '1,669', or None / empty string.

    Returns:
        float in EUR/litre, or None if absent / zero / unparseable.
    """
    if not raw:
        return None
    try:
        val = float(str(raw).replace(",", "."))
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    # Normalisation guard: if somehow > 10, treat as cents and divide.
    if val > 10:
        val = round(val / 100, 3)
    return round(val, 3)


def _parse_station(raw: dict[str, Any], fecha: str | None) -> StationData:
    """Build a StationData dict from a raw MINETUR station record.

    Args:
        raw:   Single station dict from ListaEESSPrecio.
        fecha: Top-level 'Fecha' timestamp string from the response envelope.

    Returns:
        Populated StationData.
    """
    ideess = str(raw.get("IDEESS", "")).strip() or None
    brand = str(raw.get("Rótulo", "")).strip() or None
    address = str(raw.get("Dirección", "")).strip() or None
    county = str(raw.get("Provincia", "")).strip().title() or None

    lat = _parse_coord(raw.get("Latitud"))
    lng = _parse_coord(raw.get("Longitud (WGS84)"))

    # Build per-fuel prices
    prices: dict[str, float | None] = {}
    for api_field, data_key in _PRICE_FIELDS.items():
        prices[data_key] = _parse_price(raw.get(api_field))

    # E85 and AdBlue are not published by MINETUR; always None.
    prices["e85"] = None
    prices["adblue"] = None

    # Convert Fecha string ('14/06/2026 4:50:43') to an ISO-ish timestamp.
    # Store as-is; the sensor platform accepts any truthy string for lastupdated.
    lastupdated = _normalise_fecha(fecha)

    return {
        "unleaded": prices.get("unleaded"),
        "diesel": prices.get("diesel"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "lpg": prices.get("lpg"),
        "e85": prices.get("e85"),
        "adblue": prices.get("adblue"),
        "name": brand,  # MINETUR stations have no distinct name; brand serves as name
        "brand": brand,
        "address": address,
        "county": county,
        "latitude": lat,
        "longitude": lng,
        "lastupdated": lastupdated,
        "source_station_id": ideess,
    }


def _normalise_fecha(fecha: str | None) -> str | None:
    """Convert MINETUR 'DD/MM/YYYY H:MM:SS' timestamp to ISO 8601, or return as-is.

    Args:
        fecha: Raw timestamp string like '14/06/2026 4:50:43', or None.

    Returns:
        ISO 8601 string like '2026-06-14T04:50:43', or None if unparseable.
    """
    if not fecha:
        return None
    try:
        from datetime import datetime

        dt = datetime.strptime(fecha.strip(), "%d/%m/%Y %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        # Return raw string rather than None so lastupdated is still populated.
        return fecha
