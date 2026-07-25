# Yonder

**Go yonder.** A vibey personal travel planner — multi-provider flight fares, adventure stopovers, saved itineraries, and deal history. Built for explorers who want signals, not sales pitches.

Not a booking engine. Not for commercial resale. Scan free/cheap pricing APIs in parallel, compare, then book where you trust.

## How pricing works (honest)

Free APIs never perfectly match the Google Flights page you open 10 seconds later. This app treats them as **signals** and **historical data**:

- Prices display as **`~C$420`** (approx / last-seen)
- Every result is **logged** to local SQLite `price_history.db` — you build a personal dataset over time
- **Deal labels** (great / good / ok / high) compare a fare to *your* history for that route
- **Google Flights** + **Kayak** links for verification (source of truth for booking)
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

# Or local web UI
python -m yonder.cli serve
# → http://127.0.0.1:8787
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

### 5. Grok (xAI) — natural language + ranking

1. https://console.x.ai/ → create API key
2. `XAI_API_KEY=...`
3. Optional: `XAI_MODEL=grok-4.5`

Grok does **not** invent fares. It:
1. Parses plain English → origin/dest/dates
2. Runs your real providers
3. Writes a short pick + tradeoffs over the merged results

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
```

## HTTP API (local)

```
GET /api/providers
GET /api/search?origin=YVR&destination=NRT&depart=2026-09-15&return_date=2026-09-28&currency=CAD&mock=true
```

## Architecture

```
yonder/
  types.py          # SearchQuery, FlightOffer (normalized)
  engine.py         # parallel fan-out + merge/sort
  adventure.py      # stopover / multi-leg ideas
  providers/
    base.py         # adapter interface
    amadeus.py
    travelpayouts.py
    duffel.py
    serpapi_google.py
    mock.py
  cli.py            # Typer CLI (`yonder`)
  web.py            # FastAPI UI + JSON
```

Every provider implements:

```python
async def search(query: SearchQuery) -> list[FlightOffer]
```

Add a new source by dropping a file in `providers/` and registering it in `providers/__init__.py`.

## What “complete” would look like later

If you want to grow this past personal scanning:

1. **SQLite cache** of historical prices per route/date
2. **Price alerts** (email/Telegram when under threshold)
3. **Flexible date matrix** (±3 days / whole month)
4. **Airport metro groups** (YVR/SEA, NYC area, etc.)
5. **Partner deep-links** for booking (Travelpayouts marker, etc.)
6. More adapters if you get partner access (Skyscanner, Kiwi)

## Legal / ethics

- Respect each API’s ToS and rate limits.
- Prefer official APIs over scraping.
- Personal use only unless you have commercial agreements.
- Prices are snapshots — always re-check on the provider before booking.

## License

MIT — do what you want for personal tooling.
