"""Regression: no-flight fallback suppresses bp-legs and raw provider errors.

When ``no_flight_hub_url`` or ``no_flight_adj_url`` is set on an
AdventureItinerary:

  - The ``bp-legs`` block must not appear in the rendered card.
  - Raw provider error strings (e.g. "serpapi_google_flights: …") must not
    appear in the *visible* rendered output (outside of JSON data islands).

Even when no no-flight URLs are present, the raw provider error text must
not bleed into the meta line.

These tests render templates directly via the Jinja2 env — no DB or HTTP
request involved — so they are immune to the PostgreSQL migration fixture
issues in test_boarding_pass_structure.py.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

import yonder.web as web_module


_DEPART = (date.today() + timedelta(days=30)).isoformat()

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)


def _strip_scripts(html: str) -> str:
    """Remove all <script>…</script> blocks (JSON islands, JS) from HTML.

    The JSON islands legitimately serialise the full itinerary model including
    leg.error fields so the browser JS can read them.  We only care that raw
    error strings don't reach *visible* rendered output.
    """
    return _SCRIPT_RE.sub("", html)


def _render_macro(template_src: str, **ctx) -> str:
    """Render an inline Jinja2 snippet using the app's configured env."""
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _detour_card_html(it) -> str:
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
        it=it,
    )


def _make_it(*, no_flight_hub_url=None, no_flight_hub_iata=None, no_flight_adj_url=None):
    """Build a minimal AdventureItinerary with a leg that has a provider error."""
    from yonder.adventure import AdventureItinerary, PricedLeg

    leg = PricedLeg(
        from_iata="YVR",
        to_iata="NRT",
        depart_date=_DEPART,
        offer=None,
        error="serpapi_google_flights: Google Flights hasn't returned any results",
    )
    return AdventureItinerary(
        kind="stopover",
        title="Tokyo No-Flight",
        total_price=0.0,
        currency="USD",
        stop_iata="TYO",
        stop_city="Tokyo",
        stay_days=7,
        why="No flights available",
        vibe_tags=["adventure"],
        legs=[leg],
        theme_primary="#e6b450",
        theme_label="Adventure",
        no_flight_hub_url=no_flight_hub_url,
        no_flight_hub_iata=no_flight_hub_iata,
        no_flight_adj_url=no_flight_adj_url,
    )


# ---------------------------------------------------------------------------
# No-flight fallback active
# ---------------------------------------------------------------------------


class TestNoFlightFallbackActive:
    """Tests for the case where no_flight_hub_url or no_flight_adj_url is set."""

    @pytest.fixture(autouse=True)
    def _no_api_keys(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)

    def test_bp_legs_absent_when_hub_url_set(self):
        """bp-legs block must be suppressed when no_flight_hub_url is set."""
        it = _make_it(
            no_flight_hub_url="https://aviasales.example.com/hub",
            no_flight_hub_iata="YYZ",
        )
        html = _detour_card_html(it)
        assert "bp-legs" not in html, (
            "bp-legs block must be suppressed when no_flight_hub_url is set"
        )

    def test_bp_legs_absent_when_adj_url_set(self):
        """bp-legs block must be suppressed when no_flight_adj_url is set."""
        it = _make_it(no_flight_adj_url="https://aviasales.example.com/adj")
        html = _detour_card_html(it)
        assert "bp-legs" not in html, (
            "bp-legs block must be suppressed when no_flight_adj_url is set"
        )

    def test_bp_no_flight_block_present(self):
        """bp-no-flight block must appear when no_flight_hub_url is set."""
        it = _make_it(
            no_flight_hub_url="https://aviasales.example.com/hub",
            no_flight_hub_iata="YYZ",
            no_flight_adj_url="https://aviasales.example.com/adj",
        )
        html = _detour_card_html(it)
        assert "bp-no-flight" in html, (
            "bp-no-flight block must appear when no_flight URLs are set"
        )

    def test_raw_provider_error_absent_with_no_flight_fallback(self):
        """Raw provider error identifier must not appear in visible rendered output."""
        it = _make_it(
            no_flight_hub_url="https://aviasales.example.com/hub",
            no_flight_hub_iata="YYZ",
            no_flight_adj_url="https://aviasales.example.com/adj",
        )
        visible = _strip_scripts(_detour_card_html(it))
        assert "serpapi_google_flights" not in visible, (
            "Raw provider error string must not appear in visible output when no_flight fallback is active"
        )


# ---------------------------------------------------------------------------
# No-flight fallback absent — error leg, no fallback URLs
# ---------------------------------------------------------------------------


class TestNoFlightFallbackAbsent:
    """When no_flight URLs are absent, bp-legs renders but raw error is still hidden."""

    @pytest.fixture(autouse=True)
    def _no_api_keys(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)

    def test_raw_error_absent_without_no_flight_urls(self):
        """Raw provider error must not appear in the visible meta line without no_flight URLs."""
        it = _make_it()  # no no_flight URLs
        visible = _strip_scripts(_detour_card_html(it))
        assert "serpapi_google_flights" not in visible, (
            "Raw provider error must not appear in visible meta line when no_flight URLs are absent"
        )

    def test_bp_legs_present_without_no_flight_urls(self):
        """bp-legs block must still render when no no_flight URLs are set."""
        it = _make_it()  # no no_flight URLs
        html = _detour_card_html(it)
        assert "bp-legs" in html, (
            "bp-legs block must still render when no no_flight fallback URLs are set"
        )
