"""Tests for 'stop over in X' phrasing recognition and named-stop pinning.

Covers task-spec requirements:
  1. extract_named_stop — recognises all stop-over / stopover / layover phrasings
     plus negatives.
  2. decide_shape — "Vancouver to Bangkok, stop over in Tokyo" → detour.
  3. Named-stop pinning logic (the candidate-list mutation performed by the
     detour path in web.py):
       a. Tokyo absent from Grok candidates → inserted as first candidate.
       b. Tokyo already in Grok candidates → no duplicate.
       c. Named stop equals the destination → candidate NOT inserted.

Run with::

    pytest tests/test_stopover_phrasing_and_pinning.py -v

"""
from __future__ import annotations

import pytest

from yonder.adventure import AdventureRequest, StopoverIdea
from yonder.airports import city_country_for_iata, iata_for_city
from yonder.intent import decide_shape, extract_named_stop, extract_named_stops


# ---------------------------------------------------------------------------
# Helpers — mirror the insertion logic from web.py _do_detour so we can test
# it in isolation without standing up the full web handler.
# ---------------------------------------------------------------------------


def _pin_named_stops(
    prompt: str,
    ideas: list[StopoverIdea],
    origin_iata: str,
    dest_iata: str,
    *,
    min_stop: int = 1,
    max_stop: int = 5,
    vibe: str | None = None,
) -> list[StopoverIdea]:
    """Insert any user-named stops as leading candidates.

    Mirrors the logic in web.py ``_do_detour`` exactly so we can test it
    without starting the full request pipeline.
    """
    pin_cities = extract_named_stops(prompt)
    if not pin_cities:
        return ideas

    orig = (origin_iata or "").upper()
    dest = (dest_iata or "").upper()
    result = list(ideas)
    insert_pos = 0

    for pin_city in pin_cities:
        pin_raw = pin_city.upper()
        pin_iata = (
            pin_raw
            if (len(pin_raw) == 3 and pin_raw.isalpha())
            else iata_for_city(pin_city)
        )
        if not pin_iata or pin_iata in (orig, dest):
            continue
        cc = city_country_for_iata(pin_iata)
        idea = StopoverIdea(
            iata=pin_iata,
            city=(cc[0] if cc else pin_city.title()),
            country=(cc[1] if cc else None),
            stay_days=max(min_stop, min(max_stop, 3)),
            why="You asked to stop here",
            vibe_tags=[vibe] if vibe else [],
            source="user",
        )
        if not any((i.iata or "").upper() == pin_iata for i in result):
            result.insert(insert_pos, idea)
            insert_pos += 1

    return result


# ---------------------------------------------------------------------------
# 1.  extract_named_stop — phrasing recognition
# ---------------------------------------------------------------------------


