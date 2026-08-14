"""Tests: quest ★ Save bookmarks the existing global row (Task 659).

Design rules verified here:
- Saving a quest via /api/saved never inserts a duplicate saved_itineraries row.
- Repeat clicks are idempotent (unique index on owner_sess+saved_id).
- Bookmarked quests appear on the session's /saved page.
- Quest Library card renders "✓ Saved" server-side for a bookmarking session.
- top_quest_routes counts bookmarks so ★ Save feeds popularity.
- Deleting from /saved removes the bookmark, never the shared quest row.
- A quest share save without saved_id matches the existing row by route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
from yonder.saved import (
    bookmark_quest,
    bookmarked_quest_ids,
    count_quests,
    find_global_quest_id,
    list_bookmarked_quests,
    list_quests,
    save_itinerary,
    top_quest_routes,
    unbookmark_quest,
)


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from yonder.config import Settings

    monkeypatch.setattr(web_module, "reload_settings", lambda: Settings(testing=True))
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _quest_it(entry="Bangkok", exit_="Saigon", entry_iata="BKK", exit_iata="SGN"):
    return {
        "kind": "quest",
        "title": f"{entry} → overland → {exit_}",
        "entry_iata": entry_iata,
        "exit_iata": exit_iata,
        "entry_city": entry,
        "exit_city": exit_,
        "depart_date": "2026-11-01",
    }


def _seed_global_quest(**kw):
    """Insert a library quest the way recycling does (owner NULL)."""
    return save_itinerary(
        _quest_it(**kw), trip_meta={"origin": "YVR", "vibe": "adventure"}, owner_sess=None
    )


def _save_via_api(client, saved_id=None, **kw):
    body = {
        "itinerary": _quest_it(**kw),
        "trip_meta": {"origin": "YVR", "vibe": "adventure"},
    }
    if saved_id:
        body["trip_meta"]["saved_id"] = saved_id
    resp = client.post("/api/saved", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("ok"), data
    return data


def test_save_with_saved_id_bookmarks_without_duplicate(client):
    q = _seed_global_quest()
    assert count_quests() == 1

    client.cookies.set("yv_sess", "sessA")
    data = _save_via_api(client, saved_id=q.id)
    assert data["id"] == q.id
    assert data.get("bookmarked") is True
    assert count_quests() == 1  # no duplicate row
    assert bookmarked_quest_ids(owner_sess="sessA") == {q.id}


def test_repeat_saves_are_idempotent(client):
    q = _seed_global_quest()
    client.cookies.set("yv_sess", "sessA")
    _save_via_api(client, saved_id=q.id)
    _save_via_api(client, saved_id=q.id)
    assert count_quests() == 1
    assert len(list_bookmarked_quests(owner_sess="sessA")) == 1


def test_save_without_saved_id_matches_existing_route(client):
    q = _seed_global_quest()
    client.cookies.set("yv_sess", "sessB")
    data = _save_via_api(client)  # same route, no saved_id
    assert data["id"] == q.id
    assert count_quests() == 1


def test_new_quest_inserts_one_global_row_and_bookmarks(client):
    client.cookies.set("yv_sess", "sessC")
    data = _save_via_api(client, entry="Lisbon", exit_="Porto",
                         entry_iata="LIS", exit_iata="OPO")
    assert count_quests() == 1
    rows = list_quests()
    assert rows[0].owner_sess is None  # global library row
    assert bookmarked_quest_ids(owner_sess="sessC") == {data["id"]}


def test_bookmarked_quest_appears_on_saved_page(client):
    q = _seed_global_quest(entry="Tokyo", entry_iata="NRT")
    client.cookies.set("yv_sess", "sessD")
    _save_via_api(client, saved_id=q.id, entry="Tokyo", entry_iata="NRT")
    resp = client.get("/saved")
    assert resp.status_code == 200
    assert "Tokyo" in resp.text


def test_quests_page_renders_saved_state_server_side(client):
    q = _seed_global_quest()
    assert bookmark_quest(q.id, owner_sess="sessE")
    client.cookies.set("yv_sess", "sessE")
    resp = client.get("/quests?origin=")
    assert resp.status_code == 200
    assert "✓ Saved" in resp.text

    # Other sessions still see an active Save button
    client.cookies.set("yv_sess", "other")
    resp2 = client.get("/quests?origin=")
    assert "★ Save" in resp2.text


def test_top_quest_routes_counts_bookmarks():
    a = _seed_global_quest(entry="Madrid", entry_iata="MAD")
    b = _seed_global_quest(entry="Nairobi", entry_iata="NBO")
    # Two bookmarks for Nairobi, none for Madrid → Nairobi ranks first
    bookmark_quest(b.id, owner_sess="s1")
    bookmark_quest(b.id, owner_sess="s2")
    routes = top_quest_routes(limit=5)
    assert routes[0]["entry"] == "Nairobi"
    _ = a


def test_delete_removes_bookmark_not_quest_row(client):
    q = _seed_global_quest()
    bookmark_quest(q.id, owner_sess="sessF")
    client.cookies.set("yv_sess", "sessF")
    resp = client.post(f"/saved/{q.id}/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert bookmarked_quest_ids(owner_sess="sessF") == set()
    assert count_quests() == 1  # library row survives


def test_cookieless_delete_never_removes_quest_row(client):
    """No yv_sess cookie → the shared quest row must survive delete."""
    q = _seed_global_quest()
    resp = client.post(f"/saved/{q.id}/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert "err=" in resp.headers["location"]
    assert count_quests() == 1


def test_delete_by_non_bookmarking_session_keeps_row_and_other_bookmarks(client):
    q = _seed_global_quest()
    bookmark_quest(q.id, owner_sess="sessOwner")
    client.cookies.set("yv_sess", "stranger")
    resp = client.post(f"/saved/{q.id}/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert "err=" in resp.headers["location"]
    assert count_quests() == 1
    assert bookmarked_quest_ids(owner_sess="sessOwner") == {q.id}


def test_refresh_endpoint_rejects_shared_quest_rows(client):
    q = _seed_global_quest()
    client.cookies.set("yv_sess", "sessH")
    resp = client.post(f"/saved/{q.id}/refresh", follow_redirects=False)
    assert resp.status_code == 302
    assert "err=" in resp.headers["location"]
    row = list_quests()[0]
    assert row.owner_sess is None  # global row untouched / not reassigned


def test_unbookmark_and_find_helpers():
    q = _seed_global_quest()
    assert bookmark_quest(q.id, owner_sess="sessG")
    assert unbookmark_quest(q.id, owner_sess="sessG")
    assert not unbookmark_quest(q.id, owner_sess="sessG")
    assert bookmark_quest("nonexistent", owner_sess="sessG") is False
    assert find_global_quest_id(_quest_it(), origin="YVR", dest="BKK") == q.id
