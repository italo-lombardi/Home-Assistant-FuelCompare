# Follow-up PR Requirements

Issues deferred from PR #19 review (65 total). Fix all items below on a new branch off `main`.

---

## HIGH (20) — Fix before merge

### H-01 `__init__.py:38-42` | Correctness
**Issue:** When `CONF_STATION_ID` is `""` and lat/lng both absent, `station_id` stays `""` → all entity unique_ids share prefix `fuelcompare_ie__*` → collision across entries.
**Fix:** Add final fallback after the lat/lng block:
```python
if not station_id:
    station_id = entry.entry_id
```

### H-02 `__init__.py:65-71` | Correctness
**Issue:** `if _api_key_options is not None` passes empty string `""` to provider. Providers testing `if api_key:` treat it as no key silently.
**Fix:** Change guard to `if _api_key_options` (truthy check).

### H-03 `__init__.py:47-49` | Correctness
**Issue:** Silent fallback to `DEFAULT_PROVIDER` when `provider_key` not in registry — no log message.
**Fix:** Add before fallback:
```python
_LOGGER.warning("Unknown provider key %r, falling back to %r", provider_key, DEFAULT_PROVIDER)
```

### H-04 `config_flow.py:724-726` | Correctness
**Issue:** `data[CONF_LONGITUDE] = self._longitude` inside `if self._latitude is not None:` — longitude may still be `None`.
**Fix:** Change guard to `if self._latitude is not None and self._longitude is not None:`.

### H-05 `config_flow.py:487-492` | Correctness
**Issue:** Hardcoded `https://onboarding.tankerkoenig.de/` shown as registration URL for any provider with `REQUIRES_API_KEY = True` but no `API_KEY_REGISTRATION_URL`.
**Fix:** Default fallback to `""` or generic URL; abort flow if `provider_cls_for_key is None`.

### H-06 `binary_sensor.py:91-95` | Correctness
**Issue:** `if "closed" in s: return False` fires on `"Mo-Fr 08:00-20:00; Sa closed"` (substring match) → station reported closed all week.
**Fix:** `if s.strip() == "closed": return False`

### H-07 `binary_sensor.py:125-151` | Correctness
**Issue:** `_is_open_osm` only evaluates first two `HH:MM` tokens per rule. Lunch-break rules `"Mo-Fr 08:00-12:00 13:00-20:00"` show closed 12:00–20:00.
**Fix:** Collect all `HH:MM` pairs within a rule; return `True` if current time falls in any pair.

### H-08 `page_assets.py:99-126` | Error Handling
**Issue:** `_extract_key_station_chunk` has no try/except around `session.get()`. Network timeout/non-200 propagates as unexpected exception bypassing `UpdateFailed`.
**Fix:**
```python
try:
    async with session.get(url, ...) as resp:
        ...
except (ClientError, asyncio.TimeoutError) as err:
    raise ProviderError(...) from err
```

### H-09 `crypto.py:38` | Correctness
**Issue:** `padded[-1]` accessed without checking `padded` is non-empty. Truncated ciphertext → unhandled `IndexError`.
**Fix:** Add before line 38:
```python
if not padded:
    raise ValueError("Decrypted output is empty — ciphertext too short")
```

### H-10 `fi_tankille.py:459-482` | HA Contract
**Issue:** `ClientResponseError` wrapped as `ProviderError`. Coordinator contract requires `ClientError` to propagate unwrapped for retry logic.
**Fix:** Remove `except ClientResponseError` blocks (lines 474–476 and 481–482); let it propagate.

### H-11 `eu_oil_bulletin.py:488-495` | HA Contract
**Issue:** Same `ClientResponseError` → `ProviderError` wrapping as H-10.
**Fix:** Re-raise `ClientResponseError` directly; only wrap non-network parse errors (eg. `ValueError`, openpyxl errors) as `ProviderError`.

