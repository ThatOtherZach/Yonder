"""Local cache for short place/culture briefs (token-efficient Place Book)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from yonder.config import Settings
from yonder.db import get_conn

# ~45 days
TTL_SEC = 45 * 24 * 3600


def cache_key(
    iata: str | None = None,
    country: str | None = None,
    city: str | None = None,
    lang: str | None = None,
) -> str:
    """Cache key for a place brief. lang (e.g. 'zh') keeps briefs generated
    for one prompt language from being served to another — English uses the
    legacy suffix-free key so existing English briefs stay valid."""
    parts = [
        (iata or "").upper().strip(),
        (country or "").upper().strip(),
        (city or "").strip().lower(),
    ]
    key = "|".join(parts)
    code = (lang or "en").lower()
    if code != "en":
        key += f"|l:{code}"
    return key


def _strip_emdash(obj: Any) -> Any:
    """Replace em dashes with ', ' in all string values (house style for field notes)."""
    if isinstance(obj, str):
        return obj.replace("\u2014", ", ")
    if isinstance(obj, list):
        return [_strip_emdash(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _strip_emdash(v) for k, v in obj.items()}
    return obj


def get_cached(key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload_json, fetched_at FROM place_briefs WHERE cache_key = %s",
            (key,),
        ).fetchone()
    if not row:
        return None
    if time.time() - float(row["fetched_at"]) > TTL_SEC:
        return None
    try:
        data = json.loads(row["payload_json"])
        if not isinstance(data, dict):
            return None
        # Treat entries without 'tagline' as stale — old format used era_note/vibe
        # separately; the new prompt merges them into a single punchy tagline.
        if "tagline" not in data:
            return None
        return _strip_emdash(data)
    except json.JSONDecodeError:
        return None


def get_any_cached_for_iata(iata: str, lang: str | None = None) -> dict[str, Any] | None:
    """Return the most-recent cached brief for *any* key starting with IATA|.

    Used on the static share page where the exact country/city suffix may not
    be known — returns None if nothing is cached (never calls Grok).
    lang narrows to briefs written in that language (English matches the
    legacy suffix-free keys).
    """
    prefix = (iata or "").upper().strip() + "|"
    code = (lang or "en").lower()
    if code == "en":
        lang_clause = "AND cache_key NOT LIKE '%%|l:%%'"
        params: tuple = (prefix + "%",)
    else:
        lang_clause = "AND cache_key LIKE %s"
        params = (prefix + "%", f"%|l:{code}")
    with get_conn() as conn:
        row = conn.execute(
            f"""
            SELECT payload_json, fetched_at FROM place_briefs
            WHERE cache_key LIKE %s AND cache_key NOT LIKE '%%|t:%%'
            {lang_clause}
            ORDER BY fetched_at DESC LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    if time.time() - float(row["fetched_at"]) > TTL_SEC:
        return None
    try:
        data = json.loads(row["payload_json"])
        if not isinstance(data, dict):
            return None
        # Same stale-format guard as get_cached: old entries lack 'tagline'.
        if "tagline" not in data:
            return None
        return _strip_emdash(data)
    except json.JSONDecodeError:
        return None


def purge_legacy_field_notes() -> int:
    """Delete cached field notes that lack a 'tagline' key (old era_note/vibe format).

    Returns the number of rows deleted. Safe to run at any time — the next
    request for each purged entry will re-fetch a fresh note from Grok.
    """
    deleted = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT cache_key, payload_json FROM place_briefs"
        ).fetchall()
        to_delete: list[str] = []
        for row in rows:
            try:
                data = json.loads(row["payload_json"])
                if not isinstance(data, dict) or "tagline" not in data:
                    to_delete.append(row["cache_key"])
            except json.JSONDecodeError:
                to_delete.append(row["cache_key"])
        if to_delete:
            conn.executemany(
                "DELETE FROM place_briefs WHERE cache_key = %s",
                [(k,) for k in to_delete],
            )
            conn.commit()
            deleted = len(to_delete)
    return deleted


