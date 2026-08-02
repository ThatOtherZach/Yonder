"""Tests confirming plan_adventure() prices only one idea per search
and falls through to the next candidate only when the top idea fails.

Task 401: Cut flight-API usage to one priced result per trip type.

Test strategy
-------------
All tests patch ``yonder.adventure._price_leg`` with a pure stub that returns
PricedLeg objects directly — never calling the real implementation — so there
is no recursion, no live API calls, and no dependence on mock-provider setup.

PricedLeg semantics used here:
  - success:  PricedLeg(offer=FlightOffer(price=300.0, currency="CAD", ...))
              → both legs have valid offers → total_price is set (priced card)
  - failure:  PricedLeg(error="no route")
              → price_stopover's error-path branch fires → total_price=None

IMPORTANT: plan_adventure() internally re-ranks ideas via _sort_by_comfort
before pricing, so tests must NOT assert which specific IATA is chosen first.
They should instead track call order and assert on counts / relative ordering.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from yonder.adventure import (
    AdventureRequest,
    PricedLeg,
    StopoverIdea,
    plan_adventure,
)
from yonder.types import FlightOffer


def _detour_req(
    origin: str = "YVR",
    destination: str = "LHR",
    vibe: str = "adventure",
) -> AdventureRequest:
    return AdventureRequest(
        origin=origin,
        destination=destination,
        depart_date=date(2025, 11, 1),
        vibe=vibe,
        trip_kind="detour",
    )


def _idea(iata: str, city: str, country: str = "JP") -> StopoverIdea:
    return StopoverIdea(iata=iata, city=city, stay_days=3, why="test", country=country)


def _live_offer(currency: str = "CAD") -> FlightOffer:
    """Return a minimal live-priced FlightOffer."""
    return FlightOffer(
        provider="testair",
        price=300.0,
        currency=currency,
        price_kind="live",
        display_price="CA$300",
        display_price_base="CA$300",
    )


def _ok_leg(origin: str, dest: str, dep: date) -> PricedLeg:
    """PricedLeg with a real offer — price_stopover's happy path."""
    return PricedLeg(from_iata=origin, to_iata=dest, depart_date=dep, offer=_live_offer())


def _fail_leg(origin: str, dest: str, dep: date) -> PricedLeg:
    """PricedLeg with an error — price_stopover's error-path (total_price=None)."""
    return PricedLeg(from_iata=origin, to_iata=dest, depart_date=dep, error="no route")


# ---------------------------------------------------------------------------
# 1. Normal path: only the top idea is priced (pricing stops at first success)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_top_idea_priced_when_it_succeeds():
    """When the first (top-ranked) idea prices successfully exactly one itinerary
    is returned and pricing stops — subsequent ideas must NOT have their legs called.

    Note: plan_adventure() re-ranks ideas internally before pricing, so we do
    not assert which specific IATA ends up as the stop.  We assert on call count
    (exactly 2 _price_leg calls = 1 idea × 2 legs) and result count (exactly 1).
    """
    req = _detour_req()
    ideas = [
        _idea("NRT", "Tokyo", "JP"),
        _idea("SIN", "Singapore", "SG"),
        _idea("BKK", "Bangkok", "TH"),
    ]
    priced_routes: list[tuple[str, str]] = []

    async def _stub_price_leg(origin, dest, depart, _req, **kw):
        priced_routes.append((origin, dest))
        return _ok_leg(origin, dest, depart)

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_stub_price_leg),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert len(result.itineraries) == 1, (
        f"Expected exactly 1 itinerary, got {len(result.itineraries)}: "
        f"{[it.stop_iata for it in result.itineraries]}"
    )
    # Exactly 2 _price_leg calls: the 2 legs of the single top-ranked idea.
    # If pricing continued to further ideas there would be 4 or 6 calls.
    assert len(priced_routes) == 2, (
        f"Expected exactly 2 leg calls (1 idea × 2 legs), "
        f"got {len(priced_routes)}: {priced_routes}"
    )


