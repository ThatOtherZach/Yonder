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


async def get_place_brief(
    settings: Settings,
    *,
    iata: str | None = None,
    country: str | None = None,
    city: str | None = None,
    role: str = "destination",
) -> PlaceBrief | None:
    """Cache-first place brief. Returns None on miss without Grok or on hard failure."""
    key = cache_key(iata, country, city)
    if not key.strip("|"):
        return None
    hit = get_cached(key)
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
            )
        if not payload:
            return None
        put_cached(key, payload)
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
) -> dict[str, dict[str, Any]]:
    """Map stop iata → brief dict. Caps live Grok calls."""
    out: dict[str, dict[str, Any]] = {}
    live = 0
    for iata, country, city in stops:
        code = (iata or "").upper()
        if not code or code in out:
            continue
        key = cache_key(iata, country, city)
        hit = get_cached(key)
        if hit:
            out[code] = {**hit, "iata": code, "country": country, "from_cache": True}
            continue
        if live >= max_n:
            continue
        brief = await get_place_brief(
            settings, iata=iata, country=country, city=city, role="stopover"
        )
        if brief:
            out[code] = brief.to_dict()
            live += 1
    return out
