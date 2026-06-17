# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0-beta.2] - 2026-06-17

> **Pre-release** — Ireland provider fixes and config flow UX improvements.

### Fixed
- **pumps.ie**: stations now load correctly — parser rewritten to handle malformed API responses
- **pumps.ie**: SSL warning shown once on startup (not on every poll)
- **ie_fuelfinder**: all stations visible in picker, including those without prices yet
- Config flow: station picker sorted alphabetically; station name suggested from picker label
- Config flow: correct error shown for coordinate vs county searches with no results
- Station page URL capped at 255 chars (HA state limit); falls back to provider homepage

### Changed
- README: alpha warning banner; extended disclaimer; roadmap removed
- All 35 translation files updated

## [0.7.0-beta.1] - 2026-06-17

> **Pre-release** — first multi-country release; promotes to `0.7.0` stable after soak.

### Breaking
- Integration renamed from `FuelCompare.ie` → `Fuel Compare`
- `ba_fuel`: petrol sensor renamed `unleaded` for consistency
- Entities retain last-known value on fetch failure instead of going `unavailable`

### Added
- 36 providers across 27 countries (see README for full list)
- Country → provider → location/station config flow
- `binary_sensor.<station>_data_fetch_problem` and `sensor.<station>_last_successful_fetch`
- `CONTRIBUTING.md` for adding new providers

### Fixed
- Various coordinator, config flow, sensor, and translation fixes across all providers

## [0.6.0] - 2026-06-08

### Added
- `binary_sensor.<station>_data_fetch_problem` — `on` when last fetch failed
- `sensor.<station>_last_successful_fetch` — UTC timestamp of last successful fetch

### Changed
- Entities retain last-known value on fetch failure (was `unavailable`)

## [0.5.3] - 2026-06-03

### Fixed
- AES key extraction: layered fallback fixes `Station data not found` after fuelcompare.ie relocated the key

## [0.5.2] - 2026-06-01

### Fixed
- PKCS7 padding validation raises `ValueError` instead of silent corruption
- Config flow fetch exceptions logged at DEBUG

## [0.5.1] - 2026-05-22

### Added
- Two-step config flow with auto-populated station name
- `station_name` sensor; `station_id` in extra state attributes

### Fixed
- `working_hours` / `is_open` use HA timezone

## [0.5.0] - 2026-05-18

### Added
- Encrypted API fallback; dynamic AES key extraction with auto-refresh on rotation

## [0.4.0] - 2026-05-11

### Added
- Full test suite (45 tests, 100% coverage); CI (ruff + pytest); `SECURITY.md`; HA translations

### Fixed
- Duplicate-entry bug; `async_unload_entry` crash; midnight-crossing off-by-one; invalid sensor class

## [0.3.0] - 2026-04-24

### Added
- Translations for 24 languages

## [0.2.0] - 2026-04-24

### Added
- `price_last_updated` sensor; shared HA HTTP session

## [0.1.0] - 2026-04-15

### Added
- Brand, county, working hours, is-open, facilities sensors

## [0.0.1] - 2026-04-15

### Added
- Initial release: unleaded/diesel price sensors, config flow, 30-minute auto-refresh
