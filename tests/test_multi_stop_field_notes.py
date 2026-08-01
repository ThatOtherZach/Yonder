"""Confirm multi-stop field notes load for every hop, not just the first stop.

Two areas are covered:

1. ``stops_from_itineraries`` (encyclopedia.py) — must return an entry for
   *every* intermediate IATA in a kind=multi-stop itinerary, not only the
   first stop.

2. Template (_boarding_pass.html detour_card) — a multi-stop boarding-pass
   card must render a ``field-note-slot`` element whose ``data-iata`` matches
   *each* intermediate stop's IATA code, so the client-side polling machinery
   can fill every slot as briefs arrive.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import yonder.web as web_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_macro(template_src: str, **ctx) -> str:
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _minimal_offer() -> Any:
    from yonder.types import FlightOffer

    return FlightOffer(
        provider="mock",
        price=350.0,
        currency="USD",
        airlines=["JL"],
        stops_out=0,
        price_kind="mock",
        display_price="~USD 350",
        display_price_base="~USD 350",
    )


def _multi_stop_itinerary(stop_iatas: list[str]) -> Any:
    """Build a minimal AdventureItinerary of kind='multi-stop' with the given
    intermediate stops.  ``stop_iata`` is set to the first stop for backward
    compat; the full ``stops`` list carries all of them."""
    from yonder.adventure import AdventureItinerary, PricedLeg

    legs = []
    all_iatas = ["YVR"] + stop_iatas + ["BKK"]
    depart = date.today() + timedelta(days=30)
    for i, (frm, to) in enumerate(zip(all_iatas, all_iatas[1:])):
        legs.append(
            PricedLeg(
                from_iata=frm,
                to_iata=to,
                depart_date=depart + timedelta(days=i * 3),
                offer=_minimal_offer(),
            )
        )

    stops_list = [
        {"iata": iata, "country": "JP", "city": f"City-{iata}"}
        for iata in stop_iatas
    ]

    return AdventureItinerary(
        kind="multi-stop",
        title="YVR → " + " → ".join(stop_iatas) + " → BKK",
        total_price=350.0 * len(legs),
        currency="USD",
        stop_iata=stop_iatas[0] if stop_iatas else None,
        stop_city=f"City-{stop_iatas[0]}" if stop_iatas else None,
        stay_days=3,
        why="Multi-hop adventure",
        vibe_tags=["adventure"],
        legs=legs,
        stops=stops_list,
        theme_primary="#e6b450",
        theme_label="Adventure",
    )


def _detour_html(it: Any) -> str:
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='t') }}",
        it=it,
    )


def _slot_iatas(html: str) -> list[str]:
    """Return all data-iata values from field-note-slot elements."""
    return re.findall(
        r'class="[^"]*field-note-slot[^"]*"[^>]*data-iata="([^"]*)"',
        html,
    ) + re.findall(
        r'data-iata="([^"]*)"[^>]*class="[^"]*field-note-slot[^"]*"',
        html,
    )


# ===========================================================================
# Suite 1 — stops_from_itineraries returns all intermediate IATAs
# ===========================================================================


class TestStopsFromItineraries:
    """Unit-tests for encyclopedia.stops_from_itineraries."""

    def _run(self, itineraries: list[Any]) -> list[tuple]:
        from yonder.encyclopedia import stops_from_itineraries

        return stops_from_itineraries(itineraries)

    def test_single_stop_stopover_returns_one_entry(self):
        it = _multi_stop_itinerary(["NRT"])
        # Override kind to stopover for this specific test
        it.kind = "stopover"
        it.stops = []
        result = self._run([it])
        assert len(result) == 1
        assert result[0][0] == "NRT"

    def test_multi_stop_two_stops_returns_both(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        result = self._run([it])
        iatas = [r[0] for r in result]
        assert "NRT" in iatas, f"NRT missing from {iatas}"
        assert "SIN" in iatas, f"SIN missing from {iatas}"
        assert len(iatas) == 2

    def test_multi_stop_three_stops_returns_all_three(self):
        it = _multi_stop_itinerary(["NRT", "SIN", "DXB"])
        result = self._run([it])
        iatas = [r[0] for r in result]
        assert "NRT" in iatas
        assert "SIN" in iatas
        assert "DXB" in iatas
        assert len(iatas) == 3

    def test_multi_stop_order_preserved(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        result = self._run([it])
        iatas = [r[0] for r in result]
        assert iatas.index("NRT") < iatas.index("SIN"), (
            f"Expected NRT before SIN, got order: {iatas}"
        )

    def test_multi_stop_no_duplicates(self):
        it = _multi_stop_itinerary(["NRT", "NRT", "SIN"])
        result = self._run([it])
        iatas = [r[0] for r in result]
        assert iatas.count("NRT") == 1, f"Duplicate NRT in {iatas}"

    def test_rescue_kind_also_extracts_all_stops(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        it.kind = "rescue"
        result = self._run([it])
        iatas = [r[0] for r in result]
        assert "NRT" in iatas
        assert "SIN" in iatas

    def test_empty_stops_list_returns_empty(self):
        it = _multi_stop_itinerary([])
        result = self._run([it])
        assert result == []

    def test_none_itineraries_returns_empty(self):
        from yonder.encyclopedia import stops_from_itineraries

        assert stops_from_itineraries(None) == []

    def test_country_and_city_preserved(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        result = self._run([it])
        # Each tuple is (iata, country, city)
        by_iata = {r[0]: r for r in result}
        assert by_iata["NRT"][1] == "JP"
        assert by_iata["NRT"][2] == "City-NRT"

    def test_limit_respected(self):
        from yonder.encyclopedia import stops_from_itineraries

        it = _multi_stop_itinerary(["NRT", "SIN", "DXB", "LHR", "CDG"])
        result = stops_from_itineraries([it], limit=3)
        assert len(result) <= 3

    def test_single_stop_returns_stop_iata_when_stops_empty(self):
        """Backward-compat: a plain stopover without stops list uses stop_iata."""
        from yonder.adventure import AdventureItinerary, PricedLeg

        leg = PricedLeg(
            from_iata="YVR",
            to_iata="NRT",
            depart_date=date.today() + timedelta(days=30),
            offer=_minimal_offer(),
        )
        it = AdventureItinerary(
            kind="stopover",
            title="Tokyo Stopover",
            total_price=350.0,
            currency="USD",
            stop_iata="NRT",
            stop_city="Tokyo",
            stay_days=3,
            legs=[leg],
            stops=[],
        )
        result = self._run([it])
        assert len(result) == 1
        assert result[0][0] == "NRT"


# ===========================================================================
# Suite 2 — Template renders a field-note-slot for each hop
# ===========================================================================


class TestMultiStopBoardingPassSlots:
    """The detour_card macro must emit one field-note-slot per stop in
    a multi-stop itinerary so the JS poller can populate each one."""

    def test_two_stop_card_has_two_slots(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        html = _detour_html(it)
        slots = re.findall(r'class="[^"]*field-note-slot[^"]*"', html)
        assert len(slots) == 2, (
            f"Expected 2 field-note-slot elements for 2-stop itinerary, "
            f"got {len(slots)}"
        )

    def test_two_stop_card_slots_carry_correct_iatas(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        html = _detour_html(it)
        iatas = re.findall(r'class="[^"]*field-note-slot[^"]*"[^>]*data-iata="([^"]*)"', html)
        # Also catch attribute order variation
        iatas += re.findall(r'data-iata="([^"]*)"[^>]*class="[^"]*field-note-slot[^"]*"', html)
        assert "NRT" in iatas, f"NRT slot missing; found iatas: {iatas}"
        assert "SIN" in iatas, f"SIN slot missing; found iatas: {iatas}"

    def test_three_stop_card_has_three_slots(self):
        it = _multi_stop_itinerary(["NRT", "SIN", "DXB"])
        html = _detour_html(it)
        slots = re.findall(r'class="[^"]*field-note-slot[^"]*"', html)
        assert len(slots) == 3, (
            f"Expected 3 field-note-slot elements for 3-stop itinerary, "
            f"got {len(slots)}"
        )

    def test_three_stop_card_all_iatas_present(self):
        it = _multi_stop_itinerary(["NRT", "SIN", "DXB"])
        html = _detour_html(it)
        for iata in ("NRT", "SIN", "DXB"):
            assert f'data-iata="{iata}"' in html, (
                f"data-iata=\"{iata}\" missing from multi-stop boarding pass"
            )

    def test_slots_all_start_in_loading_state(self):
        """All slots start with is-loading so the JS knows to fetch them."""
        it = _multi_stop_itinerary(["NRT", "SIN"])
        html = _detour_html(it)
        # Count field-note-slot elements that carry is-loading
        loading_slots = re.findall(
            r'class="[^"]*field-note-slot[^"]*is-loading[^"]*"', html
        ) + re.findall(
            r'class="[^"]*is-loading[^"]*field-note-slot[^"]*"', html
        )
        assert len(loading_slots) == 2, (
            f"Expected 2 loading slots for 2-stop card, got {len(loading_slots)}"
        )

    def test_each_slot_has_loading_bar(self):
        """Each slot must contain the animated bar placeholder (pb-fn-loading)."""
        it = _multi_stop_itinerary(["NRT", "SIN"])
        html = _detour_html(it)
        bars = html.count("pb-fn-loading")
        assert bars == 2, (
            f"Expected 2 pb-fn-loading bars (one per stop), found {bars}"
        )

    def test_single_stop_stopover_still_has_one_slot(self):
        """Single-stop itinerary must not be broken by the multi-stop changes."""
        from yonder.adventure import AdventureItinerary, PricedLeg

        leg = PricedLeg(
            from_iata="YVR",
            to_iata="TYO",
            depart_date=date.today() + timedelta(days=30),
            offer=_minimal_offer(),
        )
        it = AdventureItinerary(
            kind="stopover",
            title="Tokyo Stopover",
            total_price=350.0,
            currency="USD",
            stop_iata="TYO",
            stop_city="Tokyo",
            stay_days=5,
            legs=[leg],
            stops=[],
            theme_primary="#e6b450",
            theme_label="Adventure",
        )
        html = _detour_html(it)
        slots = re.findall(r'class="[^"]*field-note-slot[^"]*"', html)
        assert len(slots) == 1, (
            f"Single-stop stopover should have exactly 1 slot, got {len(slots)}"
        )
        assert 'data-iata="TYO"' in html

    def test_slot_role_is_stopover(self):
        it = _multi_stop_itinerary(["NRT", "SIN"])
        html = _detour_html(it)
        roles = re.findall(r'data-role="([^"]*)"', html)
        # Each multi-stop slot should have role=stopover
        assert all(r == "stopover" for r in roles), (
            f"Unexpected data-role values: {roles}"
        )
