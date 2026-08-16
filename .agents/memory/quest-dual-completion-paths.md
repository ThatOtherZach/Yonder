---
name: Quest has two completion paths
description: Any feature reacting to finished Quest results must cover both the eager PG job path and the manual /api/quest/plan path
---

Quest results finish through **two independent paths**: the eager background job (Postgres quest_jobs, polled via the status endpoint) and the manual/retry "Plan a Quest" button (direct POST endpoint returning JSON html).

**Why:** A detour-feed feature wired only into the eager job path was rejected in review — users completing Quest via the visible Plan/Try-again button got none of the new behavior.

**How to apply:** When adding any side effect or extra payload triggered by "quest finished" (harvesting, extra HTML sections, persistence), put the logic in shared helpers and call them from both server paths, and inject results in both client JS handlers (job poller and manual fetch). Stored-snapshot feeds must filter by route + date proximity + max age, never origin alone, and upserts refreshing price must refresh currency in lockstep.
