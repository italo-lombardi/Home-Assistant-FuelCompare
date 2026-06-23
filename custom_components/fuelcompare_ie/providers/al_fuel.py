"""AlFuelProvider — Albanian national-average fuel prices (cargopedia.net).

Source: https://www.cargopedia.net/europe-fuel-prices
        (fallback: https://www.tolls.eu/fuel-prices)
Auth:   none
Update: approximately weekly

Albania has no official government fuel-price API and no public station-level
data source.  The only confirmed free path for Albanian fuel prices is scraping
one of two European fuel-price aggregator tables that include Albania:

  Primary:  cargopedia.net/europe-fuel-prices
  Fallback: tolls.eu/fuel-prices

Both pages publish a country-level table with national average prices for
gasoline 95, diesel, and LPG in EUR/litre, updated approximately weekly.

The station_id for this provider is the country code "AL"; only one virtual
"station" (the national average) exists.

CONFIG_MODE = 'location'
  The user does not need to enter a station UUID.  The coordinator calls
  async_fetch(session, "AL") and the provider returns national averages.

Data quality
------------
- Granularity: national average only (no station-level data exists publicly).
- Update frequency: weekly (latest data typically a few days old).
- Source: scraped HTML table — fragile if either site changes its layout.
- Prices: EUR/litre (already in target currency, no conversion needed).

Scraping strategy
-----------------
The cargopedia.net page contains a <table> (or equivalent markup) with one
<tr> per country.  The Albania row is identified by the text "Albania" in the
first cell.  Cells 2, 3, 4 (0-indexed: 1, 2, 3) hold gasoline 95, diesel,
and LPG prices respectively.  Values like "1.809" are parsed as floats.

If cargopedia.net fails the provider retries tolls.eu/fuel-prices which uses
a similar table layout with a "Albania" row.

Reference TypeScript implementation:
  https://github.com/PhoenixKola/albania-fuel-prices
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

# ── Endpoints ─────────────────────────────────────────────────────────────────

_PRIMARY_URL = "https://www.cargopedia.net/europe-fuel-prices"
_FALLBACK_URL = "https://www.tolls.eu/fuel-prices"

_COUNTRY_LABEL = "Albania"
_STATION_ID = "AL"

# ── HTTP configuration ────────────────────────────────────────────────────────

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)  # HTML pages may be larger/slower

# ── Regex helpers ─────────────────────────────────────────────────────────────

# Match a decimal price like "1.809" or "0.679"
_PRICE_RE = re.compile(r"\b(\d{1,2}\.\d{2,4})\b")

# Match a <tr>…</tr> block (non-greedy, case-insensitive, dotall)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)

# Strip HTML tags
_TAG_RE = re.compile(r"<[^>]+>")


class AlFuelProvider(BaseProvider):
    """Fetch Albanian national-average fuel prices from cargopedia.net.

    No official Albanian government fuel-price API exists.  This provider
    scrapes the country-level average table at cargopedia.net/europe-fuel-prices
    (primary) or tolls.eu/fuel-prices (fallback).

    The virtual "station" has station_id = "AL" (the ISO country code).
    All three fuel prices (gasoline 95, diesel, LPG) are in EUR/litre.

    Usage
    -----
    provider = AlFuelProvider(station_id="AL")
    data = await provider.async_fetch(session, "AL")
    """

    COUNTRY = "AL"
    PROVIDER_KEY = "al_fuel"
    DISABLED = True  # 0.7.0: upstream broken — disable until fixed
    LABEL = "Albania National Average (cargopedia.net)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "global_list"

    POLL_INTERVAL_SECONDS = 86400
    STATION_PAGE_URL: ClassVar[str] = (
        "https://www.cargopedia.net"  # daily — data updates approximately weekly
    )

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices (EUR/litre national averages)
            # gasoline 95 → unleaded (standard StationData key for 95-octane petrol)
            "unleaded",
            "diesel",
            "lpg",
            # Station identity (virtual)
            "name",
            "county",
            # Timing
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Albania has only national-average data.  The station ID is 'AL' "
        "(the ISO country code).  No station-level data is available."
    )

    def __init__(self, station_id: str = _STATION_ID) -> None:
        """Initialise the provider.

        Args:
            station_id:  Always "AL" for this provider.  Other values are
                         accepted for interface compatibility but will behave
                         identically (only national averages are returned).
        """
        self._station_id = station_id

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch Albanian national-average fuel prices.

        Scrapes cargopedia.net/europe-fuel-prices (primary) or
        tolls.eu/fuel-prices (fallback) and extracts the Albania row.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored (only "AL" national average exists).

        Returns:
            StationData dict with gasoline_95, diesel, and lpg prices in
            EUR/litre, plus name and county set to "Albania".

        Raises:
            ProviderError: Albania row not found on either page, or both
                           requests fail with HTTP errors.
        """
        # Try primary source first
        html = await self._fetch_html(session, _PRIMARY_URL)
        if html is not None:
            prices = _parse_albania_row(html)
            if prices is not None:
                _LOGGER.debug(
                    "AlFuel parsed from cargopedia: unleaded(95)=%s diesel=%s lpg=%s",
                    prices.get("unleaded"),
                    prices.get("diesel"),
                    prices.get("lpg"),
                )
                return _build_station_data(prices)

        _LOGGER.debug(
            "AlFuel: primary source failed or Albania row not found; trying fallback"
        )

        # Try fallback source
        html = await self._fetch_html(session, _FALLBACK_URL)
        if html is not None:
            prices = _parse_albania_row(html)
            if prices is not None:
                _LOGGER.debug(
                    "AlFuel parsed from tolls.eu: unleaded(95)=%s diesel=%s lpg=%s",
                    prices.get("unleaded"),
                    prices.get("diesel"),
                    prices.get("lpg"),
                )
                return _build_station_data(prices)

        raise ProviderError(
            "Failed to retrieve Albanian fuel prices from cargopedia.net "
            "or tolls.eu.  The page layout may have changed.  "
            "Please open an issue at "
            "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues"
        )

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return 'Albania' as the station display name.

        This provider returns national averages only; no per-station name
        resolution is required.

        Args:
            session:    aiohttp ClientSession (not used).
            station_id: Ignored.

        Returns:
            "Albania" — the display name for the config flow.
        """
        return "Albania"

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: object,
    ) -> list[tuple[str, str]]:
        """Return the single virtual station (national average).

        For location-mode providers the config flow calls this method to
        populate the station picker.  Albania has only one virtual "station":
        the national average.

        Args:
            session: aiohttp ClientSession.
            lat:     Ignored (no station coords exist).
            lng:     Ignored.
            radius_km: Ignored.

        Returns:
            A single-element list: [("AL", "Albania — National Average")].
        """
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")

        # is-not-None checks (not falsy) so lat=0.0 / lng=0.0 are accepted
        if lat is not None and lng is not None:
            _LOGGER.debug(
                "AlFuel async_list_stations called with lat=%s lng=%s (ignored — "
                "national average only)",
                lat,
                lng,
            )

        return [(_STATION_ID, "Albania — National Average (EUR/litre, ~weekly)")]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_html(self, session: ClientSession, url: str) -> str | None:
        """Fetch a URL and return the response body as text, or None on error.

        Args:
            session: aiohttp ClientSession.
            url:     Target URL.

        Returns:
            HTML text on success, or None on HTTP/network error.
        """
        _LOGGER.debug("AlFuel fetching: %s", url)
        try:
            async with session.get(
                url,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status == 404:
                    _LOGGER.debug("AlFuel got HTTP 404 for %s", url)
                    return None
                response.raise_for_status()
                return await response.text()
        except ClientResponseError as err:
            _LOGGER.debug("AlFuel HTTP error for %s: %s", url, err)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("AlFuel unexpected error for %s: %s", url, err)
            return None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _strip_tags(html: str) -> str:
    """Strip all HTML tags from a string, returning only text content.

    Args:
        html: HTML fragment string.

    Returns:
        Plain text with tags removed and whitespace collapsed.
    """
    text = _TAG_RE.sub(" ", html)
    # Collapse runs of whitespace
    return " ".join(text.split())


def _extract_price_from_cell(cell_html: str) -> float | None:
    """Extract the first decimal price from a table cell's HTML.

    Args:
        cell_html: Raw HTML of a single <td> element.

    Returns:
        Float price (EUR/litre) or None if no valid price is found.
    """
    text = _strip_tags(cell_html)
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    try:
        val = float(match.group(1))
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    # Sanity-check: Albanian fuel prices are in the range 0.3–5.0 EUR/litre
    if val > 10.0:
        return None
    return round(val, 3)


def _parse_albania_row(html: str) -> dict[str, float | None] | None:
    """Parse the Albania row from a European fuel-price HTML table.

    Searches for a <tr> containing "Albania" and extracts the prices for
    gasoline 95 (column index 1), diesel (column index 2), and LPG
    (column index 3).  Returns None if the row is not found or no valid
    prices are extractable.

    Both cargopedia.net and tolls.eu use a similar layout:
      col 0: country name
      col 1: gasoline 95 price  → mapped to "unleaded" (StationData key)
      col 2: diesel price
      col 3: LPG price (may be absent or "—" if not applicable)

    Args:
        html: Full HTML page text.

    Returns:
        Dict with keys "unleaded" (gasoline 95), "diesel", "lpg" (values may
        be None), or None if the Albania row is not found at all.
    """
    # Split into <tr> blocks
    for tr_match in _TR_RE.finditer(html):
        row_html = tr_match.group(1)
        row_text = _strip_tags(row_html)

        # Check if this row is the Albania row
        if _COUNTRY_LABEL.lower() not in row_text.lower():
            continue

        # Extract <td> (and <th>) cells from this row
        cell_re = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
        cells = cell_re.findall(row_html)

        if len(cells) < 2:
            # Row found but too few cells — skip and keep searching
            continue

        # Validate the first cell actually names Albania (guards against partial
        # matches in data cells)
        first_cell_text = _strip_tags(cells[0]).strip()
        if _COUNTRY_LABEL.lower() not in first_cell_text.lower():
            continue

        # col 1 = gasoline 95 (mapped to 'unleaded' in StationData)
        # col 2 = diesel
        # col 3 = LPG
        unleaded = _extract_price_from_cell(cells[1]) if len(cells) > 1 else None
        diesel = _extract_price_from_cell(cells[2]) if len(cells) > 2 else None
        lpg = _extract_price_from_cell(cells[3]) if len(cells) > 3 else None

        return {
            "unleaded": unleaded,
            "diesel": diesel,
            "lpg": lpg,
        }

    return None


def _build_station_data(prices: dict[str, float | None]) -> StationData:
    """Build a StationData dict from the parsed price dict.

    Args:
        prices: Dict with keys "unleaded" (gasoline 95), "diesel", "lpg".

    Returns:
        Populated StationData dict.
    """
    data: StationData = {
        "unleaded": prices.get("unleaded"),  # gasoline 95, EUR/litre
        "diesel": prices.get("diesel"),
        "lpg": prices.get("lpg"),
        "name": "Albania",
        "county": "Albania",
        "lastupdated": None,  # scraping does not return a precise timestamp
        "source_station_id": _STATION_ID,
    }
    return data