### H-12 All 36 provider files | HA Contract
**Issue:** Every provider CAPABILITIES frozenset includes `last_successful_fetch` and `data_fetch_problem`. `BaseProvider` docstring says *do not list them*. Also: docstring claims both are always created but `last_successful_fetch` is CAPABILITIES-gated in `sensor.py:155-157`.
**Fix:**
1. Remove `last_successful_fetch` and `data_fetch_problem` from every provider's `CAPABILITIES` frozenset.
2. Correct `base.py` docstring: `data_fetch_problem` binary sensor is always created unconditionally; `last_successful_fetch` sensor is CAPABILITIES-gated.

### H-13 `ie_fuelcompare.py:34-48` | Correctness
**Issue:** CAPABILITIES declares `"brand"` and `"is_open"` but `_parse_station` never sets either → 2 permanently unavailable entities for every IE user.
**Fix:** Either:
- (a) Populate `brand` (eg. from `tablename.replace("_", " ").title()`) and `is_open` (from `working_hours`), or
- (b) Remove both from CAPABILITIES.

### H-14 `fr_carburants.py:259-277` | HA Contract
**Issue:** `source_station_id` set in `_build_station_data` but not declared in CAPABILITIES.
**Fix:** Add `"source_station_id"` to CAPABILITIES or remove from `_build_station_data`.

### H-15 `es_minetur.py:424-428` | Correctness
**Issue:** `"e85": None` and `"adblue": None` set unconditionally but neither is in CAPABILITIES. Dead assignments.
**Fix:** Remove from `_parse_station` or add to CAPABILITIES.

### H-16 `no_drivstoff.py:215` | Correctness
**Issue:** `CURRENCY = "NOK/L"` — `/L` suffix creates non-standard HA unit breaking long-term statistics. Correct is `"kr"`.
**Fix:** `CURRENCY: ClassVar[str] = "kr"`

### H-17 All 24 non-English translation files | Translation
**Issue:** `config.step.user` and `config.step.provider` are `{}` in all 24 non-English locales. First two config flow screens completely untranslated.
**Fix:** Add translated `title` and `data` entries for `user` and `provider` steps to every non-English file.

### H-18 All 24 non-English translation files | Translation
**Issue:** `config.step.station.description` has inlined hardcoded text (eg. Circle K Dublin example) instead of required `"{hint}"` placeholder.
**Fix:** Replace inlined descriptions with `"{hint}"` in all 24 non-English files.

### H-19 All 24 non-English translation files | Translation
**Issue:** `county`, `api_key`, `station_picker`, `location` step titles/labels and 4 error/abort keys (`invalid_api_key`, `invalid_location`, `no_stations_found`, `no_providers_for_country`) are hardcoded English in all 24 non-English files.
**Fix:** Translate all affected keys in every non-English file.

### H-20 `test_ie_fuelcompare_provider.py:166-204` | Test Quality
**Issue:** `test_fetch_encrypted_api_retries_with_broad_scan_on_decrypt_failure` only calls `_parse_station()`. Never exercises the retry/decrypt path its name claims.
**Fix:** Rewrite to call `_fetch_encrypted_api()` and verify `_fetch_page_assets(broad=True)` fires after a decrypt failure, with second attempt succeeding.

---

## MEDIUM (25)

### M-01 `coordinator.py:97-122` | Error Handling
`ClientError` logged at DEBUG (discards URL, status, reason). `ProviderError` not logged at all.
**Fix:** Log both at `WARNING` with `str(err)`.

### M-02 `coordinator.py:97` | Type Safety
`_async_update_data` annotated `-> dict` but class is `DataUpdateCoordinator[StationData]`.
**Fix:** Change return annotation to `-> StationData`.

### M-03 `__init__.py:51-55` | HA Contract
`from homeassistant.exceptions import ConfigEntryNotReady` inside a conditional branch.
**Fix:** Move to module-level imports.

### M-04 `config_flow.py:603` | Correctness
`if self._radius_km is not None:` is always `True` (`_radius_km` typed `float`, never `None`). Dead guard.
**Fix:** Remove or retype `_radius_km: float | None`.

### M-05 `config_flow.py:570-576` | HA Contract
`_abort_if_unique_id_configured()` called outside `if not self.unique_id:` block in `async_step_station_picker`.
**Fix:** Move inside the block so it only fires immediately after `async_set_unique_id`.

