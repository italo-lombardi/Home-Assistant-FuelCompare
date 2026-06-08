"""DataUpdateCoordinator for FuelCompare.ie."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import homeassistant.util.dt as dt_util
from aiohttp import ClientError, ClientSession, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_TIMEOUT, BASE_URL, DEFAULT_SCAN_INTERVAL, FUEL_TYPES
from .crypto import cryptojs_decrypt as _cryptojs_decrypt
from .page_assets import PageAssets

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_LOGGER = logging.getLogger(__name__)

_ISSUE_TRACKER_URL = (
    "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues"
)


class FuelCompareIECoordinator(DataUpdateCoordinator[dict[str, float | None]]):
    """Class to manage fetching FuelCompare.ie data."""

    def __init__(self, hass: HomeAssistant, station_id: str) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"FuelCompare.ie Station {station_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.station_id = station_id
        # Cached across updates; refreshed automatically when stale (HTTP non-200 or decrypt failure)
        self._assets = PageAssets(station_id)
        # Timestamp of the last successful fetch — exposed via the
        # IntegrationLastSuccessSensor and the StationFetchOkBinarySensor
        # so automations can distinguish "site stamp didn't change" (price
        # genuinely unchanged) from "integration hasn't fetched in N hours"
        # (transient site outage / throttling). Stays None until first success.
        self.last_successful_fetch: datetime | None = None

    # ---- Backwards-compatible accessors used by tests / older imports -------

    @property
    def _build_id(self) -> str | None:
        """Cached Next.js buildId (proxied to PageAssets)."""
        return self._assets.build_id

    @_build_id.setter
    def _build_id(self, value: str | None) -> None:
        """Setter mirror so ``coordinator._build_id = ...`` propagates to PageAssets."""
        self._assets.build_id = value

    @property
    def _decrypt_key(self) -> str | None:
        """Cached AES decrypt key (proxied to PageAssets)."""
        return self._assets.decrypt_key

    @_decrypt_key.setter
    def _decrypt_key(self, value: str | None) -> None:
        """Setter mirror so ``coordinator._decrypt_key = ...`` propagates to PageAssets."""
        self._assets.decrypt_key = value

    async def _fetch_page_assets(
        self, session: ClientSession, broad: bool = False
    ) -> None:
        """Refresh buildId and AES decrypt key from the station HTML.

        Thin wrapper kept for test compatibility — real work happens inside
        :class:`PageAssets`. ``broad=True`` enables scanning every chunk in
        the HTML; default mode only checks the per-page station chunk.
        """
        await self._assets.refresh(session, broad=broad)

    # ---- Main update flow ----------------------------------------------------

    async def _async_update_data(self) -> dict[str, float | None]:
        """Fetch data from FuelCompare.ie using two paths with automatic fallback.

        Primary path: Next.js static JSON (/_next/data/{buildId}/station/{id}.json).
        Fallback path: encrypted POST API (/fuelcompareback/stationbyid) introduced
        when fuelcompare.ie migrated away from server-side rendering for some stations.
        Both paths share _parse_station so sensors are identical regardless of source.
        """
        try:
            session = async_get_clientsession(self.hass)
            _LOGGER.debug("Starting data update for station %s", self.station_id)

            station_data = await self._fetch_nextjs(session)

            if station_data is None:
                _LOGGER.debug(
                    "Next.js path returned no data for station %s — trying encrypted API fallback",
                    self.station_id,
                )
                station_data = await self._fetch_encrypted_api(session)

            if station_data is None:
                # All known fetch and key-extraction strategies failed. Surface
                # a high-visibility error pointing to the issue tracker so users
                # can report site-side breakages quickly — past failures (Next.js
                # SSR removal, AES key relocation) needed code changes to recover.
                _LOGGER.error(
                    "FuelCompare.ie integration could not retrieve data for station %s "
                    "via any available method (Next.js JSON, encrypted API, broad chunk "
                    "scan). The site may have changed again. Please open an issue at %s "
                    "with your station ID and Home Assistant debug logs.",
                    self.station_id,
                    _ISSUE_TRACKER_URL,
                )
                raise UpdateFailed("Station data not found via any available method")

            _LOGGER.debug("Raw station data for %s: %s", self.station_id, station_data)
            parsed = self._parse_station(station_data)
            # Stamp success only after parsing succeeds — a parse exception still
            # raises UpdateFailed below and must not advance the timestamp.
            self.last_successful_fetch = dt_util.utcnow()
            return parsed

        except ClientError as err:
            _LOGGER.debug("HTTP error fetching station %s: %s", self.station_id, err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.debug(
                "Unexpected error fetching station %s: %s", self.station_id, err
            )
            raise UpdateFailed(f"Unexpected error: {err}") from err

    # ---- Path A: Next.js static JSON ----------------------------------------

    async def _fetch_nextjs(self, session: ClientSession) -> dict | None:
        """Try fetching station data via the Next.js static JSON path.

        Returns None (instead of raising) so the caller can fall back to the encrypted API.
        The buildId is cached and refreshed when the endpoint returns non-200 (stale deploy).
        """
        try:
            if self._build_id is None:
                # First run — fetch HTML to extract buildId and decrypt key together
                await self._fetch_page_assets(session)

            data_url = (
                f"{BASE_URL}/_next/data/{self._build_id}/station/{self.station_id}.json"
            )
            _LOGGER.debug("Fetching Next.js URL: %s", data_url)

            async with session.get(
                data_url, timeout=_TIMEOUT, headers=_HEADERS
            ) as response:
                if response.status != 200:
                    # buildId changes on every site deploy — refresh and retry once
                    _LOGGER.debug(
                        "Next.js fetch returned HTTP %s for station %s — refreshing page assets",
                        response.status,
                        self.station_id,
                    )
                    await self._fetch_page_assets(session)
                    data_url = f"{BASE_URL}/_next/data/{self._build_id}/station/{self.station_id}.json"
                    _LOGGER.debug("Retrying Next.js URL: %s", data_url)
                    async with session.get(
                        data_url, timeout=_TIMEOUT, headers=_HEADERS
                    ) as retry_response:
                        retry_response.raise_for_status()
                        json_data = await retry_response.json()
                else:
                    json_data = await response.json()

            _LOGGER.debug(
                "Next.js raw response for station %s: %s", self.station_id, json_data
            )

            station = json_data.get("pageProps", {}).get("initialStation")
            if not station:
                # Site returns success with initialStation=null for stations that have
                # been migrated to client-side rendering — this is the normal fallback trigger
                _LOGGER.debug(
                    "Next.js initialStation missing for station %s (site error: %s)",
                    self.station_id,
                    json_data.get("pageProps", {}).get("error"),
                )
                return None

            return station

        except Exception as err:
            _LOGGER.debug(
                "Next.js path failed for station %s: %s", self.station_id, err
            )
            return None

    # ---- Path B: encrypted POST API -----------------------------------------

    async def _fetch_encrypted_api(self, session: ClientSession) -> dict | None:
        """Fetch station data from the encrypted POST API endpoint.

        fuelcompare.ie introduced a /fuelcompareback/stationbyid endpoint that returns
        AES-encrypted JSON. The decrypt key is extracted from their JS bundle and cached
        on the PageAssets instance. On decrypt failure the key is re-fetched automatically
        to handle site redeployments that rotate or relocate the key — first via the
        original single-chunk lookup, then via a broad scan across every chunk in the HTML.
        """
        if self._decrypt_key is None:
            # Key not yet extracted — fetch page assets now (also refreshes buildId)
            await self._fetch_page_assets(session)

        if self._decrypt_key is None:
            # Standard single-chunk search came up empty — fall back to broad
            # scan across every chunk in the HTML before giving up. Without this
            # the encrypted API would never be hit on first run for site builds
            # where the AES key has been relocated to a shared vendor chunk.
            _LOGGER.debug(
                "Decrypt key not found via standard path for station %s — trying broad chunk scan",
                self.station_id,
            )
            await self._fetch_page_assets(session, broad=True)

        if self._decrypt_key is None:
            # JS chunk was unreachable or key pattern changed — cannot proceed
            _LOGGER.debug(
                "Decrypt key unavailable for station %s — skipping encrypted API",
                self.station_id,
            )
            return None

        encrypted = await self._post_encrypted(session)
        if encrypted is None:
            return None

        decrypted = await self._decrypt_with_recovery(session, encrypted)
        if decrypted is None:
            return None

        _LOGGER.debug(
            "Decrypted response for station %s: %s", self.station_id, decrypted
        )

        # Decrypted payload is [[station_dict, ...], mysql_metadata_dict]
        stations = decrypted[0] if isinstance(decrypted, list) and decrypted else None
        if not stations:
            _LOGGER.debug(
                "No stations in decrypted payload for station %s", self.station_id
            )
            return None

        station = stations[0] if isinstance(stations, list) else stations

        # Encrypted API uses 'state' where the Next.js path uses 'county' —
        # normalise so _parse_station and all sensors work identically for both paths
        if "state" in station and "county" not in station:
            station["county"] = station["state"]

        return station

    async def _post_encrypted(self, session: ClientSession) -> str | None:
        """POST to /fuelcompareback/stationbyid and return the encrypted blob.

        Returns None when the response is non-success or carries no data, so the
        caller can short-circuit before attempting decryption.
        """
        url = f"{BASE_URL}/fuelcompareback/stationbyid"
        _LOGGER.debug(
            "Posting to encrypted API for station %s: %s", self.station_id, url
        )

        async with session.post(
            url,
            json={"id": int(self.station_id)},
            timeout=_TIMEOUT,
            headers={**_HEADERS, "Content-Type": "application/json"},
        ) as response:
            _LOGGER.debug(
                "Encrypted API HTTP status for station %s: %s",
                self.station_id,
                response.status,
            )
            response.raise_for_status()
            payload = await response.json()

        _LOGGER.debug(
            "Encrypted API raw payload for station %s: %s", self.station_id, payload
        )

        if not payload.get("success"):
            _LOGGER.debug(
                "Encrypted API success=false for station %s: %s",
                self.station_id,
                payload,
            )
            return None

        encrypted = payload.get("data")
        if not encrypted:
            _LOGGER.debug(
                "Encrypted API returned empty data for station %s", self.station_id
            )
            return None

        return encrypted

    async def _decrypt_with_recovery(
        self, session: ClientSession, encrypted: str
    ) -> list | None:
        """Decrypt ``encrypted`` with automatic key recovery on failure.

        Tries the cached key first. On failure: refresh via the standard
        single-chunk lookup and retry; on still-failure: refresh via the broad
        chunk scan and retry once more. Returns the decoded JSON list or None
        if every attempt failed.
        """
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as err:
            _LOGGER.debug(
                "Decrypt failed for station %s (stale key?): %s — refreshing key and retrying",
                self.station_id,
                err,
            )

        await self._fetch_page_assets(session)
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as retry_err:
            _LOGGER.debug(
                "Decrypt failed again for station %s after standard refresh: %s — retrying with broad chunk scan",
                self.station_id,
                retry_err,
            )

        await self._fetch_page_assets(session, broad=True)
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as broad_err:
            _LOGGER.debug(
                "Decrypt failed for station %s even after broad chunk scan: %s",
                self.station_id,
                broad_err,
            )
            return None

    # ---- Shared parser -------------------------------------------------------

    def _parse_station(self, station: dict) -> dict[str, float | None]:
        """Parse a raw station dict (from either fetch path) into coordinator data."""
        fuel_data: dict[str, float | None] = {}

        for fuel_type in FUEL_TYPES:
            raw_value = station.get(fuel_type)
            _LOGGER.debug(
                "Parsing %s for station %s: raw=%r",
                fuel_type,
                self.station_id,
                raw_value,
            )
            if raw_value and raw_value != "":
                try:
                    price = float(
                        str(raw_value).replace("€", "").replace(",", "").strip()
                    )
                    # Site stores prices inconsistently — sometimes cents (189.9), sometimes
                    # euros (1.899). Values >10 are treated as cents and divided by 100.
                    if price > 10:
                        price = price / 100
                    fuel_data[fuel_type] = round(price, 3)
                    _LOGGER.debug(
                        "Parsed %s for station %s: %.3f EUR",
                        fuel_type,
                        self.station_id,
                        price,
                    )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Failed to parse %s price for station %s: %r",
                        fuel_type,
                        self.station_id,
                        raw_value,
                    )
                    fuel_data[fuel_type] = None
            else:
                fuel_data[fuel_type] = None

        fuel_data["lastupdated"] = station.get("lastupdated")
        for field in ["name", "tablename", "working_hours", "about", "county"]:
            fuel_data[field] = station.get(field)

        _LOGGER.debug(
            "Final parsed data for station %s: %s", self.station_id, fuel_data
        )
        return fuel_data
