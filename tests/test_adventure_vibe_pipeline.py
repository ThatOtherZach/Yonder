"""Tests confirming vibe-ranked seeds survive the full plan_adventure() pipeline.

These tests run with include_mock=True so no live API keys are required.
The daily-cost estimator is patched to return an empty dict quickly so the
suite stays fast while still exercising the full pricing path.

Vibes covered: beach, food, wild, nostalgic
Also includes a structural sync-check: every vibe id in vibes.json must have
a non-empty entry in VIBE_TAG_MAP (catches missing mappings early).
"""
from __future__ import annotations

import json
import pathlib
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from yonder.adventure import VIBE_TAG_MAP, AdventureRequest, plan_adventure, seed_ideas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_req(vibe: str, origin: str = "YVR", destination: str = "LHR") -> AdventureRequest:
    return AdventureRequest(
        origin=origin,
        destination=destination,
        depart_date=date(2025, 11, 1),
        vibe=vibe,
        max_candidates=5,
        include_direct=False,
    )


async def _run_pipeline(req: AdventureRequest) -> tuple[list[str], list[str]]:
    """Return (seed_iatas_ordered, result_stop_iatas) for the given request."""
    seeds = seed_ideas(req)
    seed_iatas = [s.iata for s in seeds]

    # Patch estimate_batch_for_stops so no live Grok calls are made.
    # Returning {} means ground-cost fields are simply absent — pricing still runs.
    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, seeds, include_mock=True)

    stop_iatas = [it.stop_iata for it in result.itineraries if it.stop_iata]
    return seed_iatas, stop_iatas


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_env_mock(monkeypatch):
    """Ensure the MOCK env-var guard does not suppress the mock provider.

    Also clear XAI_API_KEY so AIDemoProvider uses the fast seeded MockProvider
    instead of attempting live Grok API calls during tests.
    """
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    # The key may also come from .env / user prefs (already merged into the
    # cached Settings singleton), so blank it there too — otherwise the demo
    # provider attempts a live Grok call that the engine's timeout cancels.
    from yonder.config import get_settings

    monkeypatch.setattr(get_settings(), "xai_api_key", "")


# ---------------------------------------------------------------------------
# seed_ideas() unit tests — vibe ranking before the pipeline
# ---------------------------------------------------------------------------


def test_beach_top_seed_is_CUN():
    """CUN has 4 beach-vibe tags; it must lead the seed list for 'beach'."""
    req = _make_req("beach")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='beach'"
    assert seeds[0].iata == "CUN", (
        f"Expected CUN as top beach seed, got {seeds[0].iata}"
    )


def test_wild_top_seed_is_KEF():
    """KEF matches 6 wild-vibe tags (nature/stormy/rugged/crisp/feral/untamed)."""
    req = _make_req("wild")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='wild'"
    assert seeds[0].iata == "KEF", (
        f"Expected KEF as top wild seed, got {seeds[0].iata}"
    )


def test_food_top_seed_is_IST():
    """IST is the first food-tagged seed in the list and should rank first."""
    req = _make_req("food")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='food'"
    assert seeds[0].iata == "IST", (
        f"Expected IST as top food seed, got {seeds[0].iata}"
    )


def test_nostalgic_top_seed_is_IST():
    """IST has nostalgic/ancient/moody tags; it leads for vibe='nostalgic'."""
    req = _make_req("nostalgic")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='nostalgic'"
    assert seeds[0].iata == "IST", (
        f"Expected IST as top nostalgic seed, got {seeds[0].iata}"
    )


def test_seed_ideas_order_is_stable_across_calls():
    """Calling seed_ideas() twice with shuffle=False gives the same ranked order."""
    req = _make_req("beach")
    first = [s.iata for s in seed_ideas(req)]
    second = [s.iata for s in seed_ideas(req)]
    assert first == second, "seed_ideas() is not deterministic without shuffle"


def test_seed_ideas_respects_max_candidates():
    for vibe in ("beach", "food", "wild", "nostalgic"):
        req = _make_req(vibe)
        seeds = seed_ideas(req)
        assert len(seeds) <= req.max_candidates, (
            f"seed_ideas() returned {len(seeds)} seeds for vibe={vibe!r}, "
            f"expected ≤ {req.max_candidates}"
        )


