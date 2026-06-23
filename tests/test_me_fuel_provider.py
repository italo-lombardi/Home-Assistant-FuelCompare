"""Tests for MeFuelProvider (data.gov.me — Montenegro government fuel prices)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

openpyxl = pytest.importorskip("openpyxl")

from aiohttp import ClientError  # noqa: E402

from custom_components.fuelcompare_ie.providers.base import ProviderError  # noqa: E402
from custom_components.fuelcompare_ie.providers.me_fuel import (  # noqa: E402
    MeFuelProvider,
    _CKAN_SEARCH_URL,
    _COL_EURODIESEL,
    _COL_EUROSUPER_95,
    _COL_EUROSUPER_98,
    _COL_LOZ_ULJE,
    _COL_TO_KEY,
    _HEADERS,
    _PRICE_ROW,
    _STATION_ID_ME,
    _parse_price,
    _parse_prices_from_description,
    _parse_xlsx,
)


# ---------------------------------------------------------------------------
# CKAN API response fixtures
# ---------------------------------------------------------------------------

_XLSX_URL = "https://data.gov.me/dataset/gorivo/download/gorivo-2026-05.xlsx"
_MODIFIED = "2026-05-20T10:00:00"

_CKAN_SUCCESS_PAYLOAD: dict = {
    "success": True,
    "result": {
        "count": 1,
        "results": [
            {
                "id": "abc123",
                "metadata_modified": _MODIFIED,
                "resources": [
                    {
                        "format": "XLSX",
                        "url": _XLSX_URL,
                    }
                ],
            }
        ],
    },
}

_CKAN_NO_XLSX_PAYLOAD: dict = {
    "success": True,
    "result": {
        "count": 1,
        "results": [
            {
                "id": "abc123",
                "metadata_modified": _MODIFIED,
                "resources": [
                    {
                        "format": "PDF",
                        "url": "https://data.gov.me/dataset/gorivo/download/gorivo.pdf",
                    }
                ],
            }
        ],
    },
}

_CKAN_EMPTY_RESULTS_PAYLOAD: dict = {
    "success": True,
    "result": {"count": 0, "results": []},
}

_CKAN_FAILURE_PAYLOAD: dict = {
    "success": False,
    "result": {},
}

# Prices used in the synthetic XLSX fixture (matches confirmed May 2026 values)
_PRICE_E95 = 1.65
_PRICE_E98 = 1.68
_PRICE_EDIESEL = 1.69
_PRICE_LOZ = 1.73


# ---------------------------------------------------------------------------
# Synthetic XLSX builder
# ---------------------------------------------------------------------------


def _make_xlsx_bytes(
    e95: float | None = _PRICE_E95,
    e98: float | None = _PRICE_E98,
    diesel: float | None = _PRICE_EDIESEL,
    loz: float | None = _PRICE_LOZ,
    num_rows: int = 52,
) -> bytes:
    """Build a synthetic XLSX workbook with the expected layout.

    Creates a workbook whose active sheet has ``num_rows`` rows.
    When ``num_rows >= _PRICE_ROW`` (28), row 28 gets the supplied fuel
    price values in columns D-G.  When ``num_rows < _PRICE_ROW`` the MP
    row is intentionally omitted so the short-sheet path can be tested.
    """
    wb = openpyxl.Workbook()
    ws = wb.active

    # Anchor each row up to num_rows so openpyxl's max_row reflects
    # the intended sheet size.
    for r in range(1, num_rows + 1):
        ws.cell(row=r, column=1, value=f"row{r}")

    # Only write the MP row when the sheet is supposed to contain it.
    if num_rows >= _PRICE_ROW:
        ws.cell(row=_PRICE_ROW, column=1, value="MP")
        if e95 is not None:
            ws.cell(row=_PRICE_ROW, column=_COL_EUROSUPER_95, value=e95)
        if e98 is not None:
            ws.cell(row=_PRICE_ROW, column=_COL_EUROSUPER_98, value=e98)
        if diesel is not None:
            ws.cell(row=_PRICE_ROW, column=_COL_EURODIESEL, value=diesel)
        if loz is not None:
            ws.cell(row=_PRICE_ROW, column=_COL_LOZ_ULJE, value=loz)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
    read_data: bytes | None = None,
    raise_on_raise_for_status: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    mock_resp.read = AsyncMock(return_value=read_data if read_data is not None else b"")
    if raise_on_raise_for_status is not None:
        mock_resp.raise_for_status = MagicMock(side_effect=raise_on_raise_for_status)
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() call cycles through *responses*."""
    session = MagicMock()
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        return next(call_iter)

    session.get = MagicMock(side_effect=_get)
    return session


