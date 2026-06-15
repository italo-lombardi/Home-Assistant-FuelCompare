"""NlAnwbProvider — Dutch national-average fuel prices via EU Weekly Oil Bulletin.

The Netherlands has no viable free station-level fuel-price API.  The ANWB
v1/v2/v3 REST APIs are defunct (404 as of 2026).  The ANWB Onderweg app uses
a private authenticated backend with no public key programme.  CBS
(Statistics Netherlands) open data has no consumer pump price datasets.  The
GitHub topic 'fuel-prices-netherlands' has zero public repositories.

National-average prices are available via the European Commission's Weekly
Oil Bulletin.  The EC publishes an XLSX workbook every Thursday at:
  https://energy.ec.europa.eu/document/download/264c2d0f-f161-4ea3-a777-78faae59bea0_en

The document UUID is stable; the EC updates the file in-place.  No
authentication is required.

Workbook layout
---------------
The single sheet has:
  Row 1 — date serial (Excel date of the bulletin week)
  Row 2 — units header (e.g. "Euro/1000 litres")
  Rows 3-29 — one row per EU member state

Columns (0-based):
  0 — Country name (string, e.g. "Netherlands")
  1 — Euro-super 95 (E10 / benzine) price per 1000 L with taxes
  2 — Automotive diesel price per 1000 L with taxes
  3 — Heating gas oil price per 1000 L with taxes
  6 — LPG (autogas) price per 1000 L with taxes

All price values are EUR/1000 L.  Divide by 1000 to obtain EUR/litre.
Values above 10 000 are treated as data errors (realistic ceiling is ~3 000).

Confirmed working as of 2026-06-08:
  Netherlands benzine EUR 2 255.94/1000 L → 2.256 EUR/L
  Netherlands diesel  EUR 2 150.59/1000 L → 2.151 EUR/L

Poll interval
-------------
POLL_INTERVAL_SECONDS = 86400 (24 h) — the bulletin is published once per
week.  Daily polling avoids re-downloading on non-publication days while
ensuring the new data is picked up within one day of Thursday publication.

CONFIG_MODE = 'location'
STATION_LOOKUP_MODE = 'location_search'

station_id is always "NL" (the country code) for this national-average
provider.  The coordinator calls async_fetch(session, "NL") on each poll.
"""

from __future__ import annotations

import asyncio
import functools
import io
import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

# ── API configuration ─────────────────────────────────────────────────────────

# Stable EC document UUID — updated in-place every Thursday by the Commission.
_BULLETIN_URL = (
    "https://energy.ec.europa.eu/document/download/"
    "264c2d0f-f161-4ea3-a777-78faae59bea0_en"
    "?filename=Weekly%20Oil%20Bulletin%20Weekly%20prices%20with%20Taxes%20"
    "-%202024-02-19.xlsx"
)

