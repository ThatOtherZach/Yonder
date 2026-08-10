"""Anchored planning (Task: plan new trips around saved flights).

Covers:
- anchor extraction: upcoming legs only, past dates excluded, sorted + capped
- no-anchor cold path: prompts byte-free of anchor material
- prompt injection: anchors appear in system + user payload of all 3 planners
- date-window enforcement in match_anchor (arrive strictly before departure)
- dead-route filtering of the connecting leg via route knowledge
- end-to-end: a real /explore search renders the ⚓ anchor badge in HTML when a
  saved upcoming leg connects, and renders no badge without upcoming saves
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import yonder.knowledge as knowledge_mod
import yonder.grok as grok_module
import yonder.last_search as ls_module
import yonder.saved as saved_mod
import yonder.web as web_module
from yonder.adventure import match_anchor
from yonder.config import Settings
from yonder.grok import GrokClient, ParsedTrip
from yonder.saved import SavedItinerary, upcoming_anchor_legs
from yonder.types import FlightOffer, SearchQuery, UnifiedSearchResult

TODAY = date(2026, 8, 3)


def _save(kind: str, itinerary: dict, origin: str = "YVR", sid: str = "s1") -> SavedItinerary:
    return SavedItinerary(
        id=sid, saved_at=0.0, priced_at=None, title=f"{kind} trip", kind=kind,
        currency="CAD", total_price=None, display_price=None, stop_city=None,
        stop_iata=None, stay_days=None, origin=origin, destination=None,
        adults=1, cabin="economy", vibe=None, trip_prompt=None,
        theme_country=None, theme_primary=None, theme_accent=None,
        theme_gradient=None, theme_flag_img=None, theme_label=None,
        google_flights_url=None, kayak_url=None, ground_display=None,
        ground_compare_line=None, all_in_display=None, notes=[],
        itinerary=itinerary, trip_meta={},
    )


# ── Anchor extraction ────────────────────────────────────────────────────────

def test_no_saves_yields_no_anchors(monkeypatch):
    monkeypatch.setattr(saved_mod, "list_saved", lambda *a, **kw: [])
    assert upcoming_anchor_legs(today=TODAY) == []


def test_past_dated_legs_never_anchor(monkeypatch):
    s = _save("detour", {"legs": [
        {"from_iata": "ICN", "to_iata": "YVR", "depart_date": "2026-07-01"},
        {"from_iata": "ICN", "to_iata": "YVR", "depart_date": "2026-08-03"},  # today
    ]})
    monkeypatch.setattr(saved_mod, "list_saved", lambda *a, **kw: [s])
    assert upcoming_anchor_legs(today=TODAY) == []


def test_quest_save_yields_both_flight_legs(monkeypatch):
    s = _save("quest", {
        "entry_iata": "NRT", "exit_iata": "ICN",
        "depart_date": "2026-09-10", "outbound_date": "2026-09-20",
    })
    monkeypatch.setattr(saved_mod, "list_saved", lambda *a, **kw: [s])
    anchors = upcoming_anchor_legs(today=TODAY)
    routes = [(a["from_iata"], a["to_iata"], a["depart_date"]) for a in anchors]
    assert ("YVR", "NRT", "2026-09-10") in routes
    assert ("ICN", "YVR", "2026-09-20") in routes
    assert all("Connects to your saved" in a["label"] for a in anchors)


def test_anchors_sorted_soonest_first_and_capped(monkeypatch):
    legs = [{"from_iata": f, "to_iata": t, "depart_date": d} for f, t, d in [
        ("AAA", "BBB", "2026-12-01"), ("CCC", "DDD", "2026-09-01"),
        ("EEE", "FFF", "2026-10-01"), ("GGG", "HHH", "2026-11-01"),
    ]]
    s = _save("detour", {"legs": legs})
    monkeypatch.setattr(saved_mod, "list_saved", lambda *a, **kw: [s])
    anchors = upcoming_anchor_legs(today=TODAY, limit=3)
    assert [a["depart_date"] for a in anchors] == [
        "2026-09-01", "2026-10-01", "2026-11-01"
    ]


# ── match_anchor: date window + dead routes ──────────────────────────────────

ANCHOR = {
    "saved_id": "s1", "title": "Quest", "kind": "quest",
    "from_iata": "ICN", "to_iata": "YVR",
    "from_city": "Seoul", "to_city": "Vancouver",
    "depart_date": "2026-09-20",
    "label": "Connects to your saved Seoul → Vancouver flight",
}


def test_match_anchor_requires_arrival_before_departure():
    ok = match_anchor(dest_iata="ICN", arrive_date=date(2026, 9, 18), anchors=[ANCHOR])
    assert ok is ANCHOR
    # same-day and after-departure arrivals never match
    assert match_anchor(dest_iata="ICN", arrive_date=date(2026, 9, 20), anchors=[ANCHOR]) is None
    assert match_anchor(dest_iata="ICN", arrive_date=date(2026, 9, 25), anchors=[ANCHOR]) is None


def test_match_anchor_requires_city_match_and_anchors():
    assert match_anchor(dest_iata="NRT", arrive_date=date(2026, 9, 18), anchors=[ANCHOR]) is None
    assert match_anchor(dest_iata="ICN", arrive_date=date(2026, 9, 18), anchors=[]) is None
    assert match_anchor(dest_iata=None, arrive_date=date(2026, 9, 18), anchors=[ANCHOR]) is None


def test_match_anchor_skips_dead_connecting_route(monkeypatch):
    import yonder.knowledge as knowledge

    monkeypatch.setattr(knowledge, "route_status", lambda o, d: "failed")
    assert match_anchor(
        dest_iata="ICN", arrive_date=date(2026, 9, 18),
        anchors=[ANCHOR], from_iata="PEK",
    ) is None
    monkeypatch.setattr(knowledge, "route_status", lambda o, d: "ok")
    assert match_anchor(
        dest_iata="ICN", arrive_date=date(2026, 9, 18),
        anchors=[ANCHOR], from_iata="PEK",
    ) is ANCHOR


# ── Prompt integration ───────────────────────────────────────────────────────

_TA_RESPONSE = json.dumps({
    "trip_kind": "getaway", "origin": "YVR", "destination": "YVR",
    "depart_date": "2026-09-01", "arrive_by": None, "currency": "CAD",
    "min_stop_days": 3, "max_stop_days": 5, "vibe": "adventure",
    "intent_summary": "x",
    "candidates": [{"iata": "MEX", "city": "Mexico City", "country": "MX",
                    "stay_days": 4, "why": "w", "vibe_tags": []}],
})


def _settings() -> Settings:
    s = Settings()
    s.xai_api_key = "test-key"  # type: ignore[attr-defined]
    return s


async def _capture(method: str, response: str, **kwargs) -> tuple[str, str]:
    client = GrokClient(_settings())
    captured: list[tuple[str, str]] = []

    async def fake_chat(system: str, user: str, **kw) -> str:
        captured.append((system, user))
        return response

    with patch.object(client, "_chat", side_effect=fake_chat):
        await getattr(client, method)(**kwargs)
    assert captured, "_chat was never called"
    return captured[0]


@pytest.mark.anyio
async def test_translate_adventure_no_anchor_prompt_unchanged():
    sys_p, user_p = await _capture(
        "translate_adventure", _TA_RESPONSE,
        prompt="somewhere fun", form={"origin": "YVR"}, today=TODAY,
    )
    assert "saved_anchor_legs" not in user_p
    assert "anchor" not in sys_p.lower()


@pytest.mark.anyio
async def test_translate_adventure_injects_anchors():
    sys_p, user_p = await _capture(
        "translate_adventure", _TA_RESPONSE,
        prompt="somewhere fun", form={"origin": "YVR"}, today=TODAY,
        anchor_legs=[ANCHOR],
    )
    payload = json.loads(user_p)
    rows = payload["saved_anchor_legs"]
    assert rows == [{"from_iata": "ICN", "to_iata": "YVR", "from_city": "Seoul",
                     "to_city": "Vancouver", "depart_date": "2026-09-20"}]
    assert "connection targets" in sys_p


@pytest.mark.anyio
async def test_plan_quest_injects_anchors_and_cold_path_clean():
    kwargs = dict(prompt="wander asia", vibe="adventure", home_iata="YVR",
                  depart_date=date(2026, 9, 1), quest_days=10)
    resp = json.dumps({"ideas": []})
    sys_cold, user_cold = await _capture("plan_quest", resp, **kwargs)
    assert "saved_anchor_legs" not in user_cold
    sys_a, user_a = await _capture("plan_quest", resp, anchor_legs=[ANCHOR], **kwargs)
    assert "saved_anchor_legs" in user_a
    assert "connection targets" in sys_a


@pytest.mark.anyio
async def test_plan_unified_injects_anchors_and_forks_cache_key(monkeypatch):
    from yonder.grok import _anchor_fingerprint

    assert _anchor_fingerprint(None) == ""
    assert _anchor_fingerprint([ANCHOR]) == "ICN-YVR-2026-09-20"

    resp = json.dumps({"escape": None, "detour": None, "quest": []})
    kwargs = dict(prompt="plan me a trip", vibe="adventure", origin="YVR",
                  depart_date=date(2026, 9, 1), use_cache=False)
    sys_cold, user_cold = await _capture("plan_unified", resp, **kwargs)
    assert "saved_anchor_legs" not in user_cold
    sys_a, user_a = await _capture("plan_unified", resp, anchor_legs=[ANCHOR], **kwargs)
    assert "saved_anchor_legs" in user_a
    assert "connection targets" in sys_a


# ── End-to-end: /explore search renders the ⚓ anchor badge ──────────────────

_E2E_DEPART = date.today() + timedelta(days=30)
_ANCHOR_DEPART = date.today() + timedelta(days=40)


def _saved_upcoming_trip() -> SavedItinerary:
    """Saved trip with a future NRT → ICN leg — anchor departs from NRT."""
    return _save("detour", {"legs": [
        {"from_iata": "NRT", "to_iata": "ICN",
         "depart_date": _ANCHOR_DEPART.isoformat()},
    ]}, sid="e2e-anchor")


def _mock_offer() -> FlightOffer:
    return FlightOffer(provider="mock", price=500.0, currency="CAD",
                       airlines=["AC"], price_kind="mock")


def _patch_search_pipeline(monkeypatch) -> None:
    """MOCK-mode /explore pipeline with all AI + disk IO stubbed out."""
    settings = Settings(testing=True, xai_api_key="test-key-anchor-e2e")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    # No leftover env keys / real AI calls
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)

    # No last-search disk IO
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)

    # Unified planner never reaches the network — force per-panel fallback
    async def _no_unified(self, *a: Any, **kw: Any):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(grok_module.GrokClient, "plan_unified", _no_unified)

    parsed = ParsedTrip(origin="YVR", destination="NRT",
                        depart_date=_E2E_DEPART, return_date=None,
                        currency="CAD")

    async def _fake_parse(self, *a: Any, **kw: Any) -> ParsedTrip:
        return parsed

    monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

    async def _fake_search(query: SearchQuery, *, settings=None, **kw: Any) -> UnifiedSearchResult:
        return UnifiedSearchResult(query=query, results=[], offers=[_mock_offer()])

    monkeypatch.setattr(web_module, "search_flights", _fake_search)

    # No saved-trip gaps (gap label would suppress the anchor label)
    monkeypatch.setattr(web_module, "detect_trip_gaps", lambda **kw: [])
    # Connecting route is never marked dead
    monkeypatch.setattr(knowledge_mod, "route_status", lambda o, d: "ok")


def _explore(monkeypatch, saves: list[SavedItinerary], force_mode: str = "escape") -> str:
    _patch_search_pipeline(monkeypatch)
    monkeypatch.setattr(saved_mod, "list_saved", lambda *a, **kw: saves)
    client = TestClient(web_module.app, raise_server_exceptions=True)
    resp = client.post("/explore", data={
        "prompt": "fly from Vancouver to Tokyo",
        "origin": "YVR",
        "depart": _E2E_DEPART.isoformat(),
        "vibe": "adventure",
        "force_mode": force_mode,
    })
    assert resp.status_code == 200
    return resp.text


def test_explore_renders_anchor_badge_for_upcoming_saved_leg(monkeypatch):
    """Search ending at NRT before the saved NRT → ICN leg departs → ⚓ badge."""
    html = _explore(monkeypatch, [_saved_upcoming_trip()])
    assert "Connects to your saved" in html
    assert "bp-anchor-label" in html


def test_explore_renders_no_anchor_badge_without_upcoming_saves(monkeypatch):
    html = _explore(monkeypatch, [])
    assert "Connects to your saved" not in html
    assert "bp-anchor-label" not in html


# ── End-to-end: Detour & Quest cards render the ⚓ anchor badge ──────────────

from yonder.adventure import (  # noqa: E402
    AdventureItinerary,
    AdventureRequest as AdvRequest,
    AdventureResult,
    PricedLeg,
    QuestIdea,
)


def _stub_itinerary(stop_iata: str, final_iata: str) -> AdventureItinerary:
    """YVR → stop → final, final leg arriving before the anchor departs."""
    return AdventureItinerary(
        kind="stopover", title=f"YVR → {stop_iata} → {final_iata}",
        stop_city=stop_iata, stop_iata=stop_iata, stay_days=3,
        legs=[
            PricedLeg(from_iata="YVR", to_iata=stop_iata, depart_date=_E2E_DEPART),
            PricedLeg(from_iata=stop_iata, to_iata=final_iata,
                      depart_date=_E2E_DEPART + timedelta(days=3)),
        ],
    )


def _stub_quest_ideas() -> list[QuestIdea]:
    """One idea exits at the anchor's departure city (NRT), one does not."""
    common = dict(depart_date=_E2E_DEPART,
                  outbound_date=_E2E_DEPART + timedelta(days=7))
    return [
        QuestIdea(entry_iata="KIX", exit_iata="NRT",
                  entry_city="Osaka", exit_city="Tokyo", **common),
        QuestIdea(entry_iata="SGN", exit_iata="BKK",
                  entry_city="Ho Chi Minh City", exit_city="Bangkok", **common),
    ]