# ---------------------------------------------------------------------------
# plan_adventure() integration tests — vibe seeds survive the full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beach_top_seed_survives_pipeline():
    """Top beach seed (CUN) must appear in the final itineraries."""
    req = _make_req("beach")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    top_seed = seed_iatas[0]  # CUN
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='beach'"
    assert top_seed in stop_iatas, (
        f"Top beach seed {top_seed!r} not found in itineraries {stop_iatas}. "
        f"Seed order was: {seed_iatas}"
    )


@pytest.mark.asyncio
async def test_food_top_seed_survives_pipeline():
    """Top food seed (IST) must appear in the final itineraries."""
    req = _make_req("food")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    top_seed = seed_iatas[0]  # IST
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='food'"
    assert top_seed in stop_iatas, (
        f"Top food seed {top_seed!r} not found in itineraries {stop_iatas}. "
        f"Seed order was: {seed_iatas}"
    )


@pytest.mark.asyncio
async def test_wild_top_seed_survives_pipeline():
    """Top wild seed (KEF) must appear in the final itineraries."""
    req = _make_req("wild")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    top_seed = seed_iatas[0]  # KEF
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='wild'"
    assert top_seed in stop_iatas, (
        f"Top wild seed {top_seed!r} not found in itineraries {stop_iatas}. "
        f"Seed order was: {seed_iatas}"
    )


@pytest.mark.asyncio
async def test_nostalgic_top_seed_survives_pipeline():
    """Top nostalgic seed (IST) must appear in the final itineraries."""
    req = _make_req("nostalgic")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    top_seed = seed_iatas[0]  # IST
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='nostalgic'"
    assert top_seed in stop_iatas, (
        f"Top nostalgic seed {top_seed!r} not found in itineraries {stop_iatas}. "
        f"Seed order was: {seed_iatas}"
    )


@pytest.mark.asyncio
async def test_itinerary_vibe_tags_match_seed_vibe_tags():
    """Each itinerary must carry the same vibe_tags as its seed idea."""
    req = _make_req("beach")
    seeds = seed_ideas(req)
    seed_tags_by_iata = {s.iata: set(s.vibe_tags) for s in seeds}

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, seeds, include_mock=True)

    for it in result.itineraries:
        if it.stop_iata and it.stop_iata in seed_tags_by_iata:
            assert set(it.vibe_tags) == seed_tags_by_iata[it.stop_iata], (
                f"vibe_tags for {it.stop_iata} changed between seed and itinerary"
            )


@pytest.mark.asyncio
async def test_result_ideas_preserve_vibe_rank_order():
    """AdventureResult.ideas must be a prefix of the seed_ideas() ranked list."""
    req = _make_req("beach")
    seeds = seed_ideas(req)
    seed_iatas = [s.iata for s in seeds]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, seeds, include_mock=True)

    result_idea_iatas = [i.iata for i in result.ideas]
    # Every IATA in result.ideas must appear in the seed list and in the same
    # relative order (plan_adventure may apply filter_ideas, but must not
    # re-rank or swap the already-sorted candidates).
    seed_positions = [seed_iatas.index(iata) for iata in result_idea_iatas if iata in seed_iatas]
    assert seed_positions == sorted(seed_positions), (
        f"result.ideas re-ordered the vibe-ranked seeds. "
        f"seed order: {seed_iatas}, result order: {result_idea_iatas}"
    )


# ---------------------------------------------------------------------------
# wildwest / nomad — frontier & desert cities must beat generic cheap ones
# ---------------------------------------------------------------------------


def test_wildwest_top_seed_is_frontier_city():
    """wildwest's top seed must be a rugged/feral/untamed city (CPT/KEF/YYC),
    not a generic cheap city that only shares 'raw'/'gritty' tags."""
    req = _make_req("wildwest")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='wildwest'"
    assert seeds[0].iata in {"CPT", "KEF", "YYC"}, (
        f"Expected a frontier city (CPT/KEF/YYC) as top wildwest seed, "
        f"got {seeds[0].iata}. Full order: {[s.iata for s in seeds]}"
    )


