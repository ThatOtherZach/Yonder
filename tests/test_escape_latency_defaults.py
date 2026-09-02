from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

import yonder.engine as engine
import yonder.quota as quota
from yonder.config import Settings
from yonder.types import FlightOffer, ProviderResult, SearchQuery


def test_search_timing_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("SEARCH_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("SEARCH_MAX_SECONDS", raising=False)
    assert Settings(_env_file=None).search_timing() == (18.0, 24.0)
    assert Settings(
        search_budget_seconds=1,
        search_max_seconds=2,
    ).search_timing() == (8.0, 8.0)
    assert Settings(
        search_budget_seconds=25,
        search_max_seconds=20,
    ).search_timing() == (25.0, 25.0)


def test_progress_copy_defaults_and_cache_buster():
    progress = Path("yonder/static/progress.js").read_text()
    base = Path("yonder/templates/base.html").read_text()
    assert "options.expectedMs = 18000" in progress
    assert "options.skipAfterMs = 24000" in progress
    assert 'skipBtn.textContent = "Show fares now"' in progress
    assert "Quest keeps planning in the background" in progress
    assert "}, 350)" not in progress
    assert 'progress.js?v=4' in base
    settings_page = Path("yonder/templates/settings.html").read_text()
    assert "Try to finish by this (default 18)" in settings_page
    assert "Show fares after (sec)" in settings_page
    assert "Show fares now" in settings_page


@pytest.mark.anyio
async def test_provider_timeout_returns_affiliate_fallback(monkeypatch):
    cancelled = asyncio.Event()

    class StalledProvider:
        name = "stalled"

        async def safe_search(self, query):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    async def _choose(*args, **kwargs):
        return ["stalled"]

    monkeypatch.setattr(engine, "choose_providers", _choose)
    monkeypatch.setattr(
        engine, "build_providers",
        lambda *a, **kw: [StalledProvider()],
    )
    monkeypatch.setattr(quota, "record_last_search_errors", lambda *a, **kw: None)

    query = SearchQuery(
        origin="YVR",
        destination="NRT",
        depart_date=date(2026, 9, 10),
        currency="USD",
    )
    result = await engine.search_flights(
        query,
        settings=Settings(testing=True),
        timeout=0.01,
    )

    assert result.results[0].provider == "stalled"
    assert result.results[0].failure_kind == "error"
    assert result.offers[0].fare_missing is True
    assert result.offers[0].booking_url
    assert cancelled.is_set()


@pytest.mark.anyio
async def test_provider_timeout_keeps_fares_from_completed_provider(monkeypatch):
    stalled_cancelled = asyncio.Event()

    class FastProvider:
        name = "fast"

        async def safe_search(self, query):
            return ProviderResult(
                provider=self.name,
                ok=True,
                offers=[
                    FlightOffer(
                        provider=self.name,
                        price=499,
                        currency="USD",
                    )
                ],
            )

    class StalledProvider:
        name = "stalled"

        async def safe_search(self, query):
            try:
                await asyncio.sleep(60)
            finally:
                stalled_cancelled.set()

    monkeypatch.setattr(
        engine,
        "build_providers",
        lambda *a, **kw: [FastProvider(), StalledProvider()],
    )
    monkeypatch.setattr(quota, "record_last_search_errors", lambda *a, **kw: None)

    query = SearchQuery(
        origin="YVR",
        destination="NRT",
        depart_date=date(2026, 9, 10),
        currency="USD",
    )
    result = await engine.search_flights(
        query,
        settings=Settings(testing=True),
        only=["fast", "stalled"],
        timeout=0.01,
        force_all=True,
    )

    assert result.offers[0].fare_missing is False
    assert result.offers[0].price == 499
    assert any(r.provider == "stalled" and not r.ok for r in result.results)
    assert stalled_cancelled.is_set()