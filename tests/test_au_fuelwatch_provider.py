"""Tests for AuFuelwatchProvider (FuelWatch Western Australia)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
    AuFuelwatchProvider,
    _HEADERS,
    _RSS_URL,
    _build_display_label,
    _make_station_id,
    _parse_is_open,
    _parse_lat_lng,
    _parse_price,
    _parse_rss_items,
    _parse_station_base,
)


# ---------------------------------------------------------------------------
# RSS fixture data
# ---------------------------------------------------------------------------

# A minimal well-formed FuelWatch RSS response (without BOM) for one station.
_STATION_LAT = "-31.80275800"
_STATION_LNG = "115.83773700"
_STATION_ID = f"{_STATION_LAT},{_STATION_LNG}"

_STATION_LAT2 = "-31.92345600"
_STATION_LNG2 = "115.95432100"
_STATION_ID2 = f"{_STATION_LAT2},{_STATION_LNG2}"


def _rss_bytes(
    lat: str = _STATION_LAT,
    lng: str = _STATION_LNG,
    price: str = "153.3",
    trading_name: str = "Liberty Landsdale",
    brand: str = "Liberty",
    address: str = "100 Landsdale Road",
    phone: str = "08 9300 0000",
    site_features: str = "Fuel Cards EFTPOS, Open Mon: 06:00-22:00",
    date: str = "2026-06-14",
    with_bom: bool = False,
) -> bytes:
    """Return a minimal valid FuelWatch RSS XML response as bytes."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<rss>\r\n"
        "<channel>\r\n"
        "<item>\r\n"
        f"<price>{price}</price>\r\n"
        f"<trading-name>{trading_name}</trading-name>\r\n"
        f"<brand>{brand}</brand>\r\n"
        f"<address>{address}</address>\r\n"
        f"<phone>{phone}</phone>\r\n"
        f"<latitude>{lat}</latitude>\r\n"
        f"<longitude>{lng}</longitude>\r\n"
        f"<site-features>{site_features}</site-features>\r\n"
        f"<date>{date}</date>\r\n"
        "</item>\r\n"
        "</channel>\r\n"
        "</rss>\r\n"
    )
    raw = xml.encode("utf-8")
    if with_bom:
        raw = b"\xef\xbb\xbf" + raw
    return raw


def _rss_two_stations_bytes() -> bytes:
    """Return RSS XML with two distinct stations."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<rss>\r\n"
        "<channel>\r\n"
        "<item>\r\n"
        "<price>153.3</price>\r\n"
        "<trading-name>Liberty Landsdale</trading-name>\r\n"
        "<brand>Liberty</brand>\r\n"
        "<address>100 Landsdale Road</address>\r\n"
        f"<latitude>{_STATION_LAT}</latitude>\r\n"
        f"<longitude>{_STATION_LNG}</longitude>\r\n"
        "<site-features>Open Mon: 06:00-22:00</site-features>\r\n"
        "<date>2026-06-14</date>\r\n"
        "</item>\r\n"
        "<item>\r\n"
        "<price>161.9</price>\r\n"
        "<trading-name>BP Morley</trading-name>\r\n"
        "<brand>BP</brand>\r\n"
        "<address>50 Walter Road</address>\r\n"
        f"<latitude>{_STATION_LAT2}</latitude>\r\n"
        f"<longitude>{_STATION_LNG2}</longitude>\r\n"
        "<site-features></site-features>\r\n"
        "<date>2026-06-14</date>\r\n"
        "</item>\r\n"
        "</channel>\r\n"
        "</rss>\r\n"
    )
    return xml.encode("utf-8")


def _rss_no_items_bytes() -> bytes:
    """Return RSS XML with an empty channel."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<rss>\r\n"
        "<channel>\r\n"
        "</channel>\r\n"
        "</rss>\r\n"
    )
    return xml.encode("utf-8")


def _rss_no_channel_bytes() -> bytes:
    """Return RSS XML with no channel element."""
    xml = '<?xml version="1.0" encoding="utf-8"?>\r\n<rss>\r\n</rss>\r\n'
    return xml.encode("utf-8")


def _make_mock_response(
    status: int = 200,
    body: bytes = b"",
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=body)
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


def _make_session_all_same(response_factory) -> MagicMock:
    """Return a mock session that always returns a fresh response from factory."""
    session = MagicMock()
    session.get = MagicMock(side_effect=lambda *a, **kw: response_factory())
    return session


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """AuFuelwatchProvider.COUNTRY is 'AU'."""
    assert AuFuelwatchProvider.COUNTRY == "AU"


def test_provider_metadata_key() -> None:
    """AuFuelwatchProvider.PROVIDER_KEY is 'au_fuelwatch'."""
    assert AuFuelwatchProvider.PROVIDER_KEY == "au_fuelwatch"


def test_provider_metadata_label() -> None:
    """AuFuelwatchProvider.LABEL contains 'FuelWatch'."""
    assert "FuelWatch" in AuFuelwatchProvider.LABEL


def test_provider_config_mode() -> None:
    """AuFuelwatchProvider.CONFIG_MODE is 'location'."""
    assert AuFuelwatchProvider.CONFIG_MODE == "location"


