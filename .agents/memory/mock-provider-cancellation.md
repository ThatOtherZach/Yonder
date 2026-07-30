---
name: Mock provider cancellation fallback
description: Why "mock: " empty errors appear in leg pricing and how the AI demo provider must guard against CancelledError
---

The AI demo fare provider tries a live Grok call when an xAI key is configured, falling back to seeded mock data on failure. `asyncio.CancelledError` is a `BaseException` (Python 3.8+), so a plain `except Exception` fallback does NOT catch cancellation from an outer `asyncio.wait_for` timeout — the error surfaces as an empty-message `TimeoutError`, showing up as `"mock: "` in leg pricing errors and zero itineraries.

**Why:** The pricing engine wraps provider searches in `asyncio.wait_for`; a slow Grok call gets cancelled mid-await and the cancellation escapes the fallback handler.

**How to apply:** Any await inside the demo provider's Grok path must be guarded by its own internal `asyncio.wait_for` (shorter than the engine's timeout) so failures become catchable `TimeoutError`s. Tests exercising mock pricing should clear `XAI_API_KEY` (and `MOCK`) — but clearing env vars is NOT enough: the key may come from `.env`/user prefs already merged into the cached Settings singleton. Also blank `get_settings().xai_api_key` via monkeypatch, or every mock-priced leg times out and pipelines return zero itineraries.
