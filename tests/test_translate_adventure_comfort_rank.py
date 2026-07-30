"""Tests confirming translate_adventure() sends the correct traveler_comfort rank
to Grok based on visited-country count, and that the system prompt carries the
boldness-guidance phrase.

The *discriminating* signal lives in the user payload's `traveler_comfort` field.
The system prompt contains both guidance phrases unconditionally (static text),
so system-prompt phrase assertions are structural guards, not rank-discriminators.
The real rank-behavior is verified by asserting the payload value for each case.

Rank thresholds (from yonder/xp.py, XP = visited_count × 10):
    0  visited  →     0 XP → Armchair Explorer
    1  visited  →    10 XP → Day Tripper
    5  visited  →    50 XP → Weekend Wanderer
   10  visited  →   100 XP → Seasoned Traveller
   20  visited  →   200 XP → Globe-Trotter
   40  visited  →   400 XP → Nomadic Soul
   60  visited  →   600 XP → Expedition Regular
   80  visited  →   800 XP → Chaos Pilgrim
  100  visited  →  1000 XP → Chaos Pilot

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
    """100 visited countries (1000 XP) → Chaos Pilot in the user payload sent to Grok."""
    payload = await _capture_user_payload(visited_count=100)
    assert payload.get("traveler_comfort") == "Chaos Pilot", (
        f"Expected 'Chaos Pilot' for 100 visited countries, "
        f"got {payload.get('traveler_comfort')!r}"
    )


@pytest.mark.asyncio
async def test_intermediate_rank_in_payload():
    """20 visited countries (200 XP) → Globe-Trotter (mid-tier) in the payload."""
    payload = await _capture_user_payload(visited_count=20)
    assert payload.get("traveler_comfort") == "Globe-Trotter", (
        f"Expected 'Globe-Trotter' for 20 visited countries, "
        f"got {payload.get('traveler_comfort')!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visited_count, expected_rank",
    [
        (0,   "Armchair Explorer"),
        (1,   "Day Tripper"),
        (5,   "Weekend Wanderer"),
        (10,  "Seasoned Traveller"),
        (20,  "Globe-Trotter"),
        (40,  "Nomadic Soul"),
        (60,  "Expedition Regular"),
        (80,  "Chaos Pilgrim"),
        (100, "Chaos Pilot"),
    ],
)
async def test_rank_boundaries_in_payload(visited_count: int, expected_rank: str):
    """Each XP threshold maps to the correct rank in the user payload."""
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