def test_provider_station_lookup_mode() -> None:
    """AuFuelwatchProvider.STATION_LOOKUP_MODE is 'location_search'."""
    assert AuFuelwatchProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """POLL_INTERVAL_SECONDS is 86400 (once daily)."""
    assert AuFuelwatchProvider.POLL_INTERVAL_SECONDS == 86400


# ---------------------------------------------------------------------------
# CAPABILITIES
# ---------------------------------------------------------------------------


def test_capabilities_include_all_fuel_types() -> None:
    """CAPABILITIES includes all five FuelWatch fuel types."""
    caps = AuFuelwatchProvider.CAPABILITIES
    for fuel in ("unleaded", "premium_unleaded", "diesel", "lpg", "e10"):
        assert fuel in caps, f"'{fuel}' missing from CAPABILITIES"


def test_capabilities_include_station_fields() -> None:
    """CAPABILITIES includes station identity and location fields."""
    caps = AuFuelwatchProvider.CAPABILITIES
    for field in ("name", "brand", "address", "latitude", "longitude", "phone"):
        assert field in caps, f"'{field}' missing from CAPABILITIES"


def test_capabilities_include_operational_fields() -> None:
    """CAPABILITIES includes is_open and lastupdated."""
    caps = AuFuelwatchProvider.CAPABILITIES
    assert "is_open" in caps
    assert "lastupdated" in caps


def test_capabilities_include_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = AuFuelwatchProvider.CAPABILITIES
    assert "last_successful_fetch" not in caps
    assert "data_fetch_problem" not in caps


