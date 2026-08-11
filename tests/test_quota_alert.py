"""Tests for owner-facing quota/auth alerting.

Covers:
- record_last_search_errors / get_last_search_errors round-trip and disk persistence
- base.py exception → failure_kind classification (401/403 → inactive,
  quota-exhausted messages → quota_exhausted, 429 → cooldown)
- engine emits ERROR-level log for quota/auth failures, WARNING for transient ones
"""
from __future__ import annotations

import asyncio
import logging
import types
from unittest.mock import AsyncMock, patch

import pytest

from yonder.types import ProviderResult, SearchQuery


# ── Snapshot persistence ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_snapshot(tmp_path, monkeypatch):
    """Isolate the global snapshot state and temp-dir the JSON file."""
    import yonder.quota as q
    monkeypatch.setattr(q, "_last_provider_errors", {})
    monkeypatch.setattr(q, "_LAST_PROVIDER_ERR_PATH", tmp_path / ".last_provider_errors.json")
    yield
    monkeypatch.setattr(q, "_last_provider_errors", {})


def _make_results(**kwargs):
    """Return a list of ProviderResult with the given provider→failure_kind mapping."""
    return [
        ProviderResult(provider=name, ok=False, failure_kind=fk, error=f"{fk} error")
        for name, fk in kwargs.items()
    ]


def test_record_and_get_snapshot():
    from yonder.quota import get_last_search_errors, record_last_search_errors

    results = _make_results(serpapi_google_flights="quota_exhausted", amadeus="inactive")
    record_last_search_errors(results, origin="YVR", destination="LHR")

    snap = get_last_search_errors()
    assert snap["origin"] == "YVR"
    assert snap["destination"] == "LHR"
    assert len(snap["providers"]) == 2
    providers_by_name = {p["provider"]: p for p in snap["providers"]}
    assert providers_by_name["serpapi_google_flights"]["failure_kind"] == "quota_exhausted"
    assert providers_by_name["amadeus"]["failure_kind"] == "inactive"


def test_snapshot_persists_to_disk_and_reloads(tmp_path, monkeypatch):
    import yonder.quota as q
    monkeypatch.setattr(q, "_LAST_PROVIDER_ERR_PATH", tmp_path / ".errors.json")

    from yonder.quota import get_last_search_errors, record_last_search_errors

    results = _make_results(duffel="cooldown")
    record_last_search_errors(results, origin="JFK", destination="CDG")

    # Simulate cold start: wipe in-memory state, reload from disk
    monkeypatch.setattr(q, "_last_provider_errors", {})
    snap = get_last_search_errors()
    assert snap["origin"] == "JFK"
    assert snap["providers"][0]["provider"] == "duffel"
    assert snap["providers"][0]["failure_kind"] == "cooldown"


def test_get_snapshot_returns_empty_when_nothing_recorded():
    from yonder.quota import get_last_search_errors

    snap = get_last_search_errors()
    assert snap == {}


def test_snapshot_only_records_failed_providers():
    from yonder.quota import get_last_search_errors, record_last_search_errors

    ok_result = ProviderResult(provider="duffel", ok=True, offers=[], failure_kind=None)
    bad_result = ProviderResult(
        provider="amadeus", ok=False, failure_kind="quota_exhausted", error="quota exhausted"
    )
    record_last_search_errors([ok_result, bad_result], origin="SYD", destination="NRT")

    snap = get_last_search_errors()
    assert len(snap["providers"]) == 1
    assert snap["providers"][0]["provider"] == "amadeus"


# ── base.py exception → failure_kind classification ──────────────────────────

class _StubProvider:
    """Minimal FlightProvider subclass that raises a given exception."""
    name = "stub"

    def is_configured(self):
        return True

    def _client(self):
        return None

    async def search(self, query):
        raise self._exc

    async def safe_search(self, query):  # noqa: D102
        from yonder.providers.base import FlightProvider
        return await FlightProvider.safe_search(self, query)


def _run_safe_search(exc_msg: str) -> ProviderResult:
    """Drive safe_search raising RuntimeError(exc_msg) and return the ProviderResult."""
    from yonder.quota import get_registry

    # Ensure budget exists and is in clean state
    reg = get_registry()
    b = reg.ensure("stub", configured=True)
    b.active = True
    b.healthy = True
    b.cooldown_until = None
    b.monthly_remaining = None

    provider = types.SimpleNamespace(
        name="stub",
        is_configured=lambda: True,
    )

    # Use the real FlightProvider.safe_search by importing and calling directly
    async def _go():
        from yonder.providers.base import FlightProvider

        class _Stub(FlightProvider):
            name = "stub"

            def is_configured(self):
                return True

            async def search(self, query):
                raise RuntimeError(exc_msg)

        return await _Stub().safe_search(query=SearchQuery(
            origin="AAA", destination="BBB", depart_date="2026-01-01"
        ))

    return asyncio.run(_go())


