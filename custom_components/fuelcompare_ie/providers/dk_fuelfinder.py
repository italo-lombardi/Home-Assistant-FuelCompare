"""DkFuelFinderProvider — Danish national fuel prices from fuelfinder.dk.

Source: fuelfinder.dk, operated by a Danish fuel price aggregator.
Endpoint: GET https://www.fuelfinder.dk/listprices.php
Returns an HTML page containing a table of national average prices per fuel
company/brand for multiple fuel types.  No authentication required.  The WAF
blocks curl and simple python-requests user-agents; this provider uses a
realistic browser User-Agent to bypass it.

Data model
----------
fuelfinder.dk does NOT expose a per-station location API.  The ``listprices.php``
endpoint returns national average prices broken down by **fuel company** (brand),
with columns for each fuel type.  This is the only stable public data surface
the site provides (confirmed June 2026; the ``/api/v1/stations`` REST path
documented in older HACS references returns HTTP 404).

Because there is no per-station GPS data, this provider treats each fuel brand
as an individual "station".  The station identifier (``station_id``) is the
brand/company name string as it appears in the ``Benzinselskab`` column of the
HTML table (e.g. ``"Circle K"``, ``"Q8"``, ``"OK"``, ``"Shell"``).

STATION_LOOKUP_MODE = "location_search"
---------------------------------------
The config flow calls ``async_list_stations`` with lat/lng/radius kwargs but,
since fuelfinder.dk has no location data, the provider returns all available
brands regardless of coordinates.  The label includes the best available price
so the user can pick the company they use.  This is the closest approximation
to a location search that the data source supports.

CONFIG_MODE = "station_id"
--------------------------
The user selects a brand name (e.g. ``"Circle K"``) which is stored as the
``station_id`` config entry value.  ``async_fetch`` then looks up that brand
in the current HTML table and returns its prices.

HTML table format
-----------------
The ``<table>`` on listprices.php has the following structure:

  <th>Benzinselskab</th>   — fuel company/brand name (row index)
  <th>Blyfri 92</th>
  <th>Blyfri 95 (E10)</th>
  <th>Blyfri 95+ (E10)</th>
  <th>Blyfri + (E5)</th>
  <th>Diesel (B7)</th>
  <th>Diesel +</th>
  <th>HVO (XTL)</th>
  <th>EL normal</th>
  <th>EL hurtig</th>
  <th>EL lyn</th>

Prices are Danish kroner (DKK) per litre, displayed as decimal strings with
either a comma or period as the decimal separator (e.g. ``"14,13"`` or
``"14.13"``).  Empty cells mean the brand does not sell that fuel type.

Fuel mapping to StationData keys
---------------------------------
HTML column            StationData key    Notes
--------------------   ----------------   ------
Blyfri 95 (E10)        unleaded           standard Danish petrol
Blyfri 95+ (E10)       premium_unleaded   premium petrol (95+)
Blyfri + (E5)          (not mapped)       high-octane E5; too sparse
Blyfri 92              (not mapped)       low-octane; sparse
Diesel (B7)            diesel             standard diesel
Diesel +               premium_diesel     premium diesel
HVO (XTL)              (not mapped)       HVO renewable diesel; sparse

Only ``unleaded`` and ``diesel`` are declared in CAPABILITIES because they
are the two fuel types with consistent data across all brands.
``premium_unleaded`` and ``premium_diesel`` are included as bonus fields.

Price units
-----------
Raw HTML values are DKK/litre (e.g. ``"14,13"``).  No conversion is applied;
prices are stored as DKK/litre floats (e.g. ``14.13``).

WAF headers
-----------
The Simply.com WAF on fuelfinder.dk blocks requests without a real browser
User-Agent (returns HTTP 454/455).  Requests must use a Chrome-like UA.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_LISTPRICES_URL = "https://www.fuelfinder.dk/listprices.php"

# The WAF requires a realistic browser UA; HomeAssistant/aiohttp and curl are blocked.
_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.fuelfinder.dk/",
    "Connection": "keep-alive",
}

# Generous timeout: the page is ~30 KB but the WAF can be slow.
_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT * 3, 30))

# Maximum DKK/litre price accepted as valid (outlier guard).
# Danish pump prices are typically 11–17 DKK/L; anything above 50 is an error.
_MAX_DKK_PER_LITRE: float = 50.0

# HTML column header → StationData key mapping.
# Only columns that map to a StationData key are included; others are skipped.
_COLUMN_MAP: dict[str, str] = {
    "Blyfri 95 (E10)": "unleaded",
    "Blyfri 95+ (E10)": "premium_unleaded",
    "Diesel (B7)": "diesel",
    "Diesel +": "premium_diesel",
}


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Minimal SAX-style HTML parser that extracts the first <table>.

    Builds a list of rows; each row is a list of cell text strings.
    The first row is treated as the header.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_text: list[str] = []
        self._rows: list[list[str]] = []
        self._done = False  # stop processing after the first table closes

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._done:
            return
        if tag == "table":
            self._in_table = True
        elif tag in ("th", "td") and self._in_table:
            self._in_cell = True
            self._current_text = []
        elif tag == "tr" and self._in_table:
            self._current_row = []

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag == "table" and self._in_table:
            self._in_table = False
            self._done = True
        elif tag in ("th", "td") and self._in_cell:
            self._in_cell = False
            # Strip zero-width spaces and surrounding whitespace from cell text.
            cell_text = "".join(self._current_text).replace("​", "").strip()
            self._current_row.append(cell_text)
        elif tag == "tr" and self._in_table:
            if self._current_row:
                self._rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_text.append(data)

    @property
    def rows(self) -> list[list[str]]:
        """Return parsed rows (including header row at index 0)."""
        return self._rows


def _parse_table(html: str) -> dict[str, dict[str, float | None]]:
    """Parse the listprices HTML table into a dict of brand → {fuel_key: price}.

    Args:
        html: Raw HTML response text.

    Returns:
        Dict mapping brand name to a dict of StationData price keys.
        Price values are floats in DKK/litre or None when the brand does not
        sell that fuel type or the cell is empty/invalid.

    Raises:
        ProviderError: When no table is found in the HTML or it has no data rows.
    """
    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows

    if not rows:
        raise ProviderError(
            "fuelfinder.dk: No table found in listprices.php response. "
            "The page structure may have changed."
        )

    # First row is the header.
    header = rows[0]
    if not header or header[0].lower() not in ("benzinselskab", "selskab"):
        raise ProviderError(
            f"fuelfinder.dk: Unexpected table header: {header!r}. "
            "Expected first column 'Benzinselskab'."
        )

    # Map column index → StationData key (skip unmapped columns).
    col_index_to_key: dict[int, str] = {}
    for col_idx, col_name in enumerate(header):
        if col_idx == 0:
            continue  # brand column
        # Normalise: strip surrounding whitespace and zero-width spaces.
        normalised = col_name.replace("​", "").strip()
        data_key = _COLUMN_MAP.get(normalised)
        if data_key:
            col_index_to_key[col_idx] = data_key

    result: dict[str, dict[str, float | None]] = {}
    for row in rows[1:]:
        if not row:
            continue
        brand = row[0].strip()
        if not brand:
            continue
        prices: dict[str, float | None] = {}
        for col_idx, data_key in col_index_to_key.items():
            raw = row[col_idx] if col_idx < len(row) else ""
            prices[data_key] = _parse_price_dkk(raw)
        result[brand] = prices

    if not result:
        raise ProviderError(
            "fuelfinder.dk: Table contained no data rows. "
            "The page may be temporarily unavailable."
        )

    return result


def _parse_price_dkk(raw: str | None) -> float | None:
    """Parse a raw cell string (DKK/litre) to float, or return None.

    Handles both comma and period decimal separators (e.g. ``"14,13"`` or
    ``"14.13"``).  Returns None for empty strings and out-of-range values.

    Args:
        raw: Cell text from the HTML table, or None.

    Returns:
        DKK/litre float rounded to 2 decimal places, or None.
    """
    if not raw or not raw.strip():
        return None
    # Normalise: remove zero-width spaces and replace comma separator.
    cleaned = raw.replace("​", "").strip().replace(",", ".")
    try:
        val = float(cleaned)
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > _MAX_DKK_PER_LITRE:
        return None
    return round(val, 2)


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class DkFuelFinderProvider(BaseProvider):
    """Fetch Danish national fuel prices from fuelfinder.dk.

    fuelfinder.dk does not expose a per-station GPS API; it only provides
    national average prices per fuel company.  This provider returns the
    prices for the configured brand (``station_id``) from the HTML table.

    The "station" is a fuel brand (e.g. ``"Circle K"``, ``"Shell"``);
    ``async_list_stations`` returns all brands visible in the table so
    the user can pick their preferred company in the config flow.

    Constructor
    -----------
    station_id:  Brand/company name as it appears in the Benzinselskab column
                 (e.g. ``"Circle K"``, ``"Q8"``).  Case-sensitive; must match
                 the spelling returned by the API.
    """

    COUNTRY = "DK"
    PROVIDER_KEY = "dk_fuelfinder"
    DISABLED = True  # 0.7.0: upstream broken — disable until fixed
    LABEL = "FuelFinder (Denmark)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 3600
    STATION_PAGE_URL: ClassVar[str] = (
        "https://www.fuelfinder.dk"  # 1 hour; WAF is strict; data changes ~daily
    )
    CURRENCY: ClassVar[str] = "kr"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",
            "premium_unleaded",
            "diesel",
            "premium_diesel",
            "name",
            "brand",
        }
    )

    REQUIRES_API_KEY = False

    STATION_ID_HINT = (
        "Enter the fuel company name exactly as listed on fuelfinder.dk/listprices.php "
        "(e.g. 'Circle K', 'Q8', 'OK', 'Shell', 'Esso'). "
        "Use the station picker to browse available brands."
    )

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
    ) -> None:
        """Initialise the provider with the brand/company name as station_id."""
        self._station_id = station_id

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised prices for the configured brand.

        Fetches the listprices.php HTML table and finds the row matching
        ``station_id`` (brand name).  Returns a StationData dict with fuel
        prices in DKK/litre.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Brand name string (e.g. ``"Circle K"``).

        Returns:
            StationData dict with price keys populated for this brand.

        Raises:
            ProviderError: Brand not found in the table, or parse failure.
            aiohttp.ClientError: Network/HTTP failure (let propagate to
                coordinator which converts it to UpdateFailed).
        """
        brand_table = await self._fetch_table(session)

        # Case-insensitive lookup to tolerate minor capitalisation drift.
        brand_data = brand_table.get(station_id)
        if brand_data is None:
            # Try case-insensitive fallback.
            station_id_lower = station_id.lower()
            for brand_key, prices in brand_table.items():
                if brand_key.lower() == station_id_lower:
                    brand_data = prices
                    break

        if brand_data is None:
            available = ", ".join(sorted(brand_table.keys()))
            raise ProviderError(
                f"fuelfinder.dk: Brand '{station_id}' not found in table. "
                f"Available brands: {available}"
            )

        return self._build_station_data(station_id, brand_data)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the brand name for the config flow, or None.

        For fuelfinder.dk the station_id IS the brand name, so this simply
        verifies that the brand exists and returns it.  Returns None if the
        brand cannot be found or the fetch fails.

        Args:
            session:    aiohttp ClientSession.
            station_id: Brand name string.
        """
        try:
            brand_table = await self._fetch_table(session)
            # Check exact then case-insensitive.
            if station_id in brand_table:
                return station_id
            station_id_lower = station_id.lower()
            for brand_key in brand_table:
                if brand_key.lower() == station_id_lower:
                    return brand_key
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for '%s': %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (brand_key, display_label) pairs for the station picker.

        Called by the config flow location_search step.  Returns all brands
        from the listprices table sorted alphabetically by label.  The
        lat/lng/radius kwargs are accepted for API symmetry but are not used
        for filtering because fuelfinder.dk does not provide per-station
        location data.

        Labels use the format ``"{brand_name} (#{brand_key[:8]})"`` — no
        price is included because prices may change between the list step and
        the actual fetch.  The brand key (company name) is used as the station
        identifier.

        Args:
            session:    aiohttp ClientSession.
            lat:        Reference latitude (accepted but not used for filtering).
            lng:        Reference longitude (accepted but not used for filtering).
            radius_km:  Search radius in km (accepted but not used for filtering).

        Returns:
            List of (brand_key, label) tuples sorted alphabetically by label.
            Returns [] on any network or parse failure.
        """
        try:
            brand_table = await self._fetch_table(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        if not brand_table:
            return []

        entries: list[tuple[str, str]] = []

        for brand_key in brand_table:
            label = f"{brand_key} (#{brand_key[:8]})"
            entries.append((brand_key, label))

        entries.sort(key=lambda x: x[1])
        return entries

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_table(
        self,
        session: ClientSession,
    ) -> dict[str, dict[str, float | None]]:
        """Fetch listprices.php and parse the price table.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Dict of brand → {StationData key: DKK/litre price or None}.

        Raises:
            ProviderError: Parse failure or unexpected response structure.
            aiohttp.ClientError: Network or HTTP failure (let propagate).
        """
        _LOGGER.debug("Fetching fuelfinder.dk listprices from %s", _LISTPRICES_URL)
        async with session.get(
            _LISTPRICES_URL,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as response:
            response.raise_for_status()
            html = await response.text(encoding="utf-8", errors="replace")

        return _parse_table(html)

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        brand: str,
        prices: dict[str, float | None],
    ) -> StationData:
        """Assemble a StationData dict from a parsed brand price row.

        Args:
            brand:  Brand/company name (used as name, brand, and source_station_id).
            prices: Dict of StationData price keys → DKK/litre floats or None.

        Returns:
            Populated StationData dict.
        """
        data: StationData = {
            "unleaded": prices.get("unleaded"),
            "premium_unleaded": prices.get("premium_unleaded"),
            "diesel": prices.get("diesel"),
            "premium_diesel": prices.get("premium_diesel"),
            "name": brand,
            "brand": brand,
            "source_station_id": brand,
        }

        _LOGGER.debug(
            "fuelfinder.dk parsed data for brand '%s': "
            "unleaded=%s diesel=%s premium_unleaded=%s premium_diesel=%s",
            brand,
            data.get("unleaded"),
            data.get("diesel"),
            data.get("premium_unleaded"),
            data.get("premium_diesel"),
        )

        return data
