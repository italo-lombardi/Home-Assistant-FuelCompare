# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Provider disable mechanism**: new `DISABLED: ClassVar[bool] = False` on
  `BaseProvider`. Setting `True` hides the provider from the config flow
  country/provider selectors so users cannot create new entries against a
  known-broken upstream. Existing entries keep loading from cache; their
  sensors fall to `unavailable` when polls fail. Flip back to `False` once
  the upstream contract is fixed.
- **Smoke tests**: new top-level `smoke/` directory (outside `tests/` to
  avoid the pytest_homeassistant_custom_component socket block). Tests are
  skipped unless `FUELCOMPARE_RUN_SMOKE=1` is set; run with
  `FUELCOMPARE_RUN_SMOKE=1 pytest smoke -p no:homeassistant`. One test per
  provider hits the live upstream from a capital-city probe and asserts a
  non-empty station list / fetch.

### Changed
- **Provider audit (live verification)**: 12 providers disabled after live
  smoke testing returned empty results, HTTP 4xx, or stale-cache failures —
  `al_fuel`, `ba_fuel`, `cz_ccs`, `dk_fuelfinder`, `es_minetur`,
  `fi_tankille`, `lu_carbu`, `md_fuel`, `mt_fuel`, `nl_anwb`, `pl_benzyna`,
  `pt_dgeg`. README updated with new ⚠️ Disabled marker and 🤖 Smoke-tested
  tier for the 16 providers verified end-to-end on a dev HA install.

### Fixed
- Coordinator now surfaces the provider's own error text in `UpdateFailed`
  (e.g. `Provider error: Country code 'XX' not found in EC Oil Bulletin data`)
  instead of the bare class name `Provider error: ProviderError`. Messages
  longer than 240 chars are truncated to keep the repairs UI usable. Providers
  remain responsible for keeping `ProviderError` text free of secrets.
- **EU Oil Bulletin**: config flow no longer asks for coordinates. The EC
  publishes only national weighted averages, so the user picks a country
  directly from the list of 27 member states + EU27/Euro-area aggregates.
  New `STATION_LOOKUP_MODE = "global_list"` for providers serving a fixed,
  spatially uniform list.
- **EU Oil Bulletin**: country picker label now shows just the country
  name (was `Country — Diesel €X.XXX/L, E5 €X.XXX/L`); prices change
  weekly and made the dropdown look stale between polls.
- **EU Oil Bulletin**: suggested entry name now follows the convention
  `EC Weekly Oil Bulletin (EU) - <Country>` (e.g. `EC Weekly Oil Bulletin (EU) - Ireland`).

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
