# Yonder

**Go yonder.** A vibe-first travel gamble app — multi-provider fare signals, mandatory trip vibes, passport map as identity (no account required), Detour stopovers, boarding-pass results with shareable QR codes, field notes, and deal history.

Not a booking engine. Not for commercial resale. Scan free/cheap pricing APIs in parallel, compare, then book where you trust (outbound links support affiliate-style attribution).

## Modes

| Mode | What it does |
|------|----------------|
| **Escape** | Plain-English point A→B. Grok parses the trip (one traveler, economy). Providers return **one cheapest fare signal** for that destination. |
| **Detour** | Multi-day stopovers or open getaways (“somewhere new”, food/COL/safe). Grok invents candidate cities; APIs price legs. **One package per destination city**, up to five cities, cheapest first. |

**One compose card, one Go button** — no need to pick Escape vs Detour before typing. Intent is inferred from the prompt (+ vibe + passport map). Results can mix both shapes and filter with **All | Escape | Detour | Clear**.

1. **Prompt** — free text  
2. **Vibe slider** (required) — rainbow hue → named vibe; **random vibe on each fresh page load** (kept only after a live search)  
3. **Suggestion pills** — complete missing search data (shape, map, timing, budget tone); ★ Saves only **re-rank** which pills surface  
4. **Go** — posts to `POST /explore` (intent gate → Escape, Detour, or mix)  
5. **Depart** — optional; used for getaways / stopovers  
6. **Passport map** — open → visited → avoid (max 10 avoid). **First green stamp = home** (selection order). **Clear map** when more than one country is stamped  

### Architecture in one line

**Grok translates intent → structured package(s) → providers price without Grok → UI shows boarding passes. Soft aim ~30s for fares; Skip after ~42s returns partials. If you wait (or stream after Skip), field notes load with culture/food/vibe prose tinted by your prompt + vibe. COL lives inside the field-note card (behind **More** with culture/food/heads-up). ★ Save is the durable quality signal: it **re-ranks** suggestion pills and **hard-bans** those destinations from invent/board (never re-offers a city you already ★ Saved). Book links go through `/out` for affiliate attribution.**

Optional COL enrichment: Grok estimates a lean day bag (hotel + food + transit + culture) per stop; scored against your **Settings** cost/day + over/under % band. Leave cost/day at **0** to show COL without under/over ranking (budget omitted from the Grok prompt).

## How pricing works (honest)

Free APIs never perfectly match the Google Flights page you open 10 seconds later. This app treats them as **signals** and **historical data**:

- Prices display as **`~C$420`** (approx / last-seen)
- Every result is **logged** to local SQLite `price_history.db` — you build a personal dataset over time
- **Deal labels** (great / good / ok / high) compare a fare to *your* history for that route
- **Google Flights** + leg links for verification (source of truth for booking)
- Keys that are empty are ignored (no keyless scraping)

```powershell
python -m yonder.cli history
python -m yonder.cli history YYZ YVR
python -m yonder.cli history --export
```

## Reality check

You **cannot** legally unify every commercial flight API for free:

| Provider | Personal free access? | Notes |
|---|---|---|
| **Amadeus** Self-Service | Yes (monthly free quota) | Best free *live* offers API. Test env = sandbox. |
| **Travelpayouts / Aviasales** | Yes (free token) | Cached market prices — great for deal scanning. |
| **Duffel** | Sandbox free | Clean modern API; production needs account. |
| **SerpAPI → Google Flights** | Free monthly quota | Scraped Google Flights snapshot. |
| **Skyscanner partner API** | Not really | Free but needs affiliate/partner approval. |
| **Kiwi Tequila** | Restricted | No longer easy self-serve free for new users. |
| **Sabre / Travelport** | No | Enterprise GDS contracts. |
| **Airline NDCs** | Rarely | One-off partner deals. |

This app unifies whatever **you** have keys for behind one interface. Without keys it still runs on a **mock** provider so you can try the UX.

## Quick start

```powershell
cd Documents\GitHub\Yonder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# Demo with zero keys
python -m yonder.cli search YVR NRT 2026-09-15 --mock

# Local web UI (Escape + Detour)
python -m yonder.cli serve
# → http://127.0.0.1:8787
# → http://127.0.0.1:8787/?mode=escape
# → http://127.0.0.1:8787/?mode=detour
```

