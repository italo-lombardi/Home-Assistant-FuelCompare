"""Extracts buildId and AES decrypt key from the fuelcompare.ie station HTML.

The two values live in different parts of the page:

- ``buildId`` is embedded in the inline ``__NEXT_DATA__`` JSON; it powers the
  Next.js static-JSON fetch path and rotates on every site deploy.
- The AES decrypt key is hardcoded inside one of the chunked JS bundles
  referenced from the HTML. Across deploys it has lived in two locations:
  the per-page ``/_next/static/chunks/pages/station/[id]-*.js`` chunk
  (legacy), and a shared vendor chunk such as ``/_next/static/chunks/1890-*.js``
  (current). Both locations use the same ``AES.decrypt(e,"<64-hex>")`` call
  pattern, so a single regex finds the key once you fetch the right chunk.

The ``PageAssets`` class wraps both extractions and exposes two modes:

1. ``refresh()`` — legacy single-chunk lookup. Cheapest path; covers every
   deploy from initial integration release through 0.5.2.
2. ``refresh(broad=True)`` — falls back to scanning every chunk URL listed
   in the HTML. Used as the inner fallback when the legacy lookup yields
   no key (current deploys with relocated keys).

State (build_id, decrypt_key) is held on the instance and updated in place.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Final

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import API_TIMEOUT, BASE_URL

_LOGGER = logging.getLogger(__name__)

_TIMEOUT: Final = ClientTimeout(total=API_TIMEOUT)
_HEADERS: Final = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

_BUILD_ID_RE: Final = re.compile(r'"buildId":"([^"]+)"')
_STATION_CHUNK_RE: Final = re.compile(r'(/_next/static/chunks/pages/station/[^"]+\.js)')
_ANY_CHUNK_FINDALL_RE: Final = re.compile(r'/_next/static/chunks/[^"]+\.js')
_AES_KEY_RE: Final = re.compile(r'AES\.decrypt\(\w+,"([a-fA-F0-9]{64})"')


class PageAssets:
    """Holds buildId + AES decrypt key for one station and refreshes them on demand."""

    def __init__(self, station_id: str) -> None:
        """Initialize empty asset state for the given station ID."""
        self.station_id = station_id
        self.build_id: str | None = None
        self.decrypt_key: str | None = None

    async def refresh(self, session: ClientSession, broad: bool = False) -> None:
        """Re-fetch the station HTML and update build_id (and decrypt_key when found).

        ``broad=False`` (default) preserves the original integration behavior:
        only the per-page station chunk is searched for the AES key. ``broad=True``
        scans every chunk listed in the HTML — used as the inner fallback when
        the standard search has already failed.
        """
        html = await self._fetch_html(session)
        self._extract_build_id(html)
        if broad:
            await self._extract_key_broad(session, html)
        else:
            await self._extract_key_station_chunk(session, html)

    async def _fetch_html(self, session: ClientSession) -> str:
        """Fetch and return the station page HTML; raises for non-2xx responses."""
        url = f"{BASE_URL}/station/{self.station_id}"
        _LOGGER.debug("Fetching page assets from %s", url)
        async with session.get(url, timeout=_TIMEOUT, headers=_HEADERS) as response:
            response.raise_for_status()
            return await response.text()

    def _extract_build_id(self, html: str) -> None:
        """Set ``self.build_id`` from the HTML; raise UpdateFailed if missing."""
        match = _BUILD_ID_RE.search(html)
        if not match:
            _LOGGER.debug(
                "buildId pattern not found in HTML for station %s", self.station_id
            )
            raise UpdateFailed("buildId not found in page")
        self.build_id = match.group(1)
        _LOGGER.debug(
            "Extracted buildId for station %s: %s", self.station_id, self.build_id
        )

    async def _extract_key_station_chunk(
        self, session: ClientSession, html: str
    ) -> None:
        """Look for the AES key in the per-page station chunk only (legacy)."""
        chunk_match = _STATION_CHUNK_RE.search(html)
        if not chunk_match:
            _LOGGER.debug(
                "Station JS chunk URL not found in HTML for station %s — decrypt key not refreshed",
                self.station_id,
            )
            return

        chunk_url = BASE_URL + chunk_match.group(1)
        _LOGGER.debug(
            "Fetching station JS chunk for decrypt key (station %s): %s",
            self.station_id,
            chunk_url,
        )
        try:
            async with session.get(
                chunk_url, timeout=_TIMEOUT, headers=_HEADERS
            ) as response:
                response.raise_for_status()
                js = await response.text()
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug(
                "Failed to fetch station JS chunk %s for station %s: %s",
                chunk_url,
                self.station_id,
                err,
            )
            return

        if not self._set_key_from_js(js):
            _LOGGER.debug(
                "AES decrypt key pattern not found in JS chunk for station %s — key not updated",
                self.station_id,
            )

    async def _extract_key_broad(self, session: ClientSession, html: str) -> None:
        """Iterate every chunk in the HTML until the AES key regex matches."""
        station_chunks = _STATION_CHUNK_RE.findall(html)
        all_chunks = _ANY_CHUNK_FINDALL_RE.findall(html)
        # Try the station chunk first (legacy fast path), then the rest.
        ordered = list(dict.fromkeys(station_chunks + all_chunks))

        if not ordered:
            _LOGGER.debug(
                "No JS chunk URLs found in HTML for station %s — decrypt key not refreshed",
                self.station_id,
            )
            return

        for chunk_path in ordered:
            if ".." in chunk_path or not re.match(
                r"^/_next/static/chunks/[a-zA-Z0-9._\-%/]+\.js$", chunk_path
            ):
                continue
            chunk_url = BASE_URL + chunk_path
            _LOGGER.debug(
                "Scanning JS chunk for decrypt key (station %s): %s",
                self.station_id,
                chunk_url,
            )
            js = await self._fetch_chunk(session, chunk_url)
            if js is None:
                continue
            if self._set_key_from_js(js, source=chunk_path):
                return

        _LOGGER.debug(
            "AES decrypt key pattern not found in any JS chunk for station %s — key not updated",
            self.station_id,
        )

    async def _fetch_chunk(self, session: ClientSession, chunk_url: str) -> str | None:
        """Fetch one JS chunk; return None on HTTP non-200 or ClientError so caller can skip."""
        try:
            async with session.get(
                chunk_url, timeout=_TIMEOUT, headers=_HEADERS
            ) as response:
                if response.status != 200:
                    return None
                return await response.text()
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug(
                "Failed to fetch chunk %s for station %s: %s",
                chunk_url,
                self.station_id,
                err,
            )
            return None

    def _set_key_from_js(self, js: str, source: str | None = None) -> bool:
        """Update decrypt_key if the AES regex matches; return True on success."""
        match = _AES_KEY_RE.search(js)
        if not match:
            return False
        self.decrypt_key = match.group(1)
        if source is None:
            _LOGGER.debug(
                "Extracted decrypt key for station %s: <extracted>",
                self.station_id,
            )
        else:
            _LOGGER.debug(
                "Extracted decrypt key for station %s from %s: <extracted>",
                self.station_id,
                source,
            )
        return True
