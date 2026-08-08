"""Eager Quest job lifecycle — Task 561: Run Quest alongside Escape.

Covers, under MOCK mode (no fare providers configured):
  - Recycled saved-quest hit → job done without a fresh AI plan
  - Fresh plan → job done, poll endpoint returns quest-card HTML
  - Timeout degrade → job error, poll endpoint returns a friendly retry card
  - Destination exclusion → Escape's destination reaches plan_quest and
    same-destination ideas are deprioritised
  - Poll endpoint states: pending, unknown/expired job
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import yonder.adventure as adventure_module
import yonder.web as web_module
from yonder import quest_jobs
from yonder.adventure import PricedLeg
from yonder.config import Settings
from yonder.types import FlightOffer

_FUTURE_D = date.today() + timedelta(days=40)
_FUTURE = _FUTURE_D.isoformat()
_PROMPT = "overland food adventure through southeast asia"

_RAW_HAN_BKK = {
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
    quest_jobs.clear_all()
    yield
    quest_jobs.clear_all()


def _settings() -> Settings:
    # testing=True keeps the recycle pool (real DB) out of the job path;
    # no fare providers configured → MOCK pricing skeletons.
    return Settings(testing=True, xai_api_key="test-key")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _job_kwargs(settings, **over):
    kw = dict(
        settings=settings,
        prompt=_PROMPT,
        vibe="adventure",
        home_iata="YVR",
        depart_dt=_FUTURE_D,
        currency="USD",
        quest_days=10,
        mock=True,
        avoid=[],
        visited=[],
        anchor_legs=[],
        exclude_dests=[],
    )
    kw.update(over)
    return kw


def _fake_idea(entry="HAN", exit_="BKK"):
    """Minimal QuestIdea-shaped stand-in for panel assertions."""
    from yonder.adventure import QuestIdea

    leg_in = PricedLeg(
        from_iata="YVR",
        to_iata=entry,
        depart_date=_FUTURE_D,
        offer=FlightOffer(
            provider="mock", price=0.0, currency="USD", fare_missing=True, price_kind="mock"
        ),
    )
    leg_out = PricedLeg(
        from_iata=exit_,
        to_iata="YVR",
        depart_date=_FUTURE_D + timedelta(days=10),
        offer=FlightOffer(
            provider="mock", price=0.0, currency="USD", fare_missing=True, price_kind="mock"
        ),
    )
    return QuestIdea(
        entry_iata=entry,
        exit_iata=exit_,
        entry_city=entry,
        exit_city=exit_,
        overland_narrative="test",
        transport=[],
        highlights=[],
        inbound_leg=leg_in,
        outbound_leg=leg_out,
        currency="USD",
        depart_date=_FUTURE_D,
        outbound_date=_FUTURE_D + timedelta(days=10),
        inbound_fare_missing=True,
        outbound_fare_missing=True,
    )


class TestEagerQuestJobLifecycle:
    def test_recycled_hit_skips_fresh_plan(self, monkeypatch):
        """A recycled saved-quest match completes the job with no plan_quest call."""
        monkeypatch.delenv("YONDER_DISABLE_RECYCLE", raising=False)
        settings = Settings(testing=False, xai_api_key="test-key")
        recycled = [_fake_idea("LIS", "MAD")]

        import yonder.recycle as recycle_module
        monkeypatch.setattr(
            recycle_module, "find_recycled_quest", lambda **kw: recycled, raising=False
        )

        called: list = []

        async def _fail_quest(*a, **kw):
            called.append(kw)
            raise AssertionError("plan_quest must not run when recycle hits")

        monkeypatch.setattr(web_module, "plan_quest", _fail_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        assert not called
        job = quest_jobs.get_job(job_id)
        assert job["status"] == "done" and job["ok"] is True
        assert job["quest_panel"]["result"] == recycled

    def test_fresh_plan_done_and_poll_returns_html(self, client, monkeypatch):
        """No recycle → plan_quest runs; the poll endpoint returns quest-card HTML."""
        settings = _settings()
        ideas = [_fake_idea()]

        async def _stub_quest(*a, **kw):
            return ideas

        monkeypatch.setattr(web_module, "plan_quest", _stub_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        job = quest_jobs.get_job(job_id)
        assert job["status"] == "done" and job["ok"] is True

        resp = client.get(f"/api/quest/status/{job_id}")
        body = resp.json()
        assert body["status"] == "done" and body["ok"] is True
        assert "quest-results-card" in body["html"]
        assert "HAN" in body["html"]

    def test_timeout_degrades_to_retry_card(self, client, monkeypatch):
        """A hung Grok call → error state with the friendly retry card."""
        settings = _settings()

        async def _timeout_quest(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(web_module, "plan_quest", _timeout_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        job = quest_jobs.get_job(job_id)
        assert job["status"] == "error"
        assert "too long" in job["error_text"]

        resp = client.get(f"/api/quest/status/{job_id}")
        body = resp.json()
        assert body["status"] == "error" and body["ok"] is False
        assert "btn-plan-quest" in body["html"]  # retry path stays available

    def test_no_ai_key_completes_without_plan(self, monkeypatch):
        """Missing AI key → done-with-note, never a stuck pending job."""
        settings = Settings(testing=True, xai_api_key="")

        async def _fail_quest(*a, **kw):
            raise AssertionError("plan_quest must not run without an AI key")

        monkeypatch.setattr(web_module, "plan_quest", _fail_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        job = quest_jobs.get_job(job_id)
        assert job["status"] == "done" and job["ok"] is False
        assert "AI key" in (job["quest_panel"]["error"] or "")

    def test_exclude_dests_reach_plan_quest(self, monkeypatch):
        """Escape's destination is forwarded to plan_quest as exclude_dests."""
        settings = _settings()
        received: dict = {}

        async def _capture_quest(*a, **kw):
            received.update(kw)
            return []

        monkeypatch.setattr(web_module, "plan_quest", _capture_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(
            web_module._run_eager_quest(
                job_id, **_job_kwargs(settings, exclude_dests=["NRT"])
            )
        )

        assert received.get("exclude_dests") == ["NRT"]

    def test_poll_states_pending_and_unknown(self, client):
        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        body = client.get(f"/api/quest/status/{job_id}").json()
        assert body["status"] == "pending"

        body = client.get("/api/quest/status/nope").json()
        assert body["status"] == "error"
        assert "btn-plan-quest" in body["html"]


class TestDestinationSeparation:
    def test_plan_quest_prefers_differing_ideas(self, monkeypatch):
        """Ideas touching the excluded destination float behind differing ones."""
        settings = _settings()

        dup = {**_RAW_HAN_BKK, "entry_iata": "NRT", "entry_city": "Tokyo"}
        raw = [dup, dict(_RAW_HAN_BKK)]

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            return PricedLeg(
                from_iata=origin,
                to_iata=dest,
                depart_date=depart,
                offer=FlightOffer(
                    provider="mock", price=0.0, currency="USD",
                    fare_missing=True, price_kind="mock",
                ),
            )

        async def _fake_pick(*a, **kw):
            return ["mock"]

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        ideas = _run(
            adventure_module.plan_quest(
                _PROMPT,
                "adventure",
                "YVR",
                _FUTURE_D,
                settings,
                include_mock=True,
                raw_ideas=raw,
                exclude_dests=["NRT"],
            )
        )
        assert ideas, "expected priced ideas"
        # The differing (HAN) idea must come first; NRT-touching idea demoted
        assert ideas[0].entry_iata == "HAN"

    def test_explore_passes_escape_dest_as_exclusion(self, client, monkeypatch):
        """End-to-end: /explore forwards Escape's destination into the eager job."""
        import yonder.grok as grok_module
        from yonder.grok import ParsedTrip

        settings = _settings()
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        _parsed = ParsedTrip(
            origin="YVR", destination="NRT", depart_date=_FUTURE_D, currency="USD"
        )

        async def _fake_parse(self, *a, **kw):
            return _parsed

        async def _fake_plan_unified(self, *a, **kw):
            return {"escape": _parsed, "detour_cities": None, "quest_pairs": []}

        monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)
        monkeypatch.setattr(grok_module.GrokClient, "plan_unified", _fake_plan_unified)

        async def _fake_search(query, *a, **kw):
            from yonder.types import UnifiedSearchResult
            return UnifiedSearchResult(query=query, offers=[], results=[])

        monkeypatch.setattr(web_module, "search_flights", _fake_search)

        received: dict = {}

        async def _capture_quest(*a, **kw):
            received.update(kw)
            return []

        monkeypatch.setattr(web_module, "plan_quest", _capture_quest)

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
        assert "data-quest-job" in resp.text

        # Give the background task a chance to run inside the client's loop
        import time
        for _ in range(50):
            if received:
                break
            time.sleep(0.1)
            # poking any endpoint spins the portal loop
            client.get("/api/quest/status/nope")

        assert received.get("exclude_dests") == ["NRT"], (
            f"escape destination not excluded: {received.get('exclude_dests')!r}"
        )