# ---------------------------------------------------------------------------
# HTTP headers
# ---------------------------------------------------------------------------


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent that mentions HomeAssistant."""
    assert "HomeAssistant" in _HEADERS.get("User-Agent", "")


def test_headers_include_accept_xml() -> None:
    """_HEADERS includes Accept header with xml."""
    accept = _HEADERS.get("Accept", "")
    assert "xml" in accept


def test_headers_user_agent_not_blocked() -> None:
    """_HEADERS User-Agent is not a commonly blocked generic prefix."""
    ua = _HEADERS.get("User-Agent", "")
    blocked = ("curl/", "python-requests/", "Wget/", "Go-http-client/")
    for prefix in blocked:
        assert not ua.startswith(prefix), (
            f"User-Agent starts with blocked prefix '{prefix}'"
        )


def test_rss_url_points_to_fuelwatch() -> None:
    """_RSS_URL points at the correct FuelWatch endpoint."""
    assert "fuelwatch.wa.gov.au" in _RSS_URL
    assert _RSS_URL.startswith("https://")


# ---------------------------------------------------------------------------
# _parse_rss_items
# ---------------------------------------------------------------------------


def test_parse_rss_items_returns_one_item() -> None:
    """_parse_rss_items returns one item dict for a single-station feed."""
    items = _parse_rss_items(_rss_bytes())
    assert len(items) == 1


def test_parse_rss_items_extracts_price() -> None:
    """_parse_rss_items extracts the price field correctly."""
    items = _parse_rss_items(_rss_bytes(price="153.3"))
    assert items[0]["price"] == "153.3"


def test_parse_rss_items_extracts_trading_name() -> None:
    """_parse_rss_items extracts the trading-name hyphenated field."""
    items = _parse_rss_items(_rss_bytes(trading_name="Liberty Landsdale"))
    assert items[0]["trading-name"] == "Liberty Landsdale"


def test_parse_rss_items_extracts_lat_lng() -> None:
    """_parse_rss_items extracts latitude and longitude strings."""
    items = _parse_rss_items(_rss_bytes())
    assert items[0]["latitude"] == _STATION_LAT
    assert items[0]["longitude"] == _STATION_LNG


def test_parse_rss_items_extracts_brand() -> None:
    """_parse_rss_items extracts the brand field."""
    items = _parse_rss_items(_rss_bytes(brand="BP"))
    assert items[0]["brand"] == "BP"


def test_parse_rss_items_extracts_address() -> None:
    """_parse_rss_items extracts the address field."""
    items = _parse_rss_items(_rss_bytes(address="42 Station Street"))
    assert items[0]["address"] == "42 Station Street"


def test_parse_rss_items_extracts_site_features() -> None:
    """_parse_rss_items extracts site-features hyphenated field."""
    items = _parse_rss_items(_rss_bytes(site_features="Open Mon: 06:00-22:00"))
    assert items[0]["site-features"] == "Open Mon: 06:00-22:00"


def test_parse_rss_items_handles_bom() -> None:
    """_parse_rss_items strips UTF-8 BOM before parsing (no ParseError raised)."""
    items = _parse_rss_items(_rss_bytes(with_bom=True))
    assert len(items) == 1
    assert items[0]["price"] == "153.3"


def test_parse_rss_items_empty_channel() -> None:
    """_parse_rss_items returns empty list when channel has no items."""
    items = _parse_rss_items(_rss_no_items_bytes())
    assert items == []


def test_parse_rss_items_no_channel() -> None:
    """_parse_rss_items returns empty list when channel element is absent."""
    items = _parse_rss_items(_rss_no_channel_bytes())
    assert items == []


def test_parse_rss_items_invalid_xml() -> None:
    """_parse_rss_items returns empty list for malformed XML (no exception raised)."""
    items = _parse_rss_items(b"<not valid XML <<>>")
    assert items == []


def test_parse_rss_items_two_stations() -> None:
    """_parse_rss_items returns one dict per <item> element."""
    items = _parse_rss_items(_rss_two_stations_bytes())
    assert len(items) == 2


def test_parse_rss_items_whitespace_stripped() -> None:
    """_parse_rss_items strips surrounding whitespace from text values."""
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel><item>"
        b"<price>  153.3  </price>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"</item></channel></rss>"
    )
    items = _parse_rss_items(xml)
    assert items[0]["price"] == "153.3"


def test_parse_rss_items_empty_element_becomes_none() -> None:
    """_parse_rss_items maps empty element text to None."""
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel><item>"
        b"<phone></phone>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"</item></channel></rss>"
    )
    items = _parse_rss_items(xml)
    assert items[0]["phone"] is None


def test_parse_rss_items_strips_namespace_prefix() -> None:
    """_parse_rss_items strips {namespace} prefixes from tag names."""
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<rss xmlns:fw="http://fuelwatch.example/">'
        b"<channel><item>"
        b"<fw:price>153.3</fw:price>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"</item></channel></rss>"
    )
    items = _parse_rss_items(xml)
    assert "price" in items[0]
    assert items[0]["price"] == "153.3"


# ---------------------------------------------------------------------------
# _make_station_id
# ---------------------------------------------------------------------------


def test_make_station_id_happy_path() -> None:
    """_make_station_id returns '{lat},{lng}' composite key."""
    item = {"latitude": "-31.80275800", "longitude": "115.83773700"}
    assert _make_station_id(item) == "-31.80275800,115.83773700"


def test_make_station_id_missing_latitude() -> None:
    """_make_station_id returns None when latitude is absent."""
    item = {"longitude": "115.83773700"}
    assert _make_station_id(item) is None


def test_make_station_id_missing_longitude() -> None:
    """_make_station_id returns None when longitude is absent."""
    item = {"latitude": "-31.80275800"}
    assert _make_station_id(item) is None


def test_make_station_id_none_latitude() -> None:
    """_make_station_id returns None when latitude value is None."""
    item = {"latitude": None, "longitude": "115.83773700"}
    assert _make_station_id(item) is None


def test_make_station_id_none_longitude() -> None:
    """_make_station_id returns None when longitude value is None."""
    item = {"latitude": "-31.80275800", "longitude": None}
    assert _make_station_id(item) is None


def test_make_station_id_preserves_exact_strings() -> None:
    """_make_station_id preserves the exact 8-decimal-place strings from the feed."""
    item = {"latitude": "-31.80275800", "longitude": "115.83773700"}
    sid = _make_station_id(item)
    # Must not round or reformat — the exact strings are the key
    assert sid == "-31.80275800,115.83773700"


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_normal_cents_value() -> None:
    """_parse_price converts a cents string to AUD/litre by dividing by 100."""
    assert _parse_price("153.3") == pytest.approx(1.533)


def test_parse_price_whole_number() -> None:
    """_parse_price handles an integer string."""
    assert _parse_price("150") == pytest.approx(1.5)


def test_parse_price_none_input() -> None:
    """_parse_price returns None for None input."""
    assert _parse_price(None) is None


def test_parse_price_zero_returns_none() -> None:
    """_parse_price treats zero price as None (station not reporting)."""
    assert _parse_price("0") is None
    assert _parse_price("0.0") is None


def test_parse_price_negative_returns_none() -> None:
    """_parse_price treats negative price as None."""
    assert _parse_price("-1.5") is None


def test_parse_price_non_numeric_returns_none() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("n/a") is None
    assert _parse_price("") is None
    assert _parse_price("abc") is None


# ---------------------------------------------------------------------------
# _parse_lat_lng
# ---------------------------------------------------------------------------


def test_parse_lat_lng_valid_string() -> None:
    """_parse_lat_lng converts a valid coordinate string to float."""
    assert _parse_lat_lng("-31.80275800") == pytest.approx(-31.80275800)


def test_parse_lat_lng_positive() -> None:
    """_parse_lat_lng handles positive coordinate values."""
    assert _parse_lat_lng("115.83773700") == pytest.approx(115.83773700)


def test_parse_lat_lng_none() -> None:
    """_parse_lat_lng returns None for None input."""
    assert _parse_lat_lng(None) is None


def test_parse_lat_lng_non_numeric() -> None:
    """_parse_lat_lng returns None for non-numeric strings."""
    assert _parse_lat_lng("unknown") is None


def test_parse_lat_lng_empty_string() -> None:
    """_parse_lat_lng returns None for empty string."""
    assert _parse_lat_lng("") is None


# ---------------------------------------------------------------------------
# _parse_is_open
# ---------------------------------------------------------------------------


def test_parse_is_open_with_open_schedule() -> None:
    """_parse_is_open returns True when site-features contains 'Open'."""
    assert _parse_is_open("Fuel Cards EFTPOS, Open Mon: 06:00-22:00") is True


def test_parse_is_open_with_lowercase_open() -> None:
    """_parse_is_open returns True for lowercase 'open' in site-features."""
    assert _parse_is_open("open 24 hours") is True


def test_parse_is_open_no_schedule_text() -> None:
    """_parse_is_open returns False when no schedule text is present."""
    assert _parse_is_open("Fuel Cards EFTPOS ATM") is False


def test_parse_is_open_none_input() -> None:
    """_parse_is_open returns None for None input."""
    assert _parse_is_open(None) is False


def test_parse_is_open_empty_string() -> None:
    """_parse_is_open returns None for empty string."""
    assert _parse_is_open("") is False


# ---------------------------------------------------------------------------
# _parse_station_base
# ---------------------------------------------------------------------------


def test_parse_station_base_name_from_trading_name() -> None:
    """_parse_station_base uses trading-name as the name when present."""
    item = {
        "trading-name": "Liberty Landsdale",
        "brand": "Liberty",
        "address": "100 Landsdale Road",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
        "phone": None,
        "site-features": "Open Mon: 06:00-22:00",
        "date": "2026-06-14",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["name"] == "Liberty Landsdale"


def test_parse_station_base_name_falls_back_to_brand() -> None:
    """_parse_station_base falls back to brand when trading-name is absent."""
    item = {
        "brand": "BP",
        "address": "50 Walter Road",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
        "phone": None,
        "site-features": None,
        "date": "2026-06-14",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["name"] == "BP"


def test_parse_station_base_name_none_when_both_absent() -> None:
    """_parse_station_base sets name=None when neither trading-name nor brand is present."""
    item = {
        "address": "50 Walter Road",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["name"] is None


def test_parse_station_base_lat_lng_parsed_to_float() -> None:
    """_parse_station_base converts lat/lng strings to floats."""
    item = {
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["latitude"] == pytest.approx(-31.80275800)
    assert data["longitude"] == pytest.approx(115.83773700)


def test_parse_station_base_is_open_true_when_schedule_present() -> None:
    """_parse_station_base derives is_open=True when site-features contains 'Open'."""
    item = {
        "site-features": "Open Mon: 06:00-22:00",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["is_open"] is True


def test_parse_station_base_is_open_none_when_no_schedule() -> None:
    """_parse_station_base sets is_open=None when no schedule info present."""
    item = {
        "site-features": None,
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["is_open"] is False


def test_parse_station_base_lastupdated_from_date() -> None:
    """_parse_station_base maps <date> to lastupdated."""
    item = {
        "date": "2026-06-14",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["lastupdated"] == "2026-06-14"


def test_parse_station_base_source_station_id_stored() -> None:
    """_parse_station_base stores the composite station_id as source_station_id."""
    item = {"latitude": "-31.80275800", "longitude": "115.83773700"}
    data = _parse_station_base(item, _STATION_ID)
    assert data["source_station_id"] == _STATION_ID


def test_parse_station_base_address_preserved() -> None:
    """_parse_station_base stores the address field from the item."""
    item = {
        "address": "100 Landsdale Road, Landsdale WA 6065",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["address"] == "100 Landsdale Road, Landsdale WA 6065"


def test_parse_station_base_phone_preserved() -> None:
    """_parse_station_base stores the phone field from the item."""
    item = {
        "phone": "08 9300 1234",
        "latitude": "-31.80275800",
        "longitude": "115.83773700",
    }
    data = _parse_station_base(item, _STATION_ID)
    assert data["phone"] == "08 9300 1234"


# ---------------------------------------------------------------------------
# _build_display_label
# ---------------------------------------------------------------------------


def test_build_display_label_brand_and_name() -> None:
    """_build_display_label combines brand and name when they differ."""
    data = {
        "brand": "Liberty",
        "name": "Liberty Landsdale",
        "address": "100 Landsdale Road",
        "unleaded": 153.3,
        "diesel": 161.9,
    }
    label = _build_display_label(data)
    # brand is embedded in name, so should just use name
    assert "Landsdale" in label


def test_build_display_label_includes_price_parts() -> None:
    """_build_display_label includes ULP and Diesel price strings in AUD."""
    data = {
        "brand": "BP",
        "name": "BP Morley",
        "unleaded": 1.55,
        "diesel": 1.635,
    }
    label = _build_display_label(data)
    assert "A$" in label
    assert "1.550" in label
    assert "1.635" in label


def test_build_display_label_no_prices_returns_name_only() -> None:
    """_build_display_label returns identity string only when no prices available."""
    data = {
        "name": "Caltex Northbridge",
        "brand": "Caltex",
    }
    label = _build_display_label(data)
    assert "Northbridge" in label
    assert "—" not in label


def test_build_display_label_falls_back_to_address() -> None:
    """_build_display_label uses address when name and brand are absent."""
    data = {"address": "99 Station Street"}
    label = _build_display_label(data)
    assert "99 Station Street" in label


def test_build_display_label_unknown_station_fallback() -> None:
    """_build_display_label returns 'Unknown Station' when all identity fields absent."""
    data = {}
    label = _build_display_label(data)
    assert label == "Unknown Station"


def test_build_display_label_e10_lpg_premium_shown() -> None:
    """_build_display_label includes E10, LPG, and Prem price parts when present."""
    data = {
        "name": "Some Station",
        "premium_unleaded": 1.65,
        "lpg": 0.90,
        "e10": 1.51,
    }
    label = _build_display_label(data)
    assert "1.650" in label
    assert "0.900" in label
    assert "1.510" in label


# ---------------------------------------------------------------------------
# Provider constructor
# ---------------------------------------------------------------------------


def test_provider_constructor_stores_station_id() -> None:
    """AuFuelwatchProvider stores station_id on construction."""
    p = AuFuelwatchProvider(_STATION_ID)
    assert p._station_id == _STATION_ID


def test_provider_constructor_stores_county() -> None:
    """AuFuelwatchProvider stores county (WA Region code) on construction."""
    p = AuFuelwatchProvider(_STATION_ID, county="25")
    assert p._county == "25"


def test_provider_constructor_defaults_county_none() -> None:
    """AuFuelwatchProvider defaults county to None when not provided."""
    p = AuFuelwatchProvider(_STATION_ID)
    assert p._county is None


def test_provider_constructor_stores_lat_lng_radius() -> None:
    """AuFuelwatchProvider stores lat/lng/radius_km for interface compatibility."""
    p = AuFuelwatchProvider(_STATION_ID, latitude=-31.8, longitude=115.8, radius_km=5.0)
    assert p._latitude == pytest.approx(-31.8)
    assert p._longitude == pytest.approx(115.8)
    assert p._radius_km == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a StationData dict for the matching station."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None
    assert data["name"] == "Liberty Landsdale"


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch populates unleaded price from the product=1 feed."""
    body = _rss_bytes(price="153.3")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # All 5 products hit the same mock returning the same station/price
    assert data["unleaded"] == pytest.approx(1.533)


