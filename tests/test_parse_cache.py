"""Tests for the parse-cache layer in yonder/grok.py.

Verified behaviours:
1. Two identical parse_natural_language calls make exactly 1 AI call.
2. use_cache=False bypasses the cache (Refresh-for-novelty path).
3. A different model/provider fingerprint does NOT reuse a cached parse.
4. A legacy last-search snapshot containing an old "analysis" field
   hydrates without error (backward compat via hydrate_escape).
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import yonder.grok as grok_module
from yonder.grok import GrokClient, ParsedTrip, _PARSE_CACHE
from yonder.config import Settings
from yonder.last_search import hydrate_escape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date(2026, 7, 30)

_MINIMAL_TRIP_JSON = json.dumps(
    {
        "origin": "YVR",
        "destination": "NRT",
        "depart_date": "2026-09-01",
        "return_date": None,
        "currency": "USD",
        "nonstop_only": False,
        "intent_summary": "Quick Tokyo trip",
        "assumptions": [],
    }
)


def _make_settings(**kwargs) -> Settings:
    """Create a minimal Settings object with xai_api_key set."""
    defaults = {
        "xai_api_key": "test-key",
        "xai_model": "grok-test",
        "default_currency": "USD",
        "home_iata": "YVR",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def clear_parse_cache():
    """Wipe the module-level cache before and after each test."""
    _PARSE_CACHE.clear()
    yield
    _PARSE_CACHE.clear()


# ---------------------------------------------------------------------------
# 1. Identical calls → exactly 1 AI round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_calls_hit_cache():
    """Two identical parse_natural_language calls make exactly one _chat call."""
    settings = _make_settings()
    client = GrokClient(settings)

    with patch.object(client, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as mock_chat:
        trip1 = await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )
        trip2 = await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )

    assert mock_chat.call_count == 1, (
        f"Expected 1 AI call for identical prompts, got {mock_chat.call_count}"
    )
    assert trip1.destination == trip2.destination
    assert trip1.origin == trip2.origin


# ---------------------------------------------------------------------------
# 2. use_cache=False bypasses the cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_use_cache_false_bypasses_cache():
    """use_cache=False must skip both reading and writing the cache."""
    settings = _make_settings()
    client = GrokClient(settings)

    with patch.object(client, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as mock_chat:
        await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=False
        )
        await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=False
        )

    assert mock_chat.call_count == 2, (
        f"Expected 2 AI calls with use_cache=False, got {mock_chat.call_count}"
    )
    # Also verify nothing was written to the cache
    assert len(_PARSE_CACHE) == 0


@pytest.mark.asyncio
async def test_use_cache_false_does_not_populate_cache():
    """A use_cache=False call should not seed the cache for subsequent calls."""
    settings = _make_settings()
    client = GrokClient(settings)

    with patch.object(client, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as mock_chat:
        # First call with cache disabled — should NOT prime the cache
        await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=False
        )
        # Second call with cache enabled — cache is empty so must call AI again
        await client.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )

    assert mock_chat.call_count == 2, (
        "use_cache=False should not prime the cache for subsequent cached calls"
    )


# ---------------------------------------------------------------------------
# 3. Different model/provider fingerprint bypasses the cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_model_does_not_reuse_cache():
    """Switching xai_model must not serve a cached parse from the old model."""
    settings_a = _make_settings(xai_model="grok-4")
    settings_b = _make_settings(xai_model="grok-4.5")

    client_a = GrokClient(settings_a)
    client_b = GrokClient(settings_b)

    with patch.object(client_a, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as chat_a:
        await client_a.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )
        assert chat_a.call_count == 1

    # Model b has a different fingerprint — must not reuse model a's cache entry
    with patch.object(client_b, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as chat_b:
        await client_b.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )
        assert chat_b.call_count == 1, (
            "Different xai_model must not reuse cached parse from another model"
        )


@pytest.mark.asyncio
async def test_different_byom_provider_does_not_reuse_cache():
    """Switching BYOM base URL must not serve a cached parse from the built-in model."""
    settings_builtin = _make_settings(xai_model="grok-4")
    settings_byom = _make_settings(
        xai_model="grok-4",
        byom_base_url="https://openai.example.com/v1",
        byom_api_key="byom-key",
        byom_model="gpt-4o",
    )

    client_builtin = GrokClient(settings_builtin)
    client_byom = GrokClient(settings_byom)

    with patch.object(client_builtin, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON):
        await client_builtin.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )

    with patch.object(client_byom, "_chat", new_callable=AsyncMock, return_value=_MINIMAL_TRIP_JSON) as chat_byom:
        await client_byom.parse_natural_language(
            "Tokyo next month", today=_TODAY, use_cache=True
        )
        assert chat_byom.call_count == 1, (
            "BYOM provider must not reuse cached parse from built-in xAI model"
        )


# ---------------------------------------------------------------------------
# 4. Legacy snapshot with "analysis" field hydrates without error
# ---------------------------------------------------------------------------


def test_hydrate_escape_with_legacy_analysis_field():
    """hydrate_escape must tolerate an old "analysis" key without raising."""
    legacy_snap = {
        "ask": "Tokyo next month",
        "form": {"origin": "YVR", "destination": "NRT", "depart": "2026-09-01"},
        "analysis": {"summary": "great route", "score": 9},  # legacy field
        "result": None,
        "parsed": {
            "origin": "YVR",
            "destination": "NRT",
            "depart_date": "2026-09-01",
            "return_date": None,
            "adults": 1,
            "cabin": "economy",
            "currency": "USD",
            "nonstop_only": False,
            "intent_summary": "Tokyo trip",
            "assumptions": [],
        },
        "dest_theme": None,
        "place_book": None,
    }

    # Must not raise
    out = hydrate_escape(legacy_snap)

    # "analysis" must always be None (legacy field removed in production)
    assert out["analysis"] is None
    # Other fields should hydrate correctly
    assert out["ask"] == "Tokyo next month"
    assert isinstance(out["parsed"], ParsedTrip)
    assert out["parsed"].destination == "NRT"


def test_hydrate_escape_missing_analysis_field():
    """hydrate_escape works when "analysis" key is entirely absent from snapshot."""
    snap = {
        "ask": "Beach vacation",
        "form": {"origin": "YVR"},
        "result": None,
        "parsed": None,
    }
    out = hydrate_escape(snap)
    assert out["analysis"] is None
    assert out["error"] is None
