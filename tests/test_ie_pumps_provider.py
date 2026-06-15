"""Tests for IePumpsProvider (pumps.ie crowd-sourced Irish fuel prices).

Coverage areas:
  - Provider metadata and CAPABILITIES
  - XML parsing (_parse_xml)
  - Station lookup helper (_find_station)
  - Float parsing helper (_parse_float)
  - Price conversion from cents-per-litre to EUR/litre
  - async_fetch success / error paths
  - async_fetch_station_name
  - async_list_stations (location_search)
  - Connection error handling
  - Field mapping correctness
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.ie_pumps import (
    IePumpsProvider,
    _build_station_data,
    _find_station,
    _parse_float,
    _parse_xml,
)


# ---------------------------------------------------------------------------
# Test fixtures / XML helpers
# ---------------------------------------------------------------------------

_STATION_ID = "1234"

# Minimal well-formed XML response with two stations.
_DIESEL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="1234" Lat="53.3498" Lng="-6.2603"
           name="Test Station" brand="Circle K"
           addr1="Main Street" addr2="Dublin"
           price="173.9" fuel="diesel" trend="stable"
           dateupdated="2025-06-08 14:23:00" dateupdatedshort="Jun 8 2025"
           Updater="user1" Zone="Dublin" County="Dublin" />
  <station ID="9999" Lat="51.8985" Lng="-8.4756"
           name="Other Station" brand="Texaco"
           addr1="Patrick Street" addr2="Cork"
           price="175.0" fuel="diesel" trend="up"
           dateupdated="2024-12-01 10:00:00" dateupdatedshort="Dec 1 2024"
           Updater="user2" Zone="Cork" County="Cork" />
</stations>
"""

_PETROL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="1234" Lat="53.3498" Lng="-6.2603"
           name="Test Station" brand="Circle K"
           addr1="Main Street" addr2="Dublin"
           price="175.9" fuel="petrol" trend="down"
           dateupdated="2025-05-20 09:00:00" dateupdatedshort="May 20 2025"
           Updater="user1" Zone="Dublin" County="Dublin" />
</stations>
"""

_EMPTY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
</stations>
"""

_MALFORMED_XML = "this is not xml <<<"


def _make_mock_response(
    status: int,
    text_data: str = "",
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text_data)
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


# ---------------------------------------------------------------------------
# 1. Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata() -> None:
    """IePumpsProvider declares required class attributes."""
    assert IePumpsProvider.COUNTRY == "IE"
    assert IePumpsProvider.PROVIDER_KEY == "ie_pumps"
    assert IePumpsProvider.LABEL == "pumps.ie"


def test_provider_config_mode() -> None:
    """CONFIG_MODE is station_id."""
    assert IePumpsProvider.CONFIG_MODE == "station_id"


def test_provider_station_lookup_mode() -> None:
    """STATION_LOOKUP_MODE is location_search."""
    assert IePumpsProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_capabilities_include_fuel_types() -> None:
    """CAPABILITIES includes diesel, petrol, and unleaded."""
    caps = IePumpsProvider.CAPABILITIES
    assert "diesel" in caps
    # petrol alias removed from CAPABILITIES (issue 29): only unleaded now
    assert "unleaded" in caps


def test_provider_capabilities_include_identity_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = IePumpsProvider.CAPABILITIES
    for key in ("name", "brand", "address", "county", "latitude", "longitude"):
        assert key in caps, f"Key '{key}' missing from CAPABILITIES"


def test_provider_capabilities_include_timing_and_sentinels() -> None:
    """CAPABILITIES includes lastupdated and coordinator sentinel keys."""
    caps = IePumpsProvider.CAPABILITIES
    assert "lastupdated" in caps
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# 2. _parse_float helper
# ---------------------------------------------------------------------------


def test_parse_float_valid_string() -> None:
    """_parse_float returns a float for a valid string."""
    assert _parse_float("173.9") == pytest.approx(173.9)


def test_parse_float_none_input() -> None:
    """_parse_float returns None for None input."""
    assert _parse_float(None) is None


def test_parse_float_empty_string() -> None:
    """_parse_float returns None for an empty string."""
    assert _parse_float("") is None


def test_parse_float_non_numeric() -> None:
    """_parse_float returns None for non-numeric strings."""
    assert _parse_float("not-a-number") is None


def test_parse_float_integer_string() -> None:
    """_parse_float handles integer strings correctly."""
    assert _parse_float("175") == pytest.approx(175.0)


# ---------------------------------------------------------------------------
# 3. _parse_xml helper
# ---------------------------------------------------------------------------