def _default_provider() -> MeFuelProvider:
    """Return a MeFuelProvider with default settings."""
    return MeFuelProvider(station_id=_STATION_ID_ME)


# ---------------------------------------------------------------------------
# Provider metadata tests
# ---------------------------------------------------------------------------


def test_provider_country_is_me() -> None:
    """MeFuelProvider.COUNTRY is 'ME'."""
    assert MeFuelProvider.COUNTRY == "ME"


def test_provider_key_is_me_fuel() -> None:
    """MeFuelProvider.PROVIDER_KEY is 'me_fuel'."""
    assert MeFuelProvider.PROVIDER_KEY == "me_fuel"


def test_provider_label_contains_montenegro() -> None:
    """MeFuelProvider.LABEL contains 'Montenegro'."""
    assert "Montenegro" in MeFuelProvider.LABEL


def test_provider_config_mode_is_location() -> None:
    """CONFIG_MODE is 'location' (national average, no station selection)."""
    assert MeFuelProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode_is_location_search() -> None:
    """STATION_LOOKUP_MODE is 'location_search'."""
    assert MeFuelProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval_is_at_least_3600() -> None:
    """POLL_INTERVAL_SECONDS >= 3600 (data updates weekly/bi-weekly)."""
    assert MeFuelProvider.POLL_INTERVAL_SECONDS >= 3600


# ---------------------------------------------------------------------------
# Provider capabilities tests
# ---------------------------------------------------------------------------


def test_capabilities_include_diesel() -> None:
    assert "diesel" in MeFuelProvider.CAPABILITIES


def test_capabilities_include_unleaded() -> None:
    assert "unleaded" in MeFuelProvider.CAPABILITIES


def test_capabilities_include_premium_unleaded() -> None:
    assert "premium_unleaded" in MeFuelProvider.CAPABILITIES


def test_capabilities_include_kerosene() -> None:
    assert "kerosene" in MeFuelProvider.CAPABILITIES


def test_capabilities_include_lastupdated() -> None:
    assert "lastupdated" in MeFuelProvider.CAPABILITIES


def test_capabilities_include_coordinator_sentinels() -> None:
    caps = MeFuelProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


def test_constructor_default_station_id() -> None:
    """Default station_id is the country code 'ME'."""
    p = MeFuelProvider()
    assert p._station_id == "ME"


def test_constructor_extra_kwargs_do_not_raise() -> None:
    """Constructor absorbs unknown kwargs without raising."""
    MeFuelProvider(station_id="ME", county="Podgorica", unknown_param="x")


# ---------------------------------------------------------------------------
# Module constants tests
# ---------------------------------------------------------------------------


def test_ckan_search_url_points_to_data_gov_me() -> None:
    assert "data.gov.me" in _CKAN_SEARCH_URL
    assert "gorivo" in _CKAN_SEARCH_URL
    assert _CKAN_SEARCH_URL.startswith("https://")


def test_price_row_is_28() -> None:
    """_PRICE_ROW is 28 as documented in the XLSX layout."""
    assert _PRICE_ROW == 28


def test_col_to_key_maps_all_four_fuel_types() -> None:
    """_COL_TO_KEY covers all four fuel-type columns D-G."""
    assert _COL_TO_KEY[_COL_EUROSUPER_95] == "unleaded"
    assert _COL_TO_KEY[_COL_EUROSUPER_98] == "premium_unleaded"
    assert _COL_TO_KEY[_COL_EURODIESEL] == "diesel"
    assert _COL_TO_KEY[_COL_LOZ_ULJE] == "kerosene"


def test_station_id_me_constant() -> None:
    """_STATION_ID_ME is the string 'ME'."""
    assert _STATION_ID_ME == "ME"


def test_headers_include_user_agent() -> None:
    assert "User-Agent" in _HEADERS and _HEADERS["User-Agent"]


# ---------------------------------------------------------------------------
# _parse_price unit tests
# ---------------------------------------------------------------------------


def test_parse_price_normal_eur_per_litre() -> None:
    """_parse_price returns rounded float for normal value."""
    assert _parse_price(1.65) == pytest.approx(1.65)


def test_parse_price_rounds_to_3dp() -> None:
    assert _parse_price(1.65499) == pytest.approx(1.655)


def test_parse_price_none_returns_none() -> None:
    assert _parse_price(None) is None


