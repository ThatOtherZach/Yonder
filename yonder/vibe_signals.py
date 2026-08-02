"""Vibe-learning signal store — every search teaches the pills.

Tiered signals (per-installation, no accounts):
  4 saved    — ★ Save (near-conversion)
  3 engaged  — clicked More…, an outbound link, QR/share, or a chip pill
  2 reviewed — visited /saved (soft re-confirmation of saved destinations)
  1 searched — search ran and returned results, nothing further

`dest_vibe_scores` is a weighted aggregate recomputed lazily (≤ once/hour):
  score = SUM(signal_strength * recency_weight) / search_count

Test-mode guard: when the MOCK environment variable is set, every write in
this module is a no-op so demo fares never pollute the signal database, and
every read returns empty so learned rankings never bias demo results.
Callers with a per-request demo flag (Test Data switch in dev) pass
``demo=True`` to the read helpers for the same bypass.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any

from yonder.db import get_conn

# Signal strengths
SEARCHED = 1
REVIEWED = 2
ENGAGED = 3
SAVED = 4

RECOMPUTE_INTERVAL_S = 3600.0  # lazy: at most once per hour
RECENCY_HALFLIFE_DAYS = 90.0


def _mock_mode() -> bool:
    """All writes are no-ops in test/demo data mode."""
    return bool((os.environ.get("MOCK") or "").strip())


def _norm_iata(code: str | None) -> str | None:
    c = (code or "").strip().upper()
    return c if len(c) == 3 and c.isalpha() else None


def _norm_vibe(vibe: str | None) -> str:
    v = (vibe or "").strip().lower()[:40]
    return v or "adventure"


def prompt_hash(prompt: str | None) -> str | None:
    """Canonical 16-char hash of a prompt.

    Normalised before hashing (lowercase, collapsed whitespace, 200-char trim)
    so that prompts differing only in case or spacing produce the same hash.
    Both record_search (write side) and low_confidence_misses (read side) must
    call this function — never hash raw text directly.
    """
    p = " ".join((prompt or "").lower().split())[:200]
    if not p:
        return None
    return hashlib.sha256(p.encode("utf-8")).hexdigest()[:16]


def session_hash_for(fingerprint: str | None) -> str | None:
    f = (fingerprint or "").strip()
    if not f:
        return None
    return hashlib.sha256(f.encode("utf-8")).hexdigest()[:16]


def record_search(
    *,
    vibe: str | None,
    origin: str | None,
    dest_iata: str | None,
    search_type: str = "escape",
    result_count: int = 0,
    prompt: str | None = None,
    session_hash: str | None = None,
    signal_strength: int = SEARCHED,
    signal_id: str | None = None,
    model_source: str | None = None,
    intent_confidence: float | None = None,
    intent_rationale: str | None = None,
) -> str | None:
    """Write one search signal row. Returns the signal id (or None in MOCK mode)."""
    if _mock_mode():
        return None
    dest = _norm_iata(dest_iata)
    if not dest:
        return None
    sid = signal_id or uuid.uuid4().hex
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO search_signals (
                    id, ts, session_hash, vibe, origin, dest_iata,
                    search_type, result_count, signal_strength, prompt_hash,
                    model_source, intent_confidence, intent_rationale
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    sid,
                    time.time(),
                    (session_hash or "")[:32] or None,
                    _norm_vibe(vibe),
                    _norm_iata(origin),
                    dest,
                    (search_type or "escape")[:16],
                    int(result_count or 0),
                    max(1, min(4, int(signal_strength or 1))),
                    prompt_hash(prompt),
                    (model_source or "").strip() or None,
                    float(intent_confidence) if intent_confidence is not None else None,
                    (intent_rationale or "").strip()[:200] or None,
                ),
            )
            conn.commit()
    except Exception:
        return None
    return sid


def record_rejection(
    *,
    dest_iata: str | None,
    vibe: str | None,
    session_hash: str | None = None,
) -> str | None:
    """Record an explicit thumbs-down rejection for a vibe+destination pair.

    Stored with signal_strength=0 so it increments the search_count denominator
    without contributing to the affinity numerator, naturally diluting the score.
    No-op in MOCK mode.  Returns the new signal id (or None).
    """
    if _mock_mode():
        return None
    dest = _norm_iata(dest_iata)
    if not dest:
        return None
    sid = uuid.uuid4().hex
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO search_signals (
                    id, ts, session_hash, vibe, origin, dest_iata,
                    search_type, result_count, signal_strength, prompt_hash
                ) VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,NULL)
                """,
                (
                    sid,
                    time.time(),
                    (session_hash or "")[:32] or None,
                    _norm_vibe(vibe),
                    dest,
                    "thumb_down",
                    0,
                    0,  # strength=0: counts against affinity without boosting
                ),
            )
            conn.commit()
    except Exception:
        return None
    return sid


