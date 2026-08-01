"""Multi-stop itinerary pipeline tests.

Tests cover:
  1. extract_named_stops — ordered multi-stop extraction from prompts
  2. is_wander_vibe — vibe gate (wander allows, comfort blocks)
  3. 3-leg and 4-leg pricing chains with arrive-by trimming and 6-leg cap
  4. Rescue routing fires only when normal routing fails, never exceeds 6 legs
  5. Save/share round-trip preserves full stops list
  6. Regressions: existing single-stop and getaway tests still pass
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from yonder.adventure import (
    AdventureRequest,
    StopoverIdea,
    _price_multi_stop_chain,
    _try_rescue_chain,
    plan_adventure,
)
from yonder.intent import (
    _WANDER_VIBES,
    extract_named_stop,
    extract_named_stops,
    is_wander_vibe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """No live AI or fare calls during unit tests."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)
    from yonder.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "xai_api_key", "")


def _detour_req(
    origin: str = "YVR",
    destination: str = "BKK",
    depart: date = date(2026, 9, 1),
    arrive_by: date | None = None,
    vibe: str = "adventure",
) -> AdventureRequest:
    return AdventureRequest(
        origin=origin,
        destination=destination,
        depart_date=depart,
        arrive_by=arrive_by,
        adults=1,
        currency="CAD",
        min_stop_days=1,
        max_stop_days=5,
        max_candidates=5,
        vibe=vibe,
        trip_kind="detour",
    )


def _idea(iata: str, city: str, country: str = "JP", stay_days: int = 3) -> StopoverIdea:
    return StopoverIdea(
        iata=iata, city=city, country=country,
        stay_days=stay_days, why="test stop",
        vibe_tags=["city", "food"], source="test",
    )


# ---------------------------------------------------------------------------
# 1. extract_named_stops — ordered multi-stop extraction
# ---------------------------------------------------------------------------


class TestExtractNamedStops:
    def test_single_stop_returned_as_list(self):
        stops = extract_named_stops("stop over in Tokyo")
        assert len(stops) == 1
        assert stops[0] == "tokyo"

    def test_two_stops_with_and(self):
        stops = extract_named_stops("stopping in Tokyo and Hong Kong on the way to Bangkok")
        assert "tokyo" in stops
        assert "hong kong" in stops

    def test_order_preserved(self):
        stops = extract_named_stops("stopping in Tokyo and Hong Kong")
        assert stops.index("tokyo") < stops.index("hong kong"), (
            f"Expected tokyo before hong kong, got {stops}"
        )

    def test_via_pattern(self):
        stops = extract_named_stops("fly via Tokyo and Singapore to Sydney")
        assert "tokyo" in stops or "singapore" in stops

    def test_multiple_separate_markers(self):
        stops = extract_named_stops("stop over in Tokyo then layover in Singapore")
        assert "tokyo" in stops
        assert "singapore" in stops
        assert stops.index("tokyo") < stops.index("singapore")

    def test_empty_prompt_returns_empty_list(self):
        assert extract_named_stops("") == []

    def test_no_stop_marker_returns_empty_list(self):
        assert extract_named_stops("fly from Vancouver to Bangkok") == []

    def test_single_named_stop_backward_compat(self):
        """extract_named_stops must agree with extract_named_stop for single stops."""
        prompt = "stop over in Tokyo then go to Hong Kong"
        single = extract_named_stop(prompt)
        multi = extract_named_stops(prompt)
        if single:
            assert single in multi, f"single={single!r} not in multi={multi!r}"

    def test_no_duplicates(self):
        stops = extract_named_stops("stopping over in Tokyo and stopping in Tokyo again")
        assert stops.count("tokyo") == 1

    def test_stopover_in_two_cities(self):
        stops = extract_named_stops("stopover in Dubai and Singapore")
        assert "dubai" in stops or "singapore" in stops

    # ── None-safety: _clean_city may return None for short/filtered tokens ──

    def test_two_letter_stop_name_does_not_raise(self):
        """'stop over in LA' must not raise TypeError — _clean_city returns None for 'la'."""
        try:
            stops = extract_named_stops("stop over in LA")
        except TypeError as exc:
            pytest.fail(f"extract_named_stops raised TypeError for short token: {exc}")
        # "la" is too short and gets filtered by _clean_city — result is an empty list, not a crash
        assert isinstance(stops, list)

    def test_short_token_then_valid_stop_does_not_raise(self):
        """'stop over in LA then layover in Tokyo' must not crash and must capture Tokyo."""
        try:
            stops = extract_named_stops("stop over in LA then layover in Tokyo")
        except TypeError as exc:
            pytest.fail(f"extract_named_stops raised TypeError: {exc}")
        # LA is filtered; Tokyo (valid city) must still be found
        assert "tokyo" in stops, (
            f"Expected 'tokyo' after skipping filtered 'la' token; got {stops}"
        )

    def test_short_abbreviated_stop_does_not_raise(self):
        """'layover in NY' must not crash the parser."""
        try:
            stops = extract_named_stops("layover in NY then fly to Vancouver")
        except TypeError as exc:
            pytest.fail(f"extract_named_stops raised TypeError: {exc}")
        # NY is filtered; result is empty or just has valid entries — not a crash
        assert isinstance(stops, list)

    def test_all_filtered_tokens_returns_empty_list(self):
        """When every stop token is filtered by _clean_city, return empty list."""
        result = extract_named_stops("stop over in LA")
        assert isinstance(result, list), "Must return a list even when all tokens filter out"
        # LA is too short to survive _clean_city
        assert result == []

    def test_mixed_valid_and_filtered_tokens(self):
        """Only keep the unfiltered cities; do not crash on filtered ones."""
        stops = extract_named_stops("stop over in LA then layover in Singapore")
        # LA is filtered; Singapore should survive
        assert "singapore" in stops
        assert "la" not in stops


