"""Regression tests: Aviasales round-trip path format in boarding-pass JS.

The inline JS inside _boarding_pass.html builds Aviasales search URLs at
click-time.  Two handlers are covered:

  * .esc-rt-go  — Escape card "Find Return" picker
  * .rt-go      — Detour/multi-leg "Find Return" picker

Correct Aviasales round-trip format:
    /search/{ORIG}{DDMM1}{DEST}{DDMM2}{PAX}
                                           ^^ no extra origin before PAX

Broken (pre-fix) Escape format:
    /search/{ORIG}{DDMM1}{DEST}{DDMM2}{ORIG}{PAX}   ← extra ORIG is wrong

Broken (pre-fix) Detour format (doubled airports at junctions):
    /search/{ORIG}{DDMM1}{DEST}{DEST}{DDMM2}{ORIG}{PAX}  ← doubled wrong
"""

from __future__ import annotations

import re

import pytest

import yonder.web as web_module


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _render_escape_card(**ctx) -> str:
    """Render the escape_card macro with minimal required context."""
    from yonder.types import FlightOffer, SearchQuery
    from datetime import date, timedelta
    from yonder.links import aviasales_url

    depart = date.today() + timedelta(days=30)
    offer = FlightOffer(
        provider="mock",
        price=550.0,
        currency="USD",
        airlines=["AC"],
        stops_out=0,
        price_kind="mock",
        google_flights_url=aviasales_url("YVR", "PVG", depart),
    )
    query = SearchQuery(
        origin="YVR",
        destination="PVG",
        depart_date=depart,
        adults=1,
        currency="USD",
    )
    env = web_module.templates.env
    tmpl = env.from_string(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0) }}",
    )
    merged = dict(o=offer, query=query, **ctx)
    return tmpl.render(**merged)


def _render_detour_card_two_legs(**ctx) -> str:
    """Render the detour_card macro with a TWO-leg itinerary.

    Two legs are required for the .rt-picker to render
    (template condition: ``it.legs|length == 2``).
    We use a multi-hop outbound: YVR → LHR → DXB so the Return picker
    targets DXB → YVR with a user-chosen date.
    """
    from datetime import date, timedelta
    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.links import aviasales_url

    depart0 = date(2026, 9, 15)   # ddmm = "1509"
    depart1 = date(2026, 9, 20)   # ddmm = "2009"
    leg_url = aviasales_url("YVR", "DXB", depart0)

    it = AdventureItinerary(
        kind="detour",
        title="Dubai via London",
        why="Classic stopover",
        theme_label="Adventure",
        theme_primary="#e8a020",
        stop_iata="DXB",
        stop_city="Dubai",
        google_flights_url=leg_url,
        legs=[
            PricedLeg(from_iata="YVR", to_iata="LHR", depart_date=depart0),
            PricedLeg(from_iata="LHR", to_iata="DXB", depart_date=depart1),
        ],
        total_price=1100.0,
        currency="USD",
        display_price="~USD 1,100",
        display_price_base="~USD 1,100",
        stay_days=5,
    )

    env = web_module.templates.env
    tmpl = env.from_string(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0) }}",
    )
    merged = dict(it=it, **ctx)
    return tmpl.render(**merged)


def _simulate_rt_path(rt_segs_json: str, return_ddmm: str) -> str:
    """Python simulation of the .rt-go JS path-building logic (post-fix).

    Mirrors the fixed JS exactly:
        var path = segs[0][0];
        for (var i = 0; i < segs.length; i++) {
          path += segs[i][2];
          if (i < segs.length - 1) path += segs[i][1];
        }

    Args:
        rt_segs_json: the data-rt-segs attribute value (JSON array).
        return_ddmm:  DDMM of the user-chosen return date.
    Returns:
        The path string (without trailing pax digit).
    """
    import json
    segs = json.loads(rt_segs_json)
    home = segs[0][0]
    dest = segs[-1][1]
    segs.append([dest, home, return_ddmm])
    path = segs[0][0]
    for i, seg in enumerate(segs):
        path += seg[2]
        if i < len(segs) - 1:
            path += seg[1]
    return path


# ---------------------------------------------------------------------------
# Escape card: .esc-rt-go JS path format
# ---------------------------------------------------------------------------