def upsert_signal(
    *,
    signal_id: str | None = None,
    dest_iata: str | None = None,
    vibe: str | None = None,
    origin: str | None = None,
    signal_strength: int = ENGAGED,
    search_type: str = "event",
    session_hash: str | None = None,
) -> str | None:
    """Upgrade an existing signal (never downgrade) or insert a fresh row.

    Idempotent: repeated events with the same strength change nothing.
    No-op in MOCK mode. Returns the affected signal id (or None).
    """
    if _mock_mode():
        return None
    strength = max(1, min(4, int(signal_strength or 1)))
    try:
        with get_conn() as conn:
            if signal_id:
                row = conn.execute(
                    "SELECT id, signal_strength, dest_iata FROM search_signals WHERE id = %s",
                    (signal_id,),
                ).fetchone()
                # Only upgrade the row when the destination matches (or none given)
                dest_req = _norm_iata(dest_iata)
                if row and dest_req and (row["dest_iata"] or "") != dest_req:
                    row = None
                if row:
                    if strength > int(row["signal_strength"] or 1):
                        conn.execute(
                            "UPDATE search_signals SET signal_strength = %s WHERE id = %s",
                            (strength, signal_id),
                        )
                        conn.commit()
                    return signal_id
            # No known row — need dest+vibe to create a standalone signal
            dest = _norm_iata(dest_iata)
            if not dest:
                return None
            sid = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO search_signals (
                    id, ts, session_hash, vibe, origin, dest_iata,
                    search_type, result_count, signal_strength, prompt_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                """,
                (
                    sid,
                    time.time(),
                    (session_hash or "")[:32] or None,
                    _norm_vibe(vibe),
                    _norm_iata(origin),
                    dest,
                    (search_type or "event")[:16],
                    0,
                    strength,
                ),
            )
            conn.commit()
            return sid
    except Exception:
        return None


def recompute_scores(*, force: bool = False) -> bool:
    """Rebuild dest_vibe_scores from search_signals.

    Lazy by default: skipped unless RECOMPUTE_INTERVAL_S has elapsed since
    the last run (timestamp tracked in signals_meta). Returns True if it ran.
    """
    if _mock_mode():
        return False
    now = time.time()
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM signals_meta WHERE key = 'last_recompute'"
            ).fetchone()
            last = float(row["value"]) if row and row["value"] else 0.0
            if not force and (now - last) < RECOMPUTE_INTERVAL_S:
                return False
            # Claim the slot first so concurrent requests don't all recompute
            conn.execute(
                "INSERT INTO signals_meta (key, value) VALUES ('last_recompute', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (str(now),),
            )
            rows = conn.execute(
                """
                SELECT dest_iata, vibe, ts, signal_strength
                FROM search_signals
                WHERE dest_iata IS NOT NULL
                """
            ).fetchall()
            agg: dict[tuple[str, str], dict[str, Any]] = {}
            for r in rows:
                key = (r["dest_iata"], r["vibe"] or "adventure")
                a = agg.setdefault(
                    key,
                    {"num": 0.0, "n": 0, "saves": 0, "last_ts": 0.0},
                )
                age_days = max(0.0, (now - float(r["ts"] or now)) / 86400.0)
                recency = 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)
                raw_s = r["signal_strength"]
                strength = int(raw_s) if raw_s is not None else 1
                a["num"] += strength * recency
                a["n"] += 1
                if strength >= SAVED:
                    a["saves"] += 1
                a["last_ts"] = max(a["last_ts"], float(r["ts"] or 0.0))
            conn.execute("DELETE FROM dest_vibe_scores")
            conn.executemany(
                """
                INSERT INTO dest_vibe_scores (
                    dest_iata, vibe, score, search_count, save_count,
                    last_signal_ts, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        dest,
                        vibe,
                        round(a["num"] / max(1, a["n"]), 4),
                        a["n"],
                        a["saves"],
                        a["last_ts"],
                        now,
                    )
                    for (dest, vibe), a in agg.items()
                ],
            )
            conn.commit()
            return True
    except Exception:
        return False