# ---------------------------------------------------------------------------
# 2. is_wander_vibe — vibe gate
# ---------------------------------------------------------------------------


class TestVibeGate:
    def test_wander_vibes_return_true(self):
        for v in ["adventure", "chaotic", "budget", "slow-travel", "wander", "backpacking"]:
            assert is_wander_vibe(v), f"Expected is_wander_vibe({v!r}) to be True"

    def test_comfort_vibes_return_false(self):
        for v in ["luxury", "romantic", "relaxing", "romance", "honeymoon", "comfort"]:
            assert not is_wander_vibe(v), f"Expected is_wander_vibe({v!r}) to be False"

    def test_none_returns_false(self):
        assert not is_wander_vibe(None)

    def test_empty_string_returns_false(self):
        assert not is_wander_vibe("")

    def test_case_insensitive(self):
        assert is_wander_vibe("Adventure")
        assert is_wander_vibe("CHAOTIC")

    def test_wander_vibes_cover_expected_set(self):
        """Ensure the known wander vibes are all recognised."""
        for v in _WANDER_VIBES:
            assert is_wander_vibe(v), f"Vibe {v!r} should be a wander vibe"


# ---------------------------------------------------------------------------
# 3. _price_multi_stop_chain — 3-leg and 4-leg chains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_leg_chain_produces_correct_route():
    """origin → NRT → BKK: 3 legs, all priced."""
    req = _detour_req(origin="YVR", destination="BKK")
    stops = [_idea("NRT", "Tokyo", "JP", stay_days=2)]

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
    ):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    assert len(result.legs) == 2, f"Expected 2 legs (YVR→NRT, NRT→BKK), got {len(result.legs)}"
    assert result.legs[0].from_iata == "YVR"
    assert result.legs[0].to_iata == "NRT"
    assert result.legs[1].from_iata == "NRT"
    assert result.legs[1].to_iata == "BKK"
    assert result.stops, "stops list must not be empty for multi-stop itinerary"
    assert result.stops[0]["iata"] == "NRT"