class TestExtractNamedStopPhrasings:
    """extract_named_stop must recognise all stop-over / stopover / layover forms."""

    def test_stop_over_in(self):
        assert extract_named_stop("stop over in Tokyo") == "tokyo"

    def test_stopover_in(self):
        assert extract_named_stop("stopover in Tokyo") == "tokyo"

    def test_layover_in(self):
        assert extract_named_stop("layover in Tokyo") == "tokyo"

    def test_stopping_over_in(self):
        assert extract_named_stop("stopping over in Hong Kong") == "hong kong"

    def test_stop_over_in_multiword(self):
        assert extract_named_stop("I'd love to stop over in Kuala Lumpur") == "kuala lumpur"

    def test_stop_off_in(self):
        assert extract_named_stop("stop off in Singapore") == "singapore"

    def test_stopping_off_in(self):
        assert extract_named_stop("stopping off in Berlin") == "berlin"

    # --- negatives ---

    def test_no_stops_please_returns_none(self):
        assert extract_named_stop("no stops please") is None

    def test_nonstop_returns_none(self):
        assert extract_named_stop("nonstop flight to Tokyo") is None

    def test_plain_a_to_b_returns_none(self):
        assert extract_named_stop("Vancouver to Bangkok") is None

    def test_empty_returns_none(self):
        assert extract_named_stop("") is None

    def test_none_input_returns_none(self):
        assert extract_named_stop(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2.  decide_shape — "Vancouver to Bangkok, stop over in Tokyo" → detour
# ---------------------------------------------------------------------------


class TestDecideShapeStopOverPhrasing:
    """Prompts with 'stop over in X' must always route to detour."""

    def test_vancouver_to_bangkok_stop_over_tokyo(self):
        d = decide_shape("Vancouver to Bangkok, stop over in Tokyo", demo=True)
        assert d.shape == "detour", (
            f"Expected detour, got {d.shape!r} (rationale: {d.rationale!r})"
        )

    def test_stop_mentioned_after_destination(self):
        """Stop city appearing AFTER the destination must still produce detour."""
        d = decide_shape("fly Vancouver to Bangkok stopping over in Tokyo", demo=True)
        assert d.shape == "detour", (
            f"Expected detour, got {d.shape!r} (rationale: {d.rationale!r})"
        )

    def test_stopover_phrasing(self):
        d = decide_shape("Vancouver to Bangkok with a stopover in Tokyo", demo=True)
        assert d.shape in ("detour", "mix"), (
            f"Expected detour or mix, got {d.shape!r}"
        )

    def test_layover_phrasing_produces_detour(self):
        d = decide_shape("layover in Tokyo then go to Hong Kong", demo=True)
        assert d.shape == "detour", (
            f"Expected detour, got {d.shape!r} (rationale: {d.rationale!r})"
        )

    def test_stopping_over_phrasing(self):
        d = decide_shape("fly from London to Sydney stopping over in Singapore", demo=True)
        assert d.shape == "detour", (
            f"Expected detour, got {d.shape!r} (rationale: {d.rationale!r})"
        )


# ---------------------------------------------------------------------------
# 3.  Named-stop pinning logic
# ---------------------------------------------------------------------------


def _make_idea(iata: str, city: str, country: str = "TH") -> StopoverIdea:
    return StopoverIdea(
        iata=iata,
        city=city,
        country=country,
        stay_days=3,
        why="test candidate",
        vibe_tags=["city"],
        source="grok",
    )


class TestNamedStopPinning:
    """The pinning helper inserts user-named stops at the front of candidates."""

    def test_tokyo_inserted_first_when_missing(self):
        """When Grok candidates do not include Tokyo, it must be pinned first."""
        candidates = [
            _make_idea("BKK", "Bangkok"),
            _make_idea("SGN", "Ho Chi Minh City", "VN"),
        ]
        result = _pin_named_stops(
            "Vancouver to Bangkok, stop over in Tokyo",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        iatas = [i.iata for i in result]
        assert "NRT" in iatas, f"Tokyo (NRT) not in candidates after pinning: {iatas}"
        assert iatas.index("NRT") == 0, (
            f"Tokyo (NRT) must be first candidate; got order: {iatas}"
        )

    def test_pinned_stop_source_is_user(self):
        """Pinned stops must carry source='user' so the UI can distinguish them."""
        candidates = [_make_idea("BKK", "Bangkok")]
        result = _pin_named_stops(
            "stop over in Tokyo",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        pinned = next((i for i in result if (i.iata or "").upper() == "NRT"), None)
        assert pinned is not None, "NRT not found after pinning"
        assert pinned.source == "user", (
            f"Pinned stop source must be 'user', got {pinned.source!r}"
        )

    def test_pinned_stop_why_text(self):
        """Pinned stops must carry the correct 'why' text."""
        candidates = [_make_idea("BKK", "Bangkok")]
        result = _pin_named_stops(
            "stop over in Tokyo",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        pinned = next((i for i in result if (i.iata or "").upper() == "NRT"), None)
        assert pinned is not None
        assert pinned.why == "You asked to stop here"

    def test_no_duplicate_when_grok_already_returned_tokyo(self):
        """When Grok candidates already include NRT, pinning must not add a second copy."""
        candidates = [
            _make_idea("NRT", "Tokyo", "JP"),
            _make_idea("BKK", "Bangkok"),
        ]
        result = _pin_named_stops(
            "Vancouver to Bangkok, stop over in Tokyo",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        tokyo_count = sum(1 for i in result if (i.iata or "").upper() == "NRT")
        assert tokyo_count == 1, (
            f"NRT must appear exactly once; found {tokyo_count} times in {[i.iata for i in result]}"
        )

    def test_skip_when_named_stop_equals_destination(self):
        """If the named stop equals the destination, it must not be inserted."""
        candidates = [
            _make_idea("BKK", "Bangkok"),
            _make_idea("SGN", "Ho Chi Minh City", "VN"),
        ]
        result = _pin_named_stops(
            "fly to Bangkok, stop over in Bangkok",  # stop == destination
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        # BKK should appear exactly once (as a normal candidate, not pinned again)
        bkk_count = sum(1 for i in result if (i.iata or "").upper() == "BKK")
        assert bkk_count == 1, (
            f"BKK (destination) must not be duplicated; count={bkk_count}"
        )
        # Also verify it wasn't inserted at front with source='user'
        pinned_bkk = [i for i in result if (i.iata or "").upper() == "BKK" and i.source == "user"]
        assert not pinned_bkk, "Destination must not be pinned as a user stop"

    def test_skip_when_named_stop_equals_origin(self):
        """If the named stop equals the origin, it must not be inserted."""
        candidates = [_make_idea("BKK", "Bangkok")]
        result = _pin_named_stops(
            "stop over in Vancouver then go to Bangkok",
            candidates,
            origin_iata="YVR",  # Vancouver → YVR
            dest_iata="BKK",
        )
        # YVR (Vancouver) must not be in results as a pinned stop
        yvr_stops = [i for i in result if (i.iata or "").upper() == "YVR"]
        assert not yvr_stops, f"Origin must not be pinned; got {[i.iata for i in result]}"

    def test_no_stop_phrase_leaves_candidates_unchanged(self):
        """Prompts without stop language must not alter the candidate list."""
        candidates = [_make_idea("BKK", "Bangkok"), _make_idea("SGN", "Saigon", "VN")]
        result = _pin_named_stops(
            "Vancouver to Bangkok",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        assert [i.iata for i in result] == ["BKK", "SGN"]

    def test_tail_candidates_trimmed_to_fit_named_stop_first(self):
        """Named stop is added at front; total list length grows by one (not capped here —
        the cap is applied later by plan_adventure; the pinning helper just prepends)."""
        candidates = [
            _make_idea("BKK", "Bangkok"),
            _make_idea("HAN", "Hanoi", "VN"),
            _make_idea("SGN", "Ho Chi Minh City", "VN"),
        ]
        result = _pin_named_stops(
            "stop over in Tokyo",
            candidates,
            origin_iata="YVR",
            dest_iata="BKK",
        )
        # Tokyo is prepended; original three are still there
        iatas = [i.iata for i in result]
        assert iatas[0] == "NRT", f"NRT must be first; got {iatas}"
        assert len(result) == 4


# ---------------------------------------------------------------------------
# 4.  Regression — existing stop-over tests still pass (smoke check)
# ---------------------------------------------------------------------------


class TestExistingStopOverRegression:
    """Verify the pre-existing test cases still hold after task changes."""

    def test_stop_off_in_x_then_go_to_y_is_detour(self):
        d = decide_shape("stop off in Tokyo then go to Hong Kong", demo=True)
        assert d.shape == "detour"

    def test_stopping_off_in_x_then_head_to_y_is_detour(self):
        d = decide_shape("stopping off in Berlin then head to Paris", demo=True)
        assert d.shape == "detour"

    def test_extract_named_stops_stop_over_returns_list(self):
        stops = extract_named_stops("stop over in Tokyo")
        assert "tokyo" in stops

    def test_extract_named_stops_multiple(self):
        stops = extract_named_stops("stop over in Tokyo then layover in Singapore")
        assert "tokyo" in stops and "singapore" in stops
