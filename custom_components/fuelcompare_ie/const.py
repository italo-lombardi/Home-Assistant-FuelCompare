"""Constants for the Fuel Compare integration."""

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
DEFAULT_SCAN_INTERVAL = 1800  # 30 minutes
DEFAULT_COUNTRY = "IE"
DEFAULT_PROVIDER = "ie_fuelcompare"
DEFAULT_RADIUS_KM = 10.0

# API
BASE_URL = "https://fuelcompare.ie"
API_TIMEOUT = 10

# Fuel types available
FUEL_TYPES = ["unleaded", "diesel"]