def test_parse_xml_returns_station_list() -> None:
    """_parse_xml returns a list of station dicts for valid XML."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    assert len(stations) == 2


def test_parse_xml_station_has_required_keys() -> None:
    """_parse_xml returns dicts with all expected keys."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    station = stations[0]
    for key in (
        "ID",
        "name",
        "brand",
        "address",
        "county",
        "lat",
        "lng",
        "price_eur",
        "fuel",
        "dateupdated",
    ):
        assert key in station, f"Key '{key}' missing from parsed station"


def test_parse_xml_price_converted_to_eur_per_litre() -> None:
    """_parse_xml converts cents-per-litre to EUR/litre (173.9 → 1.739)."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    assert stations[0]["price_eur"] == pytest.approx(1.739, abs=1e-4)


def test_parse_xml_coordinates_parsed_as_floats() -> None:
    """_parse_xml parses Lat/Lng attributes as floats."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    s = stations[0]
    assert s["lat"] == pytest.approx(53.3498)
    assert s["lng"] == pytest.approx(-6.2603)


def test_parse_xml_address_combines_addr1_and_addr2() -> None:
    """_parse_xml combines addr1 and addr2 with comma separator."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    assert stations[0]["address"] == "Main Street, Dublin"


def test_parse_xml_county_from_county_attribute() -> None:
    """_parse_xml reads County attribute for county field."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    assert stations[0]["county"] == "Dublin"


def test_parse_xml_empty_xml_returns_empty_list() -> None:
    """_parse_xml returns an empty list for XML with no station elements."""
    stations = _parse_xml(_EMPTY_XML, "diesel")
    assert stations is not None
    assert len(stations) == 0


def test_parse_xml_malformed_xml_returns_none() -> None:
    """_parse_xml returns None when the XML cannot be parsed."""
    result = _parse_xml(_MALFORMED_XML, "diesel")
    assert result is None


def test_parse_xml_station_id_stored_as_string() -> None:
    """_parse_xml stores station ID as a string."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    assert isinstance(stations[0]["ID"], str)
    assert stations[0]["ID"] == "1234"


# ---------------------------------------------------------------------------
# 4. _find_station helper
# ---------------------------------------------------------------------------


def test_find_station_returns_matching_record() -> None:
    """_find_station returns the station with the matching ID."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    record = _find_station(stations, "1234")
    assert record is not None
    assert record["ID"] == "1234"
    assert record["name"] == "Test Station"


def test_find_station_returns_none_when_not_found() -> None:
    """_find_station returns None when no station matches."""
    stations = _parse_xml(_DIESEL_XML, "diesel")
    assert stations is not None
    record = _find_station(stations, "0000")
    assert record is None


def test_find_station_handles_empty_list() -> None:
    """_find_station returns None for an empty station list."""
    assert _find_station([], "1234") is None


# ---------------------------------------------------------------------------
# 5. _build_station_data helper
# ---------------------------------------------------------------------------


def test_build_station_data_fuel_price_mapping() -> None:
    """_build_station_data populates diesel, petrol, unleaded correctly."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    petrol_stations = _parse_xml(_PETROL_XML, "petrol")
    assert diesel_stations and petrol_stations

    diesel_record = _find_station(diesel_stations, _STATION_ID)
    petrol_record = _find_station(petrol_stations, _STATION_ID)
    assert diesel_record and petrol_record

    prices_by_fuel = {"diesel": diesel_record, "petrol": petrol_record}
    data = _build_station_data(_STATION_ID, diesel_record, prices_by_fuel)

    assert data["diesel"] == pytest.approx(1.739, abs=1e-4)
    assert data["unleaded"] == pytest.approx(1.759, abs=1e-4)
    assert data["unleaded"] == pytest.approx(1.759, abs=1e-4)  # alias for petrol


def test_build_station_data_identity_fields() -> None:
    """_build_station_data populates name, brand, address, county."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    data = _build_station_data(_STATION_ID, record, {"diesel": record})

    assert data["name"] == "Test Station"
    assert data["brand"] == "Circle K"
    assert data["address"] == "Main Street, Dublin"
    assert data["county"] == "Dublin"


def test_build_station_data_coordinates() -> None:
    """_build_station_data populates latitude and longitude."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    data = _build_station_data(_STATION_ID, record, {"diesel": record})

    assert data["latitude"] == pytest.approx(53.3498)
    assert data["longitude"] == pytest.approx(-6.2603)


def test_build_station_data_lastupdated_from_dateupdated() -> None:
    """_build_station_data maps dateupdated to lastupdated."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    data = _build_station_data(_STATION_ID, record, {"diesel": record})

    assert data["lastupdated"] == "2025-06-08 14:23:00"


