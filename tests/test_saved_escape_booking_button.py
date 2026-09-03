"""Regression: saved escape trips show the correct route label in the booking button.

Escape trips are stored via ``escape_offer_to_itinerary`` + ``save_itinerary``
and rendered on /saved using the ``detour_card`` macro (not ``escape_card``).
The booking button in ``detour_card`` reads ``Book leg.from_iata → leg.to_iata ↗``
from the first leg's data.  This test confirms that the label survives the
full round-trip: build → save → reload → GET /saved → HTML.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.saved as saved_module
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORIGIN = "YVR"
_DEST = "NRT"
_DEPART = (date.today() + timedelta(days=30)).isoformat()
_AVIA_URL = f"https://www.aviasales.com/search/{_ORIGIN}{_DEST}"
_AIRLINE_URL = "https://www.cathaypacific.com/"
_EXPECTED_LABEL = f"Book {_ORIGIN} \u2192 {_DEST} \u2197"  # "Book YVR → NRT ↗"

# Stable session id used by every save/GET in this module so the saved rows
# are visible when the test client hits /saved with this cookie.
_SESS = "testescapeclient0000testescapecl"


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
# Helpers
# ---------------------------------------------------------------------------


def _make_query() -> dict:
    return {
        "origin": _ORIGIN,
        "destination": _DEST,
        "depart_date": _DEPART,
        "return_date": (date.today() + timedelta(days=37)).isoformat(),
        "adults": 1,
        "currency": "USD",
    }


def _make_offer(with_url: bool = True) -> dict:
    return {
        "provider": "mock",
        "price": 480.0,
        "currency": "USD",
        "price_kind": "mock",
        "display_price": "~USD 480",
        "display_price_base": "~USD 480",
        "airlines": ["CX"],
        "deep_link": _AIRLINE_URL,
        "google_flights_url": _AVIA_URL if with_url else None,
    }


def _save_escape(with_url: bool = True) -> saved_module.SavedItinerary:
    """Build an escape itinerary via the canonical helper and persist it.

    Saves under ``_SESS`` so the row is visible when the test client GETs
    /saved with ``cookies={"yv_sess": _SESS}``.
    """
    itinerary = saved_module.escape_offer_to_itinerary(
        query=_make_query(),
        offer=_make_offer(with_url=with_url),
        vibe="adventure",
    )
    return saved_module.save_itinerary(itinerary, owner_sess=_SESS)


# ===========================================================================
# Suite — Saved escape trip booking button label
# ===========================================================================


class TestSavedEscapeBookingButton:
    """Booking button on /saved for an escape-kind trip must show the IATA route."""

    def test_saved_escape_kind_is_escape(self):
        """escape_offer_to_itinerary must produce kind='escape'."""
        s = _save_escape()
        assert s.kind == "escape", (
            f"Expected saved kind 'escape', got '{s.kind}'"
        )

    def test_saved_page_returns_200(self, client):
        """GET /saved must succeed when an escape trip is saved."""
        _save_escape()
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200, (
            f"Expected 200 from GET /saved with a saved escape trip, got {resp.status_code}"
        )

    def test_saved_escape_has_destination_field_note_slot(self, client):
        """A normal Saved Escape must load a field note for its destination."""
        _save_escape()

        resp = client.get("/saved", cookies={"yv_sess": _SESS})

        assert resp.status_code == 200
        assert "field-note-slot" in resp.text
        assert f'data-iata="{_DEST}"' in resp.text
        assert 'data-role="destination"' in resp.text

    def test_booking_button_label_contains_iata_pair(self, client):
        """Booking button on /saved must show 'Book YVR → NRT ↗' for this route."""
        _save_escape(with_url=True)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert _EXPECTED_LABEL in resp.text, (
            f"Expected booking button label '{_EXPECTED_LABEL}' on /saved for "
            f"a saved escape trip (YVR→NRT); button area: "
            f"{resp.text[max(0, resp.text.find('bp-actions')):resp.text.find('bp-actions') + 500]!r}"
        )

    def test_booking_button_label_has_origin_iata(self, client):
        """Origin IATA must appear in the /saved page HTML."""
        _save_escape(with_url=True)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert _ORIGIN in resp.text, (
            f"Expected origin IATA '{_ORIGIN}' to appear on /saved page"
        )

    def test_booking_button_label_has_dest_iata(self, client):
        """Destination IATA must appear in the /saved page HTML."""
        _save_escape(with_url=True)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert _DEST in resp.text, (
            f"Expected destination IATA '{_DEST}' to appear on /saved page"
        )

    def test_booking_button_absent_when_no_url(self, client):
        """Without google_flights_url the route-label button must not appear."""
        _save_escape(with_url=False)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert _EXPECTED_LABEL not in resp.text, (
            f"Expected no '{_EXPECTED_LABEL}' button when offer has no google_flights_url"
        )

    def test_google_flights_url_stored_on_saved(self):
        """google_flights_url must be stored on the SavedItinerary row."""
        s = _save_escape(with_url=True)
        assert s.google_flights_url == _AVIA_URL, (
            f"Expected google_flights_url='{_AVIA_URL}' on SavedItinerary, "
            f"got '{s.google_flights_url}'"
        )

    def test_booking_button_label_does_not_show_reversed_route(self, client):
        """The button must not display the reversed route (Book NRT → YVR ↗)."""
        _save_escape(with_url=True)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        reversed_label = f"Book {_DEST} \u2192 {_ORIGIN} \u2197"
        assert reversed_label not in resp.text, (
            f"Button must not show reversed route '{reversed_label}' on /saved"
        )

    def test_affiliate_link_remains_without_airline_site_link(self, client):
        """Saved Escape keeps its affiliate CTA but hides the carrier homepage."""
        _save_escape(with_url=True)
        resp = client.get("/saved", cookies={"yv_sess": _SESS})
        assert resp.status_code == 200
        assert _AVIA_URL.replace("&", "&amp;") in resp.text
        assert "Airline site" not in resp.text
        assert "site ↗" not in resp.text
        assert f'href="{_AIRLINE_URL}"' not in resp.text
