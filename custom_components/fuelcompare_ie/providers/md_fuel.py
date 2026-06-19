"""MdFuelProvider — Moldovan national maximum reference fuel prices (ANRE).

Source: Agenția Națională pentru Reglementare în Energetică (ANRE), Moldova.
ANRE sets and publishes a daily maximum reference price for Benzina 95 and
Motorina (diesel).  These are regulatory caps, not actual pump prices.

Endpoints (one per fuel type, HTML pages, no auth required):
  Benzina 95: https://anre.md/benzina-95-3-2
  Motorina:   https://anre.md/motorina-3-3

Both endpoints return HTTP 200 HTML with no authentication.  Prices are
embedded in ``<td class="pl_price">`` elements as ``data-price`` attributes,
e.g. ``<td class="pl_price" data-price="28.71">``.  A BeautifulSoup scraper
targeting ``td.pl_price[data-price]`` reliably extracts the current price.

Coverage
--------
- Country:        MD (Moldova)
- Station level:  No — national maximum reference prices only
- Fuel types:     benzina_95 → stored as ``unleaded``, motorina → stored as
                  ``diesel``
- Currency:       MDL (Moldovan Leu) per litre
- Prices as of 2026-06-13/14: Benzina 95 = 28.71 MDL/L, Motorina = 27.16 MDL/L

Because this provider returns a single national reference price (not per-
station data), CONFIG_MODE is 'location' and station_id is the country code
'MD'.  There are no coordinates, no station name, and no address.

Scraping approach
-----------------
An existing Ruby scraper (github.com/dragomirt/anre_prices_bot, last updated
2022) confirms the ``td.pl_price[data-price]`` approach with Nokogiri.  No
formal JSON API exists.  The historical price series is also present in
JavaScript chart arrays (``[unix_ms, price]``) on the same pages, but only
the current price (the first ``data-price`` value) is used here.

Confidence: 6/10 — data is scrapeable and clearly structured, but relies on
HTML scraping with no formal API stability guarantee.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

# ── Endpoint URLs ─────────────────────────────────────────────────────────────

_URL_BENZINA_95 = "https://anre.md/benzina-95-3-2"
_URL_MOTORINA = "https://anre.md/motorina-3-3"

# ── HTTP settings ─────────────────────────────────────────────────────────────

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "text/html,application/xhtml+xml",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# ── CSS selector ──────────────────────────────────────────────────────────────

# The current price is in the first <td class="pl_price"> element that
# carries a data-price attribute.  The value is a decimal string in MDL/litre.
_PRICE_SELECTOR = "td.pl_price[data-price]"

# ── Station identity for the single national reference entry ──────────────────

# Because there are no individual stations, station_id is fixed to the
# country code.  The coordinator uses this as the entity key.
_NATIONAL_STATION_ID = "MD"


class MdFuelProvider(BaseProvider):
    """Fetch Moldovan national maximum reference fuel prices from ANRE.

    ANRE (Agenția Națională pentru Reglementare în Energetică) publishes a
    daily maximum reference price for Benzina 95 and Motorina.  These are
    regulatory price caps — not actual pump prices and not station-level data.

    Because there is only one national reference entry, CONFIG_MODE is
    'location' and station_id is fixed to 'MD'.  The user does not choose a
    station; the integration tracks the country-level reference prices.

    Polling interval: 86400 s (24 hours) — ANRE typically publishes one
    update per day.  Halved to 43200 s (12 hours) to catch mid-day revisions.
    """

    COUNTRY = "MD"
    PROVIDER_KEY = "md_fuel"
    DISABLED = (
        True  # 0.7.0: upstream failing in live verification — disable until fixed
    )
    LABEL = "ANRE (Moldova)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 43200
    STATION_PAGE_URL: ClassVar[str] = (
        "https://anre.md"  # 12 hours — ANRE updates at most once daily
    )
    CURRENCY: ClassVar[str] = "MDL"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "unleaded",  # Benzina 95 — MDL/litre
            "diesel",  # Motorina  — MDL/litre
            "lastupdated",
            "name",
        }
    )

    STATION_ID_HINT = (
        "Moldova ANRE publishes national maximum reference prices only.  "
        "No station-level data is available.  The station ID is fixed to 'MD'."
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
            station_id:  Always 'MD' for this provider.  Accepted as a
                         parameter for BaseProvider compatibility.
            latitude:    Not used — no station-level data.  Accepted for
                         CONFIG_MODE='location' interface compatibility.
            longitude:   Not used — see latitude.
            radius_km:   Not used — see latitude.
        """
        self._station_id = station_id or _NATIONAL_STATION_ID
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return the ANRE national reference prices.

        Scrapes both ANRE pages concurrently and returns a StationData dict
        with the current Benzina 95 price as ``unleaded`` and the Motorina
        price as ``diesel``, both in MDL/litre.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored — always fetches the national reference prices.

        Returns:
            StationData dict with unleaded and diesel prices in MDL/litre.

        Raises:
            ProviderError: When both pages fail to return a valid price.
        """
        benzina_task = self._fetch_price(session, _URL_BENZINA_95, "benzina_95")
        motorina_task = self._fetch_price(session, _URL_MOTORINA, "motorina")

        benzina_price, motorina_price = await asyncio.gather(
            benzina_task, motorina_task
        )

        if benzina_price is None and motorina_price is None:
            raise ProviderError(
                "ANRE Moldova: failed to retrieve any fuel price from both "
                f"{_URL_BENZINA_95} and {_URL_MOTORINA}.  "
                "The ANRE website may have changed its HTML structure."
            )

        _LOGGER.debug(
            "ANRE Moldova parsed prices: benzina_95=%s MDL/L motorina=%s MDL/L",
            benzina_price,
            motorina_price,
        )

        return {
            "unleaded": benzina_price,  # Benzina 95 in MDL/litre
            "diesel": motorina_price,  # Motorina in MDL/litre
            "name": "ANRE Moldova — National Reference Price",
            "lastupdated": None,  # ANRE does not expose a last-updated timestamp
        }

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a display name for the config flow.

        For CONFIG_MODE='location' providers the config flow generates a
        location-based title; returning None is correct here.

        Args:
            session:    aiohttp ClientSession.
            station_id: Ignored.

        Returns:
            None — the config flow uses the auto-generated country title.
        """
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return the single national reference entry for the station picker.

        ANRE Moldova provides only country-level data.  This method returns a
        single entry so the config flow station picker has something to show.

        The ``lat`` / ``lng`` / ``radius_km`` kwargs are accepted for interface
        compatibility but are not used — Moldova is a small country and a
        single national reference is always returned.

        Args:
            session:  aiohttp ClientSession.
            **kwargs: lat, lng, radius_km — accepted but ignored.

        Returns:
            A single-element list: [('MD', 'ANRE Moldova — National Reference Price')].
        """
        lat = kwargs.get("lat")
        lng = kwargs.get("lng")

        if lat is not None and lng is not None:
            # Coordinates supplied — still return the single national entry
            # (there are no individual stations to filter by distance)
            _LOGGER.debug(
                "MdFuelProvider.async_list_stations called with lat=%s lng=%s "
                "(coordinates ignored — national data only)",
                lat,
                lng,
            )

        try:
            benzina_price = await self._fetch_price(
                session, _URL_BENZINA_95, "benzina_95"
            )
            motorina_price = await self._fetch_price(session, _URL_MOTORINA, "motorina")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed to fetch ANRE prices: %s", err)
            benzina_price = None
            motorina_price = None

        price_parts: list[str] = []
        if benzina_price is not None:
            price_parts.append(f"Benzina 95: {benzina_price:.2f} MDL/L")
        if motorina_price is not None:
            price_parts.append(f"Motorina: {motorina_price:.2f} MDL/L")

        if price_parts:
            label = f"ANRE Moldova — {' / '.join(price_parts)}"
        else:
            label = "ANRE Moldova — National Reference Price"

        return [(_NATIONAL_STATION_ID, label)]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_price(
        self,
        session: ClientSession,
        url: str,
        fuel_label: str,
    ) -> float | None:
        """Fetch one ANRE page and extract the current reference price.

        Targets the first ``<td class="pl_price" data-price="...">`` element.
        Returns None on any HTTP error, parse error, or missing element so
        the coordinator's stale-retention behaviour works correctly.

        Args:
            session:    aiohttp ClientSession.
            url:        ANRE page URL.
            fuel_label: Human-readable fuel name for log messages.

        Returns:
            Price in MDL/litre as a float, or None on failure.
        """
        _LOGGER.debug("Fetching ANRE Moldova price page: %s (%s)", url, fuel_label)
        try:
            async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                html = await resp.text(encoding="utf-8", errors="replace")
        except ClientResponseError as err:
            _LOGGER.debug(
                "HTTP error fetching ANRE Moldova %s (%s): %s",
                url,
                fuel_label,
                err,
            )
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error fetching ANRE Moldova %s (%s): %s",
                url,
                fuel_label,
                err,
            )
            return None

        return _extract_price_from_html(html, fuel_label)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _extract_price_from_html(html: str, fuel_label: str = "") -> float | None:
    """Extract the current reference price from an ANRE page HTML string.

    Targets the first ``<td class="pl_price" data-price="...">`` element.
    Falls back to scanning for the ``data-price`` attribute pattern in raw
    HTML if BeautifulSoup is unavailable.

    Args:
        html:       Decoded HTML text from the ANRE page.
        fuel_label: Human-readable fuel name for log messages.

    Returns:
        Price in MDL/litre as a float, or None if not found or unparseable.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]

        soup = BeautifulSoup(html, "html.parser")
        tag = soup.select_one("td.pl_price[data-price]")
        if tag is None:
            _LOGGER.debug(
                "ANRE Moldova: no 'td.pl_price[data-price]' element found for %s",
                fuel_label,
            )
            return None
        raw = tag.get("data-price")
        if raw is None:
            return None
        return _parse_price(str(raw), fuel_label)

    except ImportError:
        # BeautifulSoup not installed — fall back to a simple regex scan
        _LOGGER.debug(
            "BeautifulSoup not available; falling back to regex for ANRE Moldova %s",
            fuel_label,
        )
        import re

        match = re.search(r'data-price="([0-9]+(?:\.[0-9]+)?)"', html)
        if match:
            return _parse_price(match.group(1), fuel_label)
        _LOGGER.debug(
            "ANRE Moldova: regex found no data-price attribute for %s", fuel_label
        )
        return None

    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "ANRE Moldova: unexpected parse error for %s: %s", fuel_label, err
        )
        return None


def _parse_price(raw: str, fuel_label: str = "") -> float | None:
    """Convert a raw price string to a validated float in MDL/litre.

    Args:
        raw:        String extracted from the ``data-price`` attribute
                    (e.g. ``"28.71"``).
        fuel_label: Human-readable fuel name for log messages.

    Returns:
        Price in MDL/litre as a float rounded to 3 decimal places,
        or None if the value is non-numeric or non-positive.
    """
    raw = raw.strip()
    try:
        value = float(raw)
    except (ValueError, TypeError):
        _LOGGER.debug(
            "ANRE Moldova: could not parse price string '%s' for %s",
            raw,
            fuel_label,
        )
        return None
    if value <= 0:
        _LOGGER.debug(
            "ANRE Moldova: ignoring non-positive price %s for %s", value, fuel_label
        )
        return None
    return round(value, 3)
