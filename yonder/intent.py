"""Intent routing for Explore — pure Escape, pure Detour, or mix.

THE ROUTING MODEL (keep future edits consistent with this):

Three signals decide the shape — WHERE the user is now (origin), WHERE they
want to end up (destination), and whether the prompt implies a detour
(journey/stopover/open-getaway language):

  destination named + direct language ("nonstop", "direct")  → escape
  destination named, plain A→B ("Vancouver to Rome")          → mix
                                       (directs first, stop options offered)
  no destination at all ("somewhere warm", "get me out")      → detour
                                       (open getaway: home → X → home)
  origin + destination + journey phrasing ("leave Vancouver,
  end up in Rome")                                            → detour
                                       (routed: origin → vibe stop → dest)
  origin + destination + explicit stop markers ("via", "with
  a stopover")                                                → mix
  ambiguous (no anchors, no direct/getaway language)          → vibe prior
                                       decides the lean (see below)

Vibe as tie-breaker: explicit prompt signals ALWAYS win. Only when the
prompt is ambiguous does the vibe lean the shape — wander vibes (adventure,
chaotic, budget, slow-travel) lean detour; comfort vibes (luxury, romantic,
relaxing) lean escape. The lean starts from the static prior table below
and is gradually adjusted by the vibe signal store (which shapes users with
that vibe actually save / thumb up), so it is learned over time.

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


# --- Vibe → shape prior (tie-breaker only; explicit prompt signals win) ---
# Static lean: +1 → detour (wander: stopovers are the fun, often cheaper),
# -1 → escape (comfort: arrive, don't connect). Adjusted over time by the
# vibe signal store (what shapes users with this vibe actually save).
_WANDER_VIBES = {
    "adventure", "adventurous", "chaotic", "chaos", "budget", "cheap",
    "slow-travel", "slow travel", "slow_travel", "backpacking", "wander",
}
_COMFORT_VIBES = {
    "luxury", "luxurious", "romantic", "romance", "relaxing", "relax",
    "relaxed", "comfort", "cozy", "honeymoon",
}
# |lean| must reach this before the prior flips an ambiguous prompt
_PRIOR_THRESHOLD = 0.25
# Weight of the learned (signal-store) lean vs the static table
_LEARNED_WEIGHT = 0.5


def _static_vibe_lean(vibe: str | None) -> float:
    v = (vibe or "").strip().lower()
    if not v:
        return 0.0
    if v in _WANDER_VIBES:
        return 1.0
    if v in _COMFORT_VIBES:
        return -1.0
    return 0.0


def vibe_shape_prior(vibe: str | None, *, demo: bool = False) -> Shape | None:
    """Return the shape a vibe leans toward ("detour"/"escape") or None.

    Static prior table blended with the learned lean from the vibe signal
    store (saves/thumbs of that vibe's searches by shape). Only consulted
    for ambiguous prompts — explicit prompt signals always beat this.
    """
    lean = _static_vibe_lean(vibe)
    try:
        from yonder.vibe_signals import shape_lean_for_vibe

        lean += _LEARNED_WEIGHT * shape_lean_for_vibe(vibe, demo=demo)
    except Exception:
        pass
    if lean >= _PRIOR_THRESHOLD:
        return "detour"
    if lean <= -_PRIOR_THRESHOLD:
        return "escape"
    return None


def decide_shape(
    prompt: str,
    *,
    force: str | None = None,
    vibe: str | None = None,
    demo: bool = False,
) -> IntentDecision:
    """Pick pure escape / pure detour / mix from the three-signal model.

    Signals: origin known? destination known? detour implied? (see module
    docstring for the full truth table). *vibe* is only a tie-breaker for
    ambiguous prompts; explicit prompt signals always win.
    """
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

    # --- The three signals ---
    dest_named = looks_like_a_to_b(p)  # origin AND destination extracted
    direct_lang = looks_like_escape_only(p)  # "nonstop", "direct only"…
    open_getaway = looks_like_open_getaway(p)  # no destination in mind
    stop_lang = looks_like_stopover_intent(p)  # "via", "with a stopover"…
    journey_lang = looks_like_journey_phrasing(p)  # "end up in", "arrive in"…

    # Row: destination named + direct language → escape (they know where)
    if direct_lang and dest_named:
        return IntentDecision("escape", 0.92, "direct/one-way language + A→B")
    if direct_lang and not open_getaway:
        return IntentDecision("escape", 0.85, "direct/one-way language")

    # Row: no destination at all → detour (open getaway, home → X → home)
    if open_getaway and not dest_named:
        return IntentDecision("detour", 0.9, "open getaway / somewhere new")
    if open_getaway and stop_lang:
        return IntentDecision("detour", 0.88, "getaway + stop language")

    # Row: origin + destination + explicit stop markers → mix (directs AND
    # the named-stop packages both make sense)
    if dest_named and stop_lang:
        return IntentDecision("mix", 0.8, "A→B with intentional stops")
    # Row: origin + destination + journey phrasing → detour routed
    # origin → vibe stop → destination ("leave Vancouver, end up in Rome")
    if dest_named and journey_lang:
        return IntentDecision("detour", 0.85, "A→B with journey/arrival phrasing")
    # Row: plain A→B → mix so user sees directs first + stop options
    if dest_named:
        return IntentDecision("mix", 0.72, "clear A→B → straight shots + stop options")

    if stop_lang:
        return IntentDecision("detour", 0.7, "stopover language without clear O/D")

    # Row: ambiguous — no anchors, no direct/getaway language. Vibe prior
    # decides the lean; static table adjusted by the signal store.
    prior = vibe_shape_prior(vibe, demo=demo)
    if prior == "detour":
        return IntentDecision("detour", 0.6, f"ambiguous → vibe prior ({vibe}) leans detour")
    if prior == "escape":
        return IntentDecision("escape", 0.6, f"ambiguous → vibe prior ({vibe}) leans escape")
    return IntentDecision("mix", 0.55, "ambiguous → try both shapes under budget")


def mix_candidate_cap(shape: Shape, settings_cap: int) -> int:
    """When mixing, price fewer detour cities to protect the 30s budget."""
    cap = max(2, min(5, int(settings_cap or 5)))
    if shape == "mix":
        return min(3, cap)
    return cap
