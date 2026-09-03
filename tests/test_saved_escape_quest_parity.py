"""Saved Escape cards must read like Saved Quest cards.

The Saved page renders Escape trips through the ``detour_card`` macro's
``kind == 'escape'`` branch.  That branch used to diverge from the Quest card:
the label said "Direct Escape", the flight button had no "Book" prefix, the
"Fares age"/"Saved" metadata sat above the flight legs, and the field note had
no city in its heading.  These tests lock in the Quest anatomy:

    route → legs → field note → saved metadata → action row

Transport pills on a Saved Escape are streamed in by the page's JS from
``/api/place-brief``; the endpoint must therefore return the airport-rail and
ground-transfer links the server-rendered Quest note shows.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.saved as saved_module
import yonder.web as web_module


_ORIGIN = "YVR"
_DEST = "NRT"  # Tokyo — a known IATA→city mapping and a Global Hub airport
_DEPART = (date.today() + timedelta(days=30)).isoformat()
_AVIA_URL = f"https://www.aviasales.com/search/{_ORIGIN}{_DEST}"

_SESS = "testescparityclient00testescpar"


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _save_escape() -> saved_module.SavedItinerary:
    itinerary = saved_module.escape_offer_to_itinerary(
        query={
            "origin": _ORIGIN,
            "destination": _DEST,
            "depart_date": _DEPART,
            "return_date": (date.today() + timedelta(days=37)).isoformat(),
            "adults": 1,
            "currency": "USD",
        },
        offer={
            "provider": "mock",
            "price": 480.0,
            "currency": "USD",
            "price_kind": "mock",
            "display_price": "~USD 480",
            "display_price_base": "~USD 480",
            "airlines": ["CX"],
            "google_flights_url": _AVIA_URL,
        },
        vibe="adventure",
    )
    return saved_module.save_itinerary(itinerary, owner_sess=_SESS)


class TestSavedEscapeCardAnatomy:
    """Wording and section order on /saved for an Escape trip."""

    def test_card_label_is_escape(self, client):
        """The card label must read 'Escape', never 'Direct Escape'."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert '<span class="bp-label">Escape</span>' in resp.text
        assert "Direct Escape" not in resp.text

    def test_field_note_heading_carries_the_city(self, client):
        """The field note must be headed 'Field note · <City>' (NRT → Tokyo)."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert "Field note \u00b7 Tokyo" in resp.text, (
            "Saved Escape field note must show the destination city in its heading"
        )

    def test_field_note_slot_knows_the_city(self, client):
        """The hydration slot must carry data-city so the JS can title the note."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert 'data-city="Tokyo"' in resp.text

    def test_saved_metadata_sits_between_field_note_and_actions(self, client):
        """'Fares age'/'Saved' must follow the field note and precede the actions."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        html = resp.text

        note_at = html.find("field-note-slot")
        meta_at = html.find("Fares age")
        actions_at = html.find('class="bp-actions"')

        assert note_at != -1, "Saved Escape must render a field-note slot"
        assert meta_at != -1, "Saved Escape must render the 'Fares age' metadata"
        assert actions_at != -1, "Saved Escape must render an action row"
        assert note_at < meta_at < actions_at, (
            "Saved Escape metadata must sit after the field note and before the "
            f"action row (field note @{note_at}, metadata @{meta_at}, actions @{actions_at})"
        )

    def test_booking_button_uses_quest_wording(self, client):
        """The flight button must read 'Book <origin> → <destination> ↗'."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert f"Book {_ORIGIN} \u2192 {_DEST} \u2197" in resp.text


class TestSavedEscapeTransportPills:
    """Transport pills reach the Saved page through the place-brief endpoint."""

    def test_place_brief_returns_transit_links(self, client, monkeypatch):
        """/api/place-brief must return airport-rail and ground-transfer links."""
        import yonder.encyclopedia as enc

        class _Brief:
            def to_dict(self):
                return {
                    "title": "Tokyo",
                    "subtitle": "",
                    "facts": ["Fact one"],
                    "activity_links": [],
                    "poi_picks": [],
                    "culture": None,
                    "food": None,
                    "vibe": None,
                    "caution": None,
                    "era_note": None,
                    "tagline": None,
                    "iata": _DEST,
                    "country": "JP",
                    "from_cache": True,
                }

        async def _fake_brief(*args, **kwargs):
            return _Brief()

        monkeypatch.setattr(enc, "get_place_brief", _fake_brief)

        resp = client.get(f"/api/place-brief?iata={_DEST}&role=destination")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("ok") is True, data

        brief = data["brief"]
        assert brief.get("city") == "Tokyo"
        assert brief.get("airport_trains"), (
            "NRT must return airport-rail links so the hydrated note shows rail pills"
        )
        assert brief.get("ground_transfers"), (
            "NRT must return a ground-transfer link so the hydrated note shows it"
        )
        for pill in brief["airport_trains"] + brief["ground_transfers"]:
            assert pill.get("url", "").startswith("https://")
            assert pill.get("name")

    def test_saved_page_renders_transit_pills_from_the_brief(self, client):
        """The Saved page's hydration renderer must emit the transit pills."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert "brief.airport_trains" in resp.text
        assert "brief.ground_transfers" in resp.text
        assert "transitPillsHtml" in resp.text
