---
name: Import-time DDL vs multi-worker gunicorn
description: Why schema DDL at module import crashed production and how it must be run
---
Production runs gunicorn with 4 workers; each imports the app and formerly ran the full schema DDL unsynchronised, racing in Postgres ("tuple concurrently updated" / duplicate pg_type) and killing workers at import. Dev (single uvicorn process) never reproduces it.

**Rule:** any schema DDL run at startup must be serialised with a `pg_advisory_lock` on a **dedicated non-pooled connection that is closed on any failure** (closing releases the session lock — never return a lock-holding connection to the pool), with a bounded `lock_timeout`, retries, and a fallback that only continues if a sentinel-table schema check passes.

**Why:** an unguarded DDL race took prod fully down (Aug 2026); a leaked session advisory lock in the pool would deadlock all future boots.

**How to apply:** keep this logic in `_run_ddl` in the shared DB module; when reproducing prod boot issues, run the exact gunicorn command with `-w=4` locally — single-process import tests hide the race.
