"""Learning layer — destination/vibe/route knowledge graph.

Persistent knowledge that makes each search smarter and cheaper than the
last, without ever replacing the AI as the interpreter of user intent:

  route_knowledge      — one row per directed (origin, dest) pair; every
                         fare-lookup outcome (success AND failure) upserts
                         here. Failed routes carry a freshness window so a
                         route can recover when airlines add service.
  vibe_interpretations — append-only archive of every AI destination
                         proposal: the RAW user query as typed, the AI's
                         verbatim interpretation, extracted attribute tags.
                         Never rewritten or pruned — future logic can
                         reprocess history with a better vocabulary.
  dest_attributes      — aggregated attribute profile per destination,
                         keyed by (dest_iata, attribute, source).
  vibe_attributes      — learned decomposition of each vibe into the same
                         attribute vocabulary, keyed by (vibe, attribute,
                         source).
  attribute_evidence   — links every aggregated attribute row back to the
                         raw signals (interpretation / feedback rows) that
                         produced it, so scores stay traceable and
                         recomputable.

Provenance (`source` enum): editorial > user_behavior > external >
ai_inference — a single AI mention never outweighs accumulated user
behavior or curated data. Per-source multipliers live in
SOURCE_MULTIPLIERS (one tunable config, not hard-coded through the code).

Confidence is derived, never guessed: it grows with evidence_count,
shrinks with contradiction_count (thumbs-down), and decays when stale.
Raw inputs (evidence_count, contradiction_count, last_reinforced_at) are
stored so the formula can be retuned without losing data.

Test-mode guard: when the MOCK environment variable is set, every write
is a no-op and every read returns empty/unknown — matching the
vibe_signals convention so demo sessions never pollute the knowledge
tables.

Schema is plain CREATE TABLE / no SQLite-only features, so a Postgres
migration (Task #402 style) is a straight port.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

from yonder.config import ROOT

DB_PATH = ROOT / "knowledge.db"

# ── Tunables ─────────────────────────────────────────────────────────────────
# Failed routes are re-checked after this many days (negative-cache TTL).
FAILED_ROUTE_TTL_DAYS = 30.0
# A verified route is considered "recent" for seeding within this window.
VERIFIED_FRESH_DAYS = 180.0
# Confidence decays toward 0 when unreinforced (half-life, days).
CONFIDENCE_STALE_HALFLIFE_DAYS = 180.0

# Default trust ordering for the read path — one tunable config.
SOURCE_MULTIPLIERS: dict[str, float] = {
    "editorial": 1.0,
    "user_behavior": 0.8,
    "external": 0.5,
    "ai_inference": 0.3,
}
VALID_SOURCES = tuple(SOURCE_MULTIPLIERS)

# ── Controlled attribute vocabulary (~28 tags) ───────────────────────────────
ATTRIBUTE_VOCAB: tuple[str, ...] = (
    "nature", "beach", "mountains", "island", "winter", "tropical",
    "city", "nightlife", "historic", "culture", "art", "food",
    "budget", "luxury", "remote", "spiritual", "romantic", "family",
    "adventure", "relaxing", "safe", "gritty", "modern", "trains",
    "wildlife", "desert", "festivals", "shopping",
)
_VOCAB_SET = set(ATTRIBUTE_VOCAB)

# Loose synonym → canonical-attribute map used by the server-side tagger.
_SYNONYMS: dict[str, str] = {
    "beaches": "beach", "coast": "beach", "coastal": "beach", "seasalt": "beach",
    "surf": "beach", "ocean": "beach", "sea": "beach",
    "alps": "mountains", "mountain": "mountains", "hiking": "mountains",
    "hike": "mountains", "rockies": "mountains", "trek": "mountains",
    "forest": "nature", "jungle": "nature", "lush": "nature", "park": "nature",
    "outdoors": "nature", "wild": "wildlife", "safari": "wildlife",
    "snow": "winter", "ski": "winter", "skiing": "winter", "north": "winter",
    "crisp": "winter", "cold": "winter",
    "warm": "tropical", "sun": "tropical", "sunny": "tropical",
    "warmnights": "tropical",
    "urban": "city", "metropolis": "city", "neon": "city",
    "party": "nightlife", "bars": "nightlife", "clubs": "nightlife",
    "electric": "nightlife", "music": "nightlife",
    "ancient": "historic", "ruins": "historic", "history": "historic",
    "old": "historic", "nostalgic": "historic", "medieval": "historic",
    "museum": "culture", "museums": "culture", "temples": "culture",
    "temple": "spiritual", "sacred": "spiritual", "pilgrimage": "spiritual",
    "monastery": "spiritual", "zen": "spiritual",
    "gallery": "art", "street-art": "art",
    "foodie": "food", "cuisine": "food", "street-food": "food", "eat": "food",
    "hawker": "food", "restaurants": "food", "bazaar": "shopping",
    "markets": "shopping", "market": "shopping",
    "cheap": "budget", "affordable": "budget", "value": "budget",
    "opulent": "luxury", "velvet": "luxury", "upscale": "luxury",
    "isolated": "remote", "untamed": "remote", "feral": "remote",
    "offbeat": "remote", "off-beaten-path": "remote",
    "romance": "romantic", "honeymoon": "romantic", "goldenhour": "romantic",
    "tender": "romantic",
    "kids": "family", "adventurous": "adventure", "chaos": "adventure",
    "chaotic": "adventure", "rugged": "adventure", "raw": "gritty",
    "relax": "relaxing", "serene": "relaxing", "calm": "relaxing",
    "sleepy": "relaxing", "cozy": "relaxing", "quiet": "relaxing",
    "train": "trains", "rail": "trains", "railway": "trains",
    "festival": "festivals", "carnival": "festivals",
    "dunes": "desert", "sahara": "desert",
}


def _mock_mode() -> bool:
    """All writes are no-ops and reads empty in test/demo data mode."""
    return bool((os.environ.get("MOCK") or "").strip())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS route_knowledge (
            origin_iata TEXT NOT NULL,
            dest_iata TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            success_count INTEGER NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0,
            last_verified_at REAL,
            last_failed_at REAL,
            last_provider TEXT,
            best_recent_price REAL,
            currency TEXT,
            updated_at REAL,
            PRIMARY KEY (origin_iata, dest_iata)
        );

        CREATE TABLE IF NOT EXISTS vibe_interpretations (
            id TEXT PRIMARY KEY,
            vibe TEXT NOT NULL DEFAULT 'adventure',
            raw_query TEXT NOT NULL DEFAULT '',
            query_norm TEXT NOT NULL DEFAULT '',
            origin_iata TEXT,
            dest_iata TEXT NOT NULL,
            interpretation TEXT NOT NULL DEFAULT '',
            attribute_tags TEXT NOT NULL DEFAULT '[]',
            trip_shape TEXT,
            model_source TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vi_vibe ON vibe_interpretations(vibe);
        CREATE INDEX IF NOT EXISTS idx_vi_dest ON vibe_interpretations(dest_iata);

        CREATE TABLE IF NOT EXISTS dest_attributes (
            dest_iata TEXT NOT NULL,
            attribute TEXT NOT NULL,
            source TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            contradiction_count INTEGER NOT NULL DEFAULT 0,
            last_reinforced_at REAL,
            updated_at REAL,
            PRIMARY KEY (dest_iata, attribute, source)
        );

        CREATE TABLE IF NOT EXISTS vibe_attributes (
            vibe TEXT NOT NULL,
            attribute TEXT NOT NULL,
            source TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            contradiction_count INTEGER NOT NULL DEFAULT 0,
            last_reinforced_at REAL,
            updated_at REAL,
            PRIMARY KEY (vibe, attribute, source)
        );

        CREATE TABLE IF NOT EXISTS attribute_evidence (
            id TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            attribute TEXT NOT NULL,
            source TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ae_subject
            ON attribute_evidence(subject_kind, subject, attribute, source);
        """
    )
    conn.commit()
    return conn


