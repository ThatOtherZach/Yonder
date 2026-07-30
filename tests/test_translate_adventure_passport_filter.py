"""Tests confirming translate_adventure() filters out passport-map violations
from Grok's candidate list, even when Grok ignores the system-prompt constraint.

Two invariants under test:
1. avoid_countries  — any candidate whose ISO2 country is in avoid_countries is
                      dropped, regardless of trip_kind.
2. visited_countries — any candidate whose ISO2 country is in visited_countries is
                       dropped when trip_kind == "getaway" (the only kind where the
                       constraint applies per the system prompt and the filter at
                       yonder/grok.py lines 616-622).

Both tests stub _chat to return a response that *violates* the constraint and then
assert the offending candidate is absent from the returned StopoverIdea list.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from yonder.config import Settings
from yonder.grok import GrokClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings_with_key() -> Settings:
    s = Settings()
    s.xai_api_key = "test-key"  # type: ignore[attr-defined]
    return s


def _make_response(*, trip_kind: str, candidates: list[dict]) -> str:
    """Build a minimal valid translate_adventure JSON response."""
    return json.dumps(
        {
            "trip_kind": trip_kind,
            "origin": "YVR",
            "destination": "YVR" if trip_kind == "getaway" else "LHR",
            "depart_date": "2026-09-01",
            "arrive_by": None,
            "currency": "CAD",
            "min_stop_days": 3,
            "max_stop_days": 5,
            "vibe": "adventure",
            "intent_summary": "test trip",
            "candidates": candidates,
        }
    )


async def _run_translate(
    *,
    mock_response: str,
    form: dict,
) -> list[str]:
    """Run translate_adventure() with a stubbed _chat; return candidate IATA codes."""
    settings = _settings_with_key()
    client = GrokClient(settings)

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        return mock_response

    with patch.object(client, "_chat", side_effect=fake_chat):
        _req, ideas = await client.translate_adventure(
            prompt="I want to go somewhere new",
            form=form,
            today=date(2026, 8, 1),
        )

    return [idea.iata for idea in ideas]


# ---------------------------------------------------------------------------
# avoid_countries filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avoid_country_candidate_is_filtered_out():
    """A candidate whose country is in avoid_countries must be removed.

    Grok returns MX (Mexico City / MEX) even though MX is in avoid_countries.
    translate_adventure() must silently drop it from the StopoverIdea list.
    """
    violating_candidate = {
        "iata": "MEX",
        "city": "Mexico City",
        "country": "MX",   # <-- in avoid_countries
        "stay_days": 4,
        "why": "Grok ignored the constraint",
        "vibe_tags": ["city"],
    }
    safe_candidate = {
        "iata": "BOG",
        "city": "Bogotá",
        "country": "CO",   # <-- NOT in avoid_countries
        "stay_days": 3,
        "why": "allowed destination",
        "vibe_tags": ["city"],
    }
    mock = _make_response(trip_kind="getaway", candidates=[violating_candidate, safe_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "avoid_countries": ["MX"],
            "visited_countries": [],
            "max_candidates": 5,
        },
    )

    assert "MEX" not in iatas, (
        "MEX (MX) must be filtered out because MX is in avoid_countries; "
        f"got candidates: {iatas}"
    )
    assert "BOG" in iatas, (
        f"BOG (CO) is not in avoid_countries and should be kept; got candidates: {iatas}"
    )


@pytest.mark.asyncio
async def test_avoid_country_filter_applies_to_detour_trips_too():
    """avoid_countries filtering is not limited to getaway trips.

    A detour-mode response that includes a candidate from an avoided country
    must still have that candidate removed.
    """
    violating_candidate = {
        "iata": "CDG",
        "city": "Paris",
        "country": "FR",   # <-- in avoid_countries
        "stay_days": 2,
        "why": "Grok suggested it anyway",
        "vibe_tags": ["city"],
    }
    safe_candidate = {
        "iata": "AMS",
        "city": "Amsterdam",
        "country": "NL",   # <-- NOT in avoid_countries
        "stay_days": 2,
        "why": "allowed stop",
        "vibe_tags": ["city"],
    }
    mock = _make_response(trip_kind="detour", candidates=[violating_candidate, safe_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "destination": "LHR",
            "avoid_countries": ["FR"],
            "visited_countries": [],
            "max_candidates": 5,
        },
    )

    assert "CDG" not in iatas, (
        "CDG (FR) must be filtered out because FR is in avoid_countries even on a detour; "
        f"got candidates: {iatas}"
    )
    assert "AMS" in iatas, (
        f"AMS (NL) is not in avoid_countries and should be kept; got candidates: {iatas}"
    )


# ---------------------------------------------------------------------------
# visited_countries filter (getaway trips)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visited_country_candidate_filtered_on_getaway():
    """A candidate from a visited country must be removed on a getaway trip.

    Grok returns JP (Tokyo / TYO) even though JP is in visited_countries and
    trip_kind is "getaway".  translate_adventure() must drop it.
    """
    violating_candidate = {
        "iata": "TYO",
        "city": "Tokyo",
        "country": "JP",   # <-- in visited_countries
        "stay_days": 5,
        "why": "Grok forgot the passport map",
        "vibe_tags": ["city"],
    }
    safe_candidate = {
        "iata": "BKK",
        "city": "Bangkok",
        "country": "TH",   # <-- NOT in visited_countries
        "stay_days": 4,
        "why": "never been here",
        "vibe_tags": ["city", "food"],
    }
    mock = _make_response(trip_kind="getaway", candidates=[violating_candidate, safe_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "visited_countries": ["JP"],
            "avoid_countries": [],
            "max_candidates": 5,
        },
    )

    assert "TYO" not in iatas, (
        "TYO (JP) must be filtered out because JP is in visited_countries on a getaway; "
        f"got candidates: {iatas}"
    )
    assert "BKK" in iatas, (
        f"BKK (TH) is not in visited_countries and should be kept; got candidates: {iatas}"
    )


@pytest.mark.asyncio
async def test_visited_country_all_candidates_filtered_yields_empty_list():
    """When every candidate Grok returns is from a visited country, the list is empty.

    This guards against a prompt regression where Grok proposes only already-visited
    destinations on a getaway — the result must be an empty ideas list, not a crash.
    """
    all_visited_candidates = [
        {
            "iata": "NRT",
            "city": "Tokyo Narita",
            "country": "JP",
            "stay_days": 4,
            "why": "Grok forgot constraint #1",
            "vibe_tags": [],
        },
        {
            "iata": "ICN",
            "city": "Seoul",
            "country": "KR",
            "stay_days": 3,
            "why": "Grok forgot constraint #2",
            "vibe_tags": [],
        },
    ]
    mock = _make_response(trip_kind="getaway", candidates=all_visited_candidates)

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "visited_countries": ["JP", "KR"],
            "avoid_countries": [],
            "max_candidates": 5,
        },
    )

    assert iatas == [], (
        "All candidates are from visited countries on a getaway; expected empty list, "
        f"got: {iatas}"
    )
