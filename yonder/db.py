"""Shared PostgreSQL connection module (Replit built-in database).

Reads DATABASE_URL from the environment, keeps a thread-safe connection
pool, and creates every application table on first use. Modules call
``get_conn()`` and use sqlite-style ``conn.execute(...)`` via a thin
wrapper; placeholders are psycopg2-style ``%s``.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# Per-connection last-used timestamps.
# Keyed by the raw connection *object* (not id()), so identity is preserved and
# Python cannot mistake a new connection for an old one even if memory addresses
# are reused.  Entries are removed explicitly when a connection is discarded so
# the dict does not grow without bound.
_conn_last_used: dict[Any, float] = {}
_conn_last_used_lock = threading.Lock()

# Only ping a connection with SELECT 1 if it has been idle longer than this.
_IDLE_PING_THRESHOLD: float = 30.0  # seconds

# Errors that indicate the connection was dropped (idle SSL close, server
# restart, network hiccup).  These trigger a discard-and-replace cycle.
_RECONNECT_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

# Extra kwargs forwarded to psycopg2.connect() for every new connection.
# keepalives let the OS detect idle drops without waiting for a full query
# timeout; connect_timeout prevents hanging health-checks during cold starts.
_CONNECT_KWARGS: dict[str, Any] = {
    "keepalives": 1,
    "keepalives_idle": 60,    # start TCP probes after 60 s of silence
    "keepalives_interval": 10, # re-probe every 10 s
    "keepalives_count": 5,     # give up after 5 missed probes (~110 s total)
    "connect_timeout": 10,     # bail on initial connect after 10 s
}

_DDL = """
CREATE TABLE IF NOT EXISTS saved_itineraries (
    id TEXT PRIMARY KEY,
    saved_at DOUBLE PRECISION NOT NULL,
    priced_at DOUBLE PRECISION,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_price DOUBLE PRECISION,
    display_price TEXT,
    stop_city TEXT,
    stop_iata TEXT,
    stay_days INTEGER,
    origin TEXT,
    destination TEXT,
    adults INTEGER DEFAULT 1,
    cabin TEXT DEFAULT 'economy',
    vibe TEXT,
    trip_prompt TEXT,
    theme_country TEXT,
    theme_primary TEXT,
    theme_accent TEXT,
    theme_gradient TEXT,
    theme_flag_img TEXT,
    theme_label TEXT,
    google_flights_url TEXT,
    kayak_url TEXT,
    ground_display TEXT,
    ground_compare_line TEXT,
    all_in_display TEXT,
    notes_json TEXT,
    itinerary_json TEXT NOT NULL,
    trip_meta_json TEXT,
    owner_sess TEXT
);
CREATE INDEX IF NOT EXISTS idx_saved_at ON saved_itineraries(saved_at DESC);
ALTER TABLE saved_itineraries ADD COLUMN IF NOT EXISTS owner_sess TEXT;
CREATE INDEX IF NOT EXISTS idx_saved_owner ON saved_itineraries(owner_sess);

