"""End-to-end tests for /explore search pipeline toggles.

Covers all four combinations of:
  - multi_city=false  → escape-only path (plan_adventure never called)
  - multi_city=true   → detour path activated (plan_adventure called when
                         decide_shape returned a non-forced escape)
  - return_flight=false → return_date is None in every search query
  - return_flight=true  → return_date is a future date in every search query

All tests run in mock mode — no real fare API key is present and
settings.configured_providers() returns empty, which forces mock=True.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.grok as grok_module
import yonder.intent as intent_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import AdventureRequest, AdventureResult, StopoverIdea
from yonder.config import Settings
from yonder.grok import ParsedTrip
from yonder.intent import IntentDecision
from yonder.types import SearchQuery, UnifiedSearchResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEPART = (date.today() + timedelta(days=30)).isoformat()

# Prompt used for most tests — short, no city names, no directional phrasing.
# decide_shape classifies this as "detour" or "escape" depending on phrasing,
# but we control the decision for toggle tests that need a specific shape.
_GENERIC_PROMPT = "somewhere warm and sunny"

# Prompt for return_flight tests — uses force_mode=escape to guarantee the
# escape code-path runs and search_flights is called.
_ESCAPE_PROMPT = "fly from Vancouver to Tokyo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_provider_settings() -> Settings:
    """Settings that guarantee mock mode and a patchable Grok path.

    We pass a fake XAI key so grok_ready()=True — the _do_escape() gating at
    web.py:1153 uses `not grok_ready() and not mock`; with grok_ready()=True that
    guard never fires, and our parse_natural_language patch runs as expected.

    The real GrokClient methods are patched in _patch_pipeline so no actual
    API call is ever made.
    """
    return Settings(testing=True, xai_api_key="test-key-for-pipeline-tests")


def _make_parsed_trip(
    origin: str = "YVR",
    destination: str = "NRT",
    return_date: date | None = None,
) -> ParsedTrip:
    return ParsedTrip(
        origin=origin,
        destination=destination,
        depart_date=date.today() + timedelta(days=30),
        return_date=return_date,
        currency="USD",
    )


def _make_search_result(query: SearchQuery) -> UnifiedSearchResult:
    return UnifiedSearchResult(query=query, results=[], offers=[])


def _make_adventure_result(origin: str = "YVR") -> AdventureResult:
    req = AdventureRequest(
        origin=origin,
        destination="NRT",
        depart_date=date.today() + timedelta(days=30),
    )
    return AdventureResult(
        request=req,
        ideas=[StopoverIdea(iata="TYO", city="Tokyo", stay_days=3)],
        itineraries=[],
    )


# ---------------------------------------------------------------------------
# Shared patch helper
# ---------------------------------------------------------------------------


def _patch_pipeline(
    monkeypatch,
    *,
    parsed_trip_origin: str = "YVR",
    parsed_trip_destination: str = "NRT",
    parsed_return_date: date | None = None,
) -> dict[str, list]:
    """Patch settings + Grok + search_flights + plan_adventure.

    Returns a captures dict with:
      search_calls — list of SearchQuery objects passed to search_flights
      plan_calls   — list of AdventureRequest objects passed to plan_adventure
    """
    captures: dict[str, list] = {"search_calls": [], "plan_calls": []}

    settings = _no_provider_settings()
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    parsed = _make_parsed_trip(
        parsed_trip_origin,
        parsed_trip_destination,
        return_date=parsed_return_date,
    )

    async def _fake_parse(self, *a: Any, **kw: Any) -> ParsedTrip:
        return parsed

    monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

    # Detour path calls translate_adventure → return a plain getaway request
    adv_req = AdventureRequest(
        origin=parsed_trip_origin,
        destination="YYZ",
        depart_date=date.today() + timedelta(days=30),
        trip_kind="getaway",
    )
    ideas = [StopoverIdea(iata="TYO", city="Tokyo", stay_days=3)]

    async def _fake_translate(self, *a: Any, **kw: Any):
        return adv_req, ideas

    monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fake_translate)

    async def _fake_search(query: SearchQuery, *, settings=None, **kw: Any) -> UnifiedSearchResult:
        captures["search_calls"].append(query)
        return _make_search_result(query)

    monkeypatch.setattr(web_module, "search_flights", _fake_search)

    async def _fake_plan(req: AdventureRequest, idea_list, *, settings=None, **kw: Any) -> AdventureResult:
        captures["plan_calls"].append(req)
        return _make_adventure_result(req.origin)

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)

    return captures


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    """Suppress all last-search disk reads/writes."""
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure no real API key leaks from the environment."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)


# ---------------------------------------------------------------------------
# multi_city toggle tests
# ---------------------------------------------------------------------------


class TestMultiCityToggle:
    """multi_city=false forces escape-only; multi_city=true upgrades escape→mix."""

    def test_multi_city_omitted_uses_escape_only(self, client, monkeypatch):
        """Omitting multi_city (default False) → escape path, plan_adventure not called."""
        captures = _patch_pipeline(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": _GENERIC_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                # multi_city intentionally absent → False
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called on escape path"
        assert not captures["plan_calls"], (
            "plan_adventure must NOT be called when multi_city is omitted (escape-only)"
        )

    def test_multi_city_false_explicit_uses_escape_only(self, client, monkeypatch):
        """multi_city=false explicitly → plan_adventure never called."""
        captures = _patch_pipeline(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": _GENERIC_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                "multi_city": "false",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called"
        assert not captures["plan_calls"], (
            "plan_adventure must NOT be called when multi_city=false"
        )

    def test_multi_city_true_upgrades_escape_to_mix(self, client, monkeypatch):
        """multi_city=true + non-forced escape decision → plan_adventure is called.

        The toggle at web.py:1078 only upgrades when decision.forced is False.
        We patch decide_shape in yonder.intent to return a non-forced escape so
        the toggle can do its work — this is the exact wiring the toggle relies on.
        """
        captures = _patch_pipeline(monkeypatch)

        # Return a non-forced escape so multi_city=true can upgrade it to mix.
        non_forced_escape = IntentDecision(
            shape="escape",
            confidence=0.85,
            rationale="direct/one-way language",
            forced=False,
        )

        with patch.object(intent_module, "decide_shape", return_value=non_forced_escape):
            resp = client.post(
                "/explore",
                data={
                    "prompt": _GENERIC_PROMPT,
                    "origin": "YVR",
                    "depart": _DEPART,
                    "vibe": "adventure",
                    "multi_city": "true",
                    # No force_mode so decide_shape is used (and patched above)
                },
            )

        assert resp.status_code == 200
        assert captures["plan_calls"], (
            "plan_adventure must be called when multi_city=true upgrades escape→mix"
        )

    def test_multi_city_true_does_not_suppress_escape_path(self, client, monkeypatch):
        """multi_city=true → escape branch still runs (search_flights is called too)."""
        captures = _patch_pipeline(monkeypatch)

        non_forced_escape = IntentDecision(
            shape="escape",
            confidence=0.85,
            rationale="direct/one-way language",
            forced=False,
        )

        with patch.object(intent_module, "decide_shape", return_value=non_forced_escape):
            resp = client.post(
                "/explore",
                data={
                    "prompt": _GENERIC_PROMPT,
                    "origin": "YVR",
                    "depart": _DEPART,
                    "vibe": "adventure",
                    "multi_city": "true",
                },
            )

        assert resp.status_code == 200
        assert captures["search_calls"], (
            "search_flights must still run on the escape branch when multi_city=true"
        )
        assert captures["plan_calls"], (
            "plan_adventure must also run on the detour branch when multi_city=true"
        )

    def test_multi_city_false_overrides_detour_decision(self, client, monkeypatch):
        """multi_city=false forces escape even when decide_shape would return detour."""
        captures = _patch_pipeline(monkeypatch)

        # Without the toggle, this would be a pure detour
        detour_decision = IntentDecision(
            shape="detour",
            confidence=0.9,
            rationale="open getaway / somewhere new",
            forced=False,
        )

        with patch.object(intent_module, "decide_shape", return_value=detour_decision):
            resp = client.post(
                "/explore",
                data={
                    "prompt": _GENERIC_PROMPT,
                    "origin": "YVR",
                    "depart": _DEPART,
                    "vibe": "adventure",
                    "multi_city": "false",
                },
            )

        assert resp.status_code == 200
        # multi_city=false forces escape even though decide_shape said detour
        assert not captures["plan_calls"], (
            "plan_adventure must NOT be called when multi_city=false, "
            "regardless of what decide_shape returned"
        )


# ---------------------------------------------------------------------------
# return_flight toggle tests
# ---------------------------------------------------------------------------


class TestReturnFlightToggle:
    """return_flight=false → return_date None; return_flight=true → future date."""

    def test_return_flight_omitted_strips_return_date(self, client, monkeypatch):
        """Omitting return_flight (default False) strips return_date even if Grok set one."""
        # Grok returns a trip WITH a return_date — the toggle must clear it
        captures = _patch_pipeline(
            monkeypatch,
            parsed_return_date=date.today() + timedelta(days=37),
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": _ESCAPE_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                # return_flight intentionally absent → False
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called"
        for query in captures["search_calls"]:
            assert query.return_date is None, (
                f"return_date must be None when return_flight is omitted; got {query.return_date}"
            )

    def test_return_flight_false_explicit_strips_return_date(self, client, monkeypatch):
        """return_flight=false explicitly → return_date stripped even if Grok parsed one."""
        captures = _patch_pipeline(
            monkeypatch,
            parsed_return_date=date.today() + timedelta(days=37),
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": _ESCAPE_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                "return_flight": "false",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called"
        for query in captures["search_calls"]:
            assert query.return_date is None, (
                f"return_date must be None when return_flight=false; got {query.return_date}"
            )

    def test_return_flight_true_adds_default_return_date(self, client, monkeypatch):
        """return_flight=true + Grok returned no return_date → return_date = depart + 7."""
        captures = _patch_pipeline(
            monkeypatch,
            parsed_return_date=None,  # Grok did not parse a return date
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": _ESCAPE_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                "return_flight": "true",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called"
        for query in captures["search_calls"]:
            assert query.return_date is not None, (
                "return_date must be set when return_flight=true and Grok parsed none"
            )
            assert query.return_date > date.today(), (
                f"return_date must be a future date; got {query.return_date}"
            )
            assert query.return_date >= query.depart_date, (
                f"return_date {query.return_date} must not precede depart_date {query.depart_date}"
            )

    def test_return_flight_true_preserves_grok_return_date(self, client, monkeypatch):
        """return_flight=true + Grok already gave a return_date → that date is kept as-is."""
        grok_return = date.today() + timedelta(days=45)
        captures = _patch_pipeline(
            monkeypatch,
            parsed_return_date=grok_return,
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": _ESCAPE_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                "return_flight": "true",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights must be called"
        for query in captures["search_calls"]:
            assert query.return_date == grok_return, (
                f"Expected Grok's return_date {grok_return}, got {query.return_date}"
            )

    def test_return_flight_false_html_has_empty_return_date(self, client, monkeypatch):
        """The rendered HTML should carry an empty return_date value when return_flight=false."""
        _patch_pipeline(
            monkeypatch,
            parsed_return_date=date.today() + timedelta(days=37),
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": _ESCAPE_PROMPT,
                "origin": "YVR",
                "depart": _DEPART,
                "vibe": "adventure",
                "force_mode": "escape",
                # return_flight off
            },
        )
        assert resp.status_code == 200
        # The template passes `form.return_date` into hidden inputs / JS state.
        # When return_date is stripped, the template renders an empty string —
        # check that the stripped date does NOT appear as an ISO date in the page.
        stripped_date = (date.today() + timedelta(days=37)).isoformat()
        assert stripped_date not in resp.text, (
            f"Stripped return_date {stripped_date!r} must not appear in HTML "
            "when return_flight=false"
        )


# ---------------------------------------------------------------------------
# Parametrised: all four toggle combinations must return 200
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "multi_city,return_flight",
    [
        ("false", "false"),
        ("false", "true"),
        ("true",  "false"),
        ("true",  "true"),
    ],
)
def test_toggle_all_four_combinations(client, monkeypatch, multi_city, return_flight):
    """Every combination of multi_city × return_flight must return 200 without crashing.

    Also enforces the return_date contract:
      - return_flight=false → every captured query has return_date=None
      - return_flight=true  → every captured query has a non-None future return_date
    """
    captures = _patch_pipeline(monkeypatch)

    # Use force_mode=escape so the escape branch always runs and search_calls is populated
    resp = client.post(
        "/explore",
        data={
            "prompt": _ESCAPE_PROMPT,
            "origin": "YVR",
            "depart": _DEPART,
            "vibe": "adventure",
            "force_mode": "escape",
            "multi_city": multi_city,
            "return_flight": return_flight,
        },
    )
    assert resp.status_code == 200, (
        f"Expected 200 for multi_city={multi_city} return_flight={return_flight}; "
        f"got {resp.status_code}"
    )

    # Return-date contract: assert on every search query that reached search_flights
    for query in captures["search_calls"]:
        if return_flight == "false":
            assert query.return_date is None, (
                f"return_date must be None when return_flight=false "
                f"(multi_city={multi_city}); got {query.return_date}"
            )
        else:
            assert query.return_date is not None, (
                f"return_date must be set when return_flight=true "
                f"(multi_city={multi_city})"
            )
            assert query.return_date > date.today(), (
                f"return_date must be a future date (multi_city={multi_city}); "
                f"got {query.return_date}"
            )
