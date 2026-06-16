"""BaFuelProvider — Bosnian fuel prices scraped from cijenegoriva.ba.

Source: cijenegoriva.ba — a community-run fuel price tracker covering
747 petrol stations across 81 cities in Bosnia and Herzegovina (BiH).
No official API exists; prices are rendered as plain HTML on city pages.

Approach
--------
Station-level prices are available per city page at predictable URLs:
    https://cijenegoriva.ba/{city-slug}
e.g. /sarajevo, /tuzla, /mostar, /banja-luka

Each page renders a table of stations with columns for station name,
address, city, and per-fuel prices (Diesel, Super 95, Super 98, LPG)
in KM/L (Bosnian Convertible Mark per litre).

Because this is an HTML scrape there is no stable API contract.  The
scraper is designed to be tolerant of layout variations:
  - Parses with html.parser (no third-party dependency).
  - Locates price cells by column header matching (case-insensitive).
  - Returns None for any column/value it cannot parse.
  - On any HTTP/network error, async_fetch returns None for all prices
    (the coordinator's stale-retention then kicks in).

Viability: 5/10 — data is clearly available and the site is reachable,
but scraping HTML from a third-party Next.js site is fragile.  This
provider should be treated as best-effort.

Station identity
----------------
Because the site does not expose machine-readable station IDs, station
identity is built from the city slug + a positional index derived from
the station table row order on the city page.  Format:
    "{city_slug}:{row_index}"  e.g. "sarajevo:0", "tuzla:3"

CONFIG_MODE is 'location' — the user selects a city, then picks their
station from the scraped list.  The station_id stored in the config
entry is the "{city}:{index}" composite key.

Currency
--------
Prices are in KM/L (Bosnian Convertible Mark per litre).
1 KM ≈ 0.51 EUR (fixed peg: 1 EUR = 1.95583 KM).
Prices are stored as KM/L without conversion; the sensor platform
renders them as-is.  The currency label is set via the provider's
CURRENCY class attribute (CURRENCY = "KM"

Robots / ToS
------------
No robots.txt disallow rules were detected for cijenegoriva.ba.
No authentication barriers were detected during testing.
The site is publicly accessible with no rate-limiting observed.

Update frequency
----------------
The site updates daily.  Recommended poll interval: 86400 s (24 hours).
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://cijenegoriva.ba"

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)

# Mapping of canonical column header substrings (lower-case) to StationData keys.
# The scraper matches table headers case-insensitively against these patterns.
_HEADER_TO_KEY: dict[str, str] = {
    "diesel": "diesel",
    "dizel": "diesel",
    "super 95": "unleaded",
    "eurosuper 95": "unleaded",
    "benz": "unleaded",  # fallback: any "benz*" column
    "super 98": "premium_unleaded",
    "eurosuper 98": "premium_unleaded",
    "lpg": "lpg",
    "autoplin": "lpg",
}

# Well-known BiH city slugs (used for async_list_stations city picker).
# The site uses the city name lowercased and hyphenated as the URL path.
# Note: banja-luka, bijeljina, doboj, trebinje return HTTP 404 on the live site.
_CITY_SLUGS: tuple[str, ...] = (
    "sarajevo",
    "tuzla",
    "mostar",
    "zenica",
    "brcko",
    "livno",
)


class BaFuelProvider(BaseProvider):
    """Fetch Bosnian fuel prices by scraping cijenegoriva.ba.

    Station identity: "{city_slug}:{row_index}" composite key.
    CONFIG_MODE='location': the user picks a city then selects from the
    station table on that city's page.

    Usage
    -----
    Constructor accepts:
        station_id:  "{city}:{index}" composite key stored in config entry.
        latitude:    Optional WGS84 latitude (informational; site does not
                     expose coordinates per station).
        longitude:   Optional WGS84 longitude.
        radius_km:   Not meaningfully used (no coordinate data from source).
    """

    COUNTRY = "BA"
    PROVIDER_KEY = "ba_fuel"
    LABEL = "cijenegoriva.ba (Bosnia and Herzegovina)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 86400  # site updates daily
    CURRENCY: ClassVar[str] = "KM"
    STATION_PAGE_URL: ClassVar[str] = "https://cijenegoriva.ba"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "diesel",
            "unleaded",
            "premium_unleaded",
            "lpg",
            "name",
            "address",
            "county",
        }
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
            station_id:  "{city_slug}:{row_index}" key, e.g. "sarajevo:3".
            county:      Not used by this provider; stored for interface compat.
            latitude:    WGS84 latitude of the tracked location.
            longitude:   WGS84 longitude of the tracked location.
            radius_km:   Search radius in km (used for async_list_stations).
        """
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 50.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised station data for a single station.

        Args:
            session:    aiohttp ClientSession.
            station_id: "{city_slug}:{row_index}" composite key.

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: station_id malformed or row index out of range.
        """
        city_slug, row_index = _parse_station_id(station_id)

        if city_slug not in _CITY_SLUGS:
            raise ProviderError(f"Unknown city slug: {city_slug!r}")

        html = await self._fetch_city_html(session, city_slug)
        if html is None:
            raise ProviderError(
                f"Failed to fetch cijenegoriva.ba page for city '{city_slug}'. "
                "Check your network connection or try again later."
            )

        stations = _parse_station_table(html)
        if row_index >= len(stations):
            raise ProviderError(
                f"Station index {row_index} is out of range for city '{city_slug}' "
                f"(found {len(stations)} station rows). "
                "The page layout may have changed — reconfigure the integration."
            )

        raw = stations[row_index]
        return _build_station_data(station_id, raw, city_slug)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        For CONFIG_MODE='location' providers the config flow uses a generated
        title, so this returns None without making any HTTP requests.

        Args:
            session:    aiohttp ClientSession.
            station_id: "{city_slug}:{row_index}" composite key.
        """
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for the location-based picker.

        Scrapes the city page for the supplied city slug and returns all
        station rows as (station_id, label) tuples sorted alphabetically
        by label.

        Args:
            session:    aiohttp ClientSession.
            city:       City slug string (e.g. 'sarajevo').  If not supplied,
                        the first city in _CITY_SLUGS is used as a fallback.
            lat:        Centre latitude (float, optional — used for future
                        distance filtering if the site exposes coordinates).
            lng:        Centre longitude (float, optional).
            radius_km:  Search radius in km (float, optional).

        Returns:
            List of ("{city}:{index}", "Name, Address (#city:idx)") tuples,
            or an empty list on any failure.

        Note:
            The site does not expose station coordinates, so radius filtering
            is not currently applied.  All stations from the city page are
            returned regardless of lat/lng/radius_km.
        """
        city_slug: str = str(kwargs.get("city", _CITY_SLUGS[0]))

        # is-not-None coord checks (not falsy — 0.0 is a valid coordinate)

        try:
            html = await self._fetch_city_html(session, city_slug)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "async_list_stations failed for city '%s': %s", city_slug, err
            )
            return []

        if html is None:
            _LOGGER.debug(
                "async_list_stations: no HTML returned for city '%s'", city_slug
            )
            return []

        stations = _parse_station_table(html)
        if not stations:
            return []

        result: list[tuple[str, str]] = []
        for idx, raw in enumerate(stations):
            sid = f"{city_slug}:{idx}"
            name: str = raw.get("name") or "Unknown"
            address: str = raw.get("address") or ""

            label = (
                f"{name}, {address} (#{sid[:8]})" if address else f"{name} (#{sid[:8]})"
            )
            result.append((sid, label))

        result.sort(key=lambda x: x[1])
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_city_html(
        self,
        session: ClientSession,
        city_slug: str,
    ) -> str | None:
        """Fetch the HTML for a cijenegoriva.ba city page.

        Args:
            session:    aiohttp ClientSession.
            city_slug:  City URL slug, e.g. 'sarajevo'.

        Returns:
            HTML text on success, None on any HTTP or network error.
        """
        url = f"{_BASE_URL}/{city_slug}"
        _LOGGER.debug("Fetching cijenegoriva.ba city page: %s", url)
        try:
            async with session.get(
                url,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                return await response.text()
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching cijenegoriva.ba city '%s': %s",
                city_slug,
                err,
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error fetching cijenegoriva.ba city '%s': %s",
                city_slug,
                err,
            )
            return None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_station_id(station_id: str) -> tuple[str, int]:
    """Parse a "{city_slug}:{row_index}" composite station ID.

    Args:
        station_id: Composite key e.g. 'sarajevo:3'.

    Returns:
        (city_slug, row_index) tuple.

    Raises:
        ProviderError: station_id is malformed or row_index is not an integer.
    """
    parts = station_id.split(":", 1)
    if len(parts) != 2:
        raise ProviderError(
            f"Invalid cijenegoriva.ba station ID '{station_id}'. "
            "Expected format: 'city_slug:row_index' (e.g. 'sarajevo:3')."
        )
    city_slug = parts[0].strip()
    if not city_slug:
        raise ProviderError(
            f"Invalid cijenegoriva.ba station ID '{station_id}': city slug is empty."
        )
    try:
        row_index = int(parts[1])
    except (ValueError, TypeError):
        raise ProviderError(
            f"Invalid cijenegoriva.ba station ID '{station_id}': "
            f"row index '{parts[1]}' is not an integer."
        )
    if row_index < 0:
        raise ProviderError(
            f"Invalid cijenegoriva.ba station ID '{station_id}': "
            "row index must be non-negative."
        )
    return city_slug, row_index


def _parse_price(raw: Any) -> float | None:
    """Parse a raw price value from a scraped HTML cell.

    Handles:
    - Numeric floats / ints
    - Strings like "2,750" (comma decimal), "2.750", "2.75 KM"
    - Returns None for None, zero, negative, or non-numeric values.

    Prices from cijenegoriva.ba are KM/L (local currency).
    Values are stored as-is (no EUR conversion).

    Args:
        raw: Raw cell value (str, float, int, or None).

    Returns:
        Float price in KM/L, or None.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        # Strip whitespace and currency symbols
        s = str(raw).strip()
        s = re.sub(r"[^\d.,]", "", s)  # keep only digits, dot, comma
        if not s:
            return None
        # Normalise comma decimal separator (e.g. "2,750" → "2.750")
        # Handle ambiguous "2,750" — if comma is followed by exactly 3 digits
        # at end of string it is a thousands separator; otherwise it's decimal.
        if "," in s and "." not in s:
            # e.g. "2,750" could be 2.750 or 2750 depending on locale
            # cijenegoriva.ba uses comma as decimal separator (e.g. "2,75")
            s = s.replace(",", ".")
        elif "," in s and "." in s:
            # Both: e.g. "1.234,56" (European thousand-sep + decimal)
            s = s.replace(".", "").replace(",", ".")
        try:
            val = float(s)
        except (ValueError, TypeError):
            return None

    if val <= 0:
        return None
    # Sanity guard: BiH fuel prices are in KM/L and typically 2–5 KM/L.
    # Values > 20 are probably a parsing artefact; divide by 100.
    if val > 20:
        val = val / 100.0
    if val > 6.0:
        return None
    return round(val, 3)