# ---------------------------------------------------------------------------
# 2. Fallback path: top idea fails (no live fares) → second idea is tried
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_idea_fails_falls_through_to_second_idea():
    """When the first-ranked idea's legs return errors (no route), the next idea
    in the list is tried and its priced itinerary becomes the single result.

    We simulate failure by failing the first 2 _price_leg calls (= the two legs
    of whichever idea is ranked first after internal re-ranking), then succeeding
    on call 3+ (the second idea's legs).  This avoids hardcoding which IATA wins.
    """
    req = _detour_req()
    ideas = [
        _idea("NRT", "Tokyo", "JP"),
        _idea("SIN", "Singapore", "SG"),
    ]
    call_num: list[int] = [0]
    first_stop: list[str | None] = [None]

    async def _ordered_stub(origin, dest, depart, _req, **kw):
        call_num[0] += 1
        if call_num[0] == 1:
            # First leg of the first idea — remember the stop IATA (= dest)
            first_stop[0] = dest
        if call_num[0] <= 2:
            # Both legs of the first idea fail (no route)
            return _fail_leg(origin, dest, depart)
        # Legs of the fallback idea succeed
        return _ok_leg(origin, dest, depart)

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_ordered_stub),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, (
        "Expected a priced fallback itinerary when the first idea has no flights"
    )
    assert len(result.itineraries) == 1, (
        f"Expected exactly 1 itinerary after fallback, got {len(result.itineraries)}"
    )
    assert result.itineraries[0].total_price is not None, (
        "Expected the fallback itinerary to be priced"
    )
    # The priced stop must differ from the first-tried stop (which failed)
    assert result.itineraries[0].stop_iata != first_stop[0], (
        f"Expected fallback to a different stop than {first_stop[0]!r}, "
        f"got {result.itineraries[0].stop_iata!r}"
    )


# ---------------------------------------------------------------------------
# 3. All ideas fare-missing: last card is surfaced so the panel isn't blank
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_ideas_fare_missing_surfaces_last_card():
    """When every idea fails (legs return errors), the last attempted itinerary
    is still surfaced so the UI renders 'No direct flights' CTAs rather than
    showing a completely blank Detour panel.

    Rescue does NOT fire because the itineraries list is now non-empty after
    the fare-missing fallback card is added.
    """
    req = _detour_req()
    ideas = [
        _idea("NRT", "Tokyo", "JP"),
        _idea("SIN", "Singapore", "SG"),
    ]

    async def _always_fail(origin, dest, depart, _req, **kw):
        return _fail_leg(origin, dest, depart)

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_always_fail),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    # Must have exactly 1 card (the last fare-missing attempt)
    assert len(result.itineraries) == 1, (
        f"Expected 1 fare-missing card, got {len(result.itineraries)}"
    )
    assert result.itineraries[0].total_price is None, (
        "Expected the surfaced card to be fare-missing (no total_price)"
    )
    # Rescue must NOT fire since the itineraries list is non-empty
    rescue_cards = [it for it in result.itineraries if it.rescue]
    assert not rescue_cards, (
        f"Rescue fired even though a fare-missing card was already present: {rescue_cards}"
    )


# ---------------------------------------------------------------------------
# 4. Multi-stop chain success skips the single-idea loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_stop_chain_success_skips_single_idea_loop():
    """When a 2+ item named_stop_chain prices successfully, the single-idea loop
    must be entirely skipped — no extra cards are added."""
    req = _detour_req(vibe="adventure")
    chain = [_idea("NRT", "Tokyo", "JP"), _idea("SIN", "Singapore", "SG")]
    single_ideas = [_idea("BKK", "Bangkok", "TH")]

    priced_routes: list[tuple[str, str]] = []

    async def _stub_price_leg(origin, dest, depart, _req, **kw):
        priced_routes.append((origin, dest))
        return _ok_leg(origin, dest, depart)

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_stub_price_leg),
    ):
        result = await plan_adventure(
            req, single_ideas,
            named_stop_chain=chain,
            include_mock=True,
        )

    # Multi-stop chain should have produced a result
    assert result.itineraries, "Expected multi-stop chain to produce an itinerary"
    # BKK (the single idea) must NOT appear since the multi-stop chain succeeded
    bkk_routes = [(o, d) for o, d in priced_routes if "BKK" in (o, d)]
    assert not bkk_routes, (
        f"Single-idea BKK was priced even though multi-stop chain succeeded. "
        f"All routes priced: {priced_routes}"
    )
