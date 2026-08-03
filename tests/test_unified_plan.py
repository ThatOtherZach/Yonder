"""GrokClient.plan_unified — one combined call for escape + detour + quest.

Cold-start Finds combine three per-panel Grok calls into one structured call.
Incomplete/invalid sections must come back None/[] so the caller falls back
to the individual per-panel calls (quality guard).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from yonder.config import Settings
import yonder.grok as grok_mod
from yonder.grok import GrokClient, ParsedTrip


DEPART = date(2026, 9, 10)


def _settings() -> Settings:
    return Settings(xai_api_key="test-key", default_currency="USD", testing=True)


def _full_payload() -> dict:
    return {
        "escape": {
            "origin": "YVR",
            "destination": "NRT",
            "depart_date": "2026-09-10",
            "return_date": None,
            "currency": "USD",
            "nonstop_only": False,
            "intent_summary": "Tokyo escape",
            "assumptions": [],
        },
        "detour": {
            "trip_kind": "getaway",
            "origin": "YVR",
            "destination": "YVR",
            "depart_date": "2026-09-10",
            "arrive_by": None,
            "currency": "USD",
            "min_stop_days": 2,
            "max_stop_days": 5,
            "vibe": "food",
            "intent_summary": "food getaway",
            "candidates": [
                {"iata": "BKK", "city": "Bangkok", "country": "TH", "stay_days": 3, "why": "street food", "vibe_tags": ["food"]},
                {"iata": "TPE", "city": "Taipei", "country": "TW", "stay_days": 3, "why": "night markets", "vibe_tags": ["food"]},
            ],
        },
        "quest": {
            "ideas": [
                {
                    "entry_iata": "HAN",
                    "exit_iata": "BKK",
                    "entry_city": "Hanoi",
                    "exit_city": "Bangkok",
                    "overland_narrative": "Reunification Express south, overland into Thailand.",
                    "transport": ["Reunification Express"],
                    "highlights": ["Hội An"],
                }
            ]
        },
    }


async def _run_unified(monkeypatch, payload, **kwargs):
    async def _fake_chat(self, system, user, *, temperature=0.2):
        return json.dumps(payload)

    monkeypatch.setattr(GrokClient, "_chat", _fake_chat)
    # Isolate from the module-level repeat-Find cache between tests
    monkeypatch.setattr(grok_mod, "_PARSE_CACHE", {})
    async with GrokClient(_settings()) as grok:
        return await grok.plan_unified(
            "somewhere with great food",
            "food",
            "YVR",
            depart_date=DEPART,
            currency="USD",
            **kwargs,
        )


@pytest.mark.anyio
async def test_all_three_sections_parse(monkeypatch):
    out = await _run_unified(monkeypatch, _full_payload())
    trip = out["escape"]
    assert isinstance(trip, ParsedTrip)
    assert trip.destination == "NRT"
    assert trip.adults == 1
    req, ideas = out["detour_cities"]
    assert req.trip_kind == "getaway"
    assert {i.iata for i in ideas} == {"BKK", "TPE"}
    assert out["quest_pairs"] and out["quest_pairs"][0]["entry_iata"] == "HAN"


@pytest.mark.anyio
async def test_missing_sections_return_none(monkeypatch):
    payload = _full_payload()
    del payload["escape"]
    payload["detour"]["candidates"] = []
    payload["quest"] = {"ideas": []}
    out = await _run_unified(monkeypatch, payload)
    assert out["escape"] is None
    assert out["detour_cities"] is None
    assert out["quest_pairs"] == []


@pytest.mark.anyio
async def test_escape_passport_violation_drops_section(monkeypatch):
    # Destination in visited list → escape section dropped so per-panel
    # fallback (which has a correction retry) runs instead.
    out = await _run_unified(monkeypatch, _full_payload(), visited=["JP"])
    assert out["escape"] is None
    # Other sections are unaffected
    assert out["detour_cities"] is not None


@pytest.mark.anyio
async def test_quest_avoid_country_filtered(monkeypatch):
    out = await _run_unified(monkeypatch, _full_payload(), avoid=["TH"])
    assert out["quest_pairs"] == []  # exit BKK is in avoided TH


async def _run_twice(monkeypatch, *, second_kwargs=None, settings2=None):
    """Run the same Find twice; return (out1, out2, chat_call_count)."""
    calls = {"n": 0}

    async def _fake_chat(self, system, user, *, temperature=0.2):
        calls["n"] += 1
        return json.dumps(_full_payload())

    monkeypatch.setattr(GrokClient, "_chat", _fake_chat)
    monkeypatch.setattr(grok_mod, "_PARSE_CACHE", {})
    kwargs = dict(depart_date=DEPART, currency="USD")
    async with GrokClient(_settings()) as grok:
        out1 = await grok.plan_unified("somewhere with great food", "food", "YVR", **kwargs)
    async with GrokClient(settings2 or _settings()) as grok:
        out2 = await grok.plan_unified(
            "somewhere with great food", "food", "YVR", **{**kwargs, **(second_kwargs or {})}
        )
    return out1, out2, calls["n"]


@pytest.mark.anyio
async def test_repeat_find_skips_ai_call(monkeypatch):
    out1, out2, n = await _run_twice(monkeypatch)
    assert n == 1  # second identical Find served from cache
    assert isinstance(out2["escape"], ParsedTrip)
    assert out2["escape"].destination == out1["escape"].destination == "NRT"
    req, ideas = out2["detour_cities"]
    assert req.trip_kind == "getaway"
    assert {i.iata for i in ideas} == {"BKK", "TPE"}
    assert out2["quest_pairs"] and out2["quest_pairs"][0]["entry_iata"] == "HAN"


@pytest.mark.anyio
async def test_refresh_bypasses_cache(monkeypatch):
    _, _, n = await _run_twice(monkeypatch, second_kwargs={"use_cache": False})
    assert n == 2  # novelty refresh must always hit the AI


@pytest.mark.anyio
async def test_model_switch_busts_cache(monkeypatch):
    byom = Settings(
        xai_api_key="test-key",
        byom_base_url="https://byom.example.com/v1",
        byom_api_key="byom-key",
        byom_model="other-model",
        default_currency="USD",
        testing=True,
    )
    _, _, n = await _run_twice(monkeypatch, settings2=byom)
    assert n == 2  # different backend fingerprint → no cache hit


@pytest.mark.anyio
async def test_garbage_payload_returns_empty_sections(monkeypatch):
    async def _fake_chat(self, system, user, *, temperature=0.2):
        return "not json at all"

    monkeypatch.setattr(GrokClient, "_chat", _fake_chat)
    async with GrokClient(_settings()) as grok:
        with pytest.raises(Exception):
            await grok.plan_unified(
                "prompt", "food", "YVR", depart_date=DEPART, currency="USD"
            )
