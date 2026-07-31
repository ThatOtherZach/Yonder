"""GetYourGuide & Viator affiliate activity links for hub-city field notes.

Data lives in ``yonder/activities.csv`` (hand-built by the user):
``CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE`` — ~6 rows per city split
across both partners. The loader is mtime-cached (hot-reload friendly, like
other data files) and keyed by city name with IATA as tiebreaker.

SHORTTITLE from the CSV is used directly as the pill label (city prefix/suffix
stripped so "Amsterdam Evening Sunset Canal" → "Evening Sunset Canal").
No AI generation, no blocking calls.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Any

CSV_PATH = Path(__file__).resolve().parent / "activities.csv"
PROVIDERS = ("getyourguide", "viator")
PROVIDER_LABELS = {"getyourguide": "GetYourGuide", "viator": "Viator"}

# GROKVIBE values present in the CSV
_GROK_VIBES = {
    "adventure",
    "culture",
    "experience",
    "explorer",
    "foodie",
    "history",
    "nature",
    "nightlife",
    "vibe",
}

# App vibe id → closest GROKVIBE tag (exact matches need no entry).
# Unknown vibes get pure-random fallback within each provider.
_VIBE_TO_GROK = {
    "food": "foodie",
    "street": "foodie",
    "spice": "foodie",
    "party": "nightlife",
    "neon": "nightlife",
    "night": "nightlife",
    "electric": "nightlife",
    "carnival": "nightlife",
    "festival": "nightlife",
    "warmnights": "nightlife",
    "ancient": "history",
    "sacred": "history",
    "nostalgic": "history",
    "retro": "history",
    "folklore": "history",
    "gothic": "history",
    "jungle": "nature",
    "mountains": "nature",
    "forest": "nature",
    "ocean": "nature",
    "islands": "nature",
    "beach": "nature",
    "botanic": "nature",
    "valley": "nature",
    "meadow": "nature",
    "canopy": "nature",
    "lush": "nature",
    "savanna": "nature",
    "tropical": "nature",
    "reef": "nature",
    "lakeside": "nature",
    "snow": "nature",
    "canyon": "nature",
    "desert": "nature",
    "wild": "adventure",
    "rugged": "adventure",
    "feral": "adventure",
    "untamed": "adventure",
    "raw": "adventure",
    "road": "adventure",
    "dive": "adventure",
    "sail": "adventure",
    "chaos": "adventure",
    "art": "culture",
    "soul": "culture",
    "indie": "culture",
    "city": "explorer",
    "nomad": "explorer",
    "trains": "explorer",
    "luxury": "experience",
    "opulent": "experience",
    "golden": "experience",
    "spa": "experience",
    "wellbeing": "experience",
    "whimsical": "vibe",
    "magic": "vibe",
    "dream": "vibe",
    "vivid": "vibe",
}

_cache: dict[str, Any] = {"mtime": None, "by_iata": {}, "by_city": {}}


def _provider_for(url: str) -> str | None:
    low = url.lower()
    if "getyourguide.com" in low:
        return "getyourguide"
    if "viator.com" in low:
        return "viator"
    return None


def _clean_title(raw: str, city: str) -> str:
    """Strip leading/trailing city name from a SEO-style SHORTTITLE.

    "Amsterdam Evening Sunset Canal" → "Evening Sunset Canal"
    "Hydra Island Trip Athens"       → "Hydra Island Trip"
    "Bruges Day Trip"                → "Bruges Day Trip"  (no city match)
    """
    t = raw.strip()
    c = city.strip()
    if not c:
        return t
    low_t = t.lower()
    low_c = c.lower()
    candidate = t
    if low_t.startswith(low_c + " "):
        candidate = t[len(c) :].strip()
    elif low_t.endswith(" " + low_c):
        candidate = t[: -len(c)].strip()
    candidate = candidate.strip("·—–, ")
    # Keep cleaned version only when it has at least 3 words — shorter
    # results like "Best of" or "Traditional Dutch" read as fragments.
    return candidate if len(candidate.split()) >= 3 else t


def _load() -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """CSV → (iata → rows, lowercased city → rows). mtime-cached."""
    try:
        mtime = CSV_PATH.stat().st_mtime
    except OSError:
        return {}, {}
    if _cache["mtime"] == mtime:
        return _cache["by_iata"], _cache["by_city"]
    by_iata: dict[str, list[dict]] = {}
    by_city: dict[str, list[dict]] = {}
    try:
        with CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                url = (row.get("URL") or "").strip()
                if not url.lower().startswith("https://"):
                    continue
                provider = _provider_for(url)
                if not provider:
                    continue
                city = (row.get("CITY") or "").strip()
                raw_title = (row.get("SHORTTITLE") or "").strip()
                rec = {
                    "city": city,
                    "iata": (row.get("IATA") or "").strip().upper(),
                    "url": url,
                    "provider": provider,
                    "provider_label": PROVIDER_LABELS[provider],
                    "vibe": (row.get("GROKVIBE") or "").strip().lower(),
                    "emoji": (row.get("ACTIVITYEMOJI") or "").strip(),
                    "title": _clean_title(raw_title, city),
                }
                if not rec["title"] or not (rec["city"] or rec["iata"]):
                    continue
                if len(rec["iata"]) == 3 and rec["iata"].isalpha():
                    by_iata.setdefault(rec["iata"], []).append(rec)
                if rec["city"]:
                    by_city.setdefault(rec["city"].lower(), []).append(rec)
    except Exception:
        return {}, {}
    _cache["by_iata"] = by_iata
    _cache["by_city"] = by_city
    _cache["mtime"] = mtime
    return by_iata, by_city


def _rows_country(rows: list[dict]) -> str | None:
    """ISO country of a city's rows, via their anchor IATA. None when unknown."""
    try:
        from yonder.airports import city_country_for_iata

        for r in rows:
            got = city_country_for_iata(r["iata"])
            if got:
                return got[1]
    except Exception:
        pass
    return None


