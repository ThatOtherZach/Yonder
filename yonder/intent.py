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


# Departure + arrival phrasing ("leave X ... be in Y") — not just "X to Y"
_DEPART_PHRASE = re.compile(
    r"\b(?:leave|leaving|depart(?:ing)?(?:\s+from)?|fly(?:ing)?\s+(?:out\s+of|from)|out\s+of|from)\s+"
    r"([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z.'-]+){0,2})",
    re.I,
)
_ARRIVE_PHRASE = re.compile(
    r"\b(?:end(?:\s+up)?\s+in|be\s+in|arrive\s+(?:in|at)|arriving\s+(?:in|at)|"
    r"land(?:ing)?\s+in|get(?:ting)?\s+to|finish(?:ing)?\s+in|wind\s+up\s+in|make\s+it\s+to)\s+"
    r"([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z.'-]+){0,2})",
    re.I,
)
_CITY_STOPWORDS = {
    "and", "after", "before", "by", "on", "in", "for", "with", "then", "but",
    "the", "a", "an", "to", "via", "this", "next", "would", "want", "around",
    "sometime", "eventually", "town", "again", "soon",
    # verbs that leak in from loose "X to Y" matches
    "leave", "leaving", "depart", "departing", "get", "getting", "go", "going",
    "fly", "flying", "head", "heading", "travel", "traveling", "travelling",
}
_OPEN_PLACE_WORDS = {
    "somewhere", "anywhere", "wherever", "nowhere", "there", "here", "home",
    "out of town", "get out", "town", "bed", "work",
}


def _clean_city(raw: str) -> str | None:
    words: list[str] = []
    for w in (raw or "").strip().split():
        lw = w.strip(".,;:!?'\"").lower()
        if lw in _CITY_STOPWORDS:
            break
        words.append(lw)
    city = " ".join(words).strip(" .,'-")
    if not city or len(city) < 3 or city in _OPEN_PLACE_WORDS:
        return None
    if words and words[0] in _OPEN_PLACE_WORDS:
        return None
    return city


def extract_route_cities(prompt: str) -> tuple[str, str] | None:
    """Return (origin, destination) lowercase city/IATA tokens when the prompt
    clearly names a two-city trip; None otherwise."""
    p = (prompt or "").strip()
    if not p:
        return None
    m = _IATA_PAIR.search(p)
    if m:
        return m.group(1).lower(), m.group(2).lower()
    m = _CITY_TO_CITY.search(p)
    if m:
        a, b = _clean_city(m.group(1)), _clean_city(m.group(2))
        if a and b and a != b:
            return a, b
    dm = _DEPART_PHRASE.search(p)
    am = _ARRIVE_PHRASE.search(p)
    if dm and am:
        a, b = _clean_city(dm.group(1)), _clean_city(am.group(1))
        if a and b and a != b:
            return a, b
    return None


def looks_like_a_to_b(prompt: str) -> bool:
    return extract_route_cities(prompt) is not None


# Stricter than _ARRIVE_PHRASE: excludes broad "get to"/"be in" so plain
# point-to-point asks ("how do I get to Rome from Vancouver") stay mix.
_JOURNEY_ARRIVE_PHRASE = re.compile(
    r"\b(?:end(?:\s+up)?\s+in|arrive\s+(?:in|at)|arriving\s+(?:in|at)|"
    r"land(?:ing)?\s+in|finish(?:ing)?\s+in|wind\s+up\s+in|make\s+it\s+to)\s+"
    r"([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z.'-]+){0,2})",
    re.I,
)


def looks_like_journey_phrasing(prompt: str) -> bool:
    """True when the prompt uses strong arrival phrasing ("end up in",
    "arrive in", "wind up in"…) with a real place — journey language, not a
    plain A→B ticket ask. These travelers expect a routed trip with a stop."""
    m = _JOURNEY_ARRIVE_PHRASE.search((prompt or "").strip())
    return bool(m and _clean_city(m.group(1)))


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
    if looks_like_a_to_b(p) and looks_like_journey_phrasing(p):
        # "leave Vancouver, end up in Rome" — journey phrasing means they
        # want the routed trip (origin → vibe stop → destination), not a
        # plain round trip.
        return IntentDecision("detour", 0.85, "A→B with journey/arrival phrasing")
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