def shape_lean_for_vibe(vibe: str | None, *, demo: bool = False) -> float:
    """Learned shape lean for a vibe in [-1, 1]: +1 → detour, -1 → escape.

    Aggregates search_signals rows whose search_type recorded a shape
    ('escape'/'detour'), weighted by signal_strength (saves/thumbs upgrade
    strength in place, so engaged shapes count more) and recency.
    Returns 0.0 when *demo* is True, MOCK mode is on, or there is no data —
    the static prior table in yonder.intent then stands alone.
    """
    if demo or _mock_mode():
        return 0.0
    try:
        now = time.time()
        halflife_s = RECENCY_HALFLIFE_DAYS * 86400.0
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT search_type, signal_strength, ts FROM search_signals
                WHERE vibe = %s AND search_type IN ('escape', 'detour')
                """,
                (_norm_vibe(vibe),),
            ).fetchall()
        escape_w = 0.0
        detour_w = 0.0
        for r in rows:
            age = max(0.0, now - float(r["ts"] or now))
            w = float(r["signal_strength"] or 1) * (0.5 ** (age / halflife_s))
            if r["search_type"] == "detour":
                detour_w += w
            else:
                escape_w += w
        total = escape_w + detour_w
        if total <= 0:
            return 0.0
        return max(-1.0, min(1.0, (detour_w - escape_w) / total))
    except Exception:
        return 0.0


def scores_for_vibe(vibe: str | None, *, demo: bool = False) -> dict[str, float]:
    """dest_iata → aggregate signal score for one vibe (lazy-refreshes first).

    Returns {} when *demo* is True or MOCK mode is on — demo/testing sessions
    must see rankings as if the store were empty.
    """
    if demo or _mock_mode():
        return {}
    recompute_scores()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT dest_iata, score FROM dest_vibe_scores WHERE vibe = %s",
                (_norm_vibe(vibe),),
            ).fetchall()
        return {r["dest_iata"]: float(r["score"] or 0.0) for r in rows}
    except Exception:
        return {}


def top_for_vibe(
    vibe: str | None,
    *,
    limit: int = 10,
    group_by_country: bool = False,
    demo: bool = False,
) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
    """Top destinations for a vibe from dest_vibe_scores (lazy-refreshes first).

    Returns empty ([] or {}) when *demo* is True or MOCK mode is on — learned
    rankings are bypassed entirely in demo/testing sessions.
    """
    from yonder.countries import country_for_iata

    if demo or _mock_mode():
        return {} if group_by_country else []
    recompute_scores()
    lim = max(1, min(100, int(limit or 10)))
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT dest_iata, vibe, score, search_count, save_count, last_signal_ts
                FROM dest_vibe_scores
                WHERE vibe = %s
                ORDER BY score DESC, search_count DESC
                LIMIT %s
                """,
                (_norm_vibe(vibe), lim),
            ).fetchall()
    except Exception:
        rows = []
    items = [
        {
            "iata": r["dest_iata"],
            "vibe": r["vibe"],
            "score": float(r["score"] or 0.0),
            "search_count": int(r["search_count"] or 0),
            "save_count": int(r["save_count"] or 0),
            "country": country_for_iata(r["dest_iata"]) or None,
            "last_signal_ts": float(r["last_signal_ts"] or 0.0) or None,
        }
        for r in rows
    ]
    if not group_by_country:
        return items
    grouped: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        grouped.setdefault(it["country"] or "??", []).append(it)
    return grouped


def low_confidence_misses(
    *,
    confidence_threshold: float = 0.7,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return search_signals rows that are zero-result with low intent confidence.

    Also flags rows whose prompt_hash appears as a thumbs-down in result_feedback
    (via a cross-DB Python join), making them prime candidates to add to the
    paraphrase regression test suite.

    Returns [] in MOCK mode or on error.
    """
    if _mock_mode():
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    prompt_hash,
                    MAX(vibe) AS vibe,
                    MAX(search_type) AS search_type,
                    MAX(intent_confidence) AS intent_confidence,
                    MAX(intent_rationale) AS intent_rationale,
                    MAX(ts) AS ts,
                    COUNT(id) AS search_count
                FROM search_signals
                WHERE result_count = 0
                  AND intent_confidence IS NOT NULL
                  AND intent_confidence < %s
                  AND prompt_hash IS NOT NULL
                GROUP BY prompt_hash
                ORDER BY ts DESC
                LIMIT %s
                """,
                (float(confidence_threshold), int(limit)),
            ).fetchall()
    except Exception:
        return []

    # Thumbs-down cross-reference — result_feedback lives in the same Postgres DB
    thumbs_down_hashes: set[str] = set()
    try:
        with get_conn() as conn:
            _fb_rows = conn.execute(
                "SELECT DISTINCT query FROM result_feedback WHERE direction = 'down'"
            ).fetchall()
        for fr in _fb_rows:
            _q = " ".join((fr["query"] or "").lower().split())[:200]
            _h = prompt_hash(_q)
            if _h:
                thumbs_down_hashes.add(_h)
    except Exception:
        pass

    return [
        {
            "prompt_hash": r["prompt_hash"],
            "vibe": r["vibe"],
            "search_type": r["search_type"],
            "intent_confidence": (
                round(float(r["intent_confidence"]), 4)
                if r["intent_confidence"] is not None
                else None
            ),
            "intent_rationale": r["intent_rationale"],
            "search_count": int(r["search_count"] or 0),
            "ts": float(r["ts"] or 0.0),
            "thumbs_down": r["prompt_hash"] in thumbs_down_hashes,
        }
        for r in rows
    ]