@pytest.mark.parametrize("exc_msg,expected_fk", [
    # Auth failures
    ("HTTP 401 Unauthorized", "inactive"),
    ("HTTP 403 Forbidden", "inactive"),
    ("invalid api key supplied", "inactive"),
    ("authentication failed: bad token", "inactive"),
    ("unauthorized access", "inactive"),
    # Quota exhaustion
    ("quota exhausted for this month", "quota_exhausted"),
    ("monthly limit exceeded", "quota_exhausted"),
    ("quota limit depleted", "quota_exhausted"),
    # Rate limiting (transient)
    ("HTTP 429 Too Many Requests", "cooldown"),
    ("rate limit reached, retry later", "cooldown"),
    # Generic errors
    ("connection timed out", "error"),
    ("unexpected response body", "error"),
])
def test_exception_failure_kind_classification(exc_msg, expected_fk, monkeypatch):
    """Thrown provider exceptions must map to the correct failure_kind."""
    # Give the stub provider a clean quota entry
    from yonder.quota import get_registry
    reg = get_registry()
    b = reg.ensure("stub", configured=True)
    b.active = True
    b.healthy = True
    b.cooldown_until = None
    b.monthly_remaining = None
    b.last_error = None

    result = _run_safe_search(exc_msg)
    assert not result.ok
    assert result.failure_kind == expected_fk, (
        f"exc_msg={exc_msg!r} → expected failure_kind={expected_fk!r}, "
        f"got {result.failure_kind!r}"
    )


# ── Engine log-level selection ────────────────────────────────────────────────

def _make_search_query():
    return SearchQuery(origin="YVR", destination="LHR", depart_date="2026-06-01")


def _run_engine_with_results(provider_results: list[ProviderResult]):
    """Run search_flights with stubbed providers returning the given results."""
    from yonder.config import Settings
    from yonder.engine import search_flights

    settings = Settings()

    async def _go():
        with (
            patch("yonder.engine.choose_providers", return_value=["stub"]),
            patch("yonder.engine.build_providers") as mock_build,
        ):
            stub = AsyncMock()
            stub.safe_search = AsyncMock(return_value=provider_results[0])
            # For multiple providers, return them all
            all_stubs = []
            for r in provider_results:
                s = AsyncMock()
                s.safe_search = AsyncMock(return_value=r)
                s.name = r.provider
                all_stubs.append(s)
            mock_build.return_value = all_stubs
            return await search_flights(
                _make_search_query(),
                settings=settings,
                include_mock=False,
                smart_route=False,
            )

    return asyncio.run(_go())


def test_engine_logs_error_for_quota_exhausted(caplog):
    with caplog.at_level(logging.ERROR, logger="yonder.engine"):
        _run_engine_with_results([
            ProviderResult(
                provider="serpapi_google_flights",
                ok=False,
                failure_kind="quota_exhausted",
                error="monthly quota exhausted",
            )
        ])

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("FLIGHT DATA UNAVAILABLE" in m for m in error_msgs), (
        f"Expected ERROR-level FLIGHT DATA UNAVAILABLE log; got: {caplog.records}"
    )


def test_engine_logs_error_for_auth_failure(caplog):
    with caplog.at_level(logging.ERROR, logger="yonder.engine"):
        _run_engine_with_results([
            ProviderResult(
                provider="amadeus",
                ok=False,
                failure_kind="inactive",
                error="HTTP 401 Unauthorized",
            )
        ])

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("FLIGHT DATA UNAVAILABLE" in m for m in error_msgs), (
        f"Expected ERROR-level FLIGHT DATA UNAVAILABLE log for auth failure; got: {caplog.records}"
    )


def test_engine_logs_warning_not_error_for_cooldown(caplog):
    with caplog.at_level(logging.DEBUG, logger="yonder.engine"):
        _run_engine_with_results([
            ProviderResult(
                provider="serpapi_google_flights",
                ok=False,
                failure_kind="cooldown",
                error="HTTP 429 rate limited",
            )
        ])

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("FLIGHT DATA UNAVAILABLE" in m for m in error_msgs), (
        "Transient cooldown should not trigger ERROR-level log"
    )
    assert any("ALL PROVIDERS DOWN" in m for m in warning_msgs), (
        "Transient cooldown should trigger WARNING-level ALL PROVIDERS DOWN log"
    )


def test_engine_records_snapshot_on_quota_failure():
    from yonder.quota import get_last_search_errors

    _run_engine_with_results([
        ProviderResult(
            provider="serpapi_google_flights",
            ok=False,
            failure_kind="quota_exhausted",
            error="monthly quota exhausted",
        )
    ])

    snap = get_last_search_errors()
    assert snap, "Snapshot should be recorded after quota failure"
    assert snap["providers"][0]["failure_kind"] == "quota_exhausted"