class _TableParser(HTMLParser):
    """Minimal HTML parser that extracts rows from the first <table> found.

    State machine:
      - Looks for the first <table> tag.
      - Within the table, collects <th> content for the header row.
      - Collects <td> content for each data row.
      - Stops after </table>.

    Result: self.headers (list[str]) and self.rows (list[list[str]]).
    """

    def __init__(self) -> None:
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[list[str]] = []

        self._in_table: bool = False
        self._in_header: bool = False
        self._in_row: bool = False
        self._in_cell: bool = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table" and not self._in_table:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_table and tag in ("th", "td"):
            self._in_cell = True
            self._current_cell = []
            if tag == "th":
                self._in_header = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._in_header or not self.headers:
                # First row or explicit header row
                pass  # headers are closed per-cell
            elif self._current_row:
                self.rows.append(self._current_row[:])
        elif tag in ("th", "td") and self._in_cell:
            self._in_cell = False
            cell_text = " ".join(self._current_cell).strip()
            if tag == "th":
                self.headers.append(cell_text)
                self._in_header = False
            else:
                self._current_row.append(cell_text)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)


def _parse_stations_div(html: str) -> list[dict[str, Any]]:
    """Parse cijenegoriva.ba Tailwind CSS card layout (modern site).

    The site uses div cards, each containing:
      - Station name in a span/div with class pattern like "font-bold" or "text-lg"
      - Fuel prices in span elements with data attributes or structured spans
      - Address in a span/div

    Returns same format as the table parser for compatibility.
    """
    stations: list[dict[str, Any]] = []

    # Match station card blocks — look for recurring structural patterns
    # cijenegoriva.ba cards are wrapped in div.flex or similar Tailwind containers
    # Each card typically contains the station name, address, and price spans.
    # Strategy: find price-containing blocks using fuel price patterns.

    # Simpler approach: find all text spans and group by proximity
    # Extract all text content between tags, filter by fuel price patterns
    text_blocks = re.findall(r">([^<]{2,200})<", html)

    prices_found: list[float] = []
    current_block: dict[str, Any] = {}
    price_count = 0

    for text in text_blocks:
        text = text.strip()
        if not text:
            continue

        # Check if it's a price (2-3 decimal digits: "2,75" or "1,234")
        price_match = re.match(r"^(\d+)[,\.](\d{2,3})$", text)
        if price_match:
            val_str = f"{price_match.group(1)}.{price_match.group(2).ljust(3, '0')}"
            try:
                val = float(val_str)
                if 0.3 <= val <= 5.0:
                    prices_found.append(val)
                    price_count += 1
            except ValueError:
                pass
        elif len(text) > 5 and not re.match(r"^[\d\s,\.]+$", text):
            # Likely a name or address
            if not current_block.get("name"):
                current_block["name"] = text
            elif not current_block.get("address"):
                current_block["address"] = text

        # Flush block when we have collected enough data
        if price_count >= 2 and current_block.get("name"):
            station: dict[str, Any] = {
                "name": current_block.get("name"),
                "address": current_block.get("address"),
                "diesel": prices_found[0] if len(prices_found) > 0 else None,
                "unleaded": prices_found[1] if len(prices_found) > 1 else None,
                "premium_unleaded": prices_found[2] if len(prices_found) > 2 else None,
                "lpg": prices_found[3] if len(prices_found) > 3 else None,
            }
            stations.append(station)
            prices_found = []
            current_block = {}
            price_count = 0

    return stations


