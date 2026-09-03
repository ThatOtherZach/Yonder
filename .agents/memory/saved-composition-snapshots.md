---
name: Saved composition snapshots
description: The distinction between a public Quest row and a browser-specific bookmarked Quest override during composition.
---

When a traveler composes from Saved, a bookmarked Quest is selected through the owner-scoped bookmark view, not the global Quest row. The selected snapshot may contain personal rescheduled dates or repriced fares; the resulting Quest can still be canonicalized publicly, but that canonicalization must never replace the selected private snapshot.

**Why:** Public Quest rows are shared while date and fare overrides are intentionally private to each browser. Using the global row can silently compose stale travel dates and bypass expiry behavior.

**How to apply:** Resolve bookmarked Quest IDs through the session's bookmark/override loader before validating or composing. Keep personal Detour saves owner-scoped and make retries deterministic from ordered source IDs.