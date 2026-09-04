"""Regression: the Ground Spend block is the same on Escape, Detour and Quest.

Escape offers and Quest ideas historically carried no ground-cost fields at
all, so their cards silently rendered no budget block while Detour cards did.
These tests pin down that:

  - all three card types render the shared ``.bp-budget-strip`` markup once
    their model carries ground data;
  - Escape no longer falls back to the old ``pb-col`` grid blocks;
  - a Detour card shows the "already a full loop" note in place of a builder
    button, because a closed loop cannot be composed any further;
  - combining two cities sums their ground totals instead of quoting one.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import yonder.web as web_module

_DEPART = date.today() + timedelta(days=30)
_RETURN = date.today() + timedelta(days=40)


def _render(template_src: str, **ctx) -> str:
    return web_module.templates.env.from_string(template_src).render(**ctx)


def _strip_html(html: str) -> str:
    """Just the rendered budget strips — excludes JSON islands the JS reads."""
    import re

    return "\n".join(
        re.findall(
            r'<div class="bp-budget-strip".*?</div>\s*</div>', html, flags=re.S
        )
    )


def _ground_fields() -> dict[str, Any]:
    return {
        "ground_daily_stop": 90.0,
        "ground_daily_origin": 120.0,
        "ground_total": 900.0,
        "ground_display": "+~USD 900 (~USD 90 per Day for 10 Days)",
        "ground_compare_line": "Hanoi ~USD 80/day x 5 - Bangkok ~USD 100/day x 5",
        "ground_budget_status": "under",
        "ground_budget_line": "Ground plan ~USD 900",
        "all_in_display": "~USD 1,800",
    }


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------


def test_escape_card_renders_shared_budget_strip():
    from yonder.types import FlightOffer, SearchQuery

    offer = FlightOffer(
        provider="duffel",
        price=900.0,
        currency="USD",
        stops_out=0,
        price_kind="live",
        display_price_base="~USD 900",
        **_ground_fields(),
    )
    query = SearchQuery(
        origin="YVR",
        destination="HAN",
        depart_date=_DEPART,
        return_date=_RETURN,
        adults=1,
        currency="USD",
    )
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0) }}",
        o=offer,
        query=query,
    )
    assert html.count('class="bp-budget-strip"') == 1
    assert "+~USD 900 (~USD 90 per Day for 10 Days)" in html
    assert "~USD 1,800" in html
    # The old bespoke grid blocks are gone — one shared strip everywhere.
    assert "pb-col" not in html
    assert "Trip Estimate" not in html


def test_mock_fares_never_show_an_all_in_total():
    """Mock fares are internal skeletons — a mock price must never reach a user."""
    from yonder.types import FlightOffer, SearchQuery

    offer = FlightOffer(
        provider="mock",
        price=900.0,
        currency="USD",
        stops_out=0,
        price_kind="mock",
        **_ground_fields(),
    )
    query = SearchQuery(
        origin="YVR",
        destination="HAN",
        depart_date=_DEPART,
        return_date=_RETURN,
        adults=1,
        currency="USD",
    )
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0) }}",
        o=offer,
        query=query,
    )
    # Ground cost is real and still shown; the fare-derived total is not.
    assert "bp-budget-strip" in html
    assert "+~USD 900 (~USD 90 per Day for 10 Days)" in html
    assert "~USD 1,800" not in _strip_html(html)
    assert 'data-col-allin=""' in html


def test_escape_card_without_ground_data_renders_no_strip():
    from yonder.types import FlightOffer, SearchQuery

    offer = FlightOffer(
        provider="mock", price=900.0, currency="USD", stops_out=0, price_kind="mock"
    )
    query = SearchQuery(
        origin="YVR",
        destination="HAN",
        depart_date=_DEPART,
        adults=1,
        currency="USD",
    )
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0) }}",
        o=offer,
        query=query,
    )
    assert "bp-budget-strip" not in html


def test_quest_card_renders_shared_budget_strip():
    from yonder.adventure import QuestIdea

    idea = QuestIdea(
        entry_iata="HAN",
        exit_iata="BKK",
        entry_city="Hanoi",
        exit_city="Bangkok",
        currency="USD",
        depart_date=_DEPART,
        outbound_date=_RETURN,
        total_price=900.0,
        **_ground_fields(),
    )
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.quest_card('explore', idea, 0, home_iata='YVR') }}",
        idea=idea,
    )
    assert html.count('class="bp-budget-strip"') == 1
    assert "Hanoi ~USD 80/day x 5 - Bangkok ~USD 100/day x 5" in html


def test_quest_card_hides_all_in_when_a_fare_is_missing():
    from yonder.adventure import QuestIdea

    idea = QuestIdea(
        entry_iata="HAN",
        exit_iata="BKK",
        entry_city="Hanoi",
        exit_city="Bangkok",
        currency="USD",
        depart_date=_DEPART,
        outbound_date=_RETURN,
        inbound_fare_missing=True,
        **_ground_fields(),
    )
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.quest_card('explore', idea, 0, home_iata='YVR') }}",
        idea=idea,
    )
    assert "bp-budget-strip" in html
    assert "all-in est." not in html


def _detour_it(**extra: Any):
    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.types import FlightOffer

    def leg(src: str, dst: str, days: int) -> Any:
        return PricedLeg(
            from_iata=src,
            to_iata=dst,
            depart_date=_DEPART + timedelta(days=days),
            offer=FlightOffer(
                provider="mock",
                price=400.0,
                currency="USD",
                stops_out=0,
                price_kind="mock",
            ),
        )

    return AdventureItinerary(
        kind="stopover",
        title="Hanoi Detour",
        total_price=800.0,
        currency="USD",
        stop_iata="HAN",
        stop_city="Hanoi",
        stay_days=7,
        vibe_tags=["adventure"],
        legs=[leg("YVR", "HAN", 0), leg("HAN", "YVR", 7)],
        **extra,
    )


def test_detour_card_shows_loop_note_instead_of_builder_button():
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0, det_vibe='adventure') }}",
        it=_detour_it(),
    )
    assert "bp-loop-note" in html
    assert "Already a full loop" in html
    # A Detour returns home, so it can never be an input to the builder.
    assert "saved-compose-select" not in html
    assert 'data-compose-select="detour"' not in html


def test_detour_card_still_renders_its_budget_strip():
    html = _render(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('share', it, 0) }}",
        it=_detour_it(**_ground_fields()),
    )
    assert html.count('class="bp-budget-strip"') == 1
    assert "Daily Spend" in html


# ---------------------------------------------------------------------------
# Cost combination
# ---------------------------------------------------------------------------


def _compare(daily_stop: float, ground_total: float, status: str) -> Any:
    from yonder.daily_costs import DailyCostCompare

    return DailyCostCompare(
        origin_cc="CA",
        stop_cc="VN",
        origin_name="Canada",
        stop_name="Vietnam",
        daily_origin=120.0,
        daily_stop=daily_stop,
        currency="USD",
        stay_days=5,
        ground_total=ground_total,
        delta_per_day=0.0,
        source="fallback",
        blurb="",
        note_lines=[],
        display_daily_origin="~USD 120",
        display_daily_stop=f"~USD {daily_stop:.0f}",
        display_ground=f"~USD {ground_total:.0f}",
        display_delta="",
        budget_status=status,
        budget_line=f"Ground plan ~USD {ground_total:.0f}",
    )


def test_combined_ground_sums_both_cities():
    from yonder.daily_costs import _combine_ground_fields

    fields = _combine_ground_fields(
        [
            ("Hanoi", 5, _compare(80.0, 400.0, "under")),
            ("Bangkok", 5, _compare(100.0, 500.0, "under")),
        ],
        currency="USD",
    )
    assert fields["ground_total"] == 900.0
    assert fields["ground_daily_stop"] == 90.0
    assert "Hanoi" in fields["ground_compare_line"]
    assert "Bangkok" in fields["ground_compare_line"]
    assert fields["ground_budget_status"] == "under"


def test_combined_ground_takes_the_worst_budget_verdict():
    from yonder.daily_costs import _combine_ground_fields

    fields = _combine_ground_fields(
        [
            ("Hanoi", 5, _compare(80.0, 400.0, "under")),
            ("Bangkok", 5, _compare(300.0, 1500.0, "over")),
        ],
        currency="USD",
    )
    assert fields["ground_budget_status"] == "over"


def test_saving_an_escape_projects_its_ground_block_onto_the_itinerary():
    """Saved Escapes render via detour_card, which reads the itinerary, not the offer."""
    from yonder.saved import escape_offer_to_itinerary

    it = escape_offer_to_itinerary(
        query={
            "origin": "YVR",
            "destination": "HAN",
            "depart_date": _DEPART.isoformat(),
            "currency": "USD",
        },
        offer={
            "provider": "duffel",
            "price": 900.0,
            "currency": "USD",
            "price_kind": "live",
            **_ground_fields(),
        },
    )
    assert it["ground_display"] == "+~USD 900 (~USD 90 per Day for 10 Days)"
    assert it["ground_total"] == 900.0
    assert it["all_in_display"] == "~USD 1,800"


def test_saving_an_escape_without_ground_data_adds_no_empty_keys():
    from yonder.saved import escape_offer_to_itinerary

    it = escape_offer_to_itinerary(
        query={"origin": "YVR", "destination": "HAN", "currency": "USD"},
        offer={"provider": "duffel", "price": 900.0, "currency": "USD"},
    )
    assert "ground_display" not in it
    assert "all_in_display" not in it


def test_one_day_two_city_quest_is_not_charged_two_days():
    """Splitting N days across two cities must add back up to exactly N."""
    import asyncio

    from yonder.adventure import QuestIdea, attach_quest_ground
    from yonder.config import get_settings

    idea = QuestIdea(
        entry_iata="HAN",
        exit_iata="BKK",
        entry_city="Hanoi",
        exit_city="Bangkok",
        currency="USD",
        depart_date=_DEPART,
        outbound_date=_DEPART + timedelta(days=1),
    )
    out = asyncio.run(
        attach_quest_ground(idea, get_settings(), home_iata="YVR", vibe="adventure")
    )
    assert out.ground_display is not None
    assert "for 1 Days" in out.ground_display
    # One night priced, not two.
    assert out.ground_total == out.ground_daily_stop


def test_multi_day_quest_split_covers_every_day_exactly_once():
    import asyncio

    from yonder.adventure import QuestIdea, attach_quest_ground
    from yonder.config import get_settings

    idea = QuestIdea(
        entry_iata="HAN",
        exit_iata="BKK",
        entry_city="Hanoi",
        exit_city="Bangkok",
        currency="USD",
        depart_date=_DEPART,
        outbound_date=_DEPART + timedelta(days=9),
    )
    out = asyncio.run(
        attach_quest_ground(idea, get_settings(), home_iata="YVR", vibe="adventure")
    )
    assert "for 9 Days" in (out.ground_display or "")


def test_composition_refuses_to_total_a_legacy_mock_fare():
    """Legacy Saved rows predate mock normalization — composing one must not price it."""
    from yonder.adventure import PricedLeg
    from yonder.composition import _usable_price
    from yonder.types import FlightOffer

    def leg(price_kind: str) -> Any:
        return PricedLeg(
            from_iata="YVR",
            to_iata="HAN",
            depart_date=_DEPART,
            offer=FlightOffer(
                provider="mock",
                price=450.0,
                currency="USD",
                stops_out=0,
                price_kind=price_kind,
                fare_missing=False,  # legacy row: flag never set
            ),
        )

    assert _usable_price(leg("live")) is True
    assert _usable_price(leg("cached")) is True
    assert _usable_price(leg("mock")) is False
    assert _usable_price(leg("sandbox")) is False


def test_ground_attachment_skips_all_in_for_mock_legs():
    import asyncio

    from yonder.adventure import PricedLeg, QuestIdea, attach_quest_ground
    from yonder.config import get_settings
    from yonder.types import FlightOffer

    def leg(src: str, dst: str) -> Any:
        return PricedLeg(
            from_iata=src,
            to_iata=dst,
            depart_date=_DEPART,
            offer=FlightOffer(
                provider="mock",
                price=450.0,
                currency="USD",
                stops_out=0,
                price_kind="mock",
                fare_missing=False,
            ),
        )

    idea = QuestIdea(
        entry_iata="HAN",
        exit_iata="BKK",
        entry_city="Hanoi",
        exit_city="Bangkok",
        currency="USD",
        depart_date=_DEPART,
        outbound_date=_RETURN,
        total_price=900.0,
        inbound_leg=leg("YVR", "HAN"),
        outbound_leg=leg("BKK", "YVR"),
    )
    out = asyncio.run(
        attach_quest_ground(idea, get_settings(), home_iata="YVR", vibe="adventure")
    )
    assert out.ground_display is not None  # ground cost is real
    assert out.all_in_display is None  # the fare-derived total is not


def test_single_city_keeps_the_per_stop_wording():
    from yonder.daily_costs import _combine_ground_fields

    fields = _combine_ground_fields(
        [("Hanoi", 7, _compare(80.0, 560.0, "under"))], currency="USD"
    )
    assert fields["ground_display"] == "+~USD 560 (~USD 80 per Day for 7 Days)"
