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
import time
import uuid
from typing import Any

from yonder.db import get_conn


def _mock_mode() -> bool:
    return bool((os.environ.get("MOCK") or "").strip())


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
    """Append one vote to result_feedback. Returns the row id.

    Returns "" (empty string) when this (session_hash, vibe, dest_iata,
    direction) combo already voted — one up and one down vote max per session
    per destination, so vote-stuffing can't skew the archive.
    Returns None in MOCK mode, on error, or for an invalid direction.
    """
    if _mock_mode():
        return None
    direction = (direction or "").strip().lower()
    if direction not in ("up", "down"):
        return None
    row_id = uuid.uuid4().hex
    try:
        with get_conn() as conn:
            # Dedup enforced by the ux_rf_vote unique index: ON CONFLICT DO
            # NOTHING is atomic, so concurrent duplicate votes can't both land.
            cur = conn.execute(
                """
                INSERT INTO result_feedback (id, session_hash, vibe, dest_iata, query, direction, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
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
            if cur.rowcount == 0:
                return ""
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
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, answer_json FROM vibe_questions WHERE vibe = %s AND query_norm = %s",
                (v, q),
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            conn.execute(
                """
                INSERT INTO vibe_questions (id, vibe, query_norm, created_at)
                VALUES (%s, %s, %s, %s)
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
        with get_conn() as conn:
            conn.execute(
                "UPDATE vibe_questions SET answer_json = %s, answer_at = %s WHERE id = %s",
                (json.dumps(answer), time.time(), question_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_suggestions_for_vibe(
    vibe: str, *, limit: int = 20, lang: str | None = None
) -> list[dict[str, Any]]:
    """Return answered vibe questions for a given vibe, newest first.

    lang (default English) filters out suggestions written in another
    language — legacy rows without a stored lang fall back to detecting the
    question text's language.
    """
    from yonder.lang import detect_lang

    want = (lang or "en").strip().lower() or "en"
    v = _norm_vibe(vibe)
    lim = max(1, min(100, int(limit or 20)))
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, vibe, query_norm, answer_json, created_at, answer_at
                FROM vibe_questions
                WHERE vibe = %s AND answer_json IS NOT NULL
                ORDER BY answer_at DESC
                LIMIT %s
                """,
                (v, lim),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            answer = json.loads(r["answer_json"]) if r["answer_json"] else None
            row_lang = (
                str((answer or {}).get("lang") or "").strip().lower()
                or detect_lang(r["query_norm"])
            )
            if row_lang != want:
                continue
            out.append(
                {
                    "id": r["id"],
                    "vibe": r["vibe"],
                    "query": r["query_norm"],
                    "answer": answer,
                    "created_at": r["created_at"],
                    "answer_at": r["answer_at"],
                }
            )
        return out
    except Exception:
        return []


def feedback_stats() -> dict[str, Any]:
    """Quick aggregate counts — useful for admin/debug."""
    try:
        with get_conn() as conn:
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
