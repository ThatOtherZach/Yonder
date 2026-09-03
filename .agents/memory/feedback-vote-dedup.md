---
name: Feedback vote dedup
description: How result-feedback thumb votes are deduplicated and which session identity is trusted.
---

Rule: ordinary result votes allow one up and one down per (session, vibe, destination). Canonical Quest votes instead allow one per (session, Quest, direction), regardless of submitted vibe/destination. Both rules use partial database unique indexes; `record_feedback` returns `""` for a duplicate and `None` for MOCK/error.

**Why:** an app-level SELECT-then-INSERT check races under concurrent requests, and treating `None` as "duplicate" masked DB errors as dedup. Including client-controlled descriptive fields in a canonical-object uniqueness key lets callers vary those fields to inflate aggregates. Client-supplied `session_hash` is spoofable, so the endpoint trusts the `yv_sess` cookie, falling back to an IP+UA hash.

**How to apply:** any new abuse/dedup guard should (1) key on a server-derived session identity, (2) enforce uniqueness in the database, (3) derive canonical-object context from the trusted stored row, and (4) distinguish "duplicate" from "failure".
