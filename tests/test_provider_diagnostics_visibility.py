"""Provider diagnostics are visible only in server testing mode."""
from __future__ import annotations

from datetime import date, timedelta

from yonder.adventure import AdventureItinerary, AdventureRequest, AdventureResult, PricedLeg
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
    assert "Direct Escape" in html
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