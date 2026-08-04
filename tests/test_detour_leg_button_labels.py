"""Regression tests: Detour bp-actions buttons show per-leg route labels.

The Detour card's bp-actions block iterates ``it.legs`` and renders one button
per leg, labelled ``{from_iata} ➜ {to_iata} ↗`` — not a generic "Aviasales ↗".

Asserts across all three rendering modes (explore, saved, share):

  1. Single-leg Detour  → exactly one button labelled ``{FROM} ➜ {TO} ↗``
  2. Two-leg Detour     → two buttons, each correctly labelled
  3. Old ``it.google_flights_url`` top-level field is not rendered as a
     standalone generic button (the per-leg loop is the only source)
  4. No Kayak link appears in the rendered bp-actions block
  5. Shared-trip page (mode='share') carries the same per-leg labels

Covers template macro ``detour_card`` in ``_boarding_pass.html``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.saved as saved_module
import yonder.share as share_module
import yonder.web as web_module
from yonder.links import AVIASALES_MARKER, aviasales_url


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    """Isolated PG schema, no live API keys, for every test in this module."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DEPART = (date.today() + timedelta(days=30)).isoformat()
_RETURN = (date.today() + timedelta(days=37)).isoformat()
_DEPART_DATE = date.today() + timedelta(days=30)
_RETURN_DATE = date.today() + timedelta(days=37)

# Aviasales URLs that will appear in rendered HTML
_URL_LEG1 = aviasales_url("YVR", "DXB", _DEPART_DATE)
_URL_LEG2 = aviasales_url("DXB", "YVR", _RETURN_DATE)


# ---------------------------------------------------------------------------
# Helpers: build Pydantic objects for direct macro rendering
# ---------------------------------------------------------------------------


