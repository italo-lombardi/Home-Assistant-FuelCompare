"""AuVicProvider — Service Victoria Fair Fuel Open Data API (Australia VIC).

Source: Victorian Government Service Victoria Fair Fuel scheme.
Endpoint: GET https://api.fuel.service.vic.gov.au/open-data/v1/fuel/prices
Licence: CC BY 4.0 (© State of Victoria, accessed via Service Victoria Platform).

Authentication
--------------
The API requires two custom headers on every request:

  x-consumer-id:   A UUID registered via the Service Victoria developer portal.
                   Registration is at https://developer.service.vic.gov.au/
                   and is subject to manual approval gating.
                   Any unregistered UUID receives HTTP 403 (ERR_UNAUTHORIZED).

  x-transactionid: A fresh UUID generated per-request for idempotency
                   tracing. Any valid UUID string is accepted.

The consumer-id field is therefore an API key under a different name.
REQUIRES_API_KEY is set True; the config flow's API key field collects the
consumer UUID from the user.  They must have previously registered it at the
Service Victoria developer portal.

Response structure (real API, as of 2026)
------------------------------------------
The endpoint returns a single large JSON object covering all ~3 000+ Victorian
fuel stations:

  {
    "fuelPriceDetails": [
      {
        "fuelStation": {
          "id": "56ab12ef-...",
          "name": "7-Eleven Melbourne CBD",
          "brandId": "brand-uuid-...",
          "address": "123 Swanston St",
          "suburb": "Melbourne",
          "state": "VIC",
          "postcode": "3000",
          "location": {"latitude": -37.813, "longitude": 144.963}
        },
        "fuelPrices": [
          {"fuelType": "U91", "price": 1.759, "isAvailable": true,
           "updatedAt": "2026-06-13T06:00:00.000Z"},
          ...
        ],
        "updatedAt": "2026-06-13T06:00:00.000Z"
      },
      ...
    ]
  }

Note: The task brief describes a legacy v0 response shape
(``{stations: [{id, name, brand, address, location:{lat,lng},
fuels:[{fueltype, price}]}]}``) which does NOT match the live API.
This provider implements the real v1 response structure confirmed by inspection
of the python-vicfuelwatch and vic-fuel-saver open-source clients.

Fuel type mapping
-----------------
Live API fuelType strings → StationData keys:

  U91   → unleaded           (standard 91 RON unleaded)
  DSL   → diesel             (standard diesel)
  PDSL  → premium_diesel     (premium diesel)
  E10   → e10                (E10 ethanol blend)
  P95   → premium_unleaded   (premium 95 RON)
  P98   → premium_unleaded   (premium 98 RON; lower price wins when both present)
  LPG   → lpg                (autogas LPG)
  E85   → e85                (85% ethanol flex-fuel)
  B20   → (skipped — biodiesel blend, no StationData key)
  LNG   → (skipped — liquefied natural gas, no StationData key)
  CNG   → (skipped — compressed natural gas, no StationData key)

Price unit: the API returns float values in AUD/litre (e.g. 1.759).
These are already in dollars; no cents-to-dollars conversion is applied.

Data lag and poll interval
--------------------------
Price data is delayed approximately 24 hours from retailer submission.
POLL_INTERVAL_SECONDS = 86400 (daily). The API rate-limits to 10 req/60 s.

CONFIG_MODE = 'station_id' (user enters a station UUID).
STATION_LOOKUP_MODE = 'location_search' (user finds their station via lat/lng
search, then selects from the results list).

The full dataset (~3000+ stations) is fetched in a single call. Distance
filtering is applied locally using haversine_km.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

_API_URL = "https://api.fuel.service.vic.gov.au/open-data/v1/fuel/prices"

# Large payload (~3000+ stations); allow 6× the default timeout.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 6)

# Live API fuelType strings → StationData keys.
# B20 (biodiesel blend), LNG, CNG are intentionally excluded — no StationData key.
_FUELTYPE_MAP: dict[str, str] = {
    "U91": "unleaded",
    "DSL": "diesel",
    "PDSL": "premium_diesel",
    "E10": "e10",
    "P95": "premium_unleaded",
    "P98": "premium_unleaded",  # 98 RON — lower price wins when both P95 and P98 present
    "LPG": "lpg",
    "E85": "e85",
}

# Default search radius used when no radius_km kwarg is supplied.
_DEFAULT_RADIUS_KM = 10.0


class AuVicProvider(BaseProvider):
    """Fetch Victorian fuel prices from the Service Victoria Fair Fuel API.

    All stations across Victoria are returned in a single API call.
    The user identifies their station by UUID via the location-search picker
    in the config flow.

    Station ID is the ``id`` field on the ``fuelStation`` object.
    """

    COUNTRY = "AU"
    PROVIDER_KEY = "au_vic"
    LABEL = "Servo Saver VIC (Australia)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    # Data is delayed ~24h from retailer submission; poll once daily.
    POLL_INTERVAL_SECONDS = 86400
    CURRENCY: ClassVar[str] = "A$"

    # Consumer ID is a registered UUID — equivalent to an API key.
    REQUIRES_API_KEY = True
    API_KEY_REGISTRATION_URL = "https://developer.service.vic.gov.au/"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "unleaded",
            "diesel",
            "premium_diesel",
            "e10",
            "premium_unleaded",
            "lpg",
            "e85",
            # Station identity
            "name",
            "brand",
            "address",
            "county",
            "latitude",
            "longitude",
            # Timing
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Enter the Service Victoria fuel station UUID.  "
        "Use the location search to browse stations near your address.  "
        "Your consumer ID must be registered at developer.service.vic.gov.au before "
        "the API will respond successfully."
    )

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        api_key: str | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  Service Victoria fuel station UUID (``fuelStation.id``).
            county:      Unused for this provider (no county-based filtering);
                         stored for interface compatibility.
            latitude:    User's reference latitude for location-based searches.
            longitude:   User's reference longitude for location-based searches.
            radius_km:   Search radius in kilometres.
            api_key:     Registered consumer UUID.  Required for the API to
                         respond.  If absent, all requests return HTTP 403.
        """
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else _DEFAULT_RADIUS_KM
        self._api_key = api_key or ""

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for the given station UUID.

        Fetches the full Victorian fuel price dataset and extracts the station
        identified by ``station_id``.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Service Victoria fuel station UUID.

        Returns:
            StationData dict with all CAPABILITIES keys populated (may be
            None when the source has no data for a field).

        Raises:
            ProviderError: Station UUID not found in the API response, or the
                           response lacks the expected ``fuelPriceDetails`` key.
            ClientResponseError: Propagated from aiohttp on HTTP errors
                                 (e.g. 403 on invalid/unregistered consumer-id).
        """
        raw = await self._fetch_raw(session)
        station_map = _build_station_map(raw)

        entry = station_map.get(station_id)
        if entry is None:
            raise ProviderError(
                f"Station UUID '{station_id}' not found in the Service Victoria "
                "fuel price dataset.  Verify the UUID is correct by using the "
                "location search in the configuration flow."
            )

        return _build_station_data(entry)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Args:
            session:    aiohttp ClientSession.
            station_id: Service Victoria fuel station UUID.

        Returns:
            Station name string, or None on any failure.
        """
        try:
            raw = await self._fetch_raw(session)
            station_map = _build_station_map(raw)
            entry = station_map.get(station_id)
            if entry:
                return entry.get("fuelStation", {}).get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_uuid, display_label) pairs for stations near a location.

        Fetches the full dataset, filters stations within ``radius_km`` of
        (``lat``, ``lng``), and returns them sorted by the cheapest available
        fuel price.

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the search (float).
            lng:       Centre longitude for the search (float).
            radius_km: Search radius in kilometres (float, default 10.0).

        Returns:
            List of (station_uuid, "Brand/Name, Address (#uuid[:8])")
            tuples ordered alphabetically by label.  Returns empty list on any failure.
        """
        lat: float | None = (
            kwargs["lat"] if kwargs.get("lat") is not None else self._latitude
        )  # type: ignore[assignment]
        lng: float | None = (
            kwargs["lng"] if kwargs.get("lng") is not None else self._longitude
        )  # type: ignore[assignment]
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        if lat is None or lng is None:
            _LOGGER.debug("async_list_stations called without lat/lng — returning []")
            return []

        try:
            raw = await self._fetch_raw(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        station_map = _build_station_map(raw)

        result: list[tuple[str, str]] = []
        for sid, entry in station_map.items():
            fs = entry.get("fuelStation") or {}
            loc = fs.get("location") or {}
            try:
                s_lat = float(loc["latitude"])
                s_lng = float(loc["longitude"])
            except (KeyError, TypeError, ValueError):
                continue

            dist = haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            label = _build_display_label(fs, sid)
            result.append((sid, label))

        result.sort(key=lambda x: x[1].lower())
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_raw(self, session: ClientSession) -> dict[str, Any]:
        """Fetch the full Service Victoria fuel price dataset.

        Returns:
            Parsed JSON response dict with ``fuelPriceDetails`` key.

        Raises:
            ClientResponseError: On HTTP errors (including 403 for invalid
                                 consumer-id).
            ProviderError: When the response lacks the expected structure.
        """
        headers = {
            "x-consumer-id": self._api_key,
            "x-transactionid": str(uuid.uuid4()),
            "Accept": "application/json",
            "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
        }

        _LOGGER.debug("Fetching Service Victoria fuel prices")
        async with session.get(
            _API_URL,
            headers=headers,
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status == 403:
                _LOGGER.warning(
                    "Service Victoria API returned HTTP 403.  "
                    "The consumer-id may not be registered or may have expired.  "
                    "Register at https://developer.service.vic.gov.au/  "
                    "Please open an issue at "
                    "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues "
                    "if you believe your consumer-id is valid."
                )
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json(content_type=None)

        if "fuelPriceDetails" not in payload:
            raise ProviderError(
                "Service Victoria fuel API returned an unexpected response "
                f"structure.  Top-level keys: {list(payload.keys())}"
            )

        return payload


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_station_map(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index the raw API response by station UUID.

    Args:
        raw: Parsed JSON response with ``fuelPriceDetails`` list.

    Returns:
        Dict mapping station UUID → fuelPriceDetails entry dict.
        Entries with missing or empty station IDs are silently skipped.
    """
    details: list[dict] = raw.get("fuelPriceDetails") or []
    station_map: dict[str, dict[str, Any]] = {}
    for entry in details:
        fs = entry.get("fuelStation") or {}
        sid = fs.get("id")
        if sid:
            station_map[str(sid)] = entry
    return station_map


