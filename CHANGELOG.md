# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0-beta.2] - 2026-06-17

> **Pre-release** — Ireland provider fixes and config flow UX improvements.

### Fixed
- **pumps.ie**: XML parser replaced with regex — handles malformed HTML entities (`&aacute;`) and double-quoted attributes that caused silent empty-station-list failures
- **pumps.ie**: `async_fetch_station_name` now concurrent (`asyncio.gather`) instead of sequential
- **pumps.ie**: SSL warning emitted lazily on first fetch (was at module import, spamming logs for all users)
- **ie_fuelfinder**: removed `has_price` filter from picker — all stations now shown; priceless ones populate automatically once prices are submitted
- Config flow: station picker sorted case-insensitively
- Config flow: picker label used as suggested name fallback (was raw UUID)
- Config flow: flow blocked when location-search returns empty list for station-id providers
- Config flow: `no_stations_found_location` error message (was generic county message for coordinate searches)
- URL length guard: `get_station_page_url()` falls back to homepage when URL exceeds 255-char HA state limit

### Changed
- README: alpha warning banner added; disclaimer extended to cover all providers; roadmap section removed
- 35 translation files updated with `no_stations_found_location` key

## [0.7.0-beta.1] - 2026-06-17

> **Pre-release** — packaged for HACS pre-release channel and GitHub pre-release tag (`v0.7.0-beta.1`). Promotes to `0.7.0` stable after a soak window with no blocker reports.

### Breaking
- Integration display name: `"FuelCompare.ie"` → `"Fuel Compare"` (domain/entity IDs unchanged)
- `ba_fuel`: `sensor.<name>_petrol` → `sensor.<name>_unleaded` (95-octane renamed for consistency)
- Entities retain last-known value on fetch failure instead of flipping to `unavailable` — use `binary_sensor.<station>_data_fetch_problem` to detect outages

### Added
- **36 providers across 27 countries**: Albania, Austria, Australia (NSW/QLD/VIC/WA), Belgium, Bosnia, Canada (QC), Croatia, Czech Republic, Denmark, Finland, France, Germany, Greece, Iceland, Ireland (fuelcompare.ie + FuelFinder.ie + pumps.ie), Italy, Lithuania, Luxembourg, Malta, Moldova, Montenegro, Netherlands, Norway, Poland, Portugal, Slovenia, Spain, Sweden, Switzerland, UK, EU Oil Bulletin
- **Plugin architecture**: `BaseProvider` ABC, `CAPABILITIES` frozenset, `StationData` TypedDict, `PROVIDER_REGISTRY`
- **Config flow**: country → provider → location/county/station steps; API key step for providers that require it
- **FuelFinder.ie provider**: crowd-sourced Irish station prices, `kerosene`/`cng` fuel types, OSM opening hours
- **Currency-aware sensors**: `CURRENCY` ClassVar per provider (€ default, £ GB, A$ AU)
- **Diagnostic sensors**: `binary_sensor.<station>_data_fetch_problem` and `sensor.<station>_last_successful_fetch`
- `CONTRIBUTING.md` — guide for adding new providers

### Fixed
- `ConfigEntryNotReady` raised when provider key missing (was bare `KeyError`)
- Coordinator stored in `hass.data` before `async_config_entry_first_refresh`
- Unique-ID collision for location-mode entries (sensors now read `coordinator.station_id`)
- `_day_matches()`: returns `False` for unparseable specs; handles comma-separated OSM ranges (`Tu-Th,Sa`)
- `24/7` detection no longer false-positives on `Mo-Fr 07:00-24:00`; `24:00` normalized to `00:00`
- `StationIsOpenBinarySensor` device class set to `None` (was `CONNECTIVITY`)
- Config flow: `postal_code` preserved on error re-render; `_abort_if_unique_id_configured` called unconditionally; options flow no longer writes spurious `_dummy` key; `is_location_entry` detection uses `CONF_LATITUDE`
- Credentials stripped from `UpdateFailed` log message
- Staffed FR stations: `is_open = None` instead of `False`
- Falsy-zero lat/lng/radius bugs in `au_nsw`, `pt_dgeg`, `se_bensinpriser`, `lu_carbu`
- `lastupdated` removed from CAPABILITIES where always `None` (`si_goriva`, `se_bensinpriser`, `lt_saurida`)
- `de_tankerkoenig`: `ClientError` propagates for stale-data retention
- `gb_fuelfinder`: `CURRENCY = "£"` (was `"GBP/L"`)
- `crypto.py`: validates `Salted__` magic header before AES decrypt
- AU providers: cents → AUD/litre (÷100)
- Translation fixes: `uk.json` unleaded, `bg.json` name label, `da.json` price_confidence; 24 non-EN station-step descriptions repaired

## [0.6.0] - 2026-06-08

### Breaking
- Entities retain last-known value on fetch failure (was `unavailable`). Use `binary_sensor.<station>_data_fetch_problem` to detect outages.

### Added
- `binary_sensor.<station>_data_fetch_problem` — `on` when last fetch failed
- `sensor.<station>_last_successful_fetch` — UTC timestamp of last successful fetch
- Each station now creates **14 entities** (was 12)

## [0.5.3] - 2026-06-03

### Fixed
- AES decrypt key extraction: layered fallback (legacy single-chunk → broad scan across all chunks). Fixes `Station data not found` after fuelcompare.ie relocated the key to a shared vendor chunk.

### Changed
- Coordinator modularized: `crypto.py` (AES), `page_assets.py` (buildId + key extraction)

## [0.5.2] - 2026-06-01

### Fixed
- PKCS7 padding validation: bad pad length raises `ValueError` instead of silent corruption
- Config flow `_fetch_station_name` exceptions logged at DEBUG instead of swallowed

## [0.5.1] - 2026-05-22

### Added
- Two-step config flow: station name auto-fetched and pre-populated
- `station_name` sensor (full name, e.g. `Circle K Mulhuddart`)
- `station_id` in `extra_state_attributes` on all entities

### Fixed
- `working_hours` / `is_open` use `dt_util.now()` (HA timezone) instead of `datetime.now()` (system timezone)

## [0.5.0] - 2026-05-18

### Added
- Encrypted API fallback for stations not served via Next.js SSR
- Dynamic AES key extraction from JS bundle; auto-refresh on key rotation

## [0.4.0] - 2026-05-11

### Added
- Full test suite (45 tests, 100% coverage)
- `SECURITY.md`, Dependabot, CI (ruff + pytest)
- HA translations system (`strings.json`, `translations/en.json`)

### Fixed
- Duplicate-entry bug when station ID has leading zeros
- `async_unload_entry` crash if coordinator never stored
- Midnight-crossing detection off-by-one
- Invalid `MEASUREMENT` + `MONETARY` sensor class combination

## [0.3.0] - 2026-04-24

### Added
- Translations for 24 languages

## [0.2.0] - 2026-04-24

### Added
- `price_last_updated` timestamp sensor
- Shared HA HTTP session

## [0.1.0] - 2026-04-15

### Added
- Station sensors: brand, county, working hours, is-open, accessibility/offerings/amenities/payments facilities

## [0.0.1] - 2026-04-15

### Added
- Initial release: unleaded/diesel price sensors, config flow, 30-minute auto-refresh
