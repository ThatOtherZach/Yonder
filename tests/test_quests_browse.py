"""Tests for the public /quests browse page — Task 595.

Covers:
  1. End-to-end save → browse path: quests saved via /api/saved appear on /quests.
  2. Origin filter: only quests matching the origin appear (saved via /api/saved).
  3. Pagination boundary: page beyond total is clamped silently.
  4. Empty state: renders cleanly when no quests exist.
  5. Share link: browse cards link to the shared trip page (not just "Plan this Quest").
  6. Sitemap includes /quests.
  7. Nav active class is applied on /quests.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import yonder.saved as saved_module
import yonder.web as web_module
from yonder.saved import ensure_global_quest, list_bookmarked_quests


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    """Throwaway PG schema; patch get_conn in all DB-backed modules."""
    monkeypatch.delenv("MOCK", raising=False)
    # Prevent settings from auto-populating a home-airport origin filter in tests
    # by making resolve_home_iata return "" (no default filter).
    from yonder.config import Settings
    monkeypatch.setattr(
        web_module, "reload_settings",
        lambda: Settings(testing=True),
    )
    yield


def _save_quest(client, *, origin: str = "YVR", entry_city: str = "Bangkok",
                exit_city: str = "Saigon", entry_iata: str = "BKK",
                exit_iata: str = "SGN") -> dict:
    """POST a quest to /api/saved (the real save path) and return the response JSON."""
    payload = {
        "itinerary": {
            "kind": "quest",
            "title": f"{entry_city} → overland → {exit_city}",
            "entry_iata": entry_iata,
            "exit_iata": exit_iata,
            "entry_city": entry_city,
            "exit_city": exit_city,
            "depart_date": "2026-11-01",
        },
        "trip_meta": {
            "origin": origin,
            "destination": entry_iata,
            "vibe": "adventure",
        },
    }
    resp = client.post("/api/saved", json=payload)
    assert resp.status_code == 200, f"Save failed: {resp.text}"
    data = resp.json()
    assert data.get("ok"), f"Save returned ok=False: {data}"
    return data


# ── 1. End-to-end: saved quest appears on /quests ───────────────────────────

def test_saved_quest_appears_on_browse_page(client):
    _save_quest(client, origin="YVR", entry_city="Bangkok")

    # No origin filter → should show the saved quest (home airport returns "" so all shown)
    resp = client.get("/quests?origin=", follow_redirects=False)
    assert resp.status_code == 200
    assert "Bangkok" in resp.text


def test_saved_quest_has_view_trip_link(client):
    """Quests saved via the real API should get a View trip link, not just a CTA."""
    _save_quest(client, origin="YVR", entry_city="Lisbon", entry_iata="LIS")

    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200
    assert "Lisbon" in resp.text
    # The quest card must have a CTA link to the full quest share page
    assert "Open Quest" in resp.text or "View trip" in resp.text


def test_quest_save_target_stays_relative_and_bookmark_is_visible_in_saved(client):
    """Browse-card Save must stay on this host after creating its bookmark."""
    quest = ensure_global_quest(
        {
            "kind": "quest",
            "title": "Lisbon → overland → Porto",
            "entry_iata": "LIS",
            "exit_iata": "OPO",
            "entry_city": "Lisbon",
            "exit_city": "Porto",
            "depart_date": "2026-11-01",
        },
        trip_meta={"origin": "YVR", "vibe": "adventure"},
        origin="YVR",
    )
    client.cookies.set("yv_sess", "browse-save-session")

    browse = client.get("/quests?origin=YVR", follow_redirects=False)
    assert browse.status_code == 200
    assert f'data-saved-id="{quest.id}"' in browse.text
    assert 'data-share-url="/t/quest/' in browse.text
    assert 'data-share-url="https://yonder.city/' not in browse.text

    saved = client.post(
        "/api/saved",
        json={
            "itinerary": quest.itinerary,
            "trip_meta": {
                "origin": quest.origin,
                "destination": quest.destination,
                "vibe": quest.vibe,
                "saved_id": quest.id,
            },
        },
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert saved.json()["id"] == quest.id
    assert [item.id for item in list_bookmarked_quests(owner_sess="browse-save-session")] == [
        quest.id
    ]

    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    assert quest.title in saved_page.text


def test_saved_quest_origin_column_populated(client):
    """The saved row's origin column must be set from trip_meta so /quests?origin=X works."""
    _save_quest(client, origin="JFK", entry_city="Tokyo", entry_iata="NRT")

    # Filter by JFK — Tokyo quest must appear
    resp = client.get("/quests?origin=JFK", follow_redirects=False)
    assert resp.status_code == 200
    assert "Tokyo" in resp.text


# ── 2. Origin filter ─────────────────────────────────────────────────────────

