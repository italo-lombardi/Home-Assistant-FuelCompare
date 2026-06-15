"""Tests for FrCarburantsProvider (Prix Carburants, France)."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.fuelcompare_ie.providers.base import ProviderError
from custom_components.fuelcompare_ie.providers.fr_carburants import (
    FrCarburantsProvider,
    _DATA_URL,
    _HEADERS,
    _NOM_TO_KEY,
    _build_station_data,
    _find_station_in_root,
    _haversine_km,
    _parse_coord,
    _parse_pdv,
    _parse_price,
)


@pytest.fixture(autouse=True)
def reset_fr_xml_cache():
    """Reset the class-level XML cache between tests to prevent contamination."""
    FrCarburantsProvider._xml_cache = None
    FrCarburantsProvider._xml_cache_ts = 0
    yield
    FrCarburantsProvider._xml_cache = None
    FrCarburantsProvider._xml_cache_ts = 0


# ---------------------------------------------------------------------------
# Test XML / ZIP builder helpers
# ---------------------------------------------------------------------------

_STATION_ID = "34150003"
_OTHER_ID = "75001001"

_PDV_XML_TEMPLATE = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid}" latitude="4365100" longitude="354700" cp="34150" pop="R">
    <adresse>1 RUE DES FLEURS</adresse>
    <ville>MONTPELLIER</ville>
    <horaires automate-24-24="{auto24}">
      <jour id="1" nom="Lundi" ferme="non">
        <horaire ouverture="06:00" fermeture="22:00"/>
      </jour>
    </horaires>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:30:00" valeur="1.799"/>
    <prix nom="SP95" id="2" maj="2024-03-15 10:30:00" valeur="1.849"/>
    <prix nom="SP98" id="3" maj="2024-03-15 10:30:00" valeur="1.929"/>
    <prix nom="E10" id="4" maj="2024-03-15 10:30:00" valeur="1.829"/>
    <prix nom="E85" id="5" maj="2024-03-15 10:30:00" valeur="0.899"/>
    <prix nom="GPLc" id="6" maj="2024-03-15 10:30:00" valeur="0.989"/>
  </pdv>
</pdv_liste>
"""

_PDV_XML_MINIMAL = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid}" latitude="4849000" longitude="234567" cp="75001" pop="R">
    <adresse>10 RUE DE RIVOLI</adresse>
    <ville>PARIS</ville>
    <horaires automate-24-24="1"/>
    <prix nom="Gazole" id="1" maj="2024-03-15 08:00:00" valeur="1.750"/>
  </pdv>
</pdv_liste>
"""

_PDV_XML_TWO_STATIONS = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid1}" latitude="4365100" longitude="354700" cp="34150" pop="R">
    <adresse>1 RUE DES FLEURS</adresse>
    <ville>MONTPELLIER</ville>
    <horaires automate-24-24=""/>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:30:00" valeur="1.799"/>
    <prix nom="SP95" id="2" maj="2024-03-15 10:30:00" valeur="1.849"/>
  </pdv>
  <pdv id="{sid2}" latitude="4849000" longitude="234567" cp="75001" pop="R">
    <adresse>10 RUE DE RIVOLI</adresse>
    <ville>PARIS</ville>
    <horaires automate-24-24="1"/>
    <prix nom="Gazole" id="1" maj="2024-03-15 08:00:00" valeur="1.850"/>
    <prix nom="SP95" id="2" maj="2024-03-15 08:00:00" valeur="1.900"/>
  </pdv>
</pdv_liste>
"""

_PDV_XML_DOM = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="97100001" latitude="1485400" longitude="-6122500" cp="97100" pop="R">
    <adresse>ZONE INDUSTRIELLE</adresse>
    <ville>POINTE-A-PITRE</ville>
    <horaires automate-24-24=""/>
    <prix nom="Gazole" id="1" maj="2024-03-15 12:00:00" valeur="1.650"/>
  </pdv>
</pdv_liste>
"""

_PDV_XML_NO_PRICE = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid}" latitude="4365100" longitude="354700" cp="34150" pop="R">
    <adresse>1 RUE DES FLEURS</adresse>
    <ville>MONTPELLIER</ville>
    <horaires automate-24-24=""/>
  </pdv>
</pdv_liste>
"""

_PDV_XML_MISSING_COORDS = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid}" cp="34150" pop="R">
    <ville>MONTPELLIER</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:30:00" valeur="1.799"/>
  </pdv>
</pdv_liste>
"""


def _make_zip(xml_str: str, filename: str = "PrixCarburants_instantane.xml") -> bytes:
    """Build an in-memory ZIP archive containing the given XML string."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, xml_str.encode("iso-8859-1"))
    return buf.getvalue()