def _norm_iata(code: str | None) -> str | None:
    c = (code or "").strip().upper()
    return c if len(c) == 3 and c.isalpha() else None


def _norm_vibe(vibe: str | None) -> str:
    v = (vibe or "").strip().lower()[:40]
    return v or "adventure"


def _norm_query(q: str | None) -> str:
    return " ".join((q or "").lower().split())[:200]


# ── Attribute tagging (server-side, controlled vocabulary) ───────────────────

_WORD_RE = re.compile(r"[a-z][a-z-]+")


def extract_attribute_tags(
    text: str | None = None, tags: Iterable[str] | None = None
) -> list[str]:
    """Map free text + loose tags onto the controlled attribute vocabulary."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        t = tok.strip().lower()
        canon = t if t in _VOCAB_SET else _SYNONYMS.get(t)
        if canon and canon not in seen:
            seen.add(canon)
            found.append(canon)

    for t in tags or []:
        _add(str(t))
    for w in _WORD_RE.findall((text or "").lower()):
        _add(w)
    return found


# ── route_knowledge ──────────────────────────────────────────────────────────

def record_route_outcome(
    *,
    origin: str | None,
    dest: str | None,
    success: bool,
    provider: str | None = None,
    price: float | None = None,
    currency: str | None = None,
) -> bool:
    """Upsert one fare-lookup outcome (called on EVERY outcome, empties too)."""
    if _mock_mode():
        return False
    o, d = _norm_iata(origin), _norm_iata(dest)
    if not o or not d or o == d:
        return False
    now = time.time()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM route_knowledge WHERE origin_iata=? AND dest_iata=?",
                (o, d),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO route_knowledge (
                        origin_iata, dest_iata, status, success_count, fail_count,
                        last_verified_at, last_failed_at, last_provider,
                        best_recent_price, currency, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        o, d,
                        "verified" if success else "failed",
                        1 if success else 0,
                        0 if success else 1,
                        now if success else None,
                        None if success else now,
                        (provider or "").strip() or None,
                        float(price) if (success and price and price > 0) else None,
                        (currency or "").upper() or None,
                        now,
                    ),
                )
            else:
                if success:
                    best = row["best_recent_price"]
                    if price and price > 0:
                        best = min(float(best), float(price)) if best else float(price)
                    conn.execute(
                        """
                        UPDATE route_knowledge SET status='verified',
                            success_count=success_count+1, last_verified_at=?,
                            last_provider=?, best_recent_price=?, currency=?,
                            updated_at=?
                        WHERE origin_iata=? AND dest_iata=?
                        """,
                        (
                            now,
                            (provider or "").strip() or row["last_provider"],
                            best,
                            (currency or "").upper() or row["currency"],
                            now, o, d,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE route_knowledge SET status='failed',
                            fail_count=fail_count+1, last_failed_at=?,
                            last_provider=?, updated_at=?
                        WHERE origin_iata=? AND dest_iata=?
                        """,
                        (now, (provider or "").strip() or row["last_provider"], now, o, d),
                    )
            conn.commit()
        return True
    except Exception:
        return False