_TIMEOUT = ClientTimeout(total=max(API_TIMEOUT, 30))

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/octet-stream,*/*"
    ),
}

# The row label used for the Netherlands in the EC workbook
_NL_ROW_LABEL = "Netherlands"

# Country code used as station_id for this national-average provider
_STATION_ID = "NL"

# Price column indices (0-based) in the bulletin sheet.
# Row 0 = dates, Row 1 = units, Row 2+ = country rows.
# Column layout:
#   0  Country name
#   1  Euro-super 95 (E10 / benzine) EUR/1000 L with taxes
#   2  Automotive diesel EUR/1000 L with taxes
#   3  Heating gas oil EUR/1000 L with taxes
#   4  Heating gas oil weekly change (skip)
#   5  Fuel oil (low sulphur) EUR/1000 L (skip)
#   6  LPG (autogas) EUR/1000 L with taxes
_COL_COUNTRY = 0
_COL_BENZINE = 1
_COL_DIESEL = 2
_COL_LPG = 6
_COL_HEATING = 3

# Maximum plausible price per 1000 L in EUR.  Used to reject obvious data errors.
_MAX_PRICE_PER_1000L = 10_000.0


class NlAnwbProvider(BaseProvider):
    """Fetch Dutch national-average fuel prices via the EU Weekly Oil Bulletin.

    This provider downloads the EC Weekly Oil Bulletin XLSX workbook,
    locates the Netherlands row, and returns national-average prices for
    benzine (E10/Euro-super 95), diesel, LPG (autogas), and heating gas oil.

    There is no station-level data available without authentication.  The
    station_id is always 'NL'; a single virtual 'station' represents the
    national average.

    Usage
    -----
    provider = NlAnwbProvider(station_id="NL")
    data = await provider.async_fetch(session, "NL")
    """

    COUNTRY = "NL"
    PROVIDER_KEY = "nl_anwb"
    LABEL = "EU Oil Bulletin (Netherlands national average)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 86400  # daily; bulletin is published weekly (Thursdays)

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "unleaded",  # Euro-super 95 / benzine (E10 blend) EUR/litre
            "diesel",  # Automotive diesel EUR/litre
            "lpg",  # Autogas / LPG EUR/litre
            "kerosene",  # Heating gas oil EUR/litre (closest StationData key)
            # Identity / meta
            "name",
            "lastupdated",
        }
    )

    STATION_ID_HINT = (
        "This provider returns Dutch national-average prices from the EU "
        "Weekly Oil Bulletin.  No station ID is required; the station ID is "
        "always 'NL'."
    )

    def __init__(
        self,
        station_id: str = _STATION_ID,
        bulletin_url: str = _BULLETIN_URL,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:   Ignored; always treated as 'NL'.  Accepted for
                          BaseProvider compat.
            bulletin_url: URL of the EC Weekly Oil Bulletin XLSX.  Overridable
                          for tests.
        """
        self._station_id = _STATION_ID
        self._bulletin_url = bulletin_url

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Download the EC bulletin, parse the NL row, return StationData.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored; always fetches Netherlands national average.

        Returns:
            StationData with unleaded, diesel, lpg, kerosene in EUR/litre.

        Raises:
            ProviderError: Netherlands row not found in the workbook, or the
                           workbook cannot be parsed.
        """
        raw = await self._download_bulletin(session)
        return await _parse_bulletin(raw)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a display name for the config flow.

        For a national-average provider the name is fixed.  Returns the label
        without making an HTTP request so the config flow step is instant.

        Args:
            session:    aiohttp ClientSession (not used).
            station_id: Ignored.
        """
        return "Netherlands — National Average (EU Oil Bulletin)"

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return a single entry for the national-average virtual station.

        The config flow calls this method when STATION_LOOKUP_MODE is
        'location_search'.  Since this is a national-average provider there
        is only one station, represented by the country-code station_id 'NL'.

        Args:
            session: aiohttp ClientSession (not used for the list step).
            lat:     User latitude (accepted but not used for filtering).
            lng:     User longitude (accepted but not used for filtering).

        Returns:
            A list containing the single ('NL', label) tuple.  Returns []
            on any error so the config flow can fall back gracefully.
        """
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")

        # is-not-None checks (not falsy) so lat=0.0 / lng=0.0 are valid
        if lat is not None and lng is not None:
            _LOGGER.debug(
                "NlAnwbProvider.async_list_stations called with lat=%s lng=%s "
                "(national-average provider; coordinates not used for filtering)",
                lat,
                lng,
            )

        return [
            (
                _STATION_ID,
                "Netherlands — National Average (EU Weekly Oil Bulletin)",
            )
        ]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _download_bulletin(self, session: ClientSession) -> bytes:
        """Download the EC Weekly Oil Bulletin XLSX and return its raw bytes.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Raw XLSX bytes.

        Raises:
            ProviderError: HTTP error or connection failure.
        """
        _LOGGER.debug("Downloading EU Weekly Oil Bulletin from %s", self._bulletin_url)
        try:
            async with session.get(
                self._bulletin_url,
                headers=_HEADERS,
                timeout=_TIMEOUT,
                allow_redirects=True,
            ) as response:
                if response.status != 200:
                    raise ProviderError(
                        f"EU Weekly Oil Bulletin returned HTTP {response.status}.  "
                        "Check that the document UUID is still valid at "
                        "https://energy.ec.europa.eu/topics/oil-gas-and-coal/"
                        "weekly-oil-bulletin_en"
                    )
                return await response.read()
        except ProviderError:
            raise
        except ClientResponseError as err:
            raise ProviderError(
                f"HTTP error downloading EU Weekly Oil Bulletin: {err}"
            ) from err
        except Exception as err:  # noqa: BLE001
            raise ProviderError(
                f"Connection error downloading EU Weekly Oil Bulletin: {err}"
            ) from err


# ── Module-level helpers ──────────────────────────────────────────────────────


async def _parse_bulletin(raw: bytes) -> StationData:
    """Parse the EC Weekly Oil Bulletin XLSX and return NL StationData.

    Locates the Netherlands row (case-insensitive partial match on "Netherlands")
    and extracts EUR/1000 L prices for benzine (E10), diesel, LPG, and heating
    gas oil.  Divides by 1000 to convert to EUR/litre.

    Args:
        raw: Raw bytes of the XLSX workbook.

    Returns:
        StationData with unleaded, diesel, lpg, kerosene prices in EUR/litre.

    Raises:
        ProviderError: openpyxl is not installed, the workbook cannot be
                       parsed, or the Netherlands row is not found.
    """
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as err:
        raise ProviderError(
            "openpyxl is required to parse the EU Weekly Oil Bulletin XLSX.  "
            "Install it with: pip install openpyxl"
        ) from err

    try:
        wb = await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                openpyxl.load_workbook,
                io.BytesIO(raw),
                read_only=True,
                data_only=True,
            ),
        )
    except Exception as err:  # noqa: BLE001
        raise ProviderError(
            f"Failed to open EU Weekly Oil Bulletin as XLSX workbook: {err}"
        ) from err

    try:
        ws = wb.active
        if ws is None:
            raise ProviderError("EU Weekly Oil Bulletin workbook has no active sheet.")

        nl_row: tuple | None = None
        bulletin_date: str | None = None

        for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
            if row_idx == 0:
                # Row 1: date is in column A (index 0), not column B (index 1)
                date_val = row[0] if len(row) > 0 else None
                if date_val is not None:
                    from datetime import datetime as _datetime  # noqa: PLC0415

                    if isinstance(date_val, _datetime):
                        bulletin_date = date_val.date().isoformat()
                    else:
                        bulletin_date = str(date_val)
                continue
            if row_idx == 1:
                # Row 2: units header — skip
                continue

            # Data rows start at row index 2 (row 3 in the spreadsheet)
            country_cell = row[_COL_COUNTRY] if len(row) > _COL_COUNTRY else None
            if country_cell is None:
                continue
            country_str = str(country_cell).strip()
            if "netherlands" in country_str.lower():
                nl_row = row
                break
    finally:
        wb.close()

    if nl_row is None:
        raise ProviderError(
            "Netherlands row not found in EU Weekly Oil Bulletin.  "
            "The workbook layout may have changed.  "
            "Please open an issue at "
            "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues"
        )

    e10 = _extract_price(nl_row, _COL_BENZINE)
    diesel = _extract_price(nl_row, _COL_DIESEL)
    lpg = _extract_price(nl_row, _COL_LPG)
    heating = _extract_price(nl_row, _COL_HEATING)

    _LOGGER.debug(
        "EU Oil Bulletin NL: unleaded=%.3f diesel=%.3f lpg=%s heating=%s date=%s",
        e10 if e10 is not None else 0.0,
        diesel if diesel is not None else 0.0,
        f"{lpg:.3f}" if lpg is not None else "None",
        f"{heating:.3f}" if heating is not None else "None",
        bulletin_date,
    )

    data: StationData = {
        "unleaded": e10,
        "diesel": diesel,
        "lpg": lpg,
        "kerosene": heating,
        "name": "Netherlands — National Average",
        "lastupdated": bulletin_date,
        "source_station_id": _STATION_ID,
    }
    return data


def _extract_price(row: tuple, col_idx: int) -> float | None:
    """Extract and validate a EUR/1000 L price from a bulletin row.

    Divides by 1000 to obtain EUR/litre.  Returns None for missing, zero,
    negative, or implausibly large values.

    Args:
        row:     Tuple of cell values from one workbook row.
        col_idx: 0-based column index of the price cell.

    Returns:
        EUR/litre float rounded to 4 decimal places, or None.
    """
    if col_idx >= len(row):
        return None
    raw = row[col_idx]
    if raw is None:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > _MAX_PRICE_PER_1000L:
        return None
    return round(val / 1000.0, 4)
