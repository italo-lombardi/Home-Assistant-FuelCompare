# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-06-14

### Added
- **FuelFinder.ie provider** (`providers/ie_fuelfinder.py`, `IEFuelFinderProvider`) — second Ireland data source backed by the crowd-sourced [FuelFinder.ie](https://www.fuelfinder.ie/) platform (Conjora Limited). Covers diesel, petrol, kerosene, and CNG prices for ~1,000+ Irish stations sourced from the OpenStreetMap dataset with community price submissions. Requires no API key — authentication is a static set of browser-origin headers (`Sec-Fetch-Site: same-origin`, `Referer`, and a non-blocked User-Agent; see `IEFuelFinderProvider._HEADERS`).
- **New fuel types** — `kerosene` and `cng` price sensors are new entity types that have no fuelcompare.ie equivalent. They are created per station but shown as `unavailable` if the station has no community submissions for that fuel.
- **`FuelFinderConfidenceSensor`** (`sensor.<name>_price_confidence`) — string sensor exposing FuelFinder's freshness tier for the station's crowd-sourced price: `fresh` (sub-24 h), `likely` (recent), or `outdated` (stale). Gives automations a data-age signal without requiring timestamp arithmetic.
- **`FuelFinderLocationSensor`** (`sensor.<name>_location`) — exposes the station's WGS84 coordinates as `"{lat},{lng}"` (same convention as HA zone entities), with `latitude` and `longitude` as attributes. Useful for distance automations and map cards.
- **`FuelFinderHasPriceBinarySensor`** (`binary_sensor.<name>_has_price`) — `on` when at least one community price submission exists for the station; `off` for OSM placeholder stations with no submitted prices. Device class `connectivity`.
- **`FuelFinderIsOpenBinarySensor`** (`binary_sensor.<name>_is_open`) — open/closed state parsed from OSM `opening_hours` format strings (e.g. `"Mo-Su 07:00-23:00"`, `"24/7"`). Backed by a new `_parse_osm_opening_hours()` function; the existing `_parse_time()` logic (fuelcompare.ie `"6a.m."` format) is not reused.
- **`FuelFinderOpeningHoursSensor`** (`sensor.<name>_opening_hours`) — exposes the raw OSM `opening_hours` string as the sensor state; attributes carry `phone` and `website` from the station record. Replaces `StationWorkingHoursSensor` for this provider (format is incompatible).
- Each FuelFinder station creates **15 entities** (12 sensors + 3 binary sensors) vs 14 for fuelcompare.ie. The four fuelcompare.ie `about` category sensors (Accessibility, Offerings, Amenities, Payments) are absent — FuelFinder has no facility data.
- Config flow: selecting the FuelFinder.ie provider routes to a county-picker step followed by a station-picker step (station resolved by UUID from `/api/fuelfinder/stations`). The station UUID is stored as `CONF_STATION_ID`; no leading-zero stripping is applied.
- Poll interval remains **30 minutes** (`DEFAULT_SCAN_INTERVAL = 1800`). The FuelFinder `/api/fuelfinder/init` endpoint is CDN-cached at `s-maxage=300` (5 min) and `/api/fuelfinder/stations` is `no-store`; polling more frequently than 5 minutes provides no benefit for national stats.

### Changed
- `sensor.py`: `FuelPriceSensor` now uses `SensorStateClass.MEASUREMENT` (was `TOTAL`) for the FuelFinder provider. `MEASUREMENT` is the correct HA state class for a point-in-time price reading. The fuelcompare.ie provider is unchanged to avoid entity history migration for existing users.
- Roadmap section in README updated — FuelFinder.ie marked as integrated; fuelcompare.ie closure notice adjusted.

## [0.7.0] - 2026-06-14

### Context
fuelcompare.ie announced closure at end of June 2025. This release restructures the integration to decouple data-fetching from the coordinator so alternative sources (FuelFinder.ie, future countries) can be added without breaking existing installs. The fuelcompare.ie scraper continues to work as before until a replacement source is integrated.

### Changed (BREAKING)
- **Integration display name** changed from `"FuelCompare.ie"` to `"Fuel Compare"`. The internal domain (`fuelcompare_ie`), entity IDs, device registry entries, and all existing config entries are unchanged — no migration required for existing users.
- `manufacturer` field in the HA device registry now comes from the active provider's `LABEL` attribute instead of being hardcoded to `"FuelCompare.ie"`.
- `source` extra state attribute on fuel price sensors now comes from the provider's `LABEL` instead of the hardcoded string `"fuelcompare.ie"`.

### Added
- **Provider abstraction layer** (`providers/`): all data-fetching logic moved to `providers/ie_fuelcompare.py` (`IEFuelCompareProvider`), implementing a `BaseProvider` ABC. Adding a new data source requires only a new provider file and one registry entry — no changes to sensors, binary sensors, or the coordinator.
- `BaseProvider.CONFIG_MODE` — `"station_id"` (default, current behaviour) or `"location"` (lat/lng + radius, for government open-data APIs). The config flow routes to the appropriate step automatically.
- `providers/base.py`: `ProviderError` exception class — providers raise this; the coordinator catches it and converts to `UpdateFailed`.
- Config flow: country selector step + provider selector step. Both auto-skip when only one option is available — setup experience is identical to previous versions for existing Ireland users.
- Config flow: `async_step_location` — collects latitude, longitude, and search radius for location-based providers; defaults to the HA home location.
- `CONF_COUNTRY`, `CONF_PROVIDER`, `CONF_LATITUDE`, `CONF_LONGITUDE`, `CONF_RADIUS_KM` stored in config entry data. Existing entries without these keys default to `IE` / `ie_fuelcompare`.
- `requirements_test.txt`: updated `pytest-homeassistant-custom-component` to `>=0.13.338,<0.13.339`.
- 25 translation files: added `"station"`, `"provider"`, `"location"` step keys; corrected 24 non-EN station-step descriptions that were truncated after a previous edit removed the `fuelcompare.ie` URL.

### Fixed
- All 24 non-EN translation descriptions for the station-ID setup step were grammatically truncated (missing "from the station URL" equivalent). Now repaired per locale.
- `hu.json`: fixed double-article grammatical error (`"a az állomás"` → `"az állomás"`).

## [0.6.0] - 2026-06-08

### Added
- New diagnostic binary sensor `binary_sensor.<station>_data_fetch_problem` (`problem` device class) — `on` when the last coordinator update failed, `off` when it succeeded. Always reports as available so automations can rely on a deterministic on/off signal even before the first successful fetch (no fetch yet ⇒ problem ⇒ on). Attributes expose `last_exception` and `last_successful_fetch` for richer diagnostics.
- New diagnostic timestamp sensor `sensor.<station>_last_successful_fetch` — UTC timestamp of the last successful fetch by the integration itself, advanced only after a fetch parses successfully. Distinct from `price_last_updated`, which reflects the site's own price-record timestamp.
- Coordinator now tracks `last_successful_fetch`, stamped after each successful parse.

### Changed (BREAKING)
- Entities now retain the last known value when a fetch fails instead of flipping to `unavailable`. Price, station info, working-hours, facility, and is-open entities all stay populated through transient outages (site offline, throttling, network blips). Automations that previously relied on `state == 'unavailable'` to detect integration outages must migrate to `binary_sensor.<station>_data_fetch_problem` (or compare `now()` against `sensor.<station>_last_successful_fetch`). First-ever fetch failures still show `unavailable` because no last-known value exists.
- `available` properties on `FuelPriceSensor` and `StationAboutCategorySensor` no longer gate on `coordinator.last_update_success`. Station-level info sensors and `StationIsOpenBinarySensor` gain explicit `available` overrides that drop the same gate.
- Each station now creates **14 entities** (was 12): the two new diagnostic entities are added per station.

### Tests
- New tests cover stale retention across all entity types, the `data_fetch_problem` binary sensor's diagnostic attributes, and `last_successful_fetch` stamping on the coordinator. 100% line coverage maintained.

## [0.5.3] - 2026-06-03

### Fixed
- AES decrypt key extraction now uses a layered fallback: the legacy single-chunk lookup against `pages/station/[id]-*.js` runs first, and a broad scan across every chunk URL in the HTML is the inner fallback. fuelcompare.ie relocated the key into a shared vendor chunk in a recent deploy, which caused both the Next.js and encrypted-API fetch paths to fail with `Station data not found via any available method`.
- High-visibility `ERROR` log now fires when every fetch and key-extraction strategy is exhausted, pointing users to the issue tracker so site-side breakages can be reported quickly.

### Changed
- `coordinator.py` modularized: AES decryption moved to `crypto.py`, page-asset extraction (buildId + decrypt key, both single-chunk and broad modes) moved to `page_assets.py`. Public coordinator API and test imports are unchanged via property/alias re-exports.

### Tests
- 105 tests, 100% line coverage across all source files

## [0.5.2] - 2026-06-01

### Fixed
- PKCS7 padding validation in `_cryptojs_decrypt`: pad length outside 1–16 now raises `ValueError` instead of silently producing a bad slice or invalid JSON on corrupted payloads
- Config flow `_fetch_station_name` exception now logged at DEBUG (station ID + error message) instead of swallowed silently
- Renamed internal `passphrase` variable to `evp_key` to accurately reflect it is a CryptoJS EvpKDF key, not a user password

### Changed
- README: added Sibling integrations section linking Entity Guard, Entity Availability, and Entity Distance

### Tests
- 100 tests, 100% line coverage across all source files

## [0.5.2-beta.1] - 2026-05-31

### Fixed
- PKCS7 padding validation in `_cryptojs_decrypt`: pad length outside 1–16 now raises `ValueError` instead of silently producing a bad slice or invalid JSON
- Config flow `_fetch_station_name` exception now logged at DEBUG (station ID + error message) instead of swallowed silently

### Changed
- README: added Sibling integrations section linking Entity Guard, Entity Availability, and Entity Distance

### Tests
- 100 tests, 100% line coverage across all source files

## [0.5.1] - 2026-05-22

### Added
- Two-step config flow: station name auto-fetched from fuelcompare.ie and pre-populated; user can confirm or override
- New `station_name` sensor exposing the full station name (e.g. `Circle K Mulhuddart`), distinct from `brand` (chain only)
- `station_id` exposed in `extra_state_attributes` on all 13 sensors and the binary sensor

### Fixed
- `working_hours` and `is_open` now use `dt_util.now()` (HA configured timezone) instead of `datetime.now()` (system timezone)
- Silent failures in `working_hours`, `about`, and time-parsing now emit `DEBUG` log messages

### Changed
- Removed dead code: unused `DEFAULT_NAME` constant, unused `_LOGGER` in `__init__.py`
- CI upgraded to Python 3.13
- 99 tests, 100% line coverage across all source files

## [0.5.1-beta.4] - 2026-05-22

### Fixed
- CI pipeline now uses Python 3.13; test dependency pinned to `<0.13.317` to avoid versions requiring Python 3.14

## [0.5.1-beta.3] - 2026-05-22

### Added
- New `station_name` sensor exposing the full station name (e.g. `Circle K Mulhuddart`) from the API `name` field, distinct from the `brand` sensor which shows chain name only
- Config flow name auto-fetch now uses the `name` field first, falls back to formatted `tablename`
- Config flow screenshots added to README (step 1, step 1 error, step 2)

### Changed
- All 25 translations updated: `station_name` entity added, `name` config flow step added, legacy `name` field removed from `user` step
- Station count updated to 13 entities per station in README and info.md

### Tests
- 99 tests, 100% line coverage across all source files

## [0.5.1-beta.2] - 2026-05-22

### Added
- Two-step config flow: station name auto-fetched from fuelcompare.ie during setup and pre-populated; user can confirm or override
- `station_id` exposed in `extra_state_attributes` on all sensors and binary sensors

### Tests
- 5 new tests for two-step config flow and `_fetch_station_name` helper (94 tests total, 100% coverage)

## [0.5.1-beta.1] - 2026-05-22

### Fixed
- `working_hours` and `is_open` now use `dt_util.now()` (HA configured timezone) instead of `datetime.now()` (system timezone) — open/closed state and today's hours are now correct when HA timezone differs from the host system timezone
- Silent failures in `working_hours`, `about`, and time-parsing now emit `DEBUG` log messages, making it easier to diagnose missing sensor values with debug logging enabled

### Changed
- Removed unused `DEFAULT_NAME` constant from `const.py`
- Removed unused `_LOGGER` and `logging` import from `__init__.py` and `config_flow.py`
- Removed unreachable `_build_id is None` guard in `_fetch_nextjs` (dead code after `_fetch_page_assets` refactor)
- `pytest.ini`: set `asyncio_default_fixture_loop_scope = function` to silence pytest-asyncio deprecation warning

### Tests
- 37 new tests covering all previously uncovered branches — 100% line coverage across all source files (89 tests total)

## [0.5.0] - 2026-05-18

### Added
- Encrypted API fallback path: stations no longer served via Next.js SSR (e.g. Circle K Taney, id 790) now fetched via `POST /fuelcompareback/stationbyid`
- Dynamic AES decrypt key extraction from fuelcompare.ie JS bundle — no hardcoded key, automatically refreshed on key rotation
- Automatic stale-key recovery: decrypt failure triggers key re-extraction and one retry before giving up
- 7 new coordinator tests covering the encrypted API path, key extraction, key rotation recovery, and failure cases

### Fixed
- `UpdateFailed` re-raise was silent — all error branches now log at DEBUG before raising, enabling proper debug-mode diagnostics
- `_parse_station` extracted as shared method — both fetch paths use identical price parsing and sensor field population

## [0.4.0] - 2026-05-11

### Added
- Full test suite: 45 tests covering coordinator, sensors, binary sensor, and config flow
- `SECURITY.md` — vulnerability reporting policy via GitHub Private Security Advisory
- Dependabot configured for pip and GitHub Actions dependencies
- CI: ruff lint and pytest jobs added to validate workflow (4 separate jobs)
- `translations/en.json` — entity names served from HA translations system

### Changed
- All entity classes use `_attr_has_entity_name = True` + `_attr_translation_key` — names driven by translations instead of hardcoded strings
- `strings.json` and `translations/en.json` fully synced
- Station ID leading zeros stripped on entry (e.g. `007` → `7`) to prevent duplicate entries
- `DeviceInfo` definition consolidated — binary sensor reuses shared helper from sensor platform
- Coordinator iterates fuel types via `FUEL_TYPES` constant instead of hardcoded list

### Fixed
- `config_flow`: unique ID was set before station ID normalization — entering `007` and `7` could create duplicate entries for the same station; unique ID is now set after normalization
- `__init__`: `async_unload_entry` used `dict.pop(key)` which would raise `KeyError` if coordinator was never stored due to a failed setup; changed to `.pop(key, None)`
- `binary_sensor`: midnight-crossing detection used `close_time <= open_time` — equal times (e.g. 9 a.m.–9 a.m.) were incorrectly treated as midnight-crossing ranges; changed to `<`
- `sensor`: `FuelPriceSensor` used `state_class = MEASUREMENT` with `device_class = MONETARY` — invalid combination per HA validation; changed to `TOTAL`

## [0.4.0-beta.3] - 2026-05-11

### Fixed
- `sensor`: `FuelPriceSensor` used `state_class = MEASUREMENT` with `device_class = MONETARY` — invalid combination per HA validation; changed to `TOTAL`

## [0.4.0-beta.1] - 2026-05-11

### Added
- Full test suite: 45 tests covering coordinator, sensors, binary sensor, and config flow
- `SECURITY.md` — vulnerability reporting policy via GitHub Private Security Advisory
- Dependabot configured for pip and GitHub Actions dependencies
- CI: ruff lint and pytest jobs added to validate workflow (4 separate jobs)
- `translations/en.json` — entity names served from HA translations system

### Changed
- All entity classes use `_attr_has_entity_name = True` + `_attr_translation_key` — names driven by translations instead of hardcoded strings
- `strings.json` and `translations/en.json` fully synced
- Station ID leading zeros stripped on entry (e.g. `007` → `7`) to prevent duplicate entries
- `DeviceInfo` definition consolidated — binary sensor reuses shared helper from sensor platform
- Coordinator iterates fuel types via `FUEL_TYPES` constant instead of hardcoded list

## [0.3.0] - 2026-04-24

### Added
- Translations for 24 languages: bg, cs, da, de, el, es, et, fi, fr, ga, hr, hu, it, lt, lv, nb, nl, pl, pt, ro, sk, sl, sv, uk

## [0.2.0] - 2026-04-24

### Added
- Dedicated Price Last Updated timestamp sensor (`SensorDeviceClass.TIMESTAMP`)
- Robust ISO 8601 date parsing with edge case handling

### Changed
- Use Home Assistant's shared HTTP session (`async_get_clientsession`) instead of creating sessions per update
- Fixed license reference in README (GPL-3.0, not MIT)

## [0.1.0] - 2026-04-15

### Added
- Station brand/chain sensor
- Station county/location sensor
- Station working hours sensor (state = today's hours, attributes = full weekly schedule)
- Station is open binary sensor (parses working hours against current time)
- Station facility sensors: Accessibility, Offerings, Amenities, Payments
- `price_last_updated` attribute on fuel price sensors
- Fixed floating point precision on sensor values

## [0.0.1] - 2026-04-15

### Added
- Initial release
- Sensor entities for unleaded and diesel prices
- Config flow for adding stations by ID
- Auto-refresh every 30 minutes via DataUpdateCoordinator
- Next.js JSON extraction for reliable data fetching
- Device grouping per station
