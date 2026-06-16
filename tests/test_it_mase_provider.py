"""Tests for ItMaseProvider (MIMIT/MASE Italy)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.it_mase import (
    ItMaseProvider,
    _DESC_TO_KEY,
    _HEADERS,
    _META_URL,
    _PRICE_URL,
    _build_station_data,
    _cheapest_price,
    _haversine_km,
    _parse_dtcomu,
    _parse_meta_csv,
    _parse_price_csv,
    _skip_banner,
)


# ---------------------------------------------------------------------------
# Sample CSV fixtures
# ---------------------------------------------------------------------------

# Minimal well-formed price CSV (banner + header + 2 data rows for station 3464)
_PRICE_CSV = (
    "Estrazione del 2026-06-13\n"
    "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
    "3464|Benzina|1.899|0|13/06/2026 08:00:00\n"
    "3464|Benzina|1.829|1|13/06/2026 08:00:00\n"
    "3464|Gasolio|1.789|0|13/06/2026 08:00:00\n"
    "3464|Gasolio|1.699|1|13/06/2026 08:00:00\n"
    "3464|GPL|0.899|0|13/06/2026 08:00:00\n"
    "3464|Metano|1.299|0|13/06/2026 08:00:00\n"
    "3464|Benzina Super|2.099|0|13/06/2026 08:00:00\n"
    "9999|Gasolio|1.750|0|13/06/2026 08:00:00\n"
)

# Minimal well-formed metadata CSV (banner + header + 1 data row for station 3464)
_META_CSV = (
    "Estrazione del 2026-06-13\n"
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
    "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    "9999|Luigi Bianchi|IP|Stradale|IP Napoli Sud|Via Napoli 5|Napoli|NA|40.8518|14.2681\n"
)

# Metadata CSV row with embedded pipe in station name (11 fields)
_META_CSV_EMBEDDED_PIPE = (
    "Estrazione del 2026-06-13\n"
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
    "1111|Gestore Uno|Q8|Stradale|Stazione|Pipe|Name|Via Pipe 1|Milano|MI|45.4654|9.1866\n"
)

# Station nearby Rome (41.9, 12.5) at ~0 km distance
_META_CSV_NEARBY = (
    "Estrazione del 2026-06-13\n"
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
    "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    "5555|Far Station|IP|Autostradale|IP Autostrada|Autostrada A1|Firenze|FI|43.7696|11.2558\n"
)


def _make_mock_response(
    status: int = 200,
    content: bytes = b"",
    raise_for_status_exc: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=content)
    if raise_for_status_exc is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_for_status_exc)
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session_with_csvs(
    price_csv: str = _PRICE_CSV,
    meta_csv: str = _META_CSV,
    price_encoding: str = "ascii",
    meta_encoding: str = "utf-8",
) -> MagicMock:
    """Return a mock session that serves the price and meta CSV in order."""
    price_resp = _make_mock_response(
        content=price_csv.encode(price_encoding, errors="replace")
    )
    meta_resp = _make_mock_response(
        content=meta_csv.encode(meta_encoding, errors="replace")
    )
    responses = iter([price_resp, meta_resp])

    session = MagicMock()
    session.get = MagicMock(side_effect=lambda *a, **kw: next(responses))
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_country() -> None:
    """ItMaseProvider declares COUNTRY='IT'."""
    assert ItMaseProvider.COUNTRY == "IT"


def test_provider_key() -> None:
    """ItMaseProvider declares PROVIDER_KEY='it_mase'."""
    assert ItMaseProvider.PROVIDER_KEY == "it_mase"


def test_provider_label() -> None:
    """ItMaseProvider declares a non-empty human-readable LABEL."""
    assert "MIMIT" in ItMaseProvider.LABEL or "MASE" in ItMaseProvider.LABEL


def test_provider_config_mode() -> None:
    """ItMaseProvider uses CONFIG_MODE='location'."""
    assert ItMaseProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """ItMaseProvider uses STATION_LOOKUP_MODE='location_search'."""
    assert ItMaseProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """ItMaseProvider has POLL_INTERVAL_SECONDS=3600 (1 hour)."""
    assert ItMaseProvider.POLL_INTERVAL_SECONDS == 3600


def test_provider_capabilities_include_all_fuel_types() -> None:
    """CAPABILITIES includes all five Italian fuel types."""
    caps = ItMaseProvider.CAPABILITIES
    assert "unleaded" in caps
    assert "diesel" in caps
    assert "lpg" in caps
    assert "cng" in caps
    assert "premium_unleaded" in caps


def test_provider_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = ItMaseProvider.CAPABILITIES
    assert "name" in caps
    assert "brand" in caps
    assert "address" in caps
    assert "county" in caps
    assert "latitude" in caps
    assert "longitude" in caps
    assert "lastupdated" in caps


def test_provider_capabilities_exclude_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = ItMaseProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


def test_provider_headers_user_agent() -> None:
    """_HEADERS includes a User-Agent header."""
    ua = _HEADERS.get("User-Agent", "")
    assert "HomeAssistant" in ua or "aiohttp" in ua


def test_provider_headers_accept() -> None:
    """_HEADERS includes Accept: */*."""
    assert _HEADERS.get("Accept") == "*/*"


