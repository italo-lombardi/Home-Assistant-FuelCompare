"""AuNswProvider — NSW FuelCheck fuel price data (Australia).

Source: NSW Government FuelCheck via OneGov API.
Endpoint: GET https://api.onegov.nsw.gov.au/FuelCheckApp/v1/fuel/prices

Endpoint provenance
-------------------
The ``FuelCheckApp/v1`` path is the **undocumented mobile-app endpoint** used
by the official NSW FuelCheck iOS/Android apps.  It is distinct from the
published REST API listed in the NSW Government API catalog
(https://api.nsw.gov.au/Product/Index/22), which requires OAuth2 client
credentials and operates under a 2,500-calls/month free tier.

The mobile-app endpoint requires **only** the ``requesttimestamp`` header
(ISO 8601 UTC, e.g. ``2026-06-14T02:51:07Z``) — no API key, no OAuth token.

This approach is not novel: it is the same technique used by the
``nsw-fuel-api-client`` PyPI package (MIT licensed,
https://pypi.org/project/nsw-fuel-api-client/), which has relied on this
endpoint since at least 2019.  The endpoint has remained stable across that
period with no observed breaking changes.

Auth: No API key or OAuth required. Only required header is ``requesttimestamp``
      with an ISO 8601 UTC value (e.g. ``2026-06-14T02:51:07Z``).

The API returns all ~3200+ stations in NSW and TAS in a single response.
There is no per-station or per-radius endpoint — the provider fetches the full
dataset and filters locally by distance (STATION_LOOKUP_MODE=location_search).

Response structure:
  {
    "stations": [
      {
        "brandid": "...", "stationid": "...", "brand": "...", "code": "972",
        "name": "...", "address": "...",
        "location": {"latitude": -33.5, "longitude": 151.3},
        "isAdBlueAvailable": false
      },
      ...
    ],
    "prices": [
      {"stationcode": "972", "fueltype": "U91", "price": 167.9, "lastupdated": "13/06/2026 01:35:20"},
      ...
    ]
  }

Join key: prices[].stationcode == stations[].code

Price unit: CENTS per litre (float). The StationData normalisation rule
(values >10 divided by 100) converts these automatically to dollars/litre.

lastupdated format: "DD/MM/YYYY HH:MM:SS" (Australian day-first format, local Sydney time).
Parsed with strptime("%d/%m/%Y %H:%M:%S") and stored as ISO 8601 string with
Australia/Sydney offset (e.g. "2026-06-13T01:35:20+10:00").

Fuel type mapping (live API fueltype values → StationData keys):
  E10  → e10               (E10 ethanol blend)
  U91  → unleaded          (standard 91 RON unleaded)
  P95  → premium_unleaded  (premium 95 RON; task brief said U95 but live API uses P95)
  P98  → premium_unleaded  (premium 98 RON; mapped to same key — highest premium wins)
  DL   → diesel            (standard diesel)
  PDL  → premium_diesel    (premium diesel)
  E85  → e85               (85% ethanol flex-fuel)
  LPG  → lpg               (autogas LPG)
  B20  → (skipped — no StationData key for biodiesel blend)
  EV   → (skipped — electric charging, cents/kWh not cents/L, not a fuel)

Station ``address`` is a single combined string (e.g.
"307-313 Ocean Beach Road, UMINA BEACH NSW 2257"). No separate suburb/state
fields exist; county is extracted via regex matching the state abbreviation.

Poll interval: 3600 seconds (1 hour).  The mobile-app endpoint has no
documented rate limit, but polling more frequently than hourly is unnecessary
because the ``lastupdated`` timestamps in the response only advance roughly
once per hour.  The official OAuth2 API caps the free tier at 2,500 calls/month
(~3.4 calls/hour), so 3,600 s also keeps usage well within that budget if the
endpoint family ever enforces the same limit.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

_API_URL = "https://api.onegov.nsw.gov.au/FuelCheckApp/v1/fuel/prices"
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 6)  # large payload ~3200 stations

# Fueltype → StationData key mapping (live API fueltype strings)
_FUELTYPE_MAP: dict[str, str] = {
    "E10": "e10",
    "U91": "unleaded",
    "P95": "premium_unleaded",
    "P98": "premium_unleaded",  # 98 RON also maps to premium_unleaded
    "DL": "diesel",
    "PDL": "premium_diesel",
    "E85": "e85",
    "LPG": "lpg",
    # B20 (biodiesel blend) and EV (electric charging) intentionally excluded
}

# Regex to extract the state abbreviation embedded in the address string.
# Addresses follow the pattern: "... SUBURB STATE POSTCODE"
# e.g. "307-313 Ocean Beach Road, UMINA BEACH NSW 2257"
_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4}\b", re.IGNORECASE)

_LASTUPDATED_FMT = "%d/%m/%Y %H:%M:%S"


class AuNswProvider(BaseProvider):
    """Fetch Australian fuel prices from the NSW FuelCheck API.

    All ~3200+ stations in NSW and TAS are returned in a single API call.
    The user selects a location (lat/lng + radius); the provider fetches the
    full dataset and returns stations within the radius sorted by price.

    Station ID is the ``code`` field (numeric string, e.g. ``"972"``).
    This is the join key between the stations and prices arrays.
    """

    COUNTRY = "AU"
    PROVIDER_KEY = "au_nsw"
    LABEL = "FuelCheck NSW (Australia)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = (
        3600  # hourly — matches source update cadence; see module docstring
    )
    CURRENCY: ClassVar[str] = "A$"

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "e10",
            "unleaded",
            "premium_unleaded",
            "diesel",
            "premium_diesel",
            "e85",
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
        "Enter the FuelCheck NSW station code (numeric string). "
        "Use the location search to browse available stations."
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
            station_id:  FuelCheck station ``code`` field (e.g. ``"972"``).
            county:      Unused for this provider (no county-based filtering);
                         stored for informational purposes only.
            latitude:    User's reference latitude for location-based searches.
            longitude:   User's reference longitude for location-based searches.
            radius_km:   Search radius in kilometres for location-based searches.
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
        """Fetch and return normalised station data for the given station code.

        Fetches the full NSW FuelCheck dataset and extracts the station
        identified by ``station_id`` (the ``code`` field).

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: FuelCheck station code string (e.g. ``"972"``).

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: Station code not found in API response, or the
                           response structure is invalid.
        """
        raw = await self._fetch_raw(session)
        station_map, prices_map = _build_index(raw)

        station = station_map.get(station_id)
        if station is None:
            raise ProviderError(
                f"Station code '{station_id}' not found in NSW FuelCheck dataset. "
                "Verify the station code is correct."
            )

        prices = prices_map.get(station_id, {})
        raw_prices: list[dict[str, Any]] = raw.get("prices") or []
        return _build_station_data_with_ts(station, prices, raw_prices, station_id)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        For CONFIG_MODE='location' providers the config flow uses the
        auto-generated location title, so returning None is acceptable.
        This implementation makes a best-effort lookup anyway.

        Args:
            session:    aiohttp ClientSession.
            station_id: FuelCheck station code.

        Returns:
            Station name string or None on any failure.
        """
        try:
            raw = await self._fetch_raw(session)
            station_map, _ = _build_index(raw)
            station = station_map.get(station_id)
            if station:
                return station.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_code, display_label) pairs for stations near a location.

        Fetches the full dataset, filters stations within ``radius_km`` of
        (``lat``, ``lng``), and returns them sorted alphabetically by label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the search (float).
            lng:       Centre longitude for the search (float).
            radius_km: Search radius in kilometres (float, default 10.0).

        Returns:
            List of (station_code, "Brand/Name, Address (#CODE1234)")
            tuples ordered alphabetically by label, empty list on any failure.
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

        station_map, _ = _build_index(raw)

        result: list[tuple[str, str]] = []
        for code, station in station_map.items():
            loc = station.get("location") or {}
            try:
                s_lat = float(loc["latitude"])
                s_lng = float(loc["longitude"])
            except (KeyError, TypeError, ValueError):
                continue

            dist = _haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            name = station.get("name") or "Unknown"
            brand = station.get("brand") or ""
            address = station.get("address") or ""
            display_name = (
                f"{brand}, {name}"
                if brand and brand.lower() not in name.lower()
                else name
            )
            label = f"{display_name}, {address} (#{code[:8]})"
            result.append((code, label))

        result.sort(key=lambda x: x[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_raw(self, session: ClientSession) -> dict[str, Any]:
        """Fetch the full FuelCheck NSW dataset.

        Returns:
            Parsed JSON response dict with ``stations`` and ``prices`` keys.

        Raises:
            aiohttp.ClientError: Network or HTTP error (let coordinator handle).
            ProviderError: Response is missing expected top-level keys.
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        headers = {
            "requesttimestamp": ts,
            "Accept": "application/json",
            "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
        }
        _LOGGER.debug("Fetching NSW FuelCheck data with requesttimestamp=%s", ts)
        async with session.get(_API_URL, headers=headers, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json(content_type=None)

        # The API wraps data under a top-level key in some response shapes;
        # handle both flat and nested forms.
        if "stations" not in payload and "prices" not in payload:
            inner = payload.get("data") or payload
            if "stations" not in inner:
                raise ProviderError(
                    "NSW FuelCheck API returned an unexpected response structure. "
                    f"Top-level keys: {list(payload.keys())}"
                )
            return inner  # type: ignore[return-value]
        return payload


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_index(
    raw: dict[str, Any],
) -> tuple[dict[str, dict], dict[str, dict[str, float]]]:
    """Build lookup dicts from the raw API response.

    Args:
        raw: Parsed JSON response with ``stations`` and ``prices`` lists.

    Returns:
        Tuple of:
          station_map  — {code: station_dict}
          prices_map   — {code: {StationData_key: price_cents_float}}

    The prices_map stores the RAW price values (in cents/litre) as returned
    by the API. The StationData normalisation rule (>10 → /100) converts
    them to dollars automatically in the coordinator. When multiple prices
    exist for the same fueltype→StationData key (e.g. P95 and P98 both map
    to premium_unleaded), the lower (cheaper) value is kept.
    """
    stations: list[dict] = raw.get("stations") or []
    prices: list[dict] = raw.get("prices") or []

    station_map: dict[str, dict] = {}
    for s in stations:
        code = s.get("code")
        if code:
            station_map[str(code)] = s

    prices_map: dict[str, dict[str, float]] = {}
    for entry in prices:
        code = entry.get("stationcode")
        if not code:
            continue
        code = str(code)

        fueltype = entry.get("fueltype", "")
        data_key = _FUELTYPE_MAP.get(fueltype)
        if data_key is None:
            continue  # EV, B20, unknown — skip

        raw_price = entry.get("price")
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue

        station_prices = prices_map.setdefault(code, {})
        # When two fueltypes map to the same StationData key (P95 + P98 →
        # premium_unleaded), keep the lower price (better for the user).
        existing = station_prices.get(data_key)
        if existing is None or price < existing:
            station_prices[data_key] = price

    return station_map, prices_map


_SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def _parse_lastupdated(raw_ts: str | None) -> str | None:
    """Convert "DD/MM/YYYY HH:MM:SS" → ISO 8601 string with Sydney offset, or None.

    The FuelCheck API returns timestamps in Australian day-first format in
    local Sydney time (AEST UTC+10 or AEDT UTC+11 depending on DST). The
    value is NOT UTC — tagging it as UTC would misrepresent the time by 10-11
    hours. We attach the correct Australia/Sydney timezone so the offset in
    the ISO string (e.g. "+10:00" or "+11:00") is accurate.

    Args:
        raw_ts: Raw timestamp string from the API (e.g. "13/06/2026 01:35:20").

    Returns:
        ISO 8601 string e.g. "2026-06-13T01:35:20+10:00", or None on failure.
    """
    if not raw_ts:
        return None
    try:
        dt = datetime.strptime(raw_ts.strip(), _LASTUPDATED_FMT)
        # Localise to Australia/Sydney (AEST+10 / AEDT+11) — the API returns
        # local Sydney wall-clock time, not UTC.
        return dt.replace(tzinfo=_SYDNEY_TZ).isoformat()
    except ValueError:
        _LOGGER.debug("Could not parse lastupdated timestamp: %r", raw_ts)
        return None


def _extract_county(address: str | None) -> str | None:
    """Extract the Australian state abbreviation from a combined address string.

    FuelCheck addresses embed the state abbreviation before the postcode:
    e.g. "307-313 Ocean Beach Road, UMINA BEACH NSW 2257" → "NSW".

    Args:
        address: Combined address string from the API.

    Returns:
        State abbreviation string (e.g. "NSW", "TAS") or None if not found.
    """
    if not address:
        return None
    match = _STATE_RE.search(address)
    if match:
        return match.group(1).upper()
    return None


def _normalise_price(price: float | None) -> float | None:
    """Convert a cents/litre value to AUD/litre using the >10 → /100 rule.

    The NSW FuelCheck API returns prices in cents/litre (e.g. 189.9).
    Dividing by 100 gives AUD/litre (e.g. 1.899), consistent with the
    pattern used by other providers in this package (e.g. ie_fuelcompare,
    be_carbu, at_econtrol).

    Args:
        price: Raw price value from the API (cents/litre) or None.

    Returns:
        Price in AUD/litre, or None if the input is None.
    """
    if price is None:
        return None
    if price > 10:
        return price / 100
    return price


def _build_station_data(
    station: dict[str, Any],
    prices: dict[str, float],
) -> StationData:
    """Assemble a StationData dict from a station record and its price map.

    Args:
        station: Single station dict from the API ``stations`` array.
        prices:  Dict of {StationData_key: price_in_cents} for this station.

    Returns:
        Populated StationData dict with all relevant CAPABILITIES keys set.
        Price values are AUD/litre after the >10 → /100 cents conversion.
        Non-price values are None when not available from the source.
    """
    name: str | None = station.get("name") or None
    brand: str | None = station.get("brand") or None
    address: str | None = station.get("address") or None
    county: str | None = _extract_county(address)

    loc = station.get("location") or {}
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

    # Determine the best lastupdated timestamp available for this station.
    # The prices_map loses the per-price timestamp; the raw lastupdated is
    # resolved in _build_index. For single-station fetches we pass it
    # separately via _build_station_data_with_ts when available.
    # Here we just set None — callers that have the raw prices list should
    # use _build_station_data_with_ts instead.
    lastupdated: str | None = None

    return {
        "e10": _normalise_price(prices.get("e10")),
        "unleaded": _normalise_price(prices.get("unleaded")),
        "premium_unleaded": _normalise_price(prices.get("premium_unleaded")),
        "diesel": _normalise_price(prices.get("diesel")),
        "premium_diesel": _normalise_price(prices.get("premium_diesel")),
        "e85": _normalise_price(prices.get("e85")),
        "lpg": _normalise_price(prices.get("lpg")),
        "name": name,
        "brand": brand,
        "address": address,
        "county": county,
        "latitude": latitude,
        "longitude": longitude,
        "lastupdated": lastupdated,
        "source_station_id": station.get("code") or None,
    }


def _build_station_data_with_ts(
    station: dict[str, Any],
    prices: dict[str, float],
    raw_prices: list[dict[str, Any]],
    station_code: str,
) -> StationData:
    """Like _build_station_data but also resolves lastupdated from raw prices.

    Args:
        station:      Station dict from the API.
        prices:       Pre-built {StationData_key: price_cents} map for this station.
        raw_prices:   Full prices array from the API response.
        station_code: Station code to filter raw_prices by.

    Returns:
        StationData dict with lastupdated populated from the most recent
        price record for this station.
    """
    data = _build_station_data(station, prices)

    # Find the most recent lastupdated across all price entries for this station.
    best_ts: str | None = None
    best_dt: datetime | None = None
    for entry in raw_prices:
        if str(entry.get("stationcode", "")) != station_code:
            continue
        raw_ts = entry.get("lastupdated")
        if not raw_ts:
            continue
        try:
            dt = datetime.strptime(raw_ts.strip(), _LASTUPDATED_FMT)
        except ValueError:
            continue
        if best_dt is None or dt > best_dt:
            best_dt = dt
            best_ts = raw_ts

    data["lastupdated"] = _parse_lastupdated(best_ts)
    return data