### M-06 `config_flow.py:343-364` | Correctness
`_fetch_station_name` passes `station_id` at construction AND as second argument to `async_fetch_station_name`. Likely redundant.
**Fix:** Audit whether second argument is needed; remove if not.

### M-07 `strings.json:62` | Correctness
Error `invalid_station_id` says "must be a positive integer" but validation only checks for empty string.
**Fix:** Add numeric validation in `async_step_station` or change message to "Station ID cannot be empty."

### M-08 `config_flow.py:753` | HA Contract
`async_step_init` writes `user_input` directly without validating/stripping empty API key.
**Fix:** Strip and validate before `async_create_entry`.

### M-09/M-10 30+ provider files | Type Safety
`CAPABILITIES: frozenset[str] = frozenset(...)` without `ClassVar` in most providers. Breaks static type checking.
**Affected providers:** `al_fuel`, `at_econtrol`, `au_fuelwatch`, `au_nsw`, `au_qld`, `au_vic`, `ba_fuel`, `be_carbu`, `ca_qc`, `ch_tcs`, `cz_ccs`, `de_tankerkoenig`, `dk_fuelfinder`, `es_minetur`, `eu_oil_bulletin`, `fi_tankille`, `fr_carburants`, `gb_fuelfinder`, `gr_fuelgov`, `hr_mzoe`, `ie_fuelcompare`, `ie_fuelfinder`, `ie_pumps`, `is_fuel`, `it_mase`, `lt_saurida`, `lu_carbu`, `md_fuel`, `me_fuel`, `mt_fuel`, `nl_anwb`, `no_drivstoff`, `pl_benzyna`, `pt_dgeg`, `se_bensinpriser`, `si_goriva`.
**Fix:** `CAPABILITIES: ClassVar[frozenset[str]] = frozenset(...)` in every file; add `from typing import ClassVar` where missing.

### M-11 Australian providers | Correctness
`au_fuelwatch`, `au_nsw`, `au_qld`, `au_vic` all have `CURRENCY = "AUD/L"`. Correct is `"A$"`.
**Fix:** `CURRENCY: ClassVar[str] = "A$"` for all four.

### M-12 9 non-EUR providers | Correctness
`CURRENCY` with `/L` suffix in: `cz_ccs` (`"CZK/L"`), `ch_tcs` (`"CHF/L"`), `is_fuel` (`"ISK/L"`), `ca_qc` (`"CAD/L"`), `md_fuel` (`"MDL/L"`), `ba_fuel` (`"BAM/L"`), `se_bensinpriser` (`"SEK/L"`), `pl_benzyna` (`"PLN/L"`), `dk_fuelfinder` (`"DKK/L"`).
**Fix:** Use bare currency symbols as documented in `base.py` comments: `"Kč"`, `"Fr."`, `"kr"`, `"CA$"`, `"L"` (MDL), `"KM"`, `"kr"`, `"zł"`, `"kr"`.

### M-13 `si_goriva`, `lt_saurida`, `se_bensinpriser`, `pt_dgeg` | Architecture
`source_station_id`/`lastupdated` set in `_parse_station` but not in CAPABILITIES.
**Fix:** Declare in CAPABILITIES (to surface as entities) or remove from returned dict.

### M-14 `manifest.json:18` | Architecture
`"tzdata>=2024.1"` listed as requirement but never imported. HA Docker already includes it.
**Fix:** Remove, or add comment: `# Required on Windows/minimal Linux where OS timezone DB absent`.

### M-15 `base.py:226-228` | Correctness
Comment says "14 entities" but frozenset has 12 keys.
**Fix:** Correct count to 12.

### M-16 `au_nsw.py:331-342` | Correctness
`async_list_stations` applies cents-to-EUR normalisation on display prices. Should only normalise in `_build_station_data`.
**Fix:** Remove normalisation from `async_list_stations`.

