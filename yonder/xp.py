"""Travel comfort XP ranking.

XP = visited_countries × 10 − avoid_countries × 10 (floor 0).
More stamps → higher rank → more comfortable with chaotic/adventurous travel.
"""
from __future__ import annotations

# (min_xp, rank_name, emoji, blurb, countries_approx_label)
RANKS: list[tuple[int, str, str, str, str]] = [
    (0,    "Armchair Explorer",   "🛋️",  "Adventure is a thought experiment for now. The world is waiting.",       "0"),
    (10,   "Day Tripper",         "🗺️",  "A stamp or two on the passport. Detours are still a little spicy.",      "1–4"),
    (50,   "Weekend Wanderer",    "🧳",  "You've seen a few corners. Budget hotels no longer terrify you.",         "5–9"),
    (100,  "Seasoned Traveller",  "✈️",  "Airports are familiar. Bad directions are an adventure, not a crisis.",  "10–19"),
    (200,  "Globe-Trotter",       "🌍",  "Borders are suggestions. Missed connections are stories.",               "20–39"),
    (400,  "Nomadic Soul",        "🏕️",  "Home is wherever your bag lands. Chaos is comfortable.",                 "40–59"),
    (600,  "Expedition Regular",  "🧭",  "You've been to places most people can't find on a map.",                 "60–79"),
    (800,  "Chaos Pilgrim",       "🌀",  "Detours are the plan. The weirder the better.",                          "80–99"),
    (1000, "Chaos Pilot",         "🛸",  "You fly into the unknown and call it Tuesday.",                          "100+"),
]


def compute_xp(visited: list[str], avoid: list[str]) -> dict:
    """Return a profile dict with XP, rank, and progress to the next rank."""
    xp = max(0, len(visited) * 10 - len(avoid) * 10)

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

    return {
        "xp": xp,
        "rank": current[1],
        "rank_emoji": current[2],
        "rank_blurb": current[3],
        "rank_countries": current[4],
        "next_rank": next_rank[1] if next_rank else None,
        "next_rank_emoji": next_rank[2] if next_rank else None,
        "next_rank_xp": next_rank[0] if next_rank else None,
        "progress_pct": progress_pct,
        "xp_to_next": xp_to_next,
        "visited_count": len(visited),
        "avoid_count": len(avoid),
        # Expose full ladder for the "all ranks" tooltip/display
        "ladder": [
            {"xp": r[0], "rank": r[1], "emoji": r[2], "current": i == current_idx}
            for i, r in enumerate(RANKS)
        ],
    }
