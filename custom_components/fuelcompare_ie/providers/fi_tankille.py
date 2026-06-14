"""FiTankilleProvider — Finnish national average fuel prices from Statistics Finland.

Source: Statistics Finland (Tilastokeskus) — table 12ge (Energy prices).
URL:    https://pxdata.stat.fi/PXWeb/api/v1/en/StatFin/ehi/12ge.px

Station-level data is not available via any free/open API for Finland.
Tankille.fi provides station-level community data but actively blocks external
API requests (HTTP 403 as of June 2026) and the existing HA integration
(github.com/aarooh/ha-tankille) is explicitly non-functional for this reason.

This provider uses the Statistics Finland PxWeb REST API, which publishes
monthly national average consumer prices (including VAT) for motor fuels.
Data is updated quarterly (monthly figures published with ~1 quarter lag).
The poll interval is therefore set to 86400 s (daily) — polling more
frequently wastes requests as the data changes at most monthly.

API contract
------------
POST https://pxdata.stat.fi/PXWeb/api/v1/en/StatFin/ehi/12ge.px
Content-Type: application/json

Body (JSON selection query selecting the most recent time period):
{
  "query": [
    {"code": "Hyödyke", "selection": {"filter": "item", "values": ["A", "B", "D", "E"]}},
    {"code": "Tiedot",  "selection": {"filter": "item", "values": ["kuluttajahinta"]}}
  ],
  "response": {"format": "json-stat2"}
}

Variable codes:
  Hyödyke (commodity):
    A  — Motor petrol 95 E10 (EUR/litre, consumer price incl. VAT)
    B  — Diesel (EUR/litre, consumer price incl. VAT)
    D  — Light fuel oil (EUR/litre)
    E  — Renewable diesel HVO100 (EUR/litre)
  Tiedot (measure):
    kuluttajahinta — consumer price in EUR/litre

Response format: JSON-stat 2.0
  dataset.dimension — axis metadata (includes the time dimension labels)
  dataset.value     — flat array of float values in row-major order
                      (commodity × time); NaN represented as null.

Price normalisation
-------------------
Statistics Finland returns prices in EUR/litre (e.g. 1.712).  No cents
conversion is applied.  No > 10 guard is needed.

StationData mapping
-------------------
  A (95 E10)          → unleaded, e10   (same price stored under both keys)
  B (diesel)          → diesel
  D (light fuel oil)  → kerosene
  E (renewable diesel)→ premium_diesel  (closest StationData key)

CONFIG_MODE = 'location': no per-station selection; the single "station" is
identified by the country code 'FI' stored as station_id.  The sensor name
defaults to 'Finland National Average'.

STATION_LOOKUP_MODE = 'location_search': async_list_stations returns a single
entry — the national average — so the config flow can present it as a
selectable option.  The location coordinates stored in the provider are used
only as context metadata; they default to central Helsinki.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData, haversine_km  # noqa: F401

_LOGGER = logging.getLogger(__name__)

_API_URL = "https://pxdata.stat.fi/PXWeb/api/v1/en/StatFin/ehi/12ge.px"

# The PxWeb variable code for the commodity dimension.
_DIM_COMMODITY = "energia_22_20200205"
# The PxWeb variable code for the measure dimension.
_DIM_MEASURE = "Tiedot"

# Commodity codes to request from the API.
# A = 95E10 petrol, B = Diesel, D = Light fuel oil, E = Renewable diesel HVO100
_COMMODITY_CODES: list[str] = ["A", "B", "D", "E"]

# Mapping from commodity code to StationData key(s).
# Each code may map to one or more keys (price duplicated for aliases).
_CODE_TO_KEYS: dict[str, list[str]] = {
    "A": ["unleaded", "e10"],
    "B": ["diesel"],
    "D": ["kerosene"],
    "E": ["premium_diesel"],
}

# The PxWeb measure code for consumer price (EUR/litre, incl. VAT).
_MEASURE_CODE = "hinta"

# Helsinki WGS84 — used as the nominal "location" for the national average.
_FI_LAT = 60.1699
_FI_LNG = 24.9384

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Standard timeout; PxWeb API is fast for small queries.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# The JSON-stat2 query sent to the PxWeb API.
# Time dimension is left unrestricted so the API returns all available months;
# we then pick the most recent non-null value for each commodity.
_QUERY_BODY: dict[str, Any] = {
    "query": [
        {
            "code": _DIM_COMMODITY,
            "selection": {
                "filter": "item",
                "values": _COMMODITY_CODES,
            },
        },
        {
            "code": _DIM_MEASURE,
            "selection": {
                "filter": "item",
                "values": [_MEASURE_CODE],
            },
        },
    ],
    "response": {"format": "json-stat2"},
}

# station_id used for the national-average virtual station.
_NATIONAL_STATION_ID = "FI"


def _parse_price(raw: Any) -> float | None:
    """Parse a PxWeb price value to a rounded float EUR/litre, or None.

    Statistics Finland returns null for missing values (no data for a period).
    Prices are already in EUR/litre — no cents conversion applied.

    Args:
        raw: Raw value from the json-stat2 values array (float, int, or None).

    Returns:
        Rounded float price in EUR/litre, or None if invalid/unavailable.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    # API returns prices in cents/litre (e.g. 193 = 1.93 EUR/L)
    if val > 10:
        val = val / 100.0
    return round(val, 4)


