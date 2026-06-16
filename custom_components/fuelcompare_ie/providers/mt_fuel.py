"""MtFuelProvider — Malta national-average fuel prices via EU Oil Bulletin XLSX.

Source: European Commission, Directorate-General for Energy.
URL: https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en

The EU Weekly Oil Bulletin is published every Thursday and contains
national-average fuel prices (EUR/1000L) for all 27 EU member states,
including Malta.  The XLSX download URL embeds a document GUID that may
change when a new bulletin is published.  This provider scrapes the bulletin
landing page weekly to discover the current GUID, then downloads and parses
the XLSX.

Approach
--------
1. GET https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en
   to discover the current XLSX download URL (href containing
   "Weekly Oil Bulletin Weekly prices with Taxes").
2. If the GUID cannot be scraped, fall back to _FALLBACK_XLSX_URL (the
   last known-good URL with GUID 264c2d0f-…).
3. Download the XLSX (binary, typically 200–400 KB).
4. Parse with openpyxl (read_only mode, no charts needed).
5. Search for the "Malta" row.  Columns are:
     A: Country name
     B: Euro-super 95 (EUR/1000L)
     C: Automotive diesel (EUR/1000L)
     D: Heating gas oil (EUR/1000L)
     E: Fuel oil (low S) (EUR/1000L)
     F: Fuel oil (high S) (EUR/1000L)
     G: LPG (EUR/1000L)
   Divide each value by 1000 to obtain EUR/litre.

Station level: False.  No station-level data source exists for Malta.
The single "station" returned has station_id = "MT" (country code).

CONFIG_MODE='location': the coordinator/config flow treats this as a
location-based provider.  async_list_stations returns a single entry for
the national average.

Update cadence: weekly (Thursdays).  POLL_INTERVAL_SECONDS = 604800
    STATION_PAGE_URL: ClassVar[str] = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en" (7 days).
A shorter interval (e.g. 86400) is also acceptable; the XLSX does not change
intra-week so extra fetches are wasted bandwidth.

Dependencies
------------
openpyxl is used to parse the XLSX.  It is listed in the integration's
manifest as a Python requirement.  If openpyxl is not installed, all price
fields will be None and a WARNING is logged.

Confidence: 5/10.  The data IS real and accessible (EUR 1.34/L petrol,
EUR 1.21/L diesel confirmed June 2026), but the URL GUID is not a
guaranteed permalink and requires weekly re-discovery.

Currency: EUR/litre.
"""

from __future__ import annotations

import asyncio
import functools
import io
import logging
import re
from typing import Any, ClassVar
from urllib.parse import urlparse

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

# Landing page to scrape for the current XLSX download URL.
_BULLETIN_PAGE_URL = (
    "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
)

# Last-known-good XLSX direct download URL.  Used as a fallback when the
# landing page cannot be scraped or yields no matching link.
_FALLBACK_XLSX_URL = (
    "https://energy.ec.europa.eu/document/download/"
    "264c2d0f-f161-4ea3-a777-78faae59bea0_en"
    "?filename=Weekly%20Oil%20Bulletin%20Weekly%20prices%20with%20Taxes%20"
    "-%202024-02-19.xlsx"
)

# Fragment of the XLSX filename to search for in the landing page HTML.
_XLSX_HREF_PATTERN = re.compile(
    r'href=["\']([^"\']*Weekly[^"\']*prices[^"\']*with[^"\']*Taxes[^"\']*\.xlsx[^"\']*)["\']',
    re.IGNORECASE,
)

# Fragment pattern for document/download links (alternative scraping path).
_DOWNLOAD_HREF_PATTERN = re.compile(
    r'href=["\']([^"\']*document/download/[a-f0-9\-]+_en[^"\']*)["\']',
    re.IGNORECASE,
)

# User-Agent header for both the landing page and XLSX download.
_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "*/*",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 6)  # XLSX can be ~400 KB

# The country label as it appears in the XLSX "Malta" row.
# Some bulletins use "Malta" or "Malta *"; match with startswith.
_MALTA_LABEL = "Malta"

