"""Constants for the FuelCompare.ie integration."""

DOMAIN = "fuelcompare_ie"

# Config flow
CONF_STATION_ID = "station_id"

# Defaults
DEFAULT_NAME = "FuelCompare.ie"
DEFAULT_SCAN_INTERVAL = 1800  # 30 minutes

# API
BASE_URL = "https://fuelcompare.ie"
API_TIMEOUT = 10

# Fuel types available
FUEL_TYPES = ["unleaded", "diesel"]