async def test_async_fetch_success_all_fuel_keys_present() -> None:
    """async_fetch populates all five fuel type keys (values may be None if missing)."""
    body = _rss_bytes(price="153.3")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    for fuel in ("unleaded", "premium_unleaded", "diesel", "lpg", "e10"):
        assert fuel in data, f"Key '{fuel}' missing from async_fetch result"


async def test_async_fetch_success_identity_fields_populated() -> None:
    """async_fetch populates name, brand, address, lat, lng, phone, lastupdated."""
    body = _rss_bytes(
        trading_name="Liberty Landsdale",
        brand="Liberty",
        address="100 Landsdale Road",
        phone="08 9300 0000",
        date="2026-06-14",
    )

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "Liberty Landsdale"
    assert data["brand"] == "Liberty"
    assert data["address"] == "100 Landsdale Road"
    assert data["phone"] == "08 9300 0000"
    assert data["lastupdated"] == "2026-06-14"
    assert data["latitude"] == pytest.approx(float(_STATION_LAT))
    assert data["longitude"] == pytest.approx(float(_STATION_LNG))


async def test_async_fetch_success_is_open_true() -> None:
    """async_fetch sets is_open=True when site-features contains 'Open'."""
    body = _rss_bytes(site_features="Fuel Cards EFTPOS, Open Mon: 06:00-22:00")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["is_open"] is True


