"""Smoke tests for has_proximity_intent phrase detection and proximity-mode ranking."""

from datetime import date

import pytest

from yonder.intent import has_proximity_intent


_PROXIMITY_TRUE = [
    "not too far, art museums",
    "somewhere not too far from home",
    "not far, just a weekend",
    "I want somewhere nearby",
    "close to home please",
    "close by would be great",
    "short flight only",
    "looking for a short trip",
    "quick trip this weekend",
    "easy flight preferred",
    "within a few hours of flying",
    "NOT TOO FAR — warm beach",   # uppercase variant
]

_PROXIMITY_FALSE = [
    "art museums in a warm city",
    "I want to go to Tokyo",
    "cheap food vibes somewhere new",
    "adventure in South America",
    "budget beach holiday",
    "I haven't been to Europe yet",
    "fly me somewhere chaotic",
    "long weekend in the mountains",
]


class TestHasProximityIntent:
    @pytest.mark.parametrize("query", _PROXIMITY_TRUE)
    def test_returns_true_for_proximity_phrases(self, query: str) -> None:
        assert has_proximity_intent(query) is True, repr(query)

    @pytest.mark.parametrize("query", _PROXIMITY_FALSE)
    def test_returns_false_for_neutral_queries(self, query: str) -> None:
        assert has_proximity_intent(query) is False, repr(query)

    def test_empty_string_is_false(self) -> None:
        assert has_proximity_intent("") is False

    def test_none_equivalent_empty(self) -> None:
        # The function coerces None-like falsy to empty string via `or ""`
        assert has_proximity_intent("") is False


# ---------------------------------------------------------------------------
# Ranking integration: proximity_mode boosts domestic seeds above foreign ones
# ---------------------------------------------------------------------------

# US domestic hubs used in the mixed candidate list below
_US_PROX_CITIES = {"BOS", "DCA", "ORD", "BNA"}
# Mexican cities that should NOT lead the list when proximity_mode is True
_MX_CITIES = {"MEX", "CUN"}


def _mixed_candidates():
    """Return a fixed mixed seed list: US cities, Mexico, and long-haul international."""
    from yonder.adventure import StopoverIdea

    return [
        StopoverIdea(
            iata="BOS", city="Boston", stay_days=3,
            why="Historic East Coast city",
            vibe_tags=["city", "culture", "safe", "ancient", "nostalgic"],
            country="US",
        ),
        StopoverIdea(
            iata="DCA", city="Washington DC", stay_days=3,
            why="Free world-class museums",
            vibe_tags=["city", "culture", "safe", "ancient", "opulent"],
            country="US",
        ),
        StopoverIdea(
            iata="MEX", city="Mexico City", stay_days=3,
            why="North America detour with huge food culture",
            vibe_tags=["city", "food", "culture", "cheap", "vivid", "hazy"],
            country="MX",
        ),
        StopoverIdea(
            iata="CUN", city="Cancun", stay_days=3,
            why="Beach break",
            vibe_tags=["beach", "relax", "warmnights", "seasalt"],
            country="MX",
        ),
        StopoverIdea(
            iata="IST", city="Istanbul", stay_days=3,
            why="Turkish Airlines hub",
            vibe_tags=["city", "food", "bazaar", "ancient", "nostalgic", "velvet", "vivid", "hazy", "goldenhour", "moody"],
            country="TR",
        ),
        StopoverIdea(
            iata="NRT", city="Tokyo Narita", stay_days=3,
            why="Japan stopover",
            vibe_tags=["city", "food", "neon", "safe", "whimsical", "electric", "serene"],
            country="JP",
        ),
        StopoverIdea(
            iata="LHR", city="London", stay_days=3,
            why="Classic hub",
            vibe_tags=["city", "culture", "moody", "stormy"],
            country="GB",
        ),
    ]


