# Fuel Compare for Home Assistant

Live fuel prices from 36 providers across 30 countries (plus EU) — Ireland, UK, Germany, France, Australia, and more — straight into your Home Assistant dashboard. Covers unleaded, diesel, E10, LPG, and premium grades where available. No YAML required.

![Sensors](assets/sensors.png)

## Features

- Opening hours, station name, brand, address, coordinates, and facility sensors per station
- `data_fetch_problem` binary sensor and `last_successful_fetch` timestamp sensor for automations
- Stale-retention: entities keep their last known value through transient outages
- Station picker in config flow for location-based providers — sorted cheapest-first
- Currency-aware: EUR, GBP, AUD per provider
- Translated into 35 languages

## Setup

1. Install via HACS
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Fuel Compare**
4. Select your country and data source
5. For location-based providers: enter coordinates and radius, then pick a station from the sorted list
6. For Ireland fuelcompare.ie: enter the numeric station ID from the station URL
