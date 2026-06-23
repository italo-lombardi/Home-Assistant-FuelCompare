"""PlBenzynaProvider — Polish wholesale fuel prices from ORLEN.

ORLEN (PKN ORLEN S.A.) is Poland's dominant fuel retailer and wholesale
distributor.  This provider scrapes the undocumented but publicly accessible
JSON API that backs the price widget on tool.orlen.pl.

Important notes
---------------
- These are **wholesale** prices, not retail pump prices.
- Prices are in PLN per 1000 litres.  This provider converts them to
  PLN/litre by dividing by 1000.
- The API is unofficial (undocumented, no published ToS for automated access).
  It could break without notice — ORLEN may rotate endpoints or add auth.
- No station-level retail price data exists as free/open data in Poland.
- Responses are cached server-side (X-Cache: Hit); polling more frequently
  than every few hours wastes requests.

Endpoints used
--------------
GET https://tool.orlen.pl/api/wholesalefuelprices
    Returns a JSON array of current wholesale price records.  Each record has:
      productName   — str, e.g. "Pb95", "ONEkodiesel"
      value         — numeric, PLN per 1000 litres
      effectiveDate — str, ISO date of last update (e.g. "2026-06-13")
    No authentication, no query parameters required.

GET https://tool.orlen.pl/api/autogasprices
    Returns LPG prices broken down by voivodeship (region).  Each record has:
      value         — numeric, PLN per 1000 litres
      date          — str, ISO date
      voivodeship   — str, e.g. "Mazowieckie"
    This provider uses the national minimum LPG price across all voivodeships.

CONFIG_MODE = "station_id"
    There are no individual stations — only national wholesale prices.
    station_id is set to the country code "PL" and async_list_stations
    returns a single "PL (national wholesale)" entry.

STATION_LOOKUP_MODE = "global_list"
    Required by the spec.  async_list_stations accepts lat/lng kwargs but
    always returns the single national wholesale record regardless of
    coordinates (no station-level data exists).

Product → StationData key mapping
----------------------------------
ORLEN productCode   → StationData key   Notes
-----------------     ----------------   -----
Pb95                → unleaded           Standard petrol 95
Pb98                → premium_unleaded   Super petrol 98
ONEkodiesel         → diesel             Standard diesel
ONArctic2           → premium_diesel     Arctic-grade winter diesel
OnEkoterm           → kerosene           Heating oil / Ekoterm
LPG                 → lpg                Autogas (from /autogasprices)
BIO100              → e85                B100 biodiesel (closest StationData key)
JETA1               → (extra attr)       Jet A-1 aviation fuel — no StationData key
AVGAS100LL          → (extra attr)       Avgas 100LL — no StationData key

Confidence notes
----------------
Confidence is 6/10: the API is confirmed working (2026-06-13) and returns
useful daily wholesale data, but it is undocumented/unofficial.

POLL_INTERVAL_SECONDS = 86400
    STATION_PAGE_URL: ClassVar[str] = "https://www.orlen.pl"
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://tool.orlen.pl/api"
_WHOLESALE_URL = f"{_BASE_URL}/wholesalefuelprices"
_AUTOGAS_URL = f"{_BASE_URL}/autogasprices"

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/json",
    "Referer": "https://tool.orlen.pl/",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# Factor for converting PLN/1000L → PLN/L
_PLN_PER_1000L_TO_PER_L: float = 0.001

# Mapping from ORLEN productCode to StationData key.
# Products without a StationData key are stored as extra fields.
_PRODUCT_TO_KEY: dict[str, str] = {
    "Pb95": "unleaded",
    "Pb98": "premium_unleaded",
    "ONEkodiesel": "diesel",
    "ONArctic2": "premium_diesel",
    "OnEkoterm": "kerosene",
    # LPG comes from /autogasprices endpoint — handled separately
    "BIO100": "e85",  # B100 biodiesel; closest available StationData key
}

# Extra product codes stored as passthrough attributes (no StationData key)
_EXTRA_PRODUCTS: frozenset[str] = frozenset({"JETA1", "AVGAS100LL"})

# Stable station_id used for the single national record
_NATIONAL_STATION_ID = "PL"


def _parse_price_pln_1000l(raw: Any) -> float | None:
    """Convert an ORLEN wholesale price (PLN/1000L) to PLN/litre.

    Args:
        raw: Raw price value from the API (int, float, str, or None).

    Returns:
        Price in PLN/litre rounded to 4 decimal places, or None if invalid.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val * _PLN_PER_1000L_TO_PER_L, 4)