def test_build_station_data_missing_fuel_returns_none() -> None:
    """_build_station_data returns None for fuel types with no data."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    # Only diesel provided — petrol should be None
    data = _build_station_data(_STATION_ID, record, {"diesel": record})

    assert data["unleaded"] is None
    assert data["unleaded"] is None


def test_build_station_data_source_station_id() -> None:
    """_build_station_data sets source_station_id to the given station ID."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    data = _build_station_data(_STATION_ID, record, {"diesel": record})

    assert data["source_station_id"] == _STATION_ID


# ---------------------------------------------------------------------------
# 6. async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_diesel_and_petrol() -> None:
    """async_fetch returns normalised data with diesel and petrol prices."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.739, abs=1e-4)
    assert data["unleaded"] == pytest.approx(1.759, abs=1e-4)
    assert data["name"] == "Test Station"
    assert data["brand"] == "Circle K"


async def test_async_fetch_success_diesel_only() -> None:
    """async_fetch succeeds when only diesel data is available for the station."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _EMPTY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.739, abs=1e-4)
    assert data["unleaded"] is None


# ---------------------------------------------------------------------------
# 7. async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station ID not in any response."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider("9876")  # ID not in XML fixtures
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, "9876")


async def test_async_fetch_raises_provider_error_on_empty_responses() -> None:
    """async_fetch raises ProviderError when both responses are empty."""
    diesel_resp = _make_mock_response(200, _EMPTY_XML)
    petrol_resp = _make_mock_response(200, _EMPTY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# 8. async_fetch — error handling
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_on_network_error() -> None:
    """async_fetch raises ProviderError when both HTTP requests fail with ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = IePumpsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_on_malformed_xml() -> None:
    """async_fetch raises ProviderError when all responses return malformed XML."""
    diesel_resp = _make_mock_response(200, _MALFORMED_XML)
    petrol_resp = _make_mock_response(200, _MALFORMED_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_continues_on_single_fuel_http_error() -> None:
    """async_fetch succeeds when one fuel type HTTP fails but the other succeeds."""
    # Diesel returns HTTP 500 (raise_for_status will raise), petrol is fine.
    diesel_resp = _make_mock_response(500, "")
    diesel_resp.raise_for_status = MagicMock(
        side_effect=ClientResponseError(MagicMock(), MagicMock(), status=500)
    )
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # diesel should be None (request failed), petrol should be populated
    assert data["diesel"] is None
    assert data["unleaded"] == pytest.approx(1.759, abs=1e-4)


# ---------------------------------------------------------------------------
# 9. async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns station name from diesel response."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    session = _make_session(diesel_resp)

    provider = IePumpsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Test Station"


async def test_async_fetch_station_name_falls_back_to_petrol() -> None:
    """async_fetch_station_name falls back to petrol list when not in diesel."""
    diesel_resp = _make_mock_response(200, _EMPTY_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Test Station"


async def test_async_fetch_station_name_returns_none_on_network_error() -> None:
    """async_fetch_station_name returns None on network failure."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = IePumpsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_station_not_found() -> None:
    """async_fetch_station_name returns None when station ID not in results."""
    diesel_resp = _make_mock_response(200, _EMPTY_XML)
    petrol_resp = _make_mock_response(200, _EMPTY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider("0000")
    name = await provider.async_fetch_station_name(session, "0000")

    assert name is None


# ---------------------------------------------------------------------------
# 10. async_list_stations (location_search)
# ---------------------------------------------------------------------------


async def test_async_list_stations_filters_by_radius() -> None:
    """async_list_stations returns only stations within the given radius."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    # Centre on Dublin — station 1234 is at 53.3498, -6.2603 (distance ~0 km)
    # Station 9999 is at 51.8985, -8.4756 (Cork, ~260 km away)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=50.0
    )

    station_ids = [sid for sid, _ in result]
    assert _STATION_ID in station_ids
    assert "9999" not in station_ids  # Cork station is >50 km away


async def test_async_list_stations_returns_empty_without_coords() -> None:
    """async_list_stations returns empty list when lat/lng are not provided."""
    session = MagicMock()
    provider = IePumpsProvider(_STATION_ID)

    result = await provider.async_list_stations(session)
    assert result == []


async def test_async_list_stations_label_includes_price() -> None:
    """async_list_stations label includes diesel price."""
    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=50.0
    )

    assert result
    _, label = result[0]
    assert "Diesel" in label or "Petrol" in label


async def test_async_list_stations_returns_empty_on_network_error() -> None:
    """async_list_stations returns empty list on network error."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = IePumpsProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=50.0
    )
    assert result == []


# ---------------------------------------------------------------------------
# 11. SSLContext with verification disabled is used for all requests
# ---------------------------------------------------------------------------


async def test_async_fetch_passes_ssl_false() -> None:
    """Every GET request to pumps.ie uses an SSLContext with cert verification disabled."""
    import ssl  # noqa: PLC0415

    diesel_resp = _make_mock_response(200, _DIESEL_XML)
    petrol_resp = _make_mock_response(200, _PETROL_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        ssl_kwarg = call.kwargs.get("ssl")
        assert isinstance(ssl_kwarg, ssl.SSLContext), (
            f"Expected ssl.SSLContext in GET call but got ssl={ssl_kwarg!r}"
        )
        assert ssl_kwarg.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# 12. Price normalisation — zero and None guards
# ---------------------------------------------------------------------------


def test_build_station_data_zero_price_becomes_none() -> None:
    """_build_station_data treats a zero-value price as None."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    # Override price_eur to zero
    zero_price_record = {**record, "price_eur": 0.0}
    data = _build_station_data(_STATION_ID, record, {"diesel": zero_price_record})

    assert data["diesel"] is None


def test_parse_xml_zero_cent_price_results_in_none_eur() -> None:
    """_parse_xml sets price_eur=None when raw XML price is 0."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="5555" Lat="53.0" Lng="-7.0"
           name="No Price" brand="" addr1="" addr2=""
           price="0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Midlands" County="Offaly" />
</stations>
"""
    stations = _parse_xml(xml, "diesel")
    assert stations is not None
    assert stations[0]["price_eur"] is None


# ---------------------------------------------------------------------------
# 13. Provider registry integration
# ---------------------------------------------------------------------------


def test_provider_registered_in_registry() -> None:
    """IePumpsProvider is registered in the PROVIDER_REGISTRY under 'ie_pumps'."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    assert "ie_pumps" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["ie_pumps"] is IePumpsProvider


# ---------------------------------------------------------------------------
# 14. _parse_xml — station missing ID is skipped (line 447)
# ---------------------------------------------------------------------------


def test_parse_xml_skips_station_without_id() -> None:
    """_parse_xml skips <station> elements that have no ID or id attribute."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station Lat="53.0" Lng="-7.0"
           name="No ID Station" brand="" addr1="" addr2=""
           price="170.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Midlands" County="Offaly" />
  <station ID="7777" Lat="53.0" Lng="-7.0"
           name="Has ID" brand="" addr1="" addr2=""
           price="170.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Midlands" County="Offaly" />
</stations>
"""
    stations = _parse_xml(xml, "diesel")
    assert stations is not None
    assert len(stations) == 1
    assert stations[0]["ID"] == "7777"


# ---------------------------------------------------------------------------
# 15. _parse_xml — addr1 only, no addr2 (line 465)
# ---------------------------------------------------------------------------


def test_parse_xml_address_uses_addr1_when_addr2_empty() -> None:
    """_parse_xml uses addr1 alone when addr2 is absent."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="8001" Lat="53.0" Lng="-7.0"
           name="Addr1 Only" brand="" addr1="High Street" addr2=""
           price="172.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Midlands" County="Offaly" />
</stations>
"""
    stations = _parse_xml(xml, "diesel")
    assert stations is not None
    assert stations[0]["address"] == "High Street"


# ---------------------------------------------------------------------------
# 16. _build_station_data — price <= 0 returns None (line 550)
# ---------------------------------------------------------------------------


def test_build_station_data_negative_price_becomes_none() -> None:
    """_build_station_data treats a negative price_eur value as None."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    negative_price_record = {**record, "price_eur": -0.5}
    data = _build_station_data(_STATION_ID, record, {"diesel": negative_price_record})

    assert data["diesel"] is None


def test_build_station_data_explicit_none_price_becomes_none() -> None:
    """_build_station_data returns None when price_eur key is explicitly None (line 550)."""
    diesel_stations = _parse_xml(_DIESEL_XML, "diesel")
    assert diesel_stations
    record = _find_station(diesel_stations, _STATION_ID)
    assert record
    none_price_record = {**record, "price_eur": None}
    data = _build_station_data(_STATION_ID, record, {"diesel": none_price_record})

    assert data["diesel"] is None


# ---------------------------------------------------------------------------
# 17. async_fetch_station_name — generic exception path (lines 253-254)
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none_on_generic_exception() -> None:
    """async_fetch_station_name returns None when _fetch_stations raises unexpectedly."""
    provider = IePumpsProvider(_STATION_ID)
    provider._fetch_stations = AsyncMock(side_effect=RuntimeError("unexpected failure"))  # type: ignore[method-assign]

    session = MagicMock()
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# 18. async_list_stations — generic exception path (lines 297-299)
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_on_generic_exception() -> None:
    """async_list_stations returns [] when _fetch_stations raises unexpectedly."""
    provider = IePumpsProvider(_STATION_ID)
    provider._fetch_stations = AsyncMock(
        side_effect=RuntimeError("unexpected gather failure")
    )  # type: ignore[method-assign]

    session = MagicMock()
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=50.0
    )
    assert result == []


# ---------------------------------------------------------------------------
# 19. async_list_stations — petrol-only station not in diesel → merge (line 318)
# ---------------------------------------------------------------------------

_PETROL_ONLY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="1234" Lat="53.3498" Lng="-6.2603"
           name="Test Station" brand="Circle K"
           addr1="Main Street" addr2="Dublin"
           price="175.9" fuel="petrol" trend="down"
           dateupdated="2025-05-20 09:00:00" dateupdatedshort="May 20 2025"
           Updater="user1" Zone="Dublin" County="Dublin" />
  <station ID="6666" Lat="53.34" Lng="-6.26"
           name="Petrol Only" brand="Maxol"
           addr1="North Road" addr2="Dublin"
           price="176.0" fuel="petrol" trend="stable"
           dateupdated="2025-06-01 08:00:00" dateupdatedshort="Jun 1 2025"
           Updater="user3" Zone="Dublin" County="Dublin" />
</stations>
"""


async def test_async_list_stations_includes_petrol_only_station() -> None:
    """async_list_stations merges petrol-only station not present in diesel list."""
    diesel_resp = _make_mock_response(200, _EMPTY_XML)
    petrol_resp = _make_mock_response(200, _PETROL_ONLY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=5.0
    )

    station_ids = [sid for sid, _ in result]
    assert "6666" in station_ids


# ---------------------------------------------------------------------------
# 20. async_list_stations — station missing coordinates is skipped (line 330)
# ---------------------------------------------------------------------------

_NO_COORD_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="3333" name="No Coords" brand="Shell"
           addr1="Nowhere" addr2=""
           price="171.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Dublin" County="Dublin" />
  <station ID="4444" Lat="53.3498" Lng="-6.2603"
           name="Has Coords" brand="Esso"
           addr1="Main Road" addr2="Dublin"
           price="172.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Dublin" County="Dublin" />
</stations>
"""


async def test_async_list_stations_skips_station_without_coordinates() -> None:
    """async_list_stations skips stations that have no lat/lng coordinates."""
    diesel_resp = _make_mock_response(200, _NO_COORD_XML)
    petrol_resp = _make_mock_response(200, _EMPTY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=5.0
    )

    station_ids = [sid for sid, _ in result]
    assert "3333" not in station_ids
    assert "4444" in station_ids


# ---------------------------------------------------------------------------
# 21. async_list_stations — no-price station gets sort_key 9999 (lines 353-354)
# ---------------------------------------------------------------------------

_NO_PRICE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station ID="5500" Lat="53.3498" Lng="-6.2603"
           name="No Price" brand="BP"
           addr1="Some Street" addr2="Dublin"
           price="0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Dublin" County="Dublin" />
  <station ID="5501" Lat="53.3498" Lng="-6.2603"
           name="Has Price" brand="Circle K"
           addr1="Other Street" addr2="Dublin"
           price="171.0" fuel="diesel" trend="stable"
           dateupdated="" Zone="Dublin" County="Dublin" />
</stations>
"""


async def test_async_list_stations_sorts_no_price_station_last() -> None:
    """async_list_stations places stations with no price data after priced ones."""
    diesel_resp = _make_mock_response(200, _NO_PRICE_XML)
    petrol_resp = _make_mock_response(200, _EMPTY_XML)
    session = _make_session(diesel_resp, petrol_resp)

    provider = IePumpsProvider(_STATION_ID)
    result = await provider.async_list_stations(
        session, lat=53.3498, lng=-6.2603, radius_km=5.0
    )

    station_ids = [sid for sid, _ in result]
    assert station_ids.index("5501") < station_ids.index("5500")
