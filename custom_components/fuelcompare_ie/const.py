"""Constants for the Fuel Compare integration."""

import aiohttp as _aiohttp
import homeassistant.const as _ha_const

DOMAIN = "fuelcompare_ie"

# Config flow
CONF_STATION_ID = "station_id"
CONF_COUNTRY = "country"
CONF_PROVIDER = "provider"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS_KM = "radius_km"
CONF_STATION_COUNTY = "station_county"  # stored for county_search providers
CONF_API_KEY = "api_key"  # optional API key for providers that require authentication
CONF_POSTAL_CODE = "postal_code"  # for postal-code-centric providers (e.g. be_carbu)

# Defaults
DEFAULT_COUNTRY = "IE"
DEFAULT_PROVIDER = "ie_fuelcompare"
DEFAULT_RADIUS_KM = 10.0

# API
BASE_URL = "https://fuelcompare.ie"
API_TIMEOUT = 10

# Dynamic User-Agent string using actual HA and aiohttp versions.
# Import with: from .const import UA_HEADER
UA_HEADER: str = f"HomeAssistant/{_ha_const.__version__} aiohttp/{_aiohttp.__version__}"

# Day name tuple used by working_hours sensors (index matches datetime.weekday())
DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
