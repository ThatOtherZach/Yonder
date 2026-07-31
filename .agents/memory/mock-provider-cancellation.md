---
name: Mock provider cancellation fallback
description: Why mock searches return zero results when an AI key is configured, and the correct provider/forced-fare setup
---

The `AIDemoProvider` called Grok to generate demo fares whenever an xAI key was present.
`asyncio.CancelledError` is a `BaseException` (Python 3.8+), so the `except Exception` fallback
inside that provider did NOT catch cancellation from the engine's outer `asyncio.wait_for` —
the error surfaced as an empty-message `TimeoutError`, showing up as `"mock: "` in leg pricing
errors and zero itineraries.

**Fix applied:** `yonder/providers/__init__.py` now instantiates `MockProvider` directly when
`include_mock=True` — no Grok call, no CancelledError, instant seeded fares.
`AIDemoProvider` remains in `mock.py` but is no longer wired in.

**Second blocker:** `mock_forced = mock and not mock_requested` evaluated to `False` when
`testing=True` AND the user explicitly checked Test Data, so `_mark_missing_fares_*` never
injected demo fares even when real providers returned nothing.
**Fix applied:** `mock_forced = mock` in both the escape and detour paths of `explore_run`
(`yonder/web.py`). Demo fares are now always the last-resort fallback whenever `mock=True`.

**Why:** The pricing engine wraps provider searches in `asyncio.wait_for`; a slow Grok call gets
cancelled mid-await and the cancellation escapes the fallback handler. The `mock_requested` /
`mock_forced` distinction was designed to differ "user chose Test Data" from "no providers
configured", but in practice both cases need the demo-fare fallback.

**How to apply:** Keep `MockProvider` as the mock provider. If AI demo fares are ever
re-introduced, the Grok await inside must be guarded by an internal timeout shorter than the
engine's budget. Tests exercising mock pricing should monkeypatch `get_settings().xai_api_key`
to blank — clearing env vars alone is not enough (the key comes from `.env`/user prefs already
merged into the cached Settings singleton).
