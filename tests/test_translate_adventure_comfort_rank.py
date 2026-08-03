"""Tests confirming translate_adventure() sends the correct traveler_comfort rank
to Grok based on visited-country count, and that the system prompt carries the
boldness-guidance phrase.

The *discriminating* signal lives in the user payload's `traveler_comfort` field.
The system prompt contains both guidance phrases unconditionally (static text),
so system-prompt phrase assertions are structural guards, not rank-discriminators.
The real rank-behavior is verified by asserting the payload value for each case.

Rank thresholds now live in yonder/xp.py as km²-unlocked tiers (XP = total
land area of visited tiles).  Country lists are valid tile lists, so the
expected rank for each fixture is computed with compute_xp() on the same
list the payload was built from — the payload must always carry exactly
that rank.

The boldness rule in the system prompt (yonder/grok.py::translate_adventure ~line 484):
  "Chaos Pilot/Nomadic Soul → prefer off-beaten-path, emerging, or unconventional stops"
  "Armchair Explorer/Day Tripper → prefer safe hubs, easy connections, well-touristed cities"
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from yonder.config import Settings
from yonder.grok import GrokClient

# ---------------------------------------------------------------------------
# Minimal valid JSON that translate_adventure() can parse without crashing
# ---------------------------------------------------------------------------

_MOCK_RESPONSE = json.dumps(
    {
        "trip_kind": "getaway",
        "origin": "YVR",
        "destination": "YVR",
        "depart_date": "2026-09-01",
        "arrive_by": None,
        "currency": "CAD",
        "min_stop_days": 3,
        "max_stop_days": 5,
        "vibe": "adventure",
        "intent_summary": "quick escape",
        "candidates": [
            {
                "iata": "MEX",
                "city": "Mexico City",
                "country": "MX",
                "stay_days": 4,
                "why": "affordable and vibrant",
                "vibe_tags": ["city", "food"],
            },
            {
                "iata": "BOG",
                "city": "Bogotá",
                "country": "CO",
                "stay_days": 3,
                "why": "cheap and chaotic",
                "vibe_tags": ["city"],
            },
        ],
    }
)


def _settings_with_key() -> Settings:
    s = Settings()
    s.xai_api_key = "test-key"  # type: ignore[attr-defined]
    return s


def _iso2_list(n: int) -> list[str]:
    """Return n distinct fake ISO2 codes (AA, AB, …)."""
    codes: list[str] = []
    for i in range(n):
        first = chr(ord("A") + i // 26)
        second = chr(ord("A") + i % 26)
        code = f"{first}{second}"
        if code not in codes:
            codes.append(code)
    return codes[:n]


async def _capture_user_payload(
    visited_count: int,
    avoid_count: int = 0,
) -> dict:
    """Run translate_adventure() with a stubbed _chat and return the parsed user payload."""
    settings = _settings_with_key()
    client = GrokClient(settings)

    captured: list[str] = []

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        captured.append(user)
        return _MOCK_RESPONSE

    with patch.object(client, "_chat", side_effect=fake_chat):
        await client.translate_adventure(
            prompt="I want to go somewhere adventurous",
            form={
                "origin": "YVR",
                "visited_countries": _iso2_list(visited_count),
                "avoid_countries": _iso2_list(avoid_count),
                "max_candidates": 2,
            },
            today=date(2026, 8, 1),
        )

    assert captured, "_chat was never called"
    return json.loads(captured[0])


async def _capture_system_prompt(visited_count: int) -> str:
    """Run translate_adventure() with a stubbed _chat and return the system prompt."""
    settings = _settings_with_key()
    client = GrokClient(settings)

    captured: list[str] = []

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        captured.append(system)
        return _MOCK_RESPONSE

    with patch.object(client, "_chat", side_effect=fake_chat):
        await client.translate_adventure(
            prompt="I want to go somewhere adventurous",
            form={
                "origin": "YVR",
                "visited_countries": _iso2_list(visited_count),
                "avoid_countries": [],
                "max_candidates": 2,
            },
            today=date(2026, 8, 1),
        )

    assert captured, "_chat was never called"
    return captured[0]


# ---------------------------------------------------------------------------
# Payload rank assertions — the discriminating tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_armchair_explorer_rank_in_payload():
    """0 visited countries → Armchair Explorer in the user payload sent to Grok."""
    payload = await _capture_user_payload(visited_count=0)
    assert payload.get("traveler_comfort") == "Armchair Explorer", (
        f"Expected 'Armchair Explorer' for 0 visited countries, "
        f"got {payload.get('traveler_comfort')!r}"
    )


@pytest.mark.asyncio
async def test_chaos_pilot_rank_in_payload():
    """100 visited countries (~most of the planet) → Chaos Pilot in the payload."""
    payload = await _capture_user_payload(visited_count=100)
    assert payload.get("traveler_comfort") == "Chaos Pilot", (
        f"Expected 'Chaos Pilot' for 100 visited countries, "
        f"got {payload.get('traveler_comfort')!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("visited_count", [0, 1, 5, 10, 20, 40, 60, 80, 100])
async def test_rank_matches_km2_engine_in_payload(visited_count: int):
    """The payload rank always equals compute_xp() on the same visited list."""
    from yonder.xp import compute_xp

    expected_rank = compute_xp(_iso2_list(visited_count), [])["rank"]
    payload = await _capture_user_payload(visited_count=visited_count)
    assert payload.get("traveler_comfort") == expected_rank, (
        f"visited={visited_count}: expected {expected_rank!r}, "
        f"got {payload.get('traveler_comfort')!r}"
    )


# ---------------------------------------------------------------------------
# Structural system-prompt guards — confirm the guidance phrases are present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_contains_off_beaten_path_guidance():
    """The system prompt must contain the 'off-beaten-path' phrase for high-rank travelers."""
    system = await _capture_system_prompt(visited_count=100)
    assert "off-beaten-path" in system, (
        "The 'off-beaten-path' guidance phrase is missing from the system prompt. "
        "Check translate_adventure() in yonder/grok.py."
    )


@pytest.mark.asyncio
async def test_system_prompt_contains_safe_hubs_guidance():
    """The system prompt must contain the 'safe hubs' phrase for low-rank travelers."""
    system = await _capture_system_prompt(visited_count=0)
    assert "safe hubs" in system, (
        "The 'safe hubs' guidance phrase is missing from the system prompt. "
        "Check translate_adventure() in yonder/grok.py."
    )
