# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`gb_fuelfinder` — Opening Hours sensor for UK stations.** The Fuel Finder
  CSV now includes per-day opening time columns
  (`forecourts.opening_times.usual_days.*`). The provider parses these into an
  OSM `opening_hours` string (e.g. `Mo-Fr 07:00-22:00; Sa-Su 08:00-20:00`),
  enabling the Is Open binary sensor and the Opening Hours (OSM) sensor for
  all UK stations. Stations with the `twenty_four_hour_fuel` amenity flag set
  report `24/7`. `opening_hours` added to `CAPABILITIES`.

### Fixed
- **`binary_sensor.py` — midnight race in `is_on`.** `dt_util.now()` was
  called twice per evaluation: once inside `_get_today_hours_str()` to select
  the weekday key, and again inside `_is_open()` to compare the clock time.
  Calls straddling midnight could use the wrong day's hours. Fixed by
  capturing a single `now = dt_util.now()` snapshot in `is_on` and threading
  it through both calls.
- **`crypto.py` — missing AES block-alignment guard.** `cryptojs_decrypt` did
  not verify that the ciphertext length is a nonzero multiple of 16 before
  passing it to the AES-CBC decryptor. A truncated or corrupt payload now
  raises `ValueError` immediately instead of producing undefined output.
- **`base.py` — `STATION_PAGE_URL` fallback not length-capped.** The 255-char
  HA state-value limit was applied only to template-expanded URLs; the plain
  `STATION_PAGE_URL` homepage fallback was returned unchecked. Both paths now
  enforce the cap.

## [0.7.3b1] - 2026-07-08

Beta release. Final 0.7.3 will follow after additional feature work.

### Changed
- **`ie_fuelcompare` deprecated — fuelcompare.ie shut down 30 June 2026.** The
  upstream site is no longer reachable, so this provider cannot return data.
  It is now hidden from the config-flow picker (`DISABLED = True`), so new
  entries cannot be created. Existing entries continue to load and now raise
  an ERROR-severity repairs issue instructing the user to delete the entry
  and switch to `ie_fuelfinder` or `ie_pumps`. The provider class remains in
  the registry so orphaned entries stay identifiable rather than crashing on
  startup with "Unknown provider". Scheduled for full removal in a future
  major version.

## [0.7.2] - 2026-06-23

### Fixed
- **FuelWatch (au_fuelwatch) ignored the search radius** — the WA FuelWatch RSS
  endpoint has no native radius parameter, so the provider returned every
  station in the WA Region regardless of the user's `radius_km` setting.
  `async_list_stations` now applies a great-circle (haversine) filter
  client-side when `lat`, `lng` and `radius_km` are supplied, so the station
  picker shows only stations within the configured radius. Reported in #44.
- **e-control (at_econtrol) ignored the search radius** — same class of bug:
  the API hard-caps to 10 nearest stations and exposes no radius parameter,
  so `radius_km` was silently a no-op. `async_list_stations` now applies a
  client-side haversine filter, sharing `haversine_km` from the new
  `providers/_geo` module with au_fuelwatch.
- **Empty station list silently created a broken entry** — for location-search
  providers (e.g. `au_fuelwatch`), choosing coordinates outside the
  provider's coverage area (Sydney + 1 km on a WA-only feed, or a radius
  too small to capture any station) caused the config flow to fall through
  to entry creation with no `station_id`. The runtime then synthesised a
  station_id from lat/lng (e.g. `au_fuelwatch_-33.86880_151.20930`) that no
  provider could resolve, leaving the entry stuck in
  `Failed setup, will retry: Station '…' not found in FuelWatch feed`.
  The config flow now loops back to the previous step (location or
  county) with a `no_stations_found_location` / `no_stations_found`
  error banner so the user can widen the radius or pick a different
  county without restarting the flow from the country picker. The free-text
  station_id fallback that allowed any string through is gone. National-
  average / `global_list` providers (which genuinely have one synthetic
  entry) keep the silent-create shortcut; the EU Oil Bulletin path is
  unchanged.
- **National-average providers asked for coordinates** — Albania, Czech
  Republic, Malta, Moldova, Montenegro, Netherlands and Poland (ORLEN)
  publish only a single national reference row; the config flow
  nonetheless prompted for lat/lng/radius on setup and then discarded
  them. These providers now use `STATION_LOOKUP_MODE = "global_list"`
  (matching EU Oil Bulletin), so the location step is skipped and the
  user goes straight from provider → station picker → entry creation.

### Changed
- Re-enabled six providers verified live against their real upstreams
  during this audit: `al_fuel` (Albania), `cz_ccs` (Czech Republic),
  `md_fuel` (Moldova), `mt_fuel` (Malta), `nl_anwb` (Netherlands) and
  `pl_benzyna` (Poland / ORLEN). README status flipped from ⚠️ Disabled
  to 🤖 Smoke-tested for each. The remaining six providers in the
  "upstream broken" bucket (`ba_fuel`, `dk_fuelfinder`, `es_minetur`,
  `fi_tankille`, `lu_carbu`, `pt_dgeg`) still fail their live probe —
  these stay disabled.