def _extract_prices_from_jsonstat2(payload: dict[str, Any]) -> dict[str, float | None]:
    """Parse a JSON-stat 2.0 payload and return the latest price per commodity.

    The payload has structure:
      dataset.dimension.<dim>.category.index  — ordered mapping of code→position
      dataset.value                           — flat row-major array

    Dimensions are ordered as they appear in the query: commodity × measure × time.
    Since we request only one measure (kuluttajahinta), the effective shape is:
      (len(commodities),  len(time_periods))

    We iterate from the most recent time period backwards and return the first
    non-null value for each commodity.

    Args:
        payload: Parsed JSON-stat 2.0 dict from the PxWeb API.

    Returns:
        Dict mapping commodity code (e.g. 'A') to the latest float price, or
        None if no non-null value is available for that commodity.

    Raises:
        ProviderError: If the payload structure is not as expected.
    """
    try:
        dataset = payload.get("dataset") or payload  # top-level may be dataset itself
        dimension = dataset["dimension"]
        values: list[Any] = dataset["value"]
        size: list[int] = dataset["size"]
        ids: list[str] = dataset["id"]
    except (KeyError, TypeError) as exc:
        raise ProviderError(
            f"Statistics Finland API returned unexpected JSON-stat2 structure: {exc}"
        ) from exc

    # Locate the commodity and time dimensions by their id.
    try:
        commodity_idx = ids.index(_DIM_COMMODITY)
        time_idx = ids.index("timeperiod_m")  # "Year-month" — the time dimension
    except ValueError:
        # Fall back: assume commodity is first, time is last.
        commodity_idx = 0
        time_idx = len(ids) - 1

    n_commodity = size[commodity_idx]
    n_time = size[time_idx]

    # Extract ordered commodity codes.
    commodity_dim = dimension[_DIM_COMMODITY]
    # category.index maps code → position in the dimension axis.
    code_to_pos: dict[str, int] = commodity_dim["category"]["index"]
    pos_to_code: dict[int, str] = {v: k for k, v in code_to_pos.items()}

    # For each commodity, find the most recent non-null price.
    # The flat index depends on dimension order in the response:
    #   commodity-major (ids[0]=commodity): value[c * n_time + t]
    #   time-major      (ids[0]=time):      value[t * n_commodity + c]
    prices: dict[str, float | None] = {}
    commodity_is_first = commodity_idx < time_idx
    for c_pos in range(n_commodity):
        code = pos_to_code.get(c_pos)
        if code is None:
            continue
        # Scan time periods from most recent (last index) backwards.
        price: float | None = None
        for t in range(n_time - 1, -1, -1):
            if commodity_is_first:
                flat_idx = c_pos * n_time + t
            else:
                flat_idx = t * n_commodity + c_pos
            if flat_idx < len(values):
                price = _parse_price(values[flat_idx])
                if price is not None:
                    break
        prices[code] = price

    return prices


