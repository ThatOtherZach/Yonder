"""Paraphrase regression suite for intent routing.

Each entry is a (prompt, expected_shape, expected_stop_lang) triple where:
  - expected_shape:    what decide_shape() should return
  - expected_stop_lang: whether looks_like_stopover_intent() should be True

Add a new row whenever a real-world prompt is misclassified. Run with::

    pytest tests/test_intent_phrases.py -v

"""
from __future__ import annotations

import pytest

from yonder.intent import decide_shape, looks_like_stopover_intent

# ---------------------------------------------------------------------------
# Paraphrase cases  (prompt, expected_shape, expected_stop_lang)
# ---------------------------------------------------------------------------
CASES: list[tuple[str, str, bool]] = [
    # ── Explicit stop-off route: _STOP_OFF_ROUTE pattern → detour ──────────
    #    These use "stop[ping] [off] in X then [go|head|fly|travel] to Y"
    ("stop off in Tokyo then go to Hong Kong", "detour", True),
    ("stopping off in Berlin then head to Paris", "detour", True),
    ("stop off in Dubai then travel to Mumbai", "detour", True),
    ("stop in Lisbon then fly to Madrid", "detour", False),      # regex matches even without "off"
    ("stop off in Rome then go to Athens", "detour", True),

    # ── Stop markers WITHOUT a clear A→B city pair → detour ─────────────────
    #    stop_lang=True but no extractable destination → stop_lang branch fires
    ("passing through Singapore", "detour", True),
    ("pop into Paris for a few days", "detour", True),
    ("with a night in Amsterdam", "detour", True),
    ("make a quick stop in Dubai", "detour", True),
    ("swing through a few cities in Southeast Asia", "detour", True),
    # "hop over to X" – regex extracts ("hop over", city) as an A→B pair, so
    # dest_named=True fires the mix branch (A→B + stop markers → mix).
    ("hop over to Reykjavik for a couple days", "mix", True),
    ("drop into Lisbon before heading home", "detour", True),

    # ── Stop markers WITH a clear A→B pair → mix ────────────────────────────
    #    dest_named=True + stop_lang=True → "A→B with intentional stops"
    ("Vancouver to Rome via Lisbon", "mix", True),
    ("from Toronto to Paris with a stopover", "mix", True),
    ("swing through Bangkok then fly to Singapore", "mix", True),
    # "on the way" can trigger looks_like_open_getaway; use a cleaner A→B + layover prompt
    ("Vancouver to Singapore with a layover", "mix", True),

    # ── Plain A→B, no stop language → mix ───────────────────────────────────
    ("Vancouver to Rome", "mix", False),
    ("YVR to NRT", "mix", False),
    ("flights from London to New York", "mix", False),

    # ── Open getaway (no named destination) → detour ─────────────────────────
    ("get me out of Vancouver, somewhere new", "detour", False),
    ("cheap escape somewhere I haven't been", "detour", False),

    # ── Direct / nonstop language → escape ──────────────────────────────────
    ("nonstop Vancouver to Tokyo", "escape", False),
    ("direct flight from Toronto to Paris", "escape", False),
    ("cheapest direct YVR to LHR", "escape", False),
    ("one way only from Seattle to Bangkok", "escape", False),
    ("straight shot Vancouver to New York", "escape", False),
]


@pytest.mark.parametrize("prompt,expected_shape,expected_stop_lang", CASES)
def test_decide_shape(prompt: str, expected_shape: str, expected_stop_lang: bool) -> None:
    """decide_shape() returns the expected shape for every registered phrase."""
    decision = decide_shape(prompt, demo=True)
    assert decision.shape == expected_shape, (
        f"prompt={prompt!r} → shape={decision.shape!r} "
        f"(want {expected_shape!r}); rationale={decision.rationale!r}"
    )


@pytest.mark.parametrize("prompt,expected_shape,expected_stop_lang", CASES)
def test_stopover_intent_flag(
    prompt: str, expected_shape: str, expected_stop_lang: bool
) -> None:
    """looks_like_stopover_intent() matches the expected stop-language flag."""
    got = looks_like_stopover_intent(prompt)
    assert got == expected_stop_lang, (
        f"prompt={prompt!r} → stop_lang={got} (want {expected_stop_lang})"
    )


# ---------------------------------------------------------------------------
# Targeted sanity checks (not parameterised — easier to read in failure output)
# ---------------------------------------------------------------------------

class TestStopOffRouteIsAlwaysDetour:
    """All 'stop off in X then go to Y' phrasings must produce shape=detour."""

    PHRASINGS = [
        "stop off in Tokyo then go to Hong Kong",
        "stop off in Tokyo and then go to Hong Kong, looking for party clubs in Tokyo",
        "stopping off in Berlin then head to Paris",
        "stop in Lisbon then fly to Madrid",
        "stop off in Dubai then travel to Mumbai",
    ]

    def test_all_phrasings_are_detour(self) -> None:
        for p in self.PHRASINGS:
            d = decide_shape(p, demo=True)
            assert d.shape == "detour", f"prompt={p!r} got {d.shape!r}"


class TestNewStopMarkersCovered:
    """Newly added stop markers are picked up by looks_like_stopover_intent."""

    MARKED = [
        "pop into Lisbon",
        "passing through Singapore",
        "with a night in Amsterdam",
        "make a quick stop in Dubai",
        "hop over to Reykjavik",
        "drop into Lisbon",
    ]

    def test_markers_detected(self) -> None:
        for phrase in self.MARKED:
            assert looks_like_stopover_intent(phrase), (
                f"Expected stopover marker detected in: {phrase!r}"
            )


class TestDecideShapeNeverCrashes:
    """decide_shape must not raise for edge-case inputs."""

    def test_empty_string(self) -> None:
        d = decide_shape("")
        assert d.shape in ("escape", "detour", "mix")

    def test_very_long_prompt(self) -> None:
        long = "stop off in Tokyo then go to Hong Kong " * 50
        d = decide_shape(long, demo=True)
        assert d.shape in ("escape", "detour", "mix")

    def test_unicode_prompt(self) -> None:
        d = decide_shape("東京 to 香港 via 上海", demo=True)
        assert d.shape in ("escape", "detour", "mix")

    def test_numbers_only(self) -> None:
        d = decide_shape("123 456 789")
        assert d.shape in ("escape", "detour", "mix")