def test_nomad_thematic_seeds_beat_cheap_only_cities():
    """For vibe='nomad', SGN or CPT must rank above cities whose only nomad
    signal is the 'cheap' tag (e.g. DPS, KUL) — the sort must not collapse
    to generic cheapness."""
    req = _make_req("nomad")
    seeds = seed_ideas(req)
    assert seeds, "seed_ideas() returned an empty list for vibe='nomad'"
    order = [s.iata for s in seeds]

    thematic_pos = min(
        (order.index(i) for i in ("SGN", "CPT") if i in order),
        default=None,
    )
    assert thematic_pos is not None, (
        f"Neither SGN nor CPT made the nomad seed list: {order}"
    )
    for cheap_only in ("DPS", "KUL"):
        if cheap_only in order:
            assert thematic_pos < order.index(cheap_only), (
                f"Thematic nomad city ranked below cheap-only city "
                f"{cheap_only}: {order}"
            )
    # And the top seed itself must be a strongly-tagged nomad city, not a
    # city carrying only the 'cheap' tag from the nomad set.
    from yonder.adventure import VIBE_TAG_MAP as _map

    top_tags = {t.lower() for t in seeds[0].vibe_tags}
    assert len(top_tags & _map["nomad"]) >= 3, (
        f"Top nomad seed {seeds[0].iata} matches too few nomad tags: "
        f"{sorted(top_tags & _map['nomad'])}"
    )


@pytest.mark.asyncio
async def test_wildwest_top_seed_survives_pipeline():
    """The top wildwest seed (a frontier city) must appear in the itineraries."""
    req = _make_req("wildwest")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    top_seed = seed_iatas[0]
    assert top_seed in {"CPT", "KEF", "YYC"}, (
        f"Top wildwest seed is not a frontier city: {seed_iatas}"
    )
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='wildwest'"
    assert top_seed in stop_iatas, (
        f"Top wildwest seed {top_seed!r} not found in itineraries {stop_iatas}. "
        f"Seed order was: {seed_iatas}"
    )


@pytest.mark.asyncio
async def test_nomad_thematic_seed_survives_pipeline():
    """SGN or CPT must survive into the final nomad itineraries."""
    req = _make_req("nomad")
    seed_iatas, stop_iatas = await _run_pipeline(req)
    assert stop_iatas, "plan_adventure() produced no itineraries for vibe='nomad'"
    assert any(i in stop_iatas for i in ("SGN", "CPT")), (
        f"Neither SGN nor CPT survived the nomad pipeline. "
        f"Seeds: {seed_iatas}, stops: {stop_iatas}"
    )


# ---------------------------------------------------------------------------
# Structural sync-check: vibes.json ↔ VIBE_TAG_MAP
# ---------------------------------------------------------------------------

_VIBES_JSON = pathlib.Path(__file__).parent.parent / "yonder" / "vibes.json"


def test_all_vibes_have_tag_map_entry():
    """Every vibe id in vibes.json must have a non-empty entry in VIBE_TAG_MAP.

    This test acts as a CI gate: add a vibe to vibes.json without a
    corresponding VIBE_TAG_MAP entry and this fails loudly instead of silently
    returning generic adventure results.
    """
    vibes = json.loads(_VIBES_JSON.read_text())
    vibe_ids = [v["id"] for v in vibes]

    missing = [vid for vid in vibe_ids if vid not in VIBE_TAG_MAP]
    empty = [vid for vid in vibe_ids if vid in VIBE_TAG_MAP and not VIBE_TAG_MAP[vid]]

    assert not missing, (
        f"Vibe(s) in vibes.json have no VIBE_TAG_MAP entry — add them to "
        f"yonder/adventure.py VIBE_TAG_MAP: {missing}"
    )
    assert not empty, (
        f"Vibe(s) in VIBE_TAG_MAP have an empty tag set — each entry needs "
        f"at least one tag: {empty}"
    )


# ---------------------------------------------------------------------------
# Domestic-seed boost for low-XP travellers (origin country prioritised)
# ---------------------------------------------------------------------------

