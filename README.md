# Fuel Compare — Home Assistant Custom Integration

<a href="https://analytics.home-assistant.io"><img src="https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=%24.fuelcompare_ie.total" alt="Integration usage"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/releases"><img src="https://img.shields.io/github/v/release/italo-lombardi/Home-Assistant-FuelCompare" alt="Latest Release"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/italo-lombardi/Home-Assistant-FuelCompare/validate.yml?label=validate" alt="Validate"></a>
<a href="https://github.com/italo-lombardi/Home-Assistant-FuelCompare/blob/main/LICENSE"><img src="https://img.shields.io/github/license/italo-lombardi/Home-Assistant-FuelCompare?logo=gnu&logoColor=white" alt="License"></a>
<img src="https://img.shields.io/badge/coverage-100%25-brightgreen" alt="Test Coverage">

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=italo-lombardi&repository=Home-Assistant-FuelCompare&category=integration)
[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=fuelcompare_ie)

> ⚠️ **Early multi-country release — testers welcome.** 4 providers are verified end-to-end on production Home Assistant installs (✅ Tested — Ireland ×3 + EU Oil Bulletin), 16 are smoke-tested against live upstreams from a dev install (🤖 Smoke-tested), and 16 are currently disabled because their upstream is broken (12) or requires an API key the project lacks (4) (⚠️ Disabled). See the [status legend](#supported-data-sources) below. Please install, try it out, and [open a GitHub issue](https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues) for any bug, missing data point, or improvement idea.

> **Disclaimer:** This is an independent, unofficial custom integration. It is not affiliated with, endorsed by, or connected to any of the data providers it accesses. All provider names, websites, and trademarks are the property of their respective owners. This project reads publicly available data for personal, non-commercial use only.

---

## Security

### `ie_pumps` (pumps.ie) — TLS opt-in

The `pumps.ie` upstream has shipped an **invalid TLS certificate** for an
extended period. Releases up to and including `0.7.0` silently disabled
certificate verification so the provider would keep working. Starting with
`1.0.0`, **TLS verification is enforced by default** — `ie_pumps` will fail
to fetch while the upstream cert is invalid.

Why this matters: with verification disabled, any device on the network
path between Home Assistant and pumps.ie can read or replace the response.
The integration parses that response and feeds it into long-term Home
Assistant statistics, so a tampered response permanently corrupts your
price history.

If you accept the risk, you can re-enable the bypass per-entry:

1. Settings → Devices & services → Fuel Compare → **Configure** on the
   `ie_pumps` entry.
2. Tick **Disable TLS verification for pumps.ie**.
3. Tick **I understand this risk** in the same dialog.
4. Submit.

While the opt-in is active, a persistent **error-severity** repair issue
sits in Settings → System → Repairs. Use its fix flow to turn the bypass
off and restore secure verification.

If pumps.ie ever fixes its certificate, file an issue here so we can
update the README and consider re-enabling the provider for new entries
without the bypass.

> Audit reference: a 2026-06 third-party persona-driven audit raised this
> as finding **FC-1 (HIGH)** — the previous silent bypass without explicit
> user consent was treated as a security defect, not a quality-of-life
> choice.

If you find a security defect that has not been disclosed, please follow
the process in [`SECURITY.md`](SECURITY.md).

---

## What this is

A [Home Assistant](https://www.home-assistant.io/) custom integration that tracks live fuel prices and station information from 36 providers across 30 countries (plus an EU-wide regional source). Each station you configure creates a set of sensors covering prices, opening hours, location, and real-time open/closed status.

Data is refreshed every **30 minutes** via Home Assistant's `DataUpdateCoordinator`. Most providers require no API key.

---

## Supported Data Sources

36 providers across 30 countries (plus EU). Select a country in the config flow to see available providers.

| Provider | Country | Key Fuel Types | Lookup | Requires API Key | Status |
|----------|---------|----------------|--------|------------------|--------|
| Albania National Average (cargopedia.net) | 🇦🇱 Albania | Unleaded, Diesel, LPG | Location + picker | — | ⚠️ Disabled |
| e-control (Austria) | 🇦🇹 Austria | Unleaded, Diesel, CNG | Location + picker | — | 🤖 Smoke-tested |
| Fuel Prices QLD (Australia) | 🇦🇺 Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker | 🔑 | ⚠️ Disabled (untested, API key required) |
| FuelCheck NSW (Australia) | 🇦🇺 Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker | — | 🤖 Smoke-tested |
| FuelWatch (Australia WA) | 🇦🇺 Australia | Unleaded, Diesel, E10, LPG | Location + picker | — | 🤖 Smoke-tested |
| Servo Saver VIC (Australia) | 🇦🇺 Australia | Unleaded, Diesel, E10, E85, LPG | Location + picker | 🔑 | ⚠️ Disabled (untested, API key required) |
| cijenegoriva.ba (Bosnia and Herzegovina) | 🇧🇦 Bosnia & Herzegovina | Diesel, LPG | Location + picker | — | ⚠️ Disabled |
| Carbu.com (Belgium) | 🇧🇪 Belgium | Unleaded, Diesel, LPG, CNG | Location + picker | — | 🤖 Smoke-tested |
| Régie de l'énergie (Canada — QC) | 🇨🇦 Canada | Unleaded, Diesel, Premium | Location + picker | — | 🤖 Smoke-tested |
| TCS Benzinpreis-Radar (Switzerland) | 🇨🇭 Switzerland | Unleaded, Diesel, Premium | Location + picker | — | 🤖 Smoke-tested |
| MF ČR Price Caps (Czech Republic) | 🇨🇿 Czech Republic | Unleaded, Diesel | Location + picker | — | ⚠️ Disabled |
| Tankerkoenig (Germany) | 🇩🇪 Germany | Unleaded, Diesel, E10 | Location + picker (API key required) | 🔑 | ⚠️ Disabled (untested, API key required) |
| FuelFinder (Denmark) | 🇩🇰 Denmark | Unleaded, Diesel, Premium | Location + picker | — | ⚠️ Disabled |
| EC Weekly Oil Bulletin (EU) | 🇪🇺 European Union | Unleaded, Diesel, LPG, Kerosene | Country picker | — | ✅ Tested |
| MINETUR (Spain) | 🇪🇸 Spain | Unleaded, Diesel, Premium, LPG | Location + picker | — | ⚠️ Disabled |
| Statistics Finland — National Average | 🇫🇮 Finland | Unleaded, Diesel, E10, Kerosene | Location + picker | — | ⚠️ Disabled |
| Prix Carburants (France) | 🇫🇷 France | Unleaded, Diesel, E10, E85, LPG | Location + picker | — | 🤖 Smoke-tested |
| Fuel Finder (UK) | 🇬🇧 United Kingdom | Unleaded, Diesel, Premium | Location + picker | — | 🤖 Smoke-tested |
| Greek Ministry of Energy | 🇬🇷 Greece | Unleaded, Diesel, Premium, LPG | Location + picker | — | 🤖 Smoke-tested |
| MINGOR (Croatia) | 🇭🇷 Croatia | Unleaded, Diesel, LPG | County picker → station | — | 🤖 Smoke-tested |
| FuelFinder.ie | 🇮🇪 Ireland | Diesel, Petrol, Kerosene, CNG | County picker → station | — | ✅ Tested |
| fuelcompare.ie | 🇮🇪 Ireland | Unleaded, Diesel | Numeric station ID | — | ✅ Tested |
| pumps.ie | 🇮🇪 Ireland | Unleaded, Diesel, Petrol | Location + picker | — | ✅ Tested |
| Gasvaktin (Iceland) | 🇮🇸 Iceland | Unleaded, Diesel, Premium | Location + picker | — | 🤖 Smoke-tested |
| MIMIT/MASE (Italy) | 🇮🇹 Italy | Unleaded, Diesel, LPG, CNG | Location + picker | — | 🤖 Smoke-tested |
| Saurida (Lithuania) | 🇱🇹 Lithuania | Unleaded, Diesel, Premium, LPG | Location + picker | — | 🤖 Smoke-tested |
| carbu.com Luxembourg | 🇱🇺 Luxembourg | Unleaded, Diesel, LPG, CNG | Location + picker | — | ⚠️ Disabled |
| ANRE (Moldova) | 🇲🇩 Moldova | Unleaded, Diesel | Location + picker | — | ⚠️ Disabled |
| Min. of Energy (Montenegro) | 🇲🇪 Montenegro | Unleaded, Diesel, Kerosene | Location + picker | — | 🤖 Smoke-tested |
| Malta | 🇲🇹 Malta | Unleaded, Diesel, LPG, Kerosene | Location + picker | — | ⚠️ Disabled |
| Netherlands (ANWB) | 🇳🇱 Netherlands | Diesel, E10, LPG, Kerosene | Location + picker | — | ⚠️ Disabled |
| Drivstoffpriser (Norway) | 🇳🇴 Norway | Unleaded, Diesel, Premium | Location + picker | 🔑 | ⚠️ Disabled (untested, API key required) |
| ORLEN Wholesale (Poland) | 🇵🇱 Poland | Unleaded, Diesel, E85, LPG, Kerosene | Location + picker | — | ⚠️ Disabled |
| DGEG (Portugal) | 🇵🇹 Portugal | Unleaded, Diesel, LPG | Location + picker | — | ⚠️ Disabled |
| Bensinpriser.nu (Sweden) | 🇸🇪 Sweden | Unleaded, Diesel, E85 | Location + picker | — | 🤖 Smoke-tested |
| goriva.si (Slovenia) | 🇸🇮 Slovenia | Unleaded, Diesel, Premium, LPG | Location + picker | — | 🤖 Smoke-tested |

**Status legend:**
- ✅ **Tested** — verified on a production Home Assistant install with live upstream data over multiple polls.
- 🤖 **Smoke-tested** — deployed in a dev Home Assistant; `async_list_stations` and `async_fetch` returned populated sensors (`fetch_problem` = off, fuel prices live). Not yet long-running tested.
- ⚠️ **Disabled** — provider is currently failing against its upstream (empty list, HTTP 4xx/5xx, or stale cache without refresh) **or** requires an API key the project does not have. Hidden from the config flow so new entries cannot be created. Existing entries continue loading from cache. Will be re-enabled once the upstream contract is fixed or a tester confirms the API-key flow works.

**Requires API Key:** 🔑 = key required (free registration usually). — = no key, public endpoint.

> **Disabled provider for your country?** If you have an API key, want to volunteer testing time, or already verified one of these against your real upstream — please [open a GitHub issue](https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues/new) and I will re-enable it. Most disabled rows are one polling round of confirmation away from going green.

---

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

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration** and search for **Fuel Compare**.
2. Select your **country**.
3. Select the **data source** (if multiple providers exist for your country).
4. Follow the provider steps:
   - **Location-based providers** — enter coordinates and radius; a station picker shows nearby stations sorted cheapest-first.
   - **County/region providers** — select a region, then pick a station from the list.
   - **Station ID providers** — enter the station ID from the provider's website URL.
5. Confirm or edit the suggested station name.

Each config entry is independent — you can track stations from multiple countries simultaneously.

---

## Entities created

The exact set of entities depends on what data the provider exposes. All providers create at least the sensors below; richer providers add extras (location, opening hours, price confidence, facilities, etc.).

![Sensors screenshot](assets/sensors.png)

### Core entities (all providers)

| Entity | Description |
|--------|-------------|
| `sensor.<name>_<fuel_type>` | Fuel price in the provider's currency (e.g. `_diesel`, `_unleaded`) |
| `sensor.<name>_station_name` | Full station name |
| `sensor.<name>_brand` | Chain / brand name |
| `sensor.<name>_price_last_updated` | Timestamp of the last price update on the source site |
| `sensor.<name>_last_successful_fetch` | Timestamp of the last successful poll by this integration |
| `binary_sensor.<name>_is_open` | `on` = open now, `off` = closed |
| `binary_sensor.<name>_data_fetch_problem` | `on` = last poll failed, `off` = healthy |

### Optional entities (provider-dependent)

| Entity | Description |
|--------|-------------|
| `sensor.<name>_county` | County or region |
| `sensor.<name>_working_hours` | Today's opening hours |
| `sensor.<name>_opening_hours` | Full OSM opening hours string |
| `sensor.<name>_location` | `"{lat},{lng}"` coordinates |
| `sensor.<name>_price_confidence` | Freshness tier (`fresh` / `likely` / `outdated`) |
| `binary_sensor.<name>_has_price` | `on` = at least one community price exists |
| `sensor.<name>_accessibility` | Accessibility facilities |
| `sensor.<name>_offerings` | Station offerings (car wash, etc.) |
| `sensor.<name>_amenities` | Amenities (toilets, etc.) |
| `sensor.<name>_payments` | Accepted payment methods |

`last_successful_fetch` reflects **this integration's poll cadence**. `price_last_updated` reflects when the **source site** last recorded a price change. Use the former to detect integration outages; use the latter to see how fresh the source data is.

---

## Behaviour during fetch failures

When a provider is unreachable, the integration **keeps the last known values** instead of flipping entities to `unavailable`. To detect failures, monitor:

- `binary_sensor.<name>_data_fetch_problem` — flips to `on` immediately when a poll fails.
- `sensor.<name>_last_successful_fetch` — compare against `now()` to alert after a threshold (e.g. 6 hours).

Example automation:

```yaml
- alias: "Fuel Compare integration unhealthy"
  trigger:
    - platform: state
      entity_id: binary_sensor.my_station_data_fetch_problem
      to: "on"
      for: "01:00:00"
  action:
    - service: notify.mobile_app
      data:
        message: >
          Fuel Compare hasn't fetched successfully for over an hour.
          Last success: {{ state_attr('binary_sensor.my_station_data_fetch_problem',
          'last_successful_fetch') }}.
```

---

## Requirements

- Home Assistant 2024.1.0 or newer
- Internet access from the Home Assistant host

## Supported languages

35 translations: Albanian, Basque, Bosnian, Bulgarian, Catalan, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish, French, Galician, German, Greek, Hungarian, Icelandic, Irish, Italian, Latvian, Lithuanian, Luxembourgish, Norwegian Bokmål, Norwegian Nynorsk, Polish, Portuguese, Romanian, Serbian, Slovak, Slovenian, Spanish, Swedish, Ukrainian, Welsh.

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
