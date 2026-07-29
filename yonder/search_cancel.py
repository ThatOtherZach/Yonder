"""In-process cancel flags for long Escape/Detour runs (Skip button)."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# search_id → monotonic time when cancel was requested
_CANCEL: dict[str, float] = {}
# Drop stale ids (no client cleanup) after this many seconds
_TTL = 600.0


def _purge_locked(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    dead = [k for k, t0 in _CANCEL.items() if now - t0 > _TTL]
    for k in dead:
        _CANCEL.pop(k, None)


def request_cancel(search_id: str | None) -> bool:
    """Mark a search as skipped. Returns True if id was non-empty."""
    sid = (search_id or "").strip()
    if not sid or len(sid) > 80:
        return False
    with _lock:
        _purge_locked()
        _CANCEL[sid] = time.monotonic()
    return True


def is_cancelled(search_id: str | None) -> bool:
    sid = (search_id or "").strip()
    if not sid:
        return False
    with _lock:
        _purge_locked()
        return sid in _CANCEL


def clear(search_id: str | None) -> None:
    sid = (search_id or "").strip()
    if not sid:
        return
    with _lock:
        _CANCEL.pop(sid, None)
