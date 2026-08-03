"""Fare-cache short-circuit tests for _price_leg.

Covers:
  1. Cache hit → fare-missing leg, zero flight-API calls
  2. bypass_fare_cache=True → live API called even with a valid cache entry
  3. Fare-missing PricedLeg shape: provider="fare_cache", fare_missing=True,
     valid google_flights_url
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import yonder.fare_estimates as fare_estimates
from yonder.adventure import AdventureRequest, _price_leg
from yonder.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_fare_db(pg_schema, monkeypatch):
    """Isolate fare-estimate cache in a throwaway PG schema."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)


def _req() -> AdventureRequest:
    return AdventureRequest(
        origin="YVR",
        destination="BKK",
        depart_date=date(2026, 9, 15),
        adults=1,
        currency="CAD",
        trip_kind="detour",
    )


DEPART = date(2026, 9, 15)


def _seed_cache() -> None:
    fare_estimates.upsert_estimate("YVR", "BKK", 680.0, "CAD", year_month="2026-09")
    fare_estimates.upsert_estimate("YVR", "BKK", 1240.0, "CAD", year_month="2026-09")


async def _run_price_leg(search_mock, *, bypass: bool):
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        with patch("yonder.adventure.search_flights", search_mock):
            return await _price_leg(
                "YVR",
                "BKK",
                DEPART,
                _req(),
                settings=settings,
                include_mock=True,
                only=["mock"],
                http=http,
                bypass_fare_cache=bypass,
            )


@pytest.mark.asyncio
async def test_cache_hit_skips_flight_api():
    _seed_cache()
    search_mock = AsyncMock()
    leg = await _run_price_leg(search_mock, bypass=False)
    assert search_mock.await_count == 0, "cache hit must make zero flight-API calls"
    assert leg.offer is not None
    assert leg.offer.fare_missing is True
    assert leg.error is None


@pytest.mark.asyncio
async def test_bypass_fare_cache_hits_live_api():
    _seed_cache()
    # Confirm the cache genuinely has an entry — the bypass must ignore it.
    assert fare_estimates.get_estimate("YVR", "BKK", "CAD", year_month="2026-09")
    search_mock = AsyncMock(return_value=SimpleNamespace(offers=[], results=[]))
    await _run_price_leg(search_mock, bypass=True)
    assert search_mock.await_count >= 1, (
        "explicit refresh (bypass_fare_cache=True) must call the live flight API"
    )


@pytest.mark.asyncio
async def test_cached_leg_shape():
    _seed_cache()
    leg = await _run_price_leg(AsyncMock(), bypass=False)
    assert leg.offer is not None
    assert leg.offer.fare_missing is True
    assert leg.offer.provider == "fare_cache"
    assert leg.offer.price_kind == "cached"
    assert "680" in (leg.offer.notes or "") and "1,240" in (leg.offer.notes or "")
    assert leg.google_flights_url and leg.google_flights_url.startswith("http")
    assert "YVR" in leg.google_flights_url and "BKK" in leg.google_flights_url
    assert leg.booking_url == leg.google_flights_url


@pytest.mark.asyncio
async def test_empty_cache_falls_through_to_api():
    """No seeded cache → _price_leg proceeds to the live API path."""
    search_mock = AsyncMock(return_value=SimpleNamespace(offers=[], results=[]))
    leg = await _run_price_leg(search_mock, bypass=False)
    assert search_mock.await_count >= 1
    assert leg.offer is None