def put_cached(key: str, payload: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO place_briefs (cache_key, payload_json, fetched_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                payload_json = EXCLUDED.payload_json,
                fetched_at = EXCLUDED.fetched_at
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
    tagline: str = ""
    iata: str | None = None
    country: str | None = None
    from_cache: bool = False
    activity_links: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_links": self.activity_links or [],
            "title": self.title,
            "subtitle": self.subtitle,
            "facts": self.facts or [],
            "culture": self.culture,
            "food": self.food,
            "vibe": self.vibe,
            "caution": self.caution,
            "era_note": self.era_note,
            "tagline": self.tagline,
            "iata": self.iata,
            "country": self.country,
            "from_cache": self.from_cache,
        }


def _payload_lang_mismatch(payload: dict[str, Any], lang: str) -> bool:
    """True when a cached brief's prose is clearly in a different language.

    Guards legacy entries written before language partitioning (an English
    request must never surface a Chinese brief cached under the old key).
    """
    from yonder.lang import detect_lang

    prose = " ".join(
        str(payload.get(k) or "") for k in ("culture", "tagline", "food")
    ).strip()
    if not prose:
        return False
    return detect_lang(prose) != (lang or "en")


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


async def _activity_links(
    settings: Settings,
    *,
    iata: str | None,
    city: str | None,
    trip_vibe: str | None,
    user_prompt: str | None,
) -> list[dict[str, Any]]:
    """Partner activity pills for a field note — [] for unmatched cities."""
    try:
        from yonder.activities import activity_links_for

        return await activity_links_for(
            settings, city=city, iata=iata, vibe=trip_vibe, user_prompt=user_prompt
        )
    except Exception:
        return []


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
    The prompt's language picks the reply language AND partitions the cache —
    a Chinese request never sees a cached English brief, and vice versa.
    """
    from yonder.lang import detect_lang

    lang = detect_lang(user_prompt)
    base = cache_key(iata, country, city, lang=lang)
    if not base.strip("|"):
        return None
    tone = _tone_key(user_prompt, trip_vibe)
    # Prefer tone-specific cache; fall back to generic for cold hits
    key = f"{base}|t:{tone}" if tone else base
    hit = get_cached(key)
    if not hit and tone:
        hit = get_cached(base)
    if hit and _payload_lang_mismatch(hit, lang):
        # Stale pre-partition entry written in another language — refetch.
        hit = None
    if hit:
        return PlaceBrief(
            activity_links=await _activity_links(
                settings, iata=iata, city=city, trip_vibe=trip_vibe, user_prompt=user_prompt
            ),
            title=str(hit.get("title") or city or iata or country or "Somewhere"),
            subtitle=str(hit.get("subtitle") or ""),
            facts=list(hit.get("facts") or [])[:4],
            culture=str(hit.get("culture") or ""),
            food=str(hit.get("food") or ""),
            vibe=str(hit.get("vibe") or ""),
            caution=str(hit.get("caution") or ""),
            era_note=str(hit.get("era_note") or ""),
            tagline=str(hit.get("tagline") or hit.get("era_note") or hit.get("vibe") or ""),
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
                lang=lang,
            )
        if not payload:
            return None
        # Tag the cached brief with the backend that wrote it so a stale BYOM
        # brief can be told apart from a Grok one later.
        payload = dict(payload)
        payload["model_source"] = settings.model_source_label()
        put_cached(key, payload)
        # Also seed generic cache if empty (helps cold paths)
        if tone and not get_cached(base):
            put_cached(base, payload)
        return PlaceBrief(
            activity_links=await _activity_links(
                settings, iata=iata, city=city, trip_vibe=trip_vibe, user_prompt=user_prompt
            ),
            title=str(payload.get("title") or city or iata or "Somewhere"),
            subtitle=str(payload.get("subtitle") or ""),
            facts=list(payload.get("facts") or [])[:4],
            culture=str(payload.get("culture") or ""),
            food=str(payload.get("food") or ""),
            vibe=str(payload.get("vibe") or ""),
            caution=str(payload.get("caution") or ""),
            era_note=str(payload.get("era_note") or ""),
            tagline=str(payload.get("tagline") or payload.get("era_note") or payload.get("vibe") or ""),
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

    from yonder.lang import detect_lang

    out: dict[str, dict[str, Any]] = {}
    live = 0
    tone = _tone_key(user_prompt, trip_vibe)
    lang = detect_lang(user_prompt)
    for iata, country, city in stops:
        if cancel_id and is_cancelled(cancel_id):
            break
        code = (iata or "").upper()
        if not code or code in out:
            continue
        base = cache_key(iata, country, city, lang=lang)
        # Prefer tone-keyed cache, then generic
        hit = None
        if tone:
            hit = get_cached(f"{base}|t:{tone}")
        if not hit:
            hit = get_cached(base)
        if hit and _payload_lang_mismatch(hit, lang):
            hit = None
        if hit:
            out[code] = {
                **hit,
                "iata": code,
                "country": country,
                "city": city,
                "from_cache": True,
                "activity_links": await _activity_links(
                    settings,
                    iata=code,
                    city=city,
                    trip_vibe=trip_vibe,
                    user_prompt=user_prompt,
                ),
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
    """(iata, country, city) for Detour boarding passes.

    For multi-stop itineraries (kind='multi-stop' or 'rescue'), also extracts
    the additional intermediate stops from the ``stops`` list so field notes
    load for every hop, not just the first.
    """
    out: list[tuple[str | None, str | None, str | None]] = []
    seen: set[str] = set()

    def _add(iata: str, country: str | None, city: str | None) -> None:
        code = (iata or "").upper()
        if not code or code in seen:
            return
        seen.add(code)
        out.append((code, country, city))

    for it in itineraries or []:
        kind = (getattr(it, "kind", None) or "").lower()
        if kind in ("multi-stop", "rescue"):
            # Extract all intermediate stops from the stops list first
            stops_list = getattr(it, "stops", None) or []
            for s in stops_list:
                if len(out) >= limit:
                    break
                if isinstance(s, dict):
                    _add(s.get("iata", ""), s.get("country"), s.get("city"))
                else:
                    _add(
                        getattr(s, "iata", ""),
                        getattr(s, "country", None),
                        getattr(s, "city", None),
                    )
        else:
            code = (getattr(it, "stop_iata", None) or "").upper()
            _add(
                code,
                getattr(it, "theme_country", None) or getattr(it, "stop_country", None),
                getattr(it, "stop_city", None),
            )
        if len(out) >= limit:
            break
    return out