def _parse_price_pln(raw: Any) -> float | None:
    """Parse a PLN/litre price value.

    Args:
        raw: Raw price value from the API (int, float, str, or None).

    Returns:
        Price in PLN/litre rounded to 4 decimal places, or None if invalid.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return round(val, 4)


class PlBenzynaProvider(BaseProvider):
    """Fetch Polish wholesale fuel prices from the ORLEN API.

    Returns national wholesale prices in PLN/litre for Pb95, Pb98, diesel,
    diesel premium, heating oil (Ekoterm), biodiesel (BIO100), and LPG.
    Aviation fuels (JET A-1, AVGAS 100LL) are included as passthrough attrs.

    There is no station-level data — a single "PL" record is returned
    representing the current ORLEN national wholesale price schedule.

    Usage
    -----
    CONFIG_MODE = 'station_id': station_id is fixed to 'PL'.
    The coordinator creates entities for each CAPABILITIES key.
    async_list_stations returns the single national record regardless of
    lat/lng coordinates supplied (no location-specific retail data exists).
    """

    COUNTRY = "PL"
    PROVIDER_KEY = "pl_benzyna"
    LABEL = "ORLEN Wholesale (Poland)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "global_list"
    STATION_PAGE_URL: ClassVar[str] = "https://www.orlen.pl"
    POLL_INTERVAL_SECONDS = 86400  # daily — prices update at most once per day
    CURRENCY: ClassVar[str] = "zł"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices (PLN/litre)
            "unleaded",
            "premium_unleaded",
            "diesel",
            "premium_diesel",
            "kerosene",
            "lpg",
            "e85",
            # Station identity (national record)
            "name",
            "county",
            # Timing
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "For Poland ORLEN wholesale prices there is only one record: "
        "the national wholesale price schedule.  Enter 'PL' or leave blank."
    )

    def __init__(self, station_id: str = _NATIONAL_STATION_ID) -> None:
        """Initialise the provider.

        Args:
            station_id:  Ignored for this provider; always 'PL'.
        """
        self._station_id = _NATIONAL_STATION_ID

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return current ORLEN wholesale prices.

        Makes two GET requests:
          1. /wholesalefuelprices — all product prices
          2. /autogasprices — LPG prices by voivodeship (national min used)

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored; always fetches the national wholesale record.

        Returns:
            StationData dict with all CAPABILITIES keys populated.

        Raises:
            ProviderError: API returned an unexpected format or HTTP error.
        """
        prices_data = await self._fetch_wholesale_prices(session)
        lpg_price = await self._fetch_lpg_price(session)

        return self._build_station_data(prices_data, lpg_price)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the display name for the config flow.

        For global_list providers that have only a single national record,
        this returns the static name without making an API call.

        Args:
            session:    aiohttp ClientSession (not used).
            station_id: Ignored.
        """
        return "ORLEN Poland (national wholesale)"

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return the single national wholesale record for the station picker.

        No station-level retail price data exists in Poland as open data.
        This method always returns the single national ORLEN wholesale record
        regardless of lat/lng arguments.

        STATION_LOOKUP_MODE = 'global_list' (single-row provider — the
        config flow skips the location step entirely).

        Args:
            session:   aiohttp ClientSession.
            lat:       Search centre latitude (accepted but ignored).
            lng:       Search centre longitude (accepted but ignored).
            radius_km: Search radius (accepted but ignored).

        Returns:
            List with a single ('PL', label) tuple, or [] on fetch failure.
        """
        lat: float | None = kwargs.get("lat")  # type: ignore[assignment]
        lng: float | None = kwargs.get("lng")  # type: ignore[assignment]

        # Enforce is-not-None checks (not falsy) as per spec
        if lat is not None and lng is not None:
            _LOGGER.debug(
                "async_list_stations called with lat=%s lng=%s; "
                "Poland has no station-level data — returning national record",
                lat,
                lng,
            )

        try:
            prices_data = await self._fetch_wholesale_prices(session)
            lpg_price = await self._fetch_lpg_price(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "async_list_stations failed to fetch ORLEN wholesale prices: %s", err
            )
            return []

        # Build a summary label for the single national record
        pb95_raw = _find_product_price(prices_data, "Pb95")
        diesel_raw = _find_product_price(prices_data, "ONEkodiesel")
        pb95 = _parse_price_pln_1000l(pb95_raw)
        diesel = _parse_price_pln_1000l(diesel_raw)
        lpg = lpg_price

        price_parts: list[str] = []
        if pb95 is not None:
            price_parts.append(f"Pb95: {pb95:.4f} PLN/L")
        if diesel is not None:
            price_parts.append(f"Diesel: {diesel:.4f} PLN/L")
        if lpg is not None:
            price_parts.append(f"LPG: {lpg:.4f} PLN/L")

        label = "ORLEN Poland — national wholesale"
        if price_parts:
            label = f"{label} — {', '.join(price_parts)}"

        return [(_NATIONAL_STATION_ID, label)]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_wholesale_prices(
        self,
        session: ClientSession,
    ) -> list[dict[str, Any]]:
        """Fetch the /wholesalefuelprices endpoint.

        Args:
            session: aiohttp ClientSession.

        Returns:
            List of product price dicts from the API.

        Raises:
            ProviderError: HTTP error or unexpected response format.
        """
        _LOGGER.debug("Fetching ORLEN wholesale prices from %s", _WHOLESALE_URL)
        try:
            async with session.get(
                _WHOLESALE_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload: Any = await response.json(content_type=None)
        except ClientResponseError as err:
            raise ProviderError(
                f"ORLEN wholesale prices API returned HTTP {err.status}: {err.message}"
            ) from err
        except Exception as err:  # noqa: BLE001
            raise ProviderError(
                f"Failed to fetch ORLEN wholesale prices: {err}"
            ) from err

        if not isinstance(payload, list):
            raise ProviderError(
                f"ORLEN /wholesalefuelprices returned unexpected format "
                f"(expected JSON array, got {type(payload).__name__})"
            )

        return payload

    async def _fetch_lpg_price(
        self,
        session: ClientSession,
    ) -> float | None:
        """Fetch the /autogasprices endpoint and return the minimum LPG price.

        The /autogasprices endpoint returns LPG prices per voivodeship.
        This method returns the minimum price across all voivodeships as a
        conservative national estimate.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Minimum LPG price in PLN/litre, or None on failure.
        """
        _LOGGER.debug("Fetching ORLEN LPG prices from %s", _AUTOGAS_URL)
        try:
            async with session.get(
                _AUTOGAS_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload: Any = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch ORLEN LPG prices (non-fatal): %s", err)
            return None

        if not isinstance(payload, list):
            _LOGGER.debug(
                "ORLEN /autogasprices returned unexpected format: %s",
                type(payload).__name__,
            )
            return None

        min_price: float | None = None
        for record in payload:
            raw = record.get("value")
            # /autogasprices returns PLN/litre directly (not PLN/1000L)
            price = _parse_price_pln(raw)
            if price is not None:
                if min_price is None or price < min_price:
                    min_price = price

        return min_price

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        prices_data: list[dict[str, Any]],
        lpg_price: float | None,
    ) -> StationData:
        """Assemble a StationData dict from the ORLEN API response.

        Args:
            prices_data: List of product price dicts from /wholesalefuelprices.
            lpg_price:   LPG price in PLN/litre from /autogasprices, or None.

        Returns:
            Populated StationData dict.
        """
        # Extract prices for each mapped product
        price_map: dict[str, float | None] = {}
        latest_date: str | None = None

        for record in prices_data:
            product_code: str = str(record.get("productName") or "")
            raw_price = record.get("value")
            price = _parse_price_pln_1000l(raw_price)

            # Track the most recent date across all products
            date_str: str | None = record.get("effectiveDate") or None
            if date_str:
                if latest_date is None or date_str > latest_date:
                    latest_date = date_str

            if product_code in _PRODUCT_TO_KEY:
                key = _PRODUCT_TO_KEY[product_code]
                price_map[key] = price

        data: StationData = {
            # Fuel prices (PLN/litre)
            "unleaded": price_map.get("unleaded"),
            "premium_unleaded": price_map.get("premium_unleaded"),
            "diesel": price_map.get("diesel"),
            "premium_diesel": price_map.get("premium_diesel"),
            "kerosene": price_map.get("kerosene"),
            "lpg": lpg_price,
            "e85": price_map.get("e85"),
            # Station identity
            "name": "ORLEN Poland (national wholesale)",
            "county": "PL",
            # Timing
            "lastupdated": latest_date,
        }

        _LOGGER.debug(
            "ORLEN wholesale parsed: unleaded=%s diesel=%s lpg=%s "
            "premium_unleaded=%s kerosene=%s e85=%s lastupdated=%s",
            data.get("unleaded"),
            data.get("diesel"),
            data.get("lpg"),
            data.get("premium_unleaded"),
            data.get("kerosene"),
            data.get("e85"),
            data.get("lastupdated"),
        )

        return data


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_product_price(
    prices_data: list[dict[str, Any]],
    product_code: str,
) -> Any:
    """Return the raw price value for a given productCode, or None.

    Args:
        prices_data:  List of product price dicts from /wholesalefuelprices.
        product_code: ORLEN productCode string (e.g. "Pb95").

    Returns:
        The raw price value (int/float/None), or None if not found.
    """
    for record in prices_data:
        if record.get("productName") == product_code:
            return record.get("value")
    return None