def _render_macro(template_src: str, **ctx) -> str:
    """Render an inline Jinja2 snippet with the app's configured environment."""
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _single_leg_it():
    """Single-leg Detour (YVR → DXB) with a google_flights_url on the leg."""
    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.types import FlightOffer

    leg = PricedLeg(
        from_iata="YVR",
        to_iata="DXB",
        depart_date=_DEPART_DATE,
        google_flights_url=_URL_LEG1,
        offer=FlightOffer(
            provider="mock",
            price=900.0,
            currency="USD",
            airlines=["EK"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    return AdventureItinerary(
        kind="stopover",
        title="Dubai Stopover",
        total_price=900.0,
        currency="USD",
        stop_iata="DXB",
        stop_city="Dubai",
        stay_days=5,
        why="Desert adventure",
        vibe_tags=["adventure"],
        legs=[leg],
        theme_primary="#e8a020",
        theme_label="Adventure",
    )


def _two_leg_it():
    """Two-leg Detour (YVR → DXB, DXB → YVR) with per-leg google_flights_url."""
    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.types import FlightOffer

    leg1 = PricedLeg(
        from_iata="YVR",
        to_iata="DXB",
        depart_date=_DEPART_DATE,
        google_flights_url=_URL_LEG1,
        offer=FlightOffer(
            provider="mock",
            price=900.0,
            currency="USD",
            airlines=["EK"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    leg2 = PricedLeg(
        from_iata="DXB",
        to_iata="YVR",
        depart_date=_RETURN_DATE,
        google_flights_url=_URL_LEG2,
        offer=FlightOffer(
            provider="mock",
            price=880.0,
            currency="USD",
            airlines=["EK"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    return AdventureItinerary(
        kind="stopover",
        title="Dubai Round Trip",
        total_price=1780.0,
        currency="USD",
        stop_iata="DXB",
        stop_city="Dubai",
        stay_days=5,
        why="Desert adventure",
        vibe_tags=["adventure"],
        legs=[leg1, leg2],
        theme_primary="#e8a020",
        theme_label="Adventure",
        # This top-level url must NOT generate a separate button of its own
        google_flights_url="https://example.com/should-not-appear-as-button",
    )


# ---------------------------------------------------------------------------
# Helpers: build share / saved payloads (dict form accepted by share_module)
# ---------------------------------------------------------------------------


def _single_leg_payload() -> dict:
    return {
        "itinerary": {
            "kind": "detour",
            "title": "Dubai Stopover",
            "why": "Desert adventure",
            "theme_label": "Adventure",
            "theme_primary": "#e8a020",
            "stop_iata": "DXB",
            "stop_city": "Dubai",
            "total_price": 900.0,
            "currency": "USD",
            "legs": [
                {
                    "from_iata": "YVR",
                    "to_iata": "DXB",
                    "depart_date": _DEPART,
                    "google_flights_url": _URL_LEG1,
                    "offer": {
                        "provider": "mock",
                        "price": 900.0,
                        "currency": "USD",
                        "price_kind": "mock",
                        "airlines": ["EK"],
                        "stops_out": 0,
                    },
                }
            ],
        },
        "trip_meta": {"vibe": "adventure"},
    }


def _two_leg_payload() -> dict:
    return {
        "itinerary": {
            "kind": "stopover",
            "title": "Dubai Round Trip",
            "why": "Desert adventure",
            "theme_label": "Adventure",
            "theme_primary": "#e8a020",
            "stop_iata": "DXB",
            "stop_city": "Dubai",
            "total_price": 1780.0,
            "currency": "USD",
            # top-level url must NOT produce a standalone generic button
            "google_flights_url": "https://example.com/should-not-appear-as-button",
            "legs": [
                {
                    "from_iata": "YVR",
                    "to_iata": "DXB",
                    "depart_date": _DEPART,
                    "google_flights_url": _URL_LEG1,
                    "offer": {
                        "provider": "mock",
                        "price": 900.0,
                        "currency": "USD",
                        "price_kind": "mock",
                        "airlines": ["EK"],
                        "stops_out": 0,
                    },
                },
                {
                    "from_iata": "DXB",
                    "to_iata": "YVR",
                    "depart_date": _RETURN,
                    "google_flights_url": _URL_LEG2,
                    "offer": {
                        "provider": "mock",
                        "price": 880.0,
                        "currency": "USD",
                        "price_kind": "mock",
                        "airlines": ["EK"],
                        "stops_out": 0,
                    },
                },
            ],
        },
        "trip_meta": {"vibe": "adventure"},
    }


# ===========================================================================
# Suite 1 — Explore mode (macro rendered directly)
# ===========================================================================


class TestDetourExploreButtonLabels:
    """detour_card in explore mode must render per-leg route labels."""

    def test_single_leg_one_button_correct_label(self):
        """Single-leg Detour → exactly one ``YVR ➜ DXB ↗`` button."""
        it = _single_leg_it()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
            it=it,
        )
        assert "YVR ➜ DXB ↗" in html, (
            "Single-leg explore detour must render 'YVR ➜ DXB ↗' button label"
        )
        # Only one such button
        assert html.count("➜ DXB ↗") == 1, (
            "Expected exactly one '➜ DXB ↗' button for single-leg detour explore"
        )

    def test_two_leg_both_button_labels(self):
        """Two-leg Detour → two buttons labelled correctly."""
        it = _two_leg_it()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
            it=it,
        )
        assert "YVR ➜ DXB ↗" in html, (
            "Two-leg explore detour must render 'YVR ➜ DXB ↗' for the outbound leg"
        )
        assert "DXB ➜ YVR ↗" in html, (
            "Two-leg explore detour must render 'DXB ➜ YVR ↗' for the return leg"
        )

    def test_two_leg_no_generic_aviasales_label(self):
        """The button text 'Aviasales ↗' (generic) must not appear — only route labels."""
        it = _two_leg_it()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
            it=it,
        )
        # Generic label should not appear; only per-leg route labels are valid
        assert "Aviasales ↗" not in html, (
            "Generic 'Aviasales ↗' label must not appear in explore detour bp-actions"
        )

    def test_two_leg_no_toplevel_url_as_button(self):
        """The top-level it.google_flights_url must NOT appear as a standalone button.
        Only per-leg route buttons are rendered; the top-level field is ignored."""
        it = _two_leg_it()
        # _two_leg_it sets it.google_flights_url to a sentinel URL; confirm it's not
        # rendered as an <a href> button in the bp-actions block.
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
            it=it,
        )
        assert 'href="https://example.com/should-not-appear-as-button"' not in html, (
            "Top-level it.google_flights_url must not appear as a standalone <a> button "
            "in the explore detour bp-actions block"
        )

    def test_single_leg_aviasales_url_in_href(self):
        """The Aviasales URL from the leg appears as the button href."""
        it = _single_leg_it()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test') }}",
            it=it,
        )
        escaped_url = _URL_LEG1.replace("&", "&amp;")
        assert escaped_url in html, (
            f"Expected Aviasales URL {escaped_url!r} in explore detour href"
        )


# ===========================================================================
# Suite 2 — Saved mode (GET /saved page)
# ===========================================================================


class TestDetourSavedButtonLabels:
    """detour_card in saved mode must render per-leg route labels."""

    def _html(self, client, payload: dict) -> str:
        saved_module.save_itinerary(payload["itinerary"])
        resp = client.get("/saved")
        assert resp.status_code == 200
        return resp.text

    def test_single_leg_label_on_saved(self, client):
        """Single-leg saved detour shows 'YVR ➜ DXB ↗'."""
        html = self._html(client, _single_leg_payload())
        assert "YVR ➜ DXB ↗" in html, (
            "Saved single-leg detour must render 'YVR ➜ DXB ↗' button label"
        )

    def test_two_leg_both_labels_on_saved(self, client):
        """Two-leg saved detour shows both outbound and return leg labels."""
        html = self._html(client, _two_leg_payload())
        assert "YVR ➜ DXB ↗" in html, (
            "Saved two-leg detour must render 'YVR ➜ DXB ↗' for outbound leg"
        )
        assert "DXB ➜ YVR ↗" in html, (
            "Saved two-leg detour must render 'DXB ➜ YVR ↗' for return leg"
        )

    def test_two_leg_no_kayak_on_saved(self, client):
        """Top-level kayak URL must not leak into saved detour bp-actions."""
        html = self._html(client, _two_leg_payload())
        assert "kayak.com" not in html, (
            "kayak.com must not appear on saved detour page"
        )


# ===========================================================================
# Suite 3 — Share mode (GET /t/{id})
# ===========================================================================


class TestDetourShareButtonLabels:
    """detour_card in share mode must render per-leg route labels."""

    def test_single_leg_label_on_share(self, client):
        """Single-leg shared detour shows 'YVR ➜ DXB ↗'."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Stopover",
            payload=_single_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "YVR ➜ DXB ↗" in resp.text, (
            "Shared single-leg detour must render 'YVR ➜ DXB ↗' button label"
        )

    def test_two_leg_both_labels_on_share(self, client):
        """Two-leg shared detour shows both outbound and return leg labels."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Round Trip",
            payload=_two_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "YVR ➜ DXB ↗" in resp.text, (
            "Shared two-leg detour must render 'YVR ➜ DXB ↗' for outbound leg"
        )
        assert "DXB ➜ YVR ↗" in resp.text, (
            "Shared two-leg detour must render 'DXB ➜ YVR ↗' for return leg"
        )

    def test_two_leg_no_generic_aviasales_on_share(self, client):
        """Generic 'Aviasales ↗' must not appear on shared detour page."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Round Trip",
            payload=_two_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert "Aviasales ↗" not in resp.text, (
            "Generic 'Aviasales ↗' label must not appear on shared detour page"
        )

    def test_two_leg_no_kayak_on_share(self, client):
        """Top-level kayak URL in payload must not appear on shared detour page."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Round Trip",
            payload=_two_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert "kayak.com" not in resp.text, (
            "kayak.com must not appear on shared detour page"
        )

    def test_single_leg_aviasales_url_in_href_on_share(self, client):
        """The Aviasales URL from the leg appears as the button href on share page."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Stopover",
            payload=_single_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        escaped_url = _URL_LEG1.replace("&", "&amp;")
        assert escaped_url in resp.text, (
            f"Expected Aviasales URL {escaped_url!r} in shared detour href"
        )

    def test_two_leg_both_aviasales_urls_on_share(self, client):
        """Both leg Aviasales URLs appear as hrefs on the two-leg share page."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Round Trip",
            payload=_two_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        html = resp.text
        escaped_url1 = _URL_LEG1.replace("&", "&amp;")
        escaped_url2 = _URL_LEG2.replace("&", "&amp;")
        assert escaped_url1 in html, (
            f"Expected outbound Aviasales URL {escaped_url1!r} on two-leg share page"
        )
        assert escaped_url2 in html, (
            f"Expected return Aviasales URL {escaped_url2!r} on two-leg share page"
        )

    def test_affiliate_marker_present_on_share(self, client):
        """Affiliate marker (starts with 756039) must appear in share page href."""
        share = share_module.create_share(
            kind="detour",
            title="Dubai Stopover",
            payload=_single_leg_payload(),
        )
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert f"marker={AVIASALES_MARKER}" in resp.text, (
            f"Expected affiliate marker 'marker={AVIASALES_MARKER}' on shared detour page"
        )
