---
name: Per-browser session preferences
description: How visitor preferences are scoped per yv_sess browser session vs global server config
---

All personal preferences (home airport, currency, visited/avoid map+tiles, daily budget, detour stop days, return days, BYOM endpoint/key/model) live in the Postgres `session_prefs` table keyed by the `yv_sess` cookie, via `yonder/session_prefs.py`. Request paths get them through `web._session_settings(request)` → `config.apply_session_prefs(base, sid)`, which ALWAYS overlays every personal field (blank session rows mean defaults — never env/legacy values). Server/provider config stays in `.env` and is only writable from Settings POST when `settings.testing` is true.

**Why:** public site — one visitor's settings must never leak to another; quests are the only shared records.

**How to apply:**
- New request-path consumers of personal prefs must use `_session_settings(request)` (or take a Settings arg), never `get_settings()`/`reload_settings()` directly.
- Cookie-less requests (tests/CLI) fall back to legacy global settings; many tests monkeypatch `web.reload_settings` and rely on this.
- The `yv_sess` cookie is `Secure` — TestClient needs `base_url="https://testserver"` for cookie round-trips.
- Tests backing session prefs should monkeypatch `yonder.session_prefs.get/set_session_prefs` (in-memory dict) or a throwaway PG schema — never the real table.
- Pre-existing baseline test failures (unrelated): og_image 404, legacy_adventure_post, legacy_model_source, saved_escape_booking, shared_trip_aviasales (7 total as of Aug 2026).
