# Yonder

A vibe-first personal travel planner. Type a trip in plain English, pick a vibe, and get fare signals across multiple flight providers. Supports two modes: **Escape** (A→B) and **Detour** (multi-city stopovers / open getaways).

## How to run

The app is configured as the **"Start application"** workflow and starts automatically.

Manually: `python -m uvicorn yonder.web:app --host 0.0.0.0 --port 5000`

Open the preview pane to see the UI.

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
