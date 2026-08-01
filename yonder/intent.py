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
    "stop off",
    "stopping off",
    "stop over in",
    "stop-over",
    "make a stop",
    "make a quick stop",
    "pass through",
    "passing through",
    "swing through",
    "swing by",
    "pop into",
    "with a night in",
    "hop over to",
    "drop into",
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
_STOP_OFF_ROUTE = re.compile(
    r"\bstopp?(?:ing)?\s+(?:(?:off|over)\s+)?(?:in|at)\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,28}?)"
    r"\s*(?:and\s+)?then\s+(?:go|head|fly|travel)\s+to\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,28})\b",
    re.I,
)
# Matches an explicitly named mid-route stop without requiring a "then go to Y" tail:
# "stop over in Tokyo", "stopover in Tokyo", "stop off in Berlin",
# "stopping over in Hong Kong", "layover in Singapore"
_NAMED_STOP = re.compile(
    r"\b(?:"
    r"stopp?(?:ing)?\s+(?:off|over)\s+(?:in|at)|"
    r"stop-?over\s+(?:in|at)|"
    r"layover\s+(?:in|at)"
    r")\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,28})\b",
    re.I,
)
# Matches "swing by/through X then go to Y", "pass through X on the way to Y", etc.
_SWING_PASS_ROUTE = re.compile(
    r"\b(?:swing\s+(?:by|through)|pass\s+through|passing\s+through)\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,28}?)"
    r"\s*(?:(?:and\s+)?then\s+(?:go|head|fly|travel)\s+to"
    r"|on\s+(?:(?:my|our|the)\s+)?way\s+to)\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,28})\b",
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
    # "stop off in X then go to Y" — stopover city + final destination
    m = _STOP_OFF_ROUTE.search(p) or _SWING_PASS_ROUTE.search(p)
    if m:
        a, b = _clean_city(m.group(1)), _clean_city(m.group(2))
        if a and b and a != b:
            return a, b
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


def extract_named_stop(prompt: str) -> str | None:
    """Return the city the user explicitly named as a mid-route stop, or None.

    Recognises 'stop over in X', 'stopover in X', 'stop off in X',
    'stopping over in X', 'layover in X' — even when no 'then go to Y' tail
    is present and regardless of word order in the sentence.
    """
    m = _NAMED_STOP.search((prompt or "").strip())
    return _clean_city(m.group(1)) if m else None


# Matches "stopping in X and Y" / "stopping over in X and Y" / "via X and Y"
# Used for multi-stop extraction when user names two consecutive stops.
_STOP_IN_MULTI = re.compile(
    r"\b(?:"
    r"stopp?(?:ing)?\s+(?:(?:off|over)\s+)?in|"
    r"stop\s+in|"
    r"via\s+(?:city\s+of\s+)?"
    r")\s*"
    r"([A-Za-z][A-Za-z\s.'-]{1,28}?)"
    r"\s*(?:,\s*(?:and\s+)?|\band\s+)"
    r"([A-Za-z][A-Za-z\s.'-]{1,28})\b",
    re.I,
)


def extract_named_stops(prompt: str) -> list[str]:
    """Return an ordered list of cities explicitly named as mid-route stops.

    Handles:
    - "stopping in Tokyo and Hong Kong" / "stopping over in X and Y"
    - "via X and Y"
    - Multiple separate stop markers: "stop over in X … layover in Y"
    - "stop off in X then go to Y" (X only; Y is the destination)

    Returns empty list when no named stops found.
    Returns a single-element list when one stop is found (same as
    extract_named_stop but in list form).
    """
    p = (prompt or "").strip()
    if not p:
        return []

    stops: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        c = _clean_city(raw)
        if c and c not in seen:
            stops.append(c)
            seen.add(c)

    # Pattern: "stopping in X and Y", "via X and Y" — two stops in one phrase
    for m in _STOP_IN_MULTI.finditer(p):
        _add(m.group(1))
        _add(m.group(2))

    # Individual stop markers (may span multi-word city names).
    # Search iteratively rather than using finditer: the _NAMED_STOP regex is
    # greedy and its captured group includes spaces, so a single match can
    # consume "Tokyo then layover in Singapore" as one big city name.  We
    # advance past the CLEANED city (which _clean_city() already truncates at
    # stop-words like "then" / "and") so the next marker is not swallowed.
    pos = 0
    while pos < len(p):
        m = _NAMED_STOP.search(p, pos)
        if not m:
            break
        raw_city = m.group(1)
        c = _clean_city(raw_city)
        if c and c not in seen:
            stops.append(c)
            seen.add(c)
        # Advance past where the cleaned city actually ends in the source text.
        # - When c is truthy: advance by len(c) chars from the group start so
        #   multi-word names (e.g. "Hong Kong" = 9 chars) are handled correctly
        #   and subsequent markers in the same string are not swallowed.
        # - When c is None / falsy (e.g. "stop over in LA" where _clean_city
        #   filters the two-letter token): advance past only the first word of
        #   the captured group.  Advancing past m.end() would swallow any valid
        #   markers that follow (e.g. "layover in Tokyo" after "stop over in LA").
        city_start = m.start(1)
        if c:
            pos = city_start + len(c)
        else:
            # Skip just the first word of the uncleanable token; anything
            # that follows (including the next stop marker) stays reachable.
            first_word = (raw_city or "").split()[0] if raw_city else ""
            pos = city_start + max(1, len(first_word))

    return stops


def is_wander_vibe(vibe: str | None) -> bool:
    """Return True when the vibe is a wander/adventure style that allows multi-hop itineraries.

    Wander vibes (adventure, chaotic, budget, slow-travel, etc.) are willing to
    chain multiple hops — comfort vibes (luxury, romantic, relaxing) prefer
    clean direct routes and should stay single-stop/direct.
    """
    return (vibe or "").strip().lower() in _WANDER_VIBES


def looks_like_stop_off_route(prompt: str) -> bool:
    """True when the prompt uses an explicit 'stop off in X then go to Y' pattern,
    or a swing-by / pass-through variant.

    Detects constructions like:
      "stop off in Tokyo then go to Hong Kong"
      "stopping off in Berlin then head to Paris"
      "stop in Lisbon then fly to Madrid"
      "swing by Tokyo then go to Hong Kong"
      "swing through Berlin on the way to Paris"
      "pass through Singapore then head to Bangkok"
    These are routed detours (origin → X → Y), not ambiguous mix results.
    """
    p = (prompt or "").strip()
    return bool(_STOP_OFF_ROUTE.search(p) or _SWING_PASS_ROUTE.search(p))


def looks_like_escape_only(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(m in p for m in _ESCAPE_ONLY)


# --- Proximity intent detection ---
_PROXIMITY_PHRASES = (
    "not too far",
    "not far",
    "nearby",
    "close to home",
    "close by",
    "short flight",
    "short trip",
    "quick trip",
    "easy flight",
    "within a few hours",
)


def has_proximity_intent(query: str) -> bool:
    """Return True when the query contains proximity language ("not too far", etc.).

    Used to unlock the domestic seed boost and harden the Grok prompt with an
    explicit short-haul cap, regardless of the user's XP/stamp count.
    """
    q = (query or "").lower()
    return any(p in q for p in _PROXIMITY_PHRASES)


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
    stop_off_route = looks_like_stop_off_route(p)  # "stop off in X then go to Y"
    named_stop = extract_named_stop(p)  # "stop over in X", "layover in X"…

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

    # Row: user named a specific mid-route stop → detour (origin → X → Y).
    # Covers "stop off in X then go to Y" (tail phrase) AND shorter forms like
    # "stop over in X", "layover in X", "stopover in X" — even when the stop
    # is mentioned after the destination ("Vancouver to Bangkok, stop over in Tokyo").
    if dest_named and (stop_off_route or named_stop):
        return IntentDecision("detour", 0.87, "stop-off route: named mid-stop → destination")

    # Row: named stop found even when city extraction missed the full A→B pair.
    # This happens when a verb ("fly", "flying") precedes the origin city, causing
    # _CITY_TO_CITY to capture e.g. "fly Vancouver" as group-1 which _clean_city
    # then nulls out (stopword). An explicitly named stop city is a strong
    # detour signal on its own — "fly Vancouver to Bangkok stopping over in Tokyo".
    if named_stop:
        return IntentDecision("detour", 0.82, "named mid-stop → detour (city extraction missed A→B)")

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
