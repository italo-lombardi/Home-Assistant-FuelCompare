"""DataUpdateCoordinator for FuelCompare.ie."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import timedelta

from aiohttp import ClientError, ClientSession, ClientTimeout
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_TIMEOUT, BASE_URL, DEFAULT_SCAN_INTERVAL, FUEL_TYPES

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_LOGGER = logging.getLogger(__name__)


def _cryptojs_decrypt(encrypted_b64: str, passphrase: str) -> list:
    """Decrypt a CryptoJS AES-CBC base64 payload using EvpKDF key derivation.

    fuelcompare.ie API responses are encrypted with CryptoJS AES using a passphrase
    hardcoded in their station JS bundle. CryptoJS uses a non-standard OpenSSL-compatible
    format: base64("Salted__" + 8-byte-salt + ciphertext), with key+IV derived via
    iterative MD5 (EvpKDF). The passphrase is extracted dynamically by _fetch_page_assets.
    """
    raw = base64.b64decode(encrypted_b64)
    # CryptoJS Salted__ format: bytes 0-7 = magic, 8-15 = salt, 16+ = ciphertext
    salt = raw[8:16]
    ciphertext = raw[16:]

    # EvpKDF: chain MD5(prev + passphrase + salt) until we have 48 bytes (32 key + 16 IV)
    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + passphrase.encode() + salt).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    # Remove PKCS7 padding — last byte is the pad length
    pad_len = padded[-1]
    return json.loads(padded[:-pad_len])


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
        self._build_id: str | None = None
        self._decrypt_key: str | None = None

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
                raise UpdateFailed("Station data not found via any available method")

            _LOGGER.debug("Raw station data for %s: %s", self.station_id, station_data)
            return self._parse_station(station_data)

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

    async def _fetch_nextjs(self, session: ClientSession) -> dict | None:
        """Try fetching station data via the Next.js static JSON path.

        Returns None (instead of raising) so the caller can fall back to the encrypted API.
        The buildId is cached and refreshed when the endpoint returns non-200 (stale deploy).
        """
        try:
            if self._build_id is None:
                # First run — fetch HTML to extract buildId and decrypt key together
                await self._fetch_page_assets(session)

            if self._build_id is None:
                # _fetch_page_assets raised internally but we're inside a broad except below;
                # this guard makes the None-return path explicit for the fallback
                _LOGGER.debug(
                    "buildId unavailable for station %s — skipping Next.js path",
                    self.station_id,
                )
                return None

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

    async def _fetch_encrypted_api(self, session: ClientSession) -> dict | None:
        """Fetch station data from the encrypted POST API endpoint.

        fuelcompare.ie introduced a /fuelcompareback/stationbyid endpoint that returns
        AES-encrypted JSON. The decrypt key is extracted from their JS bundle and cached
        in self._decrypt_key. On decrypt failure the key is re-fetched automatically to
        handle site redeployments that rotate the key.
        """
        if self._decrypt_key is None:
            # Key not yet extracted — fetch page assets now (also refreshes buildId)
            await self._fetch_page_assets(session)

        if self._decrypt_key is None:
            # JS chunk was unreachable or key pattern changed — cannot proceed
            _LOGGER.debug(
                "Decrypt key unavailable for station %s — skipping encrypted API",
                self.station_id,
            )
            return None

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

        try:
            decrypted = _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as err:
            # Likely cause: site redeployed with a new key — refresh and retry once
            _LOGGER.debug(
                "Decrypt failed for station %s (stale key?): %s — refreshing key and retrying",
                self.station_id,
                err,
            )
            await self._fetch_page_assets(session)
            try:
                decrypted = _cryptojs_decrypt(encrypted, self._decrypt_key)
            except Exception as retry_err:
                _LOGGER.debug(
                    "Decrypt failed again for station %s after key refresh: %s",
                    self.station_id,
                    retry_err,
                )
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
        for field in ["tablename", "working_hours", "about", "county"]:
            fuel_data[field] = station.get(field)

        _LOGGER.debug(
            "Final parsed data for station %s: %s", self.station_id, fuel_data
        )
        return fuel_data

    async def _fetch_page_assets(self, session: ClientSession) -> None:
        """Fetch the station HTML page and extract buildId and AES decrypt key.

        Both assets are extracted in a single HTML fetch plus one JS chunk fetch.
        Called on first run, on stale buildId (HTTP non-200), and on decrypt failure.
        Updates self._build_id and self._decrypt_key in place.
        """
        url = f"{BASE_URL}/station/{self.station_id}"
        _LOGGER.debug("Fetching page assets from %s", url)

        async with session.get(url, timeout=_TIMEOUT, headers=_HEADERS) as response:
            response.raise_for_status()
            html = await response.text()

        build_match = re.search(r'"buildId":"([^"]+)"', html)
        if not build_match:
            _LOGGER.debug(
                "buildId pattern not found in HTML for station %s", self.station_id
            )
            raise UpdateFailed("buildId not found in page")
        self._build_id = build_match.group(1)
        _LOGGER.debug(
            "Extracted buildId for station %s: %s", self.station_id, self._build_id
        )

        # The decrypt key lives in the station-specific JS chunk, not the main bundle —
        # its filename contains the chunk hash and changes with each site deploy
        chunk_match = re.search(r'(/_next/static/chunks/pages/station/[^"]+\.js)', html)
        if not chunk_match:
            # No chunk URL in HTML — site may have restructured; key stays as-is
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

        async with session.get(
            chunk_url, timeout=_TIMEOUT, headers=_HEADERS
        ) as response:
            response.raise_for_status()
            js = await response.text()

        # Pattern matches: AES.decrypt(e,"<64-char-hex-key>")
        key_match = re.search(r'AES\.decrypt\(e,"([a-f0-9]{64})"', js)
        if not key_match:
            # Site may have obfuscated or renamed the decrypt call
            _LOGGER.debug(
                "AES decrypt key pattern not found in JS chunk for station %s — key not updated",
                self.station_id,
            )
            return

        self._decrypt_key = key_match.group(1)
        _LOGGER.debug(
            "Extracted decrypt key for station %s: %s…",
            self.station_id,
            self._decrypt_key[:8],
        )
