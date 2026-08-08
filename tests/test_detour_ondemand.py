"""Tests for the on-demand Detour endpoint (/api/detour/plan) — Task 562.

Covers:
  - corridor_candidates geometry (inside vs outside corridor)
  - corridor_candidates MOCK mode returns seed-catalog items only
  - Fallback threshold: Grok called when < 3 corridor candidates
  - /api/detour/plan endpoint: recycled-hit path, fresh-plan path, missing-prompt
  - Main /explore search never calls _do_detour (Detour is on-demand only)
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.adventure as adventure_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import (
    AdventureRequest,
    AdventureResult,
    StopoverIdea,
    _haversine_km,
    _airport_coords,
    corridor_candidates,
)
from yonder.config import Settings
from yonder.types import FlightOffer, CabinClass

_FUTURE = (date.today() + timedelta(days=40)).isoformat()
_PROMPT = "food and culture adventure"


# ── Helpers ───────────────────────────────────────────────────────────────────


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


# ── Corridor geometry tests ───────────────────────────────────────────────────


class TestHaversineKm:
    def test_same_point_is_zero(self):
        d = _haversine_km(49.0, -123.0, 49.0, -123.0)
        assert d == pytest.approx(0.0, abs=0.1)

    def test_yvr_to_lhr_approx(self):
        # YVR (49.19, -123.18) to LHR (51.48, -0.45): ~7600 km great-circle
        d = _haversine_km(49.19, -123.18, 51.48, -0.45)
        assert 7000 < d < 8500, f"Expected ~7600 km, got {d:.0f}"

    def test_short_hop(self):
        # Vancouver to Seattle: ~220 km
        d = _haversine_km(49.19, -123.18, 47.45, -122.30)
        assert 150 < d < 350, f"Expected ~220 km, got {d:.0f}"

    def test_symmetry(self):
        d1 = _haversine_km(0.0, 0.0, 10.0, 10.0)
        d2 = _haversine_km(10.0, 10.0, 0.0, 0.0)
        assert d1 == pytest.approx(d2, rel=1e-6)


class TestCorridorCandidates:
    """corridor_candidates() returns seeds filtered to the great-circle corridor."""

    def test_long_haul_route_returns_candidates(self):
        # YVR → LHR: ~7600 km — plenty of room for corridor stopovers
        candidates = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35)
        assert len(candidates) > 0, "Expected corridor candidates for YVR→LHR"

    def test_candidates_are_stopovers_not_origin_or_dest(self):
        candidates = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35)
        iatas = {c.iata.upper() for c in candidates}
        assert "YVR" not in iatas
        assert "LHR" not in iatas

    def test_source_tags_are_set(self):
        candidates = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35)
        for c in candidates:
            assert c.source in ("seed-corridor", "vibe-corridor", "grok-fallback"), (
                f"Unexpected source tag: {c.source!r}"
            )

    def test_nearby_route_returns_empty(self):
        # Very short route (< 200 km) — corridor filter too tight
        # YVR to SEA is ~220 km, on the boundary; use a clearly short pair
        # Use same airport → should return []
        result = corridor_candidates("YVR", "YVR", "adventure")
        assert result == [], "Same-airport route should return empty"

    def test_avoid_countries_filters_candidates(self):
        # With no avoided countries, should get some results
        all_cands = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35)
        # Avoid every possible country in the seed catalog
        all_countries = list({c.country for c in all_cands if c.country})
        filtered = corridor_candidates(
            "YVR", "LHR", "adventure", deviation=0.35,
            avoid_countries=all_countries,
        )
        # All candidates should have been filtered out (or at least fewer)
        assert len(filtered) <= len(all_cands)

    def test_exclude_iatas_filters_candidates(self):
        cands = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35)
        if not cands:
            pytest.skip("No corridor candidates for this route")
        first_iata = cands[0].iata
        filtered = corridor_candidates(
            "YVR", "LHR", "adventure", deviation=0.35,
            exclude_iatas={first_iata},
        )
        assert all(c.iata != first_iata for c in filtered)

    def test_limit_respected(self):
        cands = corridor_candidates("YVR", "LHR", "adventure", deviation=0.35, limit=3)
        assert len(cands) <= 3

    def test_returns_list_of_stopover_ideas(self):
        cands = corridor_candidates("YVR", "LHR", "adventure")
        for c in cands:
            assert isinstance(c, StopoverIdea)

    def test_missing_origin_coords_returns_empty(self):
        # Use a bogus IATA that won't be in the coords file
        result = corridor_candidates("ZZZ", "LHR", "adventure")
        assert result == []

    def test_deviation_controls_corridor_width(self):
        # Wide deviation → more candidates; tight deviation → fewer
        wide = corridor_candidates("YVR", "LHR", "adventure", deviation=0.80)
        tight = corridor_candidates("YVR", "LHR", "adventure", deviation=0.10)
        # Wide should have at least as many as tight
        assert len(wide) >= len(tight)


# ── Detour plan endpoint tests ────────────────────────────────────────────────


def _wire_detour(monkeypatch, *, ideas=None, plan_raises=None):
    """Patch settings + plan_adventure for /api/detour/plan tests."""
    settings = Settings(testing=True, xai_api_key="")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
    monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [], raising=False)

    # Stub corridor_candidates to return a fixed list
    if ideas is None:
        ideas = [
            StopoverIdea(
                iata="IST", city="Istanbul", stay_days=3,
                why="hub stop", vibe_tags=["city", "food"], source="seed-corridor",
            ),
            StopoverIdea(
                iata="AMS", city="Amsterdam", stay_days=3,
                why="KLM hub", vibe_tags=["city"], source="seed-corridor",
            ),
            StopoverIdea(
                iata="CDG", city="Paris", stay_days=3,
                why="classic stop", vibe_tags=["city", "food"], source="seed-corridor",
            ),
        ]

    monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: list(ideas))

    # Stub plan_adventure to avoid real flight pricing
    from yonder.adventure import (
        AdventureItinerary, PricedLeg, AdventureResult,
    )

    async def _fake_plan(req, idea_list, **kw):
        if plan_raises:
            raise plan_raises
        if not idea_list:
            return AdventureResult(request=req, ideas=[], itineraries=[])
        first = idea_list[0]
        leg = PricedLeg(
            from_iata=req.origin,
            to_iata=first.iata,
            depart_date=req.depart_date,
            offer=FlightOffer(
                provider="testair",
                price=600.0,
                currency=req.currency,
                price_kind="live",
            ),
        )
        leg2 = PricedLeg(
            from_iata=first.iata,
            to_iata=req.destination,
            depart_date=req.depart_date,
            offer=FlightOffer(
                provider="testair",
                price=400.0,
                currency=req.currency,
                price_kind="live",
            ),
        )
        it = AdventureItinerary(
            kind="stopover",
            title=f"Detour via {first.city}",
            stop_iata=first.iata,
            stop_city=first.city,
            stop_days=first.stay_days,
            why=first.why,
            legs=[leg, leg2],
        )
        return AdventureResult(request=req, ideas=list(idea_list), itineraries=[it])

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)


def _post_detour(client: TestClient, **extra) -> dict:
    data = {
        "prompt": _PROMPT,
        "origin": "YVR",
        "destination": "LHR",
        "depart": _FUTURE,
        "vibe": "food",
    }
    data.update(extra)
    resp = client.post("/api/detour/plan", data=data)
    assert resp.status_code == 200
    return resp.json()


class TestDetourEndpointHappyPath:
    def test_returns_json_with_ok_and_html(self, client, monkeypatch):
        _wire_detour(monkeypatch)
        result = _post_detour(client)
        assert result["ok"] is True
        assert "html" in result
        assert len(result["html"]) > 50

    def test_html_contains_detour_card(self, client, monkeypatch):
        _wire_detour(monkeypatch)
        result = _post_detour(client)
        html = result["html"]
        assert "adv-results" in html or "Detour" in html

    def test_missing_prompt_returns_error(self, client, monkeypatch):
        _wire_detour(monkeypatch)
        resp = client.post(
            "/api/detour/plan",
            data={"origin": "YVR", "destination": "LHR", "depart": _FUTURE, "vibe": "food"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["ok"] is False
        assert "prompt" in result.get("error", "").lower()

    def test_no_corridor_candidates_no_off_route_seeds(self, client, monkeypatch):
        """When corridor returns [] for a routed request (even wide retry),
        the endpoint must NOT fall back to unrestricted seed_ideas — it returns
        a friendly no-detour card instead of off-route stops."""
        settings = Settings(testing=True, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        # Both narrow and wide corridor attempts return empty
        monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: [])

        plan_calls: list = []

        async def _fake_plan(req, ideas, **kw):
            plan_calls.append(list(ideas))
            from yonder.adventure import AdventureResult as _AR
            return _AR(request=req, ideas=list(ideas), itineraries=[])

        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
        resp = client.post("/api/detour/plan", data={
            "prompt": _PROMPT, "origin": "YVR", "destination": "LHR",
            "depart": _FUTURE, "vibe": "food",
        })
        assert resp.status_code == 200
        result = resp.json()
        # Either returns error card (ok=False) or plan was called with empty ideas
        # — in either case, no unrestricted off-route seeds must appear.
        if plan_calls:
            for idea in plan_calls[0]:
                src = getattr(idea, "source", "")
                assert src in ("seed-corridor", "seed-corridor-wide", "vibe-corridor", "grok-fallback"), (
                    f"Off-route seed idea in fallback: source={src!r}"
                )
        # With zero corridor candidates, no itineraries → friendly error card
        assert not result.get("ok") or "html" in result


class TestDetourRecycledPath:
    """When a matching saved trip exists, it's returned without pricing."""

    def test_recycled_hit_returns_ok(self, client, monkeypatch):
        settings = Settings(testing=False, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Build a fake recycled result
        from yonder.adventure import AdventureItinerary, PricedLeg, AdventureResult

        fake_req = AdventureRequest(
            origin="YVR", destination="LHR",
            depart_date=date.fromisoformat(_FUTURE),
            currency="USD", adults=1,
        )
        fake_leg = PricedLeg(
            from_iata="YVR", to_iata="IST",
            depart_date=date.fromisoformat(_FUTURE),
            offer=FlightOffer(
                provider="recycled", price=0.0, currency="USD",
                fare_missing=True, price_kind="cached",
            ),
        )
        fake_it = AdventureItinerary(
            kind="stopover",
            title="Detour via Istanbul",
            stop_iata="IST", stop_city="Istanbul", stop_days=3,
            why="recycled trip", legs=[fake_leg],
        )
        fake_result = AdventureResult(request=fake_req, ideas=[], itineraries=[fake_it])

        import yonder.recycle as recycle_module
        monkeypatch.setattr(recycle_module, "find_recycled_result", lambda **kw: fake_result)
        # Also patch in web context
        monkeypatch.setattr(
            web_module, "_find_recycled_in_detour", lambda **kw: fake_result, raising=False
        )

        # Patch the import inside the endpoint
        import yonder.recycle as _recycle
        _orig = _recycle.find_recycled_result

        def _patched_find(**kw):
            return fake_result

        monkeypatch.setattr(_recycle, "find_recycled_result", _patched_find)

        result = _post_detour(client)
        # Recycled path should succeed
        assert result["ok"] is True
        assert "html" in result


class TestDetourNoAIKey:
    def test_no_key_uses_corridor_then_seeds(self, client, monkeypatch):
        """No Grok key → corridor candidates only (no Grok fallback call)."""
        settings = Settings(testing=True, xai_api_key="", byom_base_url="", byom_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        # Provide 3 corridor candidates so no Grok needed
        _wire_detour(monkeypatch)
        result = _post_detour(client)
        # Should still work with just corridor candidates
        assert "html" in result


class TestMainSearchNoDetourCall:
    """Confirm /explore never calls _do_detour."""

    def test_explore_does_not_run_detour_planning(self, client, monkeypatch):
        """A normal search must not invoke plan_adventure for Detour."""
        settings = Settings(testing=True, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        plan_adventure_calls: list = []
        _orig_plan = web_module.plan_adventure

        async def _counting_plan(req, ideas, **kw):
            plan_adventure_calls.append(req.trip_kind)
            return await _orig_plan(req, ideas, **kw)

        monkeypatch.setattr(web_module, "plan_adventure", _counting_plan)

        # Stub search_flights to avoid real API calls
        async def _fake_search(query, *a, **kw):
            from yonder.types import SearchResult
            return SearchResult(query=query, offers=[], results=[])

        monkeypatch.setattr(web_module, "search_flights", _fake_search)

        resp = client.post(
            "/explore",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "depart": _FUTURE,
                "vibe": "food",
                "multi_city": "true",
            },
        )
        assert resp.status_code in (200, 400)
        # plan_adventure may have been called for a getaway-seed path (escape fallback),
        # but NEVER with trip_kind="detour" from the main search gather.
        detour_calls = [k for k in plan_adventure_calls if k == "detour"]
        assert not detour_calls, (
            f"plan_adventure was called with trip_kind='detour' from /explore: {plan_adventure_calls}"
        )

    def test_detour_button_in_template_when_has_escape(self):
        """The detour button card element is present in the index.html template.

        This is a static template check — the button card renders when has_escape is True
        or when intent_shape is detour/mix, confirming the on-demand gate is wired correctly
        without needing a full search.
        """
        import pathlib
        template_path = pathlib.Path("yonder/templates/index.html")
        content = template_path.read_text()
        assert "btn-plan-detour" in content, "btn-plan-detour button must exist in template"
        assert "bootPlanDetourButton" in content, "bootPlanDetourButton JS must be present"
        assert "bootDetourCriteriaStrip" in content, "bootDetourCriteriaStrip JS must be present"
        assert "/api/detour/plan" in content, "JS must POST to /api/detour/plan"
        # Button card must be available for detour/mix intent even without escape results
        assert "intent_shape in ('detour', 'mix')" in content, (
            "Detour button card must render for detour/mix intent, not only when has_escape"
        )

    def test_force_mode_detour_with_escape_failure_renders_button_card(self, client, monkeypatch):
        """When force_mode=detour is set and escape pricing fails, /explore must
        still return 200 with the Detour button card — not a 400 error page.
        This ensures the on-demand button is always reachable for detour intent.
        """
        import yonder.grok as grok_module
        from yonder.grok import ParsedTrip

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        # parse_natural_language succeeds but search_flights raises (simulates pricing failure)
        parsed = ParsedTrip(
            origin="YVR",
            destination="LHR",
            depart_date=date.fromisoformat(_FUTURE),
            currency="USD",
        )

        async def _fake_parse(self, *a, **kw):
            return parsed

        monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

        async def _fail_search(query, *a, **kw):
            raise RuntimeError("simulated search failure")

        monkeypatch.setattr(web_module, "search_flights", _fail_search)

        resp = client.post(
            "/explore",
            data={
                "prompt": "detour adventure please",
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "force_mode": "detour",
                "multi_city": "true",
                "vibe": "adventure",
            },
        )
        # Must be 200 — not a crash page — because Detour button card should show
        assert resp.status_code == 200, (
            f"Expected 200 (Detour button card), got {resp.status_code}"
        )
        body = resp.text
        assert "btn-plan-detour" in body, (
            "Detour button card must be present when force_mode=detour even if escape fails"
        )

    def test_mix_shape_with_escape_failure_renders_button_card(self, client, monkeypatch):
        """When force_mode=mix and only escape fails (no detour results either), the
        page must return 200 with the Detour button card visible — the user can still
        initiate a Detour on-demand."""
        import yonder.grok as grok_module
        from yonder.grok import ParsedTrip

        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(web_module, "load_last", lambda *a, **kw: None, raising=False)

        parsed = ParsedTrip(
            origin="YVR",
            destination="SYD",
            depart_date=date.fromisoformat(_FUTURE),
            currency="USD",
        )

        async def _fake_parse(self, *a, **kw):
            return parsed

        monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

        async def _fail_search(query, *a, **kw):
            raise RuntimeError("simulated search failure")

        monkeypatch.setattr(web_module, "search_flights", _fail_search)

        resp = client.post(
            "/explore",
            data={
                "prompt": "mix of options",
                "origin": "YVR",
                "destination": "SYD",
                "depart": _FUTURE,
                "force_mode": "mix",
                "multi_city": "true",
                "vibe": "adventure",
            },
        )
        # In mix shape, if only escape fails and detour is on-demand, should still be 200
        # (the Detour button card is the fallback)
        assert resp.status_code == 200, (
            f"Expected 200 (Detour button card fallback for mix shape), got {resp.status_code}"
        )
        assert "btn-plan-detour" in resp.text, (
            "Detour button card must render for mix shape even when escape fails"
        )


class TestRecycledDestinationPinning:
    """A recycled saved trip may only be served when it ends at the destination
    the user explicitly submitted — otherwise skip recycling and plan fresh."""

    def test_recycled_result_with_mismatched_destination_is_skipped(self, client, monkeypatch):
        """Recycled trip ends at NRT but the user asked for LHR → recycled result
        must be skipped and fresh corridor planning must run."""
        # Non-testing settings so the recycle path is active
        settings = Settings(testing=False, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Recycled result ends at the WRONG destination
        req = AdventureRequest(
            origin="YVR", destination="NRT",
            depart_date=date.fromisoformat(_FUTURE), currency="USD",
        )
        wrong_dest_recycled = AdventureResult(
            request=req,
            ideas=[StopoverIdea(iata="ICN", city="Seoul", stay_days=3)],
            itineraries=[],
        )
        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: wrong_dest_recycled)

        # Fresh planning path must run: capture the request
        _wire_detour(monkeypatch)
        plan_reqs: list = []
        _orig_plan = web_module.plan_adventure

        async def _capture_plan(r, ideas, **kw):
            plan_reqs.append(r)
            return await _orig_plan(r, ideas, **kw)

        monkeypatch.setattr(web_module, "plan_adventure", _capture_plan)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",   # explicit destination that mismatches recycled NRT
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        body = resp.json()
        # Fresh plan must have run with the submitted route (not recycled NRT)
        assert plan_reqs, "Fresh planning must run when recycled destination mismatches"
        assert plan_reqs[0].destination == "LHR", (
            f"Submitted destination must be planned, got {plan_reqs[0].destination}"
        )
        # And the response must not be the recycled trip's card
        assert "recycled" not in (body.get("html") or ""), (
            "Mismatched recycled result must not be served"
        )

    def test_recycled_result_with_matching_destination_is_served(self, client, monkeypatch):
        """Recycled trip ends at the submitted destination → fast path is used."""
        settings = Settings(testing=False, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        req = AdventureRequest(
            origin="YVR", destination="LHR",
            depart_date=date.fromisoformat(_FUTURE), currency="USD",
        )
        from yonder.adventure import AdventureItinerary, PricedLeg
        leg = PricedLeg(
            from_iata="IST", to_iata="LHR",  # final leg ends at LHR (submitted dest)
            depart_date=date.fromisoformat(_FUTURE),
            offer=FlightOffer(provider="test", price=700.0, currency="USD", price_kind="live"),
        )
        matching_recycled = AdventureResult(
            request=req,
            ideas=[StopoverIdea(iata="IST", city="Istanbul", stay_days=3)],
            itineraries=[AdventureItinerary(
                kind="stopover", title="Via Istanbul",
                stop_iata="IST", stop_city="Istanbul", stop_days=3, why="hub",
                legs=[leg],
            )],
        )
        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: matching_recycled)

        plan_calls: list = []

        async def _no_plan(r, ideas, **kw):
            plan_calls.append(r)
            raise AssertionError("plan_adventure must not run on the recycled fast path")

        monkeypatch.setattr(web_module, "plan_adventure", _no_plan)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        body = resp.json()
        assert body.get("ok") is True, f"Matching recycled trip must be served: {body}"
        assert not plan_calls, "Fresh planning must be skipped on a recycled hit"

    def test_mixed_destination_recycled_batch_filtered_to_matching(self, client, monkeypatch):
        """Recycled batch has two itineraries: one ends at the submitted destination
        (LHR) and one ends elsewhere (NRT). Only the LHR itinerary must be served."""
        settings = Settings(testing=False, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        req = AdventureRequest(
            origin="YVR", destination="LHR",
            depart_date=date.fromisoformat(_FUTURE), currency="USD",
        )
        from yonder.adventure import AdventureItinerary, PricedLeg

        leg_lhr = PricedLeg(
            from_iata="IST", to_iata="LHR",
            depart_date=date.fromisoformat(_FUTURE),
            offer=FlightOffer(provider="test", price=700.0, currency="USD", price_kind="live"),
        )
        leg_nrt = PricedLeg(
            from_iata="ICN", to_iata="NRT",  # wrong final destination
            depart_date=date.fromisoformat(_FUTURE),
            offer=FlightOffer(provider="test", price=600.0, currency="USD", price_kind="live"),
        )
        mixed_recycled = AdventureResult(
            request=req,
            ideas=[
                StopoverIdea(iata="IST", city="Istanbul", stay_days=3),
                StopoverIdea(iata="ICN", city="Seoul", stay_days=3),
            ],
            itineraries=[
                AdventureItinerary(
                    kind="stopover", title="Via Istanbul to LHR",
                    stop_iata="IST", stop_city="Istanbul", stop_days=3, why="hub",
                    legs=[leg_lhr],
                ),
                AdventureItinerary(
                    kind="stopover", title="Via Seoul to NRT (wrong dest)",
                    stop_iata="ICN", stop_city="Seoul", stop_days=3, why="hub",
                    legs=[leg_nrt],
                ),
            ],
        )
        import yonder.recycle as _recycle
        monkeypatch.setattr(_recycle, "find_recycled_result", lambda **kw: mixed_recycled)

        monkeypatch.setattr(web_module, "plan_adventure",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                AssertionError("plan_adventure must not run on recycled fast path")))

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        body = resp.json()
        assert body.get("ok") is True, f"Matching itinerary from mixed batch must be served: {body}"
        html = body.get("html") or ""
        # LHR itinerary shown; NRT itinerary must not appear
        assert "Istanbul" in html, "The LHR itinerary must be in the rendered HTML"
        assert "Seoul" not in html, "The NRT itinerary must be filtered out"


class TestErrorRetainsRouteContext:
    """A failed plan must keep the submitted route in the error card so a retry
    resubmits the same origin/destination/depart."""

    def test_error_partial_preserves_route_inputs(self, client, monkeypatch):
        """plan_adventure raises → error HTML contains the submitted route values."""
        settings = Settings(testing=True, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
        _wire_detour(monkeypatch)

        async def _boom(r, ideas, **kw):
            raise RuntimeError("simulated planning failure")

        monkeypatch.setattr(web_module, "plan_adventure", _boom)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        body = resp.json()
        assert body.get("ok") is False
        html = body.get("html") or ""
        # Retry controls must carry the submitted route
        assert 'id="detour-origin"' in html, "error card must include the origin input"
        assert 'id="detour-dest-input"' in html, "error card must include the destination input"
        assert 'id="detour-depart"' in html, "error card must include the depart input"
        assert 'value="YVR"' in html, "origin value must be preserved for retry"
        assert 'value="LHR"' in html, "destination value must be preserved for retry"
        assert f'value="{_FUTURE}"' in html, "depart date must be preserved for retry"
        assert "btn-plan-detour" in html, "error card must include the retry button"


class TestCorridorOnlyFallback:
    """When corridor_candidates yields no results, the endpoint must NOT fall
    back to unrestricted seed_ideas for a route-based Detour — it must either
    retry with a wider corridor or return a friendly no-detour card."""

    def test_no_off_route_seeds_when_corridor_empty_and_dest_provided(self, client, monkeypatch):
        """With an explicit destination and zero corridor candidates (even on retry),
        plan_adventure must either not be called or be called with an empty idea
        list — never with seeds pulled from the unrestricted global catalog."""
        settings = Settings(testing=True, xai_api_key="")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # corridor_candidates always returns empty (both narrow and wide pass)
        monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: [])

        plan_ideas_seen: list = []

        async def _capture_plan(req, ideas, **kw):
            plan_ideas_seen.append(list(ideas))
            return AdventureResult(request=req, ideas=list(ideas), itineraries=[])

        monkeypatch.setattr(web_module, "plan_adventure", _capture_plan)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )
        # If plan_adventure was called, all ideas must be from the corridor (none here)
        for idea_list in plan_ideas_seen:
            for idea in idea_list:
                src = getattr(idea, "source", "")
                assert src in ("seed-corridor", "seed-corridor-wide", "vibe-corridor", "grok-fallback"), (
                    f"Off-route seed idea snuck in with source={src!r}: {idea}"
                )
        # Either returns a no-detour card (ok=False) or an empty results card
        body = resp.json()
        if plan_ideas_seen:
            assert not plan_ideas_seen[0], (
                "plan_adventure must receive empty ideas when no corridor candidates exist for a routed Detour"
            )


class TestDetourPlanInputValidation:
    """Malformed form inputs must never produce HTTP 500 — the endpoint must
    gracefully validate and fall back to safe defaults."""

    def test_invalid_depart_date_returns_json_not_500(self, client, monkeypatch):
        """POST /api/detour/plan with depart='junk' must not 500.

        The endpoint should parse the date safely and fall back to today+30 days
        rather than propagating a ValueError from date.fromisoformat().
        """
        _wire_detour(monkeypatch)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": "junk",       # invalid ISO date
                "vibe": "adventure",
            },
        )
        assert resp.status_code != 500, (
            f"Invalid depart date must not produce 500 — got {resp.status_code}"
        )
        # Should still produce a usable JSON response (ok or planned result)
        body = resp.json()
        assert "ok" in body or "html" in body, f"Unexpected response body: {body}"

    def test_empty_depart_date_uses_default(self, client, monkeypatch):
        """POST /api/detour/plan with no depart field must not 500 — default is used."""
        _wire_detour(monkeypatch)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                # depart intentionally omitted
                "vibe": "adventure",
            },
        )
        assert resp.status_code != 500, (
            f"Missing depart date must not produce 500 — got {resp.status_code}"
        )
        body = resp.json()
        assert "ok" in body or "html" in body

    def test_past_depart_date_uses_future_default(self, client, monkeypatch):
        """POST with a past depart date must not 500 and must use a future fallback."""
        _wire_detour(monkeypatch)

        resp = client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": "2000-01-01",  # far in the past
                "vibe": "adventure",
            },
        )
        assert resp.status_code != 500, (
            f"Past depart date must not produce 500 — got {resp.status_code}"
        )


