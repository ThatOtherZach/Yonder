---
name: Save before navigation
description: Reliability rule for browser save actions that navigate after persistence.
---

Never navigate away from a page until its save request has returned a confirmed successful response. Do not use fallback navigation timers while a write is in flight.

**Why:** Page navigation can cancel a slow fetch, so the interface appears to save while the destination page has no persisted record.

**How to apply:** Establish the session on the page GET before rendering a save action, especially in embedded previews. Send same-origin credentials explicitly, restore the action on timeout or error, and navigate only after the server reports success.