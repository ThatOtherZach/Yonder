"""Regression tests: activity pills on shared trip pages.

Covers two paths in _render_shared_trip:
  1. Destination has a cached field-note brief with activities.csv data
     → pills appear in the rendered HTML.
  2. Destination has NO cached brief (cold cache)
     → page renders cleanly (200), no pills section, no server error.

Both cases use escape shares (simplest payload shape) and rely on real
activities.csv rows for AMS (Amsterdam), which has both a GetYourGuide and
a Viator row. The encyclopedia DB is isolated per test via tmp_path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import yonder.encyclopedia as enc_module
import yonder.share as share_module
import yonder.vibe_signals as vs
import yonder.web as web_module


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


def _minimal_brief(city: str = "Amsterdam") -> dict:
    """Minimal brief payload that passes the tagline stale-format guard."""
    return {
        "title": city,
        "subtitle": f"The heart of {city}",
        "tagline": f"Unforgettable {city}.",
        "culture": "Rich heritage and open-minded culture.",
        "food": "Stroopwafels and herring.",
        "vibe": "Canal-side wandering.",
        "caution": "Watch for cyclists.",
        "era_note": "",
        "facts": ["Capital city", "Canals everywhere"],
    }


def _escape_share(dest: str, origin: str = "YVR", vibe: str = "culture") -> object:
    """Create an escape share record for origin → dest."""
    return share_module.create_share(
        kind="escape",
        title=f"{origin} → {dest}",
        payload={
            "query": {
                "origin": origin,
                "destination": dest,
                "depart": "2026-10-01",
                "adults": 1,
                "currency": "USD",
                "nonstop": False,
            },
            "offer": {
                "price": 650.0,
                "currency": "USD",
                "price_kind": "mock",
            },
            "vibe": vibe,
        },
    )


# ---------------------------------------------------------------------------
# Test 1 – pills appear when a cached brief exists for the destination
# ---------------------------------------------------------------------------


class TestSharedTripWithCachedBrief:
    """Activity pills attach and render when the destination brief is cached."""

    def test_pills_appear_in_html(self, client):
        """AMS brief is cached → pb-fact-link pill anchors appear in the HTML."""
        # Seed the cache with a valid brief for AMS
        key = enc_module.cache_key("AMS", "NL", "amsterdam", lang="en")
        enc_module.put_cached(key, _minimal_brief("Amsterdam"))

        share = _escape_share("AMS")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200, (
            f"Expected 200 for shared trip with cached brief, got {resp.status_code}"
        )
        html = resp.text
        # The pill anchor element rendered by trip.html — only present when pills attach.
        # CSS contains the rule definition but never the attribute string 'rel="noopener sponsored"'.
        assert 'rel="noopener sponsored"' in html, (
            "Expected activity pill <a> elements (rel=noopener sponsored) in HTML "
            "when brief is cached"
        )

    def test_pills_link_to_partner_sites(self, client):
        """At least one pill href points to a known partner domain."""
        key = enc_module.cache_key("AMS", "NL", "amsterdam", lang="en")
        enc_module.put_cached(key, _minimal_brief("Amsterdam"))

        share = _escape_share("AMS")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200
        html = resp.text
        # AMS has GetYourGuide and/or Viator rows in activities.csv
        has_partner = (
            "getyourguide.com" in html or "viator.com" in html
        )
        assert has_partner, (
            "Expected at least one partner pill URL (getyourguide.com or viator.com) "
            "in the shared trip HTML when the brief is cached"
        )

    def test_field_note_card_also_renders(self, client):
        """The place-book field note section element is present alongside pills."""
        key = enc_module.cache_key("AMS", "NL", "amsterdam", lang="en")
        enc_module.put_cached(key, _minimal_brief("Amsterdam"))

        share = _escape_share("AMS")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200
        html = resp.text
        # 'class="place-book field-note-inset"' only appears in rendered elements, not in CSS rules.
        assert 'class="place-book field-note-inset"' in html, (
            "Expected 'place-book field-note-inset' element in HTML when brief is cached"
        )
        assert "Field note" in html, (
            "Expected 'Field note' label in HTML when brief is cached"
        )


# ---------------------------------------------------------------------------
# Test 2 – page renders cleanly when no cached brief exists
# ---------------------------------------------------------------------------


class TestSharedTripWithoutCachedBrief:
    """When the destination has no cached brief the page still renders cleanly."""

    def test_page_returns_200(self, client):
        """Cold cache → 200, no server error, no traceback in body."""
        # Use a destination code that won't match any seeded brief
        share = _escape_share("SYD")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200, (
            f"Expected 200 for shared trip with cold cache, got {resp.status_code}"
        )

    def test_no_pills_rendered(self, client):
        """Cold cache → no pill anchor elements in the HTML."""
        share = _escape_share("SYD")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200
        html = resp.text
        # 'class="pb-fact pb-fact-link"' only appears on rendered pill <a> elements,
        # not in inline JS strings.
        assert 'class="pb-fact pb-fact-link"' not in html, (
            "Expected no pill anchor elements when destination has no cached brief"
        )

    def test_no_field_note_section(self, client):
        """Cold cache → the place-book field note section element is absent."""
        share = _escape_share("SYD")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200
        html = resp.text
        # The CSS rules contain '.place-book' but only rendered elements have
        # 'class="place-book"' (with quotes), so check for the attribute form.
        assert 'class="place-book"' not in html, (
            "Expected no place-book element when destination brief is not cached"
        )

    def test_page_still_shows_trip_title(self, client):
        """Cold cache → the share title and main trip info are still rendered."""
        share = _escape_share("SYD")
        resp = client.get(f"/t/{share.id}")

        assert resp.status_code == 200
        html = resp.text
        assert "YVR" in html, "Expected origin code in trip HTML"
        assert "SYD" in html, "Expected destination code in trip HTML"