-- Personal quest bookmarks: quests (kind='quest') are global library rows;
-- "★ Save" associates the existing row with a browser session instead of
-- duplicating it.  Unique index makes repeat clicks idempotent.
CREATE TABLE IF NOT EXISTS quest_bookmarks (
    owner_sess TEXT NOT NULL,
    saved_id TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_quest_bookmark
    ON quest_bookmarks(owner_sess, saved_id);
CREATE INDEX IF NOT EXISTS idx_qb_saved ON quest_bookmarks(saved_id);

CREATE TABLE IF NOT EXISTS shared_trips (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    slug TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shared_created ON shared_trips(created_at DESC);

CREATE TABLE IF NOT EXISTS place_briefs (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    fetched_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS search_signals (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    session_hash TEXT,
    vibe TEXT,
    origin TEXT,
    dest_iata TEXT,
    search_type TEXT,
    result_count INTEGER,
    signal_strength INTEGER NOT NULL DEFAULT 1,
    prompt_hash TEXT,
    model_source TEXT,
    intent_confidence DOUBLE PRECISION,
    intent_rationale TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_dest_vibe ON search_signals(dest_iata, vibe);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON search_signals(ts DESC);
CREATE INDEX IF NOT EXISTS idx_signals_result_count
    ON search_signals(result_count, intent_confidence);

CREATE TABLE IF NOT EXISTS dest_vibe_scores (
    dest_iata TEXT NOT NULL,
    vibe TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    search_count INTEGER NOT NULL DEFAULT 0,
    save_count INTEGER NOT NULL DEFAULT 0,
    last_signal_ts DOUBLE PRECISION,
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (dest_iata, vibe)
);

CREATE TABLE IF NOT EXISTS signals_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS result_feedback (
    id          TEXT PRIMARY KEY,
    session_hash TEXT,
    vibe        TEXT NOT NULL DEFAULT '',
    dest_iata   TEXT NOT NULL DEFAULT '',
    query       TEXT NOT NULL DEFAULT '',
    direction   TEXT NOT NULL CHECK(direction IN ('up','down')),
    created_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rf_vibe_dest ON result_feedback(vibe, dest_iata);
CREATE INDEX IF NOT EXISTS idx_rf_created ON result_feedback(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rf_vote
    ON result_feedback((COALESCE(session_hash, '')), vibe, dest_iata, direction);

CREATE TABLE IF NOT EXISTS vibe_questions (
    id              TEXT PRIMARY KEY,
    vibe            TEXT NOT NULL DEFAULT '',
    query_norm      TEXT NOT NULL DEFAULT '',
    answer_json     TEXT,
    created_at      DOUBLE PRECISION NOT NULL,
    answer_at       DOUBLE PRECISION,
    UNIQUE(vibe, query_norm)
);
CREATE INDEX IF NOT EXISTS idx_vq_vibe ON vibe_questions(vibe, answer_at DESC);

CREATE TABLE IF NOT EXISTS price_samples (
    id SERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT,
    price DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    price_kind TEXT,
    stops INTEGER,
    airlines TEXT,
    duration_minutes INTEGER,
    notes TEXT,
    google_flights_url TEXT,
    deep_link TEXT,
    raw_id TEXT,
    observed_at TEXT NOT NULL,
    model_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_date
    ON price_samples(origin, destination, depart_date, observed_at);

CREATE TABLE IF NOT EXISTS funnel_events (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    event TEXT NOT NULL,
    click_id TEXT,
    chip_id TEXT,
    chip_source TEXT,
    vibe TEXT,
    origin TEXT,
    search_id TEXT,
    saved_id TEXT,
    dest TEXT,
    url TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_funnel_ts ON funnel_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_funnel_click ON funnel_events(click_id);

CREATE TABLE IF NOT EXISTS ai_usage (
    id               SERIAL PRIMARY KEY,
    ts               TEXT    NOT NULL,
    route            TEXT    NOT NULL,
    model            TEXT,
    prompt_tokens    INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens     INTEGER DEFAULT 0,
    est_cost_usd     DOUBLE PRECISION DEFAULT 0,
    calls            INTEGER DEFAULT 1,
    model_source     TEXT
);

CREATE TABLE IF NOT EXISTS daily_cost_cache (
    cache_key TEXT PRIMARY KEY,
    origin_cc TEXT NOT NULL,
    stop_cc TEXT NOT NULL,
    currency TEXT NOT NULL,
    daily_origin DOUBLE PRECISION NOT NULL,
    daily_stop DOUBLE PRECISION NOT NULL,
    style TEXT,
    includes TEXT,
    blurb TEXT,
    source TEXT,
    fetched_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS fare_estimates (
    id           SERIAL PRIMARY KEY,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    year_month   TEXT NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'USD',
    price_low    DOUBLE PRECISION NOT NULL,
    price_high   DOUBLE PRECISION NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 1,
    sampled_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fare_estimates_route_month
    ON fare_estimates(origin, destination, year_month, currency);
CREATE TABLE IF NOT EXISTS route_knowledge (
    origin_iata TEXT NOT NULL,
    dest_iata TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_verified_at DOUBLE PRECISION,
    last_failed_at DOUBLE PRECISION,
    last_provider TEXT,
    best_recent_price DOUBLE PRECISION,
    currency TEXT,
    updated_at DOUBLE PRECISION,
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
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vi_vibe ON vibe_interpretations(vibe);
CREATE INDEX IF NOT EXISTS idx_vi_dest ON vibe_interpretations(dest_iata);

CREATE TABLE IF NOT EXISTS dest_attributes (
    dest_iata TEXT NOT NULL,
    attribute TEXT NOT NULL,
    source TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    last_reinforced_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (dest_iata, attribute, source)
);

CREATE TABLE IF NOT EXISTS vibe_attributes (
    vibe TEXT NOT NULL,
    attribute TEXT NOT NULL,
    source TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    last_reinforced_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (vibe, attribute, source)
);

CREATE TABLE IF NOT EXISTS last_search (
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    payload JSONB NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, mode)
);

CREATE TABLE IF NOT EXISTS attribute_evidence (
    id TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    attribute TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ae_subject
    ON attribute_evidence(subject_kind, subject, attribute, source);

CREATE TABLE IF NOT EXISTS pois (
    feature_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    google_maps_url TEXT NOT NULL DEFAULT '',
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    city_slug TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    list_title TEXT NOT NULL DEFAULT '',
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    status_checked_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_pois_city ON pois(city_slug);
-- Idempotent column migrations for existing installations
ALTER TABLE pois ADD COLUMN IF NOT EXISTS closed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pois ADD COLUMN IF NOT EXISTS status_checked_at DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS ad_candidates (
    dest_iata TEXT NOT NULL,
    vibe TEXT NOT NULL,
    city_name TEXT,
    ad_title TEXT,
    ad_body TEXT,
    landing_url TEXT,
    save_count INTEGER NOT NULL DEFAULT 0,
    search_count INTEGER NOT NULL DEFAULT 0,
    signal_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    push_state TEXT NOT NULL DEFAULT 'pending',
    pushed_at DOUBLE PRECISION,
    ads_api_ad_id TEXT,
    updated_at DOUBLE PRECISION NOT NULL,
    fail_count INTEGER NOT NULL DEFAULT 0,
    failed_at DOUBLE PRECISION,
    PRIMARY KEY (dest_iata, vibe)
);
-- Idempotent migrations: add columns to pre-existing tables.
ALTER TABLE ad_candidates ADD COLUMN IF NOT EXISTS fail_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ad_candidates ADD COLUMN IF NOT EXISTS failed_at DOUBLE PRECISION;
-- Backfill: legacy failed rows with no failed_at get updated_at as their failure timestamp.
UPDATE ad_candidates SET failed_at = updated_at WHERE push_state = 'failed' AND failed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ad_candidates_push
    ON ad_candidates(push_state, signal_score DESC);
CREATE INDEX IF NOT EXISTS idx_ad_candidates_pushed_at
    ON ad_candidates(pushed_at);

CREATE TABLE IF NOT EXISTS quest_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'reading_vibe',
    home_iata TEXT NOT NULL DEFAULT '',
    vibe TEXT NOT NULL DEFAULT '',
    ok BOOLEAN,
    error_text TEXT,
    payload BYTEA,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quest_jobs_created ON quest_jobs(created_at);

CREATE TABLE IF NOT EXISTS session_prefs (
    session_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (session_id, key)
);

CREATE TABLE IF NOT EXISTS ad_pipeline_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""



class Conn:
    """Thin sqlite-style wrapper over a pooled psycopg2 connection.

    ``execute()`` returns a RealDictCursor so rows read like dicts
    (``row["col"]``), matching the old ``sqlite3.Row`` access pattern.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def execute(self, sql: str, params: Any = None) -> Any:
        cur = self._raw.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq: Any) -> Any:
        cur = self._raw.cursor(cursor_factory=RealDictCursor)
        cur.executemany(sql, seq)
        return cur

    def commit(self) -> None:
        self._raw.commit()


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set — the Replit PostgreSQL database is required."
        )
    return url


def _is_conn_alive(raw: Any) -> bool:
    """Return True only if *raw* can execute a trivial round-trip query.

    psycopg2's ``connection.closed`` attribute only updates *after* an
    operation fails, so it cannot detect a server-side SSL close that happened
    while the connection sat idle in the pool.  A lightweight ``SELECT 1``
    forces the round-trip and surfaces that error before the caller's real
    query runs.
    """
    if getattr(raw, "closed", 1):
        return False
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


# Arbitrary but stable app-wide key for the schema-migration advisory lock.
_DDL_LOCK_KEY = 715_001_042


def _run_ddl(pool: ThreadedConnectionPool) -> None:
    """Run the idempotent schema DDL exactly once across all processes.

    Production runs multiple gunicorn workers that import this module
    simultaneously.  Unsynchronised concurrent DDL (CREATE TABLE / ALTER
    TABLE / CREATE INDEX) races in Postgres and can raise ("tuple
    concurrently updated", duplicate pg_type errors), killing workers at
    import time.  A session-level advisory lock serialises the migration:
    the first worker does the work, the rest wait then no-op.

    Failures are retried a few times (transient SSL drops at boot), and a
    final failure is logged loudly but does NOT crash the worker — the
    schema almost certainly already exists from a previous boot; dying at
    import just turns a migration hiccup into a full outage.
    """
    last_exc: Exception | None = None
    attempts = 3
    for attempt in range(1, attempts + 1):
        # Dedicated connection, never pooled: if anything goes wrong we CLOSE
        # it, which makes Postgres release the session advisory lock — a
        # poisoned/lock-holding connection can never leak into the pool.
        raw = None
        try:
            raw = psycopg2.connect(_database_url(), **_CONNECT_KWARGS)
            with raw.cursor() as cur:
                # Bound the lock wait so a stuck/leaked external lock cannot
                # hang worker boot forever.
                cur.execute("SET lock_timeout = '60s'")
                cur.execute("SELECT pg_advisory_lock(%s)", (_DDL_LOCK_KEY,))
                cur.execute(_DDL)
            raw.commit()
            return
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.5 * attempt)
        finally:
            if raw is not None:
                try:
                    raw.close()  # releases the session advisory lock too
                except Exception:
                    pass

    # All attempts failed. Only continue if the schema already exists —
    # otherwise serving traffic would just turn into request-time 500s.
    if _schema_present(pool):
        print(
            f"[db] SCHEMA MIGRATION FAILED after {attempts} attempts: {last_exc!r} "
            "— existing schema verified, continuing. Newest columns/tables may be missing!",
            flush=True,
        )
        return
    raise RuntimeError(
        f"Database schema migration failed and no existing schema found: {last_exc!r}"
    )


def _schema_present(pool: ThreadedConnectionPool) -> bool:
    """True if the core schema already exists (sentinel table check)."""
    try:
        raw = pool.getconn()
        try:
            with raw.cursor() as cur:
                cur.execute("SELECT to_regclass('saved_itineraries')")
                row = cur.fetchone()
            raw.rollback()
            return bool(row and row[0])
        finally:
            pool.putconn(raw)
    except Exception:
        return False


def _ensure_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                pool = ThreadedConnectionPool(
                    1, 10, _database_url(), **_CONNECT_KWARGS
                )
                _run_ddl(pool)
                _pool = pool
    return _pool


@contextmanager
def get_conn() -> Iterator[Conn]:
    """Pooled connection context manager: commit on success, rollback on error.

    Transparently survives idle SSL drops from the managed Postgres:

    * Before yielding, the pooled connection is validated with a ``SELECT 1``
      round-trip.  A stale connection is discarded (``putconn(close=True)``)
      and replaced by a fresh one from the pool — the caller never sees the
      dead connection.
    * If an ``OperationalError``/``InterfaceError`` escapes anyway (e.g. the
      server drops the connection *during* the request), the connection is
      discarded so it is not recycled to a future caller.
    """
    pool = _ensure_pool()
    raw = pool.getconn()
    _returned = False

    # Pre-validate: only ping with SELECT 1 when the connection has been idle
    # longer than _IDLE_PING_THRESHOLD.  Busy/recently-used connections skip
    # the round-trip entirely, cutting per-request overhead under load.
    #
    # The dict is keyed by the raw connection *object* (not id()), so a new
    # connection that happens to reuse the same memory address cannot inherit
    # a stale timestamp from a discarded predecessor.
    now = time.monotonic()
    with _conn_last_used_lock:
        last_used = _conn_last_used.get(raw, 0.0)
    idle_seconds = now - last_used

    # Two-tier health guard:
    # 1. `closed` is a free, synchronous flag that psycopg2 sets when a
    #    connection has been explicitly closed or already errored out.  Check it
    #    unconditionally — it costs no network round-trip.
    # 2. For connections that appear open but may have been silently dropped by
    #    the server (idle SSL close), run a SELECT 1 ping — but only when the
    #    connection has been idle longer than _IDLE_PING_THRESHOLD.  Busy /
    #    recently-returned connections skip this round-trip entirely.
    explicitly_closed = bool(getattr(raw, "closed", 1))
    if explicitly_closed or (
        idle_seconds > _IDLE_PING_THRESHOLD and not _is_conn_alive(raw)
    ):
        # Discard the stale connection and remove its tracking entry so the
        # dict does not hold a reference that prevents GC.
        with _conn_last_used_lock:
            _conn_last_used.pop(raw, None)
        try:
            pool.putconn(raw, close=True)
        except Exception:
            pass
        raw = pool.getconn()
        # The replacement has no entry in _conn_last_used (last_used=0), so if
        # it sits idle longer than the threshold it will be pinged then.

    try:
        yield Conn(raw)
        raw.commit()
    except _RECONNECT_ERRORS:
        # Mid-request connection drop: discard the broken connection so it is
        # not handed to the next caller; re-raise so the request fails loudly.
        _returned = True
        with _conn_last_used_lock:
            _conn_last_used.pop(raw, None)
        try:
            raw.rollback()
        except Exception:
            pass
        try:
            pool.putconn(raw, close=True)
        except Exception:
            pass
        raise
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        if not _returned:
            with _conn_last_used_lock:
                _conn_last_used[raw] = time.monotonic()
            pool.putconn(raw)


def init_db() -> None:
    """Create all tables (idempotent). Called on import of this module."""
    _ensure_pool()


# Run DDL at import so the app starts with all tables present.
init_db()
