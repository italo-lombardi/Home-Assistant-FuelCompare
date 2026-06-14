# Contributing a New Data Provider

This integration uses a plugin architecture. Adding a new country or data source requires only writing one new provider file and registering it. No changes to sensors, binary sensors, coordinator, or config flow are needed.

## Quick Start (6 steps)

### Step 1 — Create the provider file

```
custom_components/fuelcompare_ie/providers/{cc}_{name}.py
```

Example: `providers/de_tankerkoenig.py` for Germany via Tankerkoenig.

### Step 2 — Implement `async_fetch()`

```python
from .base import BaseProvider, ProviderError, StationData

class DEFakrankoeningProvider(BaseProvider):
    COUNTRY = "DE"
    PROVIDER_KEY = "de_tankerkoenig"
    LABEL = "Tankerkoenig"
    POLL_INTERVAL_SECONDS = 1800
    CAPABILITIES = frozenset({"diesel", "unleaded", "lastupdated", "name", "brand",
                               "county", "latitude", "longitude", "last_successful_fetch",
                               "is_open", "data_fetch_problem"})

    def __init__(self, station_id: str) -> None:
        self._station_id = station_id

    async def async_fetch(self, session, station_id: str) -> StationData:
        # Call your API, normalise the result, return StationData dict.
        # Raise ProviderError if data is unavailable.
        # Let aiohttp ClientError propagate — coordinator converts to UpdateFailed.
        url = f"https://creativecommons.tankerkoenig.de/json/detail.php"
        async with session.get(url, params={"id": station_id, "apikey": self._api_key}) as r:
            r.raise_for_status()
            raw = await r.json()
        if not raw.get("ok"):
            raise ProviderError(f"Tankerkoenig returned error for station {station_id}")
        s = raw["station"]
        return {
            "diesel": s.get("diesel"),
            "unleaded": s.get("e5"),
            "lastupdated": None,
            "name": s.get("name"),
            "brand": s.get("brand"),
            "county": s.get("place"),
            "latitude": s.get("lat"),
            "longitude": s.get("lng"),
        }
```

**Price normalisation:** if your source returns prices in cents (e.g. `189.9`), divide by 100 before returning. FuelFinder.ie returns EUR/litre directly — no division needed. Confirm before applying.

### Step 3 — Implement `async_fetch_station_name()`

Called once during config flow setup to pre-populate the station name field.

```python
async def async_fetch_station_name(self, session, station_id: str) -> str | None:
    try:
        data = await self.async_fetch(session, station_id)
        return data.get("name")
    except Exception:
        return None
```

Return `None` on any failure — the config flow falls back to `"Station {id}"`.

### Step 4 — Declare `CAPABILITIES`

`CAPABILITIES` is a `frozenset[str]` of the `StationData` keys your provider actually populates. The sensor and binary_sensor platforms create **exactly those entities and nothing else**.

```python
CAPABILITIES = frozenset({
    "diesel",            # creates a diesel price sensor
    "unleaded",          # creates an unleaded price sensor
    "lastupdated",       # creates a price_last_updated timestamp sensor
    "name",              # creates a station_name sensor
    "brand",             # creates a brand sensor
    "county",            # creates a county sensor
    "latitude",          # creates a latitude sensor
    "longitude",         # creates a longitude sensor
    # Always include these two — they are coordinator-managed:
    "last_successful_fetch",
    "data_fetch_problem",
    # Include if your source provides open/closed status:
    "is_open",
})
```

**Special keys** — always include unless you have a reason not to:
- `last_successful_fetch` — diagnostic timestamp sensor, coordinator-managed
- `data_fetch_problem` — diagnostic binary sensor, coordinator-managed
- `is_open` — only include if your source provides opening hours

**Unknown keys** — if you add a key to `CAPABILITIES` that does not exist in `StationData`, the class definition will raise `TypeError` immediately (caught at import time). Add the key to `StationData` in `base.py` first.

### Step 5 — Register in `PROVIDER_REGISTRY`

Edit `providers/__init__.py`:

```python
from .de_tankerkoenig import DETankerkoenigProvider  # add import

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    IEFuelCompareProvider.PROVIDER_KEY: IEFuelCompareProvider,
    IEFuelFinderProvider.PROVIDER_KEY: IEFuelFinderProvider,
    DETankerkoenigProvider.PROVIDER_KEY: DETankerkoenigProvider,  # add entry
}
```

That is the only change needed to the core component. The config flow country selector and provider picker update automatically.

### Step 6 — Add translation keys (if needed)

If your provider exposes entity keys not already in `strings.json`, add them:

```json
// strings.json — entity.sensor section
"diesel": { "name": "Diesel" }   // already exists, skip
"lpg":    { "name": "LPG" }      // add if your provider sets this key
```

Add the same key to `translations/en.json` and to each locale file in `translations/`. HA falls back to English for missing locale entries, so only the English entry is strictly required.

---

## StationData Field Reference

All fields are optional (`total=False`). Only populate what your source provides.