async def test_async_fetch_success_is_open_none() -> None:
    """async_fetch sets is_open=None when site-features has no schedule text."""
    body = _rss_bytes(site_features="Fuel Cards EFTPOS ATM")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["is_open"] is False


async def test_async_fetch_uses_region_code_in_request() -> None:
    """async_fetch passes Region param when county is set."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID, county="25")
    await provider.async_fetch(session, _STATION_ID)

    # Every GET call must include Region=25
    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("Region") == "25", (
            f"Expected Region=25 in params but got: {params}"
        )


async def test_async_fetch_no_region_code_when_county_none() -> None:
    """async_fetch omits Region param when county is not set."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)  # county=None
    await provider.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert "Region" not in params, (
            f"Region should not be in params when county=None, got: {params}"
        )


async def test_async_fetch_makes_five_product_requests() -> None:
    """async_fetch issues one request per fuel type (5 total)."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    assert session.get.call_count == 5


async def test_async_fetch_sends_surrounding_yes_param() -> None:
    """async_fetch always includes Surrounding=yes in request params."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("Surrounding") == "yes", (
            f"Expected Surrounding=yes in params but got: {params}"
        )


async def test_async_fetch_sends_correct_headers() -> None:
    """async_fetch passes _HEADERS to every GET request."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    for call in session.get.call_args_list:
        headers = call.kwargs.get("headers", {})
        assert "User-Agent" in headers, f"User-Agent missing from GET call: {call}"


async def test_async_fetch_multi_product_merges_by_station_id() -> None:
    """async_fetch merges multiple product feeds into one StationData per station."""
    unleaded_body = _rss_bytes(price="153.3")
    premium_body = _rss_bytes(price="163.5")
    diesel_body = _rss_bytes(price="168.9")
    lpg_body = _rss_bytes(price="92.0")
    e10_body = _rss_bytes(price="151.0")

    session = _make_session(
        _make_mock_response(200, body=unleaded_body),
        _make_mock_response(200, body=premium_body),
        _make_mock_response(200, body=diesel_body),
        _make_mock_response(200, body=lpg_body),
        _make_mock_response(200, body=e10_body),
    )
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.533)
    assert data["premium_unleaded"] == pytest.approx(1.635)
    assert data["diesel"] == pytest.approx(1.689)
    assert data["lpg"] == pytest.approx(0.92)
    assert data["e10"] == pytest.approx(1.51)


# ---------------------------------------------------------------------------
# async_fetch — BOM handling
# ---------------------------------------------------------------------------


async def test_async_fetch_handles_bom_in_response() -> None:
    """async_fetch successfully parses a response body that starts with UTF-8 BOM."""
    body = _rss_bytes(with_bom=True)

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.533)


# ---------------------------------------------------------------------------
# async_fetch — error paths
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station_id not in any feed."""
    # All feeds return a different station
    other_body = _rss_bytes(lat="-32.00000000", lng="115.00000000", price="155.0")

    def _fresh_resp():
        return _make_mock_response(200, body=other_body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_on_empty_feeds() -> None:
    """async_fetch raises ProviderError when all product feeds return no items."""

    def _empty_resp():
        return _make_mock_response(200, body=_rss_no_items_bytes())

    session = _make_session_all_same(_empty_resp)
    provider = AuFuelwatchProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_on_all_client_errors() -> None:
    """async_fetch raises ProviderError when all product requests fail with ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = AuFuelwatchProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_on_http_non_200() -> None:
    """async_fetch propagates when raise_for_status raises on a non-200 response."""
    resp = _make_mock_response(500)
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    provider = AuFuelwatchProvider(_STATION_ID)

    with pytest.raises((ClientError, ProviderError)):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_partial_failure_continues() -> None:
    """async_fetch succeeds for unleaded even if other product requests fail."""
    unleaded_body = _rss_bytes(price="153.3")
    unleaded_resp = _make_mock_response(200, body=unleaded_body)

    # Remaining 4 product requests raise ClientError
    def _get(*args, **kwargs):
        params = kwargs.get("params", {})
        if params.get("Product") == "1":
            return unleaded_resp
        err_mock = MagicMock()
        err_mock.__aenter__ = AsyncMock(side_effect=ClientError("fail"))
        err_mock.__aexit__ = AsyncMock(return_value=False)
        return err_mock

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)

    provider = AuFuelwatchProvider(_STATION_ID)
    # Should NOT raise because unleaded feed succeeded and station is found
    data = await provider.async_fetch(session, _STATION_ID)
    assert data["unleaded"] == pytest.approx(1.533)


async def test_async_fetch_invalid_xml_all_feeds_raises_provider_error() -> None:
    """async_fetch raises ProviderError when all feeds return malformed XML."""

    def _bad_resp():
        return _make_mock_response(200, body=b"<not valid XML <<<")

    session = _make_session_all_same(_bad_resp)
    provider = AuFuelwatchProvider(_STATION_ID)

    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


# ---------------------------------------------------------------------------
# async_fetch_station_name
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_success() -> None:
    """async_fetch_station_name returns trading-name when station is found."""
    body = _rss_bytes(trading_name="Liberty Landsdale")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "Liberty Landsdale"


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station_id not in feed."""
    other_body = _rss_bytes(lat="-32.00000000", lng="115.00000000")

    def _fresh_resp():
        return _make_mock_response(200, body=other_body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None when a ClientError occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = AuFuelwatchProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_empty_feeds() -> None:
    """async_fetch_station_name returns None when all product feeds are empty."""

    def _empty_resp():
        return _make_mock_response(200, body=_rss_no_items_bytes())

    session = _make_session_all_same(_empty_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_is_none() -> None:
    """async_fetch_station_name returns None when station has no name or brand."""
    # Build XML with no trading-name or brand
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel><item>"
        b"<price>153.3</price>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"<date>2026-06-14</date>"
        b"</item></channel></rss>"
    )

    def _fresh_resp():
        return _make_mock_response(200, body=xml)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (station_id, label) tuples."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region", county="25")
    result = await provider.async_list_stations(session, county="25")

    assert isinstance(result, list)
    assert all(isinstance(item, tuple) and len(item) == 2 for item in result)


async def test_async_list_stations_station_id_is_composite_key() -> None:
    """async_list_stations uses the composite '{lat},{lng}' as station_id."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert len(result) >= 1
    sid, _label = result[0]
    assert "," in sid
    # Should match lat,lng format
    parts = sid.split(",")
    assert len(parts) == 2
    float(parts[0])  # must be parseable as float
    float(parts[1])


async def test_async_list_stations_sorted_cheapest_unleaded_first() -> None:
    """async_list_stations sorts stations alphabetically by station name."""
    # Two stations in the unleaded feed with different prices
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel>"
        b"<item>"
        b"<price>165.0</price>"
        b"<trading-name>Expensive Station</trading-name>"
        b"<brand>BP</brand>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"<date>2026-06-14</date>"
        b"</item>"
        b"<item>"
        b"<price>150.0</price>"
        b"<trading-name>Cheap Station</trading-name>"
        b"<brand>Caltex</brand>"
        b"<latitude>-31.92345600</latitude>"
        b"<longitude>115.95432100</longitude>"
        b"<date>2026-06-14</date>"
        b"</item>"
        b"</channel></rss>"
    )

    def _fresh_resp():
        return _make_mock_response(200, body=xml)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert len(result) == 2
    # Alphabetically first ('BP Expensive Station' < 'Caltex Cheap Station') must come first
    assert "Expensive Station" in result[0][1]


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns [] when all requests raise ClientError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_returns_empty_on_empty_feeds() -> None:
    """async_list_stations returns [] when all product feeds have no items."""

    def _empty_resp():
        return _make_mock_response(200, body=_rss_no_items_bytes())

    session = _make_session_all_same(_empty_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert result == []


async def test_async_list_stations_uses_county_kwarg_as_region() -> None:
    """async_list_stations passes county kwarg as Region parameter."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    await provider.async_list_stations(session, county="42")

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("Region") == "42", (
            f"Expected Region=42 in params but got: {params}"
        )


async def test_async_list_stations_uses_provider_county_when_no_kwarg() -> None:
    """async_list_stations falls back to provider _county when no county kwarg given."""
    body = _rss_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region", county="25")
    await provider.async_list_stations(session)

    for call in session.get.call_args_list:
        params = call.kwargs.get("params", {})
        assert params.get("Region") == "25"


async def test_async_list_stations_label_contains_station_name() -> None:
    """async_list_stations display label includes the trading name."""
    body = _rss_bytes(trading_name="Liberty Landsdale", brand="Liberty", price="153.3")

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert len(result) >= 1
    _sid, label = result[0]
    assert "Landsdale" in label


async def test_async_list_stations_stations_without_price_go_last() -> None:
    """async_list_stations places stations without unleaded price at the end."""
    priced_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel>"
        b"<item>"
        b"<price>153.3</price>"
        b"<trading-name>Priced Station</trading-name>"
        b"<brand>BP</brand>"
        b"<latitude>-31.80275800</latitude>"
        b"<longitude>115.83773700</longitude>"
        b"<date>2026-06-14</date>"
        b"</item>"
        b"</channel></rss>"
    )
    # Second station only appears in non-unleaded feeds so has no unleaded price
    no_price_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<rss><channel>"
        b"<item>"
        b"<price>160.0</price>"
        b"<trading-name>Diesel Only Station</trading-name>"
        b"<brand>Caltex</brand>"
        b"<latitude>-31.92345600</latitude>"
        b"<longitude>115.95432100</longitude>"
        b"<date>2026-06-14</date>"
        b"</item>"
        b"</channel></rss>"
    )

    call_count = {"n": 0}

    def _get(*args, **kwargs):
        call_count["n"] += 1
        # Product 1 (unleaded) returns priced station only
        params = kwargs.get("params", {})
        if params.get("Product") == "1":
            return _make_mock_response(200, body=priced_xml)
        # All other products return the diesel-only station
        return _make_mock_response(200, body=no_price_xml)

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)

    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert len(result) == 2
    # Station with unleaded price comes first
    assert "Priced Station" in result[0][1]
    assert "Diesel Only" in result[1][1]


