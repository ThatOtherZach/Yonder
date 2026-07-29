"""Local cache for short place/culture briefs (token-efficient Place Book)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from yonder.config import ROOT, Settings

DB_PATH = ROOT / "place_book_cache.db"
# ~45 days
TTL_SEC = 45 * 24 * 3600


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS place_briefs (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def cache_key(iata: str | None = None, country: str | None = None, city: str | None = None) -> str:
    parts = [
        (iata or "").upper().strip(),
        (country or "").upper().strip(),
        (city or "").strip().lower(),
    ]
    return "|".join(parts)


def get_cached(key: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload_json, fetched_at FROM place_briefs WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    if time.time() - float(row["fetched_at"]) > TTL_SEC:
        return None
    try:
        data = json.loads(row["payload_json"])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def get_any_cached_for_iata(iata: str) -> dict[str, Any] | None:
    """Return the most-recent cached brief for *any* key starting with IATA|.

    Used on the static share page where the exact country/city suffix may not
    be known — returns None if nothing is cached (never calls Grok).
    """
    prefix = (iata or "").upper().strip() + "|"
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json, fetched_at FROM place_briefs
            WHERE cache_key LIKE ? AND cache_key NOT LIKE '%|t:%'
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (prefix + "%",),
        ).fetchone()
    if not row:
        return None
    if time.time() - float(row["fetched_at"]) > TTL_SEC:
        return None
    try:
        data = json.loads(row["payload_json"])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def put_cached(key: str, payload: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO place_briefs (cache_key, payload_json, fetched_at)
            VALUES (?, ?, ?)
            """,
            (key, json.dumps(payload, default=str), time.time()),
        )
        conn.commit()


@dataclass
class PlaceBrief:
    title: str
    subtitle: str = ""
    facts: list[str] | None = None
    culture: str = ""
    food: str = ""
    vibe: str = ""
    caution: str = ""
    era_note: str = ""
    iata: str | None = None
    country: str | None = None
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "facts": self.facts or [],
            "culture": self.culture,
            "food": self.food,
            "vibe": self.vibe,
            "caution": self.caution,
            "era_note": self.era_note,
            "iata": self.iata,
            "country": self.country,
            "from_cache": self.from_cache,
        }


def _tone_key(user_prompt: str | None, trip_vibe: str | None) -> str:
    """Short fingerprint so cache can keep tone-specific notes without exploding keys."""
    import hashlib
    import re

    vibe = (trip_vibe or "").strip().lower()[:24]
    # Lightweight bag of words from the user prompt (ignore short stopwords)
    words = re.findall(r"[a-z]{4,}", (user_prompt or "").lower())
    stop = {
        "that",
        "this",
        "with",
        "from",
        "want",
        "have",
        "been",
        "somewhere",
        "trip",
        "days",
        "week",
        "city",
        "place",
    }
    sig = " ".join(w for w in words[:12] if w not in stop)
    raw = f"{vibe}|{sig}"
    if not raw.strip("|"):
        return ""
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


async def get_place_brief(
    settings: Settings,
    *,
    iata: str | None = None,
    country: str | None = None,
    city: str | None = None,
    role: str = "destination",
    user_prompt: str | None = None,
    trip_vibe: str | None = None,
) -> PlaceBrief | None:
    """Cache-first place brief. Returns None on miss without Grok or on hard failure.

    Structure is always the same; user_prompt + trip_vibe only tint the prose.
    """
    base = cache_key(iata, country, city)
    if not base.strip("|"):
        return None
    tone = _tone_key(user_prompt, trip_vibe)
    # Prefer tone-specific cache; fall back to generic for cold hits
    key = f"{base}|t:{tone}" if tone else base
    hit = get_cached(key)
    if not hit and tone:
        hit = get_cached(base)
    if hit:
        return PlaceBrief(
            title=str(hit.get("title") or city or iata or country or "Somewhere"),
            subtitle=str(hit.get("subtitle") or ""),
            facts=list(hit.get("facts") or [])[:4],
            culture=str(hit.get("culture") or ""),
            food=str(hit.get("food") or ""),
            vibe=str(hit.get("vibe") or ""),
            caution=str(hit.get("caution") or ""),
            era_note=str(hit.get("era_note") or ""),
            iata=iata,
            country=country,
            from_cache=True,
        )

    if not settings.grok_ready():
        return None

    try:
        from yonder.grok import GrokClient

        async with GrokClient(settings) as grok:
            payload = await grok.place_brief(
                iata=iata,
                country=country,
                city=city,
                role=role,
                user_prompt=user_prompt,
                trip_vibe=trip_vibe,
            )
        if not payload:
            return None
        put_cached(key, payload)
        # Also seed generic cache if empty (helps cold paths)
        if tone and not get_cached(base):
            put_cached(base, payload)
        return PlaceBrief(
            title=str(payload.get("title") or city or iata or "Somewhere"),
            subtitle=str(payload.get("subtitle") or ""),
            facts=list(payload.get("facts") or [])[:4],
            culture=str(payload.get("culture") or ""),
            food=str(payload.get("food") or ""),
            vibe=str(payload.get("vibe") or ""),
            caution=str(payload.get("caution") or ""),
            era_note=str(payload.get("era_note") or ""),
            iata=iata,
            country=country,
            from_cache=False,
        )
    except Exception:
        return None


async def briefs_for_stops(
    settings: Settings,
    stops: list[tuple[str | None, str | None, str | None]],
    *,
    max_n: int = 4,
    cache_only: bool = False,
    cancel_id: str | None = None,
    user_prompt: str | None = None,
    trip_vibe: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Map stop iata → brief dict. Caps live Grok calls.

    cache_only: disk hits only (Skip / fares-only path).
    cancel_id: stop live Grok if user hit Skip mid-enrichment.
    user_prompt / trip_vibe: color prose only — same field structure.
    """
    from yonder.search_cancel import is_cancelled

    out: dict[str, dict[str, Any]] = {}
    live = 0
    tone = _tone_key(user_prompt, trip_vibe)
    for iata, country, city in stops:
        if cancel_id and is_cancelled(cancel_id):
            break
        code = (iata or "").upper()
        if not code or code in out:
            continue
        base = cache_key(iata, country, city)
        # Prefer tone-keyed cache, then generic
        hit = None
        if tone:
            hit = get_cached(f"{base}|t:{tone}")
        if not hit:
            hit = get_cached(base)
        if hit:
            out[code] = {
                **hit,
                "iata": code,
                "country": country,
                "city": city,
                "from_cache": True,
            }
            continue
        if cache_only or live >= max_n:
            continue
        if cancel_id and is_cancelled(cancel_id):
            break
        brief = await get_place_brief(
            settings,
            iata=iata,
            country=country,
            city=city,
            role="stopover",
            user_prompt=user_prompt,
            trip_vibe=trip_vibe,
        )
        if brief:
            d = brief.to_dict()
            d["city"] = city
            out[code] = d
            if not brief.from_cache:
                live += 1
    return out


def stops_from_itineraries(
    itineraries: list[Any] | None, *, limit: int = 8
) -> list[tuple[str | None, str | None, str | None]]:
    """(iata, country, city) for Detour boarding passes."""
    out: list[tuple[str | None, str | None, str | None]] = []
    seen: set[str] = set()
    for it in itineraries or []:
        code = (getattr(it, "stop_iata", None) or "").upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(
            (
                code,
                getattr(it, "theme_country", None) or getattr(it, "stop_country", None),
                getattr(it, "stop_city", None),
            )
        )
        if len(out) >= limit:
            break
    return out
