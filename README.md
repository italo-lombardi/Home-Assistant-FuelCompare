# Fuel Compare — Home Assistant Custom Integration

<a href="https://analytics.home-assistant.io"><img src="https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=%24.fuelcompare_ie.total" alt="Integration usage"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/releases"><img src="https://img.shields.io/github/v/release/italo-lombardi/Home-Assistant-FuelCompare" alt="Latest Release"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/italo-lombardi/Home-Assistant-FuelCompare/validate.yml?label=validate" alt="Validate"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/blob/main/LICENSE"><img src="https://img.shields.io/github/license/italo-lombardi/Home-Assistant-FuelCompare?logo=gnu&logoColor=white" alt="License"></a>
<img src="https://img.shields.io/badge/coverage-100%25-brightgreen" alt="Test Coverage">

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=italo-lombardi&repository=Home-Assistant-FuelCompare&category=integration)
[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=fuelcompare_ie)

> ⚠️ **Alpha software — testers welcome.** This integration is still in an alpha stage. While it ships providers for many countries, only a handful of them have been exercised on real Home Assistant installs. Other providers compile and pass unit tests but have not been validated end-to-end against live data. Please feel free to install, try it out, and [open a GitHub issue](https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues) for any bug, missing data point, or improvement idea — feedback is what drives this project forward.

> **Disclaimer:** This is an independent, unofficial custom integration for Home Assistant. It is not affiliated with, endorsed by, or in any way connected to any of the data providers it accesses (including FuelCompare.ie, FuelFinder.ie, pumps.ie, and all other sources listed below). All provider names, websites, and trademarks are the property of their respective owners. This project reads publicly available data from those websites for personal, non-commercial use only.

---