def _make_mock_response(
    status: int,
    body: bytes | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=body or b"")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(response: AsyncMock) -> MagicMock:
    """Return a mock session whose .get() always returns the given response."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


def _xml_bytes(template: str, **kwargs) -> bytes:
    """Format a template and encode as ISO-8859-1 bytes."""
    return template.format(**kwargs).encode("iso-8859-1")


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata_country() -> None:
    """FrCarburantsProvider declares COUNTRY='FR'."""
    assert FrCarburantsProvider.COUNTRY == "FR"


def test_provider_metadata_provider_key() -> None:
    """FrCarburantsProvider declares PROVIDER_KEY='fr_carburants'."""
    assert FrCarburantsProvider.PROVIDER_KEY == "fr_carburants"


def test_provider_metadata_label() -> None:
    """FrCarburantsProvider declares a human-readable label."""
    assert FrCarburantsProvider.LABEL == "Prix Carburants (France)"


def test_provider_metadata_config_mode() -> None:
    """FrCarburantsProvider uses location CONFIG_MODE."""
    assert FrCarburantsProvider.CONFIG_MODE == "location"


def test_provider_metadata_station_lookup_mode() -> None:
    """FrCarburantsProvider uses location_search STATION_LOOKUP_MODE."""
    assert FrCarburantsProvider.STATION_LOOKUP_MODE == "location_search"


def test_provider_poll_interval() -> None:
    """Poll interval is 600 seconds (10 minutes) matching government refresh cadence."""
    assert FrCarburantsProvider.POLL_INTERVAL_SECONDS == 600


def test_provider_capabilities_fuel_types() -> None:
    """CAPABILITIES includes all six French fuel types."""
    caps = FrCarburantsProvider.CAPABILITIES
    for fuel in ("diesel", "unleaded", "premium_unleaded", "e10", "e85", "lpg"):
        assert fuel in caps, f"Fuel type '{fuel}' missing from CAPABILITIES"


def test_provider_capabilities_station_fields() -> None:
    """CAPABILITIES includes standard station identity/location fields."""
    caps = FrCarburantsProvider.CAPABILITIES
    for field in (
        "name",
        "county",
        "address",
        "latitude",
        "longitude",
        "lastupdated",
        "is_open",
    ):
        assert field in caps, f"Field '{field}' missing from CAPABILITIES"


def test_provider_capabilities_coordinator_sentinels() -> None:
    """CAPABILITIES includes coordinator sentinel keys."""
    caps = FrCarburantsProvider.CAPABILITIES
    assert "last_successful_fetch" in caps
    assert "data_fetch_problem" in caps


# ---------------------------------------------------------------------------
# Provider constructor / init
# ---------------------------------------------------------------------------


def test_provider_init_stores_station_id() -> None:
    """Constructor stores station_id for later use."""
    provider = FrCarburantsProvider("34150003")
    assert provider._station_id == "34150003"


def test_provider_init_default_radius() -> None:
    """Constructor defaults radius_km to 10.0 when not supplied."""
    provider = FrCarburantsProvider("34150003")
    assert provider._radius_km == 10.0


def test_provider_init_custom_radius() -> None:
    """Constructor accepts a custom radius_km value."""
    provider = FrCarburantsProvider("34150003", radius_km=25.0)
    assert provider._radius_km == 25.0


def test_provider_init_stores_coordinates() -> None:
    """Constructor stores lat/lng for async_list_stations."""
    provider = FrCarburantsProvider("34150003", latitude=43.651, longitude=3.547)
    assert provider._latitude == pytest.approx(43.651)
    assert provider._longitude == pytest.approx(3.547)


def test_provider_init_stores_county() -> None:
    """Constructor stores optional county parameter."""
    provider = FrCarburantsProvider("34150003", county="Dept. 34")
    assert provider._county == "Dept. 34"


def test_provider_init_none_radius_uses_default() -> None:
    """Constructor treats radius_km=None as 10.0 default."""
    provider = FrCarburantsProvider("34150003", radius_km=None)
    assert provider._radius_km == 10.0


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_data_url_points_to_roulez_eco() -> None:
    """_DATA_URL points at the official French government open-data endpoint."""
    assert "donnees.roulez-eco.fr" in _DATA_URL
    assert _DATA_URL.startswith("https://")


def test_headers_include_user_agent() -> None:
    """_HEADERS includes a User-Agent."""
    assert "User-Agent" in _HEADERS
    assert _HEADERS["User-Agent"]


def test_nom_to_key_mapping_gazole() -> None:
    """_NOM_TO_KEY maps 'Gazole' to 'diesel'."""
    assert _NOM_TO_KEY["Gazole"] == "diesel"


def test_nom_to_key_mapping_sp95() -> None:
    """_NOM_TO_KEY maps 'SP95' to 'unleaded'."""
    assert _NOM_TO_KEY["SP95"] == "unleaded"


def test_nom_to_key_mapping_sp98() -> None:
    """_NOM_TO_KEY maps 'SP98' to 'premium_unleaded'."""
    assert _NOM_TO_KEY["SP98"] == "premium_unleaded"


def test_nom_to_key_mapping_e10() -> None:
    """_NOM_TO_KEY maps 'E10' to 'e10'."""
    assert _NOM_TO_KEY["E10"] == "e10"


def test_nom_to_key_mapping_e85() -> None:
    """_NOM_TO_KEY maps 'E85' to 'e85'."""
    assert _NOM_TO_KEY["E85"] == "e85"


def test_nom_to_key_mapping_gplc() -> None:
    """_NOM_TO_KEY maps 'GPLc' to 'lpg'."""
    assert _NOM_TO_KEY["GPLc"] == "lpg"


# ---------------------------------------------------------------------------
# _parse_coord
# ---------------------------------------------------------------------------


def test_parse_coord_standard_latitude() -> None:
    """_parse_coord divides raw integer by 100000 to produce decimal degrees."""
    assert _parse_coord("4365100") == pytest.approx(43.651)


def test_parse_coord_standard_longitude() -> None:
    """_parse_coord correctly handles longitude values."""
    assert _parse_coord("354700") == pytest.approx(3.547)


def test_parse_coord_negative_value() -> None:
    """_parse_coord handles negative coordinate strings (Guadeloupe, etc.)."""
    assert _parse_coord("-6122500") == pytest.approx(-61.225)


def test_parse_coord_none_input() -> None:
    """_parse_coord returns None when passed None."""
    assert _parse_coord(None) is None


def test_parse_coord_invalid_string() -> None:
    """_parse_coord returns None for non-numeric strings."""
    assert _parse_coord("not_a_number") is None


def test_parse_coord_zero() -> None:
    """_parse_coord handles zero correctly."""
    assert _parse_coord("0") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_standard_value() -> None:
    """_parse_price converts '1.799' to float 1.799."""
    assert _parse_price("1.799") == pytest.approx(1.799)


def test_parse_price_comma_separator() -> None:
    """_parse_price handles comma as decimal separator (French locale)."""
    assert _parse_price("1,799") == pytest.approx(1.799)


def test_parse_price_rounds_to_three_decimal_places() -> None:
    """_parse_price rounds to 3 decimal places."""
    result = _parse_price("1.7994")
    assert result == pytest.approx(1.799, abs=1e-3)


def test_parse_price_none_input() -> None:
    """_parse_price returns None when passed None."""
    assert _parse_price(None) is None


def test_parse_price_empty_string() -> None:
    """_parse_price returns None for empty string."""
    assert _parse_price("") is None


def test_parse_price_zero_returns_none() -> None:
    """_parse_price returns None for zero price (station not selling fuel)."""
    assert _parse_price("0") is None


def test_parse_price_negative_returns_none() -> None:
    """_parse_price returns None for negative price values."""
    assert _parse_price("-1.5") is None


def test_parse_price_invalid_string() -> None:
    """_parse_price returns None for non-numeric strings."""
    assert _parse_price("N/A") is None


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------


def test_haversine_km_same_point_is_zero() -> None:
    """Distance from a point to itself is 0."""
    assert _haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == pytest.approx(
        0.0, abs=1e-6
    )


def test_haversine_km_paris_to_lyon() -> None:
    """Paris to Lyon should be approximately 392 km."""
    dist = _haversine_km(48.8566, 2.3522, 45.7640, 4.8357)
    assert 380 < dist < 410


def test_haversine_km_short_distance() -> None:
    """Two points 1 km apart should return approximately 1 km."""
    # Move ~1 km north from reference point
    dist = _haversine_km(43.600, 3.870, 43.609, 3.870)
    assert 0.9 < dist < 1.1


def test_haversine_km_returns_float() -> None:
    """_haversine_km always returns a float."""
    result = _haversine_km(48.8566, 2.3522, 45.7640, 4.8357)
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# _parse_pdv
# ---------------------------------------------------------------------------


def test_parse_pdv_station_id() -> None:
    """_parse_pdv extracts the id attribute from the pdv element."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["id"] == _STATION_ID