## Add real providers

```powershell
copy .env.example .env
# edit .env
```

### 1. Amadeus (recommended first)

1. Create a free account: https://developers.amadeus.com/
2. Create an app → copy **API Key** + **API Secret**
3. Put in `.env`:

```env
AMADEUS_CLIENT_ID=...
AMADEUS_CLIENT_SECRET=...
AMADEUS_ENV=test
```

Use `AMADEUS_ENV=production` only after you enable production in the Amadeus portal (still has a free monthly free-tier quota, then pay-as-you-go).

### 2. Travelpayouts (free cached prices)

1. Sign up: https://www.travelpayouts.com/
2. Profile → **API token**
3. `.env`: `TRAVELPAYOUTS_TOKEN=...`

### 3. Duffel

1. https://duffel.com/ → developer account
2. Sandbox access token → `DUFFEL_ACCESS_TOKEN=...`

### 4. SerpAPI Google Flights

1. https://serpapi.com/
2. `SERPAPI_KEY=...`

### 5. Grok (xAI) — natural language + invent

1. https://console.x.ai/ → create API key
2. `XAI_API_KEY=...`
3. Optional: `XAI_MODEL=grok-4.5`

Grok does **not** invent fares. It:

1. **Escape** — parses plain English → origin / dest / dates (always **1 adult, economy**)  
2. **Detour** — invents stopover/getaway candidate cities (honors visited + avoid map)  
3. Providers (or Test Data) price the package  

Default **origin** when you don’t name a city (Settings → **Home airport**):

1. `HOME_IATA` if set (e.g. `YVR`)  
2. Else primary airport of the **first passport-map country** (stamp **order** — first green stamp = home)  
3. Else country implied by **default currency** (CAD→YVR, USD→JFK, …)  
4. Else **USA / JFK**

## Web UI

| Path | Purpose |
|------|---------|
| `/` | Explore compose (intent-routed) |
| `/saved` | Saved boarding passes + QR share (Escape + Detour) |
| `/settings` | Keys, home airport, daily budget + %, Detour knobs, search timing, affiliate tag, Test Data |
| `/t/{kind}/{slug}/{id}` | **Shareable trip page** (QR target) |
| `/t/{id}` | Short share form |
| `POST /explore` | **Unified Go** — decision gate → Escape / Detour / mix |
| `POST /ask` | Force Escape path (legacy / soft force) |
| `POST /adventure` | Force Detour path (legacy / soft force) |
| `POST /api/travel-map` | Autosave visited/avoid |
| `POST /api/saved` | ★ Save Escape or Detour (only durable preference write) |
| `POST /api/results-clear` | Clear last Escape + Detour snapshots (UI **Clear**) |
| `POST /api/search-cancel` | Progress **Skip** — wrap up with partials |
| `POST /api/funnel` | Light engagement events (e.g. field-note expand) |
| `GET  /api/suggest` | Save-based ranking for suggestion pills (patterns only) |
| `GET  /api/place-brief` | Stream one field note (tone from prompt + vibe) |
| `GET  /out` | Affiliate-friendly book-link redirect + funnel log |

### Compose & results

- One card: **textarea** + vibe-colored **Go** → `/explore`  
- Under the text: **rainbow hue slider** + vibe name; **Depart** optional  
- **Dataset-completion pills** under the vibe (getaway / stopover / timed / map-aware / budget…); ★ Saves only **re-rank which patterns** surface — they do **not** inject prior Save cities as chips or invent seeds  
- Vibe **required**; random on cold load; locked after a live search  
- Intent gate: pure Escape / pure Detour / **mix** (Escape first, then Detour with fewer candidates)  
- Results: **All | Escape | Detour | Clear**  
- **Criteria bar** on results: **From** (IATA), **min/max stop days**, **Refresh**  
- **Refresh**: rolls **new** cities, excluding board destinations already shown this session; if nothing new prices, falls back to the **first result set** after Clear (pinned via `last_search`)  
- After search: auto-scroll to results  
- **Escape** and **Detour** both use the same **boarding-pass** card chrome  
- **Escape**: one cheapest fare; chip-driven “From JFK…” text never overrides **home** when the pill was dataset/template  
- **Detour**: one package per city (≤5; ≤3 when mixing)  
- **★ Saved destinations are hard-banned** from invent, seeds, and the final board (`saved_destination_iatas` → `exclude_iatas` through `seed_ideas` / `plan_adventure`)  
- **Soft aim ~30s** (`SEARCH_BUDGET_SECONDS`); **Skip** after ~42s (`SEARCH_MAX_SECONDS`) for partials — without Skip, search can run longer  

