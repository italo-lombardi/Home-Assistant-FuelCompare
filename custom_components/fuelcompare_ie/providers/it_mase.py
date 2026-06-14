"""ItMaseProvider — Italian government fuel price data (MIMIT/MASE).

Source: Ministero delle Imprese e del Made in Italy (MIMIT), Italy.
Mandatory fuel price disclosure under Italian law (Decreto Trasparenza).
Endpoints (both pipe-delimited CSV, updated daily ~08:00 local time):
  1. https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv
     Price file — 5 fields per row, ~93 k rows.
  2. https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv
     Station metadata — 10 fields per row (with a resilient parser for the
     ~0.4 % of rows that embed a literal pipe in the station name field).

Both files share the same quirk: line 1 is a human-readable extraction-date
banner ("Estrazione del YYYY-MM-DD") that must be skipped; the actual column
header is on line 2; data begins on line 3.

Join key: idImpianto (numeric integer string, e.g. "3464").

Fuel type mapping (descCarburante → StationData key):
  Benzina          → unleaded
  Gasolio          → diesel
  GPL              → lpg
  Metano           → cng
  Benzina Super    → premium_unleaded
  Anything else    → silently skipped (57 distinct strings in the wild).

isSelf: "0" = attended / full-service; "1" = self-service.
When both variants exist for the same fuel type, the cheapest price wins
(self-service is typically 10–20 c/L cheaper than attended).

dtComu format: DD/MM/YYYY HH:MM:SS (Italian day-first locale).

Licence: IODL 2.0 (Italian Open Data Licence v2.0).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

# ── Endpoint URLs ─────────────────────────────────────────────────────────────

_PRICE_URL = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"
_META_URL = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"

# ── HTTP settings ─────────────────────────────────────────────────────────────

_HEADERS: dict[str, str] = {
    "User-Agent": "HomeAssistant/2025.1 aiohttp/3.9.1",
    "Accept": "*/*",
}
# The price CSV is ~3.6 MB and may be slow on Italian government servers;
# give it 3× the standard timeout.
_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)

# ── Field encodings ───────────────────────────────────────────────────────────

_PRICE_ENCODING = "latin-1"
_META_ENCODING = "utf-8"

# ── Fuel type mapping ─────────────────────────────────────────────────────────

# Only canonical mappings; all other descCarburante strings are skipped.
_DESC_TO_KEY: dict[str, str] = {
    "Benzina": "unleaded",
    "Gasolio": "diesel",
    "GPL": "lpg",
    "Metano": "cng",
    "Benzina Super": "premium_unleaded",
}


# ── Module-level CSV parsing helpers ─────────────────────────────────────────


def _skip_banner(text: str) -> str:
    """Strip the 'Estrazione del YYYY-MM-DD' banner (line 1) from a CSV text.

    Both MIMIT CSV files begin with a single non-header line.  This function
    removes it so the remaining text can be fed to a standard CSV reader with
    the header on its first line.

    Args:
        text: Raw decoded CSV text.

    Returns:
        Text with the first line removed.
    """
    idx = text.find("\n")
    if idx == -1:
        return text
    return text[idx + 1 :]


def _parse_price_csv(
    text: str,
) -> tuple[dict[str, dict[str, list[float]]], dict[str, str | None]]:
    """Parse the price CSV and return prices and per-station timestamps.

    The returned tuple contains two dicts:

    1. prices — station → fuel → [prices]::

        {
          "3464": {
            "unleaded": [2.389, 2.029],
            "diesel":   [2.479, 2.119],
            ...
          },
          ...
        }

    2. timestamps — station → latest ISO 8601 dtComu string (or None)::

        {"3464": "2026-06-13T08:00:00", ...}

    Multiple price rows exist per (station, fuel) pair because attended ("0")
    and self-service ("1") prices are reported on separate rows.  Both are
    collected; the caller selects the cheapest.  The *latest* dtComu seen for
    each station is stored; rows are typically all identical for a given station
    but the maximum is taken to be safe.

    Args:
        text: Decoded price CSV text (banner already stripped).

    Returns:
        Tuple of (prices dict, timestamps dict).
    """
    prices: dict[str, dict[str, list[float]]] = {}
    timestamps: dict[str, str | None] = {}
    lines = text.splitlines()
    # Skip the header row (first line after banner strip)
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        station_id = parts[0].strip()
        desc = parts[1].strip()
        price_str = parts[2].strip()

        # Extract dtComu timestamp from index 4 when present
        dtcomu_raw = parts[4].strip() if len(parts) >= 5 else ""
        iso_ts = _parse_dtcomu(dtcomu_raw) if dtcomu_raw else None

        fuel_key = _DESC_TO_KEY.get(desc)
        if fuel_key is None:
            # Still record the timestamp even if fuel type is unmapped
            if iso_ts is not None:
                existing = timestamps.get(station_id)
                if existing is None or iso_ts > existing:
                    timestamps[station_id] = iso_ts
            continue  # unmapped fuel type — skip price

        try:
            price = float(price_str)
        except ValueError:
            continue
        if price <= 0:
            continue

        station_prices = prices.setdefault(station_id, {})
        station_prices.setdefault(fuel_key, []).append(price)

        # Keep the latest timestamp seen for this station
        if iso_ts is not None:
            existing = timestamps.get(station_id)
            if existing is None or iso_ts > existing:
                timestamps[station_id] = iso_ts

    return prices, timestamps


def _parse_meta_csv(text: str) -> dict[str, dict[str, Any]]:
    """Parse the station metadata CSV.

    Field order (10 canonical fields)::

        idImpianto | Gestore | Bandiera | Tipo Impianto | Nome Impianto |
        Indirizzo  | Comune  | Provincia | Latitudine   | Longitudine

    CAUTION: ~103 rows (~0.4 %) embed a literal pipe inside "Nome Impianto",
    producing 11 or more fields.  The safe strategy is to anchor from the
    right: Latitudine and Longitudine are always the last two fields and are
    always numeric decimal strings.  Fields [4..n-5] are joined back as the
    station name.

    Args:
        text: Decoded metadata CSV text (banner already stripped).

    Returns:
        Dict: station_id → {gestore, bandiera, tipo, nome, indirizzo,
                             comune, provincia, lat, lon}
    """
    result: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()
    # Skip header row
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 10:
            continue  # malformed row — skip

        station_id = parts[0].strip()
        gestore = parts[1].strip()
        bandiera = parts[2].strip()
        tipo = parts[3].strip()

        # Anchor from the right to handle embedded pipes in station name
        # Last two fields are always Latitudine, Longitudine (numeric).
        # Fields [-5:] are: Indirizzo, Comune, Provincia, Lat, Lon.
        lon_str = parts[-1].strip()
        lat_str = parts[-2].strip()
        provincia = parts[-3].strip()
        comune = parts[-4].strip()
        indirizzo = parts[-5].strip()

        # Reconstruct station name from any middle parts
        nome_parts = parts[4 : len(parts) - 5]
        nome = "|".join(nome_parts).strip()

        try:
            lat = float(lat_str)
            lon = float(lon_str)
        except ValueError:
            lat = None  # type: ignore[assignment]
            lon = None  # type: ignore[assignment]

        result[station_id] = {
            "gestore": gestore,
            "bandiera": bandiera,
            "tipo": tipo,
            "nome": nome,
            "indirizzo": indirizzo,
            "comune": comune,
            "provincia": provincia,
            "lat": lat,
            "lon": lon,
        }

    return result


def _parse_dtcomu(dtcomu: str) -> str | None:
    """Parse an Italian DD/MM/YYYY HH:MM:SS timestamp to ISO 8601.

    Args:
        dtcomu: Timestamp string in "DD/MM/YYYY HH:MM:SS" format.

    Returns:
        ISO 8601 string (e.g. "2026-06-11T19:30:07"), or None on failure.
    """
    try:
        dt = datetime.strptime(dtcomu, "%d/%m/%Y %H:%M:%S")
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def _cheapest_price(prices: list[float]) -> float | None:
    """Return the cheapest price from a list, or None if the list is empty."""
    if not prices:
        return None
    return round(min(prices), 3)


def _build_station_data(
    station_id: str,
    meta: dict[str, Any],
    prices: dict[str, list[float]],
    last_updated: str | None,
) -> StationData:
    """Assemble a StationData dict from parsed metadata and prices.

    Args:
        station_id:   MIMIT numeric station ID string.
        meta:         Metadata dict from _parse_meta_csv.
        prices:       fuel_key → [prices] from _parse_price_csv.
        last_updated: ISO 8601 timestamp of the most recent price submission,
                      or None.

    Returns:
        Populated StationData dict.
    """
    lat = meta.get("lat")
    lon = meta.get("lon")

    # Address: combine street + municipality
    indirizzo = meta.get("indirizzo") or ""
    comune = meta.get("comune") or ""
    address_parts = [p for p in [indirizzo, comune] if p]
    address = ", ".join(address_parts) or None

    return {
        "unleaded": _cheapest_price(prices.get("unleaded", [])),
        "diesel": _cheapest_price(prices.get("diesel", [])),
        "lpg": _cheapest_price(prices.get("lpg", [])),
        "cng": _cheapest_price(prices.get("cng", [])),
        "premium_unleaded": _cheapest_price(prices.get("premium_unleaded", [])),
        "name": meta.get("nome") or None,
        "brand": meta.get("bandiera") or None,
        "address": address,
        "county": meta.get("provincia") or None,
        "latitude": lat if isinstance(lat, float) else None,
        "longitude": lon if isinstance(lon, float) else None,
        "lastupdated": last_updated,
        "source_station_id": station_id,
    }


# ── Provider class ────────────────────────────────────────────────────────────


class ItMaseProvider(BaseProvider):
    """Fetch Italian fuel prices from the MIMIT/MASE open-data CSV files.

    Both CSV files are downloaded on every poll (the price file is ~3.6 MB,
    the metadata file is ~2 MB) and joined on idImpianto.  STATION_LOOKUP_MODE
    is 'location_search': the user supplies lat/lng + radius_km and all
    matching stations are returned sorted cheapest-first.

    CONFIG_MODE is 'location' — the coordinator creates one entity entry per
    nearby station rather than tracking a single fixed station by ID.

    Polling interval: 3600 s (1 hour) — the files are regenerated once daily
    at ~08:00 local time but this aligns with any intra-day republications.
    """

    COUNTRY = "IT"
    PROVIDER_KEY = "it_mase"
    LABEL = "MIMIT/MASE (Italy)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600

    CAPABILITIES: frozenset[str] = frozenset(
        {
            "unleaded",
            "diesel",
            "lpg",
            "cng",
            "premium_unleaded",
            "lastupdated",
            "name",
            "brand",
            "county",
            "address",
            "latitude",
            "longitude",
            "last_successful_fetch",
            "data_fetch_problem",
        }
    )

    STATION_ID_HINT = (
        "Enter the MIMIT station ID (idImpianto) — a numeric string "
        "found in the MIMIT open-data CSV files, e.g. '3464'."
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
            station_id:  MIMIT numeric station ID (idImpianto) as a string.
                         Also used as the coordinator entity key.
            county:      Optional Italian province code (e.g. 'RM' for Rome).
                         Not used for filtering — stored for informational use.
            latitude:    WGS84 latitude of the user's location.
            longitude:   WGS84 longitude of the user's location.
            radius_km:   Search radius in kilometres.  Stations beyond this
                         distance are excluded from async_list_stations results.
        """
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km if radius_km is not None else 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch and return normalised data for the given station.

        Downloads both CSV files, joins them on idImpianto, and returns a
        StationData dict for the requested station ID.

        Args:
            session:    aiohttp ClientSession provided by the coordinator.
            station_id: MIMIT idImpianto string (e.g. '3464').

        Returns:
            Populated StationData dict.

        Raises:
            ProviderError: Station not found in the national dataset or the
                           CSV data is structurally invalid.
        """
        price_data, timestamps_data, meta_data = await self._fetch_both_csvs(session)

        prices = price_data.get(station_id)
        if prices is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in MIMIT price dataset. "
                "Verify the station ID or check that the station is still active."
            )

        meta = meta_data.get(station_id)
        if meta is None:
            raise ProviderError(
                f"Station ID '{station_id}' not found in MIMIT station metadata. "
                "The station may have been decommissioned."
            )

        last_updated = self._extract_latest_timestamp(timestamps_data, station_id)
        return _build_station_data(station_id, meta, prices, last_updated)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station display name for the config flow, or None.

        CONFIG_MODE is 'location' so the config flow uses an auto-generated
        location title; this method is provided for completeness and for any
        future manual-ID lookup flows.

        Args:
            session:    aiohttp ClientSession.
            station_id: MIMIT idImpianto string.

        Returns:
            Station name string, or None on failure.
        """
        try:
            _, _timestamps, meta_data = await self._fetch_both_csvs(session)
            meta = meta_data.get(station_id)
            if meta:
                nome = meta.get("nome")
                bandiera = meta.get("bandiera")
                if nome and bandiera:
                    return f"{bandiera} — {nome}"
                return nome or bandiera or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch station name for %s: %s", station_id, err)
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) pairs for stations near the user.

        Downloads both CSV files and returns all stations within radius_km of
        the configured coordinates, sorted cheapest-first by diesel price (or
        unleaded price if diesel is unavailable; stations with no price go to
        the end).

        Args:
            session:   aiohttp ClientSession.
            lat:       Override latitude (falls back to constructor value).
            lng:       Override longitude (falls back to constructor value).
            radius_km: Override search radius (falls back to constructor value).

        Returns:
            List of (station_id, display_label) tuples, cheapest first.
            Empty list on any failure.
        """
        raw_lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude
        raw_lng = (
            kwargs.get("lng") if kwargs.get("lng") is not None else self._longitude
        )
        if raw_lat is None or raw_lng is None:
            _LOGGER.warning("async_list_stations called without coordinates")
            return []
        lat: float = float(raw_lat)
        lng: float = float(raw_lng)
        radius_km: float = float(kwargs.get("radius_km", self._radius_km))

        try:
            price_data, _timestamps, meta_data = await self._fetch_both_csvs(session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("async_list_stations failed to fetch CSVs: %s", err)
            return []

        results: list[tuple[str, str, float]] = []

        for station_id, meta in meta_data.items():
            slat = meta.get("lat")
            slon = meta.get("lon")
            if not isinstance(slat, float) or not isinstance(slon, float):
                continue

            dist = _haversine_km(lat, lng, slat, slon)
            if dist > radius_km:
                continue

            prices = price_data.get(station_id, {})

            nome = meta.get("nome") or ""
            bandiera = meta.get("bandiera") or ""
            display_name = (
                f"{bandiera} — {nome}"
                if bandiera and nome
                else (nome or bandiera or f"Station {station_id}")
            )
            comune = meta.get("comune") or ""
            if comune:
                display_name = f"{display_name} ({comune})"

            diesel_prices = prices.get("diesel", [])
            petrol_prices = prices.get("unleaded", [])

            price_parts: list[str] = []
            sort_price = 9999.0

            d_price = _cheapest_price(diesel_prices)
            if d_price is not None:
                price_parts.append(f"Diesel €{d_price:.3f}")
                sort_price = min(sort_price, d_price)

            p_price = _cheapest_price(petrol_prices)
            if p_price is not None:
                price_parts.append(f"Benzina €{p_price:.3f}")
                sort_price = min(sort_price, p_price)

            label = (
                f"{display_name} — {' / '.join(price_parts)} ({dist:.1f} km)"
                if price_parts
                else f"{display_name} ({dist:.1f} km)"
            )

            results.append((station_id, label, sort_price))

        results.sort(key=lambda x: x[2])
        return [(sid, label) for sid, label, _ in results]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_both_csvs(
        self,
        session: ClientSession,
    ) -> tuple[
        dict[str, dict[str, list[float]]],
        dict[str, str | None],
        dict[str, dict[str, Any]],
    ]:
        """Fetch and parse both MIMIT CSV files concurrently.

        Returns:
            Tuple of (price_data, timestamps_data, meta_data).

        Raises:
            ProviderError: On HTTP error or structurally invalid response.
        """

        price_task = self._fetch_csv(session, _PRICE_URL, _PRICE_ENCODING)
        meta_task = self._fetch_csv(session, _META_URL, _META_ENCODING)

        price_text, meta_text = await asyncio.gather(price_task, meta_task)

        loop = asyncio.get_running_loop()
        price_data, timestamps_data = await loop.run_in_executor(
            None, functools.partial(_parse_price_csv, _skip_banner(price_text))
        )
        meta_data = await loop.run_in_executor(
            None, functools.partial(_parse_meta_csv, _skip_banner(meta_text))
        )

        if not price_data:
            raise ProviderError(
                "MIMIT price CSV parsed to empty dataset — possible format change."
            )
        if not meta_data:
            raise ProviderError(
                "MIMIT metadata CSV parsed to empty dataset — possible format change."
            )

        return price_data, timestamps_data, meta_data

    async def _fetch_csv(
        self,
        session: ClientSession,
        url: str,
        encoding: str,
    ) -> str:
        """Fetch a MIMIT CSV file and return the decoded text.

        Args:
            session:  aiohttp ClientSession.
            url:      Full URL to fetch.
            encoding: Character encoding to use when decoding the response.

        Returns:
            Decoded CSV text string.

        Raises:
            ProviderError: On HTTP 4xx/5xx or decoding failure.
        """
        _LOGGER.debug("Fetching MIMIT CSV: %s (encoding=%s)", url, encoding)
        async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            raw_bytes = await resp.read()

        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            # Fallback: try utf-8 with replacement characters
            _LOGGER.debug(
                "Failed to decode %s as %s; falling back to utf-8 with replacement",
                url,
                encoding,
            )
            return raw_bytes.decode("utf-8", errors="replace")

    def _extract_latest_timestamp(
        self,
        timestamps_data: dict[str, str | None],
        station_id: str,
    ) -> str | None:
        """Return the most recent dtComu timestamp for a station, or None.

        Args:
            timestamps_data: station_id → ISO 8601 timestamp dict from
                             _parse_price_csv.
            station_id:      MIMIT idImpianto string to look up.

        Returns:
            ISO 8601 timestamp string, or None if not present.
        """
        return timestamps_data.get(station_id)
