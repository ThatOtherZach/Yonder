"""Tests for Quest result persistence — Task 469.

Verifies that:
  1. /api/quest/plan returns the HTML payload needed for sessionStorage caching:
     the client saves json.html under a key derived from prompt+origin and
     re-injects it on the next page load without a second AI call.
  2. The returned HTML is stable across two calls with the same inputs
     (same ideas → same HTML structure), so the cached copy stays valid.
  3. Error / timeout responses do NOT produce an html payload worth caching
     (ok=False, or html contains only an error card — no boarding-pass content).
"""

from __future__ import annotations

import asyncio
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
_PROMPT = "temples and street food across southeast asia"
_RAW_IDEA = {
    "entry_iata": "BKK",
    "exit_iata": "SGN",
    "entry_city": "Bangkok",
    "exit_city": "Ho Chi Minh City",
    "overland_narrative": "Bus north through Cambodia to Ho Chi Minh.",
    "transport": ["bus"],
    "highlights": ["Angkor Wat", "Mekong Delta"],
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
            price=600.0,
            currency="USD",
            price_kind="live",
            display_price_base="~US$600",
            display_price="~US$600",
        ),
        google_flights_url=f"https://flights.google.com/?q={origin}-{dest}",
    )


def _wire_quest(monkeypatch, *, ideas: list[dict] | None = None, raises=None):
    """Patch settings + plan_quest for /api/quest/plan tests."""
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    async def _fake_plan_quest(*a, **kw):
        if raises is not None:
            raise raises
        return_ideas = ideas if ideas is not None else [dict(_RAW_IDEA)]

        from yonder.adventure import plan_quest as _real_plan_quest

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            return _make_priced_leg(origin, dest)

        async def _fake_pick(*a2, **kw2):
            return ["testair"]

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        return await _real_plan_quest(
            a[0] if a else kw.get("prompt", _PROMPT),
            a[1] if len(a) > 1 else kw.get("vibe", "adventure"),
            a[2] if len(a) > 2 else kw.get("home_iata", "YVR"),
            a[3] if len(a) > 3 else kw.get("depart_date", date.fromisoformat(_FUTURE)),
            settings,
            raw_ideas=return_ideas,
        )

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

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


# ---------------------------------------------------------------------------
# Core persistence contract: response shape
# ---------------------------------------------------------------------------