def route_status(
    origin: str | None, dest: str | None, *, demo: bool = False
) -> str:
    """'verified' | 'failed' | 'unknown' for a directed pair.

    'failed' is only returned while the negative cache is fresh
    (FAILED_ROUTE_TTL_DAYS); after that the route reverts to 'unknown' so it
    can recover when airlines add service. A verification newer than the
    last failure always wins.
    """
    if demo or _mock_mode():
        return "unknown"
    o, d = _norm_iata(origin), _norm_iata(dest)
    if not o or not d:
        return "unknown"
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM route_knowledge WHERE origin_iata=? AND dest_iata=?",
                (o, d),
            ).fetchone()
    except Exception:
        return "unknown"
    if row is None:
        return "unknown"
    now = time.time()
    lv = float(row["last_verified_at"] or 0.0)
    lf = float(row["last_failed_at"] or 0.0)
    if row["status"] == "failed" or lf > lv:
        if lf >= lv and (now - lf) < FAILED_ROUTE_TTL_DAYS * 86400.0:
            return "failed"
        return "verified" if lv else "unknown"
    if row["status"] == "verified" and lv:
        return "verified"
    return "unknown"


def get_route(origin: str | None, dest: str | None) -> dict[str, Any] | None:
    """Raw route_knowledge row as a dict, or None."""
    o, d = _norm_iata(origin), _norm_iata(dest)
    if not o or not d:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM route_knowledge WHERE origin_iata=? AND dest_iata=?",
                (o, d),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ── Confidence ───────────────────────────────────────────────────────────────

