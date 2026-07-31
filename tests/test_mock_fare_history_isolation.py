"""Verify that mock/demo fares never pollute the price-history table.

Covers:
- history.record_offer skips any offer with price_kind == "mock"
- history.record_offers returns 0 when every offer is mock-tagged
- A simulated mock-forced detour search leaves zero rows in price_samples
- route_stats excludes mock rows even if they were somehow inserted directly
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import yonder.history as history
from yonder.types import FlightOffer, SearchQuery


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite database."""
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "hist_test.db")
    yield


# ---------------------------------------------------------------------------
# 1. record_offer: mock offer is silently skipped
# ---------------------------------------------------------------------------


def test_record_offer_skips_mock_price_kind():
    q = SearchQuery(origin="YVR", destination="NRT", depart_date=date(2026, 9, 1))
    mock_offer = FlightOffer(
        provider="mock",
        price=299.0,
        currency="CAD",
        price_kind="mock",
    )
    written = history.record_offer(q, mock_offer)
    # record_offer returns False when the offer is skipped
    assert written is False, "record_offer must return False for mock offers"
    # count_samples() creates the table as a side-effect, so we can safely query
    assert history.count_samples() == 0, "mock offer must not be written to price_samples"


def test_record_offer_writes_live_price_kind():
    q = SearchQuery(origin="YVR", destination="NRT", depart_date=date(2026, 9, 1))
    live_offer = FlightOffer(
        provider="amadeus",
        price=550.0,
        currency="CAD",
        price_kind="live",
    )
    written = history.record_offer(q, live_offer)
    assert written is True, "record_offer must return True for live offers"
    assert history.count_samples() == 1, "live offer should be written to price_samples"


# ---------------------------------------------------------------------------
# 2. record_offers: returns 0 when all offers are mock-tagged
# ---------------------------------------------------------------------------


def test_record_offers_returns_zero_for_all_mock():
    q = SearchQuery(origin="YVR", destination="CDG", depart_date=date(2026, 10, 1))
    offers = [
        FlightOffer(provider="mock", price=float(200 + i * 50), currency="CAD", price_kind="mock")
        for i in range(4)
    ]
    written = history.record_offers(q, offers)
    assert written == 0, "record_offers must return 0 when every offer is mock"
    assert history.count_samples() == 0


def test_record_offers_counts_only_live_offers():
    q = SearchQuery(origin="YVR", destination="CDG", depart_date=date(2026, 10, 1))
    offers = [
        FlightOffer(provider="mock", price=200.0, currency="CAD", price_kind="mock"),
        FlightOffer(provider="amadeus", price=480.0, currency="CAD", price_kind="live"),
        FlightOffer(provider="mock", price=220.0, currency="CAD", price_kind="mock"),
    ]
    written = history.record_offers(q, offers)
    assert written == 1, "only the live offer should be counted"
    assert history.count_samples() == 1


# ---------------------------------------------------------------------------
# 3. Mock-forced detour search → zero rows in price_samples
#
# Simulate what happens when mock=True is forced (no configured providers):
# the mock provider returns offers tagged price_kind="mock", which are then
# passed through record_offers.  The table must stay empty.
# ---------------------------------------------------------------------------


def test_mock_forced_detour_leaves_zero_history_rows():
    """Simulates a multi-leg detour search under forced-mock mode.

    Each leg is searched independently and its offers passed to record_offers.
    Because every offer carries price_kind="mock", the history table must
    remain empty after all legs have been processed.
    """
    legs = [
        ("YVR", "NRT", date(2026, 11, 1)),
        ("NRT", "SIN", date(2026, 11, 8)),
        ("SIN", "YVR", date(2026, 11, 15)),
    ]

    for origin, dest, depart in legs:
        q = SearchQuery(origin=origin, destination=dest, depart_date=depart)
        # Mimic what MockProvider.search() returns
        mock_offers = [
            FlightOffer(
                provider="mock",
                price=float(300 + i * 40),
                currency="CAD",
                price_kind="mock",
                notes="demo data — not a real fare",
                bookable=False,
            )
            for i in range(3)
        ]
        history.record_offers(q, mock_offers)

    count = history.count_samples()
    assert count == 0, (
        "mock-forced detour search must leave zero rows in price_samples; "
        f"found {count} row(s)"
    )


# ---------------------------------------------------------------------------
# 4. route_stats excludes mock rows even if directly inserted
# ---------------------------------------------------------------------------


def test_route_stats_excludes_mock_rows():
    """route_stats must filter out mock rows via exclude_sandbox=True (default)."""
    q = SearchQuery(origin="YVR", destination="HND", depart_date=date(2026, 9, 1))

    # Bypass the record_offer guard by inserting directly into the DB
    conn = sqlite3.connect(history.DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            depart_date TEXT NOT NULL,
            return_date TEXT,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            source TEXT NOT NULL,
            price_kind TEXT,
            stops INTEGER,
            airlines TEXT,
            duration_minutes INTEGER,
            notes TEXT,
            google_flights_url TEXT,
            deep_link TEXT,
            raw_id TEXT,
            observed_at TEXT NOT NULL,
            model_source TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO price_samples "
        "(origin, destination, depart_date, price, currency, source, price_kind, observed_at) "
        "VALUES (?,?,?,?,?,?,?,datetime('now'))",
        ("YVR", "HND", "2026-09-01", 250.0, "CAD", "mock", "mock"),
    )
    conn.commit()
    conn.close()

    stats = history.route_stats("YVR", "HND", currency="CAD")
    assert stats.n == 0, (
        "route_stats must not count mock rows; "
        f"got n={stats.n}"
    )
