# Fuel Compare for Home Assistant

Track live fuel prices from petrol stations across 27 countries directly in your Home Assistant dashboard.

![Sensors](assets/sensors.png)

## Features

- **36 providers across 27 countries** — Ireland, UK, Germany, France, Spain, Italy, Portugal, Netherlands, Belgium, Austria, Switzerland, Norway, Sweden, Denmark, Finland, Poland, Czech Republic, Greece, Croatia, Slovenia, Luxembourg, Lithuania, Iceland, Moldova, Montenegro, Malta, Bosnia & Herzegovina, Australia (WA/NSW/QLD/VIC), Canada (Quebec), and the EU Oil Bulletin
- Live fuel prices: unleaded, diesel, E10, E85, LPG, CNG, premium grades, kerosene, and AdBlue where available
- Opening hours, station name, brand, address, coordinates, and facility sensors per station
- `data_fetch_problem` binary sensor and `last_successful_fetch` timestamp sensor for automations
- Stale-retention: entities keep their last known value through transient outages
- Station picker in config flow for location-based providers — sorted cheapest-first
- Currency-aware: EUR, GBP, AUD per provider
- Translated into 25 languages
- No YAML required

## Setup

1. Install via HACS
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Fuel Compare**
4. Select your country and data source
5. For location-based providers: enter coordinates and radius, then pick a station from the sorted list
6. For Ireland fuelcompare.ie: enter the numeric station ID from the station URL
