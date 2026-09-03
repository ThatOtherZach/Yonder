"""Tests for the on-demand Quest endpoint (/api/quest/plan) — Task 467.

Covers:
  - Happy path: well-formed JSON with html field containing quest cards
  - Timeout path: first attempt times out → retry → timeout again → friendly message
  - Search response contains no Quest AI call (plan_quest is not called from /explore)
  - Context round-trip: avoid / visited / anchor_legs reach plan_quest
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

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


def _make_priced_leg(origin: str, dest: str) -> PricedLeg:
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
        ),
        google_flights_url=f"https://www.google.com/travel/flights?q={origin}-{dest}",
    )


def _wire_quest(monkeypatch, *, ideas: list[dict] | None = None, raises=None):
    """Patch settings + plan_quest for /api/quest/plan tests."""
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    async def _fake_plan_quest(*a, **kw):
        if raises is not None:
            raise raises
        return_ideas = ideas if ideas is not None else [dict(_RAW_IDEA)]

        # Turn each raw idea into a QuestIdea via adventure.plan_quest's pipeline
        # by delegating to the real pricer with a patched _price_leg.
        from yonder.adventure import plan_quest as _real_plan_quest

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            return _make_priced_leg(origin, dest)

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)

        async def _fake_pick(*a2, **kw2):
            return ["testair"]

        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        # We want plan_quest to run but skip the Grok call (raw_ideas supplied)
        return await _real_plan_quest(
            a[0] if a else kw.get("prompt", _PROMPT),
            a[1] if len(a) > 1 else kw.get("vibe", "adventure"),
            a[2] if len(a) > 2 else kw.get("home_iata", "YVR"),
            a[3] if len(a) > 3 else kw.get("depart_date", date.fromisoformat(_FUTURE)),
            settings,
            raw_ideas=return_ideas,
        )

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

    # Make upcoming_anchor_legs fast/silent
    try:
        import yonder.saved as saved_module
        monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
    except Exception:
        pass


def _post_quest(client: TestClient, **extra) -> dict:
    data = {
        "prompt": _PROMPT,
        "origin": "YVR",
        "depart": _FUTURE,
        "vibe": "adventure",
        "quest_days": "10",
    }
    data.update(extra)
    resp = client.post("/api/quest/plan", data=data)
    assert resp.status_code == 200
    return resp.json()


class TestQuestEndpointHappyPath:
    def test_returns_json_with_ok_and_html(self, client, monkeypatch):
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        assert result["ok"] is True
        assert "html" in result
        assert len(result["html"]) > 50

    def test_html_contains_quest_card(self, client, monkeypatch):
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        html = result["html"]
        assert "quest-results-card" in html
        assert "HAN" in html or "Hanoi" in html
        assert "BKK" in html or "Bangkok" in html

    def test_html_contains_boarding_pass_card(self, client, monkeypatch):
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        # Should have a boarding pass with the .is-quest class
        assert "is-quest" in result["html"]

    def test_no_ai_key_returns_friendly_note(self, client, monkeypatch):
        """No Grok key → JSON ok=False with settings-link HTML."""
        settings = Settings(testing=True, xai_api_key="", byom_base_url="", byom_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        result = _post_quest(client)
        assert result["ok"] is False
        assert "Settings" in result.get("error", "") or "Settings" in result.get("html", "")

    def test_empty_ideas_returns_empty_state(self, client, monkeypatch):
        """plan_quest returning [] → ok=False + friendly empty-state HTML."""
        _wire_quest(monkeypatch, ideas=[])
        result = _post_quest(client)
        assert result["ok"] is False
        assert "html" in result
        # quest-empty-note or similar friendly text
        html = result["html"]
        assert "Quest" in html

    def test_missing_prompt_returns_error(self, client, monkeypatch):
        _wire_quest(monkeypatch)
        resp = client.post(
            "/api/quest/plan",
            data={"origin": "YVR", "depart": _FUTURE, "vibe": "adventure"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["ok"] is False
        assert "prompt" in result.get("error", "").lower()


class TestQuestEndpointTimeout:
    def test_timeout_then_retry_then_friendly_message(self, client, monkeypatch):
        """Two consecutive timeouts → friendly 'took too long' message, no crash."""
        call_count = 0

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _timeout_quest(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        monkeypatch.setattr(web_module, "plan_quest", _timeout_quest)

        result = _post_quest(client)
        assert result["ok"] is False
        assert "too long" in result.get("error", "").lower() or "try again" in result.get("error", "").lower()
        assert "html" in result
        # Friendly HTML with retry button
        assert "quest-results-card" in result["html"]
        # Exactly 2 attempts (first try + one retry)
        assert call_count == 2

    def test_timeout_message_in_html(self, client, monkeypatch):
        """The returned HTML contains a human-readable timeout message."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _always_timeout(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(web_module, "plan_quest", _always_timeout)

        result = _post_quest(client)
        html = result.get("html", "")
        # Should render something, not empty
        assert len(html) > 20


