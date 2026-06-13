"""Constants for the Fuel Compare integration."""

DOMAIN = "fuelcompare_ie"

# Config flow
CONF_STATION_ID = "station_id"
CONF_COUNTRY = "country"
CONF_PROVIDER = "provider"

# Defaults
DEFAULT_SCAN_INTERVAL = 1800  # 30 minutes
DEFAULT_COUNTRY = "IE"
DEFAULT_PROVIDER = "ie_fuelcompare"

# API
BASE_URL = "https://fuelcompare.ie"
API_TIMEOUT = 10

# Fuel types available
FUEL_TYPES = ["unleaded", "diesel"]