def test_parse_pdv_coordinates() -> None:
    """_parse_pdv converts scaled integer coordinates to decimal degrees."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["latitude"] == pytest.approx(43.651)
    assert result["longitude"] == pytest.approx(3.547)


def test_parse_pdv_name_format() -> None:
    """_parse_pdv builds name as 'VILLE (CP)' format."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["name"] == "MONTPELLIER (34150)"


def test_parse_pdv_county_metropolitan() -> None:
    """_parse_pdv derives county as 'Dept. XX' from first 2 digits of postal code."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["county"] == "Dept. 34"


def test_parse_pdv_county_dom_97x() -> None:
    """_parse_pdv uses 3-digit department prefix for DOM postal codes starting with 97."""
    xml_str = _PDV_XML_DOM
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["county"] == "Dept. 971"


def test_parse_pdv_address_combines_street_and_city() -> None:
    """_parse_pdv combines adresse and ville into the address field."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["address"] == "1 RUE DES FLEURS, MONTPELLIER"


def test_parse_pdv_is_open_none_when_empty_attribute() -> None:
    """_parse_pdv returns is_open=None for staffed stations (no real-time status)."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["is_open"] is None


def test_parse_pdv_is_open_true_when_attribute_is_1() -> None:
    """_parse_pdv returns is_open=True when automate-24-24 attribute is '1'."""
    xml_str = _PDV_XML_MINIMAL.format(sid=_STATION_ID)
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["is_open"] is True


def test_parse_pdv_lastupdated_from_first_prix_maj() -> None:
    """_parse_pdv captures lastupdated from the first <prix maj=...> attribute."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["lastupdated"] == "2024-03-15 10:30:00"


def test_parse_pdv_prices_all_fuel_types() -> None:
    """_parse_pdv extracts all six fuel type prices."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    prices = result["prices"]
    assert prices["diesel"] == pytest.approx(1.799)
    assert prices["unleaded"] == pytest.approx(1.849)
    assert prices["premium_unleaded"] == pytest.approx(1.929)
    assert prices["e10"] == pytest.approx(1.829)
    assert prices["e85"] == pytest.approx(0.899)
    assert prices["lpg"] == pytest.approx(0.989)


def test_parse_pdv_prices_empty_when_no_prix_elements() -> None:
    """_parse_pdv returns empty prices dict when no <prix> children present."""
    xml_str = _PDV_XML_NO_PRICE.format(sid=_STATION_ID)
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["prices"] == {}


def test_parse_pdv_no_horaires_gives_none_is_open() -> None:
    """_parse_pdv returns is_open=None when no <horaires> element is present."""
    xml_str = _PDV_XML_NO_PRICE.format(sid=_STATION_ID)
    # Remove the horaires element manually
    xml_no_horaires = xml_str.replace('<horaires automate-24-24=""/>', "")
    root = ET.fromstring(xml_no_horaires.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["is_open"] is None


def test_parse_pdv_name_only_ville_when_no_cp() -> None:
    """_parse_pdv uses only ville when no cp attribute present."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="99001" latitude="4849000" longitude="234567" pop="R">
    <ville>BORDEAUX</ville>
  </pdv>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["name"] == "BORDEAUX"
    assert result["county"] is None


