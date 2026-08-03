"""End-to-end Quest flow coverage — updated for on-demand Quest (Task 467).

Quest is no longer triggered by force_mode=quest on /explore; it runs via
POST /api/quest/plan.  These tests wire the same pricer + idea fixtures but
call the new endpoint.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import yonder.adventure as adventure_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import PricedLeg
from yonder.config import Settings
from yonder.types import FlightOffer

_FUTURE = (date.today() + timedelta(days=40)).isoformat()
_PROMPT = "overland food adventure through southeast asia"

_RAW_IDEA = {
    "entry_iata": "HAN",
    "exit_iata": "BKK",
    "entry_city": "Hanoi",
    "exit_city": "Bangkok",
    "overland_narrative": "Ride the Reunification Express south, then cross into Thailand.",
    "transport": ["Reunification Express", "Mekong slow boat"],
    "highlights": ["Hội An", "Phnom Penh"],
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
    monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)


def _wire(monkeypatch, *, priced: bool) -> None:
    """Patch settings + plan_quest + pricer so /api/quest/plan runs offline.

    priced=False → the pricer returns mock offers (fare_missing path).
    priced=True  → the pricer returns real live offers with display prices.
    """
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    # plan_quest: always returns one raw idea for the pricer to price.
    # The real plan_quest path is invoked with raw_ideas= so no Grok call fires.
    async def _fake_plan_quest(*a: Any, **kw: Any):
        raw_ideas = [dict(_RAW_IDEA)]
        from yonder.adventure import plan_quest as _real

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            if priced:
                offer = FlightOffer(
                    provider="testair",
                    price=500.0,
                    currency="USD",
                    price_kind="live",
                    display_price_base="~US$500",
                    display_price="~US$500",
                )
            else:
                offer = FlightOffer(
                    provider="mock",
                    price=123.0,
                    currency="USD",
                    price_kind="mock",
                )
            return PricedLeg(
                from_iata=origin,
                to_iata=dest,
                depart_date=depart,
                offer=offer,
                google_flights_url=f"https://www.google.com/travel/flights?q={origin}-{dest}",
            )

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)

        async def _fake_pick(*a2: Any, **kw2: Any):
            return ["mock" if not priced else "testair"]

        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        return await _real(
            a[0] if a else kw.get("prompt", _PROMPT),
            a[1] if len(a) > 1 else kw.get("vibe", "adventure"),
            a[2] if len(a) > 2 else kw.get("home_iata", "YVR"),
            a[3] if len(a) > 3 else kw.get("depart_date", date.fromisoformat(_FUTURE)),
            settings,
            raw_ideas=raw_ideas,
        )

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

    # Silence anchor-legs DB read
    try:
        import yonder.saved as saved_module
        monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
    except Exception:
        pass


def _post_quest(client: TestClient) -> str:
    """Call the on-demand Quest endpoint and return rendered HTML."""
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
    payload = resp.json()
    return payload.get("html", "")


class TestQuestCardsRender:
    def test_quest_card_renders_with_check_fares_on_mock_path(
        self, client, monkeypatch
    ):
        """Mock pricer → fare-missing legs → card shows Check Fares buttons."""
        _wire(monkeypatch, priced=False)
        html = _post_quest(client)

        # At least one .is-quest card rendered
        quest_cards = re.findall(r'class="boarding-pass is-adventure is-quest"', html)
        assert len(quest_cards) >= 1, "expected at least one .is-quest card"

        # entry_iata / exit_iata present on the card
        assert 'data-quest-entry="HAN"' in html
        assert 'data-quest-exit="BKK"' in html
        assert "Hanoi" in html
        assert "Bangkok" in html

        # Stub text
        assert "two one-way tickets" in html

        # Mock fares are never shown — Check Fares buttons appear instead
        assert "btn-check-fares" in html, (
            "Check Fares button missing on fare-missing quest legs"
        )
        # Both quest legs get a check-fares slot targeting the right routes
        assert 'data-cf-origin="YVR"' in html
        assert 'data-cf-dest="HAN"' in html
        assert 'data-cf-origin="BKK"' in html
        # The invented mock price must never leak into the visible card
        card_before_end = html.split("</article>")[0]
        visible = re.sub(r"<script[^>]*>.*?</script>", "", card_before_end, flags=re.S)
        assert "123" not in visible

    def test_quest_card_shows_fares_on_priced_path(self, client, monkeypatch):
        """Live-priced legs → both fares + combined total, no Check Fares."""
        _wire(monkeypatch, priced=True)
        html = _post_quest(client)

        assert 'class="boarding-pass is-adventure is-quest"' in html
        card = html.split("</article>")[0]
        assert "~US$500" in card
        assert "two one-way tickets" in card
        assert "btn-check-fares" not in card
        # Combined total = 500 + 500
        assert "1,000" in card or "1000" in card

    def test_bad_grok_payload_yields_no_quest_cards_without_crash(
        self, client, monkeypatch
    ):
        """plan_quest returning [] → 0 cards, friendly empty state, ok=False."""
        _wire(monkeypatch, priced=False)

        # Override: return empty list
        async def _empty_quest(*a: Any, **kw: Any) -> list:
            return []

        monkeypatch.setattr(web_module, "plan_quest", _empty_quest)

        resp = client.post(
            "/api/quest/plan",
            data={"prompt": _PROMPT, "origin": "YVR", "depart": _FUTURE, "vibe": "adventure"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is False
        html = payload.get("html", "")
        # Friendly empty state, no boarding pass
        assert "is-quest" not in html
        assert "quest-empty-note" in html or "Quest needs" in html

    def test_missing_ai_key_shows_friendly_quest_note(self, client, monkeypatch):
        """No Grok/BYOM key at all → ok=False with a Settings hint."""
        settings = Settings(
            testing=True, xai_api_key="", byom_base_url="", byom_api_key=""
        )
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        resp = client.post(
            "/api/quest/plan",
            data={"prompt": _PROMPT, "origin": "YVR", "depart": _FUTURE, "vibe": "adventure"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is False
        assert "Settings" in (payload.get("error", "") + payload.get("html", ""))
        assert "is-quest" not in payload.get("html", "")
