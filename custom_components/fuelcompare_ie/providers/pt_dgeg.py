"""PtDgegProvider — Portuguese government fuel price data (DGEG).

Source: Direção-Geral de Energia e Geologia (DGEG), Portugal.
Mandatory fuel price disclosure under Portuguese law.
Endpoint: GET https://precoscombustiveis.dgeg.gov.pt/api/PrecoComb/GetDadosPosto
  ?id={station_id}&idioma=pt

All stations (discovery): GET PesquisarPostos with district filter.

Fuel type mapping (TipoCombustivel field):
  'Gasóleo simples'    → diesel
  'Gasóleo especial'   → premium_diesel   (not in CAPABILITIES, ignored)
  'Gasolina simples 95'→ unleaded
  'Gasolina especial 95'→ (ignored)
  'Gasolina 98'        → premium_unleaded
  'GPL Auto'           → lpg

ToS: Personal/home-automation use acceptable; commercial use prohibited.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from aiohttp import ClientSession, ClientTimeout

from ..const import API_TIMEOUT
from .base import (
    BaseProvider,
    ProviderError,
    StationData,
    haversine_km as _haversine_km,
)

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://precoscombustiveis.dgeg.gov.pt/api/PrecoComb"
_GET_POSTO_URL = f"{_BASE_URL}/GetDadosPosto"
_SEARCH_URL = f"{_BASE_URL}/PesquisarPostos"
_FUEL_TYPES_URL = f"{_BASE_URL}/GetTiposCombustiveis"
_DISTRICTS_URL = f"{_BASE_URL}/GetDistritos"

_TIMEOUT = ClientTimeout(total=API_TIMEOUT * 3)


async def _get_json(
    session: ClientSession,
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET *url* and return the parsed JSON body.

    Raises:
        aiohttp.ClientError: propagated to the caller on any network or SSL
            error; the coordinator will handle it as UpdateFailed.
    """
    async with session.get(url, params=params, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


# Fuel name → StationData key mapping (Portuguese → internal key)
# Only map types in CAPABILITIES; others are silently skipped.
_FUEL_NAME_MAP: dict[str, str] = {
    "Gasóleo simples": "diesel",
    "Gasolina simples 95": "unleaded",
    "Gasolina 98": "premium_unleaded",
    "GPL Auto": "lpg",
}


def _parse_price(raw: str | None) -> float | None:
    """Parse a Portuguese price string to a float (EUR/litre).

    Handles both formats returned by the API:
      GetDadosPosto:   '1,953 €/litro'
      PesquisarPostos: '1,739 €'
    """
    if not raw:
        return None
    try:
        cleaned = (
            raw.replace(" €/litro", "").replace(" €", "").replace(",", ".").strip()
        )
        value = float(cleaned)
        # Prices >10 are in cents — normalise to EUR/litre
        return round(value / 100.0 if value > 10 else value, 4)
    except (ValueError, AttributeError):
        return None


class PtDgegProvider(BaseProvider):
    """Fetch Portuguese fuel prices from the DGEG government API.

    CONFIG_MODE='location': the user supplies lat/lng + radius. The
    async_list_stations() method searches across all districts and returns
    stations sorted by price. Once a station is chosen, async_fetch() uses
    GetDadosPosto to retrieve all fuel prices in a single request.
    """

    COUNTRY = "PT"
    PROVIDER_KEY = "pt_dgeg"
    DISABLED = (
        True  # 0.7.0: upstream failing in live verification — disable until fixed
    )
    LABEL = "DGEG (Portugal)"
    CONFIG_MODE = "location"
    STATION_LOOKUP_MODE = "location_search"
    POLL_INTERVAL_SECONDS = 3600
    STATION_PAGE_URL: ClassVar[str] = (
        "https://precoscombustiveis.dgeg.gov.pt"  # DGEG updates intraday; 1-hour poll is sufficient
    )

    CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {
            "diesel",
            "unleaded",
            "premium_unleaded",
            "lpg",
            "lastupdated",
            "name",
            "county",
            "address",
            "latitude",
            "longitude",
        }
    )

    STATION_ID_HINT = (
        "Enter the DGEG station ID (numeric). "
        "Use the location search to browse nearby stations."
    )

    def __init__(
        self,
        station_id: str,
        county: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
    ) -> None:
        self._station_id = station_id
        self._county = county
        self._latitude = latitude
        self._longitude = longitude
        self._radius_km = radius_km or 10.0

    # ── Public interface ──────────────────────────────────────────────────────

    async def async_fetch(
        self,
        session: ClientSession,
        station_id: str,
    ) -> StationData:
        """Fetch all fuel prices for a single station via GetDadosPosto."""
        params = {"id": station_id, "idioma": "pt"}
        try:
            data: dict[str, Any] = await _get_json(session, _GET_POSTO_URL, params)
        except Exception as err:
            raise ProviderError(
                f"DGEG: failed to fetch station {station_id}: {err}"
            ) from err

        if data.get("status") is not True:
            raise ProviderError(
                f"DGEG: API returned failure for station {station_id}: "
                f"{data.get('mensagem', 'unknown error')}"
            )

        resultado = data.get("resultado")
        if not isinstance(resultado, dict):
            raise ProviderError(
                f"DGEG: unexpected response shape for station {station_id}"
            )

        return _parse_station(station_id, resultado)

    async def async_fetch_station_name(
        self,
        session: ClientSession,
        station_id: str,
    ) -> str | None:
        """Return the station name for the config flow, or None on failure."""
        try:
            params = {"id": station_id, "idioma": "pt"}
            data: dict[str, Any] = await _get_json(session, _GET_POSTO_URL, params)
            resultado = data.get("resultado") or {}
            if isinstance(resultado, dict):
                return resultado.get("Nome") or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "DGEG: failed to fetch station name for %s: %s", station_id, err
            )
        return None

    async def async_list_stations(
        self,
        session: ClientSession,
        **kwargs: Any,
    ) -> list[tuple[str, str]]:
        """Return (station_id, display_label) sorted alphabetically by label.

        Searches PesquisarPostos across all districts, collecting up to
        qtd=5000 results, then filters by geographic proximity when lat/lng
        are available.

        Label format: "{brand/name}, {address} (#{station_id[:8]})"
        No price information is included in the label.

        Keyword args (from config flow location_search):
            lat (float): Centre latitude.
            lng (float): Centre longitude.
            radius_km (float): Search radius in kilometres.
        """
        lat: float | None = (
            kwargs["lat"] if kwargs.get("lat") is not None else self._latitude
        )
        lng: float | None = (
            kwargs["lng"] if kwargs.get("lng") is not None else self._longitude
        )
        radius_km: float = float(kwargs.get("radius_km") or self._radius_km)

        params: dict[str, Any] = {
            "qtd": 5000,
            "idioma": "pt",
            "asc": "true",
            "ordem": 1,
        }

        try:
            data: dict[str, Any] = await _get_json(session, _SEARCH_URL, params)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("DGEG: async_list_stations request failed: %s", err)
            return []

        rows = data.get("resultado") if isinstance(data.get("resultado"), list) else []
        if not rows:
            _LOGGER.debug("DGEG: async_list_stations returned empty resultado")
            return []

        # Build per-station aggregated data: collect all fuel prices per station ID
        stations: dict[str, dict[str, Any]] = {}
        for row in rows:
            sid = str(row.get("Id", "")).strip()
            if not sid:
                continue

            if sid not in stations:
                try:
                    row_lat: float | None = (
                        float(row["Latitude"]) if row.get("Latitude") else None
                    )
                    row_lng: float | None = (
                        float(row["Longitude"]) if row.get("Longitude") else None
                    )
                except (ValueError, TypeError):
                    row_lat = None
                    row_lng = None

                stations[sid] = {
                    "name": row.get("Nome") or row.get("Marca") or f"Station {sid}",
                    "brand": row.get("Marca") or None,
                    "address": row.get("Morada") or None,
                    "localidade": row.get("Localidade") or None,
                    "municipio": row.get("Municipio") or None,
                    "distrito": row.get("Distrito") or None,
                    "latitude": row_lat,
                    "longitude": row_lng,
                    "prices": {},
                }

            fuel_name: str = row.get("Combustivel") or ""
            preco_raw: str | None = row.get("Preco")
            key = _FUEL_NAME_MAP.get(fuel_name)
            if key and preco_raw:
                price = _parse_price(preco_raw)
                if price is not None:
                    # Keep first (cheapest, list is sorted asc by price)
                    stations[sid]["prices"].setdefault(key, price)

        # Filter by proximity when coordinates available
        if lat is not None and lng is not None:
            filtered: dict[str, dict[str, Any]] = {}
            for sid, info in stations.items():
                s_lat = info.get("latitude")
                s_lng = info.get("longitude")
                if s_lat is not None and s_lng is not None:
                    if _haversine_km(lat, lng, s_lat, s_lng) <= radius_km:
                        filtered[sid] = info
                # Stations without coordinates are excluded when filtering by location
            stations = filtered

        # Sort alphabetically by label
        result: list[tuple[str, str]] = []
        for sid, info in stations.items():
            name = info.get("name") or f"Station {sid}"
            brand = info.get("brand") or ""
            address = info.get("address") or ""

            # Primary identifier: prefer "Brand — Name" when brand differs from name
            if brand and brand.lower() not in name.lower():
                primary = f"{brand} — {name}"
            else:
                primary = name

            # Build label: "{primary}, {address} (#{sid[:8]})"
            parts: list[str] = [primary]
            if address:
                parts.append(address)
            label = ", ".join(parts) + f" (#{sid[:8]})"

            result.append((sid, label))

        result.sort(key=lambda item: item[1].lower())
        return result


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_station(station_id: str, resultado: dict[str, Any]) -> StationData:
    """Build a StationData dict from a GetDadosPosto resultado dict."""
    name: str | None = resultado.get("Nome") or resultado.get("Marca") or None

    morada: dict[str, Any] = resultado.get("Morada") or {}
    address: str | None = morada.get("Morada") or None
    county: str | None = morada.get("Municipio") or morada.get("Distrito") or None

    try:
        latitude: float | None = (
            float(morada["Latitude"]) if morada.get("Latitude") is not None else None
        )
    except (ValueError, TypeError):
        latitude = None

    try:
        longitude: float | None = (
            float(morada["Longitude"]) if morada.get("Longitude") is not None else None
        )
    except (ValueError, TypeError):
        longitude = None

    # Extract fuel prices and the most recent update timestamp
    combustiveis: list[dict[str, Any]] = resultado.get("Combustiveis") or []
    prices: dict[str, float | None] = {}
    latest_update: str | None = None

    for entry in combustiveis:
        fuel_name: str = entry.get("TipoCombustivel") or ""
        key = _FUEL_NAME_MAP.get(fuel_name)
        preco_raw: str | None = entry.get("Preco")
        data_atualizacao: str | None = entry.get("DataAtualizacao")

        if key and preco_raw:
            price = _parse_price(preco_raw)
            if price is not None:
                prices[key] = price

        # Track most recent update timestamp across all fuels
        if data_atualizacao:
            if latest_update is None or data_atualizacao > latest_update:
                latest_update = data_atualizacao

    return {
        "diesel": prices.get("diesel"),
        "unleaded": prices.get("unleaded"),
        "premium_unleaded": prices.get("premium_unleaded"),
        "lpg": prices.get("lpg"),
        "name": name,
        "county": county,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "lastupdated": latest_update,
    }