| Field | Type | Entity created | Notes |
|---|---|---|---|
| `unleaded` | `float \| None` | `sensor._unleaded` (€/L) | Legacy fuelcompare.ie key for petrol |
| `petrol` | `float \| None` | `sensor._petrol` (€/L) | FuelFinder.ie key |
| `diesel` | `float \| None` | `sensor._diesel` (€/L) | |
| `kerosene` | `float \| None` | `sensor._kerosene` (€/L) | |
| `cng` | `float \| None` | `sensor._cng` (€/L) | |
| `lpg` | `float \| None` | `sensor._lpg` (€/L) | |
| `e10` | `float \| None` | `sensor._e10` (€/L) | |
| `e85` | `float \| None` | `sensor._e85` (€/L) | |
| `premium_unleaded` | `float \| None` | `sensor._premium_unleaded` | |
| `premium_diesel` | `float \| None` | `sensor._premium_diesel` | |
| `adblue` | `float \| None` | `sensor._adblue` | |
| `lastupdated` | `str \| None` | `sensor._price_last_updated` | ISO 8601 string |
| `name` | `str \| None` | `sensor._station_name` | Full display name |
| `tablename` | `str \| None` | `sensor._brand` | Brand as slug (legacy) |
| `brand` | `str \| None` | `sensor._brand` | Preferred over tablename |
| `county` | `str \| None` | `sensor._county` | County or region |
| `address` | `str \| None` | `sensor._address` | Street address |
| `latitude` | `float \| None` | `sensor._latitude` | WGS84 |
| `longitude` | `float \| None` | `sensor._longitude` | WGS84 |
| `phone` | `str \| None` | `sensor._phone` | |
| `website` | `str \| None` | `sensor._website` | |
| `working_hours` | `str \| None` | `sensor._working_hours` | JSON dict `{Day: "6am-10pm"}` |
| `opening_hours` | `str \| None` | `sensor._opening_hours` | OSM format `"Mo-Su 07:00-23:00"` |
| `about` | `dict \| None` | Facility sensors | Legacy nested dict |
| `accessibility` | `dict \| None` | `sensor._accessibility` | `{feature: bool}` |
| `offerings` | `dict \| None` | `sensor._offerings` | `{feature: bool}` |
| `amenities` | `dict \| None` | `sensor._amenities` | `{feature: bool}` |
| `payments` | `dict \| None` | `sensor._payments` | `{feature: bool}` |
| `has_car_wash` | `bool \| None` | `binary_sensor._has_car_wash` | |
| `has_shop` | `bool \| None` | `binary_sensor._has_shop` | |
| `has_toilet` | `bool \| None` | `binary_sensor._has_toilet` | |
| `has_atm` | `bool \| None` | `binary_sensor._has_atm` | |
| `has_disabled_access` | `bool \| None` | `binary_sensor._has_disabled_access` | |
| `has_electric_charging` | `bool \| None` | `binary_sensor._has_electric_charging` | |
| `accepts_cash` | `bool \| None` | `binary_sensor._accepts_cash` | |
| `accepts_cards` | `bool \| None` | `binary_sensor._accepts_cards` | |
| `accepts_contactless` | `bool \| None` | `binary_sensor._accepts_contactless` | |
| `is_open` | `bool \| None` | `binary_sensor._is_open` | Direct bool from source |
| `price_confidence` | `str \| None` | `sensor._price_confidence` | FuelFinder freshness tier |
| `has_price` | `bool \| None` | `binary_sensor._has_price` | FuelFinder: any submission exists |
| `location` | `str \| None` | `sensor._location` | `"{lat},{lng}"` string |
| `source_station_id` | `str \| None` | — | Passthrough for attributes |

---

## `CONFIG_MODE`

| Value | Use case | Config flow step |
|---|---|---|
| `"station_id"` (default) | User enters a station ID (numeric or UUID) | `async_step_station` |
| `"location"` | User enters lat/lng + radius; provider fetches all nearby stations | `async_step_location` |

```python
CONFIG_MODE = "location"  # for government open-data APIs (Spain, France, etc.)
```

---

## `POLL_INTERVAL_SECONDS`

```python
POLL_INTERVAL_SECONDS = 1800  # 30 min (default, matches fuelcompare.ie)
POLL_INTERVAL_SECONDS = 600   # 10 min (for APIs that update frequently)
POLL_INTERVAL_SECONDS = 86400 # 24 h (for FuelWatch WA — daily price scheme)
```

---

## Running tests

```bash
# All tests (must stay at 100% pass)
python3 -m pytest tests/ -q

# Lint
python3 -m ruff check .
python3 -m ruff format --check .

# Coverage
python3 -m pytest tests/ --cov=custom_components/fuelcompare_ie --cov-report=term-missing
```

100% line coverage is required. Every new provider file needs a corresponding test file at `tests/test_{provider_key}_provider.py`.

---

## Minimal provider example (15 lines)

```python
from __future__ import annotations
from aiohttp import ClientSession
from .base import BaseProvider, ProviderError, StationData

class EXMinimalProvider(BaseProvider):
    COUNTRY = "IE"
    PROVIDER_KEY = "ie_minimal"
    LABEL = "Minimal Example"
    CAPABILITIES = frozenset({"diesel", "last_successful_fetch", "data_fetch_problem"})

    def __init__(self, station_id: str) -> None:
        self._station_id = station_id

    async def async_fetch(self, session: ClientSession, station_id: str) -> StationData:
        async with session.get(f"https://example.ie/api/price/{station_id}") as r:
            r.raise_for_status()
            data = await r.json()
        if not data:
            raise ProviderError(f"No data for station {station_id}")
        return {"diesel": float(data["price"])}

    async def async_fetch_station_name(self, session: ClientSession, station_id: str) -> str | None:
        return None
```
