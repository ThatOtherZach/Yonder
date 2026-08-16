"""Quest-seeded detour candidate pool: harvest, dedup, and route+date matching.

Verifies the detour_candidates module against a throwaway PostgreSQL schema:

1. fare_missing quest legs are never harvested (no fake price snapshots).
2. Duplicate routes within one quest batch collapse to the cheapest entry.
3. store_candidates upserts by route_key — re-runs refresh, never duplicate.
4. find_candidates applies route matching (origin + destination set) and
   date proximity — unrelated destinations/dates are excluded, matching
   historical snapshots appear.
5. Stale-backfill guard: an upsert carrying an older harvested_at never
   overwrites a fresher stored candidate.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import yonder.detour_candidates as dc

_DDL = """
CREATE TABLE "{schema}".detour_candidates (
    route_key TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    stop_iata TEXT NOT NULL,
    stop_city TEXT,
    destination TEXT NOT NULL,
    depart_date TEXT,
    price REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    display_price TEXT,
    booking_url TEXT,
    google_flights_url TEXT,
    fare_note TEXT,
    vibe TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'quest',
    leg_direction TEXT,
    harvested_at DOUBLE PRECISION NOT NULL
)
"""


@pytest.fixture()
def isolated_detour_candidates(monkeypatch):
    """Point yonder.detour_candidates at a throwaway PG schema."""
    import psycopg2

    from yonder.db import Conn

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    schema = f"test_dc_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(_DDL.format(schema=schema))

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        finally:
            raw.close()

    monkeypatch.setattr(dc, "get_conn", _get_conn)
    yield
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


def _mk_quest_idea(*, price=450.0, fare_missing=False, stop="CDG",
                   origin="JFK", dest="FCO", stops_out=1, price_kind="live"):
    seg1 = MagicMock()
    seg1.destination = stop
    seg2 = MagicMock()
    seg2.destination = dest
    offer = MagicMock(
        stops_out=stops_out, segments_out=[seg1, seg2], price=price,
        currency="USD", display_price=None, fare_missing=fare_missing,
        booking_url="https://aviasales.example/deal", google_flights_url=None,
        deep_link=None, fare_note=None, price_kind=price_kind,
    )
    leg = MagicMock(
        from_iata=origin, to_iata=dest, depart_date=None,
        booking_url=None, google_flights_url=None, offer=offer,
    )
    return MagicMock(inbound_leg=leg, outbound_leg=None)


def _cand(route="JFK|CDG|FCO", origin="JFK", stop="CDG", dest="FCO",
          depart="2026-09-10", price=400.0, harvested_at=None):
    return {
        "route_key": route,
        "origin": origin,
        "stop_iata": stop,
        "stop_city": None,
        "destination": dest,
        "depart_date": depart,
        "price": price,
        "currency": "USD",
        "display_price": None,
        "booking_url": None,
        "google_flights_url": None,
        "fare_note": None,
        "vibe": "adventure",
        "source": "quest_eager",
        "leg_direction": "inbound",
        "harvested_at": harvested_at if harvested_at is not None else time.time(),
    }


# ── harvest (no DB needed) ────────────────────────────────────────────────────

def test_mock_skeleton_legs_never_harvested():
    qi = _mk_quest_idea(fare_missing=True, price_kind="mock")
    assert dc.harvest_from_quest([qi], "JFK", "adventure") == []
    qi2 = _mk_quest_idea(price_kind="sandbox")
    assert dc.harvest_from_quest([qi2], "JFK", "adventure") == []


def test_real_fare_missing_leg_becomes_unpriced_check_fares_candidate():
    """API confirmed the flight exists but no fare held — keep it unpriced
    so the card pushes the affiliate link (Check Fares) instead of a price."""
    qi = _mk_quest_idea(fare_missing=True, price_kind="live", price=999.0)
    out = dc.harvest_from_quest([qi], "JFK", "adventure")
    assert len(out) == 1
    assert out[0]["price"] is None
    assert out[0]["display_price"] is None
    assert out[0]["booking_url"] == "https://aviasales.example/deal"


def test_direct_legs_never_harvested():
    qi = _mk_quest_idea(stops_out=0)
    assert dc.harvest_from_quest([qi], "JFK", "adventure") == []


def test_duplicate_routes_collapse_to_cheapest():
    out = dc.harvest_from_quest(
        [_mk_quest_idea(price=500.0), _mk_quest_idea(price=400.0)],
        "JFK", "adventure",
    )
    assert len(out) == 1
    assert out[0]["price"] == 400.0
    assert out[0]["route_key"] == "JFK|CDG|FCO"


# ── storage + matching (throwaway schema) ─────────────────────────────────────

def test_upsert_dedup_refreshes_not_duplicates(isolated_detour_candidates):
    first = _cand(price=500.0, harvested_at=time.time() - 100)
    assert dc.store_candidates([first]) == 1
    fresher = _cand(price=420.0)
    dc.store_candidates([fresher])

    rows = dc.find_candidates("JFK")
    assert len(rows) == 1
    assert rows[0]["price"] == pytest.approx(420.0)


def test_upsert_refreshes_currency_with_price(isolated_detour_candidates):
    old = _cand(price=500.0, harvested_at=time.time() - 100)
    old["currency"] = "USD"
    dc.store_candidates([old])
    newer = _cand(price=640.0)
    newer["currency"] = "CAD"
    dc.store_candidates([newer])

    rows = dc.find_candidates("JFK")
    assert len(rows) == 1
    assert rows[0]["price"] == pytest.approx(640.0)
    assert rows[0]["currency"] == "CAD"


def test_stale_upsert_never_overwrites_fresher_entry(isolated_detour_candidates):
    fresh = _cand(price=420.0, harvested_at=time.time())
    dc.store_candidates([fresh])
    stale = _cand(price=999.0, harvested_at=time.time() - 86400 * 30)
    dc.store_candidates([stale])

    rows = dc.find_candidates("JFK")
    assert len(rows) == 1
    assert rows[0]["price"] == pytest.approx(420.0)


def test_route_matching_excludes_unrelated_destinations(isolated_detour_candidates):
    dc.store_candidates([
        _cand(route="JFK|CDG|FCO", dest="FCO"),
        _cand(route="JFK|AMS|BKK", stop="AMS", dest="BKK"),
    ])
    rows = dc.find_candidates("JFK", destinations=["FCO"])
    assert [r["destination"] for r in rows] == ["FCO"]


def test_date_window_excludes_far_dates(isolated_detour_candidates):
    dc.store_candidates([
        _cand(route="JFK|CDG|FCO", depart="2026-09-10"),
        _cand(route="JFK|ZRH|FCO", stop="ZRH", depart="2027-03-01"),
    ])
    rows = dc.find_candidates(
        "JFK", destinations=["FCO"],
        depart_date_iso="2026-09-15", date_window_days=14,
    )
    assert [r["route_key"] for r in rows] == ["JFK|CDG|FCO"]


def test_max_age_excludes_old_snapshots(isolated_detour_candidates):
    dc.store_candidates([
        _cand(route="JFK|CDG|FCO", harvested_at=time.time() - 200 * 86400),
    ])
    assert dc.find_candidates("JFK", max_age_days=90) == []


def test_matching_historical_snapshot_appears(isolated_detour_candidates):
    dc.store_candidates([
        _cand(route="JFK|CDG|FCO", depart="2026-09-10",
              harvested_at=time.time() - 10 * 86400),
    ])
    rows = dc.find_candidates(
        "JFK", destinations=["FCO"], depart_date_iso="2026-09-05",
    )
    assert len(rows) == 1
    assert rows[0]["age_label"]  # non-empty freshness label, e.g. "1w ago"