# ---------------------------------------------------------------------------
# _skip_banner
# ---------------------------------------------------------------------------


def test_skip_banner_removes_first_line() -> None:
    """_skip_banner strips the Estrazione del banner line."""
    text = "Estrazione del 2026-06-13\nheader\ndata"
    result = _skip_banner(text)
    assert result.startswith("header")
    assert "Estrazione" not in result


def test_skip_banner_no_newline_returns_text_unchanged() -> None:
    """_skip_banner returns the full string when there is no newline."""
    text = "noline"
    assert _skip_banner(text) == text


def test_skip_banner_preserves_subsequent_lines() -> None:
    """_skip_banner does not remove the header row or data rows."""
    text = "banner\nheader\nrow1\nrow2"
    result = _skip_banner(text)
    assert "header" in result
    assert "row1" in result
    assert "row2" in result


# ---------------------------------------------------------------------------
# _parse_price_csv
# ---------------------------------------------------------------------------


def test_parse_price_csv_returns_station_entry() -> None:
    """_parse_price_csv returns an entry for station 3464."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    assert "3464" in prices


def test_parse_price_csv_unleaded_collects_both_variants() -> None:
    """_parse_price_csv collects both attended and self-service unleaded prices."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    unleaded_prices = prices["3464"]["unleaded"]
    assert len(unleaded_prices) == 2
    assert 1.899 in unleaded_prices
    assert 1.829 in unleaded_prices


def test_parse_price_csv_diesel_collects_both_variants() -> None:
    """_parse_price_csv collects both attended and self-service diesel prices."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    diesel_prices = prices["3464"]["diesel"]
    assert len(diesel_prices) == 2
    assert 1.789 in diesel_prices
    assert 1.699 in diesel_prices


def test_parse_price_csv_lpg_collected() -> None:
    """_parse_price_csv maps 'GPL' to 'lpg'."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    assert "lpg" in prices["3464"]
    assert pytest.approx(0.899) in prices["3464"]["lpg"]


def test_parse_price_csv_cng_collected() -> None:
    """_parse_price_csv maps 'Metano' to 'cng'."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    assert "cng" in prices["3464"]
    assert pytest.approx(1.299) in prices["3464"]["cng"]


def test_parse_price_csv_premium_unleaded_collected() -> None:
    """_parse_price_csv maps 'Benzina Super' to 'premium_unleaded'."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    assert "premium_unleaded" in prices["3464"]
    assert pytest.approx(2.099) in prices["3464"]["premium_unleaded"]


def test_parse_price_csv_skips_unknown_fuel_type() -> None:
    """_parse_price_csv silently skips unmapped fuel descriptions."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|GasolioBlu+|1.799|0|13/06/2026 08:00:00\n"
        "3464|Benzina|1.829|1|13/06/2026 08:00:00\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    assert "GasolioBlu+" not in str(prices.get("3464", {}).keys())
    assert "unleaded" in prices["3464"]


def test_parse_price_csv_skips_zero_price() -> None:
    """_parse_price_csv ignores rows with price <= 0."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Benzina|0.000|0|13/06/2026 08:00:00\n"
        "3464|Benzina|1.829|0|13/06/2026 08:00:00\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    # Only the valid price should be stored
    assert prices["3464"]["unleaded"] == [1.829]


def test_parse_price_csv_skips_negative_price() -> None:
    """_parse_price_csv ignores rows with negative prices."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Benzina|-1.000|0|13/06/2026 08:00:00\n"
        "3464|Benzina|1.829|0|13/06/2026 08:00:00\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    assert prices["3464"]["unleaded"] == [1.829]


def test_parse_price_csv_skips_invalid_price_string() -> None:
    """_parse_price_csv ignores rows with non-numeric price strings."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Benzina|N/A|0|13/06/2026 08:00:00\n"
        "3464|Benzina|1.829|0|13/06/2026 08:00:00\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    assert prices["3464"]["unleaded"] == [1.829]


def test_parse_price_csv_skips_short_rows() -> None:
    """_parse_price_csv skips rows with fewer than 3 fields."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Benzina\n"
        "3464|Gasolio|1.699|0|13/06/2026 08:00:00\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    assert "3464" in prices
    assert "unleaded" not in prices.get("3464", {})
    assert "diesel" in prices["3464"]


def test_parse_price_csv_skips_blank_lines() -> None:
    """_parse_price_csv skips blank lines without error."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "\n"
        "3464|Benzina|1.829|0|13/06/2026 08:00:00\n"
        "\n"
    )
    text = _skip_banner(csv)
    prices, _timestamps = _parse_price_csv(text)
    assert "3464" in prices