def test_parse_price_zero_returns_none() -> None:
    assert _parse_price(0) is None


def test_parse_price_negative_returns_none() -> None:
    assert _parse_price(-1.5) is None


def test_parse_price_string_numeric() -> None:
    assert _parse_price("1.65") == pytest.approx(1.65)


def test_parse_price_non_numeric_string_returns_none() -> None:
    assert _parse_price("N/A") is None


def test_parse_price_cents_guard_above_10() -> None:
    """Values >10 are divided by 100 (guard for accidental cents)."""
    assert _parse_price(165.0) == pytest.approx(1.65)


def test_parse_price_integer_input() -> None:
    assert _parse_price(2) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _parse_xlsx unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_xlsx_success_all_four_fuel_types() -> None:
    """_parse_xlsx extracts all four fuel type prices from row 28."""
    data = await _parse_xlsx(_make_xlsx_bytes())
    assert data["unleaded"] == pytest.approx(_PRICE_E95)
    assert data["premium_unleaded"] == pytest.approx(_PRICE_E98)
    assert data["diesel"] == pytest.approx(_PRICE_EDIESEL)
    assert data["kerosene"] == pytest.approx(_PRICE_LOZ)


@pytest.mark.asyncio
async def test_parse_xlsx_returns_none_for_missing_cells() -> None:
    """_parse_xlsx returns None for fuel types whose cells are blank."""
    data = await _parse_xlsx(_make_xlsx_bytes(e95=None, e98=None))
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["diesel"] == pytest.approx(_PRICE_EDIESEL)
    assert data["kerosene"] == pytest.approx(_PRICE_LOZ)


@pytest.mark.asyncio
async def test_parse_xlsx_raises_provider_error_for_invalid_bytes() -> None:
    """_parse_xlsx raises ProviderError when content is not a valid XLSX."""
    with pytest.raises(ProviderError, match="MeFuel"):
        await _parse_xlsx(b"this is not an xlsx file at all")


@pytest.mark.asyncio
async def test_parse_xlsx_short_sheet_returns_none_prices() -> None:
    """_parse_xlsx returns all-None prices when the sheet has < 28 rows."""
    data = await _parse_xlsx(_make_xlsx_bytes(num_rows=10))
    assert data["unleaded"] is None
    assert data["premium_unleaded"] is None
    assert data["diesel"] is None
    assert data["kerosene"] is None


@pytest.mark.asyncio
async def test_parse_xlsx_all_capability_keys_present() -> None:
    """_parse_xlsx result contains exactly the four expected keys."""
    data = await _parse_xlsx(_make_xlsx_bytes())
    assert set(data.keys()) == {"unleaded", "premium_unleaded", "diesel", "kerosene"}


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_success_returns_all_fuel_prices() -> None:
    """async_fetch returns a populated StationData with all four prices."""
    xlsx_bytes = _make_xlsx_bytes()
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(200, read_data=xlsx_bytes)
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID_ME)

    assert data["unleaded"] == pytest.approx(_PRICE_E95)
    assert data["premium_unleaded"] == pytest.approx(_PRICE_E98)
    assert data["diesel"] == pytest.approx(_PRICE_EDIESEL)
    assert data["kerosene"] == pytest.approx(_PRICE_LOZ)


@pytest.mark.asyncio
async def test_async_fetch_success_populates_lastupdated() -> None:
    """async_fetch returns lastupdated from the CKAN metadata_modified field."""
    xlsx_bytes = _make_xlsx_bytes()
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(200, read_data=xlsx_bytes)
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID_ME)

    assert data["lastupdated"] == _MODIFIED


@pytest.mark.asyncio
async def test_async_fetch_source_station_id_is_me() -> None:
    """async_fetch sets source_station_id to 'ME'."""
    xlsx_bytes = _make_xlsx_bytes()
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(200, read_data=xlsx_bytes)
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID_ME)

    assert data["source_station_id"] == "ME"


@pytest.mark.asyncio
async def test_async_fetch_makes_exactly_two_requests() -> None:
    """async_fetch makes exactly 2 HTTP requests: CKAN search + XLSX download."""
    xlsx_bytes = _make_xlsx_bytes()
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(200, read_data=xlsx_bytes)
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    await provider.async_fetch(session, _STATION_ID_ME)

    assert session.get.call_count == 2