def test_engine_logs_error_for_quota_when_other_provider_returns_no_offers(caplog):
    """Quota/auth ERROR must fire even when another provider returns ok=True, no_offers.

    Regression: previously the guard required ALL providers to be ok=False.
    A mixed (no_offers + quota_exhausted) result would silently drop the alert.
    """
    from yonder.types import FlightOffer

    with caplog.at_level(logging.ERROR, logger="yonder.engine"):
        _run_engine_with_results([
            # One provider: healthy but no fares on this route
            ProviderResult(
                provider="duffel",
                ok=True,
                offers=[],
                failure_kind="no_offers",
            ),
            # Another provider: quota exhausted
            ProviderResult(
                provider="serpapi_google_flights",
                ok=False,
                failure_kind="quota_exhausted",
                error="monthly quota exhausted",
            ),
        ])

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("FLIGHT DATA UNAVAILABLE" in m for m in error_msgs), (
        "Mixed no_offers + quota_exhausted must still trigger ERROR-level owner alert; "
        f"got log records: {[r.message for r in caplog.records]}"
    )


def test_engine_records_snapshot_on_mixed_no_offers_and_quota():
    """Snapshot must be recorded for mixed no_offers + quota_exhausted even though
    not every provider failed outright."""
    from yonder.quota import get_last_search_errors

    _run_engine_with_results([
        ProviderResult(provider="duffel", ok=True, offers=[], failure_kind="no_offers"),
        ProviderResult(
            provider="amadeus",
            ok=False,
            failure_kind="inactive",
            error="HTTP 401 Unauthorized",
        ),
    ])

    snap = get_last_search_errors()
    assert snap, "Snapshot must be recorded for mixed no_offers + auth failure"
    assert snap["providers"][0]["provider"] == "amadeus"
    assert snap["providers"][0]["failure_kind"] == "inactive"


def test_engine_logs_error_when_one_provider_has_live_offers_but_another_quota_fails(caplog):
    """Quota ERROR must fire even when another provider returned live fare offers.

    Regression: previously the alert was inside `if not all_offers`, so a search
    that returned at least one offer would silently drop the quota/auth alert.
    """
    from yonder.types import FlightOffer

    live_offer = FlightOffer(
        provider="duffel",
        price=420.0,
        currency="CAD",
        origin="YVR",
        destination="LHR",
        depart_date="2026-06-01",
        airlines=["BA"],
        stops_out=0,
    )
    with caplog.at_level(logging.ERROR, logger="yonder.engine"):
        _run_engine_with_results([
            # One provider: live fare returned
            ProviderResult(
                provider="duffel",
                ok=True,
                offers=[live_offer],
            ),
            # Another provider: quota exhausted
            ProviderResult(
                provider="serpapi_google_flights",
                ok=False,
                failure_kind="quota_exhausted",
                error="monthly quota exhausted",
            ),
        ])

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("FLIGHT DATA UNAVAILABLE" in m for m in error_msgs), (
        "Quota/auth ERROR must fire even when another provider returned live offers; "
        f"got log records: {[r.message for r in caplog.records]}"
    )


def test_engine_records_snapshot_when_one_provider_has_live_offers_but_another_auth_fails():
    """Snapshot must be recorded even when some providers returned live fare offers."""
    from yonder.quota import get_last_search_errors
    from yonder.types import FlightOffer

    live_offer = FlightOffer(
        provider="duffel",
        price=380.0,
        currency="CAD",
        origin="YVR",
        destination="LHR",
        depart_date="2026-06-01",
        airlines=["BA"],
        stops_out=0,
    )
    _run_engine_with_results([
        ProviderResult(provider="duffel", ok=True, offers=[live_offer]),
        ProviderResult(
            provider="amadeus",
            ok=False,
            failure_kind="inactive",
            error="HTTP 403 Forbidden",
        ),
    ])

    snap = get_last_search_errors()
    assert snap, "Snapshot must be recorded even when other providers returned live offers"
    assert snap["providers"][0]["provider"] == "amadeus"
    assert snap["providers"][0]["failure_kind"] == "inactive"


def test_engine_does_not_record_snapshot_when_offers_returned():
    from yonder.quota import get_last_search_errors
    from yonder.types import FlightOffer

    offer = FlightOffer(
        provider="serpapi_google_flights",
        price=450.0,
        currency="CAD",
        origin="YVR",
        destination="LHR",
        depart_date="2026-06-01",
        airlines=["BA"],
        stops_out=0,
    )
    _run_engine_with_results([
        ProviderResult(
            provider="serpapi_google_flights",
            ok=True,
            offers=[offer],
        )
    ])

    snap = get_last_search_errors()
    assert snap == {}, "Snapshot must NOT be written when offers are returned"