def test_parse_pdv_name_only_cp_when_no_ville() -> None:
    """_parse_pdv uses only cp when no <ville> element present."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="13001" latitude="4835100" longitude="216700" cp="13001" pop="R">
    <adresse>5 RUE DU PORT</adresse>
  </pdv>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["name"] == "13001"


def test_parse_pdv_address_only_adresse_when_no_ville() -> None:
    """_parse_pdv uses only adresse for address when no <ville> element present."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="13001" latitude="4835100" longitude="216700" cp="13001" pop="R">
    <adresse>5 RUE DU PORT</adresse>
  </pdv>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["address"] == "5 RUE DU PORT"


def test_parse_pdv_address_only_ville_when_no_adresse() -> None:
    """_parse_pdv uses only ville for address when no <adresse> element present."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="99002" latitude="4849000" longitude="234567" cp="75001" pop="R">
    <ville>PARIS</ville>
  </pdv>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["address"] == "PARIS"


def test_parse_pdv_address_none_when_neither_present() -> None:
    """_parse_pdv returns address=None when both adresse and ville are absent."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="99003" latitude="4849000" longitude="234567" cp="75001" pop="R"/>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["address"] is None


def test_parse_pdv_name_none_when_ville_and_cp_absent() -> None:
    """_parse_pdv returns name=None when both ville and cp are absent."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="99003" latitude="4849000" longitude="234567" pop="R"/>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert result["name"] is None


def test_parse_pdv_unknown_nom_ignored() -> None:
    """_parse_pdv ignores <prix> elements with unknown nom attributes."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>TEST</ville>
    <prix nom="UnknownFuel" id="99" maj="2024-03-15 10:00:00" valeur="2.500"/>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
</pdv_liste>"""
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    pdv = next(root.iter("pdv"))
    result = _parse_pdv(pdv)
    assert "diesel" in result["prices"]
    assert len(result["prices"]) == 1


# ---------------------------------------------------------------------------
# _build_station_data
# ---------------------------------------------------------------------------


def test_build_station_data_all_fuel_keys_present() -> None:
    """_build_station_data output includes all six fuel type keys."""
    raw = {
        "id": _STATION_ID,
        "latitude": 43.651,
        "longitude": 3.547,
        "cp": "34150",
        "name": "MONTPELLIER (34150)",
        "county": "Dept. 34",
        "address": "1 RUE DES FLEURS, MONTPELLIER",
        "is_open": None,
        "lastupdated": "2024-03-15 10:30:00",
        "prices": {
            "diesel": 1.799,
            "unleaded": 1.849,
            "premium_unleaded": 1.929,
            "e10": 1.829,
            "e85": 0.899,
            "lpg": 0.989,
        },
    }
    result = _build_station_data(raw)
    for fuel in ("diesel", "unleaded", "premium_unleaded", "e10", "e85", "lpg"):
        assert fuel in result, (
            f"Fuel key '{fuel}' missing from _build_station_data output"
        )


def test_build_station_data_prices_correct() -> None:
    """_build_station_data passes through fuel prices without modification."""
    raw = {
        "id": _STATION_ID,
        "latitude": 43.651,
        "longitude": 3.547,
        "name": "MONTPELLIER (34150)",
        "county": "Dept. 34",
        "address": "1 RUE DES FLEURS, MONTPELLIER",
        "is_open": None,
        "lastupdated": "2024-03-15 10:30:00",
        "prices": {"diesel": 1.799, "unleaded": 1.849},
    }
    result = _build_station_data(raw)
    assert result["diesel"] == pytest.approx(1.799)
    assert result["unleaded"] == pytest.approx(1.849)


def test_build_station_data_missing_fuel_is_none() -> None:
    """_build_station_data returns None for fuel types not in prices dict."""
    raw = {
        "id": _STATION_ID,
        "latitude": 43.651,
        "longitude": 3.547,
        "name": "MONTPELLIER (34150)",
        "county": "Dept. 34",
        "address": "1 RUE DES FLEURS, MONTPELLIER",
        "is_open": None,
        "lastupdated": "2024-03-15 10:30:00",
        "prices": {"diesel": 1.799},
    }
    result = _build_station_data(raw)
    assert result["unleaded"] is None
    assert result["premium_unleaded"] is None
    assert result["e10"] is None
    assert result["e85"] is None
    assert result["lpg"] is None


def test_build_station_data_maps_identity_fields() -> None:
    """_build_station_data maps name, county, address, lat, lng, is_open, lastupdated."""
    raw = {
        "id": _STATION_ID,
        "latitude": 43.651,
        "longitude": 3.547,
        "name": "MONTPELLIER (34150)",
        "county": "Dept. 34",
        "address": "1 RUE DES FLEURS, MONTPELLIER",
        "is_open": True,
        "lastupdated": "2024-03-15 10:30:00",
        "prices": {},
    }
    result = _build_station_data(raw)
    assert result["name"] == "MONTPELLIER (34150)"
    assert result["county"] == "Dept. 34"
    assert result["address"] == "1 RUE DES FLEURS, MONTPELLIER"
    assert result["latitude"] == pytest.approx(43.651)
    assert result["longitude"] == pytest.approx(3.547)
    assert result["is_open"] is True
    assert result["lastupdated"] == "2024-03-15 10:30:00"


def test_build_station_data_source_station_id() -> None:
    """_build_station_data stores the original station ID as source_station_id."""
    raw = {
        "id": _STATION_ID,
        "latitude": 43.651,
        "longitude": 3.547,
        "name": "MONTPELLIER (34150)",
        "county": "Dept. 34",
        "address": None,
        "is_open": None,
        "lastupdated": None,
        "prices": {},
    }
    result = _build_station_data(raw)
    assert result["source_station_id"] == _STATION_ID


def test_build_station_data_empty_prices() -> None:
    """_build_station_data handles empty prices dict (all fuels None)."""
    raw = {
        "id": _STATION_ID,
        "latitude": None,
        "longitude": None,
        "name": None,
        "county": None,
        "address": None,
        "is_open": None,
        "lastupdated": None,
        "prices": {},
    }
    result = _build_station_data(raw)
    assert result["diesel"] is None
    assert result["unleaded"] is None


# ---------------------------------------------------------------------------
# _find_station_in_root
# ---------------------------------------------------------------------------


def test_find_station_in_root_returns_matching_station() -> None:
    """_find_station_in_root returns parsed station dict for the requested ID."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    result = _find_station_in_root(root, _STATION_ID)
    assert result is not None
    assert result["id"] == _STATION_ID