### M-17 `page_assets.py:44-49` | Dead Code
`_STATION_CHUNK_FINDALL_RE` is a duplicate of `_STATION_CHUNK_RE` without capture group.
**Fix:** Remove `_STATION_CHUNK_FINDALL_RE`; use `_STATION_CHUNK_RE.findall()`.

### M-18 `page_assets.py:50` | Correctness
`_AES_KEY_RE` only matches lowercase hex; anchored on variable name `e` (minifier-fragile).
**Fix:** Add `[A-F]`; relax anchor: `AES\.decrypt\(\w+,"([a-fA-F0-9]{64})"`.

### M-19 `sensor.py:477` / `binary_sensor.py:276` | Correctness
`strftime("%A")` returns locale-specific day name. Non-English HA → never matches stored English day names → always returns `None`.
**Fix:**
```python
_DAYS = ("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")
day_name = _DAYS[dt_util.as_local(dt_util.now()).weekday()]
```

### M-20 `ie_fuelcompare.py:343` | Dead Code
`fuel_data["about"]` set but `"about"` not in CAPABILITIES. Dead data.
**Fix:** Add `"about"` to CAPABILITIES or remove the assignment.

### M-21 `sensor.py:70` | HA Contract
`coordinator._provider.CAPABILITIES` accessed directly via private attribute.
**Fix:** Expose as `coordinator.provider_capabilities` property.

### M-22 `strings.json:47-52` | Correctness
`postal_code` missing from `config.step.location.data`. Shows raw key `"postal_code"` in UI.
**Fix:** Add `"postal_code": "Postal Code"` to `strings.json` and `en.json` `config.step.location.data`.

### M-23 `ie_pumps.py:101-107` | Security
TLS certificate verification fully disabled (`CERT_NONE`).
**Fix:** Track cert renewal; log at `WARNING` once on provider startup (not module import). Add comment with cert expiry tracking issue link.

### M-24 `test_sensor.py:469-478` / `test_binary_sensor.py:247-257` | Test Quality
Stale-retention tests set `last_update_success=False` but no `available()` checks that flag. Assertions are tautological.
**Fix:** Add comment confirming intentional design (stale retention ignores `last_update_success`) and assert that entities remain `available` and retain last value after failed update.

### M-25 `test_coordinator.py` | Test Quality
`ProviderError → UpdateFailed` path untested. No full lifecycle test (`async_refresh()` → check `coordinator.data`).
**Fix:** Add test for each missing path.

---

## LOW (13)

### L-01 `__init__.py:113-114` | HA Contract
Broken coordinator left in `hass.data` if `ConfigEntryNotReady` raised during first refresh.
**Fix:** Store coordinator after successful refresh, or pop on exception.

### L-02 `__init__.py:129-134` | HA Contract
Stale coordinator not removed from `hass.data` when platform unload fails.
**Fix:** Pop unconditionally from `hass.data[DOMAIN]`.

### L-03 `coordinator.py:97` | Type Safety
Return annotation `-> dict` should be `-> StationData`. (Duplicate of M-02 — same fix.)

### L-04 `coordinator.py:74-93` | Dead Code
`_fetch_page_assets` / `_fetch_nextjs` / `_parse_station` shims exist only for test backwards-compat. No production path calls them.
**Fix:** Remove shims; patch `provider._fetch_nextjs` etc. directly in tests.

### L-05 `au_qld.py:314-315` / `au_vic.py:293-294` | Correctness
`kwargs.get("lat") or self._latitude` treats `0.0` as falsy.
**Fix:** `lat = kwargs.get("lat") if kwargs.get("lat") is not None else self._latitude`

### L-06 `binary_sensor.py:125-151` | Correctness
Time-only OSM rules (`"07:30-22:30"` with no day prefix) fail `_day_matches()` → station shows closed all day.
**Fix:** Detect when `rule.split()[0]` looks like a time token (matches `\d{1,2}:\d{2}`) and treat as no-day-restriction (all days match).

### L-07 `binary_sensor.py:148` | Correctness
`"00:00-24:00"` normalises to `00:00-00:00`; midnight-crossing logic fires by coincidence.
**Fix:** Add explicit guard: `if open_time == close_time == dt_time(0, 0): return True`.