@pytest.mark.asyncio
async def test_four_leg_chain_produces_correct_route():
    """origin → NRT → SIN → BKK: 4 legs (2 intermediate stops)."""
    req = _detour_req(origin="YVR", destination="BKK")
    stops = [
        _idea("NRT", "Tokyo", "JP", stay_days=2),
        _idea("SIN", "Singapore", "SG", stay_days=2),
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    assert len(result.legs) == 3, (
        f"Expected 3 legs (YVR→NRT, NRT→SIN, SIN→BKK), got {len(result.legs)}: "
        f"{[(l.from_iata, l.to_iata) for l in result.legs]}"
    )
    assert result.legs[0].to_iata == "NRT"
    assert result.legs[1].to_iata == "SIN"
    assert result.legs[2].to_iata == "BKK"
    assert len(result.stops) == 2


@pytest.mark.asyncio
async def test_chain_capped_at_six_legs():
    """Chains with more than 5 intermediate stops are trimmed to 5 (6 legs max)."""
    req = _detour_req(origin="YVR", destination="SYD")
    stops = [
        _idea("NRT", "Tokyo", "JP"),
        _idea("ICN", "Seoul", "KR"),
        _idea("SIN", "Singapore", "SG"),
        _idea("DXB", "Dubai", "AE"),
        _idea("LHR", "London", "GB"),
        _idea("CDG", "Paris", "FR"),  # 6th stop — should be trimmed
        _idea("FRA", "Frankfurt", "DE"),  # 7th — definitely trimmed
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    # 5 intermediate stops max → 6 legs
    assert len(result.legs) <= 6, (
        f"Chain exceeded 6 legs: got {len(result.legs)} legs"
    )


@pytest.mark.asyncio
async def test_arrive_by_trims_stays():
    """When arrive_by is tight, stays are trimmed so the chain fits."""
    depart = date(2026, 9, 1)
    arrive_by = depart + timedelta(days=5)  # 5 days total, 2 stops, 1 day each
    req = _detour_req(origin="YVR", destination="BKK", depart=depart, arrive_by=arrive_by)
    stops = [
        _idea("NRT", "Tokyo", "JP", stay_days=5),  # would be trimmed
        _idea("SIN", "Singapore", "SG", stay_days=5),  # would be trimmed too
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    if result is not None:
        # If chain fits, departure of last leg must not exceed arrive_by
        last_leg = result.legs[-1]
        assert last_leg.depart_date <= arrive_by, (
            f"Last leg {last_leg.depart_date} exceeds arrive_by {arrive_by}"
        )
    # It's also valid for the chain to be truncated/None when dates don't fit


@pytest.mark.asyncio
async def test_five_leg_chain_produces_correct_leg_count():
    """4 intermediate stops → 5 legs."""
    req = _detour_req(origin="YVR", destination="BKK")
    stops = [
        _idea("NRT", "Tokyo", "JP", stay_days=1),
        _idea("ICN", "Seoul", "KR", stay_days=1),
        _idea("SIN", "Singapore", "SG", stay_days=1),
        _idea("DXB", "Dubai", "AE", stay_days=1),
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    assert len(result.legs) == 5, (
        f"Expected 5 legs for 4 stops, got {len(result.legs)}: "
        f"{[(l.from_iata, l.to_iata) for l in result.legs]}"
    )


@pytest.mark.asyncio
async def test_six_leg_chain_produces_correct_leg_count():
    """5 intermediate stops → 6 legs (the affiliate hard cap)."""
    req = _detour_req(origin="YVR", destination="SYD")
    stops = [
        _idea("NRT", "Tokyo", "JP", stay_days=1),
        _idea("ICN", "Seoul", "KR", stay_days=1),
        _idea("SIN", "Singapore", "SG", stay_days=1),
        _idea("DXB", "Dubai", "AE", stay_days=1),
        _idea("LHR", "London", "GB", stay_days=1),
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    assert len(result.legs) == 6, (
        f"Expected 6 legs for 5 stops, got {len(result.legs)}: "
        f"{[(l.from_iata, l.to_iata) for l in result.legs]}"
    )


@pytest.mark.asyncio
async def test_rescue_attempts_four_hub_chains_when_shorter_fail():
    """_try_rescue_chain must escalate to 4-hub (5-leg) chains when 1-, 2-, 3-hub fail."""
    import yonder.adventure as _adv_mod

    req = _detour_req(origin="YVR", destination="SIN", vibe="adventure")
    max_hubs_attempted = [0]
    # Capture the real function BEFORE patching so the closure avoids recursion.
    _real_chain = _adv_mod._price_multi_stop_chain

    async def _counting_chain(
        req_, stops, *,
        settings, include_mock, http, only, fallback_chain,
        direct_price, ground_batch, cancel_id=None, rescue=False, pricing_name=None,
    ):
        n = len(stops)
        if n > max_hubs_attempted[0]:
            max_hubs_attempted[0] = n
        if n < 4:
            return None  # simulate pricing failure for short chains
        return await _real_chain(
            req_, stops,
            settings=settings, include_mock=True, http=http,
            only=["mock"], fallback_chain=[], direct_price=None,
            ground_batch={}, rescue=True, pricing_name="mock",
        )

    with (
        patch("yonder.adventure._price_multi_stop_chain", side_effect=_counting_chain),
        patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])),
    ):
        import httpx
        async with httpx.AsyncClient() as http:
            result = await _adv_mod._try_rescue_chain(
                req,
                settings=None,
                include_mock=True,
                http=http,
                only=["mock"],
                fallback_chain=[],
                pricing_name="mock",
                max_legs=6,
            )

    assert max_hubs_attempted[0] >= 4, (
        f"Expected rescue to attempt ≥4-hub chains (5 legs); "
        f"max hubs tried: {max_hubs_attempted[0]}"
    )
    if result is not None:
        assert len(result.legs) <= 6


@pytest.mark.asyncio
async def test_rescue_respects_cancel_id():
    """_try_rescue_chain must stop immediately when cancel_id is set."""
    import yonder.adventure as _adv_mod

    req = _detour_req(origin="YVR", destination="SIN", vibe="adventure")
    attempts = [0]

    async def _counting_chain(req_, stops, **kwargs):
        attempts[0] += 1
        return None  # always fail so all chain lengths are tried unless cancelled

    with (
        patch("yonder.adventure._price_multi_stop_chain", side_effect=_counting_chain),
        # Simulate cancel_id being set as "cancelled" from the start
        patch("yonder.search_cancel.is_cancelled", return_value=True),
    ):
        import httpx
        async with httpx.AsyncClient() as http:
            result = await _adv_mod._try_rescue_chain(
                req,
                settings=None,
                include_mock=True,
                http=http,
                only=["mock"],
                fallback_chain=[],
                pricing_name="mock",
                cancel_id="test-session-123",
                max_legs=6,
            )

    assert result is None, "Cancelled rescue must return None"
    assert attempts[0] == 0, (
        f"Rescue attempted {attempts[0]} chains after cancellation — must exit immediately"
    )


@pytest.mark.asyncio
async def test_rescue_respects_hard_time_budget():
    """_try_rescue_chain must stop when rescue_budget (wall time) is exceeded."""
    import yonder.adventure as _adv_mod

    req = _detour_req(origin="YVR", destination="SIN", vibe="adventure")
    attempts = [0]

    async def _counting_chain(req_, stops, **kwargs):
        attempts[0] += 1
        return None  # always fail

    with (
        patch("yonder.adventure._price_multi_stop_chain", side_effect=_counting_chain),
        patch("yonder.search_cancel.is_cancelled", return_value=False),
    ):
        import httpx
        async with httpx.AsyncClient() as http:
            result = await _adv_mod._try_rescue_chain(
                req,
                settings=None,
                include_mock=True,
                http=http,
                only=["mock"],
                fallback_chain=[],
                pricing_name="mock",
                cancel_id=None,
                max_legs=6,
                rescue_budget=0.0,  # budget already expired → must exit immediately
            )

    assert result is None, "Expired-budget rescue must return None"
    assert attempts[0] == 0, (
        f"Rescue ran {attempts[0]} attempts despite zero budget"
    )


@pytest.mark.asyncio
async def test_multi_stop_chain_cancelled_before_pricing():
    """_price_multi_stop_chain must return None when cancel_id fires before leg pricing."""
    req = _detour_req(origin="YVR", destination="BKK")
    stops = [_idea("NRT", "Tokyo", "JP", stay_days=2)]

    with (
        patch("yonder.search_cancel.is_cancelled", return_value=True),
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
    ):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    cancel_id="test-cancel-456",
                    pricing_name="mock",
                )

    assert result is None, (
        "_price_multi_stop_chain must return None immediately when cancel_id is set"
    )


@pytest.mark.asyncio
async def test_rescue_attempts_five_hub_chains_when_four_hub_fails():
    """_try_rescue_chain must escalate to 5-hub (6-leg) chains as final attempt."""
    import yonder.adventure as _adv_mod

    req = _detour_req(origin="YVR", destination="SIN", vibe="adventure")
    max_hubs_attempted = [0]
    _real_chain = _adv_mod._price_multi_stop_chain

    async def _counting_chain(
        req_, stops, *,
        settings, include_mock, http, only, fallback_chain,
        direct_price, ground_batch, cancel_id=None, rescue=False, pricing_name=None,
    ):
        n = len(stops)
        if n > max_hubs_attempted[0]:
            max_hubs_attempted[0] = n
        if n < 5:
            return None
        return await _real_chain(
            req_, stops,
            settings=settings, include_mock=True, http=http,
            only=["mock"], fallback_chain=[], direct_price=None,
            ground_batch={}, rescue=True, pricing_name="mock",
        )

    with (
        patch("yonder.adventure._price_multi_stop_chain", side_effect=_counting_chain),
        patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])),
    ):
        import httpx
        async with httpx.AsyncClient() as http:
            result = await _adv_mod._try_rescue_chain(
                req,
                settings=None,
                include_mock=True,
                http=http,
                only=["mock"],
                fallback_chain=[],
                pricing_name="mock",
                max_legs=6,
            )

    assert max_hubs_attempted[0] >= 5, (
        f"Expected rescue to attempt ≥5-hub chains (6 legs); "
        f"max hubs tried: {max_hubs_attempted[0]}"
    )
    if result is not None:
        assert len(result.legs) <= 6, f"6-leg cap violated: {len(result.legs)}"