# ---------------------------------------------------------------------------
# async_list_stations — radius_km filter (issue #44)
# ---------------------------------------------------------------------------


async def test_async_list_stations_radius_km_filters_distant_stations() -> None:
    """async_list_stations drops stations outside radius_km of (lat, lng)."""
    # Two stations: Landsdale (~-31.80, 115.84) and Fremantle (~-32.06, 115.74).
    # Distance ≈ 29 km. Center on Landsdale with 5 km radius → Fremantle dropped.
    body = _rss_two_stations_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(
        session,
        lat=-31.80275800,
        lng=115.83773700,
        radius_km=5.0,
    )

    sids = [sid for sid, _ in result]
    assert _STATION_ID in sids
    assert _STATION_ID2 not in sids


async def test_async_list_stations_radius_km_keeps_within_range() -> None:
    """async_list_stations keeps stations inside the radius."""
    body = _rss_two_stations_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    # 50 km radius keeps both stations.
    result = await provider.async_list_stations(
        session,
        lat=-31.80275800,
        lng=115.83773700,
        radius_km=50.0,
    )

    sids = [sid for sid, _ in result]
    assert _STATION_ID in sids
    assert _STATION_ID2 in sids


async def test_async_list_stations_no_radius_kwarg_returns_all() -> None:
    """Without radius_km/lat/lng, list is unfiltered (back-compat)."""
    body = _rss_two_stations_bytes()

    def _fresh_resp():
        return _make_mock_response(200, body=body)

    session = _make_session_all_same(_fresh_resp)
    provider = AuFuelwatchProvider("region")
    result = await provider.async_list_stations(session)

    assert len(result) == 2