def _extract_prices(fuel_prices: list[dict]) -> dict[str, float]:
    """Extract and normalise fuel prices from a fuelPrices list.

    Maps API fuelType strings to StationData keys.  When two fuel types
    map to the same StationData key (e.g. P95 and P98 both map to
    ``premium_unleaded``), the lower (cheaper) price is kept.

    Args:
        fuel_prices: List of fuelPrice dicts from the API response.

    Returns:
        Dict of {StationData_key: price_aud_per_litre}.  Only keys with
        valid, positive prices are included.
    """
    prices: dict[str, float] = {}
    for entry in fuel_prices:
        fueltype = entry.get("fuelType", "")
        data_key = _FUELTYPE_MAP.get(fueltype)
        if data_key is None:
            continue  # B20, LNG, CNG, unknown — skip

        raw_price = entry.get("price")
        if raw_price is None:
            continue

        # isAvailable=False means the fuel type is not currently on offer;
        # skip these entries so unavailable prices are not surfaced.
        if not entry.get("isAvailable", True):
            continue

        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue

        # Keep the lower price when two fuel types share a StationData key
        existing = prices.get(data_key)
        if existing is None or price < existing:
            prices[data_key] = price

    return prices


def _build_station_data(entry: dict[str, Any]) -> StationData:
    """Assemble a StationData dict from a single fuelPriceDetails entry.

    Args:
        entry: A single element from the ``fuelPriceDetails`` list in the
               API response.

    Returns:
        Populated StationData dict.  Price values are in AUD/litre (not
        cents — the >10 → /100 coordinator rule does NOT apply here).
    """
    fs: dict[str, Any] = entry.get("fuelStation") or {}
    loc: dict[str, Any] = fs.get("location") or {}

    try:
        latitude: float | None = float(loc["latitude"]) if "latitude" in loc else None
    except (TypeError, ValueError):
        latitude = None
    try:
        longitude: float | None = (
            float(loc["longitude"]) if "longitude" in loc else None
        )
    except (TypeError, ValueError):
        longitude = None

    name: str | None = fs.get("name") or None
    address: str | None = fs.get("address") or None
    suburb: str | None = fs.get("suburb") or None
    state: str | None = fs.get("state") or None

    # Build a human-readable address from components when they are present.
    # The API splits address into separate fields; reconstruct a single string.
    if suburb or state:
        postcode = fs.get("postcode") or ""
        parts = [p for p in [address, suburb, state, postcode] if p]
        full_address: str | None = ", ".join(parts) if parts else None
    else:
        full_address = address

    # county is the Australian state abbreviation (VIC, NSW, etc.)
    county: str | None = state or None

    # Use updatedAt from the top-level entry (most recent across all fuel types).
    updated_at: str | None = entry.get("updatedAt") or None

    fuel_prices_raw: list[dict] = entry.get("fuelPrices") or []
    prices = _extract_prices(fuel_prices_raw)

    data: StationData = {
        # Fuel prices (AUD/litre; no cents conversion needed)
        "unleaded": prices.get("unleaded"),
        "diesel": prices.get("diesel"),
        "premium_diesel": prices.get("premium_diesel"),
        "e10": prices.get("e10"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "lpg": prices.get("lpg"),
        "e85": prices.get("e85"),
        # Station identity
        "name": name,
        "brand": None,  # brandId is a UUID; brand name requires a /brands lookup
        "address": full_address,
        "county": county,
        "latitude": latitude,
        "longitude": longitude,
        # Timing
        "lastupdated": updated_at,
        # Meta
        "source_station_id": fs.get("id") or None,
    }

    _LOGGER.debug("VIC FuelWatch parsed station %s", fs.get("id"))

    return data


def _build_display_label(
    fs: dict[str, Any],
    station_id: str,
) -> str:
    """Build a human-readable display label for the station picker.

    Format: ``"Brand/Name, Address (#uuid[:8])"``
    No price information is included so the label remains stable between polls.

    Args:
        fs:         The ``fuelStation`` dict from the API response.
        station_id: The station UUID string (used for the short suffix).

    Returns:
        Label string of the form ``"Brand/Name, Address (#abcd1234)"``.
        Falls back gracefully when name or address fields are missing.
    """
    name: str = fs.get("name") or "Unknown"
    address: str = fs.get("address") or ""
    suburb: str = fs.get("suburb") or ""

    # Build a compact address: prefer the street address, append suburb if present
    # and not already embedded in the street string.
    if suburb and suburb.lower() not in address.lower():
        full_addr = f"{address}, {suburb}" if address else suburb
    else:
        full_addr = address

    uuid_prefix = station_id[:8] if len(station_id) >= 8 else station_id

    if full_addr:
        return f"{name}, {full_addr} (#{uuid_prefix})"
    return f"{name} (#{uuid_prefix})"
