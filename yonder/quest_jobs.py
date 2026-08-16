"""Postgres-backed job store for eager Quest planning.

Every main search kicks off Quest planning as a background asyncio task;
the page returns fast with Escape while the Quest panel polls
GET /api/quest/status/{job_id} until the job resolves.

Jobs are stored in Postgres (NOT process memory) because production runs
multiple gunicorn workers: the worker that runs the job is almost never
the worker that answers the poll. Job state (quest_panel dict +
place_books) is pickled so arbitrary plain-Python payloads survive the
round-trip; the poll endpoint renders HTML at read time so a real Request
is available for share-link helpers. Rows expire after a TTL — a poll for
an expired/unknown job returns None and the client degrades to the retry
card.
"""

from __future__ import annotations

import pickle
import time
import uuid
from typing import Any

import psycopg2

from .db import get_conn

_TTL_SECONDS = 15 * 60.0


def _prune(conn: Any, now: float) -> None:
    conn.execute(
        "DELETE FROM quest_jobs WHERE created_at < %s", (now - _TTL_SECONDS,)
    )


def create_job(*, home_iata: str, vibe: str) -> str:
    """Register a new pending quest job; returns its id."""
    job_id = uuid.uuid4().hex
    now = time.time()
    with get_conn() as conn:
        _prune(conn, now)
        conn.execute(
            """
            INSERT INTO quest_jobs (job_id, status, stage, home_iata, vibe, created_at)
            VALUES (%s, 'pending', 'reading_vibe', %s, %s, %s)
            """,
            (job_id, home_iata, vibe, now),
        )
    return job_id


def set_stage(job_id: str, stage: str) -> None:
    """Update the visible stage label for a pending job.

    Valid stages (in order): reading_vibe → scouting_routes → pricing_flights.
    No-ops on completed/error/unknown jobs so callers never need to guard.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE quest_jobs SET stage = %s WHERE job_id = %s AND status = 'pending'",
            (stage, job_id),
        )


def set_done(
    job_id: str,
    *,
    quest_panel: dict,
    place_books: dict | None = None,
    ok: bool = True,
    detour_candidates: list | None = None,
    detour_match: dict | None = None,
) -> None:
    payload = pickle.dumps(
        {
            "quest_panel": quest_panel,
            "place_books": place_books or {},
            "detour_candidates": detour_candidates or [],
            # Match context for the stored candidate pool: the current
            # query's depart date + relevant destination IATAs, so the
            # status endpoint can filter by route + date proximity.
            "detour_match": detour_match or {},
        },
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE quest_jobs
               SET status = 'done', ok = %s, payload = %s, error_text = NULL
             WHERE job_id = %s
            """,
            (bool(ok), psycopg2.Binary(payload), job_id),
        )


def set_error(job_id: str, error_text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE quest_jobs
               SET status = 'error', ok = FALSE, error_text = %s, payload = NULL
             WHERE job_id = %s
            """,
            (error_text, job_id),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    """Snapshot of the job state, or None when unknown/expired."""
    now = time.time()
    with get_conn() as conn:
        _prune(conn, now)
        cur = conn.execute(
            "SELECT * FROM quest_jobs WHERE job_id = %s", (job_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    job: dict[str, Any] = {
        "status": row["status"],
        "stage": row["stage"],
        "home_iata": row["home_iata"],
        "vibe": row["vibe"],
        "ok": row["ok"],
        "error_text": row["error_text"],
        "created": row["created_at"],
    }
    raw = row.get("payload") if hasattr(row, "get") else row["payload"]
    if raw is not None:
        try:
            payload = pickle.loads(bytes(raw))
            job["quest_panel"] = payload.get("quest_panel") or {}
            job["place_books"] = payload.get("place_books") or {}
            job["detour_candidates"] = payload.get("detour_candidates") or []
            job["detour_match"] = payload.get("detour_match") or {}
        except Exception:
            # Corrupted/incompatible payload: surface as an error so the
            # client shows the retry card instead of an empty "done" panel.
            job["status"] = "error"
            job["ok"] = False
            job["error_text"] = "Quest result couldn't be loaded — try again."
    return job


def clear_all() -> None:
    """Test helper — drop all jobs."""
    with get_conn() as conn:
        conn.execute("DELETE FROM quest_jobs")
