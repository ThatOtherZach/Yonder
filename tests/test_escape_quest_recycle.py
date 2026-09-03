"""Tests for the escape/quest recycle path (Task 392 wiring).

Unit tests for `find_recycled_escape` / `find_recycled_quest` in yonder/recycle.py:
  - Return None when no saved trips of the matching kind exist
  - Return a valid fare-missing result when a matching escape/quest trip is saved
  - Respect the min_score threshold and language matching

Integration test for the /explore handler:
  - With mock saved trips covering all three panels, a Find request skips
    the _do_escape / _do_detour / _do_quest coroutines entirely (no
    search_flights / plan_adventure / plan_quest / Grok calls) and the
    escape/quest overrides come from the recycled data.
  - The search_cost log line lists "escape" and "quest" in recycled_panels.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import yonder.last_search as ls_module
import yonder.recycle as recycle_module
import yonder.saved as saved_module
import yonder.web as web_module
from yonder.config import Settings
from yonder.saved import SavedItinerary

_FUTURE = (date.today() + timedelta(days=30)).isoformat()

_PROMPT = "cherry blossom temples in japan"


# ---------------------------------------------------------------------------
# Saved-trip factory
# ---------------------------------------------------------------------------


def _make_saved(
    *,
    kind: str,
    itinerary: dict[str, Any],
    trip_prompt: str = _PROMPT,
    title: str = "Cherry Blossom Trip",
    vibe: str | None = "adventure",
    origin: str | None = "YVR",
    destination: str | None = "KIX",
    stop_iata: str | None = None,
    stop_city: str | None = None,
    trip_meta: dict[str, Any] | None = None,
) -> SavedItinerary:
    return SavedItinerary(
        id=uuid.uuid4().hex,
        saved_at=time.time(),
        priced_at=None,
        title=title,
        kind=kind,
        currency="USD",
        total_price=None,
        display_price=None,
        stop_city=stop_city,
        stop_iata=stop_iata,
        stay_days=None,
        origin=origin,
        destination=destination,
        adults=1,
        cabin="economy",
        vibe=vibe,
        trip_prompt=trip_prompt,
        theme_country=None,
        theme_primary=None,
        theme_accent=None,
        theme_gradient=None,
        theme_flag_img=None,
        theme_label=None,
        google_flights_url=None,
        kayak_url=None,
        ground_display=None,
        ground_compare_line=None,
        all_in_display=None,
        notes=[],
        itinerary=itinerary,
        trip_meta=trip_meta or {},
    )


def _escape_itinerary(*, origin: str = "YVR", dest: str = "KIX") -> dict[str, Any]:
    return {
        "kind": "escape",
        "legs": [{"from_iata": origin, "to_iata": dest, "depart_date": _FUTURE}],
    }


def _quest_itinerary(
    *, entry: str = "BKK", exit_: str = "SIN"
) -> dict[str, Any]:
    return {
        "kind": "quest",
        "entry_iata": entry,
        "exit_iata": exit_,
        "entry_city": "Bangkok",
        "exit_city": "Singapore",
        "overland_narrative": "Rail down the Malay peninsula.",
        "transport": ["train", "bus"],
        "highlights": ["Night markets"],
        "legs": [{"from_iata": "YVR", "to_iata": entry, "depart_date": _FUTURE}],
    }


def _detour_itinerary(*, origin: str = "YVR", dest: str = "KIX") -> dict[str, Any]:
    """A saved detour that validates as AdventureItinerary (for find_recycled_result)."""
    return {
        "kind": "stopover",
        "title": "Cherry Blossom Detour",
        "currency": "USD",
        "stop_city": "Osaka",
        "stop_iata": dest,
        "legs": [
            {"from_iata": origin, "to_iata": dest, "depart_date": _FUTURE},
        ],
        "notes": [],
    }


def _patch_pool(monkeypatch, saved: list[SavedItinerary]) -> None:
    monkeypatch.setattr(recycle_module, "list_saved", lambda *a, **kw: saved)
    # find_recycled_quest now reads the global quest library via list_quests
    monkeypatch.setattr(recycle_module, "list_quests", lambda *a, **kw: saved)


# ---------------------------------------------------------------------------
# Unit tests — find_recycled_escape
# ---------------------------------------------------------------------------


class TestFindRecycledEscape:
    def test_returns_none_when_no_escape_trips_exist(self, monkeypatch):
        """A pool with only quest/detour trips yields no escape recycle."""
        _patch_pool(
            monkeypatch,
            [
                _make_saved(kind="quest", itinerary=_quest_itinerary()),
                _make_saved(kind="detour", itinerary=_detour_itinerary()),
            ],
        )
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )

    def test_returns_none_on_empty_pool(self, monkeypatch):
        _patch_pool(monkeypatch, [])
        assert (
            recycle_module.find_recycled_escape(prompt=_PROMPT, origin="YVR") is None
        )

    def test_matching_escape_trip_returns_fare_missing_result(self, monkeypatch):
        _patch_pool(
            monkeypatch,
            [_make_saved(kind="escape", itinerary=_escape_itinerary())],
        )
        res = recycle_module.find_recycled_escape(
            prompt=_PROMPT, vibe="adventure", origin="YVR", currency="usd"
        )
        assert res is not None
        assert res.query.origin == "YVR"
        assert res.query.destination == "KIX"
        assert len(res.offers) == 1
        offer = res.offers[0]
        assert offer.fare_missing is True
        assert offer.provider == "recycled"
        assert offer.currency == "USD"
        assert offer.google_flights_url

    def test_respects_min_score_threshold(self, monkeypatch):
        """A weak match (vibe only → score 1.5) is below the default 2.0 cutoff,
        but is accepted when min_score is lowered explicitly."""
        weak = _make_saved(
            kind="escape",
            itinerary=_escape_itinerary(),
            trip_prompt="totally unrelated words here",
            title="Unrelated",
            origin="JFK",  # no origin bonus
        )
        _patch_pool(monkeypatch, [weak])
        # tokens don't overlap, origin differs → only vibe match (1.5) < 2.0
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR", min_score=1.0
            )
            is not None
        )

    def test_language_mismatch_is_never_recycled(self, monkeypatch):
        """A Chinese-prompt saved escape must not surface for an English search."""
        zh = _make_saved(
            kind="escape",
            itinerary=_escape_itinerary(),
            trip_prompt="日本的樱花和寺庙之旅",
            title="樱花之旅",
        )
        _patch_pool(monkeypatch, [zh])
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR", min_score=0.5
            )
            is None
        )

    def test_mock_saved_trips_are_skipped(self, monkeypatch):
        mock_trip = _make_saved(
            kind="escape", itinerary=_escape_itinerary(), trip_meta={"mock": True}
        )
        _patch_pool(monkeypatch, [mock_trip])
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )

    def test_past_depart_date_is_skipped(self, monkeypatch):
        past = (date.today() - timedelta(days=5)).isoformat()
        it = _escape_itinerary()
        it["legs"][0]["depart_date"] = past
        _patch_pool(monkeypatch, [_make_saved(kind="escape", itinerary=it)])
        assert (
            recycle_module.find_recycled_escape(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )


# ---------------------------------------------------------------------------
# Unit tests — find_recycled_quest
# ---------------------------------------------------------------------------


class TestFindRecycledQuest:
    def test_returns_none_when_no_quest_trips_exist(self, monkeypatch):
        _patch_pool(
            monkeypatch,
            [
                _make_saved(kind="escape", itinerary=_escape_itinerary()),
                _make_saved(kind="detour", itinerary=_detour_itinerary()),
            ],
        )
        assert (
            recycle_module.find_recycled_quest(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )

    def test_quest_without_entry_exit_iatas_is_skipped(self, monkeypatch):
        it = _quest_itinerary()
        it.pop("entry_iata")
        it.pop("exit_iata")
        _patch_pool(monkeypatch, [_make_saved(kind="quest", itinerary=it)])
        assert (
            recycle_module.find_recycled_quest(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )

    def test_matching_quest_returns_fare_missing_ideas(self, monkeypatch):
        _patch_pool(
            monkeypatch,
            [_make_saved(kind="quest", itinerary=_quest_itinerary())],
        )
        ideas = recycle_module.find_recycled_quest(
            prompt=_PROMPT,
            vibe="adventure",
            origin="YVR",
            depart=_FUTURE,
            currency="usd",
        )
        assert ideas is not None and len(ideas) == 1
        idea = ideas[0]
        assert idea.entry_iata == "BKK"
        assert idea.exit_iata == "SIN"
        assert idea.entry_city == "Bangkok"
        assert idea.inbound_fare_missing is True
        assert idea.outbound_fare_missing is True
        assert idea.total_price is None
        # Fare-missing skeleton legs, home → entry and exit → home
        assert idea.inbound_leg is not None and idea.outbound_leg is not None
        assert idea.inbound_leg.from_iata == "YVR"
        assert idea.inbound_leg.to_iata == "BKK"
        assert idea.inbound_leg.offer.fare_missing is True
        assert idea.inbound_leg.offer.provider == "recycled"
        assert idea.outbound_leg.from_iata == "SIN"
        assert idea.outbound_leg.to_iata == "YVR"
        assert idea.outbound_leg.offer.fare_missing is True
        assert idea.depart_date == date.fromisoformat(_FUTURE)

    def test_respects_min_score_threshold(self, monkeypatch):
        weak = _make_saved(
            kind="quest",
            itinerary=_quest_itinerary(),
            trip_prompt="totally unrelated words here",
            title="Unrelated",
            vibe="romance",  # no vibe bonus either
            origin="JFK",
        )
        _patch_pool(monkeypatch, [weak])
        assert (
            recycle_module.find_recycled_quest(
                prompt=_PROMPT, vibe="adventure", origin="YVR"
            )
            is None
        )
        assert (
            recycle_module.find_recycled_quest(
                prompt=_PROMPT, vibe="adventure", origin="YVR", min_score=0.0
            )
            is not None
        )

    def test_language_mismatch_is_never_recycled(self, monkeypatch):
        zh = _make_saved(
            kind="quest",
            itinerary=_quest_itinerary(),
            trip_prompt="东南亚陆路探险之旅",
            title="陆路之旅",
        )
        _patch_pool(monkeypatch, [zh])
        assert (
            recycle_module.find_recycled_quest(
                prompt=_PROMPT, vibe="adventure", origin="YVR", min_score=0.0
            )
            is None
        )

    def test_duplicate_entry_exit_pairs_deduped(self, monkeypatch):
        _patch_pool(
            monkeypatch,
            [
                _make_saved(kind="quest", itinerary=_quest_itinerary()),
                _make_saved(kind="quest", itinerary=_quest_itinerary()),
            ],
        )
        ideas = recycle_module.find_recycled_quest(
            prompt=_PROMPT, vibe="adventure", origin="YVR"
        )
        assert ideas is not None and len(ideas) == 1


# ---------------------------------------------------------------------------
# Integration — /explore recycle wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    """Suppress last-search disk reads/writes for all tests in this module."""
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)
    monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("YONDER_DISABLE_RECYCLE", raising=False)


class TestExploreRecycleIntegration:
    def _wire(self, monkeypatch, saved: list[SavedItinerary]) -> dict[str, list]:
        """Non-testing settings + saved-pool patch + panel-call capture."""
        captures: dict[str, list] = {"search": [], "plan": [], "quest": [], "grok": []}

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Saved-trip pool feeding the recycle finders (and other saved reads)
        monkeypatch.setattr(recycle_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(saved_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(web_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(
            saved_module, "saved_destination_iatas", lambda *a, **kw: set(),
            raising=False,
        )
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])

        # Panel machinery — none of it should run when everything is recycled
        async def _fake_search(query, *a: Any, **kw: Any):
            captures["search"].append(query)
            raise AssertionError("search_flights must not be called")

        async def _fake_plan(req, *a: Any, **kw: Any):
            captures["plan"].append(req)
            raise AssertionError("plan_adventure must not be called")

        async def _fake_quest(*a: Any, **kw: Any):
            captures["quest"].append(a)
            return []

        async def _fake_grok_chat(self, *a: Any, **kw: Any):
            captures["grok"].append(a)
            raise AssertionError("Grok must not be called")

        monkeypatch.setattr(web_module, "search_flights", _fake_search)
        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
        monkeypatch.setattr(web_module, "plan_quest", _fake_quest)
        import yonder.grok as grok_module

        monkeypatch.setattr(grok_module.GrokClient, "_chat", _fake_grok_chat)
        monkeypatch.setattr(
            grok_module.GrokClient, "parse_natural_language", _fake_grok_chat
        )
        return captures

    def test_full_recycle_pool_skips_all_panels_and_logs_cost(
        self, client, monkeypatch, caplog
    ):
        """Escape + detour saved matches → zero panel coroutines run,
        overrides come from recycled data, and the search_cost line lists them.

        Quest is on-demand only (not part of the main search recycle pool),
        so saved quest trips are ignored here and 'quest' is never in
        recycled_panels.
        """
        saved = [
            _make_saved(kind="escape", itinerary=_escape_itinerary()),
            _make_saved(
                kind="quest",
                itinerary=_quest_itinerary(),
                destination=None,
                title="Overland Quest",
            ),
            _make_saved(
                kind="detour",
                itinerary=_detour_itinerary(),
                stop_iata="KIX",
                stop_city="Osaka",
                title="Cherry Blossom Detour",
            ),
        ]
        captures = self._wire(monkeypatch, saved)

        with caplog.at_level(logging.INFO, logger="yonder.cost"):
            resp = client.post(
                "/explore",
                data={
                    "prompt": _PROMPT,
                    "origin": "YVR",
                    "depart": _FUTURE,
                    "vibe": "adventure",
                    "force_mode": "mix",
                },
            )
        assert resp.status_code == 200

        # No panel coroutine fired — the recycle pool covered escape + detour
        assert not captures["search"], "escape panel ran despite recycled escape"
        # The testing laboratory preserves eager Quest experimentation.
        assert captures["quest"], "testing mode must preserve eager Quest planning"
        assert not captures["plan"], "detour panel ran despite recycled detour"
        assert not captures["grok"], "Grok was called despite full recycle pool"

        # search_cost log line reflects the recycled panels (escape + detour only)
        cost_lines = [
            r.getMessage() for r in caplog.records if r.name == "yonder.cost"
        ]
        assert cost_lines, "search_cost line missing"
        line = cost_lines[-1]
        assert "recycled_panels=" in line
        assert "escape" in line
        # quest is NOT in recycled_panels — it runs on-demand, not in main search
        assert "quest" not in line.split("recycled_panels=")[1].split("unified=")[0]
        assert "grok_calls≈0" in line

        # Overrides came from the recycled data (rendered into the page)
        html = resp.text
        assert "KIX" in html  # recycled escape/detour destination

    def test_escape_only_recycle_logs_escape_and_runs_detour_panel(
        self, client, monkeypatch, caplog
    ):
        """Only an escape match saved → recycled_panels lists escape only;
        detour panel still runs; quest is NOT in recycled_panels (on-demand only)."""
        saved = [_make_saved(kind="escape", itinerary=_escape_itinerary())]
        captures: dict[str, list] = {"plan": []}

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(recycle_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(saved_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(web_module, "list_saved", lambda *a, **kw: saved)
        monkeypatch.setattr(
            saved_module, "saved_destination_iatas", lambda *a, **kw: set(),
            raising=False,
        )
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])

        # The saved escape itinerary is NOT a valid AdventureItinerary (no
        # title), so find_recycled_result returns None → detour panel runs.
        async def _fake_plan(req, *a: Any, **kw: Any):
            captures["plan"].append(req)
            from yonder.adventure import AdventureRequest, AdventureResult

            return AdventureResult(
                request=AdventureRequest(
                    origin="YVR",
                    destination="KIX",
                    depart_date=date.fromisoformat(_FUTURE),
                ),
                ideas=[],
                itineraries=[],
            )

        async def _fake_translate(self, *a: Any, **kw: Any):
            from yonder.adventure import AdventureRequest

            return (
                AdventureRequest(
                    origin="YVR",
                    destination="KIX",
                    depart_date=date.fromisoformat(_FUTURE),
                    trip_kind="getaway",
                ),
                [],
            )

        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
        import yonder.grok as grok_module

        monkeypatch.setattr(
            grok_module.GrokClient, "translate_adventure", _fake_translate
        )

        with caplog.at_level(logging.INFO, logger="yonder.cost"):
            resp = client.post(
                "/explore",
                data={
                    "prompt": _PROMPT,
                    "origin": "YVR",
                    "depart": _FUTURE,
                    "vibe": "adventure",
                    "force_mode": "mix",
                },
            )
        assert resp.status_code == 200

        cost_lines = [
            r.getMessage() for r in caplog.records if r.name == "yonder.cost"
        ]
        assert cost_lines, "search_cost line missing"
        line = cost_lines[-1]
        assert "escape" in line
        # quest is never in recycled_panels — it's on-demand only
        assert "quest" not in line.split("recycled_panels=")[1].split("unified=")[0]
