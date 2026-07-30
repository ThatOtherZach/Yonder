"""Result feedback — thumbs up / down on search results.

Two tables:

  result_feedback   — full history log; every vote, timestamped.
  vibe_questions    — deduplicated vibe+query pairs where the user thumbed
                      down; answered asynchronously by AI.

MOCK-mode guard: writes are no-ops when MOCK env var is set, matching the
vibe_signals convention so demo fares never pollute the archive.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from typing import Any

from yonder.config import ROOT

DB_PATH = ROOT / "feedback.db"


def _mock_mode() -> bool:
    return bool((os.environ.get("MOCK") or "").strip())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS result_feedback (
            id          TEXT PRIMARY KEY,
            session_hash TEXT,
            vibe        TEXT NOT NULL DEFAULT '',
            dest_iata   TEXT NOT NULL DEFAULT '',
            query       TEXT NOT NULL DEFAULT '',
            direction   TEXT NOT NULL CHECK(direction IN ('up','down')),
            created_at  REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rf_vibe_dest"
        " ON result_feedback(vibe, dest_iata)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rf_created"
        " ON result_feedback(created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vibe_questions (
            id              TEXT PRIMARY KEY,
            vibe            TEXT NOT NULL DEFAULT '',
            query_norm      TEXT NOT NULL DEFAULT '',
            answer_json     TEXT,
            created_at      REAL NOT NULL,
            answer_at       REAL,
            UNIQUE(vibe, query_norm)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vq_vibe"
        " ON vibe_questions(vibe, answer_at DESC)"
    )
    conn.commit()
    return conn


def _norm_query(q: str) -> str:
    return " ".join((q or "").lower().split())[:200]


def _norm_vibe(v: str) -> str:
    return (v or "").strip().lower()[:40] or "adventure"


def _norm_iata(code: str | None) -> str:
    c = (code or "").strip().upper()
    return c if len(c) == 3 and c.isalpha() else ""


def record_feedback(
    *,
    direction: str,
    vibe: str | None,
    dest_iata: str | None,
    query: str | None = None,
    session_hash: str | None = None,
) -> str | None:
    """Append one vote to result_feedback. Returns the row id (or None in MOCK mode)."""
    if _mock_mode():
        return None
    direction = (direction or "").strip().lower()
    if direction not in ("up", "down"):
        return None
    row_id = uuid.uuid4().hex
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO result_feedback (id, session_hash, vibe, dest_iata, query, direction, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    (session_hash or "")[:32] or None,
                    _norm_vibe(vibe),
                    _norm_iata(dest_iata),
                    _norm_query(query or ""),
                    direction,
                    time.time(),
                ),
            )
            conn.commit()
    except Exception:
        return None
    return row_id


def upsert_vibe_question(
    *,
    vibe: str | None,
    query: str | None,
) -> tuple[str, bool]:
    """Insert or ignore a vibe+query pair. Returns (id, is_new).

    is_new=True means the row was just created and needs an AI answer.
    Always returns ("", False) in MOCK mode.
    """
    if _mock_mode():
        return "", False
    v = _norm_vibe(vibe)
    q = _norm_query(query or "")
    if not q:
        return "", False
    row_id = uuid.uuid4().hex
    try:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT id, answer_json FROM vibe_questions WHERE vibe = ? AND query_norm = ?",
                (v, q),
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            conn.execute(
                """
                INSERT INTO vibe_questions (id, vibe, query_norm, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (row_id, v, q, time.time()),
            )
            conn.commit()
    except Exception:
        return "", False
    return row_id, True


def save_vibe_answer(question_id: str, answer: dict[str, Any]) -> bool:
    """Persist the AI-generated answer for a vibe question row."""
    if not question_id:
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE vibe_questions SET answer_json = ?, answer_at = ? WHERE id = ?",
                (json.dumps(answer), time.time(), question_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_suggestions_for_vibe(vibe: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return answered vibe questions for a given vibe, newest first."""
    v = _norm_vibe(vibe)
    lim = max(1, min(100, int(limit or 20)))
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, vibe, query_norm, answer_json, created_at, answer_at
                FROM vibe_questions
                WHERE vibe = ? AND answer_json IS NOT NULL
                ORDER BY answer_at DESC
                LIMIT ?
                """,
                (v, lim),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "vibe": r["vibe"],
                "query": r["query_norm"],
                "answer": json.loads(r["answer_json"]) if r["answer_json"] else None,
                "created_at": r["created_at"],
                "answer_at": r["answer_at"],
            }
            for r in rows
        ]
    except Exception:
        return []


def feedback_stats() -> dict[str, Any]:
    """Quick aggregate counts — useful for admin/debug."""
    try:
        with _connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM result_feedback").fetchone()["c"]
            ups = conn.execute(
                "SELECT COUNT(*) AS c FROM result_feedback WHERE direction='up'"
            ).fetchone()["c"]
            downs = conn.execute(
                "SELECT COUNT(*) AS c FROM result_feedback WHERE direction='down'"
            ).fetchone()["c"]
            questions = conn.execute("SELECT COUNT(*) AS c FROM vibe_questions").fetchone()["c"]
            answered = conn.execute(
                "SELECT COUNT(*) AS c FROM vibe_questions WHERE answer_json IS NOT NULL"
            ).fetchone()["c"]
        return {
            "total_votes": int(total),
            "up": int(ups),
            "down": int(downs),
            "vibe_questions": int(questions),
            "answered": int(answered),
        }
    except Exception:
        return {}
