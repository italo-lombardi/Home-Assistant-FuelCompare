# FuelCompare.ie for Home Assistant

Track live unleaded and diesel prices from Irish petrol stations directly in your Home Assistant dashboard.

![Sensors](custom_components/fuelcompare_ie/docs/sensors.png)

## Features

- Live unleaded and diesel prices for any station on [fuelcompare.ie](https://fuelcompare.ie)
- Timestamp sensor showing when prices were last updated on fuelcompare.ie
- Opening hours, full station name, brand, county, and facilities sensors per station
- `station_id` exposed on all entity attributes for easy use in automations
- Prices refresh automatically every 30 minutes
- Add as many stations as you like
- Easy setup — no YAML, no API keys, station name auto-fetched during setup
- Stale-retention on transient outages: entities keep their last known value when the site is offline or throttling, with a dedicated `data_fetch_problem` problem binary sensor and `last_successful_fetch` timestamp sensor for automations
- Translated into 25 languages

## Setup

1. Install via HACS
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **FuelCompare.ie**
4. Enter the station ID from the fuelcompare.ie URL (e.g. `790`)
5. Confirm or customise the station name (auto-fetched from the site)

> This is an unofficial integration with no affiliation to FuelCompare.ie.