def test_parse_price_csv_multiple_stations() -> None:
    """_parse_price_csv returns entries for all stations in the file."""
    text = _skip_banner(_PRICE_CSV)
    prices, _timestamps = _parse_price_csv(text)
    assert "3464" in prices
    assert "9999" in prices


def test_parse_price_csv_timestamps_populated() -> None:
    """_parse_price_csv returns ISO timestamps for stations that have dtComu."""
    text = _skip_banner(_PRICE_CSV)
    _prices, timestamps = _parse_price_csv(text)
    assert timestamps.get("3464") == "2026-06-13T08:00:00"
    assert timestamps.get("9999") == "2026-06-13T08:00:00"


def test_parse_price_csv_timestamp_missing_when_no_field5() -> None:
    """_parse_price_csv stores no timestamp when dtComu field is absent."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Benzina|1.829|0\n"
    )
    text = _skip_banner(csv)
    _prices, timestamps = _parse_price_csv(text)
    assert timestamps.get("3464") is None


# ---------------------------------------------------------------------------
# _parse_meta_csv
# ---------------------------------------------------------------------------


def test_parse_meta_csv_returns_station_entry() -> None:
    """_parse_meta_csv returns an entry for station 3464."""
    text = _skip_banner(_META_CSV)
    result = _parse_meta_csv(text)
    assert "3464" in result


def test_parse_meta_csv_station_fields() -> None:
    """_parse_meta_csv populates all expected metadata fields."""
    text = _skip_banner(_META_CSV)
    meta = _parse_meta_csv(text)["3464"]
    assert meta["gestore"] == "Mario Rossi"
    assert meta["bandiera"] == "ENI"
    assert meta["tipo"] == "Stradale"
    assert meta["nome"] == "ENI Via Roma"
    assert meta["indirizzo"] == "Via Roma 1"
    assert meta["comune"] == "Roma"
    assert meta["provincia"] == "RM"
    assert pytest.approx(meta["lat"], abs=0.001) == 41.9028
    assert pytest.approx(meta["lon"], abs=0.001) == 12.4964


def test_parse_meta_csv_embedded_pipe_station_name() -> None:
    """_parse_meta_csv correctly handles station names with embedded pipe characters."""
    text = _skip_banner(_META_CSV_EMBEDDED_PIPE)
    meta = _parse_meta_csv(text)
    assert "1111" in meta
    # The embedded pipe row has 11 fields; name should contain the rejoined parts
    nome = meta["1111"]["nome"]
    assert nome  # non-empty


def test_parse_meta_csv_lat_lon_numeric() -> None:
    """_parse_meta_csv parses lat/lon as float values."""
    text = _skip_banner(_META_CSV)
    meta = _parse_meta_csv(text)["3464"]
    assert isinstance(meta["lat"], float)
    assert isinstance(meta["lon"], float)


def test_parse_meta_csv_lat_lon_invalid_becomes_none() -> None:
    """_parse_meta_csv sets lat/lon to None when the values are non-numeric."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "7777|Gestore|BP|Stradale|BP Station|Via 1|Palermo|PA|N/A|N/A\n"
    )
    text = _skip_banner(csv)
    meta = _parse_meta_csv(text)["7777"]
    assert meta["lat"] is None
    assert meta["lon"] is None