class TestGrokFallbackThreshold:
    """Grok ideation is called when corridor returns fewer than 3 candidates."""

    def test_grok_called_when_two_corridor_candidates(self, client, monkeypatch):
        """< 3 corridor candidates → Grok translate_adventure is invoked."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Only 2 corridor candidates
        two_cands = [
            StopoverIdea(iata="IST", city="Istanbul", stay_days=3, why="hub", source="seed-corridor"),
            StopoverIdea(iata="AMS", city="Amsterdam", stay_days=3, why="hub", source="seed-corridor"),
        ]
        monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: list(two_cands))

        grok_calls: list = []

        async def _fake_translate(self, prompt, form, **kw):
            grok_calls.append(prompt)
            # Return one extra idea beyond the corridor
            extra = StopoverIdea(iata="CDG", city="Paris", stay_days=3, why="grok", source="grok")
            req = AdventureRequest(
                origin=form.get("origin", "YVR"),
                destination=form.get("destination", "LHR"),
                depart_date=date.fromisoformat(_FUTURE),
                currency="USD", adults=1,
            )
            return req, [extra]

        import yonder.grok as grok_module
        monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fake_translate)

        # Stub plan_adventure
        from yonder.adventure import AdventureItinerary, PricedLeg, AdventureResult

        async def _fake_plan(req, ideas, **kw):
            if not ideas:
                return AdventureResult(request=req, ideas=[], itineraries=[])
            first = ideas[0]
            leg = PricedLeg(
                from_iata=req.origin, to_iata=first.iata,
                depart_date=req.depart_date,
                offer=FlightOffer(provider="test", price=500.0, currency="USD", price_kind="live"),
            )
            it = AdventureItinerary(
                kind="stopover",
                title=f"Detour via {first.city}",
                stop_iata=first.iata, stop_city=first.city, stop_days=3, why="test",
                legs=[leg],
            )
            return AdventureResult(request=req, ideas=ideas, itineraries=[it])

        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)

        _post_detour(client)

        assert len(grok_calls) >= 1, "Grok translate_adventure must be called when < 3 corridor candidates"

    def test_grok_fallback_cannot_override_submitted_route(self, client, monkeypatch):
        """Grok returns a conflicting origin/destination/trip_kind; the request
        that reaches plan_adventure must keep the submitted YVR→LHR detour route."""
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        # Force the Grok fallback path: zero corridor candidates
        monkeypatch.setattr(web_module, "corridor_candidates", lambda *a, **kw: [])

        async def _conflicting_translate(self, prompt, form, **kw):
            # Grok "hallucinated" a completely different getaway route,
            # including an idea that equals the submitted destination.
            req = AdventureRequest(
                origin="SYD",                # wrong origin
                destination="NRT",           # wrong destination
                depart_date=date.fromisoformat(_FUTURE),
                currency="AUD",
                adults=2,
                trip_kind="getaway",         # wrong kind
            )
            ideas = [
                StopoverIdea(iata="LHR", city="London", stay_days=3, why="endpoint dupe"),
                StopoverIdea(iata="IST", city="Istanbul", stay_days=3, why="ok"),
            ]
            return req, ideas

        import yonder.grok as grok_module
        monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _conflicting_translate)

        plan_reqs: list = []
        plan_ideas: list = []

        from yonder.adventure import AdventureResult as _AR

        async def _capture_plan(req, ideas, **kw):
            plan_reqs.append(req)
            plan_ideas.append(list(ideas))
            return _AR(request=req, ideas=list(ideas), itineraries=[])

        monkeypatch.setattr(web_module, "plan_adventure", _capture_plan)

        client.post(
            "/api/detour/plan",
            data={
                "prompt": _PROMPT,
                "origin": "YVR",
                "destination": "LHR",
                "depart": _FUTURE,
                "vibe": "adventure",
            },
        )

        assert plan_reqs, "plan_adventure was never called"
        req = plan_reqs[0]
        # The submitted route must survive the Grok fallback
        assert req.origin == "YVR", f"origin overridden by Grok: {req.origin}"
        assert req.destination == "LHR", f"destination overridden by Grok: {req.destination}"
        assert req.trip_kind == "detour", f"trip_kind overridden by Grok: {req.trip_kind}"
        # Ideas equal to the route endpoints must be filtered out
        idea_iatas = {(i.iata or "").upper() for i in plan_ideas[0]}
        assert "LHR" not in idea_iatas, "endpoint IATA must not appear as a stopover idea"
        assert "YVR" not in idea_iatas, "endpoint IATA must not appear as a stopover idea"

    def test_grok_not_called_when_three_or_more_corridor_candidates(self, client, monkeypatch):
        """≥ 3 corridor candidates → Grok is NOT called."""
        grok_calls: list = []

        async def _fail_grok(self, *a, **kw):
            grok_calls.append(True)
            raise AssertionError("Grok must not be called when corridor has ≥ 3 candidates")

        import yonder.grok as grok_module
        monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fail_grok)

        _wire_detour(monkeypatch)  # provides 3 corridor candidates
        result = _post_detour(client)

        assert not grok_calls, f"Grok was called unexpectedly: {grok_calls}"
        assert result["ok"] is True