def _parse_station_table(html: str) -> list[dict[str, Any]]:
    """Parse the cijenegoriva.ba station table from a city page HTML.

    Tries the modern div-card layout first; falls back to table-based parsing.

    Finds the first HTML table, reads the header row to identify column
    positions for name, address, and fuel prices, then converts each
    data row to a dict with canonical keys.

    Column header matching is case-insensitive and uses the _HEADER_TO_KEY
    mapping for fuel columns.  Name/address columns are detected by
    looking for common Bosnian/English labels ("naziv", "adresa", "name",
    "address").

    Args:
        html: Raw HTML text of a cijenegoriva.ba city page.

    Returns:
        List of station dicts, each with keys:
          name (str|None), address (str|None),
          diesel (float|None), petrol (float|None),
          premium_unleaded (float|None), lpg (float|None).
        Empty list if no table / no parseable data found.
    """
    # Use div-card parser when the modern Tailwind CSS layout is detected
    # (presence of id="item_N" divs); fall back to table parser otherwise.
    if re.search(r'id=["\']item_\d+["\']', html):
        div_result = _parse_stations_div(html)
        if div_result:
            _LOGGER.debug(
                "_parse_station_table: used div-card parser (%d stations)",
                len(div_result),
            )
            return div_result

    # Fall back to table-based parser
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("HTML parse error in _parse_station_table: %s", err)
        return []

    headers_lower = [h.lower().strip() for h in parser.headers]
    if not headers_lower:
        _LOGGER.debug("_parse_station_table: no table headers found in HTML")
        return []

    # Map column indices to semantic keys
    name_col: int | None = None
    address_col: int | None = None
    fuel_cols: dict[str, int] = {}  # StationData key → column index

    for idx, hdr in enumerate(headers_lower):
        # Name column detection
        if name_col is None and any(
            kw in hdr for kw in ("naziv", "name", "stanica", "pumpa")
        ):
            name_col = idx
            continue
        # Address column detection
        if address_col is None and any(
            kw in hdr for kw in ("adres", "address", "ulica", "lokacija")
        ):
            address_col = idx
            continue
        # Fuel column detection via _HEADER_TO_KEY
        for pattern, data_key in _HEADER_TO_KEY.items():
            if pattern in hdr:
                if data_key not in fuel_cols:
                    fuel_cols[data_key] = idx
                break

    if not fuel_cols and name_col is None:
        _LOGGER.debug(
            "_parse_station_table: could not identify any columns from headers: %s",
            parser.headers,
        )
        return []

    stations: list[dict[str, Any]] = []
    for row in parser.rows:

        def _cell(col: int | None) -> str | None:
            if col is None or col >= len(row):
                return None
            return row[col].strip() or None

        name_raw = _cell(name_col)
        address_raw = _cell(address_col)

        station: dict[str, Any] = {
            "name": name_raw,
            "address": address_raw,
            "diesel": _parse_price(_cell(fuel_cols.get("diesel"))),
            "unleaded": _parse_price(_cell(fuel_cols.get("unleaded"))),
            "premium_unleaded": _parse_price(_cell(fuel_cols.get("premium_unleaded"))),
            "lpg": _parse_price(_cell(fuel_cols.get("lpg"))),
        }
        stations.append(station)

    _LOGGER.debug(
        "_parse_station_table: parsed %d station rows from HTML", len(stations)
    )
    return stations


def _build_station_data(
    station_id: str,
    raw: dict[str, Any],
    city_slug: str,
) -> StationData:
    """Assemble a StationData dict from a parsed station row.

    Args:
        station_id: "{city_slug}:{row_index}" composite key.
        raw:        Parsed row dict from _parse_station_table.
        city_slug:  City slug used as the county/region value.

    Returns:
        Populated StationData dict.
    """
    name: str | None = raw.get("name") or None
    address: str | None = raw.get("address") or None

    # Use city slug as county (no explicit county/region field in source)
    county: str | None = city_slug.replace("-", " ").title() if city_slug else None

    data: StationData = {
        "diesel": raw.get("diesel"),
        "unleaded": raw.get("unleaded"),
        "premium_unleaded": raw.get("premium_unleaded"),
        "lpg": raw.get("lpg"),
        "name": name,
        "address": address,
        "county": county,
    }

    _LOGGER.debug(
        "cijenegoriva.ba parsed data for station %s: diesel=%s lpg=%s "
        "unleaded=%s premium_unleaded=%s",
        station_id,
        data.get("diesel"),
        data.get("lpg"),
        data.get("unleaded"),
        data.get("premium_unleaded"),
    )

    return data
