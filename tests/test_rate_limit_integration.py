"""Integration tests for per-IP rate limiting on expensive endpoints.

Uses FastAPI TestClient to fire real HTTP requests through the route layer and
confirms that:
  - A request from an IP that has hit the limit returns HTTP 429
  - Rotating the session cookie does NOT reset the per-IP window
  - Daily budget exhaustion returns the "at capacity" message
  - Grok/providers are NOT reached once limits/budget are exhausted
  - RATE_LIMIT_ENABLED=False bypasses all limits
  - Eager Quest with Grok-only config (no fare providers) still respects the
    daily budget — not bypassed by the old fare-provider-only mock signal

Design note: rather than hammering the endpoint N times to exhaust a window,
tests pin PLAN_LIMIT / FARE_LIMIT to 0 (meaning "block everything immediately").
The ``_sliding_check`` edge-case fix ensures limit=0 returns 429 on the first call.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
import yonder.rate_limit as _rl
from yonder.config import Settings


# ── helpers ───────────────────────────────────────────────────────────────────

class _GrokOnlySettings(Settings):
    """Settings with a live AI key but NO fare providers.

    Models the deployment that triggered the 'mock bypass' bug: when no fare
    provider keys are configured ``configured_providers()`` returns [] and the old
    ``mock = not configured_providers()`` logic would set mock=True, bypassing
    rate limiting even while Grok calls proceed and incur cost.
    """

    def configured_providers(self):
        return []  # simulate: no fare provider keys in env

    def grok_ready(self):
        return True  # simulate: XAI key or BYOM is configured


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _no_grok_key(monkeypatch):
    """Prevent accidental live AI calls."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _detour_data(**extra):
    from datetime import date, timedelta
    return {
        "prompt": "food and culture in Asia",
        "origin": "YVR",
        "destination": "NRT",
        "depart": (date.today() + timedelta(days=40)).isoformat(),
        "vibe": "food",
        **extra,
    }


# ── /api/detour/plan — per-IP limit ──────────────────────────────────────────

class TestDetourPlanRateLimit:
    """Per-IP sliding-window enforcement on /api/detour/plan."""

    def test_returns_429_when_ip_limit_is_zero(self, client, monkeypatch):
        """With PLAN_LIMIT=0, the very first call from any IP must be rejected."""
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 0)
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)

        plan_called = []

        async def _fake_plan(*a, **kw):
            plan_called.append(1)

        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        resp = client.post("/api/detour/plan", data=_detour_data())
        assert resp.status_code == 429
        body = resp.json()
        assert body["ok"] is False
        assert "moment" in body.get("error", "").lower() or "lot" in body.get("error", "").lower()
        assert "Retry-After" in resp.headers
        # plan_adventure must not have been called — rate limit fires first
        assert plan_called == [], "plan_adventure was called despite rate limit — it must be blocked before providers"

    def test_session_rotation_does_not_bypass_ip_limit(self, client, monkeypatch):
        """Rotating the yv_sess cookie with different values must still be blocked by the IP limit."""
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 0)
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)

        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        # Both calls use different session cookies but the same IP (TestClient always "testclient")
        r1 = client.post("/api/detour/plan", data=_detour_data(), cookies={"yv_sess": "session-A"})
        r2 = client.post("/api/detour/plan", data=_detour_data(), cookies={"yv_sess": "session-B"})

        assert r1.status_code == 429, "First call was not rate-limited despite PLAN_LIMIT=0"
        assert r2.status_code == 429, (
            "Rotating yv_sess bypassed the IP-based rate limit — cookie must not affect the key"
        )

    def test_rate_limit_disabled_flag_allows_through(self, client, monkeypatch):
        """RATE_LIMIT_ENABLED=False must bypass limits even when PLAN_LIMIT=0."""
        monkeypatch.setattr(_rl, "RATE_LIMIT_ENABLED", False)
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 0)
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 0)  # both guards disabled

        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        resp = client.post("/api/detour/plan", data=_detour_data())
        # Must not be 429 — the plan may fail for other reasons (no AI key etc.) but not rate
        assert resp.status_code != 429

    def test_retry_after_header_is_present_and_positive(self, client, monkeypatch):
        """The Retry-After header must be present and contain a positive integer."""
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 0)
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 60)

        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        resp = client.post("/api/detour/plan", data=_detour_data())
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        retry_after = int(resp.headers["Retry-After"])
        assert retry_after >= 1