class TestEscapeRtPickerPathFormat:
    """The .esc-rt-go JS must build {ORIG}{DEP_DDMM}{DEST}{RET_DDMM}1 — no extra origin."""

    def test_no_extra_origin_before_pax(self):
        """The JS string must NOT contain '+ orig + '1?marker=' (old broken pattern)."""
        html = _render_escape_card()
        # The old broken code had: retDDMM + orig + '1?marker='
        # After the fix it must be: retDDMM + '1?marker='
        assert "retDDMM + orig + '1?marker=" not in html, (
            "Found old broken pattern 'retDDMM + orig + '1?marker=' in escape card JS; "
            "the extra origin before the pax digit was not removed."
        )

    def test_correct_rt_path_structure_in_js(self):
        """The JS string must contain '+ retDDMM + '1?marker=' (correct pattern)."""
        html = _render_escape_card()
        assert "retDDMM + '1?marker=" in html, (
            "Expected 'retDDMM + '1?marker=' in escape card JS; "
            "round-trip path must end {ORIG}{DDMM1}{DEST}{DDMM2}{PAX}."
        )

    def test_esc_rt_picker_script_block_present(self):
        """The _escRtPickerReady script block must be present in the rendered HTML."""
        html = _render_escape_card()
        assert "_escRtPickerReady" in html, (
            "Expected '_escRtPickerReady' guard in rendered escape card HTML."
        )

    def test_esc_rt_picker_opens_aviasales_search(self):
        """The JS must open an aviasales.com/search/ URL (not the homepage)."""
        html = _render_escape_card()
        assert "aviasales.com/search/" in html, (
            "Expected 'aviasales.com/search/' in the .esc-rt-go click handler."
        )


# ---------------------------------------------------------------------------
# Detour card: .rt-go JS path format
# ---------------------------------------------------------------------------


class TestDetourRtPickerPathFormat:
    """The .rt-go JS must build {ORIG}{DDMM1}{MID}{DDMM2}1 — no doubled airports.

    Requires a 2-leg itinerary so the template renders the .rt-picker
    (condition: ``it.legs|length == 2``).
    """

    def test_rt_picker_renders_with_two_legs(self):
        """The .rt-picker element must appear when the itinerary has 2 legs."""
        html = _render_detour_card_two_legs()
        assert 'class="rt-picker"' in html, (
            "Expected .rt-picker div in detour card HTML with a 2-leg itinerary; "
            "the Return picker only renders when it.legs|length == 2."
        )

    def test_no_old_join_pattern(self):
        """The JS must NOT use the old .map(s[0]+s[2]+s[1]).join pattern."""
        html = _render_detour_card_two_legs()
        assert "s[0] + s[2] + s[1]" not in html, (
            "Found old broken segs.map(s[0]+s[2]+s[1]) pattern in detour .rt-go JS; "
            "this doubles airports at segment junctions."
        )

    def test_new_path_builder_present(self):
        """The JS must start the path from segs[0][0] (first origin only)."""
        html = _render_detour_card_two_legs()
        assert "segs[0][0]" in html, (
            "Expected 'segs[0][0]' as path seed in detour .rt-go JS; "
            "the running-chain builder must start from the first origin."
        )

    def test_rt_picker_script_block_present(self):
        """The _rtPickerReady script block must be present in rendered detour card HTML."""
        html = _render_detour_card_two_legs()
        assert "_rtPickerReady" in html, (
            "Expected '_rtPickerReady' guard in rendered detour card HTML."
        )

    def test_detour_rt_go_opens_aviasales_search(self):
        """The .rt-go JS must open an aviasales.com/search/ URL."""
        html = _render_detour_card_two_legs()
        assert "aviasales.com/search/" in html, (
            "Expected 'aviasales.com/search/' in the .rt-go click handler."
        )

    def test_two_leg_path_no_doubled_airports(self):
        """Simulated 2-leg path must not double the intermediate airport.

        Fixture: YVR→LHR (1509) + LHR→DXB (2009), return date 2026-10-05 (ddmm=0510).
        Old (broken): YVR1509LHRLHR2009DXBDXB0510YVR  ← doubled LHR and DXB
        New (correct): YVR1509LHR2009DXB0510           ← no doubling
        """
        import re
        html = _render_detour_card_two_legs()

        # Extract data-rt-segs from the rendered HTML
        m = re.search(r'data-rt-segs="([^"]+)"', html)
        assert m, "Expected data-rt-segs attribute in rendered detour card HTML."

        import html as html_module
        rt_segs_json = html_module.unescape(m.group(1))

        return_ddmm = "0510"  # 5 Oct
        path = _simulate_rt_path(rt_segs_json, return_ddmm)

        # Correct: YVR1509LHR2009DXB0510 (no doubled airports)
        assert path == "YVR1509LHR2009DXB0510", (
            f"Round-trip path must be 'YVR1509LHR2009DXB0510', got {path!r}. "
            "Doubled airports (e.g. LHRLHR or DXBDXB) indicate the old broken builder."
        )

    def test_two_leg_path_does_not_contain_doubled_iata(self):
        """No IATA code must appear twice in a row in the constructed path."""
        import re
        html = _render_detour_card_two_legs()

        m = re.search(r'data-rt-segs="([^"]+)"', html)
        assert m, "Expected data-rt-segs attribute in rendered detour card HTML."

        import html as html_module
        rt_segs_json = html_module.unescape(m.group(1))

        path = _simulate_rt_path(rt_segs_json, "0510")

        # Check no 3-letter sequence appears twice consecutively
        doubled = re.search(r'([A-Z]{3})\1', path)
        assert not doubled, (
            f"Path {path!r} contains a doubled IATA code ({doubled.group(0)!r}); "
            "airports must not be repeated at segment junctions."
        )
