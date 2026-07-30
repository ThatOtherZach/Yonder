"""Diversity guards for gecko/meltdown ranking (Bangkok dominance).

Bangkok's seed tags overlap 4-5 of the gecko and meltdown vibe tags, making it
the near-certain top result on every search. Two mechanisms keep results fresh:

1. Passport map — filter_ideas drops any city in a visited country, so repeat
   travelers who stamped Thailand never see BKK again (any trip kind).
2. Recent-history decay — _sort_by_comfort applies a mild score decay to
   cities already in the user's recent trip history (saved trips), demoting
   BKK below the next-best fresh city without hiding it entirely.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from yonder.adventure import (
    RECENT_HISTORY_DECAY,
    AdventureRequest,
    SEED_STOPOVERS,
    StopoverIdea,
    _sort_by_comfort,
    filter_ideas,
    seed_ideas,
)


def _req(vibe: str, *, trip_kind: str = "detour", visited: list[str] | None = None) -> AdventureRequest:
    return AdventureRequest(
        origin="YVR",
        destination="YVR" if trip_kind == "getaway" else "LHR",
        depart_date=date(2026, 9, 1),
        vibe=vibe,
        trip_kind=trip_kind,
        visited_countries=visited or [],
    )


def _seed_stopover_ideas() -> list[StopoverIdea]:
    return [
        StopoverIdea(
            iata=row["iata"],
            city=row["city"],
            why=row["why"],
            vibe_tags=list(row.get("vibe_tags") or []),
            country=row.get("country"),
        )
        for row in SEED_STOPOVERS
    ]


# ---------------------------------------------------------------------------
# 1. Passport map: visited Thailand → BKK never surfaces
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vibe", ["gecko", "meltdown"])
@pytest.mark.parametrize("trip_kind", ["detour", "getaway"])
def test_filter_ideas_excludes_bkk_for_thailand_visitors(vibe: str, trip_kind: str) -> None:
    req = _req(vibe, trip_kind=trip_kind, visited=["TH"])
    out = filter_ideas(_seed_stopover_ideas(), req)
    assert out, "filter should still return other candidates"
    assert all(i.iata != "BKK" for i in out)


# ---------------------------------------------------------------------------
# 2. Recent-history decay: BKK in trip history → demoted from #1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vibe", ["gecko", "meltdown"])
def test_bkk_tops_ranking_with_empty_history(vibe: str) -> None:
    """Baseline: with no history, BKK's tag richness makes it the top seed."""
    ranked = _sort_by_comfort(_seed_stopover_ideas(), _req(vibe), recent_iatas=set())
    assert ranked[0].iata == "BKK"


@pytest.mark.parametrize("vibe", ["gecko", "meltdown"])
def test_recent_history_decay_demotes_bkk(vibe: str) -> None:
    ranked = _sort_by_comfort(
        _seed_stopover_ideas(), _req(vibe), recent_iatas={"BKK"}
    )
    assert ranked[0].iata != "BKK", "BKK in recent history must not stay #1"
    # BKK is demoted, not hidden — it still appears somewhere in the list.
    assert any(i.iata == "BKK" for i in ranked)


@pytest.mark.parametrize("vibe", ["gecko", "meltdown"])
def test_seed_ideas_uses_saved_history_decay(vibe: str) -> None:
    """seed_ideas picks up recent history lazily from saved trips."""
    with patch("yonder.adventure._recent_history_iatas", return_value={"BKK"}):
        ideas = seed_ideas(_req(vibe))
    assert ideas
    assert ideas[0].iata != "BKK"


def test_recent_history_lookup_failure_is_safe() -> None:
    """A broken saves DB must never break ranking — decay silently no-ops."""
    with patch(
        "yonder.saved.saved_destination_iatas", side_effect=RuntimeError("boom")
    ):
        ranked = _sort_by_comfort(_seed_stopover_ideas(), _req("gecko"))
    assert ranked[0].iata == "BKK"


def test_decay_is_mild() -> None:
    """Decay stays a nudge (a couple of tag matches), not a ban."""
    assert 1 <= RECENT_HISTORY_DECAY <= 6