def test_find_station_in_root_returns_none_when_not_found() -> None:
    """_find_station_in_root returns None when station ID is absent from root."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    result = _find_station_in_root(root, "99999999")
    assert result is None


def test_find_station_in_root_selects_correct_station_from_multiple() -> None:
    """_find_station_in_root returns the correct station among multiple stations."""
    xml_str = _PDV_XML_TWO_STATIONS.format(sid1=_STATION_ID, sid2=_OTHER_ID)
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    result = _find_station_in_root(root, _OTHER_ID)
    assert result is not None
    assert result["id"] == _OTHER_ID
    assert result["name"] == "PARIS (75001)"


def test_find_station_in_root_malformed_xml_raises_parse_error() -> None:
    """ET.fromstring raises ET.ParseError for invalid XML bytes (root never created)."""
    with pytest.raises(ET.ParseError):
        ET.fromstring(b"this is not xml at all {{{{")


def test_find_station_in_root_parses_prices_correctly() -> None:
    """_find_station_in_root includes correctly parsed prices in returned dict."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    root = ET.fromstring(xml_str.encode("iso-8859-1"))
    result = _find_station_in_root(root, _STATION_ID)
    assert result is not None
    assert result["prices"]["diesel"] == pytest.approx(1.799)
    assert result["prices"]["lpg"] == pytest.approx(0.989)


# ---------------------------------------------------------------------------
# async_fetch — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_success_returns_station_data() -> None:
    """async_fetch returns a populated StationData dict for a known station ID."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data is not None
    assert data["diesel"] == pytest.approx(1.799)


async def test_async_fetch_success_diesel_price() -> None:
    """async_fetch populates diesel price in EUR/litre."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["diesel"] == pytest.approx(1.799)


async def test_async_fetch_success_unleaded_price() -> None:
    """async_fetch populates unleaded (SP95) price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["unleaded"] == pytest.approx(1.849)


async def test_async_fetch_success_premium_unleaded_price() -> None:
    """async_fetch populates premium_unleaded (SP98) price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["premium_unleaded"] == pytest.approx(1.929)


async def test_async_fetch_success_e10_price() -> None:
    """async_fetch populates e10 price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["e10"] == pytest.approx(1.829)


async def test_async_fetch_success_e85_price() -> None:
    """async_fetch populates e85 price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["e85"] == pytest.approx(0.899)


async def test_async_fetch_success_lpg_price() -> None:
    """async_fetch populates lpg (GPLc) price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lpg"] == pytest.approx(0.989)


async def test_async_fetch_prices_not_divided_by_100() -> None:
    """async_fetch does NOT apply a /100 conversion — prices are already EUR/litre."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # 1.799 must stay 1.799, never 0.01799
    assert data["diesel"] < 10.0
    assert data["diesel"] == pytest.approx(1.799)


# ---------------------------------------------------------------------------
# async_fetch — field normalisation / mapping
# ---------------------------------------------------------------------------


async def test_async_fetch_field_name() -> None:
    """async_fetch populates name field from ville + cp."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["name"] == "MONTPELLIER (34150)"


async def test_async_fetch_field_county() -> None:
    """async_fetch populates county from département prefix."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["county"] == "Dept. 34"


async def test_async_fetch_field_address() -> None:
    """async_fetch populates address combining street and city."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["address"] == "1 RUE DES FLEURS, MONTPELLIER"


async def test_async_fetch_field_latitude() -> None:
    """async_fetch populates latitude as decimal degrees."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["latitude"] == pytest.approx(43.651)


async def test_async_fetch_field_longitude() -> None:
    """async_fetch populates longitude as decimal degrees."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["longitude"] == pytest.approx(3.547)


async def test_async_fetch_field_is_open_none_for_staffed() -> None:
    """async_fetch returns is_open=None for staffed stations (no real-time status)."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["is_open"] is None


async def test_async_fetch_field_is_open_true() -> None:
    """async_fetch returns is_open=True when automate-24-24 is '1'."""
    zip_bytes = _make_zip(_PDV_XML_MINIMAL.format(sid=_STATION_ID))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["is_open"] is True


async def test_async_fetch_field_lastupdated() -> None:
    """async_fetch populates lastupdated from the first <prix maj=...> timestamp."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["lastupdated"] == "2024-03-15 10:30:00"


async def test_async_fetch_all_capabilities_keys_present() -> None:
    """async_fetch output contains all CAPABILITIES keys (excluding sentinels)."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    # Exclude coordinator sentinels (injected by coordinator, not provider)
    sentinel_keys = {"last_successful_fetch", "data_fetch_problem"}
    provider_caps = FrCarburantsProvider.CAPABILITIES - sentinel_keys
    for key in provider_caps:
        assert key in data, f"CAPABILITIES key '{key}' missing from async_fetch output"


