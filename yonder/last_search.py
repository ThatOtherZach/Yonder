"""Persist last Escape / Detour search payloads on disk (local personal use).

Survives mode switches and page reloads until a new search overwrites that mode.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yonder.config import ROOT

STORE_PATH = ROOT / ".last_search.json"
_LOCK = threading.Lock()
MODES = ("escape", "detour")


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


def _read_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {}
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_store(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(STORE_PATH)


def save_last(
    mode: str,
    payload: dict[str, Any],
    *,
    pin_first: bool = False,
) -> None:
    """Save a successful search snapshot for mode ('escape' | 'detour').

    pin_first: also store as the session's first result set for this mode
    (used when Refresh finds nothing new and should roll back).
    """
    m = (mode or "").strip().lower()
    if m not in MODES:
        return
    snap = {k: _dump(v) for k, v in payload.items()}
    snap["saved_at"] = _now()
    first_key = f"first_{m}"
    with _LOCK:
        store = _read_store()
        store[m] = snap
        if pin_first and first_key not in store:
            store[first_key] = snap
        try:
            _write_store(store)
        except Exception:
            pass


def load_last(mode: str) -> dict[str, Any] | None:
    m = (mode or "").strip().lower()
    if m not in MODES:
        return None
    with _LOCK:
        store = _read_store()
    raw = store.get(m)
    return raw if isinstance(raw, dict) else None


def load_first(mode: str) -> dict[str, Any] | None:
    """Original result set for this mode (first successful search after Clear)."""
    m = (mode or "").strip().lower()
    if m not in MODES:
        return None
    with _LOCK:
        store = _read_store()
    raw = store.get(f"first_{m}")
    return raw if isinstance(raw, dict) else None


def clear_last(mode: str | None = None) -> None:
    """Drop last-search snapshot(s) and first-set pins. mode=None clears all."""
    with _LOCK:
        if mode is None:
            try:
                if STORE_PATH.exists():
                    STORE_PATH.unlink()
            except Exception:
                pass
            return
        m = (mode or "").strip().lower()
        if m not in MODES:
            return
        store = _read_store()
        store.pop(m, None)
        store.pop(f"first_{m}", None)
        try:
            if store:
                _write_store(store)
            elif STORE_PATH.exists():
                STORE_PATH.unlink()
        except Exception:
            pass


def hydrate_escape(snap: dict[str, Any]) -> dict[str, Any]:
    """Rebuild template context pieces for Escape from a snapshot."""
    from yonder.grok import GrokAnalysis, ParsedTrip
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
    try:
        if snap.get("analysis"):
            out["analysis"] = GrokAnalysis.model_validate(snap["analysis"])
    except Exception:
        out["analysis"] = None
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