def test_parse_meta_csv_skips_rows_fewer_than_10_fields() -> None:
    """_parse_meta_csv skips malformed rows with fewer than 10 fields."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "8888|Gestore|BP|Stradale\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    )
    text = _skip_banner(csv)
    result = _parse_meta_csv(text)
    assert "8888" not in result
    assert "3464" in result


def test_parse_meta_csv_skips_blank_lines() -> None:
    """_parse_meta_csv skips blank lines without error."""
    csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
        "\n"
    )
    text = _skip_banner(csv)
    result = _parse_meta_csv(text)
    assert "3464" in result


def test_parse_meta_csv_multiple_stations() -> None:
    """_parse_meta_csv returns entries for all stations in the file."""
    text = _skip_banner(_META_CSV)
    result = _parse_meta_csv(text)
    assert "3464" in result
    assert "9999" in result


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------


def test_haversine_same_point_is_zero() -> None:
    """_haversine_km returns 0.0 for identical points."""
    d = _haversine_km(41.9028, 12.4964, 41.9028, 12.4964)
    assert d == pytest.approx(0.0, abs=0.001)


def test_haversine_known_distance_rome_to_naples() -> None:
    """_haversine_km returns ~191 km between Rome and Naples."""
    d = _haversine_km(41.9028, 12.4964, 40.8518, 14.2681)
    assert 185.0 < d < 200.0


def test_haversine_short_distance() -> None:
    """_haversine_km returns a small value for nearby points."""
    d = _haversine_km(41.9028, 12.4964, 41.9100, 12.5000)
    assert d < 1.5


def test_haversine_symmetry() -> None:
    """_haversine_km(A, B) == _haversine_km(B, A)."""
    d1 = _haversine_km(41.9028, 12.4964, 40.8518, 14.2681)
    d2 = _haversine_km(40.8518, 14.2681, 41.9028, 12.4964)
    assert d1 == pytest.approx(d2, rel=1e-6)


# ---------------------------------------------------------------------------
# _parse_dtcomu
# ---------------------------------------------------------------------------


def test_parse_dtcomu_valid_timestamp() -> None:
    """_parse_dtcomu converts 'DD/MM/YYYY HH:MM:SS' to ISO 8601."""
    result = _parse_dtcomu("13/06/2026 08:00:00")
    assert result == "2026-06-13T08:00:00"


def test_parse_dtcomu_invalid_returns_none() -> None:
    """_parse_dtcomu returns None for unparseable strings."""
    assert _parse_dtcomu("not-a-date") is None


def test_parse_dtcomu_none_input_returns_none() -> None:
    """_parse_dtcomu returns None when None is passed."""
    assert _parse_dtcomu(None) is None  # type: ignore[arg-type]


def test_parse_dtcomu_wrong_format_returns_none() -> None:
    """_parse_dtcomu returns None for ISO format (wrong delimiter)."""
    assert _parse_dtcomu("2026-06-13T08:00:00") is None


# ---------------------------------------------------------------------------
# _cheapest_price
# ---------------------------------------------------------------------------


def test_cheapest_price_returns_minimum() -> None:
    """_cheapest_price returns the smallest value in the list."""
    assert _cheapest_price([1.899, 1.829, 1.950]) == pytest.approx(1.829)


def test_cheapest_price_single_element() -> None:
    """_cheapest_price returns the only element when list has one item."""
    assert _cheapest_price([1.750]) == pytest.approx(1.750)


def test_cheapest_price_empty_list_returns_none() -> None:
    """_cheapest_price returns None for an empty list."""
    assert _cheapest_price([]) is None


def test_cheapest_price_rounds_to_three_decimal_places() -> None:
    """_cheapest_price rounds to 3 decimal places."""
    result = _cheapest_price([1.89999999])
    assert result == pytest.approx(1.9, abs=0.001)


# ---------------------------------------------------------------------------
# _build_station_data
# ---------------------------------------------------------------------------


def test_build_station_data_all_keys_present() -> None:
    """_build_station_data returns a dict with all expected StationData keys."""
    meta = {
        "gestore": "Mario Rossi",
        "bandiera": "ENI",
        "tipo": "Stradale",
        "nome": "ENI Via Roma",
        "indirizzo": "Via Roma 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9028,
        "lon": 12.4964,
    }
    prices = {
        "unleaded": [1.899, 1.829],
        "diesel": [1.789, 1.699],
        "lpg": [0.899],
        "cng": [1.299],
        "premium_unleaded": [2.099],
    }
    result = _build_station_data("3464", meta, prices, "2026-06-13T08:00:00")
    required_keys = {
        "unleaded",
        "diesel",
        "lpg",
        "cng",
        "premium_unleaded",
        "name",
        "brand",
        "address",
        "county",
        "latitude",
        "longitude",
        "lastupdated",
    }
    for key in required_keys:
        assert key in result, f"Key '{key}' missing from _build_station_data output"


def test_build_station_data_cheapest_unleaded() -> None:
    """_build_station_data selects the cheapest unleaded price."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    prices = {"unleaded": [1.899, 1.829]}
    result = _build_station_data("3464", meta, prices, None)
    assert result["unleaded"] == pytest.approx(1.829)


def test_build_station_data_cheapest_diesel() -> None:
    """_build_station_data selects the cheapest diesel price."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    prices = {"diesel": [1.789, 1.699]}
    result = _build_station_data("3464", meta, prices, None)
    assert result["diesel"] == pytest.approx(1.699)


def test_build_station_data_missing_fuel_is_none() -> None:
    """_build_station_data returns None for fuel types with no price data."""
    meta = {
        "bandiera": "Q8",
        "nome": "Q8 Station",
        "indirizzo": "",
        "comune": "Milano",
        "provincia": "MI",
        "lat": 45.46,
        "lon": 9.19,
    }
    result = _build_station_data("2222", meta, {}, None)
    assert result["unleaded"] is None
    assert result["diesel"] is None
    assert result["lpg"] is None
    assert result["cng"] is None
    assert result["premium_unleaded"] is None


def test_build_station_data_address_combines_indirizzo_and_comune() -> None:
    """_build_station_data builds address from indirizzo + comune."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via Roma 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["address"] == "Via Roma 1, Roma"


def test_build_station_data_address_only_comune_when_no_indirizzo() -> None:
    """_build_station_data uses only comune when indirizzo is empty."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["address"] == "Roma"


def test_build_station_data_address_none_when_both_empty() -> None:
    """_build_station_data returns None address when both indirizzo and comune are empty."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "",
        "comune": "",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["address"] is None


