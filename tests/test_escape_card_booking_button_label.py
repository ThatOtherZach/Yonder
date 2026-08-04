"""Regression: Escape card booking button label shows correct IATA route.

The button rendered by the escape_card macro in ``_boarding_pass.html`` (the
``o.google_flights_url`` anchor) must show ``"ORIGIN ➜ DEST ↗"`` using the
query's IATA codes — not a generic label.

This file exercises two distinct contexts where the ``query`` object differs:

1. **Explore flow** — ``query`` is a ``SearchQuery`` Pydantic instance (live
   results page).  The label must reflect ``query.origin`` / ``query.destination``
   from the Pydantic model.

2. **Saved / shared flow** — ``query`` is a plain ``dict`` reconstructed from
   the database payload (trip.html: ``{% set q = p.query or {} %}``).  Jinja2
   resolves dot notation against dicts via item-access fallback, so the label
   must remain correct even when ``query`` is not a Pydantic object.

The shared-trip case is also tested end-to-end via the real HTTP route
``GET /t/{id}`` to confirm the full rendering pipeline.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.encyclopedia as enc_module  # noqa: F401 — side-effects needed by fixtures
import yonder.share as share_module
import yonder.vibe_signals as vs  # noqa: F401 — side-effects needed by fixtures
import yonder.web as web_module
from yonder.links import aviasales_url


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DEPART = (date.today() + timedelta(days=30)).isoformat()
_ORIGIN = "YVR"
_DEST = "NRT"
_EXPECTED_LABEL = f"{_ORIGIN} \u279c {_DEST} \u2197"  # "YVR ➜ NRT ↗"

# A real Aviasales search URL to embed in the offer so the button is rendered.
_AVIA_URL = aviasales_url(_ORIGIN, _DEST, date.today() + timedelta(days=30))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    """Isolated PG schema and no live API keys for every test in this module."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helper: render macro directly (no HTTP)
# ---------------------------------------------------------------------------


def _render_macro(template_src: str, **ctx) -> str:
    """Render an inline Jinja2 snippet using the app's configured environment."""
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _minimal_flight_offer(with_avia_url: bool = True):
    """Minimal FlightOffer Pydantic instance."""
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
        google_flights_url=_AVIA_URL if with_avia_url else None,
    )


def _minimal_pydantic_query():
    """Minimal SearchQuery Pydantic instance (explore flow)."""
    from yonder.types import SearchQuery

    return SearchQuery(
        origin=_ORIGIN,
        destination=_DEST,
        depart_date=date.today() + timedelta(days=30),
        return_date=date.today() + timedelta(days=37),
        adults=1,
        currency="USD",
    )


def _minimal_dict_query() -> dict:
    """Minimal query as a plain dict (saved/shared flow — reconstructed from DB)."""
    return {
        "origin": _ORIGIN,
        "destination": _DEST,
        "depart_date": _DEPART,
        "return_date": (date.today() + timedelta(days=37)).isoformat(),
        "adults": 1,
        "currency": "USD",
        "nonstop": False,
    }


def _minimal_dict_offer(with_avia_url: bool = True) -> dict:
    """Minimal offer as a plain dict (share payload from DB)."""
    return {
        "provider": "mock",
        "price": 450.0,
        "currency": "USD",
        "price_kind": "mock",
        "google_flights_url": _AVIA_URL if with_avia_url else None,
    }


# ---------------------------------------------------------------------------
# Suite 1 — Explore flow (Pydantic SearchQuery)
# ---------------------------------------------------------------------------


class TestExploreFlowButtonLabel:
    """Escape card rendered via the macro with a Pydantic query object."""

    def test_button_label_shows_iata_pair(self):
        """Button label must be 'ORIGIN ➜ DEST ↗' using the Pydantic query codes."""
        offer = _minimal_flight_offer(with_avia_url=True)
        query = _minimal_pydantic_query()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('explore', o, query, 0) }}",
            o=offer,
            query=query,
        )
        assert _EXPECTED_LABEL in html, (
            f"Expected booking button label '{_EXPECTED_LABEL}' in explore escape card; "
            f"got HTML snippet: {html[html.find('bp-actions'):html.find('bp-actions')+400]!r}"
        )

    def test_button_label_different_iata_pair(self):
        """Label must reflect the actual origin/destination — not a hardcoded string."""
        from yonder.types import FlightOffer, SearchQuery

        other_avia = aviasales_url("LHR", "SYD", date.today() + timedelta(days=30))
        offer = FlightOffer(
            provider="mock",
            price=900.0,
            currency="GBP",
            airlines=["BA"],
            stops_out=0,
            price_kind="mock",
            google_flights_url=other_avia,
        )
        query = SearchQuery(
            origin="LHR",
            destination="SYD",
            depart_date=date.today() + timedelta(days=30),
            adults=1,
            currency="GBP",
        )
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('explore', o, query, 0) }}",
            o=offer,
            query=query,
        )
        assert "LHR \u279c SYD \u2197" in html, (
            "Expected 'LHR ➜ SYD ↗' as button label for LHR→SYD escape card"
        )

    def test_no_button_when_url_absent(self):
        """When google_flights_url is absent the action button must not appear."""
        offer = _minimal_flight_offer(with_avia_url=False)
        query = _minimal_pydantic_query()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('explore', o, query, 0) }}",
            o=offer,
            query=query,
        )
        assert _EXPECTED_LABEL not in html, (
            "Expected no route button when offer has no google_flights_url"
        )


