"""Intent routing for Explore — pure Escape, pure Detour, or mix.

Cheap heuristics first; no durable logging here (Save-only learning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from yonder.grok import looks_like_open_getaway

Shape = Literal["escape", "detour", "mix"]


@dataclass
class IntentDecision:
    shape: Shape
    confidence: float
    rationale: str
    forced: bool = False


_IATA_PAIR = re.compile(
    r"\b([A-Za-z]{3})\s*(?:→|->|to)\s*([A-Za-z]{3})\b",
    re.I,
)
_CITY_TO_CITY = re.compile(
    r"\b(?:from\s+)?([A-Za-z][A-Za-z\s.'-]{1,28}?)\s+to\s+([A-Za-z][A-Za-z\s.'-]{1,28})\b",
    re.I,
)
_STOP_MARKERS = (
    "via ",
    " stopover",
    " stop overs",
    " multi-day",
    " multiday",
    " few days",
    " couple days",
    " layover",
    " with a stop",
    " with stops",
    " intentional stop",
)
_ESCAPE_ONLY = (
    "nonstop",
    "non-stop",
    "non stop",
    "direct only",
    "direct flight",
    "cheapest direct",
    "one way only",
    "one-way only",
    "oneway only",
    "straight shot",
    "no stop",
    "no stops",
)


def looks_like_a_to_b(prompt: str) -> bool:
    p = (prompt or "").strip()
    if not p:
        return False
    if _IATA_PAIR.search(p):
        return True
    m = _CITY_TO_CITY.search(p)
    if not m:
        return False
    a, b = m.group(1).strip().lower(), m.group(2).strip().lower()
    # Reject open phrases mistaken for cities
    reject = {"somewhere", "anywhere", "wherever", "out of town", "get out"}
    if a in reject or b in reject:
        return False
    return len(a) >= 3 and len(b) >= 3


def looks_like_stopover_intent(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(m in p for m in _STOP_MARKERS)


def looks_like_escape_only(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(m in p for m in _ESCAPE_ONLY)


def decide_shape(
    prompt: str,
    *,
    force: str | None = None,
) -> IntentDecision:
    """Pick pure escape / pure detour / mix from free text (and optional force)."""
    f = (force or "").strip().lower()
    if f in ("escape", "detour", "mix"):
        return IntentDecision(
            shape=f,  # type: ignore[arg-type]
            confidence=1.0,
            rationale=f"forced:{f}",
            forced=True,
        )

    p = (prompt or "").strip()
    if not p:
        return IntentDecision("escape", 0.2, "empty prompt → escape default")

    if looks_like_escape_only(p) and looks_like_a_to_b(p):
        return IntentDecision("escape", 0.92, "direct/one-way language + A→B")
    if looks_like_escape_only(p) and not looks_like_open_getaway(p):
        return IntentDecision("escape", 0.85, "direct/one-way language")

    if looks_like_open_getaway(p) and not looks_like_a_to_b(p):
        return IntentDecision("detour", 0.9, "open getaway / somewhere new")
    if looks_like_open_getaway(p) and looks_like_stopover_intent(p):
        return IntentDecision("detour", 0.88, "getaway + stop language")

    if looks_like_a_to_b(p) and looks_like_stopover_intent(p):
        return IntentDecision("mix", 0.8, "A→B with intentional stops")
    if looks_like_a_to_b(p):
        # Clear route: mix so user can see directs + optional stop packages
        return IntentDecision("mix", 0.72, "clear A→B → straight shots + stop options")

    if looks_like_stopover_intent(p):
        return IntentDecision("detour", 0.7, "stopover language without clear O/D")

    # Ambiguous vibes / COL / food without anchors
    return IntentDecision("mix", 0.55, "ambiguous → try both shapes under budget")


def mix_candidate_cap(shape: Shape, settings_cap: int) -> int:
    """When mixing, price fewer detour cities to protect the 30s budget."""
    cap = max(2, min(5, int(settings_cap or 5)))
    if shape == "mix":
        return min(3, cap)
    return cap