def compute_confidence(
    evidence_count: int,
    contradiction_count: int = 0,
    last_reinforced_at: float | None = None,
    *,
    now: float | None = None,
) -> float:
    """Derived confidence in [0,1]: grows with evidence, decays with
    contradictions and staleness. Raw inputs stay stored so this formula can
    be retuned later without losing data."""
    e = max(0, int(evidence_count or 0))
    c = max(0, int(contradiction_count or 0))
    if e <= 0:
        return 0.0
    base = e / (e + 2.0)                      # saturating growth
    agree = e / (e + 2.0 * c) if c else 1.0   # contradiction penalty
    stale = 1.0
    if last_reinforced_at:
        age_days = max(0.0, ((now or time.time()) - float(last_reinforced_at)) / 86400.0)
        stale = 0.5 ** (age_days / CONFIDENCE_STALE_HALFLIFE_DAYS)
    return round(max(0.0, min(1.0, base * agree * stale)), 4)


def _upsert_attribute(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key_val: str,
    attribute: str,
    source: str,
    *,
    weight_delta: float = 1.0,
    contradiction: bool = False,
    now: float | None = None,
) -> None:
    now = now or time.time()
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {key_col}=? AND attribute=? AND source=?",
        (key_val, attribute, source),
    ).fetchone()
    if row is None:
        e = 0 if contradiction else 1
        c = 1 if contradiction else 0
        conn.execute(
            f"""
            INSERT INTO {table} ({key_col}, attribute, source, weight, confidence,
                evidence_count, contradiction_count, last_reinforced_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                key_val, attribute, source,
                max(0.0, weight_delta if not contradiction else 0.0),
                compute_confidence(e, c, now, now=now),
                e, c, now if not contradiction else None, now,
            ),
        )
        return
    e = int(row["evidence_count"] or 0)
    c = int(row["contradiction_count"] or 0)
    w = float(row["weight"] or 0.0)
    lr = row["last_reinforced_at"]
    if contradiction:
        c += 1
        w = max(0.0, w * 0.8)  # dampen, never below zero
    else:
        e += 1
        w += weight_delta
        lr = now
    conn.execute(
        f"""
        UPDATE {table} SET weight=?, confidence=?, evidence_count=?,
            contradiction_count=?, last_reinforced_at=?, updated_at=?
        WHERE {key_col}=? AND attribute=? AND source=?
        """,
        (w, compute_confidence(e, c, lr, now=now), e, c, lr, now,
         key_val, attribute, source),
    )


def _link_evidence(
    conn: sqlite3.Connection,
    *,
    subject_kind: str,
    subject: str,
    attribute: str,
    source: str,
    evidence_kind: str,
    evidence_id: str,
    now: float,
) -> None:
    conn.execute(
        """
        INSERT INTO attribute_evidence (id, subject_kind, subject, attribute,
            source, evidence_kind, evidence_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (uuid.uuid4().hex, subject_kind, subject, attribute, source,
         evidence_kind, evidence_id, now),
    )


# ── vibe_interpretations capture ─────────────────────────────────────────────

def record_interpretation(
    *,
    vibe: str | None,
    raw_query: str | None,
    origin: str | None,
    dest_iata: str | None,
    interpretation: str | None,
    tags: Iterable[str] | None = None,
    trip_shape: str | None = None,
    model_source: str | None = None,
) -> str | None:
    """Append one AI destination proposal to the permanent archive and grow
    dest_attributes / vibe_attributes (source='ai_inference') with evidence
    links. Returns the interpretation id (None in MOCK mode / bad input)."""
    if _mock_mode():
        return None
    dest = _norm_iata(dest_iata)
    if not dest:
        return None
    v = _norm_vibe(vibe)
    verbatim = (interpretation or "").strip()
    attrs = extract_attribute_tags(verbatim, tags)
    vid = uuid.uuid4().hex
    now = time.time()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO vibe_interpretations (id, vibe, raw_query, query_norm,
                    origin_iata, dest_iata, interpretation, attribute_tags,
                    trip_shape, model_source, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    vid, v, (raw_query or ""), _norm_query(raw_query),
                    _norm_iata(origin), dest, verbatim,
                    json.dumps(attrs), (trip_shape or "").strip()[:16] or None,
                    (model_source or "").strip() or None, now,
                ),
            )
            for a in attrs:
                _upsert_attribute(conn, "dest_attributes", "dest_iata", dest,
                                  a, "ai_inference", now=now)
                _link_evidence(conn, subject_kind="dest", subject=dest,
                               attribute=a, source="ai_inference",
                               evidence_kind="interpretation", evidence_id=vid,
                               now=now)
                _upsert_attribute(conn, "vibe_attributes", "vibe", v,
                                  a, "ai_inference", now=now)
                _link_evidence(conn, subject_kind="vibe", subject=v,
                               attribute=a, source="ai_inference",
                               evidence_kind="interpretation", evidence_id=vid,
                               now=now)
            conn.commit()
        return vid
    except Exception:
        return None


