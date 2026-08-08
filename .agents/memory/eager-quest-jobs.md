---
name: Eager Quest background jobs
description: Cost and lifecycle rules for the eager Quest job kicked off by every main search.
---

Every main search spawns a background Quest planning job (in-process store, polled by the page).

**Rules:**
- The eager job must fetch field-note briefs cache-only. **Why:** it runs on every search; live brief fetches would add Grok calls to every search's budget (the recycled-pool "zero Grok" test enforces this). Missing notes are filled client-side by the /api/place-brief slot poller; the on-demand retry path still fetches live.
- Job HTML is rendered at poll time, not in the job — share-link helpers need a real Request.
- The job's recycle lookup is skipped when settings.testing (matches Escape recycle) and when YONDER_DISABLE_RECYCLE is set — note this env var IS set in the dev shell, so recycle tests must delenv it.
- Escape's resolved destination goes into plan_quest exclude_dests as a soft preference: same-destination ideas are demoted, never dropped to empty.

**How to apply:** when touching quest planning, search cost accounting, or the quest panel polling flow.