# ---------------------------------------------------------------------------
# async_fetch — CKAN error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_raises_on_ckan_http_error() -> None:
    """async_fetch raises ProviderError when the CKAN API call fails."""
    ckan_resp = _make_mock_response(
        503,
        raise_on_raise_for_status=ClientError("service unavailable"),
    )
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="MeFuel"):
        await provider.async_fetch(session, _STATION_ID_ME)


@pytest.mark.asyncio
async def test_async_fetch_raises_on_ckan_network_error() -> None:
    """async_fetch raises ProviderError on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID_ME)


@pytest.mark.asyncio
async def test_async_fetch_raises_when_ckan_returns_success_false() -> None:
    """async_fetch raises ProviderError when CKAN returns success=false."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_FAILURE_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="success=false"):
        await provider.async_fetch(session, _STATION_ID_ME)


@pytest.mark.asyncio
async def test_async_fetch_raises_when_ckan_results_empty() -> None:
    """async_fetch raises ProviderError when CKAN returns no datasets."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_EMPTY_RESULTS_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="no datasets"):
        await provider.async_fetch(session, _STATION_ID_ME)


@pytest.mark.asyncio
async def test_async_fetch_raises_when_no_xlsx_resource_found() -> None:
    """async_fetch raises ProviderError when dataset has no XLSX and no parseable description."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_NO_XLSX_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="no XLSX resource found"):
        await provider.async_fetch(session, _STATION_ID_ME)


# ---------------------------------------------------------------------------
# async_fetch — XLSX download error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_raises_on_xlsx_download_http_error() -> None:
    """async_fetch raises ProviderError when XLSX download returns HTTP error."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(
        404,
        raise_on_raise_for_status=ClientError("not found"),
    )
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="MeFuel"):
        await provider.async_fetch(session, _STATION_ID_ME)


@pytest.mark.asyncio
async def test_async_fetch_raises_on_invalid_xlsx_content() -> None:
    """async_fetch raises ProviderError when the downloaded file is not XLSX."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    xlsx_resp = _make_mock_response(200, read_data=b"<html>not an xlsx</html>")
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID_ME)


# ---------------------------------------------------------------------------
# async_fetch — XLSX URL fallback (url ends in .xlsx without format=XLSX)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_falls_back_to_url_extension_when_format_missing() -> None:
    """async_fetch finds XLSX by .xlsx URL extension when format field is blank."""
    payload_no_format: dict = {
        "success": True,
        "result": {
            "count": 1,
            "results": [
                {
                    "id": "abc456",
                    "metadata_modified": _MODIFIED,
                    "resources": [
                        {
                            "format": "",  # format field absent / blank
                            "url": _XLSX_URL,  # URL ends in .xlsx
                        }
                    ],
                }
            ],
        },
    }
    xlsx_bytes = _make_xlsx_bytes()
    ckan_resp = _make_mock_response(200, json_data=payload_no_format)
    xlsx_resp = _make_mock_response(200, read_data=xlsx_bytes)
    session = _make_session(ckan_resp, xlsx_resp)

    provider = _default_provider()
    data = await provider.async_fetch(session, _STATION_ID_ME)

    assert data["diesel"] == pytest.approx(_PRICE_EDIESEL)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fetch_station_name_always_returns_none() -> None:
    """async_fetch_station_name always returns None (location-mode provider)."""
    session = MagicMock()
    provider = _default_provider()
    name = await provider.async_fetch_station_name(session, _STATION_ID_ME)
    assert name is None


@pytest.mark.asyncio
async def test_async_fetch_station_name_makes_no_requests() -> None:
    """async_fetch_station_name does not call session.get."""
    session = MagicMock()
    provider = _default_provider()
    await provider.async_fetch_station_name(session, _STATION_ID_ME)
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_stations_returns_single_entry() -> None:
    """async_list_stations returns exactly one entry for the national average."""
    session = MagicMock()
    provider = _default_provider()
    result = await provider.async_list_stations(session)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_async_list_stations_station_id_is_me() -> None:
    """async_list_stations returns station_id 'ME'."""
    session = MagicMock()
    provider = _default_provider()
    result = await provider.async_list_stations(session)

    station_id, _label = result[0]
    assert station_id == "ME"


@pytest.mark.asyncio
async def test_async_list_stations_label_contains_montenegro() -> None:
    """async_list_stations label mentions Montenegro."""
    session = MagicMock()
    provider = _default_provider()
    result = await provider.async_list_stations(session)

    _sid, label = result[0]
    assert "Montenegro" in label


