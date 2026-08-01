"""Integration test: 'stop off in Tokyo then go to Hong Kong' → two-leg boarding pass.

Verifies the full pipeline:
  intent (decide_shape) → AdventureRequest construction → plan_adventure (leg builder)

Sends the canonical stop-off prompt through:
  1. Intent routing  → must classify as "detour"
  2. Leg builder     → must produce exactly two legs: origin→NRT, NRT→HKG
  3. Vibe check      → the Tokyo stop must carry party-relevant vibe tags
                       (neon / electric) matching the "party" vibe from
                       "looking for some party stuff in Tokyo - clubs"

translate_adventure is bypassed (it needs a live AI key) — exactly as all
other pipeline integration tests do — and the AdventureRequest + StopoverIdea
are built directly from the values translate_adventure would produce.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from yonder.adventure import (
    VIBE_TAG_MAP,
    AdventureRequest,
    StopoverIdea,
    plan_adventure,
)
from yonder.intent import decide_shape, looks_like_stop_off_route


# ---------------------------------------------------------------------------
# The canonical prompt from the task spec
# ---------------------------------------------------------------------------

PROMPT = "stop off in Tokyo then go to Hong Kong, looking for some party stuff in Tokyo - clubs"
SIMPLE_PROMPT = "stop off in Tokyo then go to Hong Kong"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Remove XAI_API_KEY so no live AI calls are attempted during tests."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from yonder.config import get_settings
    monkeypatch.setattr(get_settings(), "xai_api_key", "")


# ---------------------------------------------------------------------------
# Step 1: Intent routing
# ---------------------------------------------------------------------------


class TestStopOffIntentRouting:
    """The prompt must route to 'detour', not 'mix' or 'escape'."""

    def test_full_prompt_is_detour(self):
        d = decide_shape(PROMPT)
        assert d.shape == "detour", (
            f"Expected detour for {PROMPT!r}, got shape={d.shape!r} "
            f"(rationale: {d.rationale!r})"
        )

    def test_simple_prompt_is_detour(self):
        d = decide_shape(SIMPLE_PROMPT)
        assert d.shape == "detour", (
            f"Expected detour for {SIMPLE_PROMPT!r}, got shape={d.shape!r} "
            f"(rationale: {d.rationale!r})"
        )

    def test_stop_off_route_pattern_detected(self):
        assert looks_like_stop_off_route(SIMPLE_PROMPT), (
            f"looks_like_stop_off_route() returned False for {SIMPLE_PROMPT!r}"
        )

    def test_party_vibe_does_not_override_stop_off_routing(self):
        """Even with a comfort vibe the stop-off explicit signal must win."""
        d = decide_shape(PROMPT, vibe="luxury", demo=True)
        assert d.shape == "detour", (
            f"luxury vibe overrode stop-off routing: shape={d.shape!r}"
        )


# ---------------------------------------------------------------------------
# Step 2 + 3: Full pipeline — AdventureRequest → plan_adventure → two legs
# ---------------------------------------------------------------------------


def _make_stop_off_request(origin: str = "YVR") -> AdventureRequest:
    """Build the AdventureRequest that translate_adventure would produce for
    'stop off in Tokyo then go to Hong Kong … party clubs'.

    trip_kind=detour: origin → stop(NRT) → destination(HKG)
    vibe=party: reflects 'party stuff in Tokyo - clubs'
    """
    return AdventureRequest(
        origin=origin,
        destination="HKG",
        depart_date=date(2025, 11, 1),
        vibe="party",
        max_candidates=5,
        include_direct=True,
        trip_kind="detour",
        prompt=PROMPT,
    )


def _tokyo_stopover_idea(stay_days: int = 3) -> StopoverIdea:
    """NRT — the explicit mid-route stop city named in the prompt."""
    return StopoverIdea(
        iata="NRT",
        city="Tokyo",
        stay_days=stay_days,
        why="Explicit stop-off city named in prompt",
        # NRT seed tags — includes 'neon' and 'electric' (party-vibe relevant)
        vibe_tags=["city", "food", "neon", "safe", "whimsical", "electric", "serene"],
        country="JP",
        source="grok",
    )


@pytest.mark.asyncio
async def test_stop_off_produces_exactly_two_legs():
    """plan_adventure() with a detour request and NRT stop must build exactly
    two legs: origin→NRT and NRT→HKG."""
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, (
        "plan_adventure() returned no itineraries for stop-off Tokyo → Hong Kong"
    )
    # There should be exactly one stopover itinerary (one stop idea supplied)
    it = result.itineraries[0]
    assert len(it.legs) == 2, (
        f"Expected exactly 2 legs, got {len(it.legs)}: "
        f"{[(lg.from_iata, lg.to_iata) for lg in it.legs]}"
    )


@pytest.mark.asyncio
async def test_first_leg_destination_is_tokyo_nrt():
    """Leg 1 must end at NRT — the explicit stop city (Tokyo)."""
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    leg1 = result.itineraries[0].legs[0]
    assert leg1.to_iata == "NRT", (
        f"First leg destination must be NRT (Tokyo), got {leg1.to_iata!r}. "
        f"Legs: {[(lg.from_iata, lg.to_iata) for lg in result.itineraries[0].legs]}"
    )


@pytest.mark.asyncio
async def test_second_leg_destination_is_hong_kong_hkg():
    """Leg 2 must end at HKG — the final destination (Hong Kong)."""
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    leg2 = result.itineraries[0].legs[1]
    assert leg2.to_iata == "HKG", (
        f"Second leg destination must be HKG (Hong Kong), got {leg2.to_iata!r}. "
        f"Legs: {[(lg.from_iata, lg.to_iata) for lg in result.itineraries[0].legs]}"
    )


@pytest.mark.asyncio
async def test_stop_iata_and_city_are_tokyo():
    """The itinerary's stop fields must point to Tokyo / NRT."""
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    it = result.itineraries[0]
    assert it.stop_iata == "NRT", (
        f"stop_iata must be NRT, got {it.stop_iata!r}"
    )