### Timing & field notes

- Fares first; **Skip** = cash out early with what’s priced  
- **Field notes** keep a fixed structure; prose tinted by **user prompt + trip vibe**  
- **Always visible**: title, subtitle, fast-fact chips, vibe line, closer  
- **Behind More** (collapsed by default): culture, food, heads-up, and **COL** (ground spend, vs home, your budget, all-in)  
- Expand is a light funnel signal only (`POST /api/funnel`) — not a preference write like ★ Save  
- If notes weren’t ready on first paint (e.g. after Skip), slots show *Writing field note…* and stream via `/api/place-brief`  

### Passport map

- Zoom / pan (wheel, +/−, ↺)  
- Click cycle: **open → visited → avoid → clear**  
- **Stamp order preserved** — first visited country = **home** when `HOME_IATA` is blank  
- Avoid list capped at **10**  
- **Clear map** when **more than one** country is stamped  
- Open countries can tint with the active vibe color  

### Cost of living (Detour)

- **Your budget** (Settings): one **cost / day** + **over/under %** (no city default; **0** = ranking off)  
- **Destination COL**: Grok/cache/fallback lean day bag per stop  
- Shown in the field-note card under **More**; under / within / over vs Settings band  

### ★ Saves vs invent

| Signal | What it does | What it does **not** do |
|--------|----------------|-------------------------|
| ★ **Save** | Durable preference; re-ranks pill **patterns**; hard-bans those IATAs on future boards | Re-show the same Save city as a new gamble |
| **Pills** | Complete missing prompt slots (shape, map, timing, budget tone) from vibe + map | Replay “Save · city” chips |
| **Refresh** | New candidates minus already-shown + Saves; else restore first set | Keep cycling the same five cities |

### Share / QR

- Boarding-pass stub shows a **PNG QR** (scannable) instead of a fake barcode  
- Link and QR use the **same absolute URL**, e.g.  
  `http://127.0.0.1:8787/t/detour/YVR-KEF-YVR-4d-2026-09-11-CAD780-getaway/abc123def456`  
- Opens a standalone trip page (route, legs, ground COL, booking links, copy link)  
- Shares stored locally in `shared_trips.db` (~90 days)  
- **Phone scans**: open the app via your **LAN IP** (not only `127.0.0.1`) so the QR host is reachable  

### Testing

```env
TESTING=true
```

Shows **Test Data** checkboxes on Escape/Detour so you can demo without live keys.

For a focused change, run the relevant file directly:

```bash
pytest -q tests/test_<area>.py
```

The complete suite is split into six deterministic, automatically discovered
file-level shards so no single command exceeds the environment limit:

```bash
python scripts/test_shards.py check
python scripts/test_shards.py verify-collection
python scripts/test_shards.py run 1  # valid shard numbers: 1-6
python scripts/test_shards.py all    # all six concurrently
```

Each test file belongs to exactly one shard. The runner reports slow-test
timings and exits non-zero when a shard fails, is interrupted, or exceeds its
270-second timeout.

## CLI

```powershell
# Which keys are loaded?
python -m yonder.cli providers

# One-way
python -m yonder.cli search YVR LHR 2026-10-01

# Round-trip, CAD, nonstop, dump JSON
python -m yonder.cli search YVR NRT 2026-09-15 -r 2026-09-28 --currency CAD --nonstop --json out.json

# Only specific providers
python -m yonder.cli search YYZ CDG 2026-11-05 --only amadeus,travelpayouts

# Local server
python -m yonder.cli serve --host 127.0.0.1 --port 8787
```

## HTTP API (local)

