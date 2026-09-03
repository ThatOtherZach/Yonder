# Yonder

A vibe-first personal travel planner. Type a trip in plain English, pick a vibe, and get fare signals across multiple flight providers. Supports two modes: **Escape** (A→B) and **Detour** (multi-city stopovers / open getaways).

## How to run

The app is configured as the **"Start application"** workflow and starts automatically.

Manually: `python -m uvicorn yonder.web:app --host 0.0.0.0 --port 5000`

Open the preview pane to see the UI.

## How to test

- Focused work: `pytest -q tests/test_<area>.py`
- Verify deterministic full-suite coverage: `python scripts/test_shards.py check`
- Verify collected test equivalence: `python scripts/test_shards.py verify-collection`
- Run one bounded shard: `python scripts/test_shards.py run 1` (valid shard numbers: 1–6)
- Run the complete suite concurrently: `python scripts/test_shards.py all`

The shard runner automatically discovers every top-level `tests/test_*.py` file,
assigns each file to exactly one stable shard, reports the 20 slowest tests per
shard, and exits non-zero for test failures, collection failures, or a
270-second shard timeout.

## Stack

- **Backend**: Python 3.11, FastAPI, Uvicorn
- **Templates**: Jinja2 HTML
- **NLP / intent**: xAI Grok (`XAI_API_KEY`)
- **Flight providers**: Amadeus, Duffel, Travelpayouts, SerpAPI, AviationStack, built-in mock

## Environment variables

Set in Replit Secrets / env vars (see `.env.example` for the full list):

| Key | Purpose | Required for |
|-----|---------|--------------|
| `TESTING` | `true` = use mock fares, no API keys needed | Mock mode |
| `XAI_API_KEY` | xAI / Grok — NLP search + analysis | Live NLP |
| `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` | Amadeus flight search | Live fares |
| `DUFFEL_ACCESS_TOKEN` | Duffel flight search | Live fares |
| `SERPAPI_KEY` | SerpAPI Google Flights | Live fares |
| `TRAVELPAYOUTS_TOKEN` | Travelpayouts cached prices | Live fares |
| `AVIATIONSTACK_KEY` | Flight schedules / status | Live fares |
| `PROVIDER_MODE` | `smart` (default) or `scan_all` | — |
| `DEFAULT_CURRENCY` | Display currency, e.g. `USD` | — |

Currently running with `TESTING=true` — mock data only, no API keys needed.

## Project structure

```
yonder/
  web.py          # FastAPI app + all routes
  cli.py          # CLI entry point (yonder serve)
  engine.py       # Search orchestration
  grok.py         # xAI/Grok NLP client
  providers/      # Flight provider adapters (one file each)
  templates/      # Jinja2 HTML templates
  static/         # JS + CSS + geo data
```

## User preferences

- Run in mock/test mode by default (no API keys required).

## Progression: tiled world map + km²-unlocked XP

XP = raw square kilometers of land unlocked on a tiled world map (not country counts).

- **Tiles**: continent-scale countries — US, CA, MX, BR, AU, plus the UK split into England/Scotland/Wales/Northern Ireland — subdivide into ISO 3166-2 first-level regions (`US-TX`, `CA-ON`, `GB-ENG`). Every other country is one tile (plain ISO2). Registry + areas: `yonder/tiles.py`; map geometry: `yonder/static/tiles_admin1.json` (Natural Earth, public domain, simplified).
- **Partial-coverage rule**: a country-level entry for a subdivided country credits ONE average region (country total ÷ region count) — "some coverage".
- **Migration rule**: legacy visited-country lists convert to country-level tiles (not expanded to all subdivisions). `visited_tiles` pref is the tile source of truth; `visited_countries` stays in sync (stamp order preserved, first stamp = home).
- **Ranks**: same names/emoji, km² thresholds (0 → Armchair Explorer … 25M km² → Chaos Pilot) in `yonder/xp.py`. Avoid list no longer subtracts XP.
- **Search behavior**: a subdivided country is suppressed from getaway suggestions only when ALL its regions are marked; partial coverage keeps it eligible. Domestic seeds get a prompt hint listing unvisited home-country regions (`_domestic_region_hint` in `yonder/grok.py`).