### Internal
- New `providers/_geo.py` module with a shared `haversine_km` function plus
  a `filter_within_radius` helper. Extended with an optional `get_coords`
  callable so providers with nested coord shapes (`at_econtrol`,
  `au_nsw`, `au_qld`, `au_vic`, `ch_tcs`) use the same helper as
  flat-coord providers. All 16 providers that previously carried a
  private `_haversine_km` copy (au_nsw/au_qld/au_vic/be_carbu/ca_qc/
  ch_tcs/es_minetur/fr_carburants/gb_fuelfinder/ie_pumps/is_fuel/it_mase/
  pt_dgeg/se_bensinpriser/si_goriva/no_drivstoff) now import from
  `providers._geo`. Providers whose filter loop matched the helper's
  drop-on-missing-coords contract collapsed their loop into a single
  `filter_within_radius(...)` call (at_econtrol, au_fuelwatch, au_nsw,
  au_qld, au_vic, ch_tcs, ie_pumps, pt_dgeg). The rest (be_carbu, ca_qc,
  es_minetur, fr_carburants, gb_fuelfinder, is_fuel, it_mase,
  no_drivstoff, se_bensinpriser, si_goriva) kept their inline loop —
  each has provider-specific gating (e.g. keep-on-missing-coords,
  late-extracted station IDs) that differs from the helper's strict
  contract — and only swapped the haversine math to `_geo.haversine_km`.
  The duplicate `base.haversine_km` (atan2 formula, numerically
  identical to within ~1e-12 km) is deleted. No behaviour change.
- Dropped unused `latitude`/`longitude`/`radius_km` constructor parameters
  from national-average / no-coords providers (`al_fuel`, `ba_fuel`,
  `dk_fuelfinder`, `eu_oil_bulletin`, `lt_saurida`, `md_fuel`, `me_fuel`,
  `mt_fuel`, `pl_benzyna`) — these providers return a single country-level
  row (or have no per-station GPS at the source) and never read coordinates.
  Also dropped the unused `county` constructor parameter from `ba_fuel`,
  `dk_fuelfinder` and `mt_fuel`, and the `**kwargs` absorber from
  `me_fuel.__init__` so kwarg typos now surface as `TypeError`.
