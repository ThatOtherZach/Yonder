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

    def test_every_fetch_path_finishes_loading_state(self):
        """All requests share a finalizer, while bad IATA exits immediately."""
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert 'querySelectorAll(".field-note-slot.is-loading")' in src, (
                f"{tpl}: bootFieldNotes loading-slot query missing"
            )
            loader = src[src.index("function bootFieldNotes()") :]
            assert loader.count('classList.remove("is-loading")') >= 2
            assert ".finally(function ()" in loader

    def test_slot_innerhtml_replaced_on_success_and_failure(self):
        """The whole slot innerHTML is replaced, discarding the loader DOM."""
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert "slot.innerHTML = renderFieldNoteHtml" in src, (
                f"{tpl}: streamed content must replace slot.innerHTML so the "
                "pb-fn-loading block is removed from the DOM"
            )


class TestClientRetryContract:
    def test_requests_are_bounded_and_timeout_becomes_retry(self):
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert "var timeoutMs = 20000" in src
            assert "new AbortController()" in src
            assert "controller.abort()" in src
            assert 'err.name === "AbortError"' in src
            assert "This field note took too long." in src
            assert 'class="pb-fn-refresh"' in src

    def test_refresh_is_accessible_forced_and_duplicate_safe(self):
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            assert 'aria-label="Refresh field note for ' in src
            assert 'q.set("refresh", "true")' in src
            assert "if (slot._fieldNoteLoading) return" in src
            assert "button.disabled = true" in src
            assert "loadFieldNote(slot, true)" in src

    def test_retry_preserves_destination_metadata(self):
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            loader = src[src.index("function loadFieldNote") :]
            for attr in ("data-iata", "data-country", "data-city", "data-role", "data-vibe"):
                assert attr in loader
            # Only the contents are replaced, leaving metadata on the slot.
            assert "slot.outerHTML" not in loader

    def test_failure_returns_to_retry_and_success_replaces_it(self):
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            loader = src[src.index("function loadFieldNote") :]
            assert "slot.innerHTML = retryHtml(iata, message, col, slot)" in loader
            assert "slot.innerHTML = renderFieldNoteHtml(data.brief, col, slot)" in loader
            assert 'slot.classList.remove("is-loading")' in loader

    def test_failure_keeps_ground_spend_fallback_with_refresh(self):
        for tpl in ("index.html", "saved.html"):
            src = _js_of(tpl)
            retry = src[src.index("function retryHtml") : src.index(
                "function loadFieldNote", src.index("function retryHtml")
            )]
            assert "hasColData(col)" in retry
            assert "renderFieldNoteHtml(null, col, slot)" in retry
            assert "pb-fn-refresh" in retry


def test_place_brief_endpoint_accepts_validated_refresh_flag():
    src = Path(web_module.__file__).read_text(encoding="utf-8")
    endpoint = src[src.index("async def api_place_brief(") : src.index(
        "\n\n", src.index("async def api_place_brief(")
    )]
    assert 'refresh: bool = Query(False)' in endpoint


async def test_forced_generation_bypasses_and_replaces_cache(monkeypatch):
    import yonder.encyclopedia as encyclopedia

    stale = {"title": "Stale Tokyo", "culture": "old"}
    writes = []

    monkeypatch.setattr(encyclopedia, "get_cached", lambda key: stale)
    monkeypatch.setattr(
        encyclopedia, "put_cached", lambda key, payload: writes.append((key, payload))
    )
    monkeypatch.setattr(encyclopedia, "_activity_links", _empty_activity_links)
    monkeypatch.setattr(encyclopedia, "_poi_picks", lambda city: [])

    class FakeGrok:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def place_brief(self, **kwargs):
            return {"title": "Fresh Tokyo", "culture": "new"}

    import yonder.grok as grok_module

    monkeypatch.setattr(grok_module, "GrokClient", FakeGrok)

    class Settings:
        def grok_ready(self):
            return True

        def model_source_label(self):
            return "test"

    brief = await encyclopedia.get_place_brief(
        Settings(), iata="NRT", city="Tokyo", trip_vibe="adventure", force_refresh=True
    )
    assert brief is not None
    assert brief.title == "Fresh Tokyo"
    assert brief.from_cache is False
    assert len(writes) == 2
    assert all(payload["title"] == "Fresh Tokyo" for _, payload in writes)


async def _empty_activity_links(*args, **kwargs):
    return []
