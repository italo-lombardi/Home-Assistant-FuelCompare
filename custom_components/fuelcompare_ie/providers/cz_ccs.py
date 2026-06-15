"""CzCcsProvider — Czech Republic national fuel price caps (MoF / Duchnaa scraper).

No station-level fuel price API exists for the Czech Republic.  The best
available public source is a community GitHub scraper maintained by Duchnaa
that re-publishes the government-regulated maximum prices set by the Czech
Ministry of Finance (Ministerstvo financí ČR).

Source
------
  Scraper repo:  https://github.com/Duchnaa/fuel-prices-cz
  Raw JSON:      https://raw.githubusercontent.com/Duchnaa/fuel-prices-cz/main/data/prices.json
  Upstream data: https://mf.gov.cz/cs/kontrola-a-regulace/cenova-regulace-a-kontrola/maximalni-pripustne-ceny-benzinu-a-nafty

The JSON is updated each weekday at 14:00 CET via a GitHub Actions workflow.
It contains the current government price cap alongside the hypothetical market
price ("without cap") for Natural95 and Diesel, plus up to 30 historical
entries.

Authentication
--------------
None.  The raw GitHub URL is a plain HTTPS GET with no auth headers needed.

Price format
------------
All prices in the JSON are in CZK per litre (e.g. 41.49 CZK/L for Natural95).
No cents-to-currency conversion is needed — the values are already CZK/litre.

JSON structure (top-level keys)
--------------------------------
  last_updated           ISO 8601 datetime string (e.g. "2026-06-12T14:16:11")
  valid_from             Date string ("YYYY-MM-DD")
  valid_to               Date string or null
  current
    natural95_cap        float CZK/L — government-capped price for Natural95
    diesel_cap           float CZK/L — government-capped price for Diesel
    natural95_without_cap float CZK/L — hypothetical market price Natural95
    diesel_without_cap   float CZK/L — hypothetical market price Diesel
    valid_to             null or date string
  government_cap
    active               bool
    cap_price_natural95  float CZK/L
    cap_price_diesel     float CZK/L
    valid_from           date string
    valid_to             null or date string
  history                list[dict] (up to 30 entries, each with a date + prices)

StationData mapping
-------------------
JSON field                  → StationData key     Notes
--------------------------    ----------------     -----
current.natural95_cap       → unleaded            CZK/L, government cap price
current.diesel_cap          → diesel              CZK/L, government cap price
last_updated                → lastupdated         ISO 8601 string
"Czech Republic"            → name                Fixed label (national average)
"CZ"                        → source_station_id   Country code used as station id

Because CONFIG_MODE is 'location', the station_id passed to async_fetch is the
country code 'CZ' (set during config entry creation).  async_list_stations
returns a single entry for the national average.

Third-party dependency risk
---------------------------
This provider depends on a third-party GitHub repository.  The raw URL may
stop being updated if the maintainer abandons the project.  The coordinator's
stale-retention logic will keep the last known values available in HA until
data_fetch_problem is raised after the configured stale-retention window.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_PRICES_URL = (
    "https://raw.githubusercontent.com/Duchnaa/fuel-prices-cz/main/data/prices.json"
)

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "application/json",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 2)

# The station_id / source_station_id used for the national-average entry.
_NATIONAL_STATION_ID = "CZ"


class CzCcsProvider(BaseProvider):
    """Fetch Czech Republic government-capped national fuel prices.

    The Czech Ministry of Finance publishes maximum pump prices for Natural95
    and Diesel, effective since April 2026.  This provider fetches the prices
    from a community-maintained JSON file that scrapes and re-publishes these
    caps each weekday.

    Because no station-level data source exists for CZ, CONFIG_MODE is
    'location' and the provider tracks national-average caps only.  The
    station_id for the single virtual "station" is the country code 'CZ'.

    Usage
    -----
    Constructor accepts no required parameters (the national average does not
    vary by location).  async_fetch ignores station_id and always returns the
    current national cap prices.  async_list_stations returns a single entry
    labelled 'Czech Republic — National Average'.
    """

    COUNTRY = "CZ"
    PROVIDER_KEY = "cz_ccs"
    LABEL = "MF ČR Price Caps (Czech Republic)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600 * 6  # updated once per weekday; 6-hour poll is ample
    CURRENCY: ClassVar[str] = "Kč"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices (CZK/litre, government-capped maximum)
            "unleaded",  # Natural95 cap price
            "diesel",  # Diesel cap price
            # Station / source identity
            "name",
            "source_station_id",
            # Timing
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Czech Republic national average (government price cap). "
        "No station-level data is available for CZ. "
        "The station ID is fixed to 'CZ'."
    )

    def __init__(self, station_id: str = _NATIONAL_STATION_ID) -> None:
        """Initialise the provider.

        Args:
            station_id: Ignored for national-average providers; kept for
                        BaseProvider interface compatibility.  Defaults to 'CZ'.
        """
        self._station_id = station_id

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return the current Czech national fuel price caps.

        Fetches the community JSON file and extracts current.natural95_cap
        and current.diesel_cap.  Both are in CZK per litre.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored; national average has no station selector.

        Returns:
            StationData dict with unleaded, diesel, lastupdated, name,
            source_station_id populated.  Values are CZK/litre.

        Raises:
            ProviderError: JSON structure is missing expected keys or contains
                           invalid price data.
        """
        payload = await self._fetch_json(session)
        return _parse_prices(payload)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return None — location-mode providers have no per-station name.

        The config flow will use the auto-generated 'Country (lat, lon)' title.

        Args:
            session:    aiohttp ClientSession.
            station_id: Ignored.
        """
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return a single entry for the national average.

        Called by the config flow location_search step.  For national-average
        providers there is exactly one "station" — the country-wide cap price.

        Args:
            session: aiohttp ClientSession.
            lat:     Optional float — ignored (national average has no location).
            lng:     Optional float — ignored.
            radius_km: Optional float — ignored.

        Returns:
            List with one tuple: ('CZ', 'Czech Republic — National Average
            (CZK/L)').  Returns empty list on fetch failure so the config flow
            can show a graceful error.
        """
        try:
            payload = await self._fetch_json(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        current = payload.get("current") or {}
        natural95 = _safe_price(current.get("natural95_cap"))
        diesel = _safe_price(current.get("diesel_cap"))

        price_parts: list[str] = []
        if natural95 is not None:
            price_parts.append(f"Natural95 {natural95:.2f} CZK/L")
        if diesel is not None:
            price_parts.append(f"Diesel {diesel:.2f} CZK/L")

        if price_parts:
            label = f"Czech Republic — National Cap: {', '.join(price_parts)}"
        else:
            label = "Czech Republic — National Average (CZK/L)"

        return [(_NATIONAL_STATION_ID, label)]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_json(self, session: ClientSession) -> dict:
        """Fetch the raw JSON payload from the community scraper endpoint.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Parsed JSON dict.

        Raises:
            ProviderError: HTTP error or non-JSON response.
            aiohttp.ClientError: Network-level failure (propagates to coordinator).
        """
        _LOGGER.debug("Fetching CZ fuel price caps from %s", _PRICES_URL)
        try:
            async with session.get(
                _PRICES_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status == 404:
                    raise ProviderError(
                        "CZ fuel prices JSON not found (HTTP 404). "
                        "The community scraper at Duchnaa/fuel-prices-cz may have "
                        "moved or been deleted."
                    )
                response.raise_for_status()
                payload: dict = await response.json(content_type=None)
        except ClientResponseError as err:
            raise ProviderError(
                f"HTTP error fetching CZ fuel prices: {err.status} {err.message}"
            ) from err

        if not isinstance(payload, dict):
            raise ProviderError(
                f"Unexpected CZ fuel prices response type: {type(payload).__name__}"
            )
        return payload


# ── Module-level helpers ──────────────────────────────────────────────────────


def _safe_price(raw: object) -> float | None:
    """Parse and validate a CZK/litre price value.

    Czech fuel prices are in the range ~30–60 CZK/litre.  Values outside
    the plausible 0–500 CZK/litre range (i.e. non-positive or > 500) are
    rejected.

    Args:
        raw: Raw value from the JSON (float, int, str, or None).

    Returns:
        Validated float rounded to 3 decimal places, or None if invalid.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > 500:
        return None
    return round(val, 3)


def _parse_prices(payload: dict) -> StationData:
    """Build a StationData dict from the raw community JSON payload.

    Args:
        payload: Parsed JSON dict from the prices endpoint.

    Returns:
        Populated StationData dict.

    Raises:
        ProviderError: 'current' key is absent from the payload.
    """
    current = payload.get("current")
    if current is None:
        raise ProviderError(
            "CZ fuel prices JSON is missing the 'current' key. "
            "The community scraper format may have changed."
        )

    natural95 = _safe_price(current.get("natural95_cap"))
    diesel = _safe_price(current.get("diesel_cap"))

    last_updated: str | None = payload.get("last_updated") or None

    _LOGGER.debug(
        "CZ fuel prices parsed: natural95_cap=%s diesel_cap=%s last_updated=%s",
        natural95,
        diesel,
        last_updated,
    )

    return {
        "unleaded": natural95,
        "diesel": diesel,
        "name": "Czech Republic — National Cap Price",
        "lastupdated": last_updated,
        "source_station_id": _NATIONAL_STATION_ID,
    }
