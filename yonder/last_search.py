"""Persist last Escape / Detour search payloads per browser session (Postgres).

Snapshots are keyed by the ``yv_sess`` cookie value so each browser session
only ever sees its own last search. An empty/missing session_id is a no-op on
write and returns None on read — never another session's data.

Survives mode switches and page reloads until a new search overwrites that mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from yonder.config import ROOT
from yonder.db import get_conn

MODES = ("escape", "detour")

# Legacy single-user store — deleted on startup so no stale shared data lingers.
_LEGACY_STORE_PATH = ROOT / ".last_search.json"


def _remove_legacy_store() -> None:
    for p in (_LEGACY_STORE_PATH, _LEGACY_STORE_PATH.with_suffix(".json.tmp")):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


_remove_legacy_store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return obj


def _norm_sess(session_id: str | None) -> str:
    return (session_id or "").strip()[:64]


def _norm_mode(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    return m if m in MODES else ""


def _get_payload(sess: str, mode_key: str) -> dict[str, Any] | None:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT payload FROM last_search WHERE session_id = %s AND mode = %s",
                (sess, mode_key),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    raw = row["payload"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    return raw if isinstance(raw, dict) else None


def save_last(
    mode: str,
    payload: dict[str, Any],
    *,
    session_id: str,
    pin_first: bool = False,
) -> None:
    """Save a successful search snapshot for mode ('escape' | 'detour').

    pin_first: also store as the session's first result set for this mode
    (used when Refresh finds nothing new and should roll back).
    """
    m = _norm_mode(mode)
    sess = _norm_sess(session_id)
    if not m or not sess:
        return
    snap = {k: _dump(v) for k, v in payload.items()}
    snap["saved_at"] = _now()
    blob = json.dumps(snap, ensure_ascii=False)
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO last_search (session_id, mode, payload, saved_at)
                VALUES (%s, %s, %s::jsonb, now())
                ON CONFLICT (session_id, mode)
                DO UPDATE SET payload = EXCLUDED.payload, saved_at = now()
                """,
                (sess, m, blob),
            )
            if pin_first:
                conn.execute(
                    """
                    INSERT INTO last_search (session_id, mode, payload, saved_at)
                    VALUES (%s, %s, %s::jsonb, now())
                    ON CONFLICT (session_id, mode) DO NOTHING
                    """,
                    (sess, f"first_{m}", blob),
                )
    except Exception:
        pass


def load_last(mode: str, *, session_id: str) -> dict[str, Any] | None:
    m = _norm_mode(mode)
    sess = _norm_sess(session_id)
    if not m or not sess:
        return None
    return _get_payload(sess, m)


def load_first(mode: str, *, session_id: str) -> dict[str, Any] | None:
    """Original result set for this mode (first successful search after Clear)."""
    m = _norm_mode(mode)
    sess = _norm_sess(session_id)
    if not m or not sess:
        return None
    return _get_payload(sess, f"first_{m}")


def clear_last(mode: str | None = None, *, session_id: str) -> None:
    """Drop this session's snapshot(s) and first-set pins. mode=None clears all."""
    sess = _norm_sess(session_id)
    if not sess:
        return
    if mode is None:
        try:
            with get_conn() as conn:
                conn.execute(
                    "DELETE FROM last_search WHERE session_id = %s", (sess,)
                )
        except Exception:
            pass
        return
    m = _norm_mode(mode)
    if not m:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM last_search WHERE session_id = %s AND mode IN (%s, %s)",
                (sess, m, f"first_{m}"),
            )
    except Exception:
        pass


def hydrate_escape(snap: dict[str, Any]) -> dict[str, Any]:
    """Rebuild template context pieces for Escape from a snapshot."""
    from yonder.grok import ParsedTrip
    from yonder.types import UnifiedSearchResult

    out: dict[str, Any] = {
        "ask": snap.get("ask") or "",
        "form": snap.get("form") or {},
        "error": None,
        "dest_theme": snap.get("dest_theme"),
        "place_book": snap.get("place_book"),
        "place_books": {},
        "trip_meta": None,
        "result": None,
        "parsed": None,
        "analysis": None,
    }
    try:
        if snap.get("result"):
            out["result"] = UnifiedSearchResult.model_validate(snap["result"])
    except Exception:
        out["result"] = None
    try:
        if snap.get("parsed"):
            out["parsed"] = ParsedTrip.model_validate(snap["parsed"])
    except Exception:
        out["parsed"] = None
    # analysis is legacy (analyze_results removed) — always None
    return out


def hydrate_detour(snap: dict[str, Any]) -> dict[str, Any]:
    from yonder.adventure import AdventureResult

    out: dict[str, Any] = {
        "form": snap.get("form") or {},
        "trip_meta": snap.get("trip_meta"),
        "place_book": None,
        "place_books": snap.get("place_books") or {},
        "ask": "",
        "parsed": None,
        "analysis": None,
        "dest_theme": None,
        "error": None,
        "result": None,
    }
    try:
        if snap.get("result"):
            out["result"] = AdventureResult.model_validate(snap["result"])
    except Exception:
        out["result"] = None
    return out
