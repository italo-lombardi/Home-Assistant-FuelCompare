# FuelCompare.ie — Home Assistant Custom Integration

> **Disclaimer:** This is an independent, unofficial custom integration for Home Assistant. It is not affiliated with, endorsed by, or in any way connected to FuelCompare.ie or its owners. The FuelCompare.ie name and website are the property of their respective owners. This project simply reads publicly available data from their website for personal use.

---

## What this is

A [Home Assistant](https://www.home-assistant.io/) custom integration that tracks live fuel prices for Irish petrol stations listed on [fuelcompare.ie](https://fuelcompare.ie). It creates sensors for unleaded and diesel prices at any station you choose, letting you use those prices in dashboards, automations, or cost calculations.

## How it works

FuelCompare.ie is built with [Next.js](https://nextjs.org/). Next.js embeds a `buildId` in every HTML page and serves its page data as structured JSON at a predictable path:

```
https://fuelcompare.ie/_next/data/{buildId}/station/{stationId}.json
```

This integration:

1. Loads the station HTML page once to extract the current `buildId`.
2. Fetches the Next.js JSON endpoint for that station to get the full station record.
3. Reads the `unleaded` and `diesel` price fields from the JSON and converts them to euros.
4. Repeats the fetch every **30 minutes** via Home Assistant's `DataUpdateCoordinator`.
5. If the `buildId` becomes stale (Next.js redeploys the site), it automatically re-fetches it before retrying.

No unofficial API keys, no scraping fragile HTML — just the same structured JSON the browser receives.

## Sensors created

![Sensors screenshot](custom_components/fuelcompare_ie/docs/sensors.png)

For each station you add, the integration creates two sensors:

| Sensor | Unit | Icon |
|--------|------|------|
| `sensor.<name>_unleaded` | € | `mdi:gas-station` |
| `sensor.<name>_diesel` | € | `mdi:gas-station-outline` |

Each sensor also exposes these attributes:

- `station_id` — the FuelCompare.ie numeric ID for the station
- `fuel_type` — `unleaded` or `diesel`
- `source` — `fuelcompare.ie`

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations** → three-dot menu → **Custom repositories**.
3. Add `https://github.com/italo-lombardi/Home-Assistant-FuelCompare` with category **Integration**.
4. Search for **FuelCompare.ie** and install it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/fuelcompare_ie` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **FuelCompare.ie**.
3. Enter the **Station ID** — the number at the end of the station URL on fuelcompare.ie.
4. Optionally enter a friendly name (defaults to `Station <id>`).

### Finding a station ID

1. Go to [fuelcompare.ie](https://fuelcompare.ie) and search for your station.
2. Click the station — the URL will look like `https://fuelcompare.ie/station/790`.
3. The number at the end (`790` in this example) is the Station ID.

You can add as many stations as you like; each gets its own device entry.

## Requirements

- Home Assistant 2024.1.0 or newer
- Internet access from the Home Assistant host

## Disclaimer (repeated for clarity)

This project is a personal, community tool. It is **not** the official FuelCompare.ie app or service. The author has no relationship with FuelCompare.ie. If FuelCompare.ie changes their website structure this integration may stop working; please open an issue and it will be looked at when time allows.

## License

MIT — see [LICENSE](LICENSE).

## Issues & contributions

Bug reports and pull requests are welcome at [italo-lombardi/Home-Assistant-FuelCompare](https://github.com/italo-lombardi/Home-Assistant-FuelCompare/issues).
