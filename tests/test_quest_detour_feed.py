"""Manual Plan Quest path feeds the Detour section — regression for the
`/api/quest/plan` endpoint returning `detour_html`.

The eager job path is covered by the status-endpoint flow; this file proves
the manual/retry Plan Quest button gets the same quest-seeded Detour feed:

1. A successful manual quest whose legs have connection stops returns
   `detour_html` containing the quest-snapshot card.
2. Direct-only legs (no stops) return an empty `detour_html` — the Detour
   section stays exactly as today (button-triggered live search only).

DB writes are stubbed (store/find) — the storage layer itself is covered by
tests/test_detour_candidates.py against an isolated schema.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.adventure as adventure_module
import yonder.detour_candidates as dc_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import PricedLeg
from yonder.config import Settings
from yonder.types import FlightOffer, Segment

_FUTURE = (date.today() + timedelta(days=40)).isoformat()
_PROMPT = "overland food adventure through southeast asia"
_RAW_IDEA = {
    "entry_iata": "HAN",
    "exit_iata": "BKK",
    "entry_city": "Hanoi",
    "exit_city": "Bangkok",
    "overland_narrative": "Ride the Reunification Express south.",
    "transport": ["Reunification Express"],
    "highlights": ["Hội An"],
}


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)
    monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)


@pytest.fixture(autouse=True)
def _stub_candidate_db(monkeypatch):
    """Keep candidate persistence away from the real detour_candidates table."""
    stored: list[dict] = []

    def _fake_store(cands):
        stored.extend(cands)
        return len(cands)

    monkeypatch.setattr(dc_module, "store_candidates", _fake_store)
    monkeypatch.setattr(dc_module, "find_candidates", lambda *a, **kw: [])
    yield stored


def _make_priced_leg(origin: str, dest: str, *, stops: int) -> PricedLeg:
    segments = []
    if stops >= 1:
        segments = [
            Segment(origin=origin, destination="CDG"),
            Segment(origin="CDG", destination=dest),
        ]
    return PricedLeg(
        from_iata=origin,
        to_iata=dest,
        depart_date=date.fromisoformat(_FUTURE),
        offer=FlightOffer(
            provider="testair",
            price=500.0,
            currency="USD",
            price_kind="live",
            display_price_base="~US$500",
            display_price="~US$500",
            stops_out=stops,
            segments_out=segments,
        ),
        google_flights_url=f"https://www.google.com/travel/flights?q={origin}-{dest}",
    )


def _wire_quest(monkeypatch, *, stops: int):
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    async def _fake_plan_quest(*a, **kw):
        from yonder.adventure import plan_quest as _real_plan_quest

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            return _make_priced_leg(origin, dest, stops=stops)

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)

        async def _fake_pick(*a2, **kw2):
            return ["testair"]

        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        return await _real_plan_quest(
            a[0] if a else kw.get("prompt", _PROMPT),
            a[1] if len(a) > 1 else kw.get("vibe", "adventure"),
            a[2] if len(a) > 2 else kw.get("home_iata", "YVR"),
            a[3] if len(a) > 3 else kw.get("depart_date", date.fromisoformat(_FUTURE)),
            settings,
            raw_ideas=[dict(_RAW_IDEA)],
        )

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

    try:
        import yonder.saved as saved_module
        monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
    except Exception:
        pass


def _post_quest(client: TestClient) -> dict:
    resp = client.post(
        "/api/quest/plan",
        data={
            "prompt": _PROMPT,
            "origin": "YVR",
            "depart": _FUTURE,
            "vibe": "adventure",
            "quest_days": "10",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_manual_quest_with_stops_returns_detour_html(client, monkeypatch, _stub_candidate_db):
    _wire_quest(monkeypatch, stops=1)
    result = _post_quest(client)
    assert result["ok"] is True
    detour_html = result.get("detour_html") or ""
    assert "Quest snapshot" in detour_html
    assert "CDG" in detour_html  # the connection stop
    # candidates were persisted to the shared pool
    assert any(c["route_key"].startswith("YVR|CDG|") for c in _stub_candidate_db)


def test_manual_quest_direct_legs_leave_detour_untouched(client, monkeypatch):
    _wire_quest(monkeypatch, stops=0)
    result = _post_quest(client)
    assert result["ok"] is True
    assert (result.get("detour_html") or "") == ""
