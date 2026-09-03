"""Provider diagnostics are visible only in server testing mode."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import yonder.encyclopedia as encyclopedia_module
import yonder.recycle as recycle_module
import yonder.saved as saved_module
import yonder.web as web_module
from yonder.adventure import AdventureItinerary, AdventureRequest, AdventureResult, PricedLeg
from yonder.adventure import StopoverIdea
from yonder.config import Settings
from yonder.rate_limit import RateLimitResult
from yonder.types import FlightOffer, ProviderResult, SearchQuery, UnifiedSearchResult
from yonder.web import templates


_USAGE = "~9.9k tok · $0.1234 · 2 AI calls"


def _escape_result() -> UnifiedSearchResult:
    query = SearchQuery(
        origin="YVR",
        destination="NRT",
        depart_date=date.today() + timedelta(days=30),
        currency="USD",
    )
    offer = FlightOffer(
        provider="fare-provider",
        price=500.0,
        currency="USD",
        display_price_base="US$500",
        google_flights_url="https://example.com/fare",
    )
    return UnifiedSearchResult(
        query=query,
        results=[
            ProviderResult(
                provider="diagnostic-provider",
                ok=True,
                latency_ms=123,
            ),
            ProviderResult(
                provider="diagnostic-failure",
                ok=False,
                error="internal diagnostic failure",
            ),
        ],
        offers=[offer],
    )


def _render_index(*, testing: bool) -> str:
    result = _escape_result()
    return templates.env.get_template("index.html").render(
        testing=testing,
        escape_result=result,
        escape_panel={"result": result},
        ai_usage_display=_USAGE,
        detour_panel={},
        countries=[],
        providers=[],
        saved_count=0,
        history_count=0,
        budgets={},
        avoid_defaults=[],
        avoid_tiles_defaults=[],
        visited_defaults=[],
        visited_tiles_defaults=[],
        xp_profile={},
        vibes_json="{}",
        vibes_v=1,
        grok_ready=True,
        vibe_theme=None,
        travel_ctx={},
        form={},
        nav="home",
        mode="escape",
        result=None,
        error=None,
        ask="",
        parsed=None,
        analysis=None,
        dest_theme=None,
        place_book=None,
        place_books={},
        trip_meta=None,
        lock_vibe=False,
        saved_shuffle=[],
    )


def _detour_panel() -> dict:
    depart = date.today() + timedelta(days=30)
    offer = FlightOffer(
        provider="fare-provider",
        price=500.0,
        currency="USD",
        display_price_base="US$500",
        google_flights_url="https://example.com/fare",
    )
    itinerary = AdventureItinerary(
        kind="stopover",
        title="Tokyo detour",
        total_price=500.0,
        currency="USD",
        stop_city="Tokyo",
        stop_iata="NRT",
        stay_days=3,
        legs=[
            PricedLeg(
                from_iata="YVR",
                to_iata="NRT",
                depart_date=depart,
                offer=offer,
            )
        ],
    )
    return {
        "result": AdventureResult(
            request=AdventureRequest(
                origin="YVR",
                destination="LHR",
                depart_date=depart,
            ),
            ideas=[],
            itineraries=[itinerary],
        ),
        "form": {},
    }


def _render_detour(*, testing: bool) -> str:
    return templates.env.get_template("_detour_results_partial.html").render(
        testing=testing,
        detour_panel=_detour_panel(),
        ai_usage_display=_USAGE,
        place_books={},
        return_days=7,
    )


def test_escape_diagnostics_hidden_in_live_mode_but_fare_card_remains():
    html = _render_index(testing=False)

    assert "diagnostic-provider" not in html
    assert "diagnostic-failure" not in html
    assert "123ms" not in html
    assert "internal diagnostic failure" not in html
    assert _USAGE not in html
    assert 'id="escape-results-card"' in html
    assert "Escape" in html
    assert "Direct Escape" not in html
    assert "YVR" in html and "NRT" in html
    assert "US$500" in html


def test_escape_diagnostics_present_in_testing_mode():
    html = _render_index(testing=True)

    assert "diagnostic-provider · 123ms" in html
    assert "diagnostic-failure · fail" in html
    assert _USAGE in html
    assert 'id="escape-results-card"' in html
    assert "US$500" in html


def test_detour_ai_usage_follows_testing_mode():
    live_html = _render_detour(testing=False)
    testing_html = _render_detour(testing=True)

    assert _USAGE not in live_html
    assert "Tokyo detour" in live_html
    assert _USAGE in testing_html
    assert "Tokyo detour" in testing_html


def test_detour_endpoint_visibility_comes_from_server_settings(monkeypatch):
    """The real Detour response hides usage in live mode but keeps the card."""
    settings = Settings(testing=False, xai_api_key="test-key", home_iata="YVR")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
    monkeypatch.setattr(web_module, "corridor_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(recycle_module, "find_recycled_result", lambda **kwargs: None)
    monkeypatch.setattr(web_module, "save_last", lambda *args, **kwargs: None)
    monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kwargs: [])
    monkeypatch.setattr(
        encyclopedia_module,
        "briefs_for_stops",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        web_module._rate_limit,
        "check_plan",
        AsyncMock(return_value=RateLimitResult(allowed=True, retry_after=0)),
    )
    monkeypatch.setattr(web_module._rate_limit, "check_daily_budget", lambda **kwargs: True)

    class _FakeGrok:
        accumulated_usage = {
            "prompt_tokens": 2_090,
            "completion_tokens": 7_810,
            "total_tokens": 9_900,
            "model": "grok-4.5",
            "calls": 2,
        }

        def __init__(self, _settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def translate_adventure(
            self, *, prompt, form, default_currency, **kwargs
        ):
            request = AdventureRequest(
                origin=form["origin"],
                destination=form["destination"],
                depart_date=date.fromisoformat(form["depart"]),
                currency=default_currency,
                vibe=form["vibe"],
                prompt=prompt,
                include_direct=False,
            )
            return request, [
                StopoverIdea(
                    iata="NRT",
                    city="Tokyo",
                    country="JP",
                    source="grok",
                )
            ]

    async def _fake_plan(request, ideas, **kwargs):
        return AdventureResult(
            request=request,
            ideas=ideas,
            itineraries=[
                AdventureItinerary(
                    kind="stopover",
                    title="Tokyo detour",
                    total_price=500.0,
                    currency="USD",
                    stop_city="Tokyo",
                    stop_iata="NRT",
                    stay_days=3,
                    legs=[
                        PricedLeg(
                            from_iata=request.origin,
                            to_iata="NRT",
                            depart_date=request.depart_date,
                            offer=FlightOffer(
                                provider="testair",
                                price=500.0,
                                currency="USD",
                                price_kind="live",
                                display_price_base="US$500",
                                google_flights_url="https://example.com/fare",
                            ),
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(web_module, "GrokClient", _FakeGrok)
    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)

    data = {
        "prompt": "food and culture in Asia",
        "origin": "YVR",
        "destination": "LHR",
        "depart": (date.today() + timedelta(days=30)).isoformat(),
        "vibe": "food",
    }

    with TestClient(web_module.app, raise_server_exceptions=True) as client:
        live_response = client.post("/api/detour/plan", data=data)
        assert live_response.status_code == 200
        live_body = live_response.json()
        assert live_body["ok"] is True
        live_html = live_body["html"]
        assert _USAGE not in live_html
        assert "9.9k tok" not in live_html
        assert "$0.1234" not in live_html
        assert "2 AI calls" not in live_html
        assert "Tokyo detour" in live_html

        # No query/form override is supplied; changing the server setting is
        # what enables developer diagnostics on the same HTTP path.
        settings.testing = True
        testing_response = client.post("/api/detour/plan", data=data)
        assert testing_response.status_code == 200
        testing_body = testing_response.json()
        assert testing_body["ok"] is True
        testing_html = testing_body["html"]
        assert _USAGE in testing_html
        assert "Tokyo detour" in testing_html