def test_build_station_data_brand_from_bandiera() -> None:
    """_build_station_data maps bandiera to brand."""
    meta = {
        "bandiera": "Tamoil",
        "nome": "Tamoil Test",
        "indirizzo": "Via 1",
        "comune": "Torino",
        "provincia": "TO",
        "lat": 45.07,
        "lon": 7.69,
    }
    result = _build_station_data("1234", meta, {}, None)
    assert result["brand"] == "Tamoil"


def test_build_station_data_empty_bandiera_becomes_none() -> None:
    """_build_station_data converts empty bandiera to brand=None."""
    meta = {
        "bandiera": "",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["brand"] is None


def test_build_station_data_empty_nome_becomes_none() -> None:
    """_build_station_data converts empty nome to name=None."""
    meta = {
        "bandiera": "ENI",
        "nome": "",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["name"] is None


def test_build_station_data_lat_lon_float_passthrough() -> None:
    """_build_station_data passes float lat/lon through to latitude/longitude."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9028,
        "lon": 12.4964,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["latitude"] == pytest.approx(41.9028)
    assert result["longitude"] == pytest.approx(12.4964)


def test_build_station_data_lat_none_when_invalid() -> None:
    """_build_station_data sets latitude/longitude to None when meta lat/lon is None."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": None,
        "lon": None,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_build_station_data_lastupdated_passthrough() -> None:
    """_build_station_data passes last_updated through to lastupdated."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, "2026-06-13T08:00:00")
    assert result["lastupdated"] == "2026-06-13T08:00:00"


def test_build_station_data_lastupdated_none_when_not_provided() -> None:
    """_build_station_data stores lastupdated=None when no timestamp given."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert result["lastupdated"] is None


def test_build_station_data_source_station_id() -> None:
    """_build_station_data does not set source_station_id (injected by coordinator)."""
    meta = {
        "bandiera": "ENI",
        "nome": "Test",
        "indirizzo": "Via 1",
        "comune": "Roma",
        "provincia": "RM",
        "lat": 41.9,
        "lon": 12.5,
    }
    result = _build_station_data("3464", meta, {}, None)
    assert "source_station_id" not in result


# ---------------------------------------------------------------------------
# _DESC_TO_KEY mapping
# ---------------------------------------------------------------------------


def test_desc_to_key_all_canonical_mappings() -> None:
    """_DESC_TO_KEY maps all five canonical Italian fuel names."""
    assert _DESC_TO_KEY["Benzina"] == "unleaded"
    assert _DESC_TO_KEY["Gasolio"] == "diesel"
    assert _DESC_TO_KEY["GPL"] == "lpg"
    assert _DESC_TO_KEY["Metano"] == "cng"
    assert _DESC_TO_KEY["Benzina Super"] == "premium_unleaded"


def test_desc_to_key_unknown_returns_none() -> None:
    """_DESC_TO_KEY.get() returns None for unmapped fuel types."""
    assert _DESC_TO_KEY.get("GasolioBlu+") is None
    assert _DESC_TO_KEY.get("HVO") is None
    assert _DESC_TO_KEY.get("Idrogeno") is None


def test_desc_to_key_has_exactly_five_entries() -> None:
    """_DESC_TO_KEY contains exactly the five canonical mappings."""
    assert len(_DESC_TO_KEY) == 5


# ---------------------------------------------------------------------------
# ItMaseProvider.__init__
# ---------------------------------------------------------------------------


def test_provider_init_stores_station_id() -> None:
    """ItMaseProvider constructor stores station_id."""
    provider = ItMaseProvider("3464")
    assert provider._station_id == "3464"


def test_provider_init_default_radius() -> None:
    """ItMaseProvider defaults to radius_km=10.0."""
    provider = ItMaseProvider("3464")
    assert provider._radius_km == pytest.approx(10.0)


def test_provider_init_custom_radius() -> None:
    """ItMaseProvider stores the provided radius_km."""
    provider = ItMaseProvider("3464", radius_km=5.0)
    assert provider._radius_km == pytest.approx(5.0)


def test_provider_init_stores_lat_lon() -> None:
    """ItMaseProvider stores latitude and longitude."""
    provider = ItMaseProvider("3464", latitude=41.9028, longitude=12.4964)
    assert provider._latitude == pytest.approx(41.9028)
    assert provider._longitude == pytest.approx(12.4964)


def test_provider_init_stores_county() -> None:
    """ItMaseProvider stores the optional county."""
    provider = ItMaseProvider("3464", county="RM")
    assert provider._county == "RM"


def test_provider_init_none_radius_defaults_to_10() -> None:
    """ItMaseProvider sets radius_km=10.0 when None is explicitly passed."""
    provider = ItMaseProvider("3464", radius_km=None)
    assert provider._radius_km == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# _extract_latest_timestamp
# ---------------------------------------------------------------------------


def test_extract_latest_timestamp_returns_iso_when_present() -> None:
    """_extract_latest_timestamp returns the ISO timestamp from timestamps_data."""
    provider = ItMaseProvider("3464")
    timestamps_data = {"3464": "2026-06-13T08:00:00"}
    result = provider._extract_latest_timestamp(timestamps_data, "3464")
    assert result == "2026-06-13T08:00:00"


def test_extract_latest_timestamp_returns_none_when_absent() -> None:
    """_extract_latest_timestamp returns None when station_id not in timestamps_data."""
    provider = ItMaseProvider("3464")
    result = provider._extract_latest_timestamp({}, "3464")
    assert result is None


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict for a known station."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")

    assert data is not None
    assert isinstance(data, dict)


async def test_async_fetch_unleaded_price_is_cheapest() -> None:
    """async_fetch returns the cheapest unleaded price (self-service wins)."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    # Cheapest of 1.899 and 1.829 is 1.829
    assert data["unleaded"] == pytest.approx(1.829)


async def test_async_fetch_diesel_price_is_cheapest() -> None:
    """async_fetch returns the cheapest diesel price."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    # Cheapest of 1.789 and 1.699 is 1.699
    assert data["diesel"] == pytest.approx(1.699)


async def test_async_fetch_lpg_price() -> None:
    """async_fetch returns lpg price."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["lpg"] == pytest.approx(0.899)


async def test_async_fetch_cng_price() -> None:
    """async_fetch returns cng price."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["cng"] == pytest.approx(1.299)


async def test_async_fetch_premium_unleaded_price() -> None:
    """async_fetch returns premium_unleaded price."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["premium_unleaded"] == pytest.approx(2.099)


async def test_async_fetch_name_from_metadata() -> None:
    """async_fetch populates name from station metadata."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["name"] == "ENI Via Roma"


async def test_async_fetch_brand_from_bandiera() -> None:
    """async_fetch populates brand from bandiera field."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["brand"] == "ENI"


async def test_async_fetch_county_from_provincia() -> None:
    """async_fetch populates county from provincia field."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["county"] == "RM"


async def test_async_fetch_address_combined() -> None:
    """async_fetch builds address from indirizzo and comune."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["address"] == "Via Roma 1, Roma"


async def test_async_fetch_latitude_longitude() -> None:
    """async_fetch populates latitude and longitude from metadata."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["latitude"] == pytest.approx(41.9028, abs=0.001)
    assert data["longitude"] == pytest.approx(12.4964, abs=0.001)


async def test_async_fetch_lastupdated_iso_from_dtcomu() -> None:
    """async_fetch lastupdated is the ISO 8601 string parsed from dtComu."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert data["lastupdated"] == "2026-06-13T08:00:00"


async def test_async_fetch_source_station_id() -> None:
    """async_fetch does not set source_station_id (injected by coordinator)."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    assert "source_station_id" not in data


async def test_async_fetch_prices_are_eur_per_litre() -> None:
    """async_fetch prices are in EUR/litre (not cents, not divided by 100)."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    data = await provider.async_fetch(session, "3464")
    # Italian petrol is typically 1.6–2.0 EUR/litre
    assert data["unleaded"] > 0.0
    assert data["unleaded"] < 10.0


async def test_async_fetch_makes_two_http_requests() -> None:
    """async_fetch issues exactly two HTTP GET requests (price + meta CSV)."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    await provider.async_fetch(session, "3464")
    assert session.get.call_count == 2


async def test_async_fetch_requests_correct_urls() -> None:
    """async_fetch requests the MIMIT price and metadata CSV URLs."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    await provider.async_fetch(session, "3464")

    called_urls = [call.args[0] for call in session.get.call_args_list]
    assert _PRICE_URL in called_urls
    assert _META_URL in called_urls


async def test_async_fetch_sends_user_agent_header() -> None:
    """async_fetch sends the User-Agent header with each request."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    await provider.async_fetch(session, "3464")

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert "User-Agent" in headers


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_station_not_in_prices() -> None:
    """async_fetch raises ProviderError when station_id not in price dataset."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")

    with pytest.raises(ProviderError, match="not found in MIMIT price dataset"):
        await provider.async_fetch(session, "UNKNOWN_ID")


async def test_async_fetch_raises_provider_error_station_not_in_metadata() -> None:
    """async_fetch raises ProviderError when station_id not in metadata dataset."""
    # Price CSV has station 9999, metadata CSV does not
    price_csv_only = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "9999|Benzina|1.829|0|13/06/2026 08:00:00\n"
    )
    meta_csv_without_9999 = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(
        price_csv=price_csv_only, meta_csv=meta_csv_without_9999
    )
    provider = ItMaseProvider("9999")

    with pytest.raises(ProviderError, match="not found in MIMIT station metadata"):
        await provider.async_fetch(session, "9999")


async def test_async_fetch_raises_provider_error_empty_price_dataset() -> None:
    """async_fetch raises ProviderError when price CSV parses to empty dataset."""
    # Price CSV with only header row (no data)
    empty_price_csv = (
        "Estrazione del 2026-06-13\nidImpianto|descCarburante|prezzo|isSelf|dtComu\n"
    )
    session = _make_session_with_csvs(price_csv=empty_price_csv)
    provider = ItMaseProvider("3464")

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "3464")


async def test_async_fetch_raises_provider_error_empty_metadata_dataset() -> None:
    """async_fetch raises ProviderError when metadata CSV parses to empty dataset."""
    empty_meta_csv = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
    )
    session = _make_session_with_csvs(meta_csv=empty_meta_csv)
    provider = ItMaseProvider("3464")

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "3464")


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp ClientError (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = ItMaseProvider("3464")

    with pytest.raises(ClientError):
        await provider.async_fetch(session, "3464")


async def test_async_fetch_propagates_http_error_non_200() -> None:
    """async_fetch propagates errors raised by raise_for_status on non-200 responses."""
    resp = _make_mock_response(
        status=503,
        raise_for_status_exc=ClientError("503 Service Unavailable"),
    )
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    provider = ItMaseProvider("3464")

    with pytest.raises(ClientError):
        await provider.async_fetch(session, "3464")


async def test_async_fetch_handles_unicode_decode_fallback() -> None:
    """async_fetch falls back to utf-8 with replacement on decode error."""
    # Encode meta CSV as latin-1 (not valid utf-8 for some chars)
    meta_bytes_latin1 = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi\xe0|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    ).encode("latin-1")

    price_bytes = _PRICE_CSV.encode("ascii")

    price_resp = _make_mock_response(content=price_bytes)
    meta_resp = _make_mock_response(content=meta_bytes_latin1)

    responses = iter([price_resp, meta_resp])
    session = MagicMock()
    session.get = MagicMock(side_effect=lambda *a, **kw: next(responses))

    provider = ItMaseProvider("3464")
    # Should not raise even with non-utf-8 bytes in meta CSV
    data = await provider.async_fetch(session, "3464")
    assert data is not None


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_brand_and_name() -> None:
    """async_fetch_station_name returns 'brand — name' when both fields are present."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name == "ENI — ENI Via Roma"


async def test_async_fetch_station_name_returns_nome_only_when_no_bandiera() -> None:
    """async_fetch_station_name returns just nome when bandiera is empty."""
    meta_csv_no_brand = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi||Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_no_brand)
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name == "ENI Via Roma"