class TestMainSearchNoQuestAICall:
    """Eager Quest never blocks /explore and skips the AI without a key."""

    def test_explore_without_ai_key_never_calls_plan_quest(self, client, monkeypatch):
        """No AI key → the eager Quest job degrades without invoking plan_quest."""
        import yonder.grok as grok_module

        quest_calls: list = []

        async def _fail_quest(*a, **kw):
            quest_calls.append(a)
            raise AssertionError("plan_quest must not be called without an AI key")

        settings = Settings(testing=True, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "plan_quest", _fail_quest)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        # No AI key → no AI calls at all; should still return the page.
        resp = client.post(
            "/explore",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        # Should not have raised (plan_quest never called for keyless searches)
        assert not quest_calls, "plan_quest was called from /explore without an AI key"

    def test_explore_page_kicks_off_eager_quest(self, client, monkeypatch):
        """A search with escape results renders the Quest progress placeholder (non-blocking)."""
        import yonder.grok as grok_module
        from datetime import timedelta
        from yonder.grok import ParsedTrip

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Keep the background eager Quest task away from real AI calls
        async def _stub_quest(*a, **kw):
            return []

        monkeypatch.setattr(web_module, "plan_quest", _stub_quest)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        # Patch parse_natural_language so the escape fallback path (used when
        # the unified cold-start call fails or is skipped) returns a valid trip.
        # The unified call in web.py uses a pre-created httpx client which bypasses
        # the _chat class-level patch, so we patch at the method level instead.
        _parsed = ParsedTrip(
            origin="YVR",
            destination="NRT",
            depart_date=date.today() + timedelta(days=40),
            currency="USD",
        )

        async def _fake_parse(self, *a, **kw):
            return _parsed

        monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

        # Also patch plan_unified so the unified cold-start call produces an
        # escape ParsedTrip without making real HTTP calls.
        async def _fake_plan_unified(self, *a, **kw):
            return {"escape": _parsed, "detour_cities": None, "quest_pairs": []}

        monkeypatch.setattr(grok_module.GrokClient, "plan_unified", _fake_plan_unified)

        # Stub search_flights to avoid real API calls
        async def _fake_search(query, *a, **kw):
            from yonder.types import UnifiedSearchResult
            return UnifiedSearchResult(query=query, offers=[], results=[])

        monkeypatch.setattr(web_module, "search_flights", _fake_search)

        resp = client.post(
            "/explore",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        # The Quest results panel should be present
        assert "quest-results" in resp.text
        # Eager Quest: a background job placeholder polls for results
        assert "data-quest-job" in resp.text
        # No rendered quest boarding pass — the initial render never blocks on
        # Quest. The shared stylesheet legitimately contains `.is-quest`.
        assert '<article class="boarding-pass is-adventure is-quest' not in resp.text


class TestQuestRetryAndSaveButtons:
    """Verify that injected HTML always includes the elements JS re-binds to."""

    def test_timeout_html_includes_retry_button_for_rebind(self, client, monkeypatch):
        """Timeout response must include #btn-plan-quest so bootPlanQuestButton
        can re-bind a retry handler after injecting the error HTML."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _always_timeout(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(web_module, "plan_quest", _always_timeout)

        result = _post_quest(client)
        html = result.get("html", "")
        # The retry button must be present in the error HTML so JS can rebind it
        assert 'id="btn-plan-quest"' in html, (
            "Timeout HTML must contain #btn-plan-quest so the retry click can be rebound"
        )
        assert "btn-plan-quest" in html

    def test_non_timeout_error_html_includes_retry_button(self, client, monkeypatch):
        """Any non-timeout error (AI unreachable, bad payload, etc.) must also
        inject HTML with #btn-plan-quest so JS can re-bind the retry handler."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _always_fail(*a, **kw):
            raise RuntimeError("AI backend unreachable")

        monkeypatch.setattr(web_module, "plan_quest", _always_fail)

        result = _post_quest(client)
        html = result.get("html", "")
        assert 'id="btn-plan-quest"' in html, (
            "Error HTML must contain #btn-plan-quest for JS retry rebind"
        )

    def test_success_html_includes_save_share_buttons(self, client, monkeypatch):
        """Successfully planned Quest cards must include .btn-save-and-share
        elements so bootSaveAndShare() can bind them after injection."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        assert result["ok"] is True
        html = result["html"]
        # Save/share buttons must be present for bootSaveAndShare() to bind
        assert "btn-save-and-share" in html, (
            "Quest result cards must include .btn-save-and-share buttons "
            "so they are functional after dynamic injection"
        )


class TestQuestContextRoundTrip:
    """Confirm avoid/visited/anchor_legs reach plan_quest."""

    def test_avoid_and_visited_reach_planner(self, client, monkeypatch):
        """Settings avoid/visited lists are passed into plan_quest."""
        received: dict = {}

        settings = Settings(
            testing=True,
            xai_api_key="test-key",
            avoid_countries="CN,RU",
            visited_countries="JP,KR",
        )
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _capture_quest(*a, **kw):
            received.update(kw)
            received["args"] = a
            return []  # empty but not an error

        monkeypatch.setattr(web_module, "plan_quest", _capture_quest)

        try:
            import yonder.saved as saved_module
            monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
        except Exception:
            pass

        _post_quest(client)

        assert "avoid" in received, "avoid list not passed to plan_quest"
        assert "visited" in received, "visited list not passed to plan_quest"
        # Avoid list contains CN and RU
        avoid = received.get("avoid") or []
        assert "CN" in avoid or "cn" in [a.lower() for a in avoid]

    def test_anchor_legs_reach_planner(self, client, monkeypatch):
        """Upcoming saved anchor legs are passed into plan_quest."""
        received: dict = {}

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        _fake_anchors = [{"from_iata": "YVR", "to_iata": "NRT", "depart_date": _FUTURE}]

        try:
            import yonder.saved as saved_module
            monkeypatch.setattr(
                saved_module, "upcoming_anchor_legs", lambda **kw: _fake_anchors, raising=False
            )
        except Exception:
            pass

        async def _capture_quest(*a, **kw):
            received.update(kw)
            return []

        monkeypatch.setattr(web_module, "plan_quest", _capture_quest)

        _post_quest(client)

        anchor_legs = received.get("anchor_legs") or []
        assert anchor_legs == _fake_anchors, (
            f"anchor_legs not passed correctly: {anchor_legs!r}"
        )
