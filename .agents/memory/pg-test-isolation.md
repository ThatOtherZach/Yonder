---
name: PG test isolation via throwaway schema
description: How tests isolate Postgres-backed modules without touching real tables
---

Tests for modules that use the shared Postgres `get_conn()` must not read/write the real dev tables.

**Why:** All server data lives in one Replit PG database; polluting `route_knowledge` etc. from tests corrupts real learning-layer state and makes tests order-dependent.

**How to apply:** Create a throwaway schema (`CREATE SCHEMA test_x_<uuid>`), create only the tables the module needs inside it, and monkeypatch the module-level `get_conn` name (modules import it as `from yonder.db import get_conn`, so patch `yonder.<module>.get_conn`) with a contextmanager that opens a fresh psycopg2 connection, `SET search_path TO <schema>`, and wraps it in `yonder.db.Conn`. Drop the schema in fixture teardown. See `tests/test_repeat_search_route_knowledge.py` for a working fixture. Note: older tests that monkeypatch a `DB_PATH` sqlite attribute predate the Postgres move and are broken.