class TestProximityModeBoostRanking:
    """Confirm that proximity_mode=True surfaces US cities ahead of MEX/CUN
    for a zero-stamp user departing a US origin airport."""

    def test_us_city_in_top3_ahead_of_mex_for_zero_stamp_jfk_user(self):
        """proximity_mode=True + no visited countries → at least one US city
        must appear in the top 3 AND rank above Mexico City (MEX) for a
        JFK-origin user.

        Domestic boost (+3 in _sort_by_comfort) is triggered by proximity_mode
        regardless of stamp count; without it a zero-stamp user receives no
        domestic boost and MEX/CUN could lead the list.
        """
        from yonder.adventure import AdventureRequest, _sort_by_comfort

        req = AdventureRequest(
            origin="JFK",
            destination="JFK",
            depart_date=date(2026, 10, 1),
            vibe="culture",
            prompt="not too far, art museums",
            proximity_mode=True,
            visited_countries=[],  # zero-stamp user — no XP-based boost
        )

        ranked = _sort_by_comfort(_mixed_candidates(), req, recent_iatas=set())
        order = [s.iata for s in ranked]

        us_in_top3 = [iata for iata in order[:3] if iata in _US_PROX_CITIES]
        assert us_in_top3, (
            f"No US city in the top 3 for proximity_mode=True zero-stamp JFK user "
            f"(vibe='culture'). Top 3: {order[:3]}, full order: {order}. "
            "Check the domestic boost in _sort_by_comfort."
        )

        # Every US city in the list must rank strictly above MEX
        mex_pos = order.index("MEX") if "MEX" in order else len(order)
        worst_us_pos = max(order.index(i) for i in order if i in _US_PROX_CITIES)
        assert worst_us_pos < mex_pos, (
            f"Not all US cities outrank MEX for proximity_mode=True zero-stamp "
            f"JFK user. Order: {order}. MEX is at position {mex_pos}, "
            f"worst US city is at position {worst_us_pos}."
        )

    def test_all_us_cities_outrank_mex_and_cun(self):
        """Every US domestic city in the mixed list must rank above both MEX
        and CUN for a proximity_mode=True zero-stamp user departing LAX.

        LAX is the origin and is pre-filtered from the candidate list (as
        seed_ideas does). The remaining US hubs (BOS, DCA) each receive
        +3 domestic boost; MEX and CUN do not — so all US entries must
        appear before all Mexican entries in the sorted output.
        """
        from yonder.adventure import AdventureRequest, _sort_by_comfort

        req = AdventureRequest(
            origin="LAX",
            destination="LAX",
            depart_date=date(2026, 10, 1),
            vibe="culture",
            prompt="not too far, art museums",
            proximity_mode=True,
            visited_countries=[],
        )

        # LAX is the origin; exclude it as seed_ideas would.
        candidates = [c for c in _mixed_candidates() if c.iata != "LAX"]
        ranked = _sort_by_comfort(candidates, req, recent_iatas=set())
        order = [s.iata for s in ranked]

        us_positions = {i: order.index(i) for i in order if i in _US_PROX_CITIES}
        mx_positions = {i: order.index(i) for i in order if i in _MX_CITIES}

        assert us_positions, f"No US city survived the sort. Order: {order}"
        assert mx_positions, f"No MX city in the list. Order: {order}"

        best_mx = min(mx_positions.values())
        for us_iata, us_pos in us_positions.items():
            assert us_pos < best_mx, (
                f"US city {us_iata} (pos {us_pos}) does not rank above the "
                f"highest-ranked Mexican city (pos {best_mx}) for "
                f"proximity_mode=True zero-stamp LAX user. Full order: {order}. "
                "Check the domestic boost in _sort_by_comfort."
            )

    def test_no_proximity_mode_zero_stamp_does_not_boost_domestic(self):
        """Without proximity_mode and with zero stamps, the domestic boost must
        NOT fire.

        Negative control: a US city with 1 culture-vibe tag match (ATL → +2 pts)
        must rank *below* a tag-richer international city (IST → +4 pts on the
        culture map) when neither proximity_mode nor visited_countries are set.

        When proximity_mode IS True the +3 domestic boost flips ATL (2+3=5) above
        IST (4). This test confirms that without the flag the boost is absent and
        pure tag-count wins.
        """
        from yonder.adventure import AdventureRequest, StopoverIdea, _sort_by_comfort

        req = AdventureRequest(
            origin="JFK",
            destination="LHR",
            depart_date=date(2026, 10, 1),
            vibe="culture",
            prompt="art museums",
            proximity_mode=False,
            visited_countries=[],  # zero stamps, no proximity → no domestic boost
        )

        # ATL has 1 culture-map tag (culture) → 2 pts without boost.
        # IST has 2 culture-map tags (ancient + nostalgic) → 4 pts without boost.
        # Without the +3 domestic boost IST should lead; with it ATL would.
        atl = StopoverIdea(
            iata="ATL", city="Atlanta", stay_days=3,
            why="Southern food and music hub",
            vibe_tags=["city", "food", "culture", "electric", "warmnights"],
            country="US",
        )
        ist = StopoverIdea(
            iata="IST", city="Istanbul", stay_days=3,
            why="Turkish Airlines hub",
            vibe_tags=["city", "food", "bazaar", "ancient", "nostalgic", "velvet", "vivid", "hazy", "goldenhour", "moody"],
            country="TR",
        )

        ranked = _sort_by_comfort([atl, ist], req, recent_iatas=set())
        top_iata = ranked[0].iata

        assert top_iata == "IST", (
            f"Without proximity_mode and zero stamps, IST (2 culture tags → 4 pts) "
            f"should outrank ATL (1 culture tag → 2 pts, no domestic boost). "
            f"Got top: {top_iata}. "
            "If this flips, the domestic boost may be firing when it should not."
        )