def capture_interpretations_async(
    proposals: list[dict[str, Any]],
    *,
    vibe: str | None,
    raw_query: str | None,
    origin: str | None,
    trip_shape: str | None = None,
    model_source: str | None = None,
) -> None:
    """Fire-and-forget capture of a batch of AI proposals — never delays or
    breaks rendering. Each proposal dict: {dest_iata, interpretation, tags}."""
    if _mock_mode() or not proposals:
        return

    def _run() -> None:
        for p in proposals:
            try:
                record_interpretation(
                    vibe=vibe,
                    raw_query=raw_query,
                    origin=origin,
                    dest_iata=p.get("dest_iata"),
                    interpretation=p.get("interpretation"),
                    tags=p.get("tags"),
                    trip_shape=trip_shape,
                    model_source=model_source,
                )
            except Exception:
                pass

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


# ── Feedback reinforcement ───────────────────────────────────────────────────

def reinforce_from_feedback(
    *,
    vibe: str | None,
    dest_iata: str | None,
    direction: str,
    feedback_id: str | None = None,
) -> bool:
    """Thumbs up/down shifts attribute weights (source='user_behavior').

    Up   → reinforce the attributes this destination was proposed for
           (from its ai_inference rows; falls back to the vibe's learned
           attribute vector).
    Down → contradiction on those same attributes: contradiction_count up,
           weight dampened, confidence recomputed lower.
    """
    if _mock_mode():
        return False
    dest = _norm_iata(dest_iata)
    d = (direction or "").strip().lower()
    if not dest or d not in ("up", "down"):
        return False
    v = _norm_vibe(vibe)
    now = time.time()
    fid = feedback_id or uuid.uuid4().hex
    try:
        with _connect() as conn:
            # Attributes this destination was proposed for (AI evidence first)
            rows = conn.execute(
                """
                SELECT DISTINCT attribute FROM dest_attributes
                WHERE dest_iata=? AND source='ai_inference'
                """,
                (dest,),
            ).fetchall()
            attrs = [r["attribute"] for r in rows]
            if not attrs:
                rows = conn.execute(
                    """
                    SELECT attribute FROM vibe_attributes
                    WHERE vibe=? ORDER BY weight*confidence DESC LIMIT 5
                    """,
                    (v,),
                ).fetchall()
                attrs = [r["attribute"] for r in rows]
            if not attrs:
                return False
            contradiction = d == "down"
            for a in attrs:
                _upsert_attribute(conn, "dest_attributes", "dest_iata", dest,
                                  a, "user_behavior",
                                  contradiction=contradiction, now=now)
                _link_evidence(conn, subject_kind="dest", subject=dest,
                               attribute=a, source="user_behavior",
                               evidence_kind="feedback", evidence_id=fid, now=now)
                _upsert_attribute(conn, "vibe_attributes", "vibe", v,
                                  a, "user_behavior",
                                  contradiction=contradiction, now=now)
                _link_evidence(conn, subject_kind="vibe", subject=v,
                               attribute=a, source="user_behavior",
                               evidence_kind="feedback", evidence_id=fid, now=now)
                if contradiction:
                    # Thumbs-down also contradicts the AI's original claim
                    for table, col, key in (
                        ("dest_attributes", "dest_iata", dest),
                        ("vibe_attributes", "vibe", v),
                    ):
                        row = conn.execute(
                            f"SELECT 1 FROM {table} WHERE {col}=? AND attribute=? "
                            "AND source='ai_inference'",
                            (key, a),
                        ).fetchone()
                        if row:
                            _upsert_attribute(conn, table, col, key, a,
                                              "ai_inference",
                                              contradiction=True, now=now)
            conn.commit()
        return True
    except Exception:
        return False


# ── Reads ────────────────────────────────────────────────────────────────────

