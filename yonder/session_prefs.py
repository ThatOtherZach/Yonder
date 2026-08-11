"""Per-browser-session visitor preferences (Postgres, keyed by yv_sess).

Every personal preference — home airport, currency, visited/avoid map,
daily budget, stop-length prefs, return window, and BYOM endpoint — is
private to one browser session.  Server/provider configuration stays in
.env / environment variables (yonder.config Settings) and is shared.

A brand-new session has no rows and gets SESSION_PREF_DEFAULTS: no home
airport, USD currency, empty map — never another visitor's data.
"""
from __future__ import annotations

import time

from yonder.db import get_conn
from yonder.user_prefs import PREF_DEFAULTS as _BASE_PREF_DEFAULTS

# All per-session keys with their factory defaults for brand-new sessions.
SESSION_PREF_DEFAULTS: dict[str, str] = {
    **_BASE_PREF_DEFAULTS,
    # Personal identity/locale prefs (previously global .env values)
    "home_iata": "",
    "default_currency": "USD",
    # BYOM — Bring Your Own Model is a personal per-browser setting
    "byom_base_url": "",
    "byom_api_key": "",
    "byom_model": "",
}


def _norm_sess(session_id: str | None) -> str:
    return (session_id or "").strip()[:64]


def get_session_prefs(session_id: str | None) -> dict[str, str]:
    """All prefs for *session_id* with defaults filled in.

    Empty/blank session id → pure defaults (never another visitor's data).
    """
    result = dict(SESSION_PREF_DEFAULTS)
    sid = _norm_sess(session_id)
    if not sid:
        return result
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM session_prefs WHERE session_id = %s",
                (sid,),
            ).fetchall()
        stored = {
            r["key"]: r["value"] for r in rows if r["key"] in SESSION_PREF_DEFAULTS
        }
        stored = _migrate_retired_region_tiles(sid, stored)
        result.update(stored)
    except Exception:
        # DB hiccup → defaults; prefs reads must never take a page down.
        pass
    return result


def _migrate_retired_region_tiles(sid: str, prefs: dict[str, str]) -> dict[str, str]:
    """Collapse retired region tiles to country marks (same rule as user_prefs)."""
    try:
        from yonder.tiles import collapse_retired_region_prefs

        merged = {**{k: "" for k in ("visited_tiles", "avoid_tiles",
                                     "visited_countries", "avoid_countries")}, **prefs}
        changed = collapse_retired_region_prefs(merged)
        if changed:
            _write(sid, changed)
            prefs.update(changed)
    except Exception:
        pass
    return prefs


def set_session_prefs(session_id: str | None, updates: dict[str, str]) -> None:
    """Persist one or more pref values for this session (whitelisted keys only)."""
    sid = _norm_sess(session_id)
    valid = {k: str(v) for k, v in updates.items() if k in SESSION_PREF_DEFAULTS}
    if not sid or not valid:
        return
    _write(sid, valid)


def _write(sid: str, values: dict[str, str]) -> None:
    now = time.time()
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO session_prefs (session_id, key, value, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id, key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """,
            [(sid, k, v, now) for k, v in values.items()],
        )
        conn.commit()
