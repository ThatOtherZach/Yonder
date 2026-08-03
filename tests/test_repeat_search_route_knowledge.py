"""Integration test: repeat vibe searches get cheaper by skipping known-dead
routes.

Drives two consecutive full searches through ``plan_adventure`` with a stubbed
fare provider:

1. First search: the top-ranked candidate's route (YVR→HAN) answers OK with an
   empty offer set — a live "no flights" — so the knowledge layer
   negative-caches it (yonder/knowledge.py) and the next candidate prices.
2. Second search: plan_adventure reorders fresh-failed routes to the back and
   ``_price_leg`` returns an instant no-flight card without touching the live
   API — zero live calls for the dead route, next viable candidate first.

Route knowledge is isolated in a throwaway PostgreSQL schema so the test never
reads or writes the real route_knowledge table.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

import yonder.adventure as adventure
import yonder.knowledge as knowledge
from yonder.adventure import AdventureRequest, StopoverIdea, plan_adventure
from yonder.config import get_settings
from yonder.knowledge import route_status
from yonder.types import FlightOffer, ProviderResult, UnifiedSearchResult

_ROUTE_DDL = """
CREATE TABLE "{schema}".route_knowledge (
    origin_iata TEXT NOT NULL,
    dest_iata TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_verified_at DOUBLE PRECISION,
    last_failed_at DOUBLE PRECISION,
    last_provider TEXT,
    best_recent_price DOUBLE PRECISION,
    currency TEXT,
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (origin_iata, dest_iata)
)
"""


@pytest.fixture()
def isolated_route_knowledge(monkeypatch):
    """Point yonder.knowledge at a throwaway PG schema with route_knowledge."""
    import psycopg2

    from yonder.db import Conn

    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    schema = f"test_rk_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(_ROUTE_DDL.format(schema=schema))

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    monkeypatch.setattr(knowledge, "get_conn", _get_conn)
    yield
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


def _stub_pipeline(monkeypatch, calls: list[tuple[str, str]], dead: tuple[str, str]):
    """Stub the live pieces around plan_adventure with a fake fare provider.

    ``calls`` records every (origin, dest) that reaches the live search API.
    ``dead`` is the one route where the provider answers OK with zero offers.
    """

    async def fake_search(q, **kw):
        route = (q.origin.upper(), q.destination.upper())
        calls.append(route)
        if route == dead:
            # Live provider answered OK with an empty offer set → confirms
            # "no flights on this route" → negative-cached by _price_leg.
            return UnifiedSearchResult(
                query=q,
                results=[ProviderResult(provider="amadeus", ok=True, offers=[])],
                offers=[],
            )
        offer = FlightOffer(provider="amadeus", price=400.0, currency="CAD")
        return UnifiedSearchResult(
            query=q,
            results=[ProviderResult(provider="amadeus", ok=True, offers=[offer])],
            offers=[offer],
        )

    monkeypatch.setattr(adventure, "search_flights", fake_search)

    async def fake_pick(settings, http, include_mock):
        return ["amadeus"]

    monkeypatch.setattr(adventure, "pick_pricing_provider", fake_pick)

    # Keep the rest of the pipeline off the network / real DB.
    monkeypatch.setattr("yonder.fare_estimates.get_estimate", lambda *a, **kw: None)
    monkeypatch.setattr(adventure, "record_leg", lambda *a, **kw: None)

    def _no_stats(*a, **kw):
        raise RuntimeError("no price history in test")

    monkeypatch.setattr(adventure, "route_stats", _no_stats)
    monkeypatch.setattr(adventure, "_recent_history_iatas", lambda: set())

    async def fake_col(*a, **kw):
        return {}

    monkeypatch.setattr("yonder.daily_costs.estimate_batch_for_stops", fake_col)


@pytest.mark.anyio
async def test_repeat_search_skips_dead_route_and_prices_next_candidate_first(
    isolated_route_knowledge, monkeypatch
):
    calls: list[tuple[str, str]] = []
    dead = ("YVR", "HAN")
    _stub_pipeline(monkeypatch, calls, dead)

    req = AdventureRequest(
        origin="YVR",
        destination="NRT",
        depart_date=date.today() + timedelta(days=45),
        vibe="chaos",
        currency="CAD",
        min_stop_days=2,
        max_stop_days=3,
        include_direct=False,
    )
    ideas = [
        StopoverIdea(iata="HAN", city="Hanoi", vibe_tags=["gritty"]),
        StopoverIdea(iata="BKK", city="Bangkok", vibe_tags=["gritty"]),
    ]
    settings = get_settings()

    # ── Search 1: dead route hits the live API once and gets negative-cached ─
    r1 = await plan_adventure(
        req, [i.model_copy() for i in ideas], settings=settings, include_mock=False
    )
    assert dead in calls, "first search must reach the live API for the dead route"
    assert route_status("YVR", "HAN") == "failed"  # negative-cached
    assert route_status("YVR", "BKK") == "verified"  # live success recorded
    # Next viable candidate still produced a fully priced itinerary
    assert any(
        it.stop_iata == "BKK" and it.total_price is not None for it in r1.itineraries
    )

    # ── Search 2: zero live calls for the dead route, viable candidate first ─
    calls.clear()
    r2 = await plan_adventure(
        req, [i.model_copy() for i in ideas], settings=settings, include_mock=False
    )
    assert dead not in calls, (
        "second search must skip the fare API entirely for the fresh-failed route"
    )
    assert calls, "second search should still price the viable candidate live"
    assert calls[0] == ("YVR", "BKK"), (
        "reorder must put the next viable candidate first"
    )
    # The priced result is the viable candidate — search got cheaper: the dead
    # route consumed no provider quota at all this time.
    assert r2.itineraries and r2.itineraries[0].stop_iata == "BKK"
    assert r2.itineraries[0].total_price is not None


@pytest.fixture
def anyio_backend():
    return "asyncio"