# ---------------------------------------------------------------------------
# Suite 2 — Share flow via direct macro render (dict query)
# ---------------------------------------------------------------------------


class TestDictQueryButtonLabel:
    """Escape card rendered with a plain dict query (DB-reconstructed query).

    In trip.html the share page does ``{% set q = p.query or {} %}`` where
    ``p.query`` is the raw JSON dict stored in the DB.  Jinja2's dot-access
    resolves dict keys transparently, so the label must be identical to the
    Pydantic path.
    """

    def test_button_label_with_dict_query(self):
        """Dict query must produce the same 'ORIGIN ➜ DEST ↗' label."""
        offer = _minimal_flight_offer(with_avia_url=True)
        query_dict = _minimal_dict_query()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('share', o, query, 0) }}",
            o=offer,
            query=query_dict,
        )
        assert _EXPECTED_LABEL in html, (
            f"Expected '{_EXPECTED_LABEL}' in share-mode escape card rendered with dict query"
        )

    def test_origin_iata_in_button(self):
        """Origin IATA must appear in the button label."""
        offer = _minimal_flight_offer(with_avia_url=True)
        query_dict = _minimal_dict_query()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('share', o, query, 0) }}",
            o=offer,
            query=query_dict,
        )
        assert _ORIGIN in html, (
            f"Origin IATA '{_ORIGIN}' must appear in shared escape card HTML"
        )

    def test_destination_iata_in_button(self):
        """Destination IATA must appear in the button label."""
        offer = _minimal_flight_offer(with_avia_url=True)
        query_dict = _minimal_dict_query()
        html = _render_macro(
            "{% import '_boarding_pass.html' as bp %}"
            "{{ bp.escape_card('share', o, query, 0) }}",
            o=offer,
            query=query_dict,
        )
        assert _DEST in html, (
            f"Destination IATA '{_DEST}' must appear in shared escape card HTML"
        )


# ---------------------------------------------------------------------------
# Suite 3 — Shared trip end-to-end (HTTP route, query from DB)
# ---------------------------------------------------------------------------


class TestSharedTripHttpButtonLabel:
    """Full HTTP round-trip: escape share created → GET /t/{id} → label check.

    This exercises the complete pipeline: share payload stored in DB, retrieved
    and parsed by the web route, ``{% set q = p.query or {} %}`` in trip.html,
    and finally rendered by the escape_card macro.
    """

    def _create_escape_share(self) -> object:
        return share_module.create_share(
            kind="escape",
            title=f"{_ORIGIN} \u2192 {_DEST}",
            payload={
                "query": {
                    "origin": _ORIGIN,
                    "destination": _DEST,
                    "depart": _DEPART,
                    "adults": 1,
                    "currency": "USD",
                    "nonstop": False,
                },
                "offer": {
                    "price": 450.0,
                    "currency": "USD",
                    "price_kind": "mock",
                    "google_flights_url": _AVIA_URL,
                },
                "vibe": "adventure",
            },
        )

    def test_shared_page_returns_200(self, client):
        """GET /t/{id} must return 200 for an escape share."""
        share = self._create_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200, (
            f"Expected 200 for escape share page, got {resp.status_code}"
        )

    def test_shared_page_button_label_is_iata_route(self, client):
        """Booking button on the shared trip page must show 'ORIGIN ➜ DEST ↗'."""
        share = self._create_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert _EXPECTED_LABEL in resp.text, (
            f"Expected booking button label '{_EXPECTED_LABEL}' on shared escape trip "
            f"page GET /t/{share.id}"
        )

    def test_shared_page_origin_in_label(self, client):
        """Origin IATA must appear in the shared page HTML."""
        share = self._create_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert _ORIGIN in resp.text, (
            f"Expected origin IATA '{_ORIGIN}' on shared escape trip page"
        )

    def test_shared_page_destination_in_label(self, client):
        """Destination IATA must appear in the shared page HTML."""
        share = self._create_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert _DEST in resp.text, (
            f"Expected destination IATA '{_DEST}' on shared escape trip page"
        )

    def test_shared_page_no_wrong_iata_pair(self, client):
        """A share created with specific IATAs must not show a different pair in the button."""
        share = self._create_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        # Button must not show a reversed or wrong route.
        assert f"{_DEST} \u279c {_ORIGIN} \u2197" not in resp.text, (
            f"Button label must not show the reversed route '{_DEST} ➜ {_ORIGIN} ↗'"
        )
