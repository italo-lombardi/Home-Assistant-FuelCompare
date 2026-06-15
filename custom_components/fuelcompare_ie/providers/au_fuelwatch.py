"""AuFuelwatchProvider — FuelWatch Western Australia (fuelwatch.wa.gov.au).

Source: Government of Western Australia FuelWatch scheme.
Endpoint: GET https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS
Auth: None.
Format: RSS 2.0 XML (UTF-8 with BOM, CRLF line endings).

WA pricing scheme: tomorrow's prices are published at 14:30 AWST (UTC+8).
Poll once daily at 14:45 AWST → POLL_INTERVAL_SECONDS = 86400.

Fuel product codes:
  1 = unleaded (ULP 91)
  2 = premium_unleaded (95)
  4 = diesel
  5 = lpg
  6 = e10

Station merge key: composite (latitude, longitude) string — both fields are
always present in the feed and use exactly 8 decimal places.

CONFIG_MODE = 'location' (user supplies a WA Region code + optional product).
STATION_LOOKUP_MODE = 'location_search' (browse stations by Region code).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

from aiohttp import ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_RSS_URL = "https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS"

# Fetch timeout: 3× the default; the feed can be large.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "text/xml,application/xml,*/*",
}

# Map provider fuel-type key → FuelWatch product code
_PRODUCT_CODES: dict[str, int] = {
    "unleaded": 1,
    "premium_unleaded": 2,
    "diesel": 4,
    "lpg": 5,
    "e10": 6,
}

# All products fetched when building a location overview (excludes lpg/e10 for
# the primary 3-request merge; they are fetched on demand in async_fetch).
_PRIMARY_PRODUCTS: list[str] = ["unleaded", "premium_unleaded", "diesel", "lpg", "e10"]

# Namespace-aware tag helpers for hyphenated names
_TAG_TRADING_NAME = "trading-name"
_TAG_SITE_FEATURES = "site-features"


class AuFuelwatchProvider(BaseProvider):
    """Fetch WA fuel prices from the FuelWatch RSS API.

    All stations within a Region are returned per-product request; the provider
    fetches multiple product feeds and merges them by (latitude, longitude).

    Station IDs are composite strings in the form '{lat},{lng}' using the exact
    8-decimal-place strings from the feed (e.g. '-31.80275800,115.83773700').
    """

    COUNTRY = "AU"
    PROVIDER_KEY = "au_fuelwatch"
    LABEL = "FuelWatch (Australia WA)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    # WA tomorrow-prices published at 14:30 AWST; poll once daily at 14:45 AWST.
    POLL_INTERVAL_SECONDS = 86400
    CURRENCY: ClassVar[str] = "A$"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",
            "premium_unleaded",
            "diesel",
            "lpg",
            "e10",
            "lastupdated",
            "name",
            "brand",
            "address",
            "latitude",
            "longitude",
            "phone",
            "is_open",
        }
    )

    STATION_ID_HINT = (
        "Enter the FuelWatch Region code for your area (e.g. '25' for Perth Metro). "
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
            station_id: Composite key '{lat},{lng}' identifying the station,
                        OR a WA Region code string used as a placeholder during
                        setup before a specific station is selected.
            county:     WA Region code (optional, used for list filtering).
            latitude:   User's reference latitude (unused in feed requests; kept
                        for interface compatibility with location-mode providers).
            longitude:  User's reference longitude (same as above).
            radius_km:  Search radius in km (unused; FuelWatch uses Region codes).
        """
        self._station_id = station_id
        self._county = county  # treated as Region code when present
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Return merged StationData for the given (lat,lng) station_id.

        The Region code is derived from self._county when available; otherwise
        the feed is fetched without a Region filter (returns statewide data).

        Args:
            station_id: Composite '{lat},{lng}' string identifying the station.

        Returns:
            StationData with all CAPABILITIES keys populated (values may be None).

        Raises:
            ProviderError: If the station is not found in any product feed.
        """
        region_code = self._county or None
        merged = await self._fetch_all_products(session, region_code)

        data = merged.get(station_id)
        if data is None:
            raise ProviderError(
                f"Station '{station_id}' not found in FuelWatch feed "
                f"(Region={region_code!r}). Verify the station ID is correct."
            )
        return data

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the trading name for the given station, or None.

        For location-mode providers the config flow does not require a name,
        but this is useful for the entity friendly-name pre-population.
        """
        region_code = self._county or None
        try:
            merged = await self._fetch_all_products(session, region_code)
            data = merged.get(station_id)
            if data:
                return data.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_fetch_station_name failed for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) sorted alphabetically by label.

        Keyword args accepted:
            county (str): WA Region code (e.g. '25'). If absent, fetches
                          without a region filter (statewide — may be slow).
            lat, lng, radius_km: accepted for interface compatibility but unused
                                 (FuelWatch does not support coordinate-based
                                 filtering; use Region codes instead).

        Returns:
            Alphabetically sorted list of (station_id, label) tuples where the
            label contains brand/name, address and an 8-char ID prefix but NO
            price, e.g.:
              '-31.80275800,115.83773700',
              'Liberty Landsdale, 123 Main St (#-31.8027)'
        """
        region_code = (
            str(kwargs.get("county", ""))
            or (str(self._county) if self._county else None)
        ) or None

        try:
            merged = await self._fetch_all_products(session, region_code)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        result: list[tuple[str, str]] = []
        for sid, data in merged.items():
            label = _build_station_list_label(data, sid)
            result.append((sid, label))

        # Sort alphabetically by label
        result.sort(key=lambda x: x[1].lower())
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_products(
        self,
        session: ClientSession,
        region_code: str | None,
    ) -> dict[str, StationData]:
        """Fetch all primary product feeds and merge by (lat, lng) key.

        Returns:
            Dict mapping composite station_id → merged StationData.
        """
        merged: dict[str, StationData] = {}
        failed = 0
        for fuel_key in _PRIMARY_PRODUCTS:
            product_code = _PRODUCT_CODES[fuel_key]
            try:
                items = await self._fetch_product_feed(
                    session, product_code, region_code
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to fetch FuelWatch product %s (code %d): %s",
                    fuel_key,
                    product_code,
                    err,
                )
                failed += 1
                continue

            for item in items:
                station_id = _make_station_id(item)
                if station_id is None:
                    continue

                if station_id not in merged:
                    merged[station_id] = _parse_station_base(item, station_id)

                # Add or overwrite the fuel price for this product
                price = _parse_price(item.get("price"))
                merged[station_id][fuel_key] = price  # type: ignore[literal-required]

        if failed == len(_PRIMARY_PRODUCTS):
            raise ProviderError("FuelWatch feed unavailable")

        return merged

    async def _fetch_product_feed(
        self,
        session: ClientSession,
        product_code: int,
        region_code: str | None,
    ) -> list[dict[str, str | None]]:
        """Fetch a single FuelWatch RSS feed and return parsed item dicts.

        Args:
            product_code: FuelWatch product integer (1, 2, 4, 5 or 6).
            region_code:  WA Region code string or None for no region filter.

        Returns:
            List of raw item dicts (keys are RSS element tag names).
        """
        params: dict[str, str] = {
            "Product": str(product_code),
            "Surrounding": "yes",
        }
        if region_code:
            params["Region"] = str(region_code)

        async with session.get(
            _RSS_URL,
            params=params,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            # Use resp.content (bytes) so ElementTree handles the UTF-8 BOM
            # transparently. Decoding with utf-8-sig first also works, but
            # parsing raw bytes is simpler and avoids re-encoding.
            raw_bytes = await resp.read()

        return _parse_rss_items(raw_bytes)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_rss_items(raw_bytes: bytes) -> list[dict[str, str | None]]:
    """Parse a FuelWatch RSS response and return a list of item field dicts.

    Handles UTF-8 BOM and CRLF line endings transparently via ElementTree.

    Args:
        raw_bytes: Raw HTTP response body from the FuelWatch RSS endpoint.

    Returns:
        List of dicts, one per <item> element.  Dict keys are the child
        element tag names (e.g. 'price', 'trading-name', 'latitude').
        Values are stripped strings or None when the element is absent/empty.
    """
    # Strip BOM if present so ElementTree does not balk at it when the XML
    # declaration specifies utf-8 (not utf-8-sig).
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as err:
        _LOGGER.debug("Failed to parse FuelWatch RSS XML: %s", err)
        return []

    items: list[dict[str, str | None]] = []
    # RSS 2.0: root → channel → item*
    channel = root.find("channel")
    if channel is None:
        return []

    for item_el in channel.findall("item"):
        item: dict[str, str | None] = {}
        for child in item_el:
            tag = child.tag  # may contain namespace prefix; strip it
            if tag.startswith("{"):
                tag = tag.split("}", 1)[1]
            text = child.text
            item[tag] = text.strip() if text else None
        items.append(item)

    return items


def _make_station_id(item: dict[str, str | None]) -> str | None:
    """Derive the composite station_id from an RSS item dict.

    The composite key is '{latitude},{longitude}' using the exact strings
    from the feed (8 decimal places each).

    Returns None if either coordinate is absent.
    """
    lat = item.get("latitude")
    lng = item.get("longitude")
    if lat is not None and lng is not None:
        return f"{lat},{lng}"
    return None


def _parse_price(raw: str | None) -> float | None:
    """Parse a FuelWatch price string (NNN.N cents/litre) to AUD/litre or None.

    FuelWatch publishes prices in cents/litre (e.g. 153.3 c/L).  Values > 10
    are divided by 100 to convert to AUD/litre (e.g. 1.533 A$/L).
    """
    if raw is None:
        return None
    try:
        value = float(raw)
        if value <= 0:
            return None
        if value > 10:
            value = round(value / 100.0, 4)
        return value
    except (ValueError, TypeError):
        return None


def _parse_lat_lng(raw: str | None) -> float | None:
    """Parse a latitude or longitude string to float or None."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _parse_is_open(site_features: str | None) -> bool:
    """Infer is_open from site-features opening hours text.

    The <site-features> field may contain structured text like:
      'Fuel Cards ATM EFTPOS Air Water Ice, Open Mon: 04:00-23:00, ...'

    Returns False when no schedule information is present (unknown/closed).
    Returns True when an "Open" schedule string is detected.
    Best-effort heuristic; FuelWatch does not publish a real-time status.
    """
    if not site_features:
        return False  # no schedule info → assume closed (unknown)
    # If there is any "Open" schedule text, treat the station as potentially
    # open (we cannot determine real-time status from a daily-updated feed).
    if "Open" in site_features or "open" in site_features:
        return True
    return False