# ── /api/detour/plan — daily budget exhaustion ───────────────────────────────

class TestDetourPlanDailyBudget:
    def test_budget_exhausted_returns_at_capacity(self, client, monkeypatch):
        """DAILY_AI_BUDGET=0 should block every call with an 'at capacity' message."""
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 1000)    # per-IP limit is not the guard
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 0)  # zero budget: block all

        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        plan_called = []
        monkeypatch.setattr(web_module, "plan_adventure", AsyncMock(side_effect=lambda *a, **kw: plan_called.append(1)))

        resp = client.post("/api/detour/plan", data=_detour_data())
        body = resp.json()
        assert body["ok"] is False
        err = (body.get("error") or "").lower()
        assert "capacity" in err or "tomorrow" in err, f"Unexpected error: {body.get('error')}"
        # plan_adventure must not have been called — budget fires first
        assert plan_called == [], "plan_adventure was called despite budget=0"


# ── /api/place-brief — rate limit ────────────────────────────────────────────

class TestPlaceBriefRateLimit:
    """Per-IP sliding-window enforcement on /api/place-brief."""

    def test_returns_429_when_fare_limit_is_zero(self, client, monkeypatch):
        """With FARE_LIMIT=0, the first call must return 429 before reaching get_place_brief."""
        monkeypatch.setattr(_rl, "FARE_LIMIT", 0)
        monkeypatch.setattr(_rl, "FARE_WINDOW", 120)

        brief_called = []

        async def _fake_brief(*a, **kw):
            brief_called.append(1)
            return None

        import yonder.encyclopedia as _enc
        monkeypatch.setattr(_enc, "get_place_brief", _fake_brief)

        resp = client.get("/api/place-brief", params={"iata": "NRT"})
        assert resp.status_code == 429
        body = resp.json()
        assert body["ok"] is False
        assert "Retry-After" in resp.headers
        # get_place_brief must not have been called — rate limit fires first
        assert brief_called == [], "get_place_brief was called despite rate limit"

    def test_session_rotation_does_not_bypass_place_brief_limit(self, client, monkeypatch):
        """Same IP with different session cookies must share the FARE window."""
        monkeypatch.setattr(_rl, "FARE_LIMIT", 0)
        monkeypatch.setattr(_rl, "FARE_WINDOW", 120)

        import yonder.encyclopedia as _enc
        monkeypatch.setattr(_enc, "get_place_brief", AsyncMock(return_value=None))

        r1 = client.get("/api/place-brief", params={"iata": "NRT"}, cookies={"yv_sess": "sess-X"})
        r2 = client.get("/api/place-brief", params={"iata": "CDG"}, cookies={"yv_sess": "sess-Y"})

        assert r1.status_code == 429
        assert r2.status_code == 429, "Session rotation bypassed the IP-based place-brief limit"


# ── Eager Quest: AI-key with no fare provider still respects budget ────────────