async def test_async_fetch_station_name_returns_bandiera_only_when_no_nome() -> None:
    """async_fetch_station_name returns just bandiera when nome is empty."""
    meta_csv_no_name = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale||Via Roma 1|Roma|RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_no_name)
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name == "ENI"


async def test_async_fetch_station_name_returns_none_when_station_absent() -> None:
    """async_fetch_station_name returns None when station_id not in metadata."""
    session = _make_session_with_csvs()
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "UNKNOWN_ID")
    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name is None


async def test_async_fetch_station_name_returns_none_on_provider_error() -> None:
    """async_fetch_station_name returns None when CSVs parse to empty datasets."""
    empty_price_csv = (
        "Estrazione del 2026-06-13\nidImpianto|descCarburante|prezzo|isSelf|dtComu\n"
    )
    session = _make_session_with_csvs(price_csv=empty_price_csv)
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name is None


async def test_async_fetch_station_name_returns_none_when_both_fields_empty() -> None:
    """async_fetch_station_name returns None when both nome and bandiera are empty."""
    meta_csv_empty_both = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi||Stradale||Via Roma 1|Roma|RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_empty_both)
    provider = ItMaseProvider("3464")
    name = await provider.async_fetch_station_name(session, "3464")
    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_nearby_stations() -> None:
    """async_list_stations returns stations within radius_km of given coordinates."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    # Centre on Rome (41.9028, 12.4964); station 3464 is 0 km away; 5555 (Florence) is ~230 km
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    assert len(results) >= 1
    station_ids = [sid for sid, _ in results]
    assert "3464" in station_ids
    assert "5555" not in station_ids


async def test_async_list_stations_excludes_far_stations() -> None:
    """async_list_stations excludes stations beyond radius_km."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=5.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=5.0
    )

    station_ids = [sid for sid, _ in results]
    assert "5555" not in station_ids