async def test_async_fetch_source_station_id_populated() -> None:
    """async_fetch stores the station's source ID in source_station_id."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    data = await provider.async_fetch(session, _STATION_ID)

    assert data["source_station_id"] == _STATION_ID


# ---------------------------------------------------------------------------
# async_fetch — station not found → ProviderError
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_station_not_found() -> None:
    """async_fetch raises ProviderError when station ID is absent from the XML."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid="99999999", auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError, match=_STATION_ID):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_provider_error_message_contains_station_id() -> None:
    """ProviderError message includes the station ID that was not found."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid="00000000", auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError) as exc_info:
        await provider.async_fetch(session, _STATION_ID)
    assert _STATION_ID in str(exc_info.value)


# ---------------------------------------------------------------------------
# async_fetch — HTTP / network error propagation
# ---------------------------------------------------------------------------


async def test_async_fetch_propagates_client_error() -> None:
    """async_fetch propagates aiohttp.ClientError (coordinator converts to UpdateFailed)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_for_status_on_non_200() -> None:
    """async_fetch calls raise_for_status; HTTP errors propagate to caller."""
    resp = _make_mock_response(500, body=b"Internal Server Error")
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ClientError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_for_empty_zip() -> None:
    """async_fetch raises ProviderError when ZIP archive is empty (no files)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as _zf:
        pass  # empty ZIP
    empty_zip = buf.getvalue()

    resp = _make_mock_response(200, body=empty_zip)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError, match="empty"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_for_bad_zip() -> None:
    """async_fetch raises ProviderError when response body is not a valid ZIP."""
    resp = _make_mock_response(200, body=b"this is not a zip file")
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError, match="ZIP"):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_raises_provider_error_for_malformed_xml() -> None:
    """async_fetch raises ProviderError when the extracted XML is malformed."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("data.xml", b"<broken xml <<< not valid")
    broken_zip = buf.getvalue()

    resp = _make_mock_response(200, body=broken_zip)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider.async_fetch(session, _STATION_ID)


async def test_async_fetch_sends_correct_url() -> None:
    """async_fetch issues GET request to the Prix Carburants endpoint URL."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_args = session.get.call_args
    assert call_args[0][0] == _DATA_URL


async def test_async_fetch_sends_headers() -> None:
    """async_fetch passes the module _HEADERS dict on each GET request."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    await provider.async_fetch(session, _STATION_ID)

    call_kwargs = session.get.call_args.kwargs
    assert "headers" in call_kwargs
    assert call_kwargs["headers"] == _HEADERS


# ---------------------------------------------------------------------------
# async_fetch_station_name — success path
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_name_on_success() -> None:
    """async_fetch_station_name returns the station name when lookup succeeds."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "MONTPELLIER (34150)"


async def test_async_fetch_station_name_minimal_station() -> None:
    """async_fetch_station_name returns name from minimal station data."""
    zip_bytes = _make_zip(_PDV_XML_MINIMAL.format(sid=_STATION_ID))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name == "PARIS (75001)"


# ---------------------------------------------------------------------------
# async_fetch_station_name — error / not-found paths
# ---------------------------------------------------------------------------


async def test_async_fetch_station_name_returns_none_when_not_found() -> None:
    """async_fetch_station_name returns None when station ID is absent from XML."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid="99999999", auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_client_error() -> None:
    """async_fetch_station_name returns None (swallows) when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("connection refused"))

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_bad_zip() -> None:
    """async_fetch_station_name returns None when response body is not a ZIP."""
    resp = _make_mock_response(200, body=b"not a zip")
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_on_http_error() -> None:
    """async_fetch_station_name returns None when HTTP error is raised."""
    resp = _make_mock_response(503, body=b"Service Unavailable")
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("503 Service Unavailable")
    )
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


async def test_async_fetch_station_name_returns_none_when_name_is_none() -> None:
    """async_fetch_station_name returns None when station has no name derivable."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="{sid}" latitude="4849000" longitude="234567" pop="R"/>
</pdv_liste>""".format(sid=_STATION_ID)
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    name = await provider.async_fetch_station_name(session, _STATION_ID)

    assert name is None


# ---------------------------------------------------------------------------
# async_list_stations — success path
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_nearby_stations() -> None:
    """async_list_stations returns station tuples for stations within radius."""
    # Montpellier coords: lat=43.651, lon=3.547
    zip_bytes = _make_zip(
        _PDV_XML_TWO_STATIONS.format(sid1=_STATION_ID, sid2=_OTHER_ID)
    )
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    # Use Montpellier as center with large radius to include both stations
    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=1000.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 2
    ids = [sid for sid, _ in results]
    assert _STATION_ID in ids
    assert _OTHER_ID in ids


async def test_async_list_stations_returns_list_of_tuples() -> None:
    """async_list_stations returns a list of (station_id, label) 2-tuples."""
    zip_bytes = _make_zip(
        _PDV_XML_TWO_STATIONS.format(sid1=_STATION_ID, sid2=_OTHER_ID)
    )
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=1000.0
    )
    results = await provider.async_list_stations(session)

    for item in results:
        assert len(item) == 2
        sid, label = item
        assert isinstance(sid, str)
        assert isinstance(label, str)


async def test_async_list_stations_label_includes_diesel_price() -> None:
    """async_list_stations label includes 'Diesel €x.xxx' when diesel price available."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 1
    _sid, label = results[0]
    assert "Diesel" in label
    assert "1.799" in label


