---
name: Trip reschedule ordering
description: Durable persistence and fare-snapshot rules for changing saved travel dates.
---

When a saved itinerary is moved to new dates, persist the complete date shift before attempting live repricing. Remove fare offers, totals, display prices, and date-bound booking URLs from the shifted snapshot before it reaches the provider fallback path.

**Why:** Provider timeouts, quota gates, and empty results must not undo the traveler’s date choice or make an old-date fare/link appear valid for the new schedule.

**How to apply:** Any Saved, Quest, or future itinerary date editor should shift all supported date fields with one offset, save that fare-empty result first, then layer only genuinely live repricing results onto it.