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
def _clean_env(pg_schema, monkeypatch):
    # pg_schema patches quest_jobs.get_conn to a throwaway schema BEFORE any
    # clear_all() runs — without it, clear_all() would wipe real users'
    # in-flight quest jobs in the configured (shared/prod) database.
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)
    quest_jobs.clear_all()
    yield
    quest_jobs.clear_all()


def test_job_store_uses_isolated_schema(pg_schema):
    """Regression: quest job ops must hit the patched test schema, not public.

    Creates a job through the module API and asserts the row landed in the
    isolated schema's quest_jobs table (visible via the patched get_conn) and
    NOT in the default public.quest_jobs table.
    """
    job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
    with pg_schema() as conn:
        row = conn.execute(
            "SELECT job_id FROM quest_jobs WHERE job_id = %s", (job_id,)
        ).fetchone()
    assert row is not None and row["job_id"] == job_id
    # The same row must be absent from the real public schema.
    import os
    import psycopg2 as _pg
    raw = _pg.connect(os.environ["DATABASE_URL"])
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT to_regclass('public.quest_jobs')")
            if cur.fetchone()[0] is not None:
                cur.execute(
                    "SELECT 1 FROM public.quest_jobs WHERE job_id = %s", (job_id,)
                )
                assert cur.fetchone() is None, "job leaked into public schema"
    finally:
        raw.close()


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


class TestDeferredQuestLaunch:
    """Quest launches immediately alongside Escape on every path (Task 604).
    Exclusions are best-effort hints known at launch time (unified parse,
    resolved route, prompt text, chip seed) — never deferred until after
    Escape completes. When no hint exists, Quest launches with an empty
    exclusion list (soft exclusion — plan_quest only reorders anyway).
    """

    def _setup_common(self, monkeypatch):
        """Shared monkeypatching: stub parse, search, and capture Quest."""
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

        monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

        async def _fake_search(query, *a, **kw):
            from yonder.types import UnifiedSearchResult
            return UnifiedSearchResult(query=query, offers=[], results=[])

        monkeypatch.setattr(web_module, "search_flights", _fake_search)

        received: dict = {}

        async def _capture_quest(*a, **kw):
            received.update(kw)
            return []

        monkeypatch.setattr(web_module, "plan_quest", _capture_quest)
        return settings, received

    def _wait_for_quest(self, client, received):
        import time
        for _ in range(50):
            if received:
                break
            time.sleep(0.1)
            client.get("/api/quest/status/nope")

    def test_chip_seed_path_uses_chip_seed_hint(self, client, monkeypatch):
        """Chip/fast-seed search skips plan_unified → Quest launches immediately
        with the chip-seed IATA as its best-effort exclusion hint."""
        import yonder.grok as grok_module
        settings, received = self._setup_common(monkeypatch)

        # plan_unified must NOT be called on a chip/fast-seed search
        async def _fail_unified(self, *a, **kw):
            raise AssertionError("plan_unified must not run on chip-seed search")

        monkeypatch.setattr(grok_module.GrokClient, "plan_unified", _fail_unified)

        # chip_source=chip and seed_iatas=SYD triggers _chip_fast_seeds=True
        resp = client.post(
            "/explore",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "depart": _FUTURE,
                "vibe": "adventure",
                "chip_source": "chip",
                "seed_iatas": "SYD",
            },
        )
        assert resp.status_code == 200
        assert "data-quest-job" in resp.text

        self._wait_for_quest(client, received)

        # Quest fires at launch time with the chip-seed IATA as the hint —
        # it does not wait for Escape's parse (soft exclusion only).
        assert received.get("exclude_dests") == ["SYD"], (
            f"chip-seed path: seed hint not used: {received.get('exclude_dests')!r}"
        )

    def test_unified_fallback_path_launches_without_waiting(self, client, monkeypatch):
        """When plan_unified raises and no destination hint is known at launch,
        Quest still fires immediately with an empty exclusion list rather than
        waiting for Escape's fallback parse (soft exclusion)."""
        import yonder.grok as grok_module

        settings, received = self._setup_common(monkeypatch)

        # Force the unified call to fail so Escape falls back to parse_natural_language
        async def _fail_unified(self, *a, **kw):
            raise RuntimeError("unified call failed — testing fallback path")

        monkeypatch.setattr(grok_module.GrokClient, "plan_unified", _fail_unified)

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

        self._wait_for_quest(client, received)

        assert received.get("exclude_dests") == [], (
            f"unified-fallback path: expected empty hint list: {received.get('exclude_dests')!r}"
        )


