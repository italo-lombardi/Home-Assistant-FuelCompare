"""DataUpdateCoordinator for FuelCompare.ie."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_TIMEOUT, BASE_URL, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


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
        self._build_id: str | None = None

    async def _async_update_data(self) -> dict[str, float | None]:
        """Fetch data from FuelCompare.ie using Next.js JSON extraction."""
        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }

            # Step 1: Fetch HTML to extract buildId (if we don't have it or if data fetch fails)
            if self._build_id is None:
                self._build_id = await self._fetch_build_id(timeout, headers)

            # Step 2: Fetch Next.js JSON data
            data_url = f"{BASE_URL}/_next/data/{self._build_id}/station/{self.station_id}.json"

            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(data_url) as response:
                    if response.status != 200:
                        # Build ID might be stale, refresh it
                        self._build_id = await self._fetch_build_id(timeout, headers)
                        data_url = f"{BASE_URL}/_next/data/{self._build_id}/station/{self.station_id}.json"
                        async with session.get(data_url) as retry_response:
                            retry_response.raise_for_status()
                            json_data = await retry_response.json()
                    else:
                        json_data = await response.json()

            # Extract fuel prices from JSON
            initial_station = json_data.get("pageProps", {}).get("initialStation", {})

            if not initial_station:
                raise UpdateFailed("Station data not found in response")

            # Parse fuel prices
            fuel_data: dict[str, float | None] = {}

            for fuel_type in ["unleaded", "diesel"]:
                raw_value = initial_station.get(fuel_type)
                if raw_value and raw_value != "":
                    try:
                        # Parse the price (remove €, commas, etc.)
                        price = float(str(raw_value).replace("€", "").replace(",", "").strip())
                        # Convert cents to euros if necessary
                        if price > 10:
                            price = price / 100
                        fuel_data[fuel_type] = round(price, 3)
                    except (ValueError, TypeError):
                        _LOGGER.warning("Failed to parse %s price: %s", fuel_type, raw_value)
                        fuel_data[fuel_type] = None
                else:
                    fuel_data[fuel_type] = None

            # Store last updated timestamp from the station data
            fuel_data["lastupdated"] = initial_station.get("lastupdated")

            # Store extra station metadata for sensor attributes
            for field in ["tablename", "working_hours", "about", "county"]:
                fuel_data[field] = initial_station.get(field)

            _LOGGER.debug("Fetched fuel data for station %s: %s", self.station_id, fuel_data)
            return fuel_data

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _fetch_build_id(self, timeout: aiohttp.ClientTimeout, headers: dict[str, str]) -> str:
        """Fetch the Next.js buildId from the HTML page."""
        url = f"{BASE_URL}/station/{self.station_id}"

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()

        # Extract buildId from HTML
        build_match = re.search(r'"buildId":"([^"]+)"', html)
        if not build_match:
            raise UpdateFailed("buildId not found in page")

        build_id = build_match.group(1)
        _LOGGER.debug("Extracted buildId: %s", build_id)
        return build_id