def effective_attributes(
    *,
    dest_iata: str | None = None,
    vibe: str | None = None,
    demo: bool = False,
) -> dict[str, float]:
    """attribute → effective score = Σ(weight × confidence × source_mult).

    Pass exactly one of dest_iata / vibe. Per-source rows are combined here
    so future recommendation logic can reweigh signals without schema
    changes."""
    if demo or _mock_mode():
        return {}
    if dest_iata:
        table, col, key = "dest_attributes", "dest_iata", _norm_iata(dest_iata)
    else:
        table, col, key = "vibe_attributes", "vibe", _norm_vibe(vibe)
    if not key:
        return {}
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT attribute, source, weight, confidence FROM {table} "
                f"WHERE {col}=?",
                (key,),
            ).fetchall()
    except Exception:
        return {}
    out: dict[str, float] = {}
    for r in rows:
        mult = SOURCE_MULTIPLIERS.get(r["source"], 0.0)
        out[r["attribute"]] = out.get(r["attribute"], 0.0) + (
            float(r["weight"] or 0.0) * float(r["confidence"] or 0.0) * mult
        )
    return {a: round(s, 4) for a, s in out.items() if s > 0}


def evidence_for(
    *, subject_kind: str, subject: str, attribute: str | None = None
) -> list[dict[str, Any]]:
    """Trace an aggregated attribute back to its raw rows (interpretation /
    feedback ids). Answers "which exact queries built this score?"."""
    try:
        with _connect() as conn:
            sql = (
                "SELECT * FROM attribute_evidence WHERE subject_kind=? AND subject=?"
            )
            params: list[Any] = [subject_kind, subject]
            if attribute:
                sql += " AND attribute=?"
                params.append(attribute)
            rows = conn.execute(sql + " ORDER BY created_at", params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_interpretations(
    *, vibe: str | None = None, dest_iata: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Read raw archive rows (for reprocessing / debugging)."""
    try:
        with _connect() as conn:
            sql = "SELECT * FROM vibe_interpretations WHERE 1=1"
            params: list[Any] = []
            if vibe:
                sql += " AND vibe=?"
                params.append(_norm_vibe(vibe))
            if dest_iata:
                sql += " AND dest_iata=?"
                params.append(_norm_iata(dest_iata))
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(1000, int(limit or 100))))
            rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["attribute_tags"] = json.loads(d.get("attribute_tags") or "[]")
            except Exception:
                d["attribute_tags"] = []
            out.append(d)
        return out
    except Exception:
        return []


def seed_candidates(
    *,
    vibe: str | None,
    origin: str | None,
    limit: int = 8,
    demo: bool = False,
) -> list[dict[str, Any]]:
    """Knowledge-assisted candidate list for prompt seeding.

    Combines direct vibe→destination evidence (dest_vibe_scores) with the
    attribute match between the vibe's learned attribute vector and each
    destination's attribute profile, then filters on route knowledge from
    the user's origin: fresh-failed routes are dropped, verified routes rank
    first. Empty on cold start / MOCK / demo — today's flow unchanged."""
    if demo or _mock_mode():
        return []
    v = _norm_vibe(vibe)
    o = _norm_iata(origin)
    lim = max(1, min(20, int(limit or 8)))

    # Direct vibe→destination evidence (reused as-is)
    try:
        from yonder.vibe_signals import scores_for_vibe

        direct = scores_for_vibe(v)
    except Exception:
        direct = {}

    vibe_vec = effective_attributes(vibe=v)
    candidates: dict[str, float] = dict(direct)
    if vibe_vec:
        try:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT dest_iata, attribute, source, weight, confidence "
                    "FROM dest_attributes",
                ).fetchall()
        except Exception:
            rows = []
        per_dest: dict[str, float] = {}
        for r in rows:
            va = vibe_vec.get(r["attribute"])
            if not va:
                continue
            mult = SOURCE_MULTIPLIERS.get(r["source"], 0.0)
            per_dest[r["dest_iata"]] = per_dest.get(r["dest_iata"], 0.0) + (
                va * float(r["weight"] or 0.0) * float(r["confidence"] or 0.0) * mult
            )
        for iata, s in per_dest.items():
            candidates[iata] = candidates.get(iata, 0.0) + s

    if o:
        candidates.pop(o, None)
    if not candidates:
        return []

    out: list[dict[str, Any]] = []
    for iata, score in candidates.items():
        st = route_status(o, iata) if o else "unknown"
        if st == "failed":
            continue  # fresh-failed → don't waste a suggestion (or an API call)
        out.append({"iata": iata, "score": round(float(score), 4), "route": st})
    out.sort(key=lambda c: (c["route"] == "verified", c["score"]), reverse=True)
    return out[:lim]