### L-08 `sensor.py:297-310` | HA Contract
`lastupdated` stored as raw string in `extra_state_attributes`.
**Fix:** Parse via `_parse_lastupdated()` and return the `datetime` object.

### L-09 `ie_pumps.py:619-627` | Correctness
Debug log calls `data.get("petrol")` but price stored under `"unleaded"`. Always logs `None`.
**Fix:** Change to `data.get("unleaded")`.

### L-10 `at_econtrol.py:367` | HA Contract
`"lastupdated": None` set but not in CAPABILITIES. `"is_open"` in CAPABILITIES but never set.
**Fix:** Align: remove `lastupdated` from dict or add to CAPABILITIES; populate `is_open` or remove from CAPABILITIES.

### L-11 `de_tankerkoenig.py:232-234` | Security
API key exposed in query params; aiohttp debug logging logs full constructed URL.
**Fix:** Use redacting debug log: `_LOGGER.debug("Fetching station %s (key redacted)", station_id)`.

### L-12 `coordinator.py:61-67` | Correctness
`_build_id`/`_decrypt_key` setters silently discard writes when provider lacks attribute.
**Fix:** `_LOGGER.debug("Coordinator proxy setter: provider has no %r attribute, write discarded", name)`

### L-13 `test_coordinator.py:39` | Test Quality
`AsyncMock()` as HTTP response container. `resp.status` returns coroutine on Python 3.13+ (same bug fixed in commit `ec3557a`).
**Fix:** Change `mock_resp = AsyncMock()` to `mock_resp = MagicMock()`, keep `.json = AsyncMock(...)`.

---

## INFO (7)

### I-01 `base.py:250-253` | Architecture
Docstring claims both sentinel keys are always created. Only `data_fetch_problem` is; `last_successful_fetch` is CAPABILITIES-gated.
**Fix:** Correct docstring.

### I-02 `base.py:324` | Architecture
`__init_subclass__` abstract-methods timing check unreliable.
**Fix:** `if not inspect.isabstract(cls):` instead of `if not getattr(cls, '__abstractmethods__', None)`.

### I-03 `base.py:393-396` | Architecture
No enforcement that `STATION_LOOKUP_MODE != "manual_id"` providers override `async_list_stations`.
**Fix:** Add to `__init_subclass__`:
```python
if cls.STATION_LOOKUP_MODE != "manual_id" and "async_list_stations" not in cls.__dict__:
    raise TypeError(f"{cls.__name__} must override async_list_stations")
```

### I-04 `coordinator.py:44-48` | Architecture
`getattr(provider, "POLL_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL)` fallback is unreachable.
**Fix:** `provider.POLL_INTERVAL_SECONDS` directly.

### I-05 `de_tankerkoenig.py:6` | Security
Literal test API key `00000000-0000-0000-0000-000000000002` in module docstring triggers secret scanners.
**Fix:** Replace with Tankerkoenig docs URL reference.

### I-06 `ie_fuelfinder.py:620-630` | Architecture
6 keys suppressed with `# type: ignore[typeddict-unknown-key]`; not in StationData or CAPABILITIES. Dead data.
**Fix:** Add to `StationData` and CAPABILITIES, or remove from `_build_station_data`.

### I-07 `__init__.py:89-91` | Correctness
Numeric county code (eg. `"75"` for Paris) may be misidentified as postal code.
**Fix:** Only apply county→postal fallback when `NEEDS_POSTAL_CODE = True` and no explicit `CONF_POSTAL_CODE` in `entry.data`.

---

## Constraints for the new PR

- `python3 -m pytest tests/ -q` must pass (3459+ tests, 0 failures) after every commit
- `python3 -m pytest tests/ --cov=custom_components/fuelcompare_ie --cov-report=term-missing -q` must stay at 100.00%
- `ruff check custom_components/ tests/` must be clean
- Commit in logical groups (binary_sensor / coordinator / config_flow / providers / tests / translations)
- Do not break any existing test
- Branch off `main` after PR #19 merges
