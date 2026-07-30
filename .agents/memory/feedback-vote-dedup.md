---
name: Feedback vote dedup
description: How result-feedback thumb votes are deduplicated and which session identity is trusted.
---

Rule: one up and one down vote max per (session, vibe, destination). Enforced at the DB level by a unique index over `(IFNULL(session_hash,''), vibe, dest_iata, direction)` with `INSERT OR IGNORE`; `record_feedback` returns `""` for a duplicate, `None` for MOCK/error — callers must distinguish the two.

**Why:** an app-level SELECT-then-INSERT check races under concurrent requests, and treating `None` as "duplicate" masked DB errors as dedup (both were review rejections). Client-supplied `session_hash` is spoofable, so the endpoint trusts the `yv_sess` cookie (same id the /saved page sets), falling back to an IP+UA hash.

**How to apply:** any new abuse/dedup guard on write endpoints should (1) key on a server-derived session identity, (2) enforce uniqueness in the database, not just in code, and (3) use distinct return values for "duplicate" vs "failure".
