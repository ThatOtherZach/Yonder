"""Smoke tests for has_proximity_intent phrase detection."""

import pytest

from yonder.intent import has_proximity_intent


_PROXIMITY_TRUE = [
    "not too far, art museums",
    "somewhere not too far from home",
    "not far, just a weekend",
    "I want somewhere nearby",
    "close to home please",
    "close by would be great",
    "short flight only",
    "looking for a short trip",
    "quick trip this weekend",
    "easy flight preferred",
    "within a few hours of flying",
    "NOT TOO FAR — warm beach",   # uppercase variant
]

_PROXIMITY_FALSE = [
    "art museums in a warm city",
    "I want to go to Tokyo",
    "cheap food vibes somewhere new",
    "adventure in South America",
    "budget beach holiday",
    "I haven't been to Europe yet",
    "fly me somewhere chaotic",
    "long weekend in the mountains",
]


class TestHasProximityIntent:
    @pytest.mark.parametrize("query", _PROXIMITY_TRUE)
    def test_returns_true_for_proximity_phrases(self, query: str) -> None:
        assert has_proximity_intent(query) is True, repr(query)

    @pytest.mark.parametrize("query", _PROXIMITY_FALSE)
    def test_returns_false_for_neutral_queries(self, query: str) -> None:
        assert has_proximity_intent(query) is False, repr(query)

    def test_empty_string_is_false(self) -> None:
        assert has_proximity_intent("") is False

    def test_none_equivalent_empty(self) -> None:
        # The function coerces None-like falsy to empty string via `or ""`
        assert has_proximity_intent("") is False
