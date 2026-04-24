# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
