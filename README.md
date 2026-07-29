# Yonder

**Go yonder.** A vibey personal travel planner — multi-provider flight signals, mandatory trip vibes, passport map stamps, Detour stopovers, boarding-pass results with shareable QR codes, place notes, and deal history.

Not a booking engine. Not for commercial resale. Scan free/cheap pricing APIs in parallel, compare, then book where you trust.

## Modes

| Mode | What it does |
|------|----------------|
| **Escape** | Plain-English point A→B. Grok parses the trip (one traveler, economy). Providers return **one cheapest fare signal** for that destination. |
| **Detour** | Multi-day stopovers or open getaways (“somewhere new”, food/COL/safe). Grok invents candidate cities; APIs price legs. **One package per destination city**, up to five cities, cheapest first. |

Both modes share **one compose card** with an in-card **Escape | Detour** toggle (no full page reload):

1. **Prompt** — free text  
2. **Vibe slider** (required) — rainbow hue → named vibe; **random vibe on each fresh page load** (kept only after a live search)  
3. **Go button** — beside the text box, tinted to the current vibe (**Plan Escape** / **Find Detour**)  
4. **Passport map** — click countries: open → visited → avoid (max 10 avoid). Autosaves. **Clear map** appears when more than one country is stamped  

### Architecture in one line

**Grok translates intent → structured package → providers price without Grok → UI shows boarding passes.**

Optional COL enrichment: Grok estimates a lean day bag (hotel + food + transit + culture) per stop; scored against your **Settings** day bag + over-budget % band. If the bag is all zeros, budget is omitted from the Grok prompt and ranking is off.

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
2. Else primary airport of the **first passport-map country**  
3. Else country implied by **default currency** (CAD→YVR, USD→JFK, …)  
4. Else **USA / JFK**

## Web UI

| Path | Purpose |
|------|---------|
| `/` or `/?mode=escape` | Unified compose — Escape active |
| `/?mode=detour` | Unified compose — Detour active |
| `/saved` | Saved boarding passes + QR share |
| `/settings` | Keys, home airport, day bag, Detour knobs, Test Data |
| `/t/{kind}/{slug}/{id}` | **Shareable trip page** (QR target; slug embeds route/dates/fare) |
| `/t/{id}` | Short share form (same payload) |
| `GET /adventure` | Redirects to `/?mode=detour` |
| `POST /ask` | Escape search |
| `POST /adventure` | Detour planning |
| `POST /api/travel-map` | Autosave visited/avoid from the passport map |
| `POST /api/saved` | Save itinerary JSON |

### Compose (shared Escape / Detour)

- One card: **Escape | Detour** toggle, **textarea** + vibe-colored **Go**  
- Under the text: **rainbow hue slider** + vibe name  
- Vibe is **required**; random on each cold load; locked only after a live search re-render  
- Detour needs **depart** date. Stop length / option count live under **Settings → Detour defaults** (defaults: 3–5 days, up to **5** cities)  
- **Escape**: one cheapest fare for the destination  
- **Detour**: one package per destination city (≤5 cities), cheapest first  
- **Hard ~30s budget** for Escape/Detour (progress UI ~32s). Grok invent is capped so seed fallback still has time for fares  

### Passport map

- Zoom / pan (wheel, +/−, ↺)  
- Click cycle: **open → visited → avoid → clear**  
- Avoid list capped at **10**  
- **Clear map** button when **more than one** country is stamped (visited + avoid)  
- Open countries can tint with the active vibe color  

### Cost of living (Detour)

- Grok (or cache/fallback) estimates **hotel + food + transit + culture** per stop (sum = daily)  
- Compared to your Settings day bag total with **over-budget %** band (under / within / over)  
- If all bag fields are **0**, COL estimates still show but **no under/over ranking**, and the bag is **not** sent in the Grok prompt  

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
POST /api/travel-map   # JSON { "visited": ["CA","JP"], "avoid": ["RU"] }
POST /api/saved        # save itinerary
GET  /t/{kind}/{slug}/{id}  # shared trip page
```

## Architecture

```
yonder/
  types.py              # SearchQuery, FlightOffer (normalized)
  engine.py             # parallel fan-out + merge; one cheapest Escape fare
  adventure.py          # Detour planning; one package per destination city
  encyclopedia.py       # short place briefs (field notes)
  grok.py               # NL parse, detour invent, COL batch prompts
  daily_costs.py        # COL compare vs Settings bag / cache
  share.py              # shareable trips + QR PNG generation (segno)
  last_search.py        # persist last Escape + Detour snapshots
  web.py                # FastAPI UI + JSON
  cli.py                # Typer CLI (`yonder`)
  static/
    country_map.js      # passport map + clear-map
    country_map.css
    vibe_slider.js      # mandatory hue vibe control
    progress.js         # search progress overlay
    iso_numeric_to_a2.json
  templates/
    base.html           # lounge chrome, boarding-pass + QR CSS
    index.html          # unified Escape/Detour compose + results
    trip.html           # standalone share page
    saved.html
    settings.html
  providers/
    base.py             # adapter interface
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
| `saved_itineraries.db` | Saved trips |
| `shared_trips.db` | QR / shareable trip payloads |
| `daily_costs_cache.db` | Cost-of-stay cache |
| `.last_search.json` | Last Escape + Detour result panels |
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