```
GET  /api/providers
GET  /api/search?origin=YVR&destination=NRT&depart=2026-09-15&return_date=2026-09-28&currency=CAD&mock=true
POST /api/travel-map      # JSON { "visited": ["CA","JP"], "avoid": ["RU"] }
POST /api/saved           # ★ Save itinerary (durable preference)
POST /api/results-clear   # wipe last Escape + Detour snapshots
POST /api/search-cancel   # { "search_id": "…" } — Skip
POST /api/funnel          # light engagement (e.g. field-note expand)
GET  /api/suggest?vibe=food&origin=YVR
GET  /api/place-brief?iata=DPS&prompt=…&vibe=food
GET  /out?u=https://…&click_id=…   # book redirect + attribution
GET  /t/{kind}/{slug}/{id}         # shared trip page
```

`POST /explore` also accepts (from the results criteria bar / Refresh):

| Field | Role |
|-------|------|
| `origin` | Override home for this run |
| `min_stop_days` / `max_stop_days` | Detour stay window |
| `exclude_iatas` | Comma IATAs already on the board (Refresh) |
| `refresh` / `chip_source=refresh` | New candidates; pin-first fallback if empty |
| `search_id` | Correlates progress **Skip** cancel |
| `seed_iatas` | Dataset-pill seeds only (never prior Saves) |

## Architecture

```
yonder/
  types.py              # SearchQuery, FlightOffer (normalized)
  engine.py             # parallel fan-out + merge; one cheapest Escape fare
  adventure.py          # Detour planning; seed_ideas/plan_adventure exclude_iatas
  intent.py             # Escape / Detour / mix decision gate
  encyclopedia.py       # field notes (tone-aware cache + stream API)
  grok.py               # NL parse, invent, place_brief, COL prompts
  daily_costs.py        # COL compare vs Settings bag / cache
  countries.py          # IATA city/country labels, home resolution helpers
  share.py              # shareable trips + QR PNG (segno)
  last_search.py        # last + first-pinned Escape/Detour snapshots (+ clear)
  search_cancel.py      # Skip cancel flags
  attribution.py        # funnel events + outbound URL stamping
  saved.py              # ★ Saves, ranking_from_saves, saved_destination_iatas ban
  web.py                # FastAPI UI + JSON; explore hard-ban + refresh fallback
  cli.py                # Typer CLI (`yonder`)
  static/
    country_map.js      # passport map; stamp order = home
    country_map.css
    vibe_slider.js      # vibe control + dataset-completion pills (no Save city seeds)
    progress.js         # progress overlay + Skip + story phase hints
    iso_numeric_to_a2.json
  templates/
    base.html           # lounge chrome, boarding-pass + field-note CSS
    index.html          # compose, pills, results, collapsible field notes
    trip.html           # standalone share page
    saved.html
    settings.html
  providers/
    base.py
    amadeus.py
    travelpayouts.py
    duffel.py
    serpapi_google.py
    mock.py
```

Every provider implements:

```python
async def search(query: SearchQuery) -> list[FlightOffer]
```

Add a new source by dropping a file in `providers/` and registering it in `providers/__init__.py`.

## Local data files (gitignored)

| File | Purpose |
|------|---------|
| `price_history.db` | Fare signal history / deal labels |
| `saved_itineraries.db` | ★ Saves (pill ranking + hard dest ban list) |
| `shared_trips.db` | QR / shareable trip payloads |
| `daily_costs_cache.db` | Cost-of-stay cache |
| `place_book_cache.db` | Field-note cache (place + tone fingerprint) |
| `attribution.db` | Funnel / outbound click events (local product metrics) |
| `.last_search.json` | Last Escape + Detour panels **and** first-set pins for Refresh fallback |
| `.env` | Your keys (never commit) |

## Dependencies

See `requirements.txt` / `pyproject.toml`. Notable: **FastAPI**, **httpx**, **segno** (QR codes).

## What “complete” would look like later

1. **Price alerts** (email/Telegram when under threshold)  
2. **Flexible date matrix** (±3 days / whole month)  
3. **Airport metro groups** (YVR/SEA, NYC area, etc.)  
4. **Partner deep-links** for booking (Travelpayouts marker, etc.)  
5. More adapters if you get partner access (Skyscanner, Kiwi)  

## Legal / ethics

- Respect each API’s ToS and rate limits.
- Prefer official APIs over scraping.
- Personal use only unless you have commercial agreements.
- Prices are snapshots — always re-check on the provider before booking.
- Stopovers = **two tickets**. Signals aren’t tickets.

## License

MIT — do what you want for personal tooling.