def _patch_detour_quest_planners(monkeypatch) -> None:
    """Stub the Detour/Quest planners so /explore mix mode needs no network."""
    import yonder.encyclopedia as enc_mod

    async def _fake_translate(self, **kw):
        return AdvRequest(origin="YVR", destination="NRT",
                          depart_date=_E2E_DEPART), []

    monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fake_translate)

    async def _fake_plan_adventure(req, ideas, **kw) -> AdventureResult:
        # Final legs: TPE → NRT (matches the anchor) and HKG → BKK (does not)
        return AdventureResult(request=req, ideas=[], itineraries=[
            _stub_itinerary("TPE", "NRT"),
            _stub_itinerary("HKG", "BKK"),
        ])

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan_adventure)

    async def _fake_plan_quest(*a, **kw) -> list[QuestIdea]:
        return _stub_quest_ideas()

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

    async def _no_briefs(*a, **kw) -> dict:
        return {}

    monkeypatch.setattr(enc_mod, "briefs_for_stops", _no_briefs)


def _card_with(html: str, marker: str) -> str:
    cards = [c for c in html.split("<article") if marker in c]
    assert cards, f"no rendered card contains {marker!r}"
    return cards[0]


def test_explore_mix_shows_detour_button_card_not_eager_cards(monkeypatch):
    """Mix search: Detour is now on-demand — main page shows a button card, not eager boarding passes.

    Both Detour and Quest are on-demand only (/api/detour/plan, /api/quest/plan).
    """
    _patch_detour_quest_planners(monkeypatch)
    html = _explore(monkeypatch, [_saved_upcoming_trip()], force_mode="mix")

    # Detour button card must be present; no eagerly-rendered detour boarding passes
    assert "btn-plan-detour" in html or "Plan a Detour" in html
    assert 'data-stop-iata="TPE"' not in html, "Detour is on-demand: boarding passes must not appear on /explore"
    assert 'data-stop-iata="HKG"' not in html

    # Quest is also on-demand
    assert "btn-plan-quest" in html or "Plan a Quest" in html
    assert 'data-quest-exit="NRT"' not in html
    assert 'data-quest-exit="BKK"' not in html


