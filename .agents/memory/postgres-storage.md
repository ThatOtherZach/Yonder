---
name: Postgres storage layer
description: All server-side app data lives in Replit PostgreSQL through one shared connection module; only user prefs stay as a local SQLite file.
---

All server-side data (including the learning-layer knowledge graph) is in the shared Replit PostgreSQL database, accessed through one pooled connection module exposing a sqlite-style `get_conn()` wrapper (dict rows, `%s` placeholders). All DDL is centralized there and runs idempotently at import.

**Why:** SQLite files were container-local — dev and prod never shared data and deploys wiped them.

**How to apply:**
- New tables: add DDL to the shared db module; never `sqlite3.connect` for server data.
- Postgres gotchas: literal `%` in LIKE patterns must be doubled when params are passed; `GROUP BY` needs aggregates on non-grouped columns; use `ON CONFLICT` instead of `INSERT OR REPLACE/IGNORE`; compute time cutoffs in Python instead of `datetime('now', ...)`.
- Intentionally local: `user_prefs.db` (user-owned prefs) and the two ephemeral JSON caches.