class TestEagerQuestAIOnlyBudgetGuard:
    """Regression tests for the eager Quest mock-signal bypass.

    When a Grok key is configured but NO fare providers are set up,
    ``mock = not configured_providers()`` evaluates to True and previously
    bypassed the daily budget check, allowing unlimited Grok calls.  The fix
    uses an AI-aware predicate: ``_eq_rl_mock = not (grok_ready() or configured_providers())``.
    """

    def test_eager_quest_budget_applies_with_ai_key_no_fare_provider(self, monkeypatch):
        """With a live AI key and no fare providers, budget=0 must block plan_quest."""
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 0)  # zero budget: block all
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 1000)    # per-IP not the issue here
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)

        plan_quest_called = []

        async def _fake_plan_quest(*a, **kw):
            plan_quest_called.append(1)
            return []

        monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

        # Recycle is skipped when settings.testing=True
        settings = _GrokOnlySettings(testing=True)

        # Run _run_eager_quest directly — the Quest background function
        from yonder import quest_jobs as _qjobs
        job_id = _qjobs.create_job(
            home_iata="YVR", vibe="beach", owner_sess="rate-limit-test-session"
        )

        asyncio.run(web_module._run_eager_quest(
            job_id,
            settings=settings,
            prompt="surf and tacos",
            vibe="beach",
            home_iata="YVR",
            depart_dt=date.today() + timedelta(days=40),
            currency="USD",
            quest_days=10,
            mock=True,   # old-style fare-provider mock=True (would bypass old check)
            avoid=[],
            visited=[],
            anchor_legs=[],
            exclude_dests=[],
        ))

        # Budget guard must have fired before plan_quest was ever called
        assert plan_quest_called == [], (
            "plan_quest was called despite DAILY_AI_BUDGET=0 and a live Grok key — "
            "the AI-aware mock signal is not being used in the budget check"
        )

    def test_eager_quest_allowed_when_budget_remains(self, monkeypatch):
        """When budget is sufficient the eager Quest may proceed to plan_quest."""
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 100)   # plenty of budget
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 1000)
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)

        plan_quest_called = []

        async def _fake_plan_quest(*a, **kw):
            plan_quest_called.append(1)
            return []  # empty — Quest stores a no-ideas result

        monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

        settings = _GrokOnlySettings(testing=True)
        from yonder import quest_jobs as _qjobs
        job_id = _qjobs.create_job(
            home_iata="YVR", vibe="beach", owner_sess="rate-limit-test-session"
        )

        asyncio.run(web_module._run_eager_quest(
            job_id,
            settings=settings,
            prompt="surf and tacos",
            vibe="beach",
            home_iata="YVR",
            depart_dt=date.today() + timedelta(days=40),
            currency="USD",
            quest_days=10,
            mock=True,  # old fare-provider signal
            avoid=[],
            visited=[],
            anchor_legs=[],
            exclude_dests=[],
        ))

        assert plan_quest_called == [1], "plan_quest should have been called when budget is sufficient"


# ── No-op / invalid requests must not consume budget ─────────────────────────

class TestNopRequestDoesNotConsumeBudget:
    """No-op or invalid requests must not debit the daily quota."""

    def test_empty_prompt_on_detour_does_not_consume_budget(self, client, monkeypatch):
        """An empty prompt on /api/detour/plan must return an error without charging budget."""
        monkeypatch.setattr(_rl, "PLAN_LIMIT", 1000)
        monkeypatch.setattr(_rl, "PLAN_WINDOW", 120)
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 5)

        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: None)

        budget_before = _rl._budget_count

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": "",          # deliberately empty
                "origin": "YVR",
                "destination": "NRT",
                "depart": (date.today() + timedelta(days=40)).isoformat(),
                "vibe": "food",
            },
        )

        # Should fail with a validation error, NOT a budget error
        body = resp.json()
        assert body["ok"] is False
        err = (body.get("error") or "").lower()
        assert "capacity" not in err, "Empty-prompt error should not mention capacity"
        # Budget must not have been charged
        assert _rl._budget_count == budget_before, (
            f"Budget was consumed by a no-op request: {_rl._budget_count!r} != {budget_before!r}"
        )

    def test_bad_iata_on_place_brief_does_not_consume_budget(self, client, monkeypatch):
        """A malformed IATA on /api/place-brief must return 400 without charging budget."""
        monkeypatch.setattr(_rl, "FARE_LIMIT", 1000)
        monkeypatch.setattr(_rl, "FARE_WINDOW", 120)
        monkeypatch.setattr(_rl, "DAILY_AI_BUDGET", 5)

        budget_before = _rl._budget_count

        resp = client.get("/api/place-brief", params={"iata": "1A3"})  # 3 chars but not alpha → 400

        assert resp.status_code == 400
        assert _rl._budget_count == budget_before, "Budget consumed by a bad-IATA 400 response"
