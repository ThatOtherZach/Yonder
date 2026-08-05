"""Session isolation for the Postgres-backed last-search store.

Each browser session (yv_sess cookie) must only ever see its own last-search
snapshot: saving under session A must never leak to session B, and an empty
session_id is a strict no-op / None.

Uses a throwaway PG schema so the real last_search table is never touched.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest

import yonder.last_search as ls

_DDL = """
CREATE TABLE "{schema}".last_search (
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    payload JSONB NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, mode)
)
"""


@pytest.fixture()
def isolated_last_search(monkeypatch):
    """Point yonder.last_search at a throwaway PG schema."""
    import psycopg2

    from yonder.db import Conn

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    schema = f"test_ls_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(_DDL.format(schema=schema))

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    monkeypatch.setattr(ls, "get_conn", _get_conn)
    yield
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


PAYLOAD_A = {"ask": "beach trip", "form": {"origin": "YVR"}}
PAYLOAD_B = {"ask": "city break", "form": {"origin": "JFK"}}


def test_sessions_are_isolated(isolated_last_search):
    ls.save_last("escape", PAYLOAD_A, session_id="sess-a")

    got_a = ls.load_last("escape", session_id="sess-a")
    assert got_a is not None and got_a["ask"] == "beach trip"

    # Session B never sees session A's snapshot
    assert ls.load_last("escape", session_id="sess-b") is None
    assert ls.load_first("escape", session_id="sess-b") is None


def test_empty_session_id_is_noop(isolated_last_search):
    # Writes with no session are dropped, reads return None
    ls.save_last("escape", PAYLOAD_A, session_id="")
    assert ls.load_last("escape", session_id="") is None
    assert ls.load_first("escape", session_id="") is None
    assert ls.load_last("escape", session_id="   ") is None
    # And nothing leaked into any real session
    assert ls.load_last("escape", session_id="sess-a") is None
    # clear with empty session is a safe no-op
    ls.clear_last(None, session_id="")


def test_same_session_roundtrip_and_modes(isolated_last_search):
    ls.save_last("escape", PAYLOAD_A, session_id="sess-a", pin_first=True)
    ls.save_last("detour", PAYLOAD_B, session_id="sess-a")

    esc = ls.load_last("escape", session_id="sess-a")
    det = ls.load_last("detour", session_id="sess-a")
    assert esc and esc["ask"] == "beach trip"
    assert det and det["ask"] == "city break"
    assert esc.get("saved_at")

    # pin_first keeps the first snapshot even after an overwrite
    ls.save_last("escape", PAYLOAD_B, session_id="sess-a", pin_first=True)
    first = ls.load_first("escape", session_id="sess-a")
    assert first and first["ask"] == "beach trip"
    latest = ls.load_last("escape", session_id="sess-a")
    assert latest and latest["ask"] == "city break"


def test_clear_scoped_to_session(isolated_last_search):
    ls.save_last("escape", PAYLOAD_A, session_id="sess-a", pin_first=True)
    ls.save_last("escape", PAYLOAD_B, session_id="sess-b")

    ls.clear_last("escape", session_id="sess-a")
    assert ls.load_last("escape", session_id="sess-a") is None
    assert ls.load_first("escape", session_id="sess-a") is None
    # Other session untouched
    got_b = ls.load_last("escape", session_id="sess-b")
    assert got_b and got_b["ask"] == "city break"

    # clear-all for one session
    ls.save_last("detour", PAYLOAD_A, session_id="sess-b")
    ls.clear_last(None, session_id="sess-b")
    assert ls.load_last("escape", session_id="sess-b") is None
    assert ls.load_last("detour", session_id="sess-b") is None


def test_invalid_mode_ignored(isolated_last_search):
    ls.save_last("bogus", PAYLOAD_A, session_id="sess-a")
    assert ls.load_last("bogus", session_id="sess-a") is None
