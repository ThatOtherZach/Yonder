"""GetYourGuide & Viator affiliate activity links for hub-city field notes.

Data lives in ``yonder/activities.csv`` (hand-built by the user):
``CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE`` — ~6 rows per city split
across both partners. The loader is mtime-cached (hot-reload friendly, like
other data files) and keyed by city name with IATA as tiebreaker.

Pill titles shown in the UI are AI-generated (SHORTTITLE/GROKVIBE/emoji are
seeds, not final copy) and cached per URL+language in a small SQLite store;
the CSV SHORTTITLE is only a fallback when no AI backend is configured or a
call fails. URLs pass through unchanged.
"""

from __future__ import annotations

import asyncio
import csv
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from yonder.config import ROOT, Settings

CSV_PATH = Path(__file__).resolve().parent / "activities.csv"
TITLE_DB_PATH = ROOT / "activity_titles.db"
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

# App vibe id → closest GROKVIBE tag (exact matches like "adventure",
# "culture", "history", "nature" need no entry). Unknown vibes simply get the
# pure-random fallback within each provider.
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

# In-memory negative cache so a failing AI backend doesn't add latency to
# every render (retry after 5 minutes).
_title_fail_at: dict[str, float] = {}
_TITLE_RETRY_SEC = 300.0


def _provider_for(url: str) -> str | None:
    low = url.lower()
    if "getyourguide.com" in low:
        return "getyourguide"
    if "viator.com" in low:
        return "viator"
    return None


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
                    continue  # malformed / URL-less row
                provider = _provider_for(url)
                if not provider:
                    continue
                rec = {
                    "city": (row.get("CITY") or "").strip(),
                    "iata": (row.get("IATA") or "").strip().upper(),
                    "url": url,
                    "provider": provider,
                    "provider_label": PROVIDER_LABELS[provider],
                    "vibe": (row.get("GROKVIBE") or "").strip().lower(),
                    "emoji": (row.get("ACTIVITYEMOJI") or "").strip(),
                    "title": (row.get("SHORTTITLE") or "").strip(),
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


# ── AI pill titles (cached per URL + language) ───────────────────────────────


def _title_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(TITLE_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_titles (
            cache_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _title_key(url: str, lang: str | None) -> str:
    code = (lang or "en").lower()
    return url if code == "en" else f"{url}|l:{code}"


def get_cached_title(url: str, lang: str | None = None) -> str | None:
    try:
        with _title_conn() as conn:
            row = conn.execute(
                "SELECT title FROM activity_titles WHERE cache_key = ?",
                (_title_key(url, lang),),
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def put_cached_title(url: str, lang: str | None, title: str) -> None:
    try:
        with _title_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_titles (cache_key, title, fetched_at)"
                " VALUES (?, ?, ?)",
                (_title_key(url, lang), title, time.time()),
            )
            conn.commit()
    except Exception:
        pass


async def resolve_pill_titles(
    links: list[dict],
    settings: Settings | None,
    *,
    lang: str | None = None,
) -> list[dict]:
    """Swap each link's CSV title for the AI pill title (cache-first).

    Cache miss + configured AI backend → one Grok call for all misses. On
    failure (or no backend) the CSV SHORTTITLE stays as the visible fallback
    and the URL is negative-cached in memory for a few minutes.
    """
    misses = []
    for link in links:
        cached = get_cached_title(link["url"], lang)
        if cached:
            link["title"] = cached
        else:
            misses.append(link)
    if not misses or settings is None or not settings.grok_ready():
        return links
    now = time.time()
    todo = [
        l for l in misses if now - _title_fail_at.get(l["url"], 0.0) > _TITLE_RETRY_SEC
    ]
    if not todo:
        return links
    try:
        from yonder.grok import GrokClient

        async with GrokClient(settings) as grok:
            # Internal timeout — a hung backend must not stall card renders.
            titles = await asyncio.wait_for(
                grok.activity_pill_titles(todo, lang=lang), timeout=12.0
            )
    except Exception:
        for link in todo:
            _title_fail_at[link["url"]] = time.time()
        return links
    for link, title in zip(todo, titles):
        title = (title or "").strip()
        if title:
            link["title"] = title
            put_cached_title(link["url"], lang, title)
        else:
            _title_fail_at[link["url"]] = time.time()
    return links


async def activity_links_for(
    settings: Settings | None,
    *,
    city: str | None = None,
    iata: str | None = None,
    vibe: str | None = None,
    user_prompt: str | None = None,
) -> list[dict]:
    """Picked + titled outbound pills for one card render ([] when no match)."""
    links = pick_activity_links(city=city, iata=iata, vibe=vibe)
    if not links:
        return []
    from yonder.lang import detect_lang

    return await resolve_pill_titles(links, settings, lang=detect_lang(user_prompt))