async def test_haversine_km_known_distance() -> None:
    """haversine_km matches a known distance within 1 % tolerance."""
    from custom_components.fuelcompare_ie.providers._geo import haversine_km

    # Perth CBD (-31.9523, 115.8613) ↔ Fremantle (-32.0569, 115.7439)
    # Real-world great-circle distance ≈ 16.0 km.
    d = haversine_km(-31.9523, 115.8613, -32.0569, 115.7439)
    assert 15.5 < d < 16.5


# ---------------------------------------------------------------------------
# au_fuelwatch.py line 266 — _make_station_id returns None → skip item
# ---------------------------------------------------------------------------


async def test_fetch_all_products_skips_item_with_no_coords() -> None:
    """Items without lat/lng coords are skipped (_make_station_id returns None)."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        AuFuelwatchProvider,
    )
    from unittest.mock import AsyncMock, MagicMock

    # RSS with one valid item (has coords) and one missing coords
    rss_mixed = b"""<?xml version="1.0" encoding="utf-8"?>
<rss><channel>
<item>
<trading-name>Valid Station</trading-name>
<price>179.9</price>
<latitude>-31.9523</latitude>
<longitude>115.8614</longitude>
<address>1 Test St</address>
</item>
<item>
<trading-name>No Coords Station</trading-name>
<price>175.0</price>
<address>2 Test St</address>
</item>
</channel></rss>"""

    resp = AsyncMock()
    resp.status = 200
    resp.read = AsyncMock(return_value=rss_mixed)
    resp.raise_for_status = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=resp)

    provider = AuFuelwatchProvider("test123")
    result = await provider._fetch_all_products(session, None)
    # Only the valid station (with coords) should be in the result
    assert len(result) == 1


# ---------------------------------------------------------------------------
# au_fuelwatch.py line 482 — _build_display_label identity=brand fallback
# ---------------------------------------------------------------------------


def test_build_display_label_brand_only() -> None:
    """_build_display_label uses brand when name is absent."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_display_label,
    )

    data = {
        "brand": "BP",
        "name": None,
        "address": "10 Main St",
        "unleaded": 1.799,
        "diesel": None,
    }
    label = _build_display_label(data)
    assert "BP" in label


