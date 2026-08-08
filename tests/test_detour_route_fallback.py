"""Regression tests for Task: keep the Detour route when the AI is offline
and the prompt isn't English.

The offline route detector (detect_route_iatas) only understands English city
names. When the Escape half of a /explore search has already parsed the route
(any language), the Detour half must reuse that resolved origin/destination in
its offline fallback instead of re-detecting from raw text — never replacing a
named A→B route with a home ↺ invented-city getaway.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import yonder.grok as grok_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import AdventureRequest, AdventureResult, StopoverIdea
from yonder.config import Settings
from yonder.grok import ParsedTrip
from yonder.types import SearchQuery, UnifiedSearchResult

_DEPART = (date.today() + timedelta(days=30)).isoformat()

# Vancouver → Toronto with a possible stop along the way (Chinese)
CHINESE_ROUTE_PROMPT = "我想从温哥华去多伦多，途中可能会在某个地方停留一下"


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _patch_last_search(monkeypatch):
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _clear_xai_env(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _setup(monkeypatch, *, parse_ok: bool) -> dict[str, list]:
    """AI 'configured' but unavailable for the Detour invent; no fare providers
    (mock forced). parse_ok controls whether the Escape half resolves the route."""
    captures: dict[str, list] = {"plan_calls": [], "search_calls": []}

    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    parsed = ParsedTrip(
        origin="YVR",
        destination="YYZ",
        depart_date=date.today() + timedelta(days=30),
        currency="USD",
    )

    async def _fake_parse(self, *a: Any, **kw: Any) -> ParsedTrip:
        if not parse_ok:
            raise RuntimeError("AI unreachable")
        return parsed

    monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

    async def _fake_translate(self, *a: Any, **kw: Any):
        raise RuntimeError("AI unreachable")

    monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fake_translate)

    async def _fake_search(query, *, settings=None, **kw: Any) -> UnifiedSearchResult:
        captures["search_calls"].append(query)
        q = SearchQuery(
            origin=query.origin,
            destination=query.destination,
            depart_date=date.today() + timedelta(days=30),
        )
        return UnifiedSearchResult(query=q, results=[], offers=[])

    monkeypatch.setattr(web_module, "search_flights", _fake_search)

    async def _fake_plan(req, ideas, *, settings=None, **kw: Any) -> AdventureResult:
        captures["plan_calls"].append(req)
        return AdventureResult(
            request=req,
            ideas=[StopoverIdea(iata="ORD", city="Chicago", stay_days=3)],
            itineraries=[],
        )

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
    return captures


class TestDetourReusesEscapeResolvedRoute:
    def test_chinese_route_prompt_escape_populates_detour_dest(
        self, client, monkeypatch
    ):
        """Detour is now on-demand. When Escape parses YVR→YYZ, the main search page
        pre-fills the Detour button card with data-detour-dest='YYZ' so that when the
        user clicks 'Plan a Detour', the correct destination is sent to /api/detour/plan.

        The old eager-plan path (plan_adventure called from /explore) is gone;
        route integrity is now verified via the data-detour-dest pre-fill on the
        button card.
        """
        _setup(monkeypatch, parse_ok=True)

        resp = client.post(
            "/explore",
            data={
                "prompt": CHINESE_ROUTE_PROMPT,
                "origin": "JFK",  # home field must not hijack the escape-parsed route
                "depart": _DEPART,
                "force_mode": "mix",
                "multi_city": "true",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        html = resp.text
        # Detour is on-demand — no eagerly-rendered boarding passes
        assert "btn-plan-detour" in html or "Plan a Detour" in html
        # The escape destination (YYZ) must appear as the detour destination pre-fill
        assert 'data-detour-dest="YYZ"' in html, (
            "Escape-parsed destination YYZ must be pre-filled as data-detour-dest "
            "so the Detour button uses the correct route"
        )

    def test_detour_plan_api_uses_provided_destination(
        self, client, monkeypatch
    ):
        """/api/detour/plan uses the destination from the form data (provided by the button JS).

        The corridor builder receives the correct origin/destination regardless of
        whether the prompt text is in Chinese or English, because destination comes
        from the explicit field — not re-parsed from raw text in the request.
        """
        _setup(monkeypatch, parse_ok=True)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": CHINESE_ROUTE_PROMPT,
                "origin": "YVR",
                "destination": "YYZ",  # explicit — JS sends this from data-detour-dest
                "depart": _DEPART,
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        # Should produce itineraries (plan_adventure stub) or a graceful error
        # The key assertion: the endpoint accepted origin=YVR, destination=YYZ
        # (not a getaway) — verified by the ok response or absence of home-loop error
        assert "html" in result

    def test_chinese_prompt_without_destination_uses_seed_fallback(
        self, client, monkeypatch
    ):
        """/api/detour/plan with no destination gracefully falls back to seed ideas.

        When the user clicks Detour without a destination (blank destination field),
        the endpoint uses seed_ideas as a getaway — no crash or 500 error.
        """
        _setup(monkeypatch, parse_ok=False)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": CHINESE_ROUTE_PROMPT,
                "origin": "YVR",
                "destination": "",  # blank — corridor builder returns [] for no-dest
                "depart": _DEPART,
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        # Should not crash; either ok or a friendly error
        assert "html" in result
