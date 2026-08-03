"""Anchored planning (Task: plan new trips around saved flights).

Covers:
- anchor extraction: upcoming legs only, past dates excluded, sorted + capped
- no-anchor cold path: prompts byte-free of anchor material
- prompt injection: anchors appear in system + user payload of all 3 planners
- date-window enforcement in match_anchor (arrive strictly before departure)
- dead-route filtering of the connecting leg via route knowledge
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

import yonder.saved as saved_mod
from yonder.adventure import match_anchor
from yonder.config import Settings
from yonder.grok import GrokClient
from yonder.saved import SavedItinerary, upcoming_anchor_legs

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
    monkeypatch.setattr(saved_mod, "list_saved", lambda limit=25: [])
    assert upcoming_anchor_legs(today=TODAY) == []


def test_past_dated_legs_never_anchor(monkeypatch):
    s = _save("detour", {"legs": [
        {"from_iata": "ICN", "to_iata": "YVR", "depart_date": "2026-07-01"},
        {"from_iata": "ICN", "to_iata": "YVR", "depart_date": "2026-08-03"},  # today
    ]})
    monkeypatch.setattr(saved_mod, "list_saved", lambda limit=25: [s])
    assert upcoming_anchor_legs(today=TODAY) == []


def test_quest_save_yields_both_flight_legs(monkeypatch):
    s = _save("quest", {
        "entry_iata": "NRT", "exit_iata": "ICN",
        "depart_date": "2026-09-10", "outbound_date": "2026-09-20",
    })
    monkeypatch.setattr(saved_mod, "list_saved", lambda limit=25: [s])
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
    monkeypatch.setattr(saved_mod, "list_saved", lambda limit=25: [s])
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
