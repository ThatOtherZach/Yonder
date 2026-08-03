---
name: Tiled km² progression
description: Rules for the tile map / km²-unlocked XP system — coverage semantics, migration, and suppression behavior.
---
XP is raw km² of visited tiles (yonder/tiles.py registry + static/tiles_admin1.json geometry). Whitelist US/CA/MX/BR/AU/GB(4 nations) subdivide; everything else is one ISO2 tile.

**Rules to stay consistent with:**
- Country-level entry on a subdivided country = "some coverage": credits ONE average region's area, never blocks that country from getaway suggestions.
- Only a FULL subdivision sweep marks a subdivided country as "seen" for the visited filter (see fully_visited_countries).
- Legacy visited-country lists migrate to country-level tiles, never expanded to all regions.
- `visited_tiles` pref is source of truth; `visited_countries` kept in sync in stamp order (first stamp still resolves home IATA).
- Rank names/emoji are stable; only thresholds (km²) change. Avoid list no longer subtracts XP.

**Why:** explicit product decisions in the tiled-map task; changing any rule silently would corrupt users' XP and filter behavior.
**How to apply:** any feature touching visited data, XP, or getaway suppression must go through yonder/tiles.py helpers rather than treating visited lists as country sets.

## Retired regions (Aug 2026)
Subdivision whitelist is US/CA/GB only. MX/BR/AU region tiles are RETIRED: any stored/incoming `MX-*`/`BR-*`/`AU-*` code collapses to the plain country tile (visited → country visited with full km²; avoided → country avoid; visited wins). Collapse lives in `collapse_retired_region_prefs` (yonder/tiles.py), applied lazily+persisted on user_prefs read, and `normalize_tile_list` is read-tolerant of stale codes.
