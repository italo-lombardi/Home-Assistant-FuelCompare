# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0-beta.1] - 2026-06-08

### Added
- New diagnostic binary sensor `binary_sensor.<station>_data_fetch_problem` (`problem` device class) — `on` when the last coordinator update failed, `off` when it succeeded. Always reports as available so automations can rely on a deterministic on/off signal even before the first successful fetch (no fetch yet ⇒ problem ⇒ on). Attributes expose `last_exception` and `last_successful_fetch` for richer diagnostics.
- New diagnostic timestamp sensor `sensor.<station>_last_successful_fetch` — UTC timestamp of the last successful fetch by the integration itself, advanced only after a fetch parses successfully. Distinct from `price_last_updated`, which reflects the site's own price-record timestamp.
- Coordinator now tracks `last_successful_fetch`, stamped after each successful parse.

### Changed (BREAKING)
- Entities now retain the last known value when a fetch fails instead of flipping to `unavailable`. Price, station info, working-hours, facility, and is-open entities all stay populated through transient outages (site offline, throttling, network blips). Automations that previously relied on `state == 'unavailable'` to detect integration outages must migrate to `binary_sensor.<station>_data_fetch_problem` (or compare `now()` against `sensor.<station>_last_successful_fetch`). First-ever fetch failures still show `unavailable` because no last-known value exists.
- `available` properties on `FuelPriceSensor` and `StationAboutCategorySensor` no longer gate on `coordinator.last_update_success`. Station-level info sensors and `StationIsOpenBinarySensor` gain explicit `available` overrides that drop the same gate.
- Each station now creates **15 entities** (was 13): the two new diagnostic entities are added per station.

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
