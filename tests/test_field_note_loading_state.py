"""Regression: the field-note loading mini-bar must disappear once the note loads.

The ``.pb-fn-loading`` block (animated bar + promo slot) is the placeholder
shown while a field note streams in. Two mechanisms replace it:

  1. Server-side: when the card is rendered with a place book already
     available (``escape_place_book`` / ``pb``), the template must emit the
     real note content and *no* loading block.

  2. Client-side: ``bootFieldNotes()`` fetches ``/api/place-brief`` and
     replaces ``slot.innerHTML`` with ``renderFieldNoteHtml(...)`` output.
     That replacement HTML must never contain the loading block, and every
     fetch resolution path (success, failure, bad IATA) must drop the
     ``is-loading`` class.

Covers both the escape card and detour/stop card paths.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yonder.web as web_module

TEMPLATES = Path(web_module.__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_boarding_pass_structure.py)
# ---------------------------------------------------------------------------


def _render_macro(template_src: str, **ctx) -> str:
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _minimal_flight_offer() -> Any:
    from yonder.types import FlightOffer

    return FlightOffer(
        provider="mock",
        price=450.0,
        currency="USD",
        airlines=["UA"],
        stops_out=0,
        price_kind="mock",
        display_price="~USD 450",
        display_price_base="~USD 450",
    )


def _minimal_escape_query() -> Any:
    from yonder.types import SearchQuery

    return SearchQuery(
        origin="YVR",
        destination="NRT",
        depart_date=date.today() + timedelta(days=30),
        return_date=date.today() + timedelta(days=37),
        adults=1,
        currency="USD",
    )


def _minimal_detour_it() -> Any:
    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.types import FlightOffer

    leg = PricedLeg(
        from_iata="YVR",
        to_iata="TYO",
        depart_date=date.today() + timedelta(days=30),
        offer=FlightOffer(
            provider="mock",
            price=450.0,
            currency="USD",
            airlines=["JL"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    return AdventureItinerary(
        kind="stopover",
        title="Tokyo Detour",
        total_price=450.0,
        currency="USD",
        stop_iata="TYO",
        stop_city="Tokyo",
        stay_days=7,
        why="Great city for a stopover",
        vibe_tags=["adventure"],
        legs=[leg],
        theme_primary="#e6b450",
        theme_label="Adventure",
    )


_PLACE_BOOK = {
    "title": "Tokyo",
    "subtitle": "Neon and noodles",
    "tagline": "Old temples, new circuits",
    "culture": "Bowing matters.",
    "food": "Ramen everywhere.",
    "caution": "Rush hour is intense.",
    "facts": ["Population 37M"],
    "activity_links": [],
}


def _escape_html(place_book=None) -> str:
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0, escape_place_book=pb) }}",
        o=_minimal_flight_offer(),
        query=_minimal_escape_query(),
        pb=place_book,
    )


def _detour_html(pb=None) -> str:
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='t', pb=pb) }}",
        it=_minimal_detour_it(),
        pb=pb,
    )


# ===========================================================================
# Suite 1 — Server-side rendering: loading block absent once note present
# ===========================================================================


class TestServerRenderedLoadingState:
    # -- Escape card path ----------------------------------------------------

    def test_escape_without_note_shows_loading_bar(self):
        html = _escape_html(place_book=None)
        assert "pb-fn-loading" in html
        assert "pb-fn-bar-track" in html
        assert "field-note-slot is-loading" in html or re.search(
            r'class="[^"]*field-note-slot[^"]*is-loading', html
        )

    def test_escape_with_note_has_no_loading_bar(self):
        html = _escape_html(place_book=_PLACE_BOOK)
        assert "pb-fn-loading" not in html, (
            "Escape card still renders the loading mini-bar even though the "
            "field note content is present"
        )
        assert "pb-fn-bar-track" not in html
        assert "is-loading" not in html
        # Real content took its place
        assert "Tokyo" in html
        assert "pb-title-line" in html

    # -- Detour / stop card path ----------------------------------------------

    def test_detour_without_note_shows_loading_bar(self):
        html = _detour_html(pb=None)
        assert "pb-fn-loading" in html
        assert "pb-fn-bar-track" in html
        assert "is-loading" in html

    def test_detour_with_note_has_no_loading_bar(self):
        html = _detour_html(pb=_PLACE_BOOK)
        assert "pb-fn-loading" not in html, (
            "Detour card still renders the loading mini-bar even though the "
            "field note content is present"
        )
        assert "pb-fn-bar-track" not in html
        assert "is-loading" not in html
        assert "pb-title-line" in html


# ===========================================================================
# Suite 2 — Client-side replacement logic (static contract on the JS)
# ===========================================================================


def _js_of(template_name: str) -> str:
    return (TEMPLATES / template_name).read_text(encoding="utf-8")


class TestClientReplacementContract:
    """The JS that swaps in streamed content must fully retire the loader."""

    def test_replacement_html_never_contains_loading_block(self):
        """renderFieldNoteHtml (the innerHTML replacement) must not emit
        pb-fn-loading — otherwise the bar would survive the swap."""
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            m = re.search(
                r"function renderFieldNoteHtml[\s\S]*?\n      }", src
            ) or re.search(r'var h = [\'"]<div class="pb-story-body">', src)
            assert m is not None, f"renderFieldNoteHtml body not found in {tpl}"
            body_start = src.find("pb-story-body", src.find("renderFieldNoteHtml") if "renderFieldNoteHtml" in src else 0)
            assert body_start != -1, f"pb-story-body replacement missing in {tpl}"
            # The generated replacement markup must never include the loader.
            gen_region = src[body_start : body_start + 4000]
            assert "pb-fn-loading" not in gen_region, (
                f"{tpl}: replacement field-note HTML still contains the "
                "pb-fn-loading block"
            )

    def test_every_fetch_path_removes_is_loading(self):
        """Success, error and bad-IATA paths must all strip is-loading."""
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert 'querySelectorAll(".field-note-slot.is-loading")' in src, (
                f"{tpl}: bootFieldNotes loading-slot query missing"
            )
            removals = src.count('classList.remove("is-loading")')
            assert removals >= 3, (
                f"{tpl}: expected is-loading removal in bad-IATA, then() and "
                f"catch() paths (>=3), found {removals}"
            )

    def test_slot_innerhtml_replaced_on_success_and_failure(self):
        """The whole slot innerHTML is replaced, discarding the loader DOM."""
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert "slot.innerHTML = renderFieldNoteHtml" in src, (
                f"{tpl}: streamed content must replace slot.innerHTML so the "
                "pb-fn-loading block is removed from the DOM"
            )