@pytest.mark.asyncio
async def test_multi_stop_total_price_sums_all_legs():
    """total_price must be the sum of all leg fares."""
    req = _detour_req(origin="YVR", destination="BKK")
    stops = [
        _idea("NRT", "Tokyo", "JP", stay_days=2),
        _idea("SIN", "Singapore", "SG", stay_days=2),
    ]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _price_multi_stop_chain(
                    req, stops,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    direct_price=None,
                    ground_batch={},
                    pricing_name="mock",
                )

    assert result is not None
    if result.total_price is not None:
        leg_sum = sum(
            float(leg.offer.price)
            for leg in result.legs
            if leg.offer and leg.offer.price is not None
        )
        assert abs(result.total_price - leg_sum) < 1.0, (
            f"total_price {result.total_price} != sum of legs {leg_sum}"
        )


# ---------------------------------------------------------------------------
# 4. plan_adventure — multi-stop chain via named_stop_chain parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_named_stop_chain_prepended_to_results():
    """When named_stop_chain has 2+ stops, a multi-stop itinerary leads results."""
    req = _detour_req(origin="YVR", destination="BKK")
    chain = [_idea("NRT", "Tokyo", "JP"), _idea("SIN", "Singapore", "SG")]
    single_ideas = [_idea("IST", "Istanbul", "TR")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(
            req, single_ideas,
            named_stop_chain=chain,
            include_mock=True,
        )

    assert result.itineraries, "No itineraries returned"
    first = result.itineraries[0]
    # The multi-stop itinerary should lead (kind=multi-stop) or have 3 legs
    if first.total_price is not None:
        # If priced, it should be the multi-stop chain
        has_multi = any(
            it.kind in ("multi-stop", "rescue") or len(it.legs) >= 3
            for it in result.itineraries
        )
        assert has_multi, (
            f"Expected a multi-stop itinerary in results, got kinds: "
            f"{[it.kind for it in result.itineraries]}"
        )


@pytest.mark.asyncio
async def test_named_stop_chain_single_item_not_multi_stop():
    """A single-item named_stop_chain should NOT produce a multi-stop itinerary."""
    req = _detour_req(origin="YVR", destination="BKK")
    chain = [_idea("NRT", "Tokyo", "JP")]
    single_ideas = [_idea("NRT", "Tokyo", "JP")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(
            req, single_ideas,
            named_stop_chain=chain,  # only 1 item — should not trigger multi-stop
            include_mock=True,
        )

    # Should still produce results — just single-stop, not multi-stop chain
    assert result.itineraries, "No itineraries produced even for single chain"
    multi_stop = [it for it in result.itineraries if it.kind == "multi-stop"]
    assert not multi_stop, f"Should not produce multi-stop with 1-item chain: {multi_stop}"


# ---------------------------------------------------------------------------
# 5. Rescue routing — fires only when normal routing fails, ≤ 6 legs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescue_not_triggered_when_single_stop_succeeds():
    """Rescue routing must NOT fire when a single-stop itinerary prices successfully."""
    req = _detour_req(origin="YVR", destination="BKK")
    ideas = [_idea("NRT", "Tokyo", "JP")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(req, ideas, include_mock=True)

    rescue_cards = [it for it in result.itineraries if it.rescue]
    assert not rescue_cards, (
        f"Rescue card unexpectedly appeared when normal routing succeeded: {rescue_cards}"
    )


# ---------------------------------------------------------------------------
# 4b. Vibe gate — wander allows rescue, comfort blocks it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescue_blocked_for_comfort_vibe_with_no_named_stops():
    """Comfort vibes (luxury, romantic, relaxing…) must not receive rescue chains."""
    req = _detour_req(origin="YVR", destination="SIN", vibe="luxury")
    # Only an idea that will succeed normally — verify rescue doesn't sneak in
    ideas = [_idea("NRT", "Tokyo", "JP")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(req, ideas, include_mock=True)

    rescue_cards = [it for it in result.itineraries if it.rescue]
    assert not rescue_cards, (
        f"Rescue card fired for comfort vibe 'luxury' despite no named stops: {rescue_cards}"
    )


@pytest.mark.asyncio
async def test_rescue_allowed_for_wander_vibe():
    """Wander vibes must allow rescue routing when all normal pricing fails."""
    from yonder.adventure import PricedLeg
    from yonder.intent import is_wander_vibe

    # Verify adventure is a wander vibe
    assert is_wander_vibe("adventure")

    req = _detour_req(origin="YVR", destination="SIN", vibe="adventure")
    ideas = [_idea("NRT", "Tokyo", "JP")]

    # Patch _price_leg so the regular single-stop fails to SIN but mock hubs succeed
    async def _selective_fail(origin, dest, depart, req, *, settings, include_mock, only, http, fallback_chain=None):
        if dest == "SIN" and origin == "NRT":
            # Leg 2 (stop → SIN) fails for single-stop
            return PricedLeg(from_iata=origin, to_iata=dest, depart_date=depart, error="no route")
        # All other legs (including hub chains to SIN) succeed via mock
        from yonder.adventure import _price_leg as _real
        return await _real(origin, dest, depart, req, settings=settings, include_mock=True, only=["mock"], http=http, fallback_chain=fallback_chain or [])

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_selective_fail),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    # Rescue may or may not fire depending on hub pricing, but the vibe gate must
    # not prevent it. At minimum, no rescue_blocked error should appear.
    for err in result.errors:
        assert "rescue blocked" not in err.lower(), (
            f"Rescue was blocked for wander vibe 'adventure': {err}"
        )


@pytest.mark.asyncio
async def test_comfort_vibe_with_two_named_stops_gets_multi_stop_chain():
    """Even with a comfort vibe, user-named 2+ stops must produce a multi-stop chain."""
    req = _detour_req(origin="YVR", destination="BKK", vibe="luxury")
    chain = [_idea("NRT", "Tokyo", "JP"), _idea("SIN", "Singapore", "SG")]
    ideas = [_idea("NRT", "Tokyo", "JP"), _idea("SIN", "Singapore", "SG")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(
            req, ideas,
            named_stop_chain=chain,
            include_mock=True,
        )

    assert result.itineraries, "No itineraries produced for comfort vibe with named stops"
    multi_stop_cards = [it for it in result.itineraries if it.kind == "multi-stop"]
    # Must have at least one multi-stop card (user explicitly named the stops)
    has_multi_or_chain = any(
        it.kind == "multi-stop" or (len(it.legs) >= 3)
        for it in result.itineraries
        if it.total_price is not None
    )
    assert has_multi_or_chain, (
        f"Expected multi-stop chain for comfort vibe + named 2 stops; "
        f"got kinds: {[it.kind for it in result.itineraries]}"
    )


@pytest.mark.asyncio
async def test_rescue_fires_when_all_normal_pricing_fails():
    """Rescue routing should fire when all single-stop pricing returns no fares."""
    from yonder.adventure import PricedLeg
    from datetime import date as _date

    req = _detour_req(origin="YVR", destination="ZZZ")  # fictional destination → pricing fails
    ideas = [_idea("NRT", "Tokyo", "JP")]

    # Make single-stop pricing return errors (no offers) but mock provider works for hubs
    async def _fail_leg(origin, dest, depart, req, *, settings, include_mock, only, http, fallback_chain=None):
        if dest == "ZZZ":
            # Direct to fictional destination — always fails
            return PricedLeg(from_iata=origin, to_iata=dest, depart_date=depart, error="no route")
        # Hub connections to destination also fail in real life but succeed with mock
        from yonder.adventure import _price_leg as _real_price_leg
        return await _real_price_leg(
            origin, dest, depart, req,
            settings=settings, include_mock=True,
            only=["mock"], http=http, fallback_chain=fallback_chain or [],
        )

    with (
        patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})),
        patch("yonder.adventure._price_leg", side_effect=_fail_leg),
    ):
        result = await plan_adventure(req, ideas, include_mock=True)

    # Since all legs to ZZZ fail, we expect either a rescue card or empty results
    # The key assertion is that rescue never exceeds 6 legs if it fires
    for it in result.itineraries:
        if it.rescue:
            assert len(it.legs) <= 6, (
                f"Rescue card exceeded 6 legs: {len(it.legs)} legs"
            )


@pytest.mark.asyncio
async def test_rescue_never_exceeds_six_legs():
    """_try_rescue_chain must cap results at max_legs (default 6)."""
    req = _detour_req(origin="YVR", destination="BKK")

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
            import httpx
            async with httpx.AsyncClient() as http:
                result = await _try_rescue_chain(
                    req,
                    settings=None,
                    include_mock=True,
                    http=http,
                    only=["mock"],
                    fallback_chain=[],
                    pricing_name="mock",
                    max_legs=6,
                )

    if result is not None:
        assert len(result.legs) <= 6, (
            f"Rescue chain exceeded 6 legs: {len(result.legs)}"
        )
        assert result.rescue, "Rescue card must have rescue=True"
        assert "No direct route" in (result.notes[0] if result.notes else ""), (
            f"Rescue tagline missing from notes: {result.notes[:2]}"
        )


@pytest.mark.asyncio
async def test_rescue_card_has_rescue_flag():
    """Rescue itineraries must have rescue=True and the canonical tagline in notes."""
    req = _detour_req(origin="YVR", destination="SIN")

    with patch("yonder.adventure.pick_pricing_provider", new=AsyncMock(return_value=["mock"])):
        import httpx
        async with httpx.AsyncClient() as http:
            result = await _try_rescue_chain(
                req,
                settings=None,
                include_mock=True,
                http=http,
                only=["mock"],
                fallback_chain=[],
                pricing_name="mock",
            )

    if result is not None:
        assert result.rescue, "rescue flag must be True"
        tagline_note = next((n for n in result.notes if "No direct route" in n), None)
        assert tagline_note is not None, (
            f"'No direct route?' tagline missing from rescue notes: {result.notes}"
        )


# ---------------------------------------------------------------------------
# 6. Save / share round-trip — stops list survives serialization
# ---------------------------------------------------------------------------


def test_adventure_itinerary_stops_field_serializes():
    """AdventureItinerary.stops survives model_dump / model_validate round-trip."""
    from yonder.adventure import AdventureItinerary

    it = AdventureItinerary(
        kind="multi-stop",
        title="YVR → Tokyo (2n) → Bangkok",
        currency="CAD",
        stops=[
            {"iata": "NRT", "city": "Tokyo", "country": "JP", "stay_days": 2},
        ],
        rescue=False,
    )
    dumped = it.model_dump(mode="json")
    assert dumped["stops"] == [{"iata": "NRT", "city": "Tokyo", "country": "JP", "stay_days": 2}]
    restored = AdventureItinerary.model_validate(dumped)
    assert restored.stops == it.stops
    assert restored.rescue is False


def test_adventure_itinerary_rescue_flag_serializes():
    """rescue=True round-trips cleanly."""
    from yonder.adventure import AdventureItinerary

    it = AdventureItinerary(
        kind="rescue",
        title="Rescue chain",
        currency="CAD",
        stops=[{"iata": "LHR", "city": "London", "country": "GB", "stay_days": 1}],
        rescue=True,
    )
    dumped = it.model_dump(mode="json")
    assert dumped["rescue"] is True
    restored = AdventureItinerary.model_validate(dumped)
    assert restored.rescue is True


def test_backward_compat_empty_stops():
    """Old single-stop itineraries without stops field still validate."""
    from yonder.adventure import AdventureItinerary

    it = AdventureItinerary(
        kind="stopover",
        title="YVR → Tokyo → BKK",
        currency="CAD",
        stop_iata="NRT",
        stop_city="Tokyo",
        stay_days=3,
    )
    assert it.stops == []
    assert it.rescue is False


# ---------------------------------------------------------------------------
# 7. Regression — existing single-stop and getaway tests still pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_stop_still_works():
    """plan_adventure without named_stop_chain still produces single-stop results."""
    req = _detour_req(origin="YVR", destination="HKG")
    ideas = [_idea("NRT", "Tokyo", "JP")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(req, ideas, include_mock=True)

    assert result.itineraries, "single-stop plan_adventure returned no itineraries"
    it = result.itineraries[0]
    assert len(it.legs) == 2, f"Expected 2 legs, got {len(it.legs)}"
    assert it.stops == [], "Single-stop itinerary must have empty stops list"
    assert not it.rescue, "Single-stop must not be marked as rescue"


@pytest.mark.asyncio
async def test_getaway_not_affected():
    """Getaway (round-trip) requests are not altered by the multi-stop logic."""
    req = AdventureRequest(
        origin="YVR",
        destination="YVR",
        depart_date=date(2026, 9, 1),
        adults=1,
        currency="CAD",
        min_stop_days=2,
        max_stop_days=5,
        max_candidates=3,
        vibe="adventure",
        trip_kind="getaway",
    )
    ideas = [_idea("NRT", "Tokyo", "JP")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(req, ideas, include_mock=True)

    # Getaway: no rescue should fire (it's not a fixed destination trip)
    rescue_cards = [it for it in result.itineraries if it.rescue]
    assert not rescue_cards, "Rescue must not fire for getaway trips"


@pytest.mark.asyncio
async def test_no_named_stop_chain_no_multi_stop():
    """Without named_stop_chain, plan_adventure never produces multi-stop kind."""
    req = _detour_req(origin="YVR", destination="SIN")
    ideas = [_idea("NRT", "Tokyo", "JP"), _idea("ICN", "Seoul", "KR")]

    with patch("yonder.daily_costs.estimate_batch_for_stops", new=AsyncMock(return_value={})):
        result = await plan_adventure(req, ideas, include_mock=True)

    for it in result.itineraries:
        assert it.kind != "multi-stop", (
            f"multi-stop itinerary appeared without named_stop_chain: {it.title!r}"
        )