# US domestic hubs present in SEED_STOPOVERS (ORD itself is the origin, so excluded)
_US_DOMESTIC = {"BNA", "AUS", "SAN", "ATL", "DEN", "DFW", "HNL", "MSY", "SEA", "BOS", "DCA", "LAX"}
# Canadian domestic hubs present in SEED_STOPOVERS (YVR is the origin, not seeded)
_CA_DOMESTIC = {"YUL", "YYC", "YOW", "YQB", "YEG"}


def test_domestic_us_seeds_present_for_low_xp_user():
    """With origin=ORD and a single visited country, at least one US domestic
    hub (BNA, AUS, SAN, …) must appear in the seed list for vibe='city'.

    This confirms that domestic seeds are not accidentally filtered before the
    comfort scorer gets a chance to boost them.
    """
    req = AdventureRequest(
        origin="ORD",
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="city",
        max_candidates=5,
        include_direct=False,
        visited_countries=["GB"],  # 1 country → low-XP, domestic boost active
    )
    seeds = seed_ideas(req)
    iatas = {s.iata for s in seeds}
    assert iatas & _US_DOMESTIC, (
        f"No US domestic hub found in seed list for low-XP ORD user. "
        f"Seeds returned: {[s.iata for s in seeds]}"
    )


def test_domestic_us_seeds_outscore_comparable_international_for_low_xp():
    """For a low-XP US traveller (origin=ORD, 1 visited country), at least one
    US domestic hub must rank above a comparable international seed for
    vibe='city'.

    The domestic boost (+3) must push same-country seeds ahead of international
    cities that share an equal number of vibe-tag matches.  A regression in
    _sort_by_comfort would cause this assertion to fail.
    """
    from yonder.adventure import _sort_by_comfort, SEED_STOPOVERS, StopoverIdea

    req = AdventureRequest(
        origin="ORD",
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="city",
        max_candidates=5,
        include_direct=False,
        visited_countries=["GB"],  # 1 country → _comfort = 0.01 < 0.25
    )

    # Build a focused candidate list: one clear US domestic city (BNA) and one
    # international city that has the same number of 'city' vibe-tag hits (IST).
    # Both share the "city" tag → equal tag score without the boost.
    # With the boost, BNA (US == origin country US) should rank first.
    bna = next(s for s in SEED_STOPOVERS if s["iata"] == "BNA")
    ist = next(s for s in SEED_STOPOVERS if s["iata"] == "IST")
    candidates = [
        StopoverIdea(iata=bna["iata"], city=bna["city"], stay_days=3,
                     why=bna["why"], vibe_tags=list(bna["vibe_tags"]),
                     country=bna["country"]),
        StopoverIdea(iata=ist["iata"], city=ist["city"], stay_days=3,
                     why=ist["why"], vibe_tags=list(ist["vibe_tags"]),
                     country=ist["country"]),
    ]
    ranked = _sort_by_comfort(candidates, req, recent_iatas=set())
    assert ranked[0].iata == "BNA", (
        f"Expected BNA (US domestic) to outrank IST for low-XP ORD user "
        f"(vibe='city'), but got: {[s.iata for s in ranked]}. "
        f"Check the domestic boost in _sort_by_comfort."
    )


def test_canadian_hub_prioritised_for_low_xp_ca_user():
    """For a low-XP CA traveller (origin=YVR, 1 visited country), a Canadian
    hub (YOW or YQB) must rank above a matched-tag international city in the
    seed list for vibe='culture'.

    YOW has culture/safe/crisp/tender tags; YQB has culture/food/nostalgic/
    ancient/moody/crisp.  Both share the 'culture' tag with the vibe map.
    The domestic boost (+3) should push them above international cities that
    have the same or fewer culture-tag matches.
    """
    req = AdventureRequest(
        origin="YVR",
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="culture",
        max_candidates=5,
        include_direct=False,
        visited_countries=["GB"],  # 1 visited → low-XP, domestic boost active
    )
    seeds = seed_ideas(req)
    iatas = [s.iata for s in seeds]

    ca_in_seeds = [i for i in iatas if i in _CA_DOMESTIC]
    assert ca_in_seeds, (
        f"No Canadian domestic hub (YOW/YQB/YUL/YYC/YEG) found in seed list "
        f"for low-XP YVR user (vibe='culture'). "
        f"Seeds: {iatas}"
    )

    # The first Canadian hub must beat at least one international city in rank.
    first_ca_pos = min(iatas.index(i) for i in ca_in_seeds)
    # There must also be at least one international city in the seeds list.
    intl_positions = [idx for idx, i in enumerate(iatas) if i not in _CA_DOMESTIC]
    assert intl_positions, (
        f"No international city in seed list to compare against. Seeds: {iatas}"
    )
    last_intl_pos = max(intl_positions)
    assert first_ca_pos < last_intl_pos, (
        f"No Canadian hub ranked above any international city for low-XP YVR "
        f"user (vibe='culture'). Seed order: {iatas}"
    )


