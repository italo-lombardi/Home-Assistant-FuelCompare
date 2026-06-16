"""LtSauridaProvider — Lithuanian fuel prices from saurida.lt.

Source: saurida.lt/kuro-kainos-degalinese/ — the official Saurida fuel chain
website, which publishes per-station prices as a plain HTML table.

Coverage
--------
Saurida is an independent regional chain operating ~34 stations across
Lithuania.  It is NOT a national aggregator — major chains (Circle K,
Neste, Orlen) are listed on kuro-kainos.lt but that site explicitly
prohibits data reuse without permission.  No official Lithuanian government
fuel price API was found (data.gov.lt and stat.gov.lt both return 403).

Confidence: moderate (5/10).  The source is live and scrapeable today but
limited to one chain, has no API stability guarantee, and the terms of use
are ambiguous.

HTML table format
-----------------
The page contains a single table:
  <table class="table text-left responsive">

The first row is the header; subsequent rows contain one station per row.
Column order (as observed June 2026):

  Column 0: station name (e.g. "Vilnius, Kalvarijų g. 3")
  Column 1: dyzelinas_b7       (Diesel B7,   EUR/litre, e.g. "1.539")
  Column 2: benzinas_a95_e5    (Petrol 95 E5, EUR/litre)
  Column 3: benzinas_a98_e5    (Petrol 98 E5, EUR/litre)
  Column 4: dujos_lpg          (LPG,          EUR/litre)
  Column 5: dyzelinas_dz       (Premium diesel, EUR/litre)

Empty cells (the station does not sell that fuel type) are stored as None.

The column order is inferred from header text matching; the parser is
resilient to column reordering.

StationData key mapping
-----------------------
Saurida column          StationData key   Notes
----------------------  ----------------  -----
station name            name              display name; also used as station_id
dyzelinas_b7            diesel            Diesel B7, EUR/litre
benzinas_a95_e5         unleaded          Petrol 95 E5; standard unleaded
benzinas_a98_e5         premium_unleaded  Petrol 98 E5; premium
dujos_lpg               lpg               Autogas LPG
dyzelinas_dz            premium_diesel    Saurida premium diesel (DZ grade)

STATION_LOOKUP_MODE = "location_search"
---------------------------------------
Saurida's HTML table contains no GPS coordinates.  The config flow calls
``async_list_stations`` with lat/lng/radius kwargs; since no location data
is available, all stations are returned regardless of coordinates, sorted
cheapest-first by diesel price.  The user selects the nearest station by
name.

CONFIG_MODE = "station_id"
--------------------------
The station identifier is the station name string exactly as it appears in
the first column of the HTML table (e.g. ``"Vilnius, Kalvarijų g. 3"``).

Price units
-----------
Saurida prices are EUR/litre already (e.g. ``"1.539"``).  No conversion
is applied.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_PRICES_URL = "https://www.saurida.lt/kuro-kainos-degalinese/"

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.saurida.lt/",
    "Connection": "keep-alive",
}

_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT * 3, 30))

# Max EUR/litre guard — Lithuanian pump prices are typically 1.2–2.0 EUR/L.
_MAX_EUR_PER_LITRE: float = 10.0

# Header text fragments → StationData key.
# Matching is case-insensitive substring match so minor whitespace/case
# changes in the header do not break the parser.
_HEADER_TO_KEY: dict[str, str] = {
    "dyzelinas_b7": "diesel",
    "dyzelinas b7": "diesel",
    "b7": "diesel",
    "benzinas_a95_e5": "unleaded",
    "benzinas a95": "unleaded",
    "a95": "unleaded",
    "benzinas_a98_e5": "premium_unleaded",
    "benzinas a98": "premium_unleaded",
    "a98": "premium_unleaded",
    "dujos_lpg": "lpg",
    "dujos": "lpg",
    "lpg": "lpg",
    "dyzelinas_dz": "premium_diesel",
    "dyzelinas dz": "premium_diesel",
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
        self._done = False

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
        """Return parsed rows (header row at index 0)."""
        return self._rows


def _header_to_data_key(header_cell: str) -> str | None:
    """Map a raw table header cell text to a StationData key, or None.

    Matching is done by iterating _HEADER_TO_KEY fragments from longest to
    shortest (greedy) so more specific patterns win over generic ones.

    Args:
        header_cell: Raw cell text from the HTML table header.

    Returns:
        StationData key string, or None if the header is not recognised.
    """
    normalised = header_cell.lower().replace("​", "").strip()
    # Try longest-first to avoid e.g. "dz" matching before "dyzelinas_dz"
    for fragment, key in sorted(_HEADER_TO_KEY.items(), key=lambda x: -len(x[0])):
        if fragment in normalised:
            return key
    return None


def _parse_price_eur(raw: str | None) -> float | None:
    """Parse a raw cell string (EUR/litre) to float, or return None.

    Handles both comma and period decimal separators.  Returns None for
    empty strings, zero, and out-of-range values.

    Args:
        raw: Cell text from the HTML table, or None.

    Returns:
        EUR/litre float rounded to 3 decimal places, or None.
    """
    if not raw or not raw.strip():
        return None
    cleaned = raw.replace("​", "").strip().replace(",", ".")
    try:
        val = float(cleaned)
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > _MAX_EUR_PER_LITRE:
        return None
    return round(val, 3)


def _parse_table(html: str) -> list[dict[str, Any]]:
    """Parse the saurida.lt price table into a list of station dicts.

    Each dict contains:
      "name"             — station display name (str)
      "diesel"           — EUR/litre float or None
      "unleaded"         — EUR/litre float or None
      "premium_unleaded" — EUR/litre float or None
      "lpg"              — EUR/litre float or None
      "premium_diesel"   — EUR/litre float or None

    Args:
        html: Raw HTML response text.

    Returns:
        List of station price dicts (one per data row).

    Raises:
        ProviderError: No table found, unexpected header, or no data rows.
    """
    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows

    if not rows:
        raise ProviderError(
            "saurida.lt: No table found in kuro-kainos-degalinese/ response. "
            "The page structure may have changed."
        )

    # First row is the header.
    header = rows[0]
    if not header:
        raise ProviderError(
            "saurida.lt: Empty header row in price table. "
            "The page structure may have changed."
        )

    # Map column index → StationData key.  Column 0 is always the station name.
    col_index_to_key: dict[int, str] = {}
    for col_idx, col_name in enumerate(header):
        if col_idx == 0:
            continue  # station name column
        data_key = _header_to_data_key(col_name)
        if data_key:
            col_index_to_key[col_idx] = data_key

    stations: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        station_name = row[0].strip()
        prices: dict[str, float | None] = {
            "diesel": None,
            "unleaded": None,
            "premium_unleaded": None,
            "lpg": None,
            "premium_diesel": None,
        }
        for col_idx, data_key in col_index_to_key.items():
            raw = row[col_idx] if col_idx < len(row) else ""
            prices[data_key] = _parse_price_eur(raw)
        entry: dict[str, Any] = {"name": station_name}
        entry.update(prices)
        stations.append(entry)

    if not stations:
        raise ProviderError(
            "saurida.lt: Table contained no data rows. "
            "The page may be temporarily unavailable."
        )

    return stations


def _find_station(
    stations: list[dict[str, Any]], station_id: str
) -> dict[str, Any] | None:
    """Return the station dict whose name matches station_id, or None.

    Exact match first; falls back to case-insensitive comparison.

    Args:
        stations:   List of station dicts from _parse_table.
        station_id: Target station name string.

    Returns:
        Matching station dict, or None.
    """
    for station in stations:
        if station.get("name") == station_id:
            return station
    station_id_lower = station_id.lower()
    for station in stations:
        if (station.get("name") or "").lower() == station_id_lower:
            return station
    return None


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class LtSauridaProvider(BaseProvider):
    """Fetch Lithuanian fuel prices from the Saurida chain (saurida.lt).

    Saurida is an independent regional fuel chain with ~34 stations across
    Lithuania.  Prices are published as a plain HTML table on their website.
    No API key is required.

    The "station" is identified by the station name string exactly as it
    appears in the first column of the HTML table.  ``async_list_stations``
    returns all stations so the user can pick their nearest station in the
    config flow.

    Coverage note
    -------------
    This provider covers Saurida-branded stations only.  Major Lithuanian
    chains (Circle K, Neste, Orlen) are not included because their aggregator
    (kuro-kainos.lt) prohibits scraping.  No official Lithuanian government
    fuel price API is available.

    Constructor
    -----------
    station_id:  Station name as listed on saurida.lt (e.g.
                 ``"Vilnius, Kalvarijų g. 3"``).  Accepted at construction
                 for API symmetry; the active value is passed to async_fetch.
    latitude:    Optional WGS84 latitude (accepted but not used for filtering
                 because saurida.lt provides no GPS data).
    longitude:   Optional WGS84 longitude (same caveat).
    radius_km:   Optional search radius in km (accepted but not used).
    """

    COUNTRY = "LT"
    PROVIDER_KEY = "lt_saurida"
    LABEL = "Saurida (Lithuania)"
    CONFIG_MODE = "station_id"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 3600
    STATION_PAGE_URL: ClassVar[str] = (
        "https://saurida.lt"  # prices change ~daily; 1-hour poll is sufficient
    )

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "diesel",
            "unleaded",
            "premium_unleaded",
            "lpg",
            "premium_diesel",
            # Station identity
            "name",
            "brand",
        }
    )

    REQUIRES_API_KEY = False

    STATION_ID_HINT = (
        "Enter the Saurida station name exactly as listed on "
        "saurida.lt/kuro-kainos-degalinese/ "
        "(e.g. 'Vilnius, Kalvarijų g. 3'). "
        "Use the station picker to browse all available stations."
    )

    def __init__(
        self,
        station_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        self._station_id = station_id
        # lat/lng/radius accepted for API symmetry; saurida.lt has no GPS data.
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised fuel prices for the configured station.

        Fetches the saurida.lt HTML price table, parses it, and finds the row
        matching ``station_id`` (station name).  Returns a StationData dict
        with prices in EUR/litre.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Station name string (as listed on saurida.lt).

        Returns:
            StationData dict with CAPABILITIES keys populated.

        Raises:
            ProviderError: Station not found in the table, or parse failure.
            aiohttp.ClientError: Network/HTTP failure — propagates to the
                coordinator which converts it to UpdateFailed.
        """
        stations = await self._fetch_all_stations(session)

        station = _find_station(stations, station_id)
        if station is None:
            available = ", ".join(s.get("name", "") for s in stations[:5])
            raise ProviderError(
                f"saurida.lt: Station '{station_id}' not found in price table. "
                f"First available stations: {available}. "
                "Verify the station name at saurida.lt/kuro-kainos-degalinese/"
            )

        return self._build_station_data(station_id, station)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station name for the config flow, or None.

        For saurida.lt the station_id IS the station name, so this verifies
        the station exists and returns it.  Returns None on any failure so the
        config flow can fall back to ``'Station {id}'``.

        Args:
            session:    aiohttp ClientSession.
            station_id: Station name string.
        """
        try:
            stations = await self._fetch_all_stations(session)
            station = _find_station(stations, station_id)
            if station:
                return station.get("name") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for '%s': %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_name, display_label) pairs for the config flow picker.

        Fetches all stations from the saurida.lt HTML table.  The lat/lng/radius
        kwargs are accepted for API symmetry but are not used for filtering
        because the HTML source provides no GPS coordinates.  All stations are
        returned sorted alphabetically by display label.

        Label format: "{name} (#{key[:8]})" where key is the station name used
        as the station identifier.  No price information is included in the
        label.

        Args:
            session:   aiohttp ClientSession.
            lat:       Reference latitude (accepted but not used).
            lng:       Reference longitude (accepted but not used).
            radius_km: Search radius in km (accepted but not used).

        Returns:
            List of (station_name, label) tuples sorted alphabetically by
            label.  Returns [] on any network or parse failure.
        """
        # lat/lng are accepted (is-not-None coord check per spec) but saurida.lt
        # has no GPS data so we cannot filter by location.
        lat: float | None = kwargs.get("lat")  # type: ignore[assignment]
        lng: float | None = kwargs.get("lng")  # type: ignore[assignment]

        # Log if coordinates were provided — they are unused but accepted.
        if lat is not None and lng is not None:
            _LOGGER.debug(
                "async_list_stations: lat/lng provided (%s, %s) but saurida.lt "
                "provides no GPS data; returning all stations",
                lat,
                lng,
            )

        try:
            stations = await self._fetch_all_stations(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed: %s", err)
            return []

        if not stations:
            return []

        result: list[tuple[str, str]] = []

        for station in stations:
            name: str = station.get("name") or ""
            if not name:
                continue

            # key is the station name string used as the station identifier.
            key: str = name
            label = f"{name} (#{key[:8]})"
            result.append((name, label))

        result.sort(key=lambda x: x[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_all_stations(
        self,
        session: ClientSession,
    ) -> list[dict[str, Any]]:
        """Fetch and parse the saurida.lt HTML price table.

        Args:
            session: aiohttp ClientSession.

        Returns:
            List of station dicts (one per data row).

        Raises:
            ProviderError: Response could not be parsed as expected HTML table.
            aiohttp.ClientError: Network or HTTP failure (propagates to coordinator).
        """
        _LOGGER.debug("Fetching saurida.lt fuel prices from %s", _PRICES_URL)
        async with session.get(
            _PRICES_URL,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as response:
            response.raise_for_status()
            html = await response.text(encoding="utf-8", errors="replace")

        return _parse_table(html)

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(
        self,
        station_id: str,
        station: dict[str, Any],
    ) -> StationData:
        """Assemble a StationData dict from a parsed station row.

        Args:
            station_id: Station name string (used as source_station_id).
            station:    Parsed station dict from _parse_table.

        Returns:
            Populated StationData dict.
        """
        name: str | None = station.get("name") or None

        data: StationData = {
            "diesel": station.get("diesel"),
            "unleaded": station.get("unleaded"),
            "premium_unleaded": station.get("premium_unleaded"),
            "lpg": station.get("lpg"),
            "premium_diesel": station.get("premium_diesel"),
            "name": name,
            "brand": "Saurida",
        }

        _LOGGER.debug(
            "saurida.lt parsed data for station '%s': "
            "diesel=%s unleaded=%s premium_unleaded=%s lpg=%s premium_diesel=%s",
            station_id,
            data.get("diesel"),
            data.get("unleaded"),
            data.get("premium_unleaded"),
            data.get("lpg"),
            data.get("premium_diesel"),
        )

        return data