def test_origin_filter_excludes_other_origins(client):
    _save_quest(client, origin="YVR", entry_city="Madrid")
    _save_quest(client, origin="JFK", entry_city="Nairobi")

    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200
    assert "Madrid" in resp.text
    assert "Nairobi" not in resp.text


def test_origin_filter_case_insensitive(client):
    _save_quest(client, origin="YVR", entry_city="Seoul", entry_iata="ICN")

    resp = client.get("/quests?origin=yvr", follow_redirects=False)
    assert resp.status_code == 200
    assert "Seoul" in resp.text


def test_explicit_empty_origin_shows_all(client):
    """?origin= (explicit empty) clears the filter and shows all quests."""
    _save_quest(client, origin="YVR", entry_city="Madrid")
    _save_quest(client, origin="JFK", entry_city="Nairobi")

    resp = client.get("/quests?origin=", follow_redirects=False)
    assert resp.status_code == 200
    assert "2 Quests" in resp.text
    assert "Madrid" in resp.text
    assert "Nairobi" in resp.text


# ── 3. Empty state ───────────────────────────────────────────────────────────

def test_empty_state_no_quests(client):
    resp = client.get("/quests?origin=", follow_redirects=False)
    assert resp.status_code == 200
    assert "No quests yet" in resp.text


def test_empty_state_with_origin_filter(client):
    _save_quest(client, origin="JFK", entry_city="Berlin")

    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200
    assert "No Quest adventures found departing from" in resp.text or "No quests yet" in resp.text


# ── 4. Pagination ────────────────────────────────────────────────────────────

def _insert_quest_direct(pg_schema, *, origin: str, entry_city: str, idx: int) -> str:
    """Directly insert a quest row into the test schema (bypasses save_itinerary).

    Used for volume tests that need more rows than SAVE_LIMIT allows via the API.
    The inserted rows have origin set, so list_quests filters on them correctly.
    """
    sid = uuid.uuid4().hex[:12]
    it = json.dumps({
        "kind": "quest",
        "entry_iata": f"Q{idx:02d}",
        "exit_iata": "SGN",
        "entry_city": entry_city,
        "exit_city": "Saigon",
        "depart_date": "2026-11-01",
    })
    # Stagger saved_at so ORDER BY saved_at DESC is deterministic
    ts = time.time() - (1000 - idx)
    with pg_schema() as conn:
        conn.execute(
            """
            INSERT INTO saved_itineraries
              (id, saved_at, title, kind, currency, origin, destination,
               adults, cabin, itinerary_json, trip_meta_json)
            VALUES (%s, %s, %s, 'quest', 'USD', %s, %s, 1, 'economy', %s, %s)
            """,
            (
                sid, ts, f"{entry_city} Quest", origin, f"Q{idx:02d}", it,
                json.dumps({"origin": origin}),
            ),
        )
        conn.commit()
    return sid


def test_pagination_across_multiple_pages(client, pg_schema):
    """Pagination controls appear when more quests exist than the production page size (10).

    Inserts 13 rows directly into the test schema so we exceed the page size
    without hitting SAVE_LIMIT eviction (quests are exempt, but the API is slower).
    """
    for i in range(13):
        _insert_quest_direct(pg_schema, origin="YVR", entry_city=f"City{i}", idx=i)

    page1 = client.get("/quests?origin=YVR&page=1", follow_redirects=False)
    assert page1.status_code == 200
    assert "Next →" in page1.text
    assert "13 Quests" in page1.text

    page2 = client.get("/quests?origin=YVR&page=2", follow_redirects=False)
    assert page2.status_code == 200
    assert "← Prev" in page2.text


def test_pagination_beyond_total_clamps_silently(client):
    """A page number way beyond the total clamps to last page without an error."""
    _save_quest(client, origin="YVR", entry_city="Vienna", entry_iata="VIE")

    resp = client.get("/quests?origin=YVR&page=999", follow_redirects=False)
    assert resp.status_code == 200
    assert "Vienna" in resp.text


def test_page_beyond_empty_set(client):
    """Page > 1 on an empty quest list returns empty state without error."""
    resp = client.get("/quests?origin=&page=5", follow_redirects=False)
    assert resp.status_code == 200
    assert "No quests yet" in resp.text


# ── 5. Sitemap includes /quests ──────────────────────────────────────────────

def test_sitemap_includes_quests(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "yonder.city/quests" in resp.text


# ── 6. Nav active class ──────────────────────────────────────────────────────

def test_nav_active_class(client):
    resp = client.get("/quests?origin=", follow_redirects=False)
    assert resp.status_code == 200
    assert 'href="/quests"' in resp.text
    # The nav link for /quests should carry the active class
    assert 'class="active"' in resp.text