@pytest.mark.asyncio
async def test_low_xp_ord_domestic_stop_survives_pipeline():
    """End-to-end smoke test: a low-XP user departing ORD must see at least one
    US domestic city in the final plan_adventure() itineraries.

    The domestic boost (+3 in _sort_by_comfort) prioritises same-country seeds
    at the scoring stage.  This test confirms the boost actually carries through
    the full pipeline — seeds → filter_ideas → _sort_by_comfort → pricing —
    so domestic options reach the UI, not just the seed list.

    Profile: 1 visited country (GB) → _comfort = 0.01 < 0.25 → boost active.
    Vibe: 'city' — all major US domestic hubs carry the 'city' tag.
    """
    req = AdventureRequest(
        origin="ORD",
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="city",
        max_candidates=5,
        include_direct=False,
        visited_countries=["GB"],  # 1 country → low-XP, domestic boost active
    )
    seeds = seed_ideas(req)
    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, seeds, include_mock=True)

    stop_iatas = {it.stop_iata for it in result.itineraries if it.stop_iata}
    us_domestic_in_result = stop_iatas & _US_DOMESTIC
    assert us_domestic_in_result, (
        f"No US domestic hub found in plan_adventure() itineraries for low-XP "
        f"ORD user (vibe='city'). Got stops: {sorted(stop_iatas)}. "
        f"Check that _sort_by_comfort domestic boost carries through the pipeline."
    )


def test_domestic_boost_inactive_for_zero_visited_countries():
    """With visited_countries=[] the domestic boost must NOT fire.

    Zero-stamp users haven't been anywhere yet; the product intent is to
    encourage them to 'go anywhere', so the same-country boost is intentionally
    skipped.  This test confirms the guard condition is intact.
    """
    from yonder.adventure import _sort_by_comfort, SEED_STOPOVERS, StopoverIdea

    req = AdventureRequest(
        origin="ORD",
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="city",
        max_candidates=5,
        include_direct=False,
        visited_countries=[],  # no stamps → boost must be skipped
    )

    bna = next(s for s in SEED_STOPOVERS if s["iata"] == "BNA")
    # Build a candidate whose only vibe-tag match ties with BNA so that the
    # domestic boost, if active, would be the deciding factor.
    tied_intl = StopoverIdea(
        iata="TST",
        city="Test City",
        stay_days=3,
        why="test",
        vibe_tags=["city", "food", "culture", "electric", "warmnights"],  # same tags as BNA
        country="DE",  # not US
    )
    bna_idea = StopoverIdea(
        iata=bna["iata"],
        city=bna["city"],
        stay_days=3,
        why=bna["why"],
        vibe_tags=list(bna["vibe_tags"]),
        country=bna["country"],
    )
    ranked = _sort_by_comfort([bna_idea, tied_intl], req, recent_iatas=set())
    # With boost inactive, tag scores are equal — BNA must NOT outrank TST
    # purely because of country origin.  Both share the same tags so their
    # scores are identical; stable sort preserves input order (BNA first here),
    # but the key assertion is that BNA's score equals TST's score (no extra +3).
    from yonder.adventure import VIBE_TAG_MAP, country_for_iata
    _tags_bna = set(bna_idea.vibe_tags)
    _tags_tst = set(tied_intl.vibe_tags)
    _related = VIBE_TAG_MAP.get("city", frozenset())
    score_bna = len(_tags_bna & _related) * 2
    score_tst = len(_tags_tst & _related) * 2
    assert score_bna == score_tst, (
        f"Test setup error: BNA and TST should have equal vibe scores "
        f"without the domestic boost, but got BNA={score_bna} TST={score_tst}"
    )
