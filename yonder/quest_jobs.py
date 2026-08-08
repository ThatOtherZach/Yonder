"""In-process job store for eager Quest planning.

Every main search kicks off Quest planning as a background asyncio task;
the page returns fast with Escape while the Quest panel polls
GET /api/quest/status/{job_id} until the job resolves.

Jobs hold plain Python state (quest_panel dict + place_books); the poll
endpoint renders HTML at read time so a real Request is available for
share-link helpers. Entries expire after a TTL so the dict never grows
unbounded — a poll for an expired/unknown job returns None and the client
degrades to the retry card.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_TTL_SECONDS = 15 * 60.0
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _prune_locked(now: float) -> None:
    dead = [k for k, v in _JOBS.items() if now - v.get("created", 0.0) > _TTL_SECONDS]
    for k in dead:
        _JOBS.pop(k, None)


def create_job(*, home_iata: str, vibe: str) -> str:
    """Register a new pending quest job; returns its id."""
    job_id = uuid.uuid4().hex
    now = time.monotonic()
    with _LOCK:
        _prune_locked(now)
        _JOBS[job_id] = {
            "status": "pending",
            "created": now,
            "home_iata": home_iata,
            "vibe": vibe,
            "stage": "reading_vibe",
        }
    return job_id


def set_stage(job_id: str, stage: str) -> None:
    """Update the visible stage label for a pending job.

    Valid stages (in order): reading_vibe → scouting_routes → pricing_flights.
    No-ops on completed/error/unknown jobs so callers never need to guard.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") != "pending":
            return
        job["stage"] = stage


def set_done(job_id: str, *, quest_panel: dict, place_books: dict | None = None, ok: bool = True) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(
            status="done",
            ok=ok,
            quest_panel=quest_panel,
            place_books=place_books or {},
        )


def set_error(job_id: str, error_text: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(status="error", ok=False, error_text=error_text)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Snapshot of the job state, or None when unknown/expired."""
    now = time.monotonic()
    with _LOCK:
        _prune_locked(now)
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def clear_all() -> None:
    """Test helper — drop all jobs."""
    with _LOCK:
        _JOBS.clear()