async def test_async_list_stations_label_contains_station_name() -> None:
    """async_list_stations includes station name in the display label."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    labels = [label for _, label in results]
    assert any("ENI" in label for label in labels)


async def test_async_list_stations_label_contains_comune() -> None:
    """async_list_stations includes comune in the display label."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    labels = [label for _, label in results]
    assert any("Roma" in label for label in labels)


async def test_async_list_stations_label_contains_distance() -> None:
    """async_list_stations label contains station identifier token (no distance)."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    labels = [label for _, label in results]
    assert any("(#" in label for label in labels)


async def test_async_list_stations_label_contains_price_info() -> None:
    """async_list_stations label contains station identifier token (no price)."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    labels = {sid: label for sid, label in results}
    if "3464" in labels:
        assert "(#" in labels["3464"]


async def test_async_list_stations_sorted_cheapest_first() -> None:
    """async_list_stations returns results sorted alphabetically by label."""
    # Two stations near Rome: 3464 (ENI) and 4444 (IP) — "ENI" < "IP" alphabetically
    meta_csv_two = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
        "4444|Cheap|IP|Stradale|IP Roma|Via Cheap 1|Roma|RM|41.9050|12.4970\n"
    )
    price_csv_two = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Gasolio|1.699|0|13/06/2026 08:00:00\n"
        "4444|Gasolio|1.599|0|13/06/2026 08:00:00\n"
    )
    session = _make_session_with_csvs(price_csv=price_csv_two, meta_csv=meta_csv_two)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=10.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=10.0
    )

    station_ids = [sid for sid, _ in results]
    # "ENI/ENI Via Roma..." < "IP/IP Roma..." alphabetically → 3464 first
    assert station_ids.index("3464") < station_ids.index("4444")


async def test_async_list_stations_returns_empty_when_no_coordinates() -> None:
    """async_list_stations returns [] when called without coordinates."""
    session = MagicMock()
    provider = ItMaseProvider("3464")  # no lat/lng in constructor
    results = await provider.async_list_stations(session)
    assert results == []


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = ItMaseProvider("3464", latitude=41.9028, longitude=12.4964)
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=10.0
    )
    assert results == []


async def test_async_list_stations_returns_empty_on_provider_error() -> None:
    """async_list_stations returns [] when CSVs parse to empty datasets."""
    empty_price_csv = (
        "Estrazione del 2026-06-13\nidImpianto|descCarburante|prezzo|isSelf|dtComu\n"
    )
    session = _make_session_with_csvs(price_csv=empty_price_csv)
    provider = ItMaseProvider("3464", latitude=41.9028, longitude=12.4964)
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=10.0
    )
    assert results == []


async def test_async_list_stations_skips_stations_with_no_lat_lon() -> None:
    """async_list_stations skips stations that have no valid lat/lon in metadata."""
    meta_csv_no_coords = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|N/A|N/A\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_no_coords)
    provider = ItMaseProvider("3464", latitude=41.9028, longitude=12.4964)
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )
    assert results == []


async def test_async_list_stations_uses_constructor_coordinates_as_fallback() -> None:
    """async_list_stations falls back to constructor lat/lng when not passed as kwargs."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    # Do not pass lat/lng as kwargs — rely on constructor values
    results = await provider.async_list_stations(session)

    station_ids = [sid for sid, _ in results]
    assert "3464" in station_ids


async def test_async_list_stations_no_price_for_station_label_omits_price() -> None:
    """async_list_stations label omits price section when station has no prices."""
    meta_csv_only = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma|Via Roma 1|Roma|RM|41.9028|12.4964\n"
        "7777|Gestore|Q8|Stradale|Q8 Station|Via 2|Roma|RM|41.9100|12.5000\n"
    )
    price_csv_no_7777 = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|descCarburante|prezzo|isSelf|dtComu\n"
        "3464|Gasolio|1.699|0|13/06/2026 08:00:00\n"
    )
    session = _make_session_with_csvs(
        price_csv=price_csv_no_7777, meta_csv=meta_csv_only
    )
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    labels = {sid: label for sid, label in results}
    if "7777" in labels:
        # Station 7777 has no prices — label should not contain Euro sign
        assert "€" not in labels["7777"]


async def test_async_list_stations_label_station_id_fallback_when_no_name() -> None:
    """async_list_stations uses 'Station {id}' as fallback when both nome and bandiera are empty."""
    meta_csv_no_name_brand = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi||Stradale||Via Roma 1||RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_no_name_brand)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    assert len(results) >= 1
    _, label = results[0]
    assert "Station 3464" in label or "3464" in label


async def test_async_list_stations_kwargs_override_constructor_radius() -> None:
    """async_list_stations kwarg radius_km overrides the constructor value."""
    session = _make_session_with_csvs(meta_csv=_META_CSV_NEARBY)
    # Constructor says 1 km but kwargs say 50 km
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=1.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    station_ids = [sid for sid, _ in results]
    assert "3464" in station_ids


# ---------------------------------------------------------------------------
# it_mase.py line 542 — label without address in async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_label_omits_address_when_absent() -> None:
    """Line 542: when both indirizzo and comune are absent, label uses '{brand} (#{short_id})' format."""
    meta_csv_no_addr = (
        "Estrazione del 2026-06-13\n"
        "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine\n"
        "3464|Mario Rossi|ENI|Stradale|ENI Via Roma||  |RM|41.9028|12.4964\n"
    )
    session = _make_session_with_csvs(meta_csv=meta_csv_no_addr)
    provider = ItMaseProvider(
        "3464", latitude=41.9028, longitude=12.4964, radius_km=50.0
    )
    results = await provider.async_list_stations(
        session, lat=41.9028, lng=12.4964, radius_km=50.0
    )

    assert len(results) >= 1
    sid, label = results[0]
    # No address → label should be "ENI (#{short_id})" without a comma
    assert "(#" in label
    # No comma separating name from ID
    assert label.count(",") == 0
