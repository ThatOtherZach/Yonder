"""Travel comfort XP ranking — square kilometres unlocked.

XP = total km² of visited tiles on the tiled world map (see yonder.tiles):
subdivision tiles for continent-scale countries credit their full land
area, single-tile countries credit their land area, and a country-level
entry for a subdivided country credits one average region ("some
coverage").  Raw km² by explicit design — no log scaling or dampening.

The avoid list no longer subtracts XP (ground claimed can't be un-claimed
by disliking a place); it is still shown alongside the profile.

Tier thresholds are calibrated in km² so travellers land in a sensible
tier relative to the old country-count ladder (a typical country is worth
a few hundred thousand km², so old "5–9 countries" travellers now land at
Weekend Wanderer or above rather than regressing).  The later milestones
keep adding reachable steps all the way toward world-scale coverage.
"""
from __future__ import annotations

from yonder.tiles import unlocked_km2, visited_countries_from_tiles

# (min_km2, rank_name, emoji, blurb, km2_approx_label)
RANKS: list[tuple[int, str, str, str, str]] = [
    (0,          "Armchair Explorer",   "🛋️",  "Adventure is a thought experiment for now. The world is waiting.",       "0 km²"),
    (100,        "Day Tripper",         "🗺️",  "A stamp or two on the passport. Detours are still a little spicy.",      "100+ km²"),
    (10_000,     "Local Roamer",        "🥾",   "The map has started to fill in. Nearby adventures count.",               "10K+ km²"),
    (25_000,     "Weekend Wanderer",    "🧳",  "You've seen a few corners. Budget hotels no longer terrify you.",         "25K+ km²"),
    (100_000,    "City Hopper",         "🚆",  "A long weekend can cover serious ground. Keep the passport handy.",       "100K+ km²"),
    (250_000,    "Seasoned Traveller",  "✈️",  "Airports are familiar. Bad directions are an adventure, not a crisis.",  "250K+ km²"),
    (500_000,    "Passport Pro",        "🎫",  "You know the shortcuts, the good layovers, and the quiet gate.",          "500K+ km²"),
    (1_000_000,  "Globe-Trotter",       "🌍",  "Borders are suggestions. Missed connections are stories.",               "1M+ km²"),
    (2_000_000,  "Border Drifter",      "🛂",  "Borders blur when the next good story is just over the line.",             "2M+ km²"),
    (3_000_000,  "Nomadic Soul",        "🏕️",  "Home is wherever your bag lands. Chaos is comfortable.",                 "3M+ km²"),
    (6_000_000,  "Expedition Regular",  "🧭",  "You've been to places most people can't find on a map.",                 "6M+ km²"),
    (9_000_000,  "Waypoint Whisperer",  "📍",  "Your internal compass has opinions, and they are usually right.",          "9M+ km²"),
    (12_000_000, "Chaos Pilgrim",       "🌀",  "Detours are the plan. The weirder the better.",                          "12M+ km²"),
    (18_000_000, "Detour Legend",       "🏴‍☠️", "You take the scenic route so often it became the main route.",            "18M+ km²"),
    (25_000_000, "Chaos Pilot",         "🛸",  "You fly into the unknown and call it Tuesday.",                          "25M+ km²"),
    (45_000_000, "Horizon Chaser",      "⛵",  "The horizon is a starting line. There is always more beyond it.",         "45M+ km²"),
    (52_000_000,  "World Roamer",        "🌐",  "Continents feel like neighborhoods when you keep moving.",                "52M+ km²"),
    (64_000_000,  "Atlas Keeper",        "📚",  "Your map has stories in every margin and a few in the binding.",           "64M+ km²"),
    (72_000_000,  "Planetary Local",    "🪐",  "The whole planet feels strangely familiar. You know where to turn.",      "72M+ km²"),
    (80_000_000,  "Worldwise Wayfarer", "🛰️",  "Almost nowhere is unknown now, but the best detours remain.",             "80M+ km²"),
]


def format_km2(v: int | float) -> str:
    """Compact human label for a km² value: 12,450 → '12,450'; big → '1.2M'."""
    v = int(round(v))
    if v >= 10_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    return f"{v:,}"


def compute_xp(visited: list[str], avoid: list[str]) -> dict:
    """Return a profile dict with km² XP, rank, and progress to the next rank.

    *visited* is a tile list (subdivision codes and/or plain country
    codes); legacy country-only lists work unchanged.
    """
    xp = unlocked_km2(visited or [])

    current_idx = 0
    for i, (threshold, *_) in enumerate(RANKS):
        if xp >= threshold:
            current_idx = i

    current = RANKS[current_idx]
    next_rank = RANKS[current_idx + 1] if current_idx + 1 < len(RANKS) else None

    if next_rank:
        span = next_rank[0] - current[0]
        earned = xp - current[0]
        progress_pct = min(99, int(earned / span * 100))
        xp_to_next = next_rank[0] - xp
    else:
        progress_pct = 100
        xp_to_next = 0

    countries = visited_countries_from_tiles(visited or [])

    return {
        "xp": xp,
        "km2": xp,
        "km2_label": format_km2(xp),
        "rank": current[1],
        "rank_emoji": current[2],
        "rank_blurb": current[3],
        "rank_countries": current[4],
        "next_rank": next_rank[1] if next_rank else None,
        "next_rank_emoji": next_rank[2] if next_rank else None,
        "next_rank_xp": next_rank[0] if next_rank else None,
        "progress_pct": progress_pct,
        "xp_to_next": xp_to_next,
        "xp_to_next_label": format_km2(xp_to_next),
        "visited_count": len(countries),
        "tile_count": len(visited or []),
        "avoid_count": len(avoid or []),
        # Expose full ladder for the "all ranks" tooltip/display
        "ladder": [
            {
                "xp": r[0],
                "threshold_label": r[4],
                "rank": r[1],
                "emoji": r[2],
                "blurb": r[3],
                "current": i == current_idx,
            }
            for i, r in enumerate(RANKS)
        ],
    }
