---
name: Map autosave echo race
description: Server echoes of debounced autosaves must never be applied over newer client mutations.
---

The passport map autosave POSTs the full visited/avoid sets and repaints from the server's normalized echo. If the echo is applied unconditionally, any stamp clicked while a save is in flight gets silently reverted — and the next debounced save persists the reverted state (stamps "stop working" once response latency exceeds click cadence, ~15+ countries).

**Why:** debounce (280ms) + fetch RTT means clicks routinely land between request and response; server normalization itself was always correct.

**How to apply:** any client that repaints from a save echo must guard with a mutation revision counter + request sequence (see `save()` in `yonder/static/country_map.js`) and drop stale responses — the newest save carries fresh state. Same pattern applies to any future echo-repaint endpoints.
