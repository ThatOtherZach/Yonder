---
name: Quest AI latency budget
description: Quest's per-panel Grok call is slow (50-70s); timeout budgets must respect this
---

Quest's on-demand planner call (3 full open-jaw itineraries from grok-4.5) routinely takes 50–70 s — far slower than Detour/Escape, which use the unified/recycled paths.

**Why:** With a 45 s server budget + 50 s httpx read timeout, Quest failed almost every run with `httpx.ReadTimeout('')` — whose `str()` is empty, so the UI showed "couldn't reach the AI planner — " with no detail. Looked like a connectivity bug; was purely a latency budget bug.

**How to apply:**
- Keep the latency chain ordered: frontend Skip prompt (~45 s) < server `asyncio.wait_for` (80 s) < GrokClient httpx read timeout (90 s). Whoever shrinks one must shrink the ones before it.
- When catching httpx errors for user-facing messages, use `repr(exc)` fallback — many httpx exceptions stringify to "".
- Don't add retry loops to compensate for a too-short timeout; fix the budget instead.