class FiTankilleProvider(BaseProvider):
    """Fetch Finnish national average fuel prices from Statistics Finland.

    Statistics Finland (stat.fi) publishes monthly national average consumer
    prices for motor fuels via their PxWeb REST API (table 12ge).  Prices
    are in EUR/litre including VAT and are updated quarterly.

    Since no station-level data is available via any free/open API, this
    provider tracks national averages only.  The virtual "station" is
    identified by the country code 'FI' as station_id.

    Usage
    -----
    CONFIG_MODE = 'location': the config flow asks for coordinates; the
    provider ignores them for data fetching (national average only) but
    stores them as context.  station_id is fixed to 'FI'.

    STATION_LOOKUP_MODE = 'location_search': async_list_stations() always
    returns a single entry — the Finnish national average — regardless of
    the supplied coordinates.
    """

    COUNTRY = "FI"
    PROVIDER_KEY = "fi_tankille"
    LABEL = "Statistics Finland — National Average (Finland)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 86400  # daily — data updates at most monthly

    CAPABILITIES: frozenset[str] = frozenset(
        {
            # Fuel prices
            "unleaded",
            "e10",
            "diesel",
            "kerosene",
            "premium_diesel",
            # Station identity (national average context)
            "name",
            "latitude",
            "longitude",
            # Timing
            "lastupdated",
            # Diagnostic / coordinator-managed
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Finland national average prices are fetched automatically.  "
        "No station ID is required — leave this as 'FI'."
    )

    def __init__(
        self,
        station_id: str = _NATIONAL_STATION_ID,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  Ignored for data fetching; stored for compat.
                         Defaults to 'FI' (the national-average virtual id).
            latitude:    WGS84 latitude of the user's location (context only).
                         Defaults to Helsinki (60.1699).
            longitude:   WGS84 longitude of the user's location (context only).
                         Defaults to Helsinki (24.9384).
            radius_km:   Not used by this provider (national average only).
        """
        self._station_id = station_id
        self._latitude: float = latitude if latitude is not None else _FI_LAT
        self._longitude: float = longitude if longitude is not None else _FI_LNG
        self._radius_km: float = radius_km if radius_km is not None else 0.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return Finnish national average fuel prices.

        POSTs to the Statistics Finland PxWeb API and parses the JSON-stat 2.0
        response to extract the most recent monthly price for each fuel type.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored — always returns national averages for Finland.

        Returns:
            StationData dict with CAPABILITIES keys populated.  Price values
            are float EUR/litre or None if Statistics Finland has no data.

        Raises:
            ProviderError: On malformed API response or unexpected data format.
            aiohttp.ClientError: On network/HTTP errors (propagates to coordinator).
        """
        payload = await self._post_query(session)
        prices = _extract_prices_from_jsonstat2(payload)

        _LOGGER.debug(
            "Statistics Finland parsed prices: 95E10=%s diesel=%s "
            "light_fuel_oil=%s renewable_diesel=%s",
            prices.get("A"),
            prices.get("B"),
            prices.get("D"),
            prices.get("E"),
        )

        data: StationData = {
            "unleaded": prices.get("A"),
            "e10": prices.get("A"),  # 95 E10 — same value, different key alias
            "diesel": prices.get("B"),
            "kerosene": prices.get("D"),
            "premium_diesel": prices.get("E"),
            "name": "Finland — National Average",
            "latitude": self._latitude,
            "longitude": self._longitude,
            "lastupdated": None,  # PxWeb does not return a per-row timestamp
            "source_station_id": _NATIONAL_STATION_ID,
        }
        return data

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the display name for the config flow.

        For national-average providers there is no station to look up.
        Returns a fixed human-readable description.

        Args:
            session:    aiohttp ClientSession (not used).
            station_id: Ignored.
        """
        return "Finland — National Average (Statistics Finland)"

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return a single entry for the national average.

        Called by the config flow location_search step.  Since only one
        "station" (the national average) is available, this method returns a
        fixed single-item list without making any network request.

        The lat/lng kwargs are accepted for interface compatibility but are
        not used — the national average is not location-specific.

        Args:
            session:   aiohttp ClientSession.
            lat:       User latitude (accepted but unused).
            lng:       User longitude (accepted but unused).
            radius_km: Search radius (accepted but unused).

        Returns:
            [('FI', 'Finland — National Average (Statistics Finland, monthly)')]
        """
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")

        if lat is not None and lng is not None:
            _LOGGER.debug(
                "FiTankilleProvider.async_list_stations called with lat=%s lng=%s "
                "(ignored — national average only)",
                lat,
                lng,
            )

        return [
            (
                _NATIONAL_STATION_ID,
                "Finland — National Average (Statistics Finland, monthly)",
            )
        ]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _post_query(self, session: ClientSession) -> dict[str, Any]:
        """POST the PxWeb selection query and return the parsed JSON-stat2 dict.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Parsed JSON dict (top-level JSON-stat2 object).

        Raises:
            ProviderError: HTTP 4xx/5xx response from the API.
            aiohttp.ClientError: On network errors (propagates to coordinator).
        """
        _LOGGER.debug("Fetching Statistics Finland fuel prices from %s", _API_URL)
        try:
            async with session.post(
                _API_URL,
                json=_QUERY_BODY,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status == 400:
                    text = await response.text()
                    raise ProviderError(
                        f"Statistics Finland API returned HTTP 400 Bad Request. "
                        f"The query may be malformed or commodity codes may have "
                        f"changed.  Response: {text[:200]}"
                    )
                if response.status == 404:
                    raise ProviderError(
                        "Statistics Finland API returned HTTP 404 — table 12ge "
                        "may have been moved or renamed.  Check "
                        "https://pxdata.stat.fi/PXWeb/api/v1/en/StatFin/ehi/"
                    )
                try:
                    response.raise_for_status()
                except ClientResponseError as exc:
                    raise ProviderError(
                        f"Statistics Finland API returned HTTP {response.status}: {exc}"
                    ) from exc
                payload: dict[str, Any] = await response.json(content_type=None)
        except ProviderError:
            raise
        except ClientResponseError as exc:
            raise ProviderError(f"Statistics Finland API HTTP error: {exc}") from exc
        except Exception:  # noqa: BLE001
            # Re-raise network errors so the coordinator can apply stale-retention.
            raise

        return payload
