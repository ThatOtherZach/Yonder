"""Reuse previously saved trips as search results when not in testing mode.

When TESTING is false, searches first look for saved, non-mock trips that fit
the request. Matches render through the exact same result-card pipeline as
fresh results — with the fare hidden until a live price check on Share/Save.
If nothing fits, the caller falls back to normal AI generation.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from yonder.adventure import AdventureItinerary, AdventureRequest, AdventureResult
from yonder.saved import SavedItinerary, _STOP_WORDS, list_saved
from yonder.types import CabinClass

# Notes that would hint the trip is an old snapshot / demo — never show these
_NOTE_BLOCK = re.compile(
    r"fare|price|refresh|snapshot|last.known|provider|demo|mock|live|signal",
    re.IGNORECASE,
)


def _is_mock_saved(s: SavedItinerary) -> bool:
    """True when the stored trip came from Test Data / demo fares."""
    meta = s.trip_meta or {}
    if meta.get("mock"):
        return True
    it = s.itinerary or {}
    if str(it.get("price_kind") or "").lower() == "mock":
        return True
    for leg in it.get("legs") or []:
        offer = (leg or {}).get("offer") or {}
        if isinstance(offer, dict) and str(offer.get("price_kind") or "").lower() == "mock":
            return True
    return False


def _first_depart(s: SavedItinerary) -> date | None:
    legs = (s.itinerary or {}).get("legs") or []
    if not legs or not isinstance(legs[0], dict):
        return None
    raw = str(legs[0].get("depart_date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _score(
    s: SavedItinerary,
    *,
    tokens: set[str],
    vibe: str | None,
    origin: str | None,
    depart: date | None,
) -> float:
    score = 0.0
    text = f"{s.trip_prompt or ''} {s.title or ''} {s.stop_city or ''}".lower()
    words = set(re.findall(r"[a-z0-9]{3,}", text))
    if tokens:
        score += (len(tokens & words) / max(1, len(tokens))) * 4.0
    if vibe and (s.vibe or "").strip().lower() == vibe:
        score += 1.5
    if origin and (s.origin or "").upper() == origin:
        score += 2.0
    dep = _first_depart(s)
    if depart and dep:
        gap = abs((dep - depart).days)
        if gap <= 21:
            score += 1.0
        elif gap <= 90:
            score += 0.5
    return score


def _sanitize_itinerary(s: SavedItinerary) -> AdventureItinerary | None:
    """Rebuild the stored itinerary as a fresh-looking card with no price."""
    try:
        it = AdventureItinerary.model_validate(s.itinerary or {})
    except Exception:
        return None
    legs = [leg.model_copy(update={"offer": None, "error": None}) for leg in it.legs]
    notes = [n for n in it.notes if not _NOTE_BLOCK.search(str(n))]
    return it.model_copy(
        update={
            "legs": legs,
            "notes": notes,
            "total_price": None,
            "display_price": None,
            "display_price_base": None,
            "price_sign": None,
            "price_glyph": None,
            "price_tone": None,
            "vs_direct_delta": None,
            "all_in_display": None,
        }
    )


def find_recycled_result(
    *,
    prompt: str,
    vibe: str | None = None,
    origin: str | None = None,
    depart: str | None = None,
    currency: str = "USD",
    limit: int = 5,
    exclude_iatas: set[str] | None = None,
    min_score: float = 1.5,
) -> AdventureResult | None:
    """Best saved-trip matches for a search, packaged like a fresh result.

    Returns None when no adequate matches exist (caller runs the AI path).
    Mock-flagged trips and trips whose depart date has passed are never used.
    """
    vibe_l = (vibe or "").strip().lower() or None
    origin_u = (origin or "").strip().upper() or None
    exclude = {c.upper() for c in (exclude_iatas or set())}
    today = date.today()
    depart_d: date | None = None
    if depart:
        try:
            depart_d = date.fromisoformat(str(depart)[:10])
        except ValueError:
            depart_d = None
    tokens = {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (prompt or "").lower())
        if t not in _STOP_WORDS
    }
    # Reply language must match the current prompt's language: a saved trip's
    # title/why lines are written in the language of the prompt that created
    # it, so a Chinese-prompt trip must never be recycled into an English
    # search (and vice versa).
    from yonder.lang import detect_lang

    want_lang = detect_lang(prompt)

    scored: list[tuple[float, SavedItinerary]] = []
    for s in list_saved(limit=200):
        if _is_mock_saved(s):
            continue
        if detect_lang(f"{s.trip_prompt or ''} {s.title or ''}") != want_lang:
            continue
        dep = _first_depart(s)
        if dep is not None and dep < today:
            continue  # a past depart date would give the reuse away
        dest_code = (s.stop_iata or s.destination or "").upper()
        if dest_code and dest_code in exclude:
            continue
        sc = _score(s, tokens=tokens, vibe=vibe_l, origin=origin_u, depart=depart_d)
        if sc >= min_score:
            scored.append((sc, s))
    if not scored:
        return None

    scored.sort(key=lambda x: (-x[0], -x[1].saved_at))
    itineraries: list[AdventureItinerary] = []
    seen_dest: set[str] = set()
    for _, s in scored:
        dest_code = (s.stop_iata or s.destination or "").upper()
        if dest_code and dest_code in seen_dest:
            continue
        it = _sanitize_itinerary(s)
        if it is None:
            continue
        if dest_code:
            seen_dest.add(dest_code)
        itineraries.append(it)
        if len(itineraries) >= max(1, min(5, limit)):
            break
    if not itineraries:
        return None

    first_legs = itineraries[0].legs
    req_origin = (
        first_legs[0].from_iata if first_legs else (origin_u or "YVR")
    ).upper()
    req_dest = (
        first_legs[-1].to_iata if first_legs else req_origin
    ).upper()
    if len(req_origin) != 3:
        req_origin = origin_u or "YVR"
    if len(req_dest) != 3:
        req_dest = req_origin
    req = AdventureRequest(
        origin=req_origin,
        destination=req_dest,
        depart_date=depart_d or _first_depart(scored[0][1]) or today,
        adults=1,
        currency=(currency or "USD").upper(),
        cabin=CabinClass.ECONOMY,
        vibe=vibe_l or "adventure",
        prompt=prompt or "",
        trip_kind="getaway" if req_origin == req_dest else "detour",
        include_direct=False,
    )
    return AdventureResult(request=req, ideas=[], itineraries=itineraries)


def strip_revealing_notes(it: AdventureItinerary) -> AdventureItinerary:
    """Remove refresh/provider chatter before returning a repriced card."""
    return it.model_copy(
        update={"notes": [n for n in it.notes if not _NOTE_BLOCK.search(str(n))]}
    )