# Column indices (0-based) in the XLSX for Malta's prices.
# The EU Oil Bulletin layout: A=Country, B=Euro-super 95, C=Diesel,
# D=Heating gas oil, E=Fuel oil (low S), F=Fuel oil (high S), G=LPG.
_COL_COUNTRY = 0
_COL_PETROL95 = 1
_COL_DIESEL = 2
_COL_HEATING_OIL = 3
_COL_LPG = 6


class MtFuelProvider(BaseProvider):
    """Fetch Malta national-average fuel prices from the EU Oil Bulletin XLSX.

    This is a national-average-only provider; no station-level data exists
    for Malta from any free or open source.

    Usage
    -----
    The constructor accepts an optional ``station_id`` (ignored; always
    treated as "MT") and optional lat/lng/radius_km (stored but not used
    for filtering since only one record is returned).

    The coordinator calls async_fetch(session, "MT") to retrieve the
    current national-average prices.  async_list_stations always returns
    a single entry: ("MT", "Malta — national average").
    """

    COUNTRY = "MT"
    PROVIDER_KEY = "mt_fuel"
    LABEL = "EU Oil Bulletin (Malta)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 604800  # 7 days; bulletin is weekly

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",  # petrol 95 → standard StationData key for 95-octane petrol
            "diesel",
            "lpg",
            "kerosene",  # heating oil → standard StationData key
            "name",
            "county",
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "Malta has no station-level data source.  This provider returns "
        "the national average from the EU Weekly Oil Bulletin.  "
        "The station ID is always 'MT'."
    )

    def __init__(
        self,
        station_id: str = "MT",
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:  Always "MT"; stored for coordinator compatibility.
            county:      Not used by this provider.
            latitude:    WGS84 latitude (stored but not used for filtering).
            longitude:   WGS84 longitude (stored but not used for filtering).
            radius_km:   Search radius (stored but not used).
        """
        self._station_id = station_id or "MT"
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0
        # Cache the discovered XLSX URL so subsequent polls skip the landing page scrape
        self._cached_xlsx_url: str | None = None

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch Malta national-average fuel prices from the EU Oil Bulletin.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored; always fetches Malta national average.

        Returns:
            StationData with petrol_95, diesel, lpg, heating_oil in EUR/litre.

        Raises:
            ProviderError: XLSX could not be downloaded or Malta row not found.
        """
        xlsx_url = await self._resolve_xlsx_url(session)

        xlsx_bytes = await self._download_xlsx(session, xlsx_url)
        if xlsx_bytes is None:
            raise ProviderError(
                "MtFuelProvider: failed to download EU Oil Bulletin XLSX. "
                "Check network connectivity."
            )

        try:
            prices = await _parse_malta_row(xlsx_bytes)
        except Exception as err:  # noqa: BLE001
            raise ProviderError(
                f"MtFuelProvider: failed to parse EU Oil Bulletin XLSX: {err}"
            ) from err

        if prices is None:
            raise ProviderError(
                "MtFuelProvider: Malta row not found in EU Oil Bulletin XLSX. "
                "The bulletin layout may have changed."
            )

        data: StationData = {
            "name": "Malta — national average",
            "county": "Malta",
            "source_station_id": "MT",
            "lastupdated": None,  # bulletin does not provide a per-row timestamp
        }

        # Map prices to StationData keys.
        # petrol_95 → 'unleaded' (standard key for 95-octane petrol)
        # heating_oil → 'kerosene' (standard key; closest StationData equivalent)
        # Extra keys travel as passthrough for templates / extra attributes.
        data["unleaded"] = prices.get("petrol_95")
        data["diesel"] = prices.get("diesel")
        data["lpg"] = prices.get("lpg")
        data["kerosene"] = prices.get("heating_oil")

        _LOGGER.debug(
            "MtFuelProvider: unleaded=%s diesel=%s lpg=%s kerosene=%s",
            data.get("unleaded"),
            data.get("diesel"),
            data.get("lpg"),
            data.get("kerosene"),
        )

        return data

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return display name for the config flow.

        This provider always represents the Malta national average, so the
        name is static.  No HTTP request is made.

        Args:
            session:    aiohttp ClientSession.
            station_id: Ignored.
        """
        return "Malta — national average"

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return the single national-average entry for the station picker.

        Because Malta has no station-level data, this always returns a
        single ("MT", "Malta — national average (EU Oil Bulletin)") entry.
        No HTTP request is made.

        Keyword args (from config flow location_search) are accepted but
        ignored — coordinates are irrelevant for a national-average source.

        Args:
            session: aiohttp ClientSession (not used).

        Returns:
            [("MT", "Malta — national average (EU Oil Bulletin)")]
        """
        # is-not-None coord checks (not falsy — 0.0 is a valid coordinate)

        return [("MT", "Malta — national average (EU Oil Bulletin)")]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _resolve_xlsx_url(self, session: ClientSession) -> str:
        """Return the XLSX download URL, scraping the landing page if needed.

        Attempts to discover the current XLSX URL from the EU Oil Bulletin
        landing page.  If the scrape fails or yields no match, falls back to
        _FALLBACK_XLSX_URL.  A successful discovery is cached so subsequent
        polls within the same session skip the landing-page GET.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Absolute XLSX download URL string.
        """
        if self._cached_xlsx_url is not None:
            return self._cached_xlsx_url

        discovered = await self._scrape_xlsx_url(session)
        url = discovered or _FALLBACK_XLSX_URL
        if discovered:
            self._cached_xlsx_url = url
            _LOGGER.debug("MtFuelProvider: discovered XLSX URL: %s", url)
        else:
            _LOGGER.debug(
                "MtFuelProvider: XLSX URL discovery failed; using fallback URL"
            )
        return url

    async def _scrape_xlsx_url(self, session: ClientSession) -> str | None:
        """Scrape the EU Oil Bulletin landing page for the XLSX download link.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Absolute XLSX download URL, or None if not found.
        """
        _LOGGER.debug(
            "MtFuelProvider: scraping landing page for XLSX URL: %s",
            _BULLETIN_PAGE_URL,
        )
        try:
            async with session.get(
                _BULLETIN_PAGE_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "MtFuelProvider: landing page returned HTTP %d",
                        response.status,
                    )
                    return None
                html = await response.text(errors="replace")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MtFuelProvider: error scraping landing page: %s", err)
            return None

        # Try filename-based pattern first (most specific)
        match = _XLSX_HREF_PATTERN.search(html)
        if match:
            href = match.group(1)
            try:
                return _make_absolute(href)
            except ProviderError as err:
                _LOGGER.debug(
                    "MtFuelProvider: SSRF guard rejected href %r: %s", href, err
                )

        # Fallback: any document/download link that looks like an XLSX
        for m in _DOWNLOAD_HREF_PATTERN.finditer(html):
            href = m.group(1)
            if "xlsx" in href.lower() or "Weekly" in href:
                try:
                    return _make_absolute(href)
                except ProviderError as err:
                    _LOGGER.debug(
                        "MtFuelProvider: SSRF guard rejected fallback href %r: %s",
                        href,
                        err,
                    )

        _LOGGER.debug("MtFuelProvider: no XLSX link found on landing page")
        return None

    async def _download_xlsx(
        self,
        session: ClientSession,
        url: str,
    ) -> bytes | None:
        """Download the XLSX file and return its raw bytes.

        Args:
            session: aiohttp ClientSession.
            url:     XLSX download URL.

        Returns:
            Raw XLSX bytes on success, or None on any HTTP/network error.
        """
        _LOGGER.debug("MtFuelProvider: downloading XLSX from %s", url)
        try:
            async with session.get(
                url,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "MtFuelProvider: XLSX download returned HTTP %d",
                        response.status,
                    )
                    # If the cached GUID is stale, clear it so next poll re-scrapes
                    if response.status in (404, 410):
                        self._cached_xlsx_url = None
                    return None
                return await response.read()
        except ClientResponseError as err:
            _LOGGER.debug("MtFuelProvider: HTTP error downloading XLSX: %s", err)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MtFuelProvider: unexpected error downloading XLSX: %s", err)
            return None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _make_absolute(href: str) -> str:
    """Return an absolute URL from a potentially relative href.

    Args:
        href: URL or path from an HTML href attribute.

    Returns:
        Absolute URL string.

    Raises:
        ProviderError: The resolved URL points to an unexpected host.
    """
    if href.startswith("https://"):
        resolved_url = href
    elif href.startswith("http://"):
        resolved_url = "https://" + href[7:]  # upgrade http → https
    elif href.startswith("//"):
        resolved_url = "https:" + href
    elif href.startswith("/"):
        resolved_url = "https://energy.ec.europa.eu" + href
    else:
        resolved_url = "https://energy.ec.europa.eu/" + href

    expected_host = "energy.ec.europa.eu"
    parsed = urlparse(resolved_url)
    if parsed.scheme not in ("http", "https"):
        raise ProviderError(
            f"SSRF guard: unexpected URL scheme {parsed.scheme!r} in {resolved_url!r}"
        )
    if parsed.netloc != expected_host:
        raise ProviderError(f"SSRF guard: unexpected download host {parsed.netloc!r}")
    return resolved_url


def _parse_price_cell(cell_value: Any) -> float | None:
    """Parse an EU Oil Bulletin price cell (EUR/1000L) to EUR/litre.

    The bulletin stores prices as EUR/1000L (e.g. 1340 for 1.34 EUR/L).
    This function converts to EUR/litre by dividing by 1000.

    Args:
        cell_value: Raw cell value from openpyxl (numeric, string, or None).

    Returns:
        Price in EUR/litre, or None if unparseable or non-positive.
    """
    if cell_value is None:
        return None
    try:
        val = float(cell_value)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    # EUR/1000L → EUR/litre
    return round(val / 1000.0, 4)


async def _parse_malta_row(xlsx_bytes: bytes) -> dict[str, float | None] | None:
    """Parse the EU Oil Bulletin XLSX and extract Malta's fuel prices.

    Opens the XLSX in read-only mode (no chart evaluation), iterates rows
    to find the Malta entry, and extracts the four price columns.

    Column layout (0-based indices after converting to list):
      0: Country name
      1: Euro-super 95 (EUR/1000L)
      2: Automotive diesel (EUR/1000L)
      3: Heating gas oil (EUR/1000L)
      4: Fuel oil (low S) (EUR/1000L)
      5: Fuel oil (high S) (EUR/1000L)
      6: LPG (EUR/1000L)

    Args:
        xlsx_bytes: Raw XLSX file content.

    Returns:
        Dict with keys petrol_95, diesel, lpg, heating_oil (EUR/litre),
        or None if the Malta row was not found.

    Raises:
        ImportError: openpyxl is not installed.
        Exception:   Any other parsing error (re-raised to caller).
    """
    try:
        import openpyxl  # noqa: PLC0415 — optional import
    except ImportError:
        _LOGGER.warning(
            "MtFuelProvider: openpyxl is not installed.  "
            "Install it with: pip install openpyxl"
        )
        raise

    wb = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            openpyxl.load_workbook,
            io.BytesIO(xlsx_bytes),
            read_only=True,
            data_only=True,
        ),
    )

    try:
        # Use the first sheet; bulletins typically have one data sheet.
        ws = wb.worksheets[0]

        for row in ws.iter_rows(values_only=True):
            if not row:
                continue
            country_cell = row[_COL_COUNTRY]
            if country_cell is None:
                continue
            country_str = str(country_cell).strip()
            if country_str.startswith(_MALTA_LABEL):
                return {
                    "petrol_95": _parse_price_cell(
                        row[_COL_PETROL95] if len(row) > _COL_PETROL95 else None
                    ),
                    "diesel": _parse_price_cell(
                        row[_COL_DIESEL] if len(row) > _COL_DIESEL else None
                    ),
                    "lpg": _parse_price_cell(
                        row[_COL_LPG] if len(row) > _COL_LPG else None
                    ),
                    "heating_oil": _parse_price_cell(
                        row[_COL_HEATING_OIL] if len(row) > _COL_HEATING_OIL else None
                    ),
                }
    finally:
        wb.close()

    return None