> **Status notice:** fuelcompare.ie announced closure at end of June 2025. The integration continues to work as long as the site remains online. **0.7.0 (currently pre-release `0.7.0-beta.1`)** introduces the provider abstraction layer and bundles 36 providers across 27 countries. See [Supported Data Sources](#supported-data-sources) below.

---

## Supported Data Sources

36 providers across 27 countries. Select a country in the config flow to see available providers.

| Provider | Country | Key Fuel Types | Lookup |
|----------|---------|----------------|--------|
| Albania National Average (cargopedia.net) | Albania | Unleaded, Diesel, LPG | Location + picker |
| e-control (Austria) | Austria | Unleaded, Diesel, CNG | Location + picker |
| Fuel Prices QLD (Australia) | Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker |
| FuelCheck NSW (Australia) | Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker |
| FuelWatch (Australia WA) | Australia | Unleaded, Diesel, E10, LPG | Location + picker |
| Servo Saver VIC (Australia) | Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker |
| cijenegoriva.ba (Bosnia and Herzegovina) | Bosnia & Herzegovina | Diesel, LPG | Location + picker |
| Carbu.com (Belgium) | Belgium | Unleaded, Diesel, LPG, CNG | Location + picker |
| Régie de l'énergie (Canada — QC) | Canada | Unleaded, Diesel, Premium | Location + picker |
| TCS Benzinpreis-Radar (Switzerland) | Switzerland | Unleaded, Diesel, Premium | Location + picker |
| MF ČR Price Caps (Czech Republic) | Czech Republic | Unleaded, Diesel | Location + picker |
| Tankerkoenig (Germany) | Germany | Unleaded, Diesel, E10 | Location + picker (API key required) |
| FuelFinder (Denmark) | Denmark | Unleaded, Diesel, Premium | Location + picker |
| EC Weekly Oil Bulletin (EU) | European Union | Unleaded, Diesel, LPG, Kerosene | Location + picker |
| MINETUR (Spain) | Spain | Unleaded, Diesel, Premium, LPG | Location + picker |
| Statistics Finland — National Average | Finland | Unleaded, Diesel, E10, Kerosene | Location + picker |
| Prix Carburants (France) | France | Unleaded, Diesel, E10, E85, LPG | Location + picker |
| Fuel Finder (UK) | United Kingdom | Unleaded, Diesel, Premium | Location + picker |
| Greek Ministry of Energy | Greece | Unleaded, Diesel, Premium, LPG | Location + picker |
| MINGOR (Croatia) | Croatia | Unleaded, Diesel, LPG | County picker → station |
| FuelFinder.ie | Ireland | Diesel, Petrol, Kerosene, CNG | County picker → station |
| fuelcompare.ie | Ireland | Unleaded, Diesel | Numeric station ID |
| pumps.ie | Ireland | Unleaded, Diesel, Petrol | Location + picker |
| Gasvaktin (Iceland) | Iceland | Unleaded, Diesel, Premium | Location + picker |
| MIMIT/MASE (Italy) | Italy | Unleaded, Diesel, LPG, CNG | Location + picker |
| Saurida (Lithuania) | Lithuania | Unleaded, Diesel, Premium, LPG | Location + picker |
| carbu.com Luxembourg | Luxembourg | Unleaded, Diesel, LPG, CNG | Location + picker |
| ANRE (Moldova) | Moldova | Unleaded, Diesel | Location + picker |
| Min. of Energy (Montenegro) | Montenegro | Unleaded, Diesel, Kerosene | Location + picker |
| Malta | Malta | Unleaded, Diesel, LPG, Kerosene | Location + picker |
| Netherlands (ANWB) | Netherlands | Diesel, E10, LPG, Kerosene | Location + picker |
| Drivstoffpriser (Norway) | Norway | Unleaded, Diesel, Premium | Location + picker |
| ORLEN Wholesale (Poland) | Poland | Unleaded, Diesel, E85, LPG, Kerosene | Location + picker |
| DGEG (Portugal) | Portugal | Unleaded, Diesel, LPG | Location + picker |
| Bensinpriser.nu (Sweden) | Sweden | Unleaded, Diesel, E85 | Location + picker |
| goriva.si (Slovenia) | Slovenia | Unleaded, Diesel, Premium, LPG | Location + picker |

### Adding a station

1. Go to **Settings → Devices & Services → Add Integration** and search for **Fuel Compare**.
2. Select your country.
3. Select the data source (if multiple providers are available for that country).
4. Follow the provider-specific steps — location-based providers show a station picker sorted cheapest-first; Ireland county-search providers show a county dropdown then a station picker.

Each config entry is independent — you can track stations from multiple countries simultaneously.

---

## What this is

A [Home Assistant](https://www.home-assistant.io/) custom integration that tracks live fuel prices and station information for Irish petrol stations. Each station you add creates a full set of sensors covering prices, opening hours, facilities, and real-time open/closed status.

## How it works

FuelCompare.ie is built with [Next.js](https://nextjs.org/). The integration uses two data paths with automatic fallback:

**Primary path — Next.js static JSON**

Next.js embeds a `buildId` in every HTML page and serves page data as structured JSON at a predictable path:

```
https://fuelcompare.ie/_next/data/{buildId}/station/{stationId}.json
```

The integration extracts the `buildId` from the station HTML page and fetches that JSON directly. If the `buildId` becomes stale (site redeploys), it is refreshed automatically before retrying.

**Fallback path — encrypted POST API**

FuelCompare.ie migrated some stations (including stations not available via SSR) to a client-side API endpoint (`/fuelcompareback/stationbyid`). Responses from this endpoint are AES-encrypted using a key embedded in their JS bundle. The integration:

1. Extracts the decrypt key from the station JS chunk (same HTML fetch as the `buildId`).
2. Caches the key and reuses it across updates.
3. If decryption fails (site redeployed with a new key), automatically re-extracts the key and retries.

Both paths feed the same sensor pipeline — all entities are identical regardless of which path succeeds.

**Update cycle**

The integration repeats data fetch every **30 minutes** via Home Assistant's `DataUpdateCoordinator`. No unofficial API keys required.

## Entities created

![Sensors screenshot](assets/sensors.png)

### fuelcompare.ie provider

Each station creates **14 entities** grouped under a single device.

#### Fuel price sensors

| Entity | Unit | Attributes |
|--------|------|------------|
| `sensor.<name>_unleaded` | € | `station_id`, `fuel_type`, `source`, `price_last_updated` |
| `sensor.<name>_diesel` | € | `station_id`, `fuel_type`, `source`, `price_last_updated` |

#### Station info sensors

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.<name>_price_last_updated` | Timestamp (UTC) of last price update on fuelcompare.ie | `station_id` |
| `sensor.<name>_station_name` | Full station name e.g. `Circle K Mulhuddart` | `station_id` |
| `sensor.<name>_brand` | Chain name e.g. `Circle K` | `station_id` |
| `sensor.<name>_county` | County e.g. `Co. Dublin` | `station_id` |
| `sensor.<name>_working_hours` | Today's hours e.g. `6a.m.-10p.m.` | `station_id`, full weekly schedule |
| `sensor.<name>_accessibility` | Active features e.g. `Wheelchair-accessible entrance` | `station_id`, full category dict |
| `sensor.<name>_offerings` | Active offerings e.g. `Car wash, Diesel fuel` | `station_id`, full category dict |
| `sensor.<name>_amenities` | Active amenities e.g. `Toilets` | `station_id`, full category dict |
| `sensor.<name>_payments` | Accepted payments e.g. `Debit cards, NFC mobile payments` | `station_id`, full category dict |

> Facility sensors (`accessibility`, `offerings`, `amenities`, `payments`) are marked unavailable if a station does not provide data for that category.

#### Diagnostic sensor

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.<name>_last_successful_fetch` | Timestamp (UTC) of the last successful fetch by *this integration* | `station_id` |

#### Binary sensors

| Entity | State | Attributes |
|--------|-------|------------|
| `binary_sensor.<name>_is_open` | `on` = open, `off` = closed | `station_id`, `today_hours` |
| `binary_sensor.<name>_data_fetch_problem` | `on` = problem (last fetch failed), `off` = healthy | `station_id`, `last_exception`, `last_successful_fetch` |

---

### FuelFinder.ie provider

Each station creates **15 entities** grouped under a single device. The four facility sensors (`accessibility`, `offerings`, `amenities`, `payments`) are absent — FuelFinder has no facility data. Two new fuel types (kerosene, CNG) and three new entity types (confidence, location, has-price) are added instead.

#### Fuel price sensors

| Entity | Unit | Attributes |
|--------|------|------------|
| `sensor.<name>_diesel` | € | `station_id`, `osm_id`, `fuel_type`, `confidence`, `price_last_updated`, `source` |
| `sensor.<name>_petrol` | € | `station_id`, `osm_id`, `fuel_type`, `confidence`, `price_last_updated`, `source` |
| `sensor.<name>_kerosene` | € | `station_id`, `osm_id`, `fuel_type`, `confidence`, `price_last_updated`, `source` |
| `sensor.<name>_cng` | € | `station_id`, `osm_id`, `fuel_type`, `confidence`, `price_last_updated`, `source` |

> Kerosene and CNG sensors are marked unavailable if no community submission exists for that fuel at the station.

#### Station info sensors

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.<name>_price_last_updated` | Timestamp (UTC) of the most recent community price submission | `station_id` |
| `sensor.<name>_price_confidence` | `fresh` / `likely` / `outdated` — FuelFinder freshness tier | `station_id`, `has_price`, per-fuel confidence |
| `sensor.<name>_station_name` | Full station name e.g. `Circle K Mulhuddart` | `station_id`, `slug`, `osm_id` |
| `sensor.<name>_brand` | Chain name e.g. `Circle K` | `station_id`, `logo_url` |
| `sensor.<name>_county` | County e.g. `Dublin` | `station_id`, `street`, `lat`, `lng` |
| `sensor.<name>_opening_hours` | OSM opening hours string e.g. `Mo-Su 07:00-23:00` | `station_id`, `phone`, `website` |
| `sensor.<name>_location` | `"{lat},{lng}"` e.g. `53.345,-6.278` | `station_id`, `latitude`, `longitude`, `osm_id` |

#### Diagnostic sensor

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.<name>_last_successful_fetch` | Timestamp (UTC) of the last successful fetch by *this integration* | `station_id` |

#### Binary sensors

| Entity | State | Attributes |
|--------|-------|------------|
| `binary_sensor.<name>_is_open` | `on` = open, `off` = closed | `station_id`, `opening_hours`, `today_rule` |
| `binary_sensor.<name>_data_fetch_problem` | `on` = problem (last fetch failed), `off` = healthy | `station_id`, `last_exception`, `last_successful_fetch` |
| `binary_sensor.<name>_has_price` | `on` = at least one community price exists, `off` = no submissions yet | `station_id`, per-fuel prices |

---

The `last_successful_fetch` sensor is distinct from `price_last_updated`: the former reflects the **integration's own poll cadence**, the latter reflects the timestamp the **source site** records for the price record. Use `last_successful_fetch` to detect "the integration hasn't fetched anything in N hours" independently of whether the site has refreshed its prices.

The `data_fetch_problem` sensor (device class `problem`, diagnostic category) is always available and gives automations a single deterministic on/off signal for "is the integration currently failing to reach the data source?". Pairs with the stale-retention behaviour described below.

## Behaviour during fetch failures

When the site is offline, throttling, or returning errors, the integration **keeps the last known values** for prices, station info, and is-open state instead of flipping every entity to `unavailable`. This lets dashboards keep showing the most recent prices through transient outages.

To detect that a fetch has actually failed, automations should monitor:

- `binary_sensor.<name>_data_fetch_problem` — flips to `on` immediately when a poll fails. Attributes carry the last exception and the timestamp of the last successful fetch.
- `sensor.<name>_last_successful_fetch` — automations can compare this against `now()` to alert when the gap exceeds a threshold (e.g. no successful fetch in 6 hours).

> **Breaking change in 0.6.0:** Earlier versions marked all entities as `unavailable` whenever a fetch failed. From 0.6.0 onward, entities retain the last good value on failure. Automations that previously detected outages via `state == 'unavailable'` should migrate to the `data_fetch_problem` binary sensor.

Example automation skeleton:

```yaml
- alias: "FuelCompare integration unhealthy"
  trigger:
    - platform: state
      entity_id: binary_sensor.my_station_data_fetch_problem
      to: "on"
      for: "01:00:00"
  action:
    - service: notify.mobile_app
      data:
        message: >
          FuelCompare hasn't fetched successfully for over an hour.
          Last success: {{ state_attr('binary_sensor.my_station_data_fetch_problem',
          'last_successful_fetch') }}.
```

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations** → three-dot menu → **Custom repositories**.
3. Add `https://github.com/italo-lombardi/Home-Assistant-FuelCompare` with category **Integration**.
4. Search for **Fuel Compare** and install it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/fuelcompare_ie` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Fuel Compare**.
3. Select **Ireland** as the country (auto-skipped if only one country is available).
4. Select a **data source**: **fuelcompare.ie** or **FuelFinder.ie** (auto-skipped if only one provider is available).

### fuelcompare.ie setup

5. Enter the **Station ID** — the number at the end of the station URL on fuelcompare.ie. Leading zeros are stripped automatically (`007` → `7`).

   ![Config flow step 1](assets/config_flow_step_1.png)

   If you enter an invalid ID, an error is shown inline:

   ![Config flow step 1 error](assets/config_flow_step_1_error.png)

6. The integration will automatically fetch the station's name and pre-populate the name field. Confirm or enter a custom name.

   ![Config flow step 2](assets/config_flow_step_2.png)

#### Finding a station ID

1. Go to [fuelcompare.ie](https://fuelcompare.ie) and search for your station.
2. Click the station — the URL will look like `https://fuelcompare.ie/station/790`.
3. The number at the end (`790` in this example) is the Station ID.

### FuelFinder.ie setup

5. Select the **county** where the station is located (e.g. Dublin, Cork, Kerry).
6. Select the **station** from the list of stations in that county. The integration resolves the station UUID automatically.

You can add as many stations as you like from either provider; each gets its own device entry.

## Requirements

- Home Assistant 2024.1.0 or newer
- Internet access from the Home Assistant host

## Supported languages

Bulgarian, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Irish, Italian, Latvian, Lithuanian, Norwegian, Polish, Portuguese, Romanian, Slovak, Slovenian, Spanish, Swedish, Ukrainian.

## Disclaimer (repeated for clarity)

This project is a personal, community tool. It is **not** the official FuelCompare.ie app or service. The author has no relationship with FuelCompare.ie. If FuelCompare.ie changes their website structure this integration may stop working; please open an issue and it will be looked at when time allows.

## Sibling integrations

Other Home Assistant custom integrations by the same author:

| Integration | What it does |
|-------------|-------------|
| [Entity Guard](https://github.com/italo-lombardi/Home-Assistant-EntityGuard) | Enforce desired entity states via declarative rules — replaces N hand-written auto-off / auto-lock automations with built-in cooldowns, rate limiting, and a custom dashboard card. |
| [Entity Availability](https://github.com/italo-lombardi/Home-Assistant-EntityAvailability) | Monitor entity availability across groups — tracks offline entities, uptime percentages, battery health, and degraded states with a custom dashboard card. |
| [Entity Distance](https://github.com/italo-lombardi/Home-Assistant-EntityDistance) | Track distance between people, devices, and zones — direction of travel, closing speed, ETA, proximity detection, and today's time together, all from a single config entry. |
| [WashWise](https://github.com/italo-lombardi/Home-Assistant-WashWise) | Decide whether to wash your car, bike, or solar panels — or skip garden irrigation — based on the weather forecast. Produces a verdict, 0–100 score, blocking reason, and per-day breakdown with a custom Lovelace card. |

## License

GPL-3.0 — see [LICENSE](LICENSE).

## Issues & contributions

Bug reports and pull requests are welcome at [italo-lombardi/Home-Assistant-FuelCompare](https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues).