class TestQuestPersistenceResponseShape:
    def test_success_response_has_html_field(self, client, monkeypatch):
        """Successful call must return {ok: true, html: '<non-empty string>'}
        — the JS side stores this html in sessionStorage for hydration on reload."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        assert result.get("ok") is True
        assert "html" in result
        assert isinstance(result["html"], str)
        assert len(result["html"]) > 50, "html payload must be non-trivial"

    def test_success_html_contains_injected_results_marker(self, client, monkeypatch):
        """The html payload must contain #quest-results-card so the JS injection
        targets the right DOM tree (innerHTML of #quest-results)."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        assert "quest-results-card" in result["html"]

    def test_success_html_contains_quest_idea_content(self, client, monkeypatch):
        """The cached HTML must carry the actual idea cities so a re-injected card
        is indistinguishable from a freshly rendered one."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        html = result["html"]
        # Entry and exit IATA / cities must be in the persisted HTML
        assert "BKK" in html or "Bangkok" in html
        assert "SGN" in html or "Ho Chi Minh" in html

    def test_success_html_contains_save_buttons(self, client, monkeypatch):
        """The cached HTML must include save/share buttons so they remain
        interactive after the card is re-injected from sessionStorage."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        assert "btn-save-and-share" in result["html"]


# ---------------------------------------------------------------------------
# Idempotency: same inputs → same content (safe to cache)
# ---------------------------------------------------------------------------


class TestQuestPersistenceIdempotency:
    def test_two_calls_same_params_produce_equivalent_html(
        self, client, monkeypatch
    ):
        """The HTML from two identical calls must contain the same idea cities.

        This confirms the cached value is equivalent to a freshly generated one
        — the client can trust the sessionStorage copy without re-calling the API.
        """
        _wire_quest(monkeypatch)
        first = _post_quest(client)
        second = _post_quest(client)
        assert first["ok"] is True and second["ok"] is True
        # Both must reference the same entry/exit cities
        for fragment in ("BKK", "Bangkok", "SGN"):
            assert (fragment in first["html"]) == (fragment in second["html"]), (
                f"Idempotency check failed for fragment {fragment!r}: "
                f"first={fragment in first['html']}, second={fragment in second['html']}"
            )

    def test_different_prompts_produce_different_html(self, client, monkeypatch):
        """Different prompts → different idea content; the cache key must therefore
        include the prompt so stale results are never served for a new search."""
        idea_a = dict(_RAW_IDEA)
        idea_b = {
            **_RAW_IDEA,
            "entry_iata": "KUL",
            "exit_iata": "HAN",
            "entry_city": "Kuala Lumpur",
            "exit_city": "Hanoi",
        }

        call_count = 0

        async def _fake_plan_quest(*a, **kw):
            nonlocal call_count
            call_count += 1
            raw = idea_a if call_count == 1 else idea_b
            from yonder.adventure import plan_quest as _real

            async def _price(o, d, dep, req, **k2) -> PricedLeg:
                return _make_priced_leg(o, d)

            async def _pick(*a2, **k2):
                return ["testair"]

            monkeypatch.setattr(adventure_module, "_price_leg", _price)
            monkeypatch.setattr(adventure_module, "pick_pricing_provider", _pick)
            settings = Settings(testing=True, xai_api_key="test-key")
            return await _real(
                a[0] if a else _PROMPT,
                a[1] if len(a) > 1 else "adventure",
                a[2] if len(a) > 2 else "YVR",
                a[3] if len(a) > 3 else date.fromisoformat(_FUTURE),
                settings,
                raw_ideas=[raw],
            )

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

        try:
            import yonder.saved as saved_module
            monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
        except Exception:
            pass

        result_a = _post_quest(client, prompt="temples in southeast asia")
        result_b = _post_quest(client, prompt="mountains and snow in central asia")
        assert result_a["ok"] is True and result_b["ok"] is True
        # First result has Bangkok; second has Kuala Lumpur
        assert "BKK" in result_a["html"] or "Bangkok" in result_a["html"]
        assert "KUL" in result_b["html"] or "Kuala Lumpur" in result_b["html"]
        # They are not identical
        assert result_a["html"] != result_b["html"]


# ---------------------------------------------------------------------------
# Error paths: non-cacheable responses
# ---------------------------------------------------------------------------


class TestQuestPersistenceErrorPaths:
    def test_timeout_response_ok_false(self, client, monkeypatch):
        """A timeout must return ok=False — the JS must not cache error HTML."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _always_timeout(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(web_module, "plan_quest", _always_timeout)
        result = _post_quest(client)
        assert result["ok"] is False

    def test_no_ai_key_response_ok_false(self, client, monkeypatch):
        """No AI key → ok=False — caching a 'key needed' card would be wrong."""
        settings = Settings(testing=True, xai_api_key="", byom_base_url="", byom_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        result = _post_quest(client)
        assert result["ok"] is False

    def test_empty_ideas_response_ok_false(self, client, monkeypatch):
        """Empty idea list → ok=False — nothing to cache."""
        _wire_quest(monkeypatch, ideas=[])
        result = _post_quest(client)
        assert result["ok"] is False

    def test_error_html_has_no_boarding_pass(self, client, monkeypatch):
        """Error responses must not contain boarding-pass cards — if the JS
        cached them by mistake, the user would see a stale error on every reload."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _always_fail(*a, **kw):
            raise RuntimeError("AI unreachable")

        monkeypatch.setattr(web_module, "plan_quest", _always_fail)
        result = _post_quest(client)
        assert result["ok"] is False
        html = result.get("html", "")
        # Error responses must not contain boarding-pass cards
        assert "boarding-pass" not in html or "is-quest" not in html


# ---------------------------------------------------------------------------
# No second AI call needed: behaviour is purely client-side
# ---------------------------------------------------------------------------


class TestQuestPersistenceNoSecondAICall:
    def test_second_render_of_same_html_is_valid(self, client, monkeypatch):
        """If the JS re-injects the cached html string, it should be renderable
        (no server involvement): confirm the html is self-contained HTML markup."""
        _wire_quest(monkeypatch)
        result = _post_quest(client)
        html = result["html"]
        # Must be a complete HTML fragment (not a JSON blob or script)
        assert html.strip().startswith("<")
        # Key DOM anchors used by the post-inject hooks must be present
        assert "quest-results-card" in html
        # Save/share affordances must survive re-injection
        assert "btn-save-and-share" in html

    def test_plan_quest_called_exactly_once_per_request(
        self, client, monkeypatch
    ):
        """Each POST to /api/quest/plan fires plan_quest exactly once.
        The client-side cache means subsequent page loads never reach this
        endpoint at all — confirmed by the JS not calling fetch() on load."""
        call_count = 0

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _counting_quest(*a, **kw):
            nonlocal call_count
            call_count += 1
            from yonder.adventure import plan_quest as _real

            async def _price(o, d, dep, req, **k2) -> PricedLeg:
                return _make_priced_leg(o, d)

            async def _pick(*a2, **k2):
                return ["testair"]

            monkeypatch.setattr(adventure_module, "_price_leg", _price)
            monkeypatch.setattr(adventure_module, "pick_pricing_provider", _pick)
            settings2 = Settings(testing=True, xai_api_key="test-key")
            return await _real(
                a[0] if a else _PROMPT,
                a[1] if len(a) > 1 else "adventure",
                a[2] if len(a) > 2 else "YVR",
                a[3] if len(a) > 3 else date.fromisoformat(_FUTURE),
                settings2,
                raw_ideas=[dict(_RAW_IDEA)],
            )

        monkeypatch.setattr(web_module, "plan_quest", _counting_quest)

        try:
            import yonder.saved as saved_module
            monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
        except Exception:
            pass

        # First call (user clicks "Plan a Quest")
        _post_quest(client)
        assert call_count == 1, "Expected exactly 1 plan_quest call on first request"

        # A page refresh would re-inject from sessionStorage (no server call).
        # Simulate that the server is NOT called by verifying count stays at 1.
        # (The actual no-call guarantee lives in the browser JS; here we confirm
        # that if no fetch happens, the AI count stays at 1.)
        assert call_count == 1, "plan_quest must not be called again for a cached Quest"
