"""GrFuelgovProvider — Greek fuel prices via the nireas.iee.ihu.gr community API.

Source: Community API maintained by iee.ihu.gr / nireas project, sourced from
Greek Ministry of Energy and Climate Change daily PDF bulletins.

Endpoint
--------
GET https://nireas.iee.ihu.gr/fuel/api/v1/prices/latest
  No authentication required.
  Returns JSON with prefecture-level (51 prefectures) and a national weighted
  average (ΠΑΝΕΛΛΗΝΙΟΣ ΣΤΑΘΜΙΣΜΕΝΟΣ Μ.Ο., prefecture id=52) for 4 fuel types.
  Updated daily; the ``data.date`` field holds the bulletin date (YYYY-MM-DD).

Response shape
--------------
{
  "data": {
    "date": "2026-06-11",
    "entries": [
      {
        "prefecture": {"id": 1, "name": "ΝΟΜΟΣ ΑΤΤΙΚΗΣ"},
        "prices": {
          "Αμόλυβδη 95 οκτ.": 1.973,
          "Αμόλυβδη 100 οκτ.": 2.231,
          "Diesel Κίνησης": 1.722,
          "Υγραέριο κίνησης (Autogas)": 1.017
        }
      },
      ...
      {
        "prefecture": {"id": 52, "name": "ΠΑΝΕΛΛΗΝΙΟΣ ΣΤΑΘΜΙΣΜΕΝΟΣ Μ.Ο."},
        "prices": { ... }
      }
    ]
  },
  "meta": {"count": 52, "unit": "EUR/L"}
}

Fuel key mapping
----------------
API name (Greek)                 → StationData key
-----------------------------       ----------------
Αμόλυβδη 95 οκτ.                → unleaded_95  (stored as ``unleaded``)
Αμόλυβδη 100 οκτ.               → unleaded_100 (stored as ``premium_unleaded``)
Diesel Κίνησης                  → diesel
Υγραέριο κίνησης (Autogas)      → lpg

CONFIG_MODE
-----------
'location' — there is no station-level data in this API.  The station_id is
always the country code 'GR'.  The provider returns prefecture-level
or national-average prices depending on which prefecture (by name or id)
was configured.  By default the national weighted average is used.

Prefecture selection
--------------------
The user may optionally specify a prefecture by:
  - name (Greek or ASCII-approximate) via the ``prefecture`` constructor arg
  - id (1–51 for prefectures, 52 = national average) via ``prefecture_id``

When neither is supplied the national average (id=52) is returned.

async_list_stations
-------------------
Returns a list of (prefecture_id_str, display_label) tuples for each
entry in the API response, so the config flow can offer a prefecture picker.
Station-level data is not available (STATION_LEVEL = False).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

from ..const import UA_HEADER, API_TIMEOUT
from .base import BaseProvider, ProviderError, StationData

_LOGGER = logging.getLogger(__name__)

_API_URL = "https://nireas.iee.ihu.gr/fuel/api/v1/prices/latest"

_HEADERS: dict[str, str] = {
    "User-Agent": UA_HEADER,
    "Accept": "application/json",
}

_TIMEOUT = ClientTimeout(total=API_TIMEOUT)

# National weighted average entry uses this name in the API.
_NATIONAL_AVG_NAME = "ΠΑΝΕΛΛΗΝΙΟΣ ΣΤΑΘΜΙΣΜΕΝΟΣ Μ.Ο."
_NATIONAL_AVG_ID = 52


class GrFuelgovProvider(BaseProvider):
    """Fetch Greek fuel prices from the nireas.iee.ihu.gr community API.

    Prefecture-level and national-average prices are available for 4 fuel
    types in EUR/litre, updated daily from Ministry of Energy bulletins.
    Station-level data is not available via this API.

    Usage
    -----
    The constructor accepts an optional prefecture name or id.  When neither
    is supplied the national weighted average is returned.  The station_id
    passed to async_fetch is always ``'GR'`` (the country code).

    Args:
        station_id:     Ignored at runtime; always pass ``'GR'``.
        prefecture:     Optional prefecture name (Greek, e.g. 'ΝΟΜΟΣ ΑΤΤΙΚΗΣ')
                        or None for national average.
        prefecture_id:  Optional prefecture id (1–51) or 52 for national avg.
                        Takes precedence over ``prefecture`` when both supplied.
    """

    COUNTRY = "GR"
    PROVIDER_KEY = "gr_fuelgov"
    LABEL = "Greek Ministry of Energy (nireas.iee.ihu.gr)"
    CONFIG_MODE = "location"

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            # Fuel prices
            "unleaded",
            "premium_unleaded",
            "diesel",
            "lpg",
            # Location / identity
            "name",
            "county",
            # Timing
            "lastupdated",
        }
    )

    STATION_LOOKUP_MODE = "location_search"

    POLL_INTERVAL_SECONDS = 86400
    STATION_PAGE_URL: ClassVar[str] = (
        "https://www.fuelgov.gr"  # Daily — bulletin updated once per day.
    )

    def __init__(
        self,
        station_id: str = "GR",
        prefecture: str | None = None,
        prefecture_id: int | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            station_id:     Ignored at runtime (always 'GR').
            prefecture:     Greek prefecture name, e.g. 'ΝΟΜΟΣ ΑΤΤΙΚΗΣ'.
                            When None, national average is used.
            prefecture_id:  Prefecture numeric id (1–51 or 52 for national avg).
                            When supplied, takes precedence over ``prefecture``.
        """
        self._station_id = station_id
        self._prefecture = prefecture
        # Normalise: if neither is given, default to national average id.
        if prefecture_id is not None:
            self._prefecture_id: int | None = prefecture_id
        elif prefecture is None:
            self._prefecture_id = _NATIONAL_AVG_ID
        else:
            self._prefecture_id = None  # will match by name

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch prefecture or national average prices from the API.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: Ignored; always fetches from the single endpoint.

        Returns:
            StationData dict populated with all CAPABILITIES keys.

        Raises:
            ProviderError:  Requested prefecture not found in API response,
                            or the API returns an unexpected payload shape.
            ClientError:    Network or HTTP error (propagated to coordinator).
        """
        payload = await self._fetch_payload(session)
        entry = self._select_entry(payload)
        return self._build_station_data(entry, payload)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return a human-readable name for the config flow, or None.

        For location-mode providers this method returns None and the config
        flow uses the auto-generated 'Country (lat, lon)' title instead.
        """
        try:
            payload = await self._fetch_payload(session)
            entry = self._select_entry(payload)
            name: str | None = entry.get("prefecture", {}).get("name")
            return name or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("GrFuelgovProvider: failed to fetch station name: %s", err)
            return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (prefecture_id_str, display_label) tuples for the station picker.

        Called by the config flow location_search step.  Returns all 52
        entries (51 prefectures + national average), sorted by prefecture id
        ascending, with the national average appended last.

        The ``lat``, ``lng``, and ``radius_km`` kwargs accepted by other
        location_search providers are accepted but ignored — this API
        returns all prefectures in a single response and there is no
        coordinate filtering to apply.

        Args:
            session: aiohttp ClientSession.

        Returns:
            List of (id_str, "Prefecture Name — Diesel €x.xxx / Unleaded €x.xxx")
            tuples.  Empty list on any failure.
        """
        try:
            payload = await self._fetch_payload(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("GrFuelgovProvider: async_list_stations failed: %s", err)
            return []

        entries: list[dict] = payload.get("data", {}).get("entries", [])
        if not entries:
            return []

        result: list[tuple[str, str, int]] = []
        for entry in entries:
            pref = entry.get("prefecture", {})
            pref_id: int | None = pref.get("id")
            pref_name: str = pref.get("name") or "Unknown"

            # Skip entries with no id.
            if pref_id is None:
                continue

            prices: dict[str, float] = entry.get("prices", {})
            diesel = prices.get("Diesel Κίνησης")
            unleaded = prices.get("Αμόλυβδη 95 οκτ.")

            price_parts: list[str] = []
            if diesel is not None:
                try:
                    diesel_val = float(diesel)
                    price_parts.append(f"Diesel €{diesel_val:.3f}")
                except (ValueError, TypeError):
                    pass
            if unleaded is not None:
                try:
                    unleaded_val = float(unleaded)
                    price_parts.append(f"Unleaded €{unleaded_val:.3f}")
                except (ValueError, TypeError):
                    pass

            if price_parts:
                label = f"{pref_name} — {' / '.join(price_parts)}"
            else:
                label = pref_name

            result.append((str(pref_id), label, pref_id))

        # Sort by prefecture id; national average (id=52) sorts naturally last.
        result.sort(key=lambda x: x[2])
        return [(pid, lbl) for pid, lbl, _ in result]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_payload(self, session: ClientSession) -> dict:
        """Fetch and return the raw JSON payload from the API.

        Args:
            session: aiohttp ClientSession.

        Returns:
            Parsed JSON dict.

        Raises:
            ProviderError:  Non-retryable API-level error (e.g. unexpected shape).
            ClientResponseError: HTTP 4xx/5xx (propagated to coordinator).
            Exception:      Any other network error (propagated to coordinator).
        """
        _LOGGER.debug("GrFuelgovProvider: fetching %s", _API_URL)
        try:
            async with session.get(
                _API_URL,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload: dict = await response.json(content_type=None)
        except ClientResponseError as err:
            _LOGGER.debug(
                "GrFuelgovProvider: HTTP error %s fetching %s", err.status, _API_URL
            )
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "GrFuelgovProvider: connection error fetching %s: %s", _API_URL, err
            )
            raise

        if not isinstance(payload, dict) or "data" not in payload:
            raise ProviderError(
                f"GrFuelgovProvider: unexpected API response shape — "
                f"'data' key missing.  Got keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}"
            )

        entries = payload.get("data", {}).get("entries")
        if not isinstance(entries, list) or not entries:
            raise ProviderError(
                "GrFuelgovProvider: API returned empty or missing 'entries' list."
            )

        return payload

    def _select_entry(self, payload: dict) -> dict:
        """Select the target prefecture entry from the payload.

        Selection priority:
          1. Match by ``_prefecture_id`` when set.
          2. Match by ``_prefecture`` name (case-insensitive) when set.
          3. Fall back to the national average (id=52).

        Args:
            payload: Parsed API response dict.

        Returns:
            The matching entry dict.

        Raises:
            ProviderError: When the requested prefecture is not found in the
                           API response.
        """
        entries: list[dict] = payload.get("data", {}).get("entries", [])

        if self._prefecture_id is not None:
            for entry in entries:
                pref = entry.get("prefecture", {})
                if pref.get("id") == self._prefecture_id:
                    return entry
            raise ProviderError(
                f"GrFuelgovProvider: prefecture id={self._prefecture_id} not found "
                f"in API response.  Available ids: "
                f"{[e.get('prefecture', {}).get('id') for e in entries]}"
            )

        if self._prefecture is not None:
            target = self._prefecture.strip().upper()
            for entry in entries:
                pref = entry.get("prefecture", {})
                name: str = (pref.get("name") or "").strip().upper()
                if name == target:
                    return entry
            raise ProviderError(
                f"GrFuelgovProvider: prefecture '{self._prefecture}' not found "
                f"in API response.  Check the spelling matches the Greek name "
                f"returned by the API."
            )

        # Default: national weighted average.
        for entry in entries:
            pref = entry.get("prefecture", {})
            if pref.get("id") == _NATIONAL_AVG_ID:
                return entry
            if pref.get("name") == _NATIONAL_AVG_NAME:
                return entry

        raise ProviderError(
            "GrFuelgovProvider: national average entry not found in API response."
        )

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_station_data(self, entry: dict, payload: dict) -> StationData:
        """Assemble a StationData dict from a single prefecture entry.

        Args:
            entry:   A single entry dict from payload['data']['entries'].
            payload: The full payload dict (used for the bulletin date).

        Returns:
            Populated StationData dict.
        """
        pref: dict = entry.get("prefecture", {})
        pref_id: int | None = pref.get("id")
        pref_name: str | None = pref.get("name") or None

        prices_raw: dict = entry.get("prices", {})

        def _price(greek_name: str) -> float | None:
            """Extract and validate a fuel price from the raw prices dict."""
            val = prices_raw.get(greek_name)
            if val is None:
                return None
            try:
                f = float(val)
            except (ValueError, TypeError):
                return None
            if f <= 0:
                return None
            return round(f, 3)

        # Bulletin date from the top-level data section.
        bulletin_date: str | None = payload.get("data", {}).get("date") or None

        # Build a human-readable station name from the prefecture.
        # For the national average, include "Greece" for clarity.
        if pref_id == _NATIONAL_AVG_ID or pref_name == _NATIONAL_AVG_NAME:
            display_name: str | None = "Greece (National Average)"
            county: str | None = None
        else:
            display_name = pref_name
            county = pref_name

        data: StationData = {
            # Fuel prices (EUR/litre — no conversion needed, API returns EUR/L)
            "unleaded": _price("Αμόλυβδη 95 οκτ."),
            "premium_unleaded": _price("Αμόλυβδη 100 οκτ."),
            "diesel": _price("Diesel Κίνησης"),
            "lpg": _price("Υγραέριο κίνησης (Autogas)"),
            # Identity
            "name": display_name,
            "county": county,
            # Timing
            "lastupdated": bulletin_date,
            # Passthrough
            "source_station_id": "GR",
        }

        _LOGGER.debug(
            "GrFuelgovProvider: parsed data for prefecture '%s' (id=%s): "
            "unleaded=%s diesel=%s lpg=%s date=%s",
            pref_name,
            pref_id,
            data.get("unleaded"),
            data.get("diesel"),
            data.get("lpg"),
            bulletin_date,
        )

        return data
