# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