def test_detour_plan_api_renders_anchor_badge(monkeypatch):
    """/api/detour/plan: itinerary with final leg ending at NRT gets the ⚓ anchor badge.

    Detour is on-demand (button-triggered) — anchor badges are applied during
    the partial template render inside the /api/detour/plan endpoint.
    """
    import yonder.saved as _saved_mod
    import yonder.encyclopedia as enc_mod
    from datetime import timedelta

    _patch_search_pipeline(monkeypatch)
    monkeypatch.setattr(_saved_mod, "list_saved", lambda *a, **kw: [_saved_upcoming_trip()])

    # Stub plan_adventure to return two itineraries: TPE→NRT (connects) and HKG→BKK (does not)
    async def _fake_plan(req, ideas, **kw):
        return AdventureResult(request=req, ideas=ideas or [], itineraries=[
            _stub_itinerary("TPE", "NRT"),
            _stub_itinerary("HKG", "BKK"),
        ])

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
    monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: [], raising=False)

    async def _no_briefs(*a, **kw):
        return {}

    monkeypatch.setattr(enc_mod, "briefs_for_stops", _no_briefs)

    client = TestClient(web_module.app, raise_server_exceptions=True)
    resp = client.post("/api/detour/plan", data={
        "prompt": "fly from Vancouver to Tokyo",
        "origin": "YVR",
        "destination": "NRT",
        "depart": _E2E_DEPART.isoformat(),
        "vibe": "adventure",
    })
    assert resp.status_code == 200
    result = resp.json()
    assert result.get("ok") is True, f"Detour plan failed: {result.get('error')}"
    html = result["html"]

    # TPE→NRT matches the anchor; HKG→BKK does not
    det_match = _card_with(html, 'data-stop-iata="TPE"')
    assert "bp-anchor-label" in det_match
    assert "Connects to your saved" in det_match

    det_miss = _card_with(html, 'data-stop-iata="HKG"')
    assert "bp-anchor-label" not in det_miss


def test_explore_mix_renders_no_anchor_badge_without_upcoming_saves(monkeypatch):
    """Without upcoming saves, no anchor badges appear; Detour button is shown."""
    _patch_detour_quest_planners(monkeypatch)
    html = _explore(monkeypatch, [], force_mode="mix")
    # Detour is on-demand: no boarding passes in the main search response
    assert 'data-stop-iata="TPE"' not in html
    # Quest is on-demand: no quest cards in initial search response
    assert 'data-quest-exit="NRT"' not in html
    assert 'data-quest-exit="BKK"' not in html
    assert "Connects to your saved" not in html
    assert "bp-anchor-label" not in html
    # Both on-demand buttons should be present
    assert "btn-plan-detour" in html or "Plan a Detour" in html