def links_for(city: str | None = None, iata: str | None = None) -> list[dict]:
    """All partner rows for a place — city name first, IATA as tiebreaker.

    Multi-airport metros collapse onto the city: an IATA the CSV doesn't list
    (LGW, HND, ORY…) resolves to its city name and matches the city's rows,
    guarded by country so namesakes (London, Ontario) never borrow another
    city's links.
    """
    by_iata, by_city = _load()
    rows = by_city.get((city or "").strip().lower())
    if rows:
        return list(rows)
    code = (iata or "").strip().upper()
    rows = by_iata.get(code)
    if rows:
        return list(rows)
    # Sibling-airport fallback: IATA → its city name (+country guard)
    if code:
        try:
            from yonder.airports import city_country_for_iata

            got = city_country_for_iata(code)
        except Exception:
            got = None
        if got:
            metro_city, country = got
            rows = by_city.get(metro_city.strip().lower())
            if rows:
                anchor = _rows_country(rows)
                if anchor is None or not country or anchor == country:
                    return list(rows)
    return []


def _grok_vibe_for(vibe: str | None) -> str | None:
    v = (vibe or "").strip().lower()
    if not v:
        return None
    if v in _GROK_VIBES:
        return v
    return _VIBE_TO_GROK.get(v)


def pick_activity_links(
    *,
    city: str | None = None,
    iata: str | None = None,
    vibe: str | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """One GetYourGuide + one Viator row for a place (copies, safe to mutate).

    Prefers rows whose GROKVIBE matches the trip vibe (random among matches);
    pure random fallback within the provider when none match. Empty list when
    the place isn't in the file.
    """
    rows = links_for(city=city, iata=iata)
    if not rows:
        return []
    pick = (rng or random).choice
    gv = _grok_vibe_for(vibe)
    out: list[dict] = []
    for provider in PROVIDERS:
        cand = [r for r in rows if r["provider"] == provider]
        if not cand:
            continue
        pref = [r for r in cand if gv and r["vibe"] == gv]
        out.append(dict(pick(pref or cand)))
    return out


async def activity_links_for(
    settings: object | None = None,
    *,
    city: str | None = None,
    iata: str | None = None,
    vibe: str | None = None,
    user_prompt: str | None = None,
) -> list[dict]:
    """Outbound activity pills for one card render — [] when no match.

    Uses CSV SHORTTITLE (city-stripped) directly. No AI calls, no blocking.
    """
    return pick_activity_links(city=city, iata=iata, vibe=vibe)
