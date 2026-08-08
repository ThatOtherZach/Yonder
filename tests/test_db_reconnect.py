"""Tests: get_conn() survives a stale/dropped Postgres connection.

Simulates the production failure mode — psycopg2.OperationalError when the
managed Postgres closes an idle SSL connection — and asserts that:

1. ``_is_conn_alive`` correctly identifies closed connections.
2. ``get_conn()`` pre-validates the pooled connection and transparently
   replaces a stale one before yielding to the caller.
3. A caller that was about to receive a stale connection gets a working one
   and can execute queries without raising.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import psycopg2
import pytest

import yonder.db as db_mod
from yonder.db import Conn, _DDL, _is_conn_alive


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(url: str) -> str:
    """Create an isolated throwaway PG schema with full DDL; return schema name."""
    schema = f"test_rc_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(_DDL)
    admin.close()
    return schema


def _drop_schema(url: str, schema: str) -> None:
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    admin.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")
    return url


@pytest.fixture()
def isolated_schema(db_url):
    """Throwaway PG schema; yields a ``get_conn``-compatible factory."""
    schema = _make_schema(db_url)

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(db_url, **db_mod._CONNECT_KWARGS)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            raw.close()

    yield _get_conn

    _drop_schema(db_url, schema)


# ---------------------------------------------------------------------------
# Unit tests for _is_conn_alive
# ---------------------------------------------------------------------------

class TestIsConnAlive:
    def test_live_connection_is_alive(self, db_url):
        raw = psycopg2.connect(db_url)
        try:
            assert _is_conn_alive(raw) is True
        finally:
            raw.close()

    def test_closed_connection_is_not_alive(self, db_url):
        raw = psycopg2.connect(db_url)
        raw.close()
        assert _is_conn_alive(raw) is False

    def test_object_with_closed_eq_1_is_not_alive(self):
        fake = MagicMock()
        fake.closed = 1
        assert _is_conn_alive(fake) is False

    def test_cursor_execute_raises_returns_false(self):
        """If execute raises for any reason, connection is treated as dead."""
        bad_cur = MagicMock()
        bad_cur.__enter__ = lambda s: s
        bad_cur.__exit__ = MagicMock(return_value=False)
        bad_cur.execute.side_effect = psycopg2.OperationalError("simulated drop")

        fake_conn = MagicMock()
        fake_conn.closed = 0
        fake_conn.cursor.return_value = bad_cur

        assert _is_conn_alive(fake_conn) is False


# ---------------------------------------------------------------------------
# Integration tests for get_conn() reconnect behaviour
# ---------------------------------------------------------------------------

class TestGetConnReconnect:
    def test_get_conn_works_normally(self, isolated_schema):
        """Baseline: get_conn works when pool is healthy."""
        with patch.object(db_mod, "_ensure_pool"):
            pass  # just verify fixture wires up; use isolated_schema directly
        with isolated_schema() as conn:
            cur = conn.execute("SELECT 1 AS v")
            assert cur.fetchone()["v"] == 1

    def test_stale_connection_replaced_transparently(self, db_url, monkeypatch):
        """Pool hands out a closed connection; get_conn must replace it silently."""
        schema = _make_schema(db_url)
        try:
            # Build a stale (already-closed) connection and a fresh one.
            stale = psycopg2.connect(db_url)
            stale.close()  # simulates server-side SSL idle close
            assert stale.closed, "pre-condition: stale must be closed"

            fresh = psycopg2.connect(db_url)
            with fresh.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            getconn_calls = iter([stale, fresh])

            class FakePool:
                def getconn(self):
                    return next(getconn_calls)

                def putconn(self, conn, close: bool = False):
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            fake_pool = FakePool()
            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: fake_pool)

            # get_conn must detect stale, call putconn(stale, close=True),
            # call getconn() again for fresh, then yield without raising.
            with db_mod.get_conn() as conn:
                cur = conn.execute("SELECT 42 AS answer")
                row = cur.fetchone()

            assert row["answer"] == 42
            assert stale.closed, "stale connection must have been closed/discarded"
        finally:
            fresh.close()
            _drop_schema(db_url, schema)

    def test_stale_connection_not_yielded_to_caller(self, db_url, monkeypatch):
        """The caller never executes against the stale raw connection."""
        schema = _make_schema(db_url)
        try:
            stale = psycopg2.connect(db_url)
            stale.close()

            fresh = psycopg2.connect(db_url)
            with fresh.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            connections_yielded: list = []
            getconn_calls = iter([stale, fresh])

            class FakePool:
                def getconn(self):
                    return next(getconn_calls)

                def putconn(self, conn, close: bool = False):
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            with db_mod.get_conn() as conn:
                connections_yielded.append(conn._raw)

            # The Conn wrapper given to the caller must wrap fresh, not stale.
            assert connections_yielded[0] is fresh
            assert connections_yielded[0] is not stale
        finally:
            fresh.close()
            _drop_schema(db_url, schema)

    def test_mid_request_operational_error_discards_connection(
        self, db_url, monkeypatch
    ):
        """If OperationalError fires mid-request, connection goes to pool with close=True."""
        schema = _make_schema(db_url)
        try:
            raw = psycopg2.connect(db_url)
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            putconn_calls: list[dict] = []

            class FakePool:
                def getconn(self):
                    return raw

                def putconn(self, conn, close: bool = False):
                    putconn_calls.append({"conn": conn, "close": close})
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            with pytest.raises(psycopg2.OperationalError):
                with db_mod.get_conn() as conn:
                    raise psycopg2.OperationalError("simulated mid-request drop")

            # The broken connection must have been returned with close=True
            assert any(c["close"] for c in putconn_calls), (
                "broken connection must be discarded (putconn close=True)"
            )
        finally:
            _drop_schema(db_url, schema)

    def test_non_db_exception_rolls_back_and_reraises(self, db_url, monkeypatch):
        """Non-DB exceptions still roll back and propagate."""
        schema = _make_schema(db_url)
        try:
            raw = psycopg2.connect(db_url)
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            class FakePool:
                def getconn(self):
                    return raw

                def putconn(self, conn, close: bool = False):
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            with pytest.raises(ValueError, match="boom"):
                with db_mod.get_conn() as conn:
                    raise ValueError("boom")
        finally:
            raw.close()
            _drop_schema(db_url, schema)

    def test_ping_skipped_for_recently_used_connection(self, db_url, monkeypatch):
        """Connection returned to pool within threshold is not pinged on recheckout."""
        schema = _make_schema(db_url)
        try:
            raw = psycopg2.connect(db_url)
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            class FakePool:
                def getconn(self):
                    return raw

                def putconn(self, conn, close: bool = False):
                    pass  # don't actually return to pool; keep raw reusable

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            ping_calls: list[object] = []
            original_is_conn_alive = db_mod._is_conn_alive

            def tracking_is_conn_alive(r: object) -> bool:
                ping_calls.append(r)
                return original_is_conn_alive(r)

            monkeypatch.setattr(db_mod, "_is_conn_alive", tracking_is_conn_alive)

            # First checkout/return: stamps last_used on the *object* key.
            with db_mod.get_conn() as conn:
                conn.execute("SELECT 1")
            # The first use may or may not ping (raw has no prior entry → idle=∞).
            # Clear the ping log; only the second checkout matters.
            ping_calls.clear()

            # Second checkout: raw was just returned (<< 30 s ago) → skip ping.
            with db_mod.get_conn() as conn:
                cur = conn.execute("SELECT 1 AS v")
                assert cur.fetchone()["v"] == 1

            assert ping_calls == [], (
                "_is_conn_alive must NOT be called for a connection returned "
                "within the idle threshold"
            )
        finally:
            raw.close()
            _drop_schema(db_url, schema)
            with db_mod._conn_last_used_lock:
                db_mod._conn_last_used.pop(raw, None)

    def test_discard_removes_tracking_entry(self, db_url, monkeypatch):
        """When a connection is discarded (close=True), its tracking entry is removed."""
        schema = _make_schema(db_url)
        try:
            stale = psycopg2.connect(db_url)
            stale.close()  # make it look dropped

            fresh = psycopg2.connect(db_url)
            with fresh.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            getconn_calls = iter([stale, fresh])

            class FakePool:
                def getconn(self):
                    return next(getconn_calls)

                def putconn(self, conn, close: bool = False):
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            # Seed an entry for stale so its idle time appears recent;
            # that lets us confirm the discard path explicitly removes it.
            with db_mod._conn_last_used_lock:
                db_mod._conn_last_used[stale] = time.monotonic()

            # get_conn will still detect stale is dead (closed=True → _is_conn_alive
            # returns False regardless of idle time), discard it, and use fresh.
            with db_mod.get_conn() as conn:
                cur = conn.execute("SELECT 42 AS answer")
                assert cur.fetchone()["answer"] == 42

            with db_mod._conn_last_used_lock:
                assert stale not in db_mod._conn_last_used, (
                    "discarded connection must be removed from _conn_last_used"
                )
        finally:
            fresh.close()
            _drop_schema(db_url, schema)
            with db_mod._conn_last_used_lock:
                db_mod._conn_last_used.pop(stale, None)
                db_mod._conn_last_used.pop(fresh, None)

    def test_new_connection_not_confused_with_discarded_predecessor(
        self, db_url, monkeypatch
    ):
        """A replacement connection must not inherit a recent timestamp from the
        discarded connection it replaced (object-key safety check).

        With integer-id keys, if Python reuses a memory address the new
        connection could silently skip its first health-check.  With
        object-identity keys that is structurally impossible: the old entry is
        removed on discard, and the new object is a distinct key.
        """
        schema = _make_schema(db_url)
        try:
            # first — will be discarded because closed=1.
            first = psycopg2.connect(db_url)
            first.close()

            second = psycopg2.connect(db_url)
            with second.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')

            getconn_calls = iter([first, second])

            class FakePool:
                def getconn(self):
                    return next(getconn_calls)

                def putconn(self, conn, close: bool = False):
                    if close:
                        try:
                            conn.close()
                        except Exception:
                            pass

            monkeypatch.setattr(db_mod, "_ensure_pool", lambda: FakePool())

            # Seed a recent timestamp for first so that — if the code used
            # id() keys — the replacement might incorrectly inherit the skip.
            with db_mod._conn_last_used_lock:
                db_mod._conn_last_used[first] = time.monotonic()

            with db_mod.get_conn() as conn:
                cur = conn.execute("SELECT 99 AS n")
                assert cur.fetchone()["n"] == 99

            with db_mod._conn_last_used_lock:
                # first's entry must be removed (discarded).
                assert first not in db_mod._conn_last_used, (
                    "discarded connection must be removed from _conn_last_used"
                )
                # second must NOT have inherited first's timestamp; it gets its
                # own fresh entry stamped when returned to the pool.
                # The key point: second is a distinct object with its own entry.
                assert second not in db_mod._conn_last_used or (
                    db_mod._conn_last_used.get(second, 0.0)
                    != db_mod._conn_last_used.get(first, -1.0)
                ), "replacement must not inherit predecessor's timestamp"
        finally:
            second.close()
            _drop_schema(db_url, schema)
            with db_mod._conn_last_used_lock:
                db_mod._conn_last_used.pop(first, None)
                db_mod._conn_last_used.pop(second, None)