- Aligned the `radius_km=0` contract across **every** client-side filter
  provider: `0` / `None` / missing kwarg = "no filter" (matches
  `providers._geo.filter_within_radius`'s falsy-check semantics).
  Twelve providers used `kwargs.get("radius_km") or self._radius_km`,
  which silently rewrote an explicit `0` back to the constructor default
  (au_nsw, au_qld, au_vic, be_carbu, ca_qc, ch_tcs, de_tankerkoenig,
  es_minetur, fr_carburants, pt_dgeg, se_bensinpriser, si_goriva); three
  more (`at_econtrol`, `au_fuelwatch`, `ie_pumps`) used
  `kwargs.get("radius_km", default)` which preserves `0` but differs
  cosmetically. All fifteen now use the same
  `kwargs["radius_km"] if kwargs.get("radius_km") is not None else <default>`
  ternary that preserves a user-supplied `0`. `ca_qc.__init__` and
  `pt_dgeg.__init__` also dropped their `radius_km or 10.0` init
  rewrites in favour of the strict `is not None` check.
- Added the `no_stations_found` / `no_stations_found_location` /
  `no_stations_found_global` keys to `strings.json` and every locale's
  `config.abort` block (matching the existing `config.error` entries) so
  HA picks up the translated text on the re-rendered location / county
  step and on the `global_list` abort path.

## [0.7.1] - 2026-06-23

### Fixed
- **Multi-station add blocked for location-search providers** — a second (or
  third) station from the same search area could not be added because the
  config flow set the entry unique-ID at the location step (lat/lng-based)
  instead of the station-picker step (station-ID-based). On the second run,
  the lat/lng unique-ID was already taken and `_abort_if_unique_id_configured`
  blocked the flow before the user could pick a different station. Affected
  all providers with `CONFIG_MODE="location"` + `STATION_LOOKUP_MODE=
  "location_search"` (≈ 30 providers including au_fuelwatch, fr_carburants,
  de_tankerkoenig, gb_fuelfinder, es_minetur, it_mase and others). The unique
  ID is now always set from the selected station ID, matching the behaviour of
  `county_search` providers. Fixes #44.
- **Silent entry clobber on empty station list** — when a location-search
  provider returned no stations (API outage, coordinates outside coverage),
  the config flow fell through to `async_create_entry` with the lat/lng
  unique-ID still set, causing HA to silently unload and replace an existing
  entry. The flow now correctly aborts with `already_configured` in this case.

## [0.7.0] - 2026-06-19

First multi-country release. The integration now ships 36 providers across
30 countries (plus an EU-wide regional source) behind a country → provider →
location/station config flow. Existing Ireland entries upgrade in place;
only new-entry creation is filtered for currently-broken upstreams.

### Breaking
- Integration display name renamed from `FuelCompare.ie` → `Fuel Compare`.
  The `fuelcompare_ie` domain is unchanged, so existing entries keep working.
- `ba_fuel`: petrol sensor renamed `unleaded` for cross-provider consistency.
- Entities retain their last-known value on fetch failure instead of going
  `unavailable`. Use `binary_sensor.<name>_data_fetch_problem` and
  `sensor.<name>_last_successful_fetch` to detect outages.

### Added
- 36 providers across 30 countries (plus EU regional source) — see README
  for the full table and status legend (✅ Tested / 🤖 Smoke-tested /
  ⚠️ Disabled).
- Country → provider → location/station config flow. Each entry is
  independent; multiple countries can be tracked simultaneously.
- `binary_sensor.<station>_data_fetch_problem` — `on` when the last poll
  failed.
- `sensor.<station>_last_successful_fetch` — UTC timestamp of the last
  successful poll.
- Provider disable mechanism: new `DISABLED: ClassVar[bool] = False` on
  `BaseProvider`. Setting `True` hides the provider from the config flow
  selectors so users cannot create new entries against a known-broken
  upstream. Existing entries keep loading from cache and fall to
  `unavailable` only when polls fail. Flip back once the upstream is
  fixed.
- Smoke tests: top-level `smoke/` directory (outside `tests/` to avoid the
  `pytest_homeassistant_custom_component` socket block). Skipped unless
  `FUELCOMPARE_RUN_SMOKE=1` is set; one test per provider hits the live
  upstream from a capital-city probe.
- EC Weekly Oil Bulletin (EU) provider: country-list lookup mode for
  providers serving a fixed, spatially uniform list. Picker exposes the 27
  member states + EU27 / Euro-area aggregates.
- New `STATION_LOOKUP_MODE = "global_list"` and `no_stations_found_global`
  error key for global-list providers.
- `CONTRIBUTING.md` for adding new providers.

### Changed
- 12 providers disabled after a live audit returned empty results, HTTP
  4xx, or stale-cache failures: `al_fuel`, `ba_fuel`, `cz_ccs`,
  `dk_fuelfinder`, `es_minetur`, `fi_tankille`, `lu_carbu`, `md_fuel`,
  `mt_fuel`, `nl_anwb`, `pl_benzyna`, `pt_dgeg`. A further 4 providers
  remain disabled pending a tester with credentials (`au_qld`, `au_vic`,
  `de_tankerkoenig`, `no_drivstoff` — all require an API key). README
  marks the 16 disabled providers with the ⚠️ Disabled tier and the 16
  verified providers with the 🤖 Smoke-tested tier.
- Coordinator now surfaces the provider's own error text in `UpdateFailed`
  (e.g. `Provider error: Country code 'XX' not found in EC Oil Bulletin
  data`) instead of the bare class name. Messages over 240 chars are
  truncated. Providers remain responsible for keeping `ProviderError` text
  free of secrets.
- README rewritten to be provider-agnostic — generic entities, config, and
  setup sections; testers-welcome banner; extended disclaimer.
- All 35 translation files updated for the new flow and error keys.

### Fixed
- **EU Oil Bulletin**: `openpyxl>=3.1.0` added to `manifest.json`
  requirements. Without it the lazy import failed silently and
  `async_list_stations` returned `[]`, leaving the config-flow picker
  showing `No stations found for this county. Try a different county.`
  for a country-list provider.
- **EU Oil Bulletin**: config flow no longer asks for coordinates — the EC
  publishes only national weighted averages. Users pick a country
  directly.
- **EU Oil Bulletin**: country picker label shows just the country name;
  the previous label embedded weekly prices and went stale between polls.
- **EU Oil Bulletin**: suggested entry name follows the convention
  `EC Weekly Oil Bulletin (EU) - <Country>`.
- **EU Oil Bulletin**: station-picker description is now generic
  (`Pick a station, region, or country from the list below.`) instead of
  the diesel/petrol wording.
- **pumps.ie**: stations now load — parser rewritten to handle malformed
  API responses. SSL warning shown once on startup, not every poll.
- **ie_fuelfinder**: all stations visible in picker, including those
  without prices yet.
- Config flow: station picker sorted alphabetically; station name
  suggested from the picker label; correct error shown for coordinate vs
  county searches with no results; station page URL capped at 255 chars
  (HA state limit), falls back to provider homepage.

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