async def test_async_list_stations_label_includes_sp_price() -> None:
    """async_list_stations label includes 'SP €x.xxx' for unleaded/e10 price."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    _sid, label = results[0]
    assert "SP" in label


async def test_async_list_stations_sorted_by_diesel_price_ascending() -> None:
    """async_list_stations sorts results by diesel price, cheapest first."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>CHEAP</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.699"/>
  </pdv>
  <pdv id="34002" latitude="4365200" longitude="354800" cp="34002" pop="R">
    <ville>EXPENSIVE</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.899"/>
  </pdv>
  <pdv id="34003" latitude="4365300" longitude="354900" cp="34003" pop="R">
    <ville>MEDIUM</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34001", latitude=43.651, longitude=3.547, radius_km=100.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 3
    # First result should be cheapest diesel
    assert results[0][0] == "34001"
    # Last result should be most expensive diesel
    assert results[2][0] == "34002"


async def test_async_list_stations_no_diesel_sorts_last() -> None:
    """async_list_stations puts stations with no diesel price at the end."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>HAS DIESEL</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
  <pdv id="34002" latitude="4365200" longitude="354800" cp="34002" pop="R">
    <ville>NO DIESEL</ville>
    <prix nom="SP95" id="2" maj="2024-03-15 10:00:00" valeur="1.899"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34001", latitude=43.651, longitude=3.547, radius_km=100.0
    )
    results = await provider.async_list_stations(session)

    # Station with diesel should come first
    assert results[0][0] == "34001"
    assert results[1][0] == "34002"


async def test_async_list_stations_filters_by_radius() -> None:
    """async_list_stations excludes stations outside the radius."""
    # Montpellier station is ~0 km away; Paris station is ~600 km away
    zip_bytes = _make_zip(
        _PDV_XML_TWO_STATIONS.format(sid1=_STATION_ID, sid2=_OTHER_ID)
    )
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=5.0
    )
    results = await provider.async_list_stations(session)

    ids = [sid for sid, _ in results]
    assert _STATION_ID in ids
    assert _OTHER_ID not in ids


async def test_async_list_stations_uses_kwargs_lat_lng() -> None:
    """async_list_stations uses lat/lng passed via kwargs, overriding stored coords."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=0.0, longitude=0.0, radius_km=5.0
    )
    # Override with Montpellier coords via kwargs
    results = await provider.async_list_stations(
        session, lat=43.651, lng=3.547, radius_km=5.0
    )

    assert len(results) == 1
    assert results[0][0] == _STATION_ID


async def test_async_list_stations_uses_kwargs_radius_km() -> None:
    """async_list_stations uses radius_km passed via kwargs."""
    zip_bytes = _make_zip(
        _PDV_XML_TWO_STATIONS.format(sid1=_STATION_ID, sid2=_OTHER_ID)
    )
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    # Montpellier center, very small radius — only local station in range
    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=1000.0
    )
    results = await provider.async_list_stations(
        session, lat=43.651, lng=3.547, radius_km=5.0
    )

    ids = [sid for sid, _ in results]
    assert _STATION_ID in ids
    assert _OTHER_ID not in ids


# ---------------------------------------------------------------------------
# async_list_stations — empty / error paths
# ---------------------------------------------------------------------------


async def test_async_list_stations_returns_empty_when_no_lat_lng() -> None:
    """async_list_stations returns empty list when no lat/lng is available."""
    session = MagicMock()

    provider = FrCarburantsProvider(_STATION_ID)
    # Neither stored nor kwargs lat/lng
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_client_error() -> None:
    """async_list_stations returns empty list when a network error occurs."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_bad_zip() -> None:
    """async_list_stations returns empty list when response is not a valid ZIP."""
    resp = _make_mock_response(200, body=b"not a zip file")
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_returns_empty_on_http_error() -> None:
    """async_list_stations returns empty list when HTTP error occurs."""
    resp = _make_mock_response(500, body=b"error")
    resp.raise_for_status = MagicMock(
        side_effect=ClientError("500 Internal Server Error")
    )
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert results == []


async def test_async_list_stations_skips_stations_without_coordinates() -> None:
    """async_list_stations silently skips stations with no lat/lng attributes."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" cp="34001" pop="R">
    <ville>NO COORDS</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
  <pdv id="34002" latitude="4365200" longitude="354800" cp="34002" pop="R">
    <ville>HAS COORDS</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34002", latitude=43.652, longitude=3.548, radius_km=100.0
    )
    results = await provider.async_list_stations(session)

    ids = [sid for sid, _ in results]
    assert "34001" not in ids
    assert "34002" in ids


async def test_async_list_stations_skips_stations_without_id() -> None:
    """async_list_stations silently skips stations with empty id attribute."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>NO ID</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
  <pdv id="34002" latitude="4365200" longitude="354800" cp="34002" pop="R">
    <ville>HAS ID</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34002", latitude=43.652, longitude=3.548, radius_km=100.0
    )
    results = await provider.async_list_stations(session)

    ids = [sid for sid, _ in results]
    assert "" not in ids
    assert "34002" in ids


async def test_async_list_stations_label_uses_sid_when_no_name() -> None:
    """async_list_stations uses station ID as label name when name cannot be derived."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" pop="R">
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34001", latitude=43.651, longitude=3.547, radius_km=100.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 1
    _sid, label = results[0]
    assert "34001" in label


async def test_async_list_stations_label_includes_address_when_available() -> None:
    """async_list_stations label includes address when station has address data."""
    zip_bytes = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        _STATION_ID, latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    _sid, label = results[0]
    assert "RUE DES FLEURS" in label or "MONTPELLIER" in label