@pytest.mark.asyncio
async def test_tokyo_leg_carries_party_vibe_tags():
    """The Tokyo stop must carry party-vibe relevant tags (neon/electric).

    The 'party' vibe maps to {neon, electric, vivid, warmnights} in VIBE_TAG_MAP.
    NRT's tags include 'neon' and 'electric', so the itinerary must reflect these.
    """
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    it = result.itineraries[0]

    party_tags = VIBE_TAG_MAP.get("party", frozenset())
    it_tags = {t.lower() for t in (it.vibe_tags or [])}
    overlap = it_tags & party_tags
    assert overlap, (
        f"Tokyo itinerary carries no party-vibe tags. "
        f"itinerary vibe_tags={sorted(it_tags)}, "
        f"party tag set={sorted(party_tags)}"
    )


@pytest.mark.asyncio
async def test_leg_chain_is_origin_to_nrt_to_hkg():
    """Full chain: origin(YVR) → NRT → HKG — both leg from/to must be correct."""
    req = _make_stop_off_request(origin="YVR")
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    it = result.itineraries[0]
    legs = [(lg.from_iata, lg.to_iata) for lg in it.legs]
    assert legs == [("YVR", "NRT"), ("NRT", "HKG")], (
        f"Expected [('YVR', 'NRT'), ('NRT', 'HKG')], got {legs}"
    )


@pytest.mark.asyncio
async def test_mock_fares_attached_to_both_legs():
    """With include_mock=True, both legs should receive a fare offer (mock price)."""
    req = _make_stop_off_request()
    ideas = [_tokyo_stopover_idea()]

    with patch(
        "yonder.daily_costs.estimate_batch_for_stops",
        new=AsyncMock(return_value={}),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "no itineraries produced"
    it = result.itineraries[0]
    for i, leg in enumerate(it.legs, start=1):
        assert leg.offer is not None, (
            f"Leg {i} ({leg.from_iata}→{leg.to_iata}) has no fare offer with mock provider. "
            f"Error: {leg.error!r}"
        )
    assert it.total_price is not None, (
        "total_price should be set when both legs have mock fare offers"
    )
