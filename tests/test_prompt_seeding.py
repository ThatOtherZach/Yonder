"""Knowledge-assisted prompt seeding — proves learned destination candidates
reach the Grok prompt (and stay out of it when they should).

Covers, without any live AI call (asserts on prompt construction via a
patched ``_chat``):
  - cold start: empty knowledge tables → prompt identical to pre-learning
  - seeded prompt: populated tables → learned_candidates in the user payload,
    with route knowledge respected (verified first)
  - novelty bypass: refresh requests skip injection entirely
  - dead-route filter: a fresh-failed route never appears as a candidate

Uses the throwaway-schema PG isolation pattern (``pg_schema`` fixture).
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import yonder.grok as grok_module
from yonder.grok import _PARSE_CACHE, GrokClient
from yonder.knowledge import record_interpretation, record_route_outcome
from yonder.config import Settings

_TODAY = date(2026, 8, 3)
_DEPART = date(2026, 9, 10)

# Minimal-but-valid replies so the methods complete after prompt construction.
_ADVENTURE_JSON = json.dumps(
    {
        "trip_kind": "getaway",
        "origin": "YVR",
        "destination": "YVR",
        "depart_date": _DEPART.isoformat(),
        "arrive_by": None,
        "currency": "USD",
        "min_stop_days": 3,
        "max_stop_days": 5,
        "vibe": "chaos",
        "intent_summary": "test",
        "candidates": [
            {"iata": "BKK", "city": "Bangkok", "country": "TH",
             "stay_days": 3, "why": "x", "vibe_tags": ["food"]}
        ],
    }
)
_UNIFIED_JSON = json.dumps({"escape": None, "detour": None, "quest": {"ideas": []}})


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    _PARSE_CACHE.clear()
    yield
    _PARSE_CACHE.clear()


def _settings(**kw) -> Settings:
    defaults = {
        "xai_api_key": "test-key",
        "xai_model": "grok-test",
        "default_currency": "USD",
        "home_iata": "YVR",
    }
    defaults.update(kw)
    return Settings(**defaults)


def _form(**kw) -> dict:
    base = {
        "origin": "YVR",
        "destination": "",
        "depart": _DEPART.isoformat(),
        "arrive_by": "",
        "min_stop_days": 3,
        "max_stop_days": 5,
        "max_candidates": 5,
        "currency": "USD",
        "vibe": "chaos",
        "avoid_countries": [],
        "visited_countries": [],
    }
    base.update(kw)
    return base


def _seed_knowledge(monkeypatch, *, dead: str | None = None) -> None:
    """Two learned chaos candidates from YVR; optionally kill one route."""
    for dest in ("BKK", "HAN"):
        record_interpretation(
            vibe="chaos", raw_query="cheap chaos", origin="YVR", dest_iata=dest,
            interpretation="cheap street food", tags=["food", "budget"],
        )
    monkeypatch.setattr(
        "yonder.vibe_signals.scores_for_vibe", lambda v, **kw: {"BKK": 2.0, "HAN": 2.0}
    )
    record_route_outcome(origin="YVR", dest="BKK", success=True, price=700.0)
    if dead:
        record_route_outcome(origin="YVR", dest=dead, success=False)


async def _run_translate(client: GrokClient, **kw):
    with patch.object(
        client, "_chat", new_callable=AsyncMock, return_value=_ADVENTURE_JSON
    ) as chat:
        await client.translate_adventure(
            prompt="cheap chaos somewhere",
            form=_form(),
            default_currency="USD",
            today=_TODAY,
            **kw,
        )
    assert chat.call_count == 1
    system, user = chat.call_args.args[0], chat.call_args.args[1]
    return system, json.loads(user)


async def _run_unified(client: GrokClient, **kw):
    with patch.object(
        client, "_chat", new_callable=AsyncMock, return_value=_UNIFIED_JSON
    ) as chat:
        await client.plan_unified(
            "cheap chaos somewhere",
            "chaos",
            "YVR",
            depart_date=_DEPART,
            currency="USD",
            today=_TODAY,
            **kw,
        )
    assert chat.call_count == 1
    system, user = chat.call_args.args[0], chat.call_args.args[1]
    return system, json.loads(user)


# ── 1. Cold start — empty tables → prompt unchanged, no errors ───────────────


@pytest.mark.asyncio
async def test_cold_start_translate_prompt_has_no_candidates():
    client = GrokClient(_settings())
    system, payload = await _run_translate(client)
    assert "learned_candidates" not in payload
    assert "learned_candidates" not in system


@pytest.mark.asyncio
async def test_cold_start_unified_prompt_has_no_candidates():
    client = GrokClient(_settings())
    system, payload = await _run_unified(client)
    assert "learned_candidates" not in payload
    assert "learned_candidates" not in system


@pytest.mark.asyncio
async def test_cold_start_prompt_identical_to_seeding_disabled():
    """Empty tables produce byte-for-byte the same prompt as no seeding at all
    (seeding layer forced empty) — pre-learning behavior is preserved."""
    client = GrokClient(_settings())
    cold_sys, cold_payload = await _run_translate(client)
    with patch.object(grok_module, "_learned_seed_candidates", return_value=[]):
        off_sys, off_payload = await _run_translate(client)
    assert cold_sys == off_sys
    assert cold_payload == off_payload


# ── 2. Seeded prompt — populated tables → candidates in the payload ─────────


@pytest.mark.asyncio
async def test_seeded_translate_prompt_contains_learned_candidates(monkeypatch):
    _seed_knowledge(monkeypatch)
    client = GrokClient(_settings())
    system, payload = await _run_translate(client)
    learned = payload.get("learned_candidates")
    assert learned, "populated knowledge tables must seed the prompt"
    iatas = [c["iata"] for c in learned]
    assert "BKK" in iatas and "HAN" in iatas
    # Origin route verification respected: verified route ranks first
    assert learned[0]["iata"] == "BKK"
    assert learned[0]["route"] == "verified"
    # System prompt explains the candidates are optional
    assert "learned_candidates" in system
    assert "OPTIONAL" in system


@pytest.mark.asyncio
async def test_seeded_unified_prompt_contains_learned_candidates(monkeypatch):
    _seed_knowledge(monkeypatch)
    client = GrokClient(_settings())
    system, payload = await _run_unified(client)
    learned = payload.get("learned_candidates")
    assert learned
    assert learned[0]["iata"] == "BKK" and learned[0]["route"] == "verified"
    assert "learned_candidates" in system


# ── 3. Novelty bypass — refresh requests skip injection ─────────────────────


@pytest.mark.asyncio
async def test_refresh_translate_skips_injection(monkeypatch):
    _seed_knowledge(monkeypatch)
    client = GrokClient(_settings())
    system, payload = await _run_translate(client, seed_learned=False)
    assert "learned_candidates" not in payload
    assert "learned_candidates" not in system


@pytest.mark.asyncio
async def test_refresh_unified_skips_injection(monkeypatch):
    _seed_knowledge(monkeypatch)
    client = GrokClient(_settings())
    # Refresh path calls plan_unified with use_cache=False
    system, payload = await _run_unified(client, use_cache=False)
    assert "learned_candidates" not in payload
    assert "learned_candidates" not in system


# ── 4. Dead-route filter — fresh-failed route never appears ─────────────────


@pytest.mark.asyncio
async def test_dead_route_never_seeded(monkeypatch):
    _seed_knowledge(monkeypatch, dead="HAN")
    client = GrokClient(_settings())
    for system, payload in (
        await _run_translate(client),
        await _run_unified(client),
    ):
        learned = payload.get("learned_candidates")
        assert learned, "live candidates must still be seeded"
        iatas = [c["iata"] for c in learned]
        assert "BKK" in iatas
        assert "HAN" not in iatas, "fresh-failed route must be filtered out"
