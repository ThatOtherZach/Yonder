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
import shutil
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

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


def test_quest_save_target_opens_saved_page_and_bookmark_is_visible(client):
    """Browse-card Save must open this browser's updated Saved page."""
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
    assert 'data-saved-url="/saved?flash=Quest%20saved"' in browse.text
    assert "window.location.href = savedUrl || \"/saved\"" in browse.text
    assert "setTimeout(go, 6000)" not in browse.text
    assert 'credentials: "same-origin"' in browse.text

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


def test_quests_page_mints_session_before_first_save_and_saved_page_uses_it():
    """A first-time visitor must bookmark under the session minted on /quests."""
    quest = ensure_global_quest(
        {
            "kind": "quest",
            "title": "Tokyo → overland → Kyoto",
            "entry_iata": "NRT",
            "exit_iata": "KIX",
            "entry_city": "Tokyo",
            "exit_city": "Kyoto",
            "depart_date": "2026-11-01",
        },
        trip_meta={"origin": "YVR", "vibe": "adventure"},
        origin="YVR",
    )

    with TestClient(
        web_module.app,
        base_url="https://testserver",
        raise_server_exceptions=True,
    ) as first_visit:
        assert first_visit.cookies.get("yv_sess") is None
        browse = first_visit.get("/quests?origin=YVR")
        assert browse.status_code == 200
        browse_session = first_visit.cookies.get("yv_sess")
        assert browse_session

        saved = first_visit.post(
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
        assert first_visit.cookies.get("yv_sess") == browse_session

        saved_page = first_visit.get("/saved")
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

def test_browse_cards_use_compact_canonical_ticket_and_keep_save_hooks(client):
    """The browse-only Quest variant remains a ticket, not a separate card UI."""
    _save_quest(client, origin="YVR", entry_city="Lisbon", entry_iata="LIS")
    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.text
    assert 'data-ticket-kind="quest"' in html
    assert 'data-ticket-variant="compact"' in html
    assert "quest-ticket--compact" in html
    assert '<span class="bp-label">Quest</span>' in html
    assert "Quest · library" not in html
    assert 'class="bp-cities text-left"' in html
    assert 'class="quest-browse-stats mt-[0px]"' in html
    assert '<span class="price-amt">👀</span>' in html
    assert 'class="bp-plane bp-overland-mark" role="img" aria-label="Overland connection"' in html
    assert '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"' in html
    assert 'class="bp-plane bp-overland-mark" role="img" aria-label="Overland connection">🚶</span>' not in html
    assert 'class="bp-overland-mark" aria-label="Overland connection">overland' not in html
    assert 'class="quest-browse-details-label"' not in html
    assert "Open Quest" in html
    assert "btn-ql-save" in html
    assert 'id="ql-quest-json-0"' in html
    assert 'id="ql-meta-json-0"' in html

def test_browse_card_keeps_overland_context_out_of_flight_details(client):
    """Browse gives the Quest story priority; the full page owns flight metadata."""
    ensure_global_quest(
        {
            "kind": "quest",
            "title": "Lisbon → overland → Porto",
            "entry_iata": "LIS",
            "exit_iata": "OPO",
            "entry_city": "Lisbon",
            "exit_city": "Porto",
            "depart_date": "2026-11-01",
            "outbound_date": "2026-11-12",
            "transport": ["regional trains", "coastal buses"],
            "overland_narrative": "Follow the coast slowly, stopping wherever the light is good.",
            "highlights": ["Atlantic cliffs", "small wine towns"],
        },
        trip_meta={"origin": "YVR", "vibe": "adventure"},
        origin="YVR",
    )

    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.text
    assert "Follow the coast slowly" in html
    assert "Via regional trains · coastal buses" in html
    assert html.index('class="bp-route"') < html.index('aria-label="Quest timing"')
    assert html.index('aria-label="Quest timing"') < html.index('class="bp-cities text-left"')
    assert html.index('class="bp-cities text-left"') < html.index("Via regional trains · coastal buses")
    assert 'class="bp-why mt-[0px]"' in html
    assert "Atlantic cliffs" in html
    assert 'class="bp-fast-facts quest-browse-highlights mb-[8px]"' in html
    assert "📍 Atlantic cliffs" in html
    assert "● Atlantic cliffs" not in html
    assert '<p class="bp-cities text-left">Lisbon <span aria-hidden="true">→</span> Porto</p>' in html
    assert html.index('class="bp-top"') < html.index('class="bp-route"')
    assert "Entry flight" not in html
    assert "Exit flight" not in html

def test_compact_ticket_css_has_mobile_single_column_fallback():
    """Keep the responsive source contract explicit alongside browser coverage."""
    template = (web_module.templates.env.loader.searchpath[0] + "/quests.html")
    with open(template, encoding="utf-8") as source:
        css = source.read()
    assert "@media (max-width:575px)" in css
    assert ".quest-browse-details" in css
    assert "minmax(min(100%, 22rem), 1fr)" in css
    assert "overflow-wrap:anywhere" in css
    assert "white-space:normal" in css
    assert "row-gap:.55rem" in css
    assert r".quest-browse-details .bp-why.mt-\[0px\] { margin-top:0; }" in css
    assert r".quest-browse-stats.mt-\[0px\] { margin-top:0; }" in css
    assert r".quest-browse-highlights.mb-\[8px\] { margin-bottom:8px; }" in css
    assert ".quest-ticket--compact .bp-stub { justify-content:center; align-items:center; gap:1rem; }" in css
    assert "grid-template-columns:1fr auto;" in css
    assert "color:var(--bp-accent); font-size:.72rem;" in css
    assert "color:var(--bp-accent); font-size:.61rem;" in css

@pytest.mark.parametrize("width", [390, 1280])
def test_compact_ticket_fits_real_browser_viewports(client, tmp_path, width):
    """The live browse HTML keeps route, context, actions, and stub on-canvas."""
    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("Chromium is required for ticket layout regressions")

    _save_quest(client, origin="YVR", entry_city="Lisbon", entry_iata="LIS")
    resp = client.get("/quests?origin=YVR", follow_redirects=False)
    assert resp.status_code == 200

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chromium,
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": width, "height": 1100})
        page.set_content(resp.text, wait_until="domcontentloaded")
        page.screenshot(path=str(tmp_path / f"quest-browse-{width}.png"))

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        card = page.locator(
            '[data-ticket-kind="quest"][data-ticket-variant="compact"]'
        ).first
        bounds = card.bounding_box()
        assert bounds is not None
        assert bounds["x"] >= 0
        assert bounds["x"] + bounds["width"] <= width + 1
        assert card.locator(".bp-route").is_visible()
        details = card.locator(".quest-browse-details")
        if details.count():
            assert details.is_visible()
        assert card.locator(".bp-actions .btn-ql-save").is_visible()
        assert card.locator(".bp-stub").is_visible()
        timing = card.locator(".quest-browse-stats")
        assert timing.evaluate(
            "(element) => getComputedStyle(element).marginTop"
        ) == "0px"
        timing_color = timing.evaluate("(element) => getComputedStyle(element).color")
        assert timing.locator("strong").first.evaluate(
            "(element) => getComputedStyle(element).color"
        ) == timing_color
        highlights = card.locator(".quest-browse-highlights")
        if highlights.count():
            assert highlights.evaluate(
                "(element) => getComputedStyle(element).marginBottom"
            ) == "8px"
        assert card.locator(".bp-cities").evaluate(
            "(element) => getComputedStyle(element).textAlign"
        ) == "left"
        price = card.locator(".bp-stub-price").bounding_box()
        qr = card.locator(".bp-qr").bounding_box()
        stub = card.locator(".bp-stub").bounding_box()
        if price and qr and stub:
            if width >= 576:
                stub_center = stub["x"] + stub["width"] / 2
                assert abs((price["x"] + price["width"] / 2) - stub_center) <= 3
                assert abs((qr["x"] + qr["width"] / 2) - stub_center) <= 3
                vertical_gap = qr["y"] - (price["y"] + price["height"])
                assert vertical_gap >= 0
                assert vertical_gap <= stub["height"] * 0.5
            else:
                assert abs((price["y"] + price["height"] / 2) - (qr["y"] + qr["height"] / 2)) <= 12
        browser.close()
