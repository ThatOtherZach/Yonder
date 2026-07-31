---
name: Affiliate activity pills
description: How GetYourGuide/Viator pills flow into field notes and the invariant every place_book construction site must honor.
---

**Rule:** Affiliate activity pills (`activity_links`) are attached during place-brief enrichment (encyclopedia's `_activity_links` → `yonder.activities`). Any code path that builds a `place_book` dict directly from the brief cache (instead of `get_place_brief`/`briefs_for_stops`) must attach `activity_links` itself, or pills silently disappear on that page.

**Why:** Several routes build place_book dicts straight from `get_cached()` for speed; two such sites in web.py needed manual attachment when the feature shipped. Templates render nothing when the key is absent (by design for unmatched cities), so a missed site fails silently.

Matching is city-name first: an IATA the CSV doesn't list (LGW, HND, ORY…) resolves via airportsdata to its metro city, country-guarded so namesakes (London ON, Sydney NS) never borrow another city's links.

**How to apply:** When adding a new render path that shows field notes, either go through `get_place_brief`/`briefs_for_stops` or call `yonder.activities.activity_links_for(...)` and set `activity_links` on the dict. Picks are random per render (vibe-preferred); pill titles are AI-generated and cached per URL+lang in `activity_titles.db` with CSV SHORTTITLE as fallback — never treat the CSV title as final copy when an AI backend is configured.