@pytest.mark.asyncio
async def test_async_list_stations_makes_no_requests() -> None:
    """async_list_stations does not make any HTTP requests."""
    session = MagicMock()
    provider = _default_provider()
    await provider.async_list_stations(session, lat=42.5, lng=19.3, radius_km=50.0)
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_xlsx_url — unit tests via internal helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_xlsx_url_returns_url_and_modified() -> None:
    """_fetch_xlsx_url returns (xlsx_url, metadata_modified) on success."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_SUCCESS_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    url, modified, _fallback = await provider._fetch_xlsx_url(session)

    assert url == _XLSX_URL
    assert modified == _MODIFIED


@pytest.mark.asyncio
async def test_fetch_xlsx_url_raises_on_http_error() -> None:
    """_fetch_xlsx_url raises ProviderError on HTTP failure."""
    ckan_resp = _make_mock_response(
        500,
        raise_on_raise_for_status=ClientError("server error"),
    )
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider._fetch_xlsx_url(session)


@pytest.mark.asyncio
async def test_fetch_xlsx_url_raises_when_no_results() -> None:
    """_fetch_xlsx_url raises ProviderError when CKAN returns no results."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_EMPTY_RESULTS_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider._fetch_xlsx_url(session)


@pytest.mark.asyncio
async def test_fetch_xlsx_url_raises_when_no_xlsx_resource() -> None:
    """_fetch_xlsx_url returns (None, modified, description) when no XLSX resource found."""
    ckan_resp = _make_mock_response(200, json_data=_CKAN_NO_XLSX_PAYLOAD)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    url, modified, _desc = await provider._fetch_xlsx_url(session)
    assert url is None
    assert modified == _MODIFIED


# ---------------------------------------------------------------------------
# _download_xlsx — unit tests via internal helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_xlsx_returns_bytes() -> None:
    """_download_xlsx returns the raw bytes from the HTTP response."""
    expected = b"PK\x03\x04fake xlsx content"
    xlsx_resp = _make_mock_response(200, read_data=expected)
    session = _make_session(xlsx_resp)

    provider = _default_provider()
    result = await provider._download_xlsx(session, _XLSX_URL)

    assert result == expected


@pytest.mark.asyncio
async def test_download_xlsx_raises_on_http_error() -> None:
    """_download_xlsx raises ProviderError on HTTP failure."""
    xlsx_resp = _make_mock_response(
        403,
        raise_on_raise_for_status=ClientError("forbidden"),
    )
    session = _make_session(xlsx_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="MeFuel"):
        await provider._download_xlsx(session, _XLSX_URL)


@pytest.mark.asyncio
async def test_download_xlsx_raises_on_network_error() -> None:
    """_download_xlsx raises ProviderError on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("timeout"))

    provider = _default_provider()
    with pytest.raises(ProviderError):
        await provider._download_xlsx(session, _XLSX_URL)


# ---------------------------------------------------------------------------
# Integration: provider registered in PROVIDER_REGISTRY
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """MeFuelProvider is registered in the provider registry."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "me_fuel" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["me_fuel"] is MeFuelProvider


def test_provider_registry_get_provider_class() -> None:
    """get_provider_class returns MeFuelProvider for key 'me_fuel'."""
    from custom_components.fuelcompare_ie.providers import get_provider_class

    cls = get_provider_class("me_fuel")
    assert cls is MeFuelProvider


# ---------------------------------------------------------------------------
# New tests — covering lines 319, 368, 396, 493, 504-505
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_xlsx_url_reraises_provider_error_from_json() -> None:
    """_fetch_xlsx_url re-raises ProviderError raised by response.json() (line 319)."""
    ckan_resp = _make_mock_response(200)
    ckan_resp.json = AsyncMock(side_effect=ProviderError("inner error"))
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="inner error"):
        await provider._fetch_xlsx_url(session)


@pytest.mark.asyncio
async def test_fetch_xlsx_url_raises_on_untrusted_xlsx_host() -> None:
    """_fetch_xlsx_url raises ProviderError for a XLSX URL not on data.gov.me (line 368)."""
    untrusted_payload: dict = {
        "success": True,
        "result": {
            "count": 1,
            "results": [
                {
                    "id": "evil123",
                    "metadata_modified": _MODIFIED,
                    "resources": [
                        {
                            "format": "XLSX",
                            "url": "https://evil.example.com/malware.xlsx",
                        }
                    ],
                }
            ],
        },
    }
    ckan_resp = _make_mock_response(200, json_data=untrusted_payload)
    session = _make_session(ckan_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="untrusted host"):
        await provider._fetch_xlsx_url(session)