class TestStageProgressions:
    """Stage field surfaces through the status endpoint throughout the job lifecycle."""

    def test_new_job_has_reading_vibe_stage(self, client):
        """A freshly created pending job starts at reading_vibe."""
        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        body = client.get(f"/api/quest/status/{job_id}").json()
        assert body["status"] == "pending"
        assert body.get("stage") == "reading_vibe"

    def test_stage_advances_to_scouting(self, client):
        """set_stage() pushes the visible stage to scouting_routes."""
        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        quest_jobs.set_stage(job_id, "scouting_routes")
        body = client.get(f"/api/quest/status/{job_id}").json()
        assert body["status"] == "pending"
        assert body.get("stage") == "scouting_routes"

    def test_stage_advances_to_pricing(self, client):
        """set_stage() pushes the visible stage to pricing_flights."""
        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        quest_jobs.set_stage(job_id, "pricing_flights")
        body = client.get(f"/api/quest/status/{job_id}").json()
        assert body["status"] == "pending"
        assert body.get("stage") == "pricing_flights"

    def test_set_stage_noop_on_done_job(self):
        """set_stage() is silently ignored on a finished job."""
        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        quest_jobs.set_done(
            job_id,
            quest_panel={"ask": "test", "result": [], "home_iata": "YVR", "vibe": "adventure"},
        )
        quest_jobs.set_stage(job_id, "scouting_routes")  # must be no-op
        job = quest_jobs.get_job(job_id)
        assert job is not None
        assert job["status"] == "done"
        # Stage stored in the dict before done should not be overwritten
        assert job.get("stage") != "scouting_routes"

    def test_eager_quest_advances_through_stages(self, monkeypatch):
        """_run_eager_quest transitions the job through scouting→pricing stages."""
        settings = _settings()
        stage_calls: list[str] = []

        _orig_set_stage = quest_jobs.set_stage

        def _spy_set_stage(job_id: str, stage: str) -> None:
            stage_calls.append(stage)
            _orig_set_stage(job_id, stage)

        monkeypatch.setattr(quest_jobs, "set_stage", _spy_set_stage)

        pricing_stage_cb_called: list[str] = []

        async def _stub_quest(*a, stage_cb=None, **kw):
            if stage_cb is not None:
                stage_cb("pricing_flights")
                pricing_stage_cb_called.append("pricing_flights")
            return [_fake_idea()]

        monkeypatch.setattr(web_module, "plan_quest", _stub_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        assert "scouting_routes" in stage_calls, f"scouting_routes never set; got: {stage_calls}"
        assert "pricing_flights" in pricing_stage_cb_called, "stage_cb never called with pricing_flights"

    def test_recycled_hit_skips_stage_cb(self, monkeypatch):
        """A recycled quest completes directly without going through stage transitions."""
        monkeypatch.delenv("YONDER_DISABLE_RECYCLE", raising=False)
        settings = Settings(testing=False, xai_api_key="test-key")
        recycled = [_fake_idea("LIS", "MAD")]
        stage_calls: list[str] = []

        import yonder.recycle as recycle_module
        monkeypatch.setattr(
            recycle_module, "find_recycled_quest", lambda **kw: recycled, raising=False
        )

        _orig_set_stage = quest_jobs.set_stage

        def _spy_set_stage(job_id: str, stage: str) -> None:
            stage_calls.append(stage)
            _orig_set_stage(job_id, stage)

        monkeypatch.setattr(quest_jobs, "set_stage", _spy_set_stage)

        async def _fail_quest(*a, **kw):
            raise AssertionError("plan_quest must not run on a recycled hit")

        monkeypatch.setattr(web_module, "plan_quest", _fail_quest)

        job_id = quest_jobs.create_job(home_iata="YVR", vibe="adventure")
        _run(web_module._run_eager_quest(job_id, **_job_kwargs(settings)))

        job = quest_jobs.get_job(job_id)
        assert job["status"] == "done"
        # scouting_routes/pricing_flights must not appear — recycled path is instant
        assert "scouting_routes" not in stage_calls
        assert "pricing_flights" not in stage_calls


class TestConcurrentPricing:
    """plan_quest prices all candidate ideas concurrently, then selects by preference."""

    def test_all_ideas_priced_in_single_gather(self, monkeypatch):
        """With 3 ideas, all 6 legs are priced concurrently (not sequentially)."""
        settings = _settings()
        price_call_pairs: list[tuple[str, str]] = []

        async def _counting_price_leg(origin, dest, depart, req, **kw) -> PricedLeg:
            price_call_pairs.append((origin, dest))
            return PricedLeg(
                from_iata=origin,
                to_iata=dest,
                depart_date=depart,
                offer=FlightOffer(
                    provider="live", price=199.0, currency="USD",
                    fare_missing=False, price_kind="live",
                ),
            )

        async def _fake_pick(*a, **kw):
            return ["live"]

        monkeypatch.setattr(adventure_module, "_price_leg", _counting_price_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        raw = [
            dict(_RAW_HAN_BKK),
            {**_RAW_HAN_BKK, "entry_iata": "SIN", "exit_iata": "KUL",
             "entry_city": "Singapore", "exit_city": "Kuala Lumpur"},
            {**_RAW_HAN_BKK, "entry_iata": "ICN", "exit_iata": "TPE",
             "entry_city": "Seoul", "exit_city": "Taipei"},
        ]

        ideas = _run(
            adventure_module.plan_quest(
                _PROMPT, "adventure", "YVR", _FUTURE_D, settings,
                include_mock=True, raw_ideas=raw,
            )
        )
        assert ideas, "expected a priced idea"
        # All 6 legs priced (concurrent gather, not early-exit sequential)
        assert len(price_call_pairs) == 6, (
            f"expected 6 price calls for concurrent pricing, got {len(price_call_pairs)}: {price_call_pairs}"
        )

    def test_third_idea_preferred_when_all_have_fares(self, monkeypatch):
        """The 3rd idea (index 2) is chosen when all three have live fares."""
        settings = _settings()

        async def _priced_leg(origin, dest, depart, req, **kw) -> PricedLeg:
            return PricedLeg(
                from_iata=origin, to_iata=dest, depart_date=depart,
                offer=FlightOffer(
                    provider="live", price=250.0, currency="USD",
                    fare_missing=False, price_kind="live",
                ),
            )

        async def _fake_pick(*a, **kw):
            return ["live"]

        monkeypatch.setattr(adventure_module, "_price_leg", _priced_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        raw = [
            {**_RAW_HAN_BKK, "entry_iata": "MXP", "exit_iata": "LIS",
             "entry_city": "Milan", "exit_city": "Lisbon"},
            {**_RAW_HAN_BKK, "entry_iata": "PRG", "exit_iata": "VIE",
             "entry_city": "Prague", "exit_city": "Vienna"},
            {**_RAW_HAN_BKK, "entry_iata": "FCO", "exit_iata": "ATH",
             "entry_city": "Rome", "exit_city": "Athens"},
        ]

        ideas = _run(
            adventure_module.plan_quest(
                _PROMPT, "adventure", "YVR", _FUTURE_D, settings,
                include_mock=True, raw_ideas=raw,
            )
        )
        assert ideas, "expected a priced idea"
        assert ideas[0].entry_iata == "FCO", (
            f"expected 3rd idea (FCO), got {ideas[0].entry_iata}"
        )

    def test_falls_back_to_second_when_third_has_missing_fares(self, monkeypatch):
        """When the 3rd idea has missing fares, the 2nd idea is chosen."""
        settings = _settings()

        # FCO (idea3) → fare-missing; PRG (idea2) → live fare
        async def _selective_price_leg(origin, dest, depart, req, **kw) -> PricedLeg:
            fare_missing = dest in ("FCO",) or origin in ("ATH",)
            return PricedLeg(
                from_iata=origin, to_iata=dest, depart_date=depart,
                offer=FlightOffer(
                    provider="live", price=0.0 if fare_missing else 199.0,
                    currency="USD", fare_missing=fare_missing,
                    price_kind="mock" if fare_missing else "live",
                ),
            )

        async def _fake_pick(*a, **kw):
            return ["live"]

        monkeypatch.setattr(adventure_module, "_price_leg", _selective_price_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        raw = [
            {**_RAW_HAN_BKK, "entry_iata": "MXP", "exit_iata": "LIS",
             "entry_city": "Milan", "exit_city": "Lisbon"},
            {**_RAW_HAN_BKK, "entry_iata": "PRG", "exit_iata": "VIE",
             "entry_city": "Prague", "exit_city": "Vienna"},
            {**_RAW_HAN_BKK, "entry_iata": "FCO", "exit_iata": "ATH",
             "entry_city": "Rome", "exit_city": "Athens"},
        ]

        ideas = _run(
            adventure_module.plan_quest(
                _PROMPT, "adventure", "YVR", _FUTURE_D, settings,
                include_mock=True, raw_ideas=raw,
            )
        )
        assert ideas, "expected a priced idea"
        assert ideas[0].entry_iata == "PRG", (
            f"expected fallback to 2nd idea (PRG), got {ideas[0].entry_iata}"
        )