def _parse_station_base(
    item: dict[str, str | None],
    station_id: str,
) -> StationData:
    """Build a StationData skeleton (no fuel prices) from an RSS item dict.

    Prices are added separately per product in _fetch_all_products().

    Args:
        item:       Parsed RSS item dict from _parse_rss_items().
        station_id: Composite '{lat},{lng}' key for this station.

    Returns:
        StationData with identity/metadata fields populated.
    """
    trading_name = item.get(_TAG_TRADING_NAME)
    brand = item.get("brand")
    site_features = item.get(_TAG_SITE_FEATURES)
    date_str = item.get("date")

    # Derive a human-friendly name: prefer trading name, fall back to brand
    name: str | None = trading_name or brand or None

    return {
        "name": name,
        "brand": brand,
        "address": item.get("address"),
        "latitude": _parse_lat_lng(item.get("latitude")),
        "longitude": _parse_lat_lng(item.get("longitude")),
        "phone": item.get("phone"),
        "lastupdated": date_str,
        "is_open": _parse_is_open(site_features),
        "source_station_id": station_id,
        # Fuel price keys initialised to None so stations that only appear in
        # a subset of product feeds still have all expected CAPABILITIES keys.
        "unleaded": None,
        "premium_unleaded": None,
        "diesel": None,
        "lpg": None,
        "e10": None,
    }