@pytest.mark.asyncio
async def test_download_xlsx_reraises_provider_error_from_read() -> None:
    """_download_xlsx re-raises ProviderError raised by response.read() (line 396)."""
    xlsx_resp = _make_mock_response(200)
    xlsx_resp.read = AsyncMock(side_effect=ProviderError("read provider error"))
    session = _make_session(xlsx_resp)

    provider = _default_provider()
    with pytest.raises(ProviderError, match="read provider error"):
        await provider._download_xlsx(session, _XLSX_URL)


@pytest.mark.asyncio
async def test_parse_xlsx_logs_debug_for_unparseable_cell_value() -> None:
    """_parse_xlsx emits a debug log when a cell has an unparseable non-None value (line 493)."""
    wb_tmp = openpyxl.Workbook()
    ws_tmp = wb_tmp.active
    for r in range(1, 53):
        ws_tmp.cell(row=r, column=1, value=f"row{r}")
    ws_tmp.cell(row=_PRICE_ROW, column=1, value="MP")
    ws_tmp.cell(row=_PRICE_ROW, column=_COL_EUROSUPER_95, value="N/A")
    ws_tmp.cell(row=_PRICE_ROW, column=_COL_EUROSUPER_98, value=_PRICE_E98)
    ws_tmp.cell(row=_PRICE_ROW, column=_COL_EURODIESEL, value=_PRICE_EDIESEL)
    ws_tmp.cell(row=_PRICE_ROW, column=_COL_LOZ_ULJE, value=_PRICE_LOZ)
    buf = io.BytesIO()
    wb_tmp.save(buf)

    data = await _parse_xlsx(buf.getvalue())

    assert data["unleaded"] is None
    assert data["diesel"] == pytest.approx(_PRICE_EDIESEL)


def test_parse_prices_from_description_valid() -> None:
    """_parse_prices_from_description extracts all four prices from description text."""
    desc = (
        "EUROSUPER 98 1,70 eur +0.02\n"
        "EUROSUPER 95 1,66 eur +0.02\n"
        "EURODIEZEL 1,66 eur -0.04\n"
        "LOŽ ULJE 1,59 eur -0.04"
    )
    result = _parse_prices_from_description(desc)
    assert result["premium_unleaded"] == pytest.approx(1.70)
    assert result["unleaded"] == pytest.approx(1.66)
    assert result["diesel"] == pytest.approx(1.66)
    assert result["kerosene"] == pytest.approx(1.59)


def test_parse_prices_from_description_invalid_float() -> None:
    """_parse_prices_from_description returns None when float() conversion fails."""
    # Monkeypatch re.search to return a match whose group(1) is non-numeric
    import re
    from unittest.mock import patch

    fake_match = re.match(r"(.*)", "not_a_number")

    original_search = re.search

    def patched_search(pattern, string, flags=0):
        if "EUROSUPER" in pattern and "95" in pattern:
            return fake_match
        return original_search(pattern, string, flags)

    with patch(
        "custom_components.fuelcompare_ie.providers.me_fuel.re.search",
        side_effect=patched_search,
    ):
        result = _parse_prices_from_description("EUROSUPER 95 1,66 eur")
    assert result["unleaded"] is None


def test_parse_prices_from_description_zero_price() -> None:
    """_parse_prices_from_description returns None when price is zero."""
    desc = "EUROSUPER 95 0,00 eur"
    result = _parse_prices_from_description(desc)
    assert result["unleaded"] is None


async def test_parse_xlsx_swallows_wb_close_exception(monkeypatch) -> None:
    """_parse_xlsx swallows any exception from wb.close() without raising (lines 504-505)."""
    from unittest.mock import MagicMock, patch

    mock_cell = MagicMock()
    mock_cell.value = 1.65

    mock_ws = MagicMock()
    mock_ws.max_row = _PRICE_ROW
    mock_ws.cell.return_value = mock_cell

    mock_wb = MagicMock()
    mock_wb.active = mock_ws
    mock_wb.close.side_effect = OSError("disk flush error")

    # Patch at the asyncio event loop run_in_executor level to avoid
    # coverage/monkeypatch thread interaction issues
    async def _fake_executor(executor, func, *args):
        return mock_wb

    with patch(
        "custom_components.fuelcompare_ie.providers.me_fuel.asyncio.get_running_loop"
    ) as mock_loop:
        mock_loop.return_value.run_in_executor = _fake_executor
        data = await _parse_xlsx(b"irrelevant bytes - workbook is mocked")

    assert data["unleaded"] == pytest.approx(1.65)