# ---------------------------------------------------------------------------
# Grok-merge path: proximity_mode survives when Grok appends extra candidates
# ---------------------------------------------------------------------------


class TestProximityModePreservedAfterGrokMerge:
    """Confirm that proximity_mode=True still boosts US cities when seed ideas
    and Grok-sourced StopoverIdeas are merged into a single list before the
    sort pass — matching the plan_adventure Grok branch behaviour."""

    def test_us_city_in_top3_of_merged_seed_plus_grok_list(self):
        """Merge seed ideas (contains one US city) with Grok-appended candidates
        (all foreign, tag-rich cities), call _sort_by_comfort once with
        proximity_mode=True, and assert a US city still lands in the top 3.

        Without the domestic +3 boost the tag-rich Grok cities (IST, NRT, MEX)
        would crowd out the US seed.  This test confirms the flag survives a
        second sort pass over the merged list.
        """
        from yonder.adventure import AdventureRequest, StopoverIdea, _sort_by_comfort

        req = AdventureRequest(
            origin="JFK",
            destination="JFK",
            depart_date=date(2026, 10, 1),
            vibe="culture",
            prompt="not too far, something cultural",
            proximity_mode=True,
            visited_countries=[],  # zero-stamp user — no XP-based boost
        )

        # --- Seed ideas (what seed_ideas() would return for a JFK user) ---
        seed = [
            StopoverIdea(
                iata="BOS", city="Boston", stay_days=3,
                why="Historic East Coast city",
                vibe_tags=["city", "culture", "safe", "ancient", "nostalgic"],
                country="US",
            ),
            StopoverIdea(
                iata="DCA", city="Washington DC", stay_days=3,
                why="Free world-class museums",
                vibe_tags=["city", "culture", "safe", "opulent"],
                country="US",
            ),
        ]

        # --- Grok-appended candidates (foreign, tag-rich — likely to crowd out
        #     domestic seeds if proximity_mode is not carried through the merge) ---
        grok_extras = [
            StopoverIdea(
                iata="IST", city="Istanbul", stay_days=3,
                why="Grok pick: crossroads of cultures",
                vibe_tags=["city", "culture", "bazaar", "ancient", "nostalgic",
                           "velvet", "vivid", "hazy", "goldenhour", "moody"],
                country="TR",
            ),
            StopoverIdea(
                iata="NRT", city="Tokyo Narita", stay_days=3,
                why="Grok pick: refined culture scene",
                vibe_tags=["city", "culture", "ancient", "serene", "safe",
                           "neon", "whimsical", "electric"],
                country="JP",
            ),
            StopoverIdea(
                iata="MEX", city="Mexico City", stay_days=3,
                why="Grok pick: vibrant art and food",
                vibe_tags=["city", "culture", "food", "vivid", "ancient", "cheap"],
                country="MX",
            ),
            StopoverIdea(
                iata="CDG", city="Paris", stay_days=3,
                why="Grok pick: classic European culture capital",
                vibe_tags=["city", "culture", "ancient", "opulent", "velvet",
                           "goldenhour", "nostalgic"],
                country="FR",
            ),
        ]

        # Merge exactly as the plan_adventure Grok branch does: seed first,
        # then Grok extras appended, then a single _sort_by_comfort pass.
        merged = seed + grok_extras
        ranked = _sort_by_comfort(merged, req, recent_iatas=set())
        order = [s.iata for s in ranked]

        us_iatas = {"BOS", "DCA"}
        us_in_top3 = [iata for iata in order[:3] if iata in us_iatas]

        assert us_in_top3, (
            f"No US city in the top 3 after merging seed + Grok extras with "
            f"proximity_mode=True (zero-stamp JFK user, vibe='culture'). "
            f"Top 3: {order[:3]}, full order: {order}. "
            "The domestic +3 boost in _sort_by_comfort must fire for US cities "
            "regardless of how many tag-rich foreign Grok candidates are appended."
        )

    def test_proximity_mode_not_diluted_by_large_grok_batch(self):
        """Even when Grok appends a large batch of tag-rich foreign cities, the
        domestic +3 boost must still place at least one US city in the top 3.

        This exercises the case where Grok returns more candidates than the seed
        list (realistic for the re-rank / append branch in plan_adventure).
        """
        from yonder.adventure import AdventureRequest, StopoverIdea, _sort_by_comfort

        req = AdventureRequest(
            origin="ORD",
            destination="ORD",
            depart_date=date(2026, 10, 1),
            vibe="culture",
            prompt="not too far",
            proximity_mode=True,
            visited_countries=[],
        )

        # BNA has 2 culture-map matching tags (culture + ancient → 4 pts raw).
        # The domestic +3 boost brings it to 7 pts, which beats foreign cities
        # that also have 2 culture-map matches (4 pts) but no boost.
        us_seed = StopoverIdea(
            iata="BNA", city="Nashville", stay_days=3,
            why="Seed pick: domestic culture hub",
            vibe_tags=["city", "culture", "ancient", "electric", "warmnights"],
            country="US",
        )

        # Eight Grok-style foreign candidates — each has at most 2 culture-map
        # matching tags (4 pts raw), so they score below BNA's boosted 7 pts.
        grok_extras = [
            StopoverIdea(iata="LHR", city="London", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "moody", "stormy"], country="GB"),
            StopoverIdea(iata="IST", city="Istanbul", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "bazaar", "vivid", "hazy"], country="TR"),
            StopoverIdea(iata="CDG", city="Paris", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "opulent", "velvet", "goldenhour"], country="FR"),
            StopoverIdea(iata="FCO", city="Rome", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "ancient", "opulent"], country="IT"),
            StopoverIdea(iata="BCN", city="Barcelona", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "vivid", "warmnights", "seasalt"], country="ES"),
            StopoverIdea(iata="NRT", city="Tokyo", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "serene", "electric"], country="JP"),
            StopoverIdea(iata="MEX", city="Mexico City", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "vivid", "food", "cheap"], country="MX"),
            StopoverIdea(iata="GRU", city="São Paulo", stay_days=3,
                         why="Grok", vibe_tags=["city", "culture", "electric", "vivid"], country="BR"),
        ]

        merged = [us_seed] + grok_extras
        ranked = _sort_by_comfort(merged, req, recent_iatas=set())
        order = [s.iata for s in ranked]

        assert order[:3].count("BNA") >= 1, (
            f"BNA (the sole US city, 2 culture-map tags → 4 pts raw + 3 boost = 7 pts) "
            f"should appear in the top 3 when proximity_mode=True, even against "
            f"8 Grok foreign cities (each ≤2 culture-map tags → ≤4 pts, no boost). "
            f"Got top 3: {order[:3]}, full order: {order}. "
            "The domestic +3 boost must survive the merge-then-sort path."
        )