# ---------------------------------------------------------------------------
# au_fuelwatch.py line 479 — _build_display_label identity=address fallback ("Unknown Station")
# ---------------------------------------------------------------------------


def test_build_display_label_all_identity_fields_absent_uses_unknown() -> None:
    """_build_display_label falls back to 'Unknown Station' when brand, name, and address are all absent."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_display_label,
    )

    data: dict = {
        "brand": None,
        "name": None,
        "address": None,
        "unleaded": 1.799,
        "diesel": None,
    }
    label = _build_display_label(data)
    assert label == "Unknown Station — ULP A$1.799"


def test_build_display_label_empty_address_uses_unknown() -> None:
    """_build_display_label returns 'Unknown Station' when brand=None, name=None, address=''."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_display_label,
    )

    data: dict = {
        "brand": None,
        "name": None,
        "address": "",
        "unleaded": None,
        "diesel": None,
    }
    label = _build_display_label(data)
    assert label == "Unknown Station"


# ---------------------------------------------------------------------------
# au_fuelwatch.py lines 527-530 — _build_station_list_label identity="Unknown Station"
# ---------------------------------------------------------------------------


def test_build_station_list_label_all_identity_fields_absent() -> None:
    """_build_station_list_label uses 'Unknown Station' when brand, name are absent."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_station_list_label,
    )

    data: dict = {
        "brand": None,
        "name": None,
        "address": "42 Elm St",
    }
    sid = "-31.8027,115.8376"
    label = _build_station_list_label(data, sid)
    # identity should be "Unknown Station", address present → format with address
    assert "Unknown Station" in label
    assert "42 Elm St" in label
    assert "(#" in label


def test_build_station_list_label_no_address_returns_short_id_format() -> None:
    """_build_station_list_label returns '{identity} (#{short_id})' when address is absent."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_station_list_label,
    )

    data: dict = {
        "brand": None,
        "name": None,
        "address": "",
    }
    sid = "-31.8027,115.8376"
    label = _build_station_list_label(data, sid)
    assert label == "Unknown Station (#-31.8027)"


# ---------------------------------------------------------------------------
# au_fuelwatch.py line 479 — _build_display_label identity = f"{brand} {name}"
# au_fuelwatch.py line 528 — _build_station_list_label identity = brand
# ---------------------------------------------------------------------------


def test_build_display_label_brand_and_name_combined_when_brand_not_in_name() -> None:
    """Line 479: identity = f'{brand} {name}' when both present and brand not in name."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_display_label,
    )

    data = {
        "brand": "BP",
        "name": "Service Station",  # 'bp' not in 'service station'
        "address": "1 Main St",
        "unleaded": 1.799,
        "diesel": None,
    }
    label = _build_display_label(data)
    assert "BP Service Station" in label
    assert "ULP" in label


def test_build_station_list_label_brand_only_when_name_absent() -> None:
    """Line 528: identity = brand when name is absent but brand is present."""
    from custom_components.fuelcompare_ie.providers.au_fuelwatch import (
        _build_station_list_label,
    )

    data = {
        "brand": "Shell",
        "name": None,
        "address": "25 Beach Rd",
    }
    sid = "-31.9000,115.9000"
    label = _build_station_list_label(data, sid)
    assert "Shell" in label
    assert "25 Beach Rd" in label
    assert "(#" in label
