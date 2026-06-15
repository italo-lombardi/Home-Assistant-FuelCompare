"""AuQldProvider — Queensland Fuel Prices Scheme (Australia).

Source: Queensland Government Fuel Prices Scheme (FPPS) via the Direct API.
Operator: Queensland Department of Energy and Public Works.

API overview
------------
The FPPS Direct API requires a subscriber token obtained through manual
registration via a Microsoft Forms form.  See:
  https://www.fuelpricesqld.com.au/

Two endpoints are called per poll cycle:

1. GET https://fppdirectapi-prod.fuelpricesqld.com.au/Subscriber/GetFullSiteDetails
   ?countryId=21&geoRegionLevel=3&geoRegionId=1

   Returns all fuel station site records.  Response body:
   {
     "S": [
       {
         "S": <site_id_int>,       # Site ID (integer)
         "N": "<name>",            # Station name
         "A": "<address>",         # Street address
         "B": "<brand>",           # Brand name (some records may omit this)
         "P": "<postcode>",        # Postcode string
         "Lat": <float>,           # WGS84 latitude
         "Lng": <float>,           # WGS84 longitude
       },
       ...
     ]
   }

2. GET https://fppdirectapi-prod.fuelpricesqld.com.au/Price/GetSitesPrices
   ?countryId=21&geoRegionLevel=3&geoRegionId=1

   Returns all current fuel prices.  Response body:
   {
     "SitePrices": [
       {
         "SiteId": <int>,          # Matches Site.S
         "FuelId": <int>,          # Fuel type code (see _FUELID_MAP)
         "Price": <int>,           # Price in tenths of a cent (divide by 10 → cents/L)
         "TransactionDateutc": "<ISO8601>",
         "CollectionMethod": "C",
       },
       ...
     ]
   }

Authentication
--------------
All requests must include the header:
  Authorization: FPDAPI SubscriberToken={token}

Without a valid token the server returns HTTP 403.  The provider stores the
token passed to __init__; if no token is supplied it falls back to an empty
string (which will fail with 403 from the live API).

Price normalisation
-------------------
The API returns prices in tenths of a cent (e.g. 1799 = 179.9 c/L).
Dividing by 10 yields cents/litre (e.g. 179.9 c/L).
The StationData normalisation rule (values > 10 → divide by 100) then
converts cents to AUD/litre (e.g. 1.799 A$/L) automatically in the
coordinator.  Therefore this provider stores the raw ``Price / 10`` value
(i.e. still > 10) and lets the coordinator handle the final /100 step.

Fuel type mapping (QLD FuelId → StationData key)
-------------------------------------------------
  2  → unleaded          (ULP / Unleaded 91)
  3  → diesel            (Standard diesel)
  4  → lpg               (Autogas LPG)
  5  → premium_unleaded  (PULP / Premium 95 RON)
  8  → premium_unleaded  (PULP 98 RON — lower price wins)
 10  → e85               (E85 flex-fuel)
 11  → premium_diesel    (Premium diesel — V-Power Diesel, etc.)
 12  → e10               (E10 / ULP+10% ethanol)
 14  → premium_diesel    (Additional premium diesel code)
 19  → e85               (Additional E85 code)

FuelId values not in the map are silently skipped.

Station lookup
--------------
STATION_LOOKUP_MODE = 'location_search': the config flow passes lat/lng and
radius_km; the provider fetches the full state dataset and filters locally.
CONFIG_MODE = 'station_id': the user selects (or enters) a station ID that is
the SiteId integer stored as a string (e.g. '1234').

Poll interval: 3600 seconds (1 hour) — matches the observed update cadence
of the FPPS data feed.

API availability
----------------
The live API (fppdirectapi-prod.fuelpricesqld.com.au) requires a registered
subscriber token.  Without one the provider raises ProviderError with a clear
registration guidance message.  async_list_stations returns [] gracefully on
any HTTP/network failure so the config flow does not crash.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km

_LOGGER = logging.getLogger(__name__)

# ── API constants ─────────────────────────────────────────────────────────────

_BASE_URL = "https://fppdirectapi-prod.fuelpricesqld.com.au"

_SITES_URL = (
    f"{_BASE_URL}/Subscriber/GetFullSiteDetails"
    "?countryId=21&geoRegionLevel=3&geoRegionId=1"
)
_PRICES_URL = (
    f"{_BASE_URL}/Price/GetSitesPrices?countryId=21&geoRegionLevel=3&geoRegionId=1"
)

# Generous timeout: QLD dataset has ~2000+ stations.
_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT * 4, 30))

# ── Fuel type mapping ─────────────────────────────────────────────────────────

# QLD FPPS FuelId integer → StationData key.
# When multiple FuelId values map to the same key, the lower (cheaper) price wins.
_FUELID_MAP: dict[int, str] = {
    2: "unleaded",  # ULP / 91 RON
    3: "diesel",  # Standard diesel
    4: "lpg",  # Autogas LPG
    5: "premium_unleaded",  # PULP 95 RON
    8: "premium_unleaded",  # PULP 98 RON (higher octane; keep lower price)
    10: "e85",  # E85 flex-fuel
    11: "premium_diesel",  # Premium diesel (e.g. Shell V-Power Diesel)
    12: "e10",  # E10 (91 RON + 10% ethanol)
    14: "premium_diesel",  # Additional premium diesel code (keep lower price)
    19: "e85",  # Additional E85 code
}


# ── Provider class ────────────────────────────────────────────────────────────


class AuQldProvider(BaseProvider):
    """Fetch Queensland fuel prices from the FPPS Direct API.

    A subscriber token issued by the Queensland Government is required.
    Register at: https://www.fuelpricesqld.com.au/

    The station is identified by its SiteId (integer, stored as string).
    Use the location search in the config flow to browse available stations.
    """

    COUNTRY = "AU"
    PROVIDER_KEY = "au_qld"
    LABEL = "Fuel Prices QLD (Australia)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600  # 1 hour — matches FPPS update cadence
    CURRENCY: ClassVar[str] = "A$"

    REQUIRES_API_KEY = True
    API_KEY_REGISTRATION_URL = "https://www.fuelpricesqld.com.au/"

    CAPABILITIES: frozenset[str] = frozenset(
        {
            # Fuel prices
            "unleaded",
            "e10",
            "diesel",
            "premium_unleaded",
            "premium_diesel",
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
        "Enter the QLD FPPS SiteId for your station (numeric string, e.g. '61403154'). "
        "Use the location search to browse stations near you."
    )

    def __init__(
        self,
        station_id: str,
        api_key: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        county: str | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  FPPS SiteId as a string (e.g. ``'61403154'``).
            api_key:     FPPS subscriber token.  Required for live API access.
                         Register at https://www.fuelpricesqld.com.au/
            latitude:    Reference latitude for location-based searches.
            longitude:   Reference longitude for location-based searches.
            radius_km:   Search radius in kilometres (default 10.0).
            county:      Not used by this provider; accepted for API uniformity.
        """
        self._station_id = station_id
        self._api_key = api_key or ""
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0
        self._county = county  # stored for informational purposes; not used

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for the given SiteId.

        Fetches the full QLD FPPS dataset (sites + prices) and extracts the
        station identified by ``station_id`` (the FPPS ``S`` field as string).

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: FPPS SiteId string (e.g. ``'61403154'``).

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: Station not found in API response, API key missing /
                           invalid, or the response structure is unexpected.
        """
        sites_raw, prices_raw = await self._fetch_raw(session)
        site_map, prices_map, timestamps_map = _build_index(sites_raw, prices_raw)

        site = site_map.get(station_id)
        if site is None:
            raise ProviderError(
                f"QLD FPPS: SiteId '{station_id}' not found in the dataset. "
                "Verify the SiteId is correct or re-run the location search."
            )

        prices = prices_map.get(station_id, {})
        lastupdated = timestamps_map.get(station_id)
        return _build_station_data(site, prices, station_id, lastupdated)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        Args:
            session:    aiohttp ClientSession.
            station_id: FPPS SiteId string.

        Returns:
            Station name string or None on any failure.
        """
        try:
            sites_raw, _ = await self._fetch_raw(session)
            site_map, _, _ts = _build_index(sites_raw, [])
            site = site_map.get(station_id)
            if site:
                return site.get("N") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "AuQldProvider: failed to fetch station name for %s: %s",
                station_id,
                err,
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (site_id, display_label) pairs for stations near a location.

        Fetches the full QLD FPPS dataset, filters stations within
        ``radius_km`` of (``lat``, ``lng``), and returns them sorted by the
        cheapest available fuel price.

        Args:
            session:   aiohttp ClientSession.
            lat:       Centre latitude for the search (float).
            lng:       Centre longitude for the search (float).
            radius_km: Search radius in kilometres (float, default 10.0).

        Returns:
            List of (site_id_str, "Brand Name — Diesel A$1.79 / Unleaded A$1.83")
            tuples ordered cheapest-first.  Empty list on any failure.
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
                "AuQldProvider: async_list_stations called without lat/lng — returning []"
            )
            return []

        try:
            sites_raw, prices_raw = await self._fetch_raw(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("AuQldProvider: async_list_stations fetch failed: %s", err)
            return []

        site_map, prices_map, _ts = _build_index(sites_raw, prices_raw)

        result: list[tuple[str, str, float]] = []
        for site_id_str, site in site_map.items():
            try:
                s_lat = float(site["Lat"])
                s_lng = float(site["Lng"])
            except (KeyError, TypeError, ValueError):
                continue

            dist = haversine_km(lat, lng, s_lat, s_lng)
            if dist > radius_km:
                continue

            prices = prices_map.get(site_id_str, {})
            name: str = site.get("N") or "Unknown"
            brand: str = site.get("B") or ""
            display_name = (
                f"{brand} — {name}"
                if brand and brand.lower() not in name.lower()
                else name
            )

            # Build price label; prices are already in AUD/litre.
            price_parts: list[str] = []
            sort_price = 9999.0

            diesel_price = prices.get("diesel")
            unleaded_price = prices.get("unleaded")
            e10_price = prices.get("e10")

            if diesel_price is not None:
                price_parts.append(f"Diesel A${diesel_price:.3f}")
                sort_price = min(sort_price, diesel_price)
            if unleaded_price is not None:
                price_parts.append(f"Unleaded A${unleaded_price:.3f}")
                sort_price = min(sort_price, unleaded_price)
            elif e10_price is not None:
                price_parts.append(f"E10 A${e10_price:.3f}")
                sort_price = min(sort_price, e10_price)

            label = (
                f"{display_name} — {' / '.join(price_parts)}"
                if price_parts
                else display_name
            )
            result.append((site_id_str, label, sort_price))

        result.sort(key=lambda x: x[2])
        return [(sid, label) for sid, label, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Return the Authorization and Accept headers for all API requests."""
        return {
            "Authorization": f"FPDAPI SubscriberToken={self._api_key}",
            "Accept": "application/json",
            "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
        }

    async def _fetch_raw(self, session: ClientSession) -> tuple[list[dict], list[dict]]:
        """Fetch and return (sites_list, prices_list) from the QLD FPPS API.

        Returns:
            Tuple of (site records list, price records list).

        Raises:
            ProviderError: HTTP 403 (bad/missing token) or unexpected structure.
            aiohttp.ClientError: Network errors (coordinator converts to UpdateFailed).
        """
        headers = self._auth_headers()

        # Fetch sites and prices concurrently.
        sites_task = self._get_json(session, _SITES_URL, headers, "sites")
        prices_task = self._get_json(session, _PRICES_URL, headers, "prices")
        sites_payload, prices_payload = await asyncio.gather(sites_task, prices_task)

        sites_list: list[dict] = sites_payload.get("S") or []
        prices_list: list[dict] = prices_payload.get("SitePrices") or []

        return sites_list, prices_list

    async def _get_json(
        self,
        session: ClientSession,
        url: str,
        headers: dict[str, str],
        label: str,
    ) -> dict[str, Any]:
        """Perform a GET request and return the parsed JSON payload.

        Args:
            session: aiohttp ClientSession.
            url:     Full URL to fetch.
            headers: Request headers including Authorization.
            label:   Human-readable label for error messages ('sites'/'prices').

        Returns:
            Parsed JSON dict.

        Raises:
            ProviderError: HTTP 403 (token rejected) or unexpected response.
            aiohttp.ClientError: Network / timeout errors.
        """
        _LOGGER.debug("AuQldProvider: fetching %s from %s", label, url)
        async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status == 403:
                raise ProviderError(
                    "QLD FPPS API returned HTTP 403 Forbidden. "
                    "Your subscriber token may be invalid or missing. "
                    f"Register at {AuQldProvider.API_KEY_REGISTRATION_URL}"
                )
            try:
                resp.raise_for_status()
            except ClientResponseError as err:
                raise ProviderError(
                    f"QLD FPPS API returned HTTP {resp.status} for {label}: {err}"
                ) from err
            payload: dict[str, Any] = await resp.json(content_type=None)

        return payload


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_index(
    sites_list: list[dict],
    prices_list: list[dict],
) -> tuple[dict[str, dict], dict[str, dict[str, float]], dict[str, str]]:
    """Build lookup dicts from raw API response lists.

    Args:
        sites_list:  List of site records from GetFullSiteDetails (``payload["S"]``).
        prices_list: List of price records from GetSitesPrices (``payload["SitePrices"]``).

    Returns:
        Tuple of:
          site_map    — {site_id_str: site_dict}
          prices_map  — {site_id_str: {StationData_key: price_aud_float}}

    Prices are stored as **AUD/litre** (``raw_price / 10 / 100``).
    When multiple FuelId values map to the same StationData key, the lower
    (cheaper) value is kept.
    """
    site_map: dict[str, dict] = {}
    for site in sites_list:
        raw_id = site.get("S")
        if raw_id is not None:
            site_map[str(raw_id)] = site

    prices_map: dict[str, dict[str, float]] = {}
    timestamps_map: dict[str, str] = {}
    for entry in prices_list:
        raw_site_id = entry.get("SiteId")
        if raw_site_id is None:
            continue
        site_id_str = str(raw_site_id)

        # Capture the first available timestamp for this station.
        if site_id_str not in timestamps_map:
            tx = entry.get("TransactionDateutc") or entry.get("LastConfirmedDate")
            if tx:
                timestamps_map[site_id_str] = tx

        fuel_id = entry.get("FuelId")
        if fuel_id is None:
            continue
        data_key = _FUELID_MAP.get(int(fuel_id))
        if data_key is None:
            continue  # unknown fuel type — skip

        raw_price = entry.get("Price")
        if raw_price is None:
            continue
        try:
            # API returns price in tenths of a cent; convert to cents/litre.
            price_cents = float(raw_price) / 10.0
        except (ValueError, TypeError):
            continue
        if price_cents <= 0:
            continue
        # Sanity check: valid pump prices are between 50 and 999 c/L.
        if not (50 <= price_cents <= 999):
            continue
        # Convert cents/litre to AUD/litre.
        price_aud = round(price_cents / 100.0, 4)

        station_prices = prices_map.setdefault(site_id_str, {})
        # When two fuel IDs map to the same StationData key, keep the lower price.
        existing = station_prices.get(data_key)
        if existing is None or price_aud < existing:
            station_prices[data_key] = price_aud

    return site_map, prices_map, timestamps_map


def _build_station_data(
    site: dict[str, Any],
    prices: dict[str, float],
    station_id: str,
    lastupdated: str | None = None,
) -> StationData:
    """Assemble a StationData dict from a site record and its price map.

    Args:
        site:       Single site dict from the FPPS sites array.
        prices:     Dict of {StationData_key: price_aud_float} for this station.
        station_id: SiteId string (used for source_station_id).

    Returns:
        Populated StationData dict.  Price values are in AUD/litre.
    """
    name: str | None = site.get("N") or None
    brand: str | None = site.get("B") or None
    address: str | None = site.get("A") or None
    postcode: str | None = str(site.get("P")) if site.get("P") else None

    # Build a simple county string from the postcode when available.
    # The API does not provide a state/county field; "QLD" is implicit.
    county: str | None = "QLD"

    try:
        latitude: float | None = float(site["Lat"]) if "Lat" in site else None
    except (TypeError, ValueError):
        latitude = None
    try:
        longitude: float | None = float(site["Lng"]) if "Lng" in site else None
    except (TypeError, ValueError):
        longitude = None

    # Append postcode to address when address does not already contain it.
    if address and postcode and postcode not in address:
        address = f"{address} {postcode}"

    _LOGGER.debug(
        "AuQldProvider: built station data for SiteId=%s name=%s "
        "unleaded=%s diesel=%s e10=%s",
        station_id,
        name,
        prices.get("unleaded"),
        prices.get("diesel"),
        prices.get("e10"),
    )

    return {
        "unleaded": prices.get("unleaded"),
        "e10": prices.get("e10"),
        "diesel": prices.get("diesel"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "premium_diesel": prices.get("premium_diesel"),
        "lpg": prices.get("lpg"),
        "e85": prices.get("e85"),
        "name": name,
        "brand": brand,
        "tablename": brand,
        "address": address,
        "county": county,
        "latitude": latitude,
        "longitude": longitude,
        "lastupdated": lastupdated,
        "source_station_id": station_id,
    }