async def test_async_list_stations_e10_used_as_sp_fallback() -> None:
    """async_list_stations uses e10 price as SP fallback when unleaded (SP95) absent."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>E10 ONLY</ville>
    <prix nom="Gazole" id="1" maj="2024-03-15 10:00:00" valeur="1.799"/>
    <prix nom="E10" id="4" maj="2024-03-15 10:00:00" valeur="1.829"/>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34001", latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 1
    _sid, label = results[0]
    assert "SP" in label
    assert "1.829" in label


async def test_async_list_stations_no_price_label_has_no_euro_sign() -> None:
    """async_list_stations label has no price hint when station has no prices."""
    xml_str = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<pdv_liste>
  <pdv id="34001" latitude="4365100" longitude="354700" cp="34001" pop="R">
    <ville>NO PRICES</ville>
  </pdv>
</pdv_liste>"""
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(
        "34001", latitude=43.651, longitude=3.547, radius_km=10.0
    )
    results = await provider.async_list_stations(session)

    assert len(results) == 1
    _sid, label = results[0]
    assert "€" not in label


# ---------------------------------------------------------------------------
# _fetch_xml internal helper (via provider method)
# ---------------------------------------------------------------------------


async def test_fetch_xml_returns_xml_bytes() -> None:
    """_fetch_xml returns the raw XML bytes extracted from the ZIP."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    xml_bytes = await provider._fetch_xml(session)

    assert isinstance(xml_bytes, bytes)
    # Should be parseable XML
    root = ET.fromstring(xml_bytes)
    assert root is not None


async def test_fetch_xml_reads_first_file_from_zip() -> None:
    """_fetch_xml reads names[0] from the ZIP archive."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    zip_bytes = _make_zip(xml_str, filename="PrixCarburants_instantane.xml")
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    xml_bytes = await provider._fetch_xml(session)

    # The returned bytes must be the XML content, not the ZIP
    assert b"<?xml" in xml_bytes or b"<pdv_liste" in xml_bytes


async def test_fetch_xml_raises_provider_error_empty_zip() -> None:
    """_fetch_xml raises ProviderError when ZIP has no files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as _zf:
        pass
    resp = _make_mock_response(200, body=buf.getvalue())
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider._fetch_xml(session)


async def test_fetch_xml_raises_provider_error_bad_zip() -> None:
    """_fetch_xml raises ProviderError when response body is not a valid ZIP."""
    resp = _make_mock_response(200, body=b"garbage bytes not a zip")
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError):
        await provider._fetch_xml(session)


async def test_fetch_xml_calls_raise_for_status() -> None:
    """_fetch_xml calls raise_for_status on the response object."""
    xml_str = _PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24="")
    zip_bytes = _make_zip(xml_str)
    resp = _make_mock_response(200, body=zip_bytes)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    await provider._fetch_xml(session)

    resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# _fetch_and_parse_xml — cache hit path (lines 472-476)
# ---------------------------------------------------------------------------


async def test_fetch_and_parse_xml_returns_cached_element_on_cache_hit() -> None:
    """_fetch_and_parse_xml returns cached element and skips HTTP when cache is fresh."""
    cached_root = ET.fromstring("<pdv_liste/>")
    FrCarburantsProvider._xml_cache = cached_root
    # A far-future timestamp means (now - ts) is negative, always less than TTL.
    FrCarburantsProvider._xml_cache_ts = 1e18

    session = MagicMock()
    provider = FrCarburantsProvider(_STATION_ID)
    result = await provider._fetch_and_parse_xml(session)

    assert result is cached_root
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_xml — size-limit branch (line 522)
# ---------------------------------------------------------------------------


async def test_fetch_xml_raises_provider_error_when_xml_exceeds_size_limit() -> None:
    """_fetch_xml raises ProviderError when the ZIP entry's uncompressed size exceeds 50 MB."""
    # Build a valid ZIP, then patch the uncompressed-size field in the central
    # directory so ZipInfo.file_size reports > 50_000_000.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.xml", b"<pdv_liste/>")
    z = bytearray(buf.getvalue())

    # Patch uncompressed size (4-byte LE at offset +24 in the central directory header).
    cd_idx = z.find(b"PK\x01\x02")
    assert cd_idx != -1, "central directory signature not found"
    us_offset = cd_idx + 24
    z[us_offset : us_offset + 4] = (60_000_000).to_bytes(4, "little")

    resp = _make_mock_response(200, body=bytes(z))
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)
    with pytest.raises(ProviderError, match="exceeds size limit"):
        await provider._fetch_xml(session)


# ---------------------------------------------------------------------------
# _fetch_xml — generic extraction error branch (line 536)
# ---------------------------------------------------------------------------


async def test_fetch_xml_raises_provider_error_on_generic_extraction_error() -> None:
    """_fetch_xml wraps non-BadZipFile extraction errors in ProviderError."""
    from unittest.mock import patch

    # Make _extract_xml raise a non-BadZipFile exception (e.g. RuntimeError)
    # by patching zipfile.ZipFile so it raises inside the executor.
    valid_zip = _make_zip(_PDV_XML_TEMPLATE.format(sid=_STATION_ID, auto24=""))
    resp = _make_mock_response(200, body=valid_zip)
    session = _make_session(resp)

    provider = FrCarburantsProvider(_STATION_ID)

    def _raise_runtime(*args, **kwargs):
        raise RuntimeError("unexpected extraction error")

    with patch(
        "custom_components.fuelcompare_ie.providers.fr_carburants.zipfile.ZipFile",
        _raise_runtime,
    ):
        with pytest.raises(ProviderError, match="ZIP extraction failed"):
            await provider._fetch_xml(session)
