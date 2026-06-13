"""IEFuelCompareProvider — scrapes fuelcompare.ie for Irish fuel prices."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..const import API_TIMEOUT, BASE_URL, FUEL_TYPES
from ..crypto import cryptojs_decrypt as _cryptojs_decrypt
from ..page_assets import PageAssets
from .base import BaseProvider

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_LOGGER = logging.getLogger(__name__)

_ISSUE_TRACKER_URL = (
    "https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues"
)


class IEFuelCompareProvider(BaseProvider):
    """Fetch Irish fuel prices by scraping fuelcompare.ie."""

    COUNTRY = "IE"
    PROVIDER_KEY = "ie_fuelcompare"
    LABEL = "fuelcompare.ie"

    def __init__(self, station_id: str) -> None:
        self._station_id = station_id
        self._assets = PageAssets(station_id)

    # ---- Public interface -------------------------------------------------------

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> dict[str, Any]:
        """Fetch and return normalised station data dict."""
        station_data = await self._fetch_nextjs(session)

        if station_data is None:
            _LOGGER.debug(
                "Next.js path returned no data for station %s — trying encrypted API fallback",
                station_id,
            )
            station_data = await self._fetch_encrypted_api(session)

        if station_data is None:
            _LOGGER.error(
                "Fuel Compare integration could not retrieve data for station %s "
                "via any available method (Next.js JSON, encrypted API, broad chunk "
                "scan). The site may have changed again. Please open an issue at %s "
                "with your station ID and Home Assistant debug logs.",
                station_id,
                _ISSUE_TRACKER_URL,
            )
            raise RuntimeError("Station data not found via any available method")

        _LOGGER.debug("Raw station data for %s: %s", station_id, station_data)
        return self._parse_station(station_data)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return station name for config flow pre-population."""
        try:
            await self._fetch_page_assets(session)
            data = await self._fetch_nextjs(session)
            if data is None:
                data = await self._fetch_encrypted_api(session)
            if data:
                if data.get("name"):
                    return data["name"]
                if data.get("tablename"):
                    return data["tablename"].replace("_", " ").title()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    # ---- Backwards-compatible accessors (used by tests) -------------------------

    @property
    def _build_id(self) -> str | None:
        return self._assets.build_id

    @_build_id.setter
    def _build_id(self, value: str | None) -> None:
        self._assets.build_id = value

    @property
    def _decrypt_key(self) -> str | None:
        return self._assets.decrypt_key

    @_decrypt_key.setter
    def _decrypt_key(self, value: str | None) -> None:
        self._assets.decrypt_key = value

    async def _fetch_page_assets(
        self, session: ClientSession, broad: bool = False
    ) -> None:
        await self._assets.refresh(session, broad=broad)

    # ---- Path A: Next.js static JSON --------------------------------------------

    async def _fetch_nextjs(self, session: ClientSession) -> dict | None:
        try:
            if self._build_id is None:
                await self._fetch_page_assets(session)

            data_url = (
                f"{BASE_URL}/_next/data/{self._build_id}/station/{self._station_id}.json"
            )
            _LOGGER.debug("Fetching Next.js URL: %s", data_url)

            async with session.get(
                data_url, timeout=_TIMEOUT, headers=_HEADERS
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "Next.js fetch returned HTTP %s for station %s — refreshing page assets",
                        response.status,
                        self._station_id,
                    )
                    await self._fetch_page_assets(session)
                    data_url = f"{BASE_URL}/_next/data/{self._build_id}/station/{self._station_id}.json"
                    _LOGGER.debug("Retrying Next.js URL: %s", data_url)
                    async with session.get(
                        data_url, timeout=_TIMEOUT, headers=_HEADERS
                    ) as retry_response:
                        retry_response.raise_for_status()
                        json_data = await retry_response.json()
                else:
                    json_data = await response.json()

            _LOGGER.debug(
                "Next.js raw response for station %s: %s", self._station_id, json_data
            )

            station = json_data.get("pageProps", {}).get("initialStation")
            if not station:
                _LOGGER.debug(
                    "Next.js initialStation missing for station %s (site error: %s)",
                    self._station_id,
                    json_data.get("pageProps", {}).get("error"),
                )
                return None

            return station

        except Exception as err:
            _LOGGER.debug(
                "Next.js path failed for station %s: %s", self._station_id, err
            )
            return None

    # ---- Path B: encrypted POST API ---------------------------------------------

    async def _fetch_encrypted_api(self, session: ClientSession) -> dict | None:
        if self._decrypt_key is None:
            await self._fetch_page_assets(session)

        if self._decrypt_key is None:
            _LOGGER.debug(
                "Decrypt key not found via standard path for station %s — trying broad chunk scan",
                self._station_id,
            )
            await self._fetch_page_assets(session, broad=True)

        if self._decrypt_key is None:
            _LOGGER.debug(
                "Decrypt key unavailable for station %s — skipping encrypted API",
                self._station_id,
            )
            return None

        encrypted = await self._post_encrypted(session)
        if encrypted is None:
            return None

        decrypted = await self._decrypt_with_recovery(session, encrypted)
        if decrypted is None:
            return None

        _LOGGER.debug(
            "Decrypted response for station %s: %s", self._station_id, decrypted
        )

        stations = decrypted[0] if isinstance(decrypted, list) and decrypted else None
        if not stations:
            _LOGGER.debug(
                "No stations in decrypted payload for station %s", self._station_id
            )
            return None

        station = stations[0] if isinstance(stations, list) else stations

        if "state" in station and "county" not in station:
            station["county"] = station["state"]

        return station

    async def _post_encrypted(self, session: ClientSession) -> str | None:
        url = f"{BASE_URL}/fuelcompareback/stationbyid"
        _LOGGER.debug(
            "Posting to encrypted API for station %s: %s", self._station_id, url
        )

        async with session.post(
            url,
            json={"id": int(self._station_id)},
            timeout=_TIMEOUT,
            headers={**_HEADERS, "Content-Type": "application/json"},
        ) as response:
            _LOGGER.debug(
                "Encrypted API HTTP status for station %s: %s",
                self._station_id,
                response.status,
            )
            response.raise_for_status()
            payload = await response.json()

        _LOGGER.debug(
            "Encrypted API raw payload for station %s: %s", self._station_id, payload
        )

        if not payload.get("success"):
            _LOGGER.debug(
                "Encrypted API success=false for station %s: %s",
                self._station_id,
                payload,
            )
            return None

        encrypted = payload.get("data")
        if not encrypted:
            _LOGGER.debug(
                "Encrypted API returned empty data for station %s", self._station_id
            )
            return None

        return encrypted

    async def _decrypt_with_recovery(
        self, session: ClientSession, encrypted: str
    ) -> list | None:
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as err:
            _LOGGER.debug(
                "Decrypt failed for station %s (stale key?): %s — refreshing key and retrying",
                self._station_id,
                err,
            )

        await self._fetch_page_assets(session)
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as retry_err:
            _LOGGER.debug(
                "Decrypt failed again for station %s after standard refresh: %s — retrying with broad chunk scan",
                self._station_id,
                retry_err,
            )

        await self._fetch_page_assets(session, broad=True)
        try:
            return _cryptojs_decrypt(encrypted, self._decrypt_key)
        except Exception as broad_err:
            _LOGGER.debug(
                "Decrypt failed for station %s even after broad chunk scan: %s",
                self._station_id,
                broad_err,
            )
            return None

    # ---- Shared parser ----------------------------------------------------------

    def _parse_station(self, station: dict) -> dict[str, Any]:
        fuel_data: dict[str, Any] = {}

        for fuel_type in FUEL_TYPES:
            raw_value = station.get(fuel_type)
            _LOGGER.debug(
                "Parsing %s for station %s: raw=%r",
                fuel_type,
                self._station_id,
                raw_value,
            )
            if raw_value and raw_value != "":
                try:
                    price = float(
                        str(raw_value).replace("€", "").replace(",", "").strip()
                    )
                    if price > 10:
                        price = price / 100
                    fuel_data[fuel_type] = round(price, 3)
                    _LOGGER.debug(
                        "Parsed %s for station %s: %.3f EUR",
                        fuel_type,
                        self._station_id,
                        price,
                    )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Failed to parse %s price for station %s: %r",
                        fuel_type,
                        self._station_id,
                        raw_value,
                    )
                    fuel_data[fuel_type] = None
            else:
                fuel_data[fuel_type] = None

        fuel_data["lastupdated"] = station.get("lastupdated")
        for field in ["name", "tablename", "working_hours", "about", "county"]:
            fuel_data[field] = station.get(field)

        _LOGGER.debug(
            "Final parsed data for station %s: %s", self._station_id, fuel_data
        )
        return fuel_data
