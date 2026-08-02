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


# ---------------------------------------------------------------------------
# visited_countries filter does NOT apply to detour trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visited_country_candidate_kept_on_detour():
    """A candidate from a visited country must NOT be filtered out on a detour trip.

    The visited-country constraint only applies to getaway trips ("somewhere new").
    On a detour the traveller may legitimately stop in a country they've visited
    before, so the filter at yonder/grok.py must leave those candidates in place.

    Grok returns JP (Tokyo / TYO) and JP is in visited_countries, but trip_kind
    is "detour" — translate_adventure() must keep it in the StopoverIdea list.
    """
    visited_candidate = {
        "iata": "TYO",
        "city": "Tokyo",
        "country": "JP",   # <-- in visited_countries, but trip is a detour
        "stay_days": 3,
        "why": "Great stopover on the way to London",
        "vibe_tags": ["city"],
    }
    other_candidate = {
        "iata": "SIN",
        "city": "Singapore",
        "country": "SG",   # <-- NOT in visited_countries
        "stay_days": 2,
        "why": "Another option",
        "vibe_tags": ["city"],
    }
    mock = _make_response(trip_kind="detour", candidates=[visited_candidate, other_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "destination": "LHR",
            "visited_countries": ["JP"],
            "avoid_countries": [],
            "max_candidates": 5,
        },
    )

    assert "TYO" in iatas, (
        "TYO (JP) must be kept on a detour trip even though JP is in visited_countries; "
        f"got candidates: {iatas}"
    )
    assert "SIN" in iatas, (
        f"SIN (SG) is not in visited_countries and should also be kept; got candidates: {iatas}"
    )


# ---------------------------------------------------------------------------
# visited_countries filter does NOT apply to stop-off trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visited_country_candidate_kept_on_stop_off():
    """A candidate from a visited country must NOT be filtered out on a stop-off trip.

    Stop-off trips (trip_kind == "stop_off") are point-A-to-point-B journeys with
    an intentional intermediate stop — semantically identical to a detour, not a
    getaway.  The visited-country constraint only applies to getaway trips where the
    traveller wants somewhere new; on a stop-off the stop city is chosen for routing
    and convenience, so a previously-visited country is entirely valid.

    Grok returns JP (Tokyo / NRT) and JP is in visited_countries, but trip_kind is
    "stop_off" — filter_ideas() must keep it in the StopoverIdea list.
    """
    visited_candidate = {
        "iata": "NRT",
        "city": "Tokyo Narita",
        "country": "JP",   # <-- in visited_countries, but trip is a stop-off
        "stay_days": 3,
        "why": "Great stop on the way to Singapore",
        "vibe_tags": ["city"],
    }
    other_candidate = {
        "iata": "ICN",
        "city": "Seoul",
        "country": "KR",   # <-- NOT in visited_countries
        "stay_days": 2,
        "why": "Another option",
        "vibe_tags": ["city"],
    }
    mock = _make_response(trip_kind="stop_off", candidates=[visited_candidate, other_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "destination": "SIN",
            "visited_countries": ["JP"],
            "avoid_countries": [],
            "max_candidates": 5,
        },
    )

    assert "NRT" in iatas, (
        "NRT (JP) must be kept on a stop-off trip even though JP is in visited_countries; "
        f"got candidates: {iatas}"
    )
    assert "ICN" in iatas, (
        f"ICN (KR) is not in visited_countries and should also be kept; got candidates: {iatas}"
    )


# ---------------------------------------------------------------------------
# avoid_countries normalises country code case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avoid_country_filter_catches_lowercase_country_code():
    """A candidate whose country arrives as lowercase (e.g. "fr") must still be filtered.

    The normalisation at yonder/grok.py calls .upper() on the candidate's country
    field before comparing against avoid_set.  This test confirms that a lowercase
    code is correctly caught even though avoid_countries stores the code in uppercase.
    """
    violating_candidate = {
        "iata": "CDG",
        "city": "Paris",
        "country": "fr",   # <-- lowercase; avoid_countries has "FR"
        "stay_days": 2,
        "why": "Grok returned a lowercase country code",
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
        'CDG (country "fr") must be filtered out because FR is in avoid_countries; '
        f"got candidates: {iatas}"
    )
    assert "AMS" in iatas, (
        f"AMS (NL) is not in avoid_countries and should be kept; got candidates: {iatas}"
    )


@pytest.mark.asyncio
async def test_avoid_country_filter_catches_mixed_case_country_code():
    """A candidate whose country arrives in mixed case (e.g. "Fr") must still be filtered.

    Guards against a future change to the normalisation that might only handle
    fully-uppercase or fully-lowercase codes and miss mixed-case variants.
    """
    violating_candidate = {
        "iata": "CDG",
        "city": "Paris",
        "country": "Fr",   # <-- mixed-case; avoid_countries has "FR"
        "stay_days": 2,
        "why": "Grok returned a mixed-case country code",
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
        'CDG (country "Fr") must be filtered out because FR is in avoid_countries; '
        f"got candidates: {iatas}"
    )
    assert "AMS" in iatas, (
        f"AMS (NL) is not in avoid_countries and should be kept; got candidates: {iatas}"
    )


# ---------------------------------------------------------------------------
# avoid_countries wins when a candidate appears in BOTH lists (detour)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avoid_country_wins_over_visited_country_on_detour():
    """avoid_countries always beats visited_countries — even on a detour.

    A candidate whose country is in *both* avoid_countries and visited_countries
    must be filtered out.  The visited-country rule does not apply to detour trips,
    but avoid_countries applies unconditionally, so the candidate must still be
    removed.

    This guards against a future refactor that accidentally short-circuits the
    avoid_countries check because the visited-country branch was reached first
    (or vice-versa).
    """
    both_listed_candidate = {
        "iata": "CDG",
        "city": "Paris",
        "country": "FR",   # <-- in BOTH avoid_countries AND visited_countries
        "stay_days": 2,
        "why": "Grok suggested it despite constraints",
        "vibe_tags": ["city"],
    }
    safe_candidate = {
        "iata": "AMS",
        "city": "Amsterdam",
        "country": "NL",   # <-- in neither list
        "stay_days": 2,
        "why": "allowed stop",
        "vibe_tags": ["city"],
    }
    mock = _make_response(trip_kind="detour", candidates=[both_listed_candidate, safe_candidate])

    iatas = await _run_translate(
        mock_response=mock,
        form={
            "origin": "YVR",
            "destination": "LHR",
            "avoid_countries": ["FR"],
            "visited_countries": ["FR"],   # same country in both lists
            "max_candidates": 5,
        },
    )

    assert "CDG" not in iatas, (
        "CDG (FR) must be filtered out because FR is in avoid_countries, "
        "even though the trip is a detour and FR is also in visited_countries; "
        f"got candidates: {iatas}"
    )
    assert "AMS" in iatas, (
        f"AMS (NL) is in neither list and should be kept; got candidates: {iatas}"
    )
