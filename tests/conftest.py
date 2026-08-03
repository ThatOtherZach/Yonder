"""Shared pytest fixtures for PostgreSQL-backed module isolation.

All yonder modules that previously used SQLite now share the Replit PostgreSQL
instance via ``yonder.db.get_conn()``.  Tests that need isolation use the
``pg_schema`` fixture, which creates a throwaway schema, runs the full DDL,
patches ``get_conn`` in every affected module, and drops the schema on teardown.

Usage in a test module:
    @pytest.fixture(autouse=True)
    def _isolated(pg_schema, monkeypatch):
        monkeypatch.delenv("MOCK", raising=False)
        yield
"""
from __future__ import annotations

import importlib
import os
import uuid
from contextlib import contextmanager

import psycopg2
import pytest

from yonder.db import Conn, _DDL

# Modules that bind ``get_conn`` at import time via ``from yonder.db import get_conn``
_PG_MODULES = [
    "yonder.vibe_signals",
    "yonder.feedback",
    "yonder.encyclopedia",
    "yonder.saved",
    "yonder.share",
    "yonder.fare_estimates",
    "yonder.history",
    "yonder.knowledge",
]


@pytest.fixture()
def pg_schema(monkeypatch):
    """Create a throwaway PG schema with full DDL; patch get_conn in all modules.

    Yields the patched ``get_conn`` callable so tests can open direct connections
    to the test schema:

        with pg_schema() as conn:
            conn.execute("SELECT ...")
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(_DDL)

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
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

    # Patch every module that bound get_conn at import time
    for mod_name in _PG_MODULES:
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "get_conn", _get_conn)

    # ai_usage imports get_conn lazily (inside function bodies); patch yonder.db
    import yonder.db as db_mod
    monkeypatch.setattr(db_mod, "get_conn", _get_conn)

    yield _get_conn

    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()