def _build_display_label(data: StationData) -> str:
    """Build a human-readable display label for the station picker.

    Format: '{Brand} {TradingName} — Unleaded NNN.N c/L | Diesel NNN.N c/L'

    Args:
        data: Merged StationData for a station.

    Returns:
        Non-empty display string.
    """
    brand = data.get("brand") or ""
    name = data.get("name") or ""
    address = data.get("address") or ""

    if brand and name and brand.lower() not in name.lower():
        identity = f"{brand} {name}"
    elif name:
        identity = name
    elif brand:
        identity = brand
    else:
        identity = address or "Unknown Station"

    price_parts: list[str] = []
    for key, label in (
        ("unleaded", "ULP"),
        ("premium_unleaded", "Prem"),
        ("diesel", "Diesel"),
        ("lpg", "LPG"),
        ("e10", "E10"),
    ):
        price = data.get(key)  # type: ignore[literal-required]
        if price is not None:
            price_parts.append(f"{label} A${price:.3f}")

    if price_parts:
        return f"{identity} — {' | '.join(price_parts)}"
    return identity


def _build_station_list_label(data: StationData, sid: str) -> str:
    """Build a price-free display label for the station picker list.

    Format: '{brand/name}, {address} (#{sid[:8]})'

    The first 8 characters of the station ID (e.g. '-31.8027') give the user
    enough context to distinguish duplicate names without showing price.

    Args:
        data: Merged StationData for a station.
        sid:  Composite '{lat},{lng}' station ID string.

    Returns:
        Non-empty display string with no price information.
    """
    brand = data.get("brand") or ""
    name = data.get("name") or ""
    address = data.get("address") or ""

    if brand and name and brand.lower() not in name.lower():
        identity = f"{brand} {name}"
    elif name:
        identity = name
    elif brand:
        identity = brand
    else:
        identity = "Unknown Station"

    short_id = sid[:8]
    if address:
        return f"{identity}, {address} (#{short_id})"
    return f"{identity} (#{short_id})"
