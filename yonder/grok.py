from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from typing import Any  # noqa: F401 — used by translate_adventure form dict

import httpx
from pydantic import BaseModel, Field, ValidationError

from yonder.adventure import AdventureRequest, StopoverIdea
from yonder.config import Settings
from yonder.lang import detect_lang, language_directive
from yonder.types import CabinClass, FlightOffer, SearchQuery
from yonder.xp import compute_xp as _compute_xp

XAI_BASE = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.5"

# ── Parse cache: identical prompt + settings skips a round-trip ──────────────
# Keyed on normalized prompt, currency, home, avoid/visited lists, and today
# (relative dates parse against today, so day change busts naturally).
_PARSE_CACHE: dict[str, tuple[float, dict]] = {}
_PARSE_TTL_S = 6 * 3600.0
_PARSE_CACHE_MAX = 256


def _parse_cache_key(
    prompt: str,
    currency: str,
    home: str,
    avoid: list[str],
    visited: list[str],
    today: date,
    backend: str = "",
) -> str:
    raw = "|".join(
        [
            backend,
            " ".join((prompt or "").strip().lower().split()),
            (currency or "").upper(),
            home,
            ",".join(sorted(avoid)),
            ",".join(sorted(visited)),
            today.isoformat(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_cache_get(key: str) -> dict | None:
    hit = _PARSE_CACHE.get(key)
    if not hit:
        return None
    ts, payload = hit
    if (time.time() - ts) > _PARSE_TTL_S:
        _PARSE_CACHE.pop(key, None)
        return None
    return payload


def _parse_cache_put(key: str, payload: dict) -> None:
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        # Evict oldest entries (simple LRU-ish trim)
        for k in sorted(_PARSE_CACHE, key=lambda k: _PARSE_CACHE[k][0])[
            : _PARSE_CACHE_MAX // 4
        ]:
            _PARSE_CACHE.pop(k, None)
    _PARSE_CACHE[key] = (time.time(), payload)


def _learned_seed_candidates(*, vibe: str | None, origin: str | None) -> list[dict]:
    """Knowledge-assisted candidates for prompt seeding, or [] on cold start.

    Never raises; never blocks meaningfully (local SQLite reads only)."""
    try:
        from yonder.knowledge import seed_candidates

        return seed_candidates(vibe=vibe, origin=origin, limit=8)
    except Exception:
        return []


_ANCHOR_DIRECTIVE = (
    "\n- saved_anchor_legs are flights the traveler has ALREADY saved and "
    "intends to take (from_iata → to_iata departing depart_date). They are "
    "OPTIONAL connection targets: you MAY compose an itinerary that ends at "
    "an anchor's from_iata city, arriving at least one day BEFORE its "
    "depart_date, so the traveler connects into that saved flight. Never "
    "modify, rebook, or duplicate the anchor leg itself; ignore anchors "
    "that don't fit the request."
)


def _fully_visited_set(visited: list[str], settings=None) -> set[str]:
    """Countries counted as completely seen for hard destination drops.

    Tile-aware: reads the stored tile list when it matches the given
    country list, so partial coverage of a subdivided country (e.g. only
    Ontario) never hard-blocks that country.  Legacy country-only data
    treats each country as its country-level tile (subdivided whitelist
    countries then count as partial coverage).
    """
    try:
        from yonder.tiles import fully_visited_countries, visited_countries_from_tiles

        codes = [str(v).upper() for v in (visited or []) if v]
        try:
            if settings is None:
                from yonder.config import get_settings

                settings = get_settings()
            tiles = settings.visited_tile_list()
        except Exception:  # noqa: BLE001
            tiles = []
        if tiles and set(visited_countries_from_tiles(tiles)) == set(codes):
            return fully_visited_countries(tiles)
        return fully_visited_countries(codes)
    except Exception:  # noqa: BLE001
        return {str(v).upper() for v in (visited or []) if v}


def _domestic_region_hint(home_iata: str | None, settings=None) -> str:
    """Prompt line steering domestic picks toward unvisited home regions.

    Only fires when the traveller's home country is one of the subdivided
    whitelist countries (US/CA/GB), they have some coverage
    recorded, and unvisited regions remain.  Empty string otherwise.
    """
    try:
        from yonder.config import get_settings
        from yonder.countries import country_for_iata
        from yonder.tiles import is_subdivided, unvisited_home_regions

        cc = (country_for_iata((home_iata or "").strip().upper()) or "").upper()
        if not is_subdivided(cc):
            return ""
        if settings is None:
            settings = get_settings()
        tiles = settings.visited_tile_list()
        if not any(t == cc or t.startswith(cc + "-") for t in tiles):
            return ""
        remaining = unvisited_home_regions(cc, tiles)
        if not remaining:
            return ""
        names = ", ".join(n for _, n in remaining[:8])
        return (
            "\n- Domestic picks: the traveler has NOT yet explored these home-country "
            f"regions: {names}. When suggesting domestic destinations, prefer cities "
            "in those regions over already-visited ones."
        )
    except Exception:  # noqa: BLE001 — prompt hint must never break parsing
        return ""


def _anchor_prompt_rows(anchor_legs: list[dict] | None) -> list[dict]:
    """Compact anchor rows for prompt JSON (no ids/labels — token-lean)."""
    rows: list[dict] = []
    for a in anchor_legs or []:
        try:
            rows.append(
                {
                    "from_iata": str(a["from_iata"]).upper(),
                    "to_iata": str(a["to_iata"]).upper(),
                    "from_city": a.get("from_city"),
                    "to_city": a.get("to_city"),
                    "depart_date": str(a["depart_date"])[:10],
                }
            )
        except (KeyError, TypeError):
            continue
    return rows[:3]


def _anchor_fingerprint(anchor_legs: list[dict] | None) -> str:
    """Cache-key fragment — anchored and unanchored plans must not collide."""
    return ";".join(
        f"{r['from_iata']}-{r['to_iata']}-{r['depart_date']}"
        for r in _anchor_prompt_rows(anchor_legs)
    )


def _filter_quest_rows(raw: list, home_iata: str, avoid_set: set[str]) -> list[dict]:
    """Validate raw open-jaw quest rows: IATA shape, avoid list, distinct countries."""
    from yonder.countries import country_for_iata

    results: list[dict] = []
    for row in (raw or [])[:3]:
        try:
            entry = str(row.get("entry_iata") or "").upper()
            exit_ = str(row.get("exit_iata") or "").upper()
            if (
                len(entry) != 3
                or not entry.isalpha()
                or len(exit_) != 3
                or not exit_.isalpha()
            ):
                continue
            home_up = home_iata.upper()
            if entry == exit_ or entry == home_up or exit_ == home_up:
                continue
            # Respect avoid list
            entry_cc = (country_for_iata(entry) or "").upper()
            exit_cc = (country_for_iata(exit_) or "").upper()
            if entry_cc and entry_cc in avoid_set:
                continue
            if exit_cc and exit_cc in avoid_set:
                continue
            # Entry and exit must be in different countries
            if entry_cc and exit_cc and entry_cc == exit_cc:
                continue
            results.append(
                {
                    "entry_iata": entry,
                    "exit_iata": exit_,
                    "entry_city": str(row.get("entry_city") or entry),
                    "exit_city": str(row.get("exit_city") or exit_),
                    "overland_narrative": str(row.get("overland_narrative") or ""),
                    "transport": [str(t) for t in (row.get("transport") or [])],
                    "highlights": [str(h) for h in (row.get("highlights") or [])],
                }
            )
        except (TypeError, ValueError):
            continue

    return results


class ParsedTrip(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    depart_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = "USD"
    nonstop_only: bool = False
    intent_summary: str = ""
    assumptions: list[str] = Field(default_factory=list)


class GrokClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._usage_log: list[dict] = []

    def _backend_fingerprint(self) -> str:
        """Cache key fragment: a result from one model/provider must not be
        served after the user switches models in Settings."""
        byom_base = getattr(self.settings, "byom_base_url", "").strip().rstrip("/")
        byom_key = getattr(self.settings, "byom_api_key", "").strip()
        if byom_base and byom_key:
            return f"byom:{byom_base}:{getattr(self.settings, 'byom_model', '').strip() or DEFAULT_MODEL}"
        return f"xai:{self.settings.xai_model or DEFAULT_MODEL}"

    def is_configured(self) -> bool:
        byom_on = bool(
            getattr(self.settings, "byom_base_url", "")
            and getattr(self.settings, "byom_api_key", "")
        )
        return byom_on or bool(self.settings.xai_api_key)

    def model_source_label(self) -> str:
        """Label of the backend this client would call (see Settings.model_source_label)."""
        byom_base = getattr(self.settings, "byom_base_url", "").strip()
        byom_key = getattr(self.settings, "byom_api_key", "").strip()
        if byom_base and byom_key:
            name = getattr(self.settings, "byom_model", "").strip()
            return f"BYOM, {name}" if name else "BYOM"
        if self.settings.xai_api_key:
            return "Grok (Server)"
        return ""

    async def __aenter__(self) -> GrokClient:
        if self._client is None:
            # Connect fast; allow enough read time for invent/parse (fallback still catches stalls).
            # read=90 s ensures callers' asyncio.wait_for (up to 80 s for Quest)
            # fires before httpx cuts the connection.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=8.0, read=90.0, write=10.0, pool=8.0)
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Grok client not started")
        return self._client

    @property
    def accumulated_usage(self) -> dict:
        """Aggregate token usage across all _chat() calls on this instance."""
        if not self._usage_log:
            return {}
        from yonder.ai_usage import estimate_cost
        prompt = sum(u["prompt_tokens"] for u in self._usage_log)
        completion = sum(u["completion_tokens"] for u in self._usage_log)
        total = sum(u["total_tokens"] for u in self._usage_log)
        model = next((u["model"] for u in self._usage_log if u.get("model")), DEFAULT_MODEL)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "model": model,
            "model_source": self.model_source_label(),
            "calls": len(self._usage_log),
            "est_cost_usd": estimate_cost(prompt, completion, model),
        }

    async def _chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        if not self.is_configured():
            raise RuntimeError("XAI_API_KEY not set — add it in Settings")
        # BYOM takes precedence over built-in Grok when fully configured
        byom_base = getattr(self.settings, "byom_base_url", "").strip().rstrip("/")
        byom_key = getattr(self.settings, "byom_api_key", "").strip()
        byom_model = getattr(self.settings, "byom_model", "").strip()
        if byom_base and byom_key:
            from yonder.url_guard import BYOMUrlError, validate_byom_url

            try:
                validate_byom_url(byom_base)
            except BYOMUrlError as exc:
                raise RuntimeError(f"BYOM endpoint rejected: {exc}") from exc
            base_url = byom_base
            api_key = byom_key
            model = byom_model or DEFAULT_MODEL
        else:
            base_url = XAI_BASE
            api_key = self.settings.xai_api_key
            model = self.settings.xai_model or DEFAULT_MODEL
        resp = await self.client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"AI HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        usage = data.get("usage") or {}
        if usage:
            self._usage_log.append({
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
                "model": model,
            })
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected xAI response: {data!r}"[:400]) from exc

    async def parse_natural_language(
        self,
        prompt: str,
        *,
        default_currency: str = "USD",
        default_origin: str | None = None,
        today: date | None = None,
        avoid_countries: list[str] | None = None,
        visited_countries: list[str] | None = None,
        use_cache: bool = True,
    ) -> ParsedTrip:
        """Parse free-text into a trip. Honors passport map: avoid + visited ISO2 lists.

        use_cache: serve identical prompt+settings from a short-lived in-process
        cache (no AI call). Pass False on Refresh-for-novelty, where a repeat
        answer is exactly what the user does NOT want.
        """
        from yonder.countries import country_for_iata, country_label

        today = today or date.today()
        avoid = [a.upper() for a in (avoid_countries or []) if a]
        visited = [v.upper() for v in (visited_countries or []) if v]
        avoid_set = set(avoid)
        # Hard destination blocks only apply to FULLY visited countries —
        # partial tile coverage of a subdivided country keeps it eligible
        # (see yonder.tiles.fully_visited_countries).
        visited_set = _fully_visited_set(visited, self.settings)
        home = (default_origin or "").strip().upper()
        if len(home) != 3 or not home.isalpha():
            home = "YVR"

        # Backend fingerprint: a parse from one model/provider must not be
        # served after the user switches models in Settings.
        backend = self._backend_fingerprint()
        cache_k = _parse_cache_key(
            prompt, default_currency, home, avoid, visited, today, backend=backend
        )
        if use_cache:
            cached = _parse_cache_get(cache_k)
            if cached is not None:
                return ParsedTrip.model_validate(cached)

        system = (
            "You are a flight-search query parser for a personal multi-provider scanner. "
            "Convert the user's free-text trip request into STRICT JSON only (no markdown fences). "
            "Use IATA airport codes (3 letters). Prefer major commercial airports. "
            "If the user names a city with multiple airports, pick the most common one and note it in assumptions. "
            "Resolve relative dates using the provided 'today'.\n"
            "ORIGIN: Prefer default_origin. Use it when the user omits a from-city, "
            "or only says 'From XXX' with a 3-letter code that may be a UI template — "
            "if default_origin is set, origin MUST be default_origin unless the user "
            "clearly names a different city in words (e.g. 'from New York', 'leaving Miami').\n"
            "PASSPORT RULES (hard constraints — ground truth is the ISO2 lists, not prose):\n"
            "- NEVER set destination in avoid_countries (ISO2).\n"
            "- If the user wants somewhere new / not been / nowhere I've been / unexplored / "
            "or names places they already visited (e.g. 'including Thailand' as past travel): "
            "NEVER set destination in visited_countries. Pick a different country.\n"
            "- Phrases like 'nowhere I've been including X' mean X is already-visited, NOT the destination.\n"
            "- Open-ended 'somewhere new' / vibe-only prompts still need a concrete destination IATA — "
            "invent a plausible major-city airport outside visited+avoid; origin = default_origin.\n"
            "Schema:\n"
            "{"
            f'"origin":"{home}","destination":"NRT","depart_date":"YYYY-MM-DD",'
            '"return_date":"YYYY-MM-DD"|null,'
            '"currency":"USD","nonstop_only":false,'
            '"intent_summary":"one line","assumptions":["..."]'
            "}\n"
            "Always single traveler economy — do not invent party size or cabin."
            + language_directive(detect_lang(prompt))
        )

        def _blocked(dest_iata: str) -> str | None:
            cc = (country_for_iata(dest_iata) or "").upper()
            if not cc:
                return None
            if cc in avoid_set:
                return f"{dest_iata} is in avoided country {cc} ({country_label(cc)})"
            if cc in visited_set:
                return f"{dest_iata} is in visited country {cc} ({country_label(cc)}) — user wants new places"
            return None

        def _user_msg(extra: str = "") -> str:
            parts = [
                f"today: {today.isoformat()}",
                f"default_currency: {default_currency}",
                f"default_origin (use when user omits from-city): {home}",
                f"avoid_countries (ISO2, NEVER destination): {avoid or []}",
                f"visited_countries (ISO2, NEVER destination if user wants new places): {visited or []}",
                f"request: {prompt.strip()}",
            ]
            if extra:
                parts.append(extra)
            return "\n".join(parts)

        text = await self._chat(system, _user_msg(), temperature=0.1)
        payload = _extract_json(text)
        try:
            trip = ParsedTrip.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(f"Grok returned invalid trip JSON: {exc}") from exc
        trip.origin = trip.origin.upper()
        trip.destination = trip.destination.upper()
        trip.currency = (trip.currency or default_currency).upper()
        # Personal app: always one traveler, economy; fill missing origin from home
        if len(trip.origin) != 3 or not trip.origin.isalpha():
            trip = trip.model_copy(update={"origin": home})
        trip = trip.model_copy(update={"adults": 1, "cabin": CabinClass.ECONOMY})

        # Enforce passport map: one correction retry if destination is visited/avoided
        reason = _blocked(trip.destination)
        if reason:
            retry_extra = (
                f"CORRECTION REQUIRED: previous destination was invalid: {reason}. "
                f"Output a NEW destination IATA whose country is NOT in avoid_countries "
                f"and NOT in visited_countries. Do not use {trip.destination}."
            )
            # Minimal retry prompt — the correction only needs schema + constraint,
            # not the full parsing rulebook (cheaper tokens, same 1 call).
            retry_system = (
                "Flight query parser. STRICT JSON only (no markdown fences), schema: "
                "{"
                f'"origin":"{home}","destination":"NRT","depart_date":"YYYY-MM-DD",'
                '"return_date":"YYYY-MM-DD"|null,"currency":"USD","nonstop_only":false,'
                '"intent_summary":"one line","assumptions":["..."]'
                "}\n"
                "Use IATA codes for major commercial airports. "
                "destination country must NOT be in avoid_countries or visited_countries."
                + language_directive(detect_lang(prompt))
            )
            text2 = await self._chat(retry_system, _user_msg(retry_extra), temperature=0.2)
            payload2 = _extract_json(text2)
            try:
                trip2 = ParsedTrip.model_validate(payload2)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Grok returned invalid trip JSON on retry: {exc}"
                ) from exc
            trip2.origin = trip2.origin.upper()
            trip2.destination = trip2.destination.upper()
            trip2.currency = (trip2.currency or default_currency).upper()
            trip2 = trip2.model_copy(update={"adults": 1, "cabin": CabinClass.ECONOMY})
            reason2 = _blocked(trip2.destination)
            if reason2:
                raise RuntimeError(
                    "Could not pick a destination outside your visited/avoid map. "
                    f"Last try: {reason2}. Update the passport map or name a specific new city."
                )
            # keep origin/dates from first pass when retry omits them poorly
            if len(trip2.origin) == 3:
                trip.origin = trip2.origin
            trip.destination = trip2.destination
            if trip2.depart_date:
                trip.depart_date = trip2.depart_date
            trip.return_date = trip2.return_date
            trip.assumptions = list(trip.assumptions or []) + [
                f"Retried destination: avoided {reason}"
            ]
            trip.intent_summary = trip2.intent_summary or trip.intent_summary

        if use_cache:
            _parse_cache_put(cache_k, trip.model_dump(mode="json"))
        # Learning layer: archive the AI's escape interpretation (fire-and-forget)
        try:
            from yonder.knowledge import capture_interpretations_async

            capture_interpretations_async(
                [
                    {
                        "dest_iata": trip.destination,
                        "interpretation": trip.intent_summary
                        or "; ".join(trip.assumptions or []),
                        "tags": [],
                    }
                ],
                vibe=None,
                raw_query=prompt,
                origin=trip.origin,
                trip_shape="escape",
                model_source=self.model_source_label() or None,
            )
        except Exception:
            pass
        return trip

    async def place_brief(
        self,
        *,
        iata: str | None = None,
        country: str | None = None,
        city: str | None = None,
        role: str = "destination",
        user_prompt: str | None = None,
        trip_vibe: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        """Tiny culture card for Place Book. Keep tokens low.

        lang: reply language for prose fields; detected from user_prompt when
        omitted. Structured keys stay English/machine-readable either way.

        Structure is fixed (culture/food/vibe/…). Tone layers the user's query
        + trip vibe on top of the default field-note voice — do not invent new fields.
        """
        system = (
            "You write micro travel field notes for a vibe-first travel app. "
            "STRUCTURE is fixed — always the same JSON keys; never add sections.\n"
            "BASE VOICE: Rick Steves practicality, Bourdain hunger, Hunter S. Thompson heat, "
            "J. Peterman romanticism — one beer with all four. No filler, no marketing brochure.\n"
            "TONE LAYER: If user_prompt and/or trip_vibe are provided, color the prose to "
            "match that traveler's energy (cheap/chaotic/romantic/food-obsessed/quiet/etc.) "
            "while keeping the same structure and length. Echo their vibe, don't quote them.\n"
            "STRICT JSON only:\n"
            '{"title":"Name",'
            '"facts":["≤6 word punchy fact","…"],'
            '"culture":"1-2 sentences","food":"1 sentence",'
            '"caution":"optional 1 sentence or empty","tagline":"one cinematic sentence ≤15 words — vibe-matched prose ending with a specific evocative image"}\n'
            "facts are optional FAST FACT chips (max 3, each ≤6 words). Prefer culture/food/tagline."
            + language_directive(lang or detect_lang(user_prompt))
        )
        user = json.dumps(
            {
                "role": role,
                "iata": (iata or "").upper() or None,
                "country_iso2": (country or "").upper() or None,
                "city": city or None,
                "max_facts": 3,
                "trip_vibe": (trip_vibe or "").strip().lower() or None,
                "user_prompt": ((user_prompt or "").strip()[:400] or None),
            },
            default=str,
        )
        def _strip_emdash(obj: Any) -> Any:
            if isinstance(obj, str):
                return obj.replace("\u2014", ", ")
            if isinstance(obj, list):
                return [_strip_emdash(v) for v in obj]
            if isinstance(obj, dict):
                return {k: _strip_emdash(v) for k, v in obj.items()}
            return obj

        text = await self._chat(system, user, temperature=0.5)
        try:
            return _strip_emdash(_extract_json(text))
        except Exception:
            return {
                "title": city or iata or "Somewhere",
                "facts": [],
                "culture": text.strip()[:280] if text else "",
                "food": "",
                "caution": "",
                "tagline": "Trust your gut; recheck the fare.",
            }

    async def plan_quest(
        self,
        prompt: str,
        vibe: str,
        home_iata: str,
        depart_date: date,
        *,
        quest_days: int = 10,
        avoid: list[str] | None = None,
        visited: list[str] | None = None,
        anchor_legs: list[dict] | None = None,
        exclude_iatas: list[str] | set[str] | None = None,
    ) -> list[dict]:
        """Propose 1–3 open-jaw overland Quest itineraries.

        Entry and exit in different countries, neither in avoid_countries.
        Narrative names specific real transport links.
        exclude_iatas: destinations already covered elsewhere in the search
        (e.g. Escape's pick) — steered away from so Quest answers differently.
        Returns list of raw dicts matching QuestIdea schema.
        """
        from datetime import timedelta
        from yonder.countries import country_for_iata

        days = max(1, int(quest_days or 10))
        outbound_date = depart_date + timedelta(days=days)
        avoid_codes = [a.upper() for a in (avoid or []) if a]
        avoid_set = {a.upper() for a in avoid_codes}
        excl_codes = sorted({str(x).upper() for x in (exclude_iatas or []) if x})

        system = (
            "You are an open-jaw adventure trip planner for a vibe-first travel app. "
            "Propose 1–3 open-jaw itineraries: fly INTO one city, travel OVERLAND to another, fly OUT of there. "
            "Rules:\n"
            "- Entry and exit MUST be in DIFFERENT countries, NEITHER in avoid_countries (ISO2).\n"
            "- Name SPECIFIC real transport: actual train lines (e.g. 'Reunification Express'), "
            "ferry routes, bus companies — not generic 'bus' or 'train'.\n"
            f"- The overland journey must be genuinely feasible in the {days}-day window.\n"
            "- Match the traveler's vibe: food, chaos, romance, nature, etc.\n"
            "- Vary regions across ideas; don't repeat the same pair.\n"
            "Return STRICT JSON only (no markdown):\n"
            '{"ideas":['
            '{"entry_iata":"HAN","exit_iata":"BKK","entry_city":"Hanoi","exit_city":"Bangkok",'
            '"overland_narrative":"Ride the Reunification Express south to Ho Chi Minh City, then a Mekong slow boat through Cambodia to Phnom Penh, crossing overland into Thailand.",'
            '"transport":["Reunification Express","Mekong slow boat","National Bus Cambodia"],'
            '"highlights":["Hội An","Phnom Penh","Siem Reap"]}'
            "]}"
        )
        _anchor_rows = _anchor_prompt_rows(anchor_legs)
        if _anchor_rows:
            system += _ANCHOR_DIRECTIVE
        system += language_directive(detect_lang(prompt))
        user = json.dumps(
            {
                "prompt": prompt.strip()[:400],
                "vibe": vibe,
                "home_iata": home_iata,
                "depart_date": depart_date.isoformat(),
                "outbound_date": outbound_date.isoformat(),
                "window_days": days,
                "avoid_countries": avoid_codes,
                **({"exclude_destination_iatas": excl_codes} if excl_codes else {}),
                "count": "1 to 3 ideas (prefer 2-3 diverse options)",
                **({"exclude_destination_iatas": excl_codes} if excl_codes else {}),
                **({"saved_anchor_legs": _anchor_rows} if _anchor_rows else {}),
            },
            default=str,
        )

        text = await self._chat(system, user, temperature=0.65)
        try:
            payload = _extract_json(text)
        except Exception:
            return []

        # Normalise: model may return {"ideas":[...]} or a bare top-level array
        if isinstance(payload, list):
            raw = payload
        else:
            raw = payload.get("ideas") or []

        rows = _filter_quest_rows(raw, home_iata, avoid_set)
        self._capture_quest_interpretations(
            rows, vibe=vibe, prompt=prompt, home_iata=home_iata
        )
        return rows

    def _capture_quest_interpretations(
        self, rows: list[dict], *, vibe: str, prompt: str, home_iata: str
    ) -> None:
        """Archive quest proposals in the knowledge layer (fire-and-forget)."""
        try:
            from yonder.knowledge import capture_interpretations_async

            proposals = []
            for r in rows:
                narrative = str(r.get("overland_narrative") or "")
                tags = [str(h) for h in (r.get("highlights") or [])]
                for key in ("entry_iata", "exit_iata"):
                    if r.get(key):
                        proposals.append(
                            {
                                "dest_iata": r[key],
                                "interpretation": narrative,
                                "tags": tags,
                            }
                        )
            capture_interpretations_async(
                proposals,
                vibe=vibe,
                raw_query=prompt,
                origin=home_iata,
                trip_shape="quest",
                model_source=self.model_source_label() or None,
            )
        except Exception:
            pass

    def to_search_query(self, trip: ParsedTrip, max_results: int = 5) -> SearchQuery:
        return SearchQuery(
            origin=trip.origin,
            destination=trip.destination,
            depart_date=trip.depart_date,
            return_date=trip.return_date,
            adults=1,
            cabin=CabinClass.ECONOMY,
            currency=trip.currency,
            max_results=max_results,
            nonstop_only=trip.nonstop_only,
        )

    async def translate_adventure(
        self,
        *,
        prompt: str,
        form: dict[str, Any],
        default_currency: str = "CAD",
        today: date | None = None,
        seed_learned: bool = True,
        anchor_legs: list[dict] | None = None,
    ) -> tuple[AdventureRequest, list[StopoverIdea]]:
        """ONE Grok call: normalize the trip for APIs + propose detour cities.

        Token-efficient: replaces separate parse + propose calls.

        seed_learned: pass False on Refresh-for-novelty — re-suggesting the
        same learned destinations defeats the point of a refresh.
        """
        today = today or date.today()
        from yonder.countries import country_label

        avoid = [str(a).upper() for a in (form.get("avoid_countries") or []) if a]
        visited = [str(v).upper() for v in (form.get("visited_countries") or []) if v]
        system = (
            "You are a flight API translator + adventure trip planner. "
            "Turn messy human travel text into CLEAN structured data for flight APIs. "
            "Return STRICT JSON only (no markdown):\n"
            "{"
            '"trip_kind":"detour|getaway",'
            '"origin":"YVR","destination":"YVR",'
            '"depart_date":"YYYY-MM-DD",'
            '"arrive_by":null,"currency":"USD",'
            '"min_stop_days":3,"max_stop_days":5,"vibe":"adventure",'
            '"intent_summary":"one line",'
            '"candidates":[{"iata":"PDX","city":"Portland","country":"US",'
            '"stay_days":3,"why":"...","vibe_tags":["city","cheap"]}]'
            "}\n"
            "trip_kind rules (IMPORTANT — same model as local routing: "
            "no destination → getaway; named destination → detour):\n"
            "- getaway: ONLY when the user names NO second city anywhere in the text — they just "
            "want OUT OF a home base for a few days "
            "(e.g. 'get out of Vancouver', 'somewhere I haven't been', 'cheap escape', "
            "'not really anywhere specific', 'low hassle different'). "
            "Set origin AND destination to the SAME home IATA "
            "(round-trip home→X→home). candidates are DESTINATIONS (the X places), not mid-route stops.\n"
            "- detour: user names TWO different cities in ANY phrasing — 'A to B', or a departure "
            "city ('leave X', 'depart X', 'fly out of X') paired with an arrival city "
            "('end in Y', 'be in Y', 'arrive in Y', 'land in Y', 'get to Y'). "
            "A named arrival city ALWAYS forces detour: origin=departure city IATA, "
            "destination=arrival city IATA, origin≠destination. candidates are mid-route stops.\n"
            "- CRITICAL stop-off rule: When the user says 'stop off in X', 'stopping off in X', "
            "'stop over in X', 'stopping over in X', 'stopover in X', 'layover in X', "
            "'stop in X then go to Y', 'swing through X on the way to Y', or any similar phrasing "
            "where X is a mid-route stop — even when the stop is mentioned AFTER the destination "
            "(e.g. 'Vancouver to Bangkok, stop over in Tokyo') — ALWAYS set trip_kind=detour. "
            "X is a mid-route candidate stop; the final/last named city is the destination. "
            "NEVER swap them — even if the user adds vibe description for X "
            "(e.g. 'party stuff in Tokyo'). destination=final city IATA, candidates include X as a stop.\n"
            "- NEVER return origin==destination when two different cities are named.\n"
            "PASSPORT MAP (ground truth — always apply):\n"
            "- NEVER propose candidate countries in avoid_countries (ISO2)\n"
            "- For getaway / 'not somewhere I've been' / new places: NEVER propose visited_countries (ISO2)\n"
            "- For detour trips (user specified a fixed A→B route): stopovers are connection cities, NOT novelty destinations — visited_countries does NOT exclude them\n"
            "- The map lists are authoritative even if the user names a visited place in prose\n"
            "Other rules:\n"
            "- IATA 3-letter codes only\n"
            "- Prefer form fields when provided; fill gaps from free-text\n"
            "- Resolve relative dates from today\n"
            "- currency MUST match form/default\n"
            "- Never use home/origin as a candidate\n"
            "- Exactly max_candidates creative but bookable places\n"
            "- For cheap/low-hassle/chaos getaways prefer short-haul or easy hub cities\n"
            "- country = ISO2 for each candidate\n"
            "- traveler_comfort rank guides candidate boldness: "
            "Chaos Pilot/Nomadic Soul → prefer off-beaten-path, emerging, or unconventional stops; "
            "Armchair Explorer/Day Tripper → prefer domestic or nearby short-haul destinations first, "
            "safe hubs, easy connections, well-touristed cities; avoid suggesting 10+ hour flights; easy connections only"
        )
        from yonder.intent import has_proximity_intent
        _proximity_mode = has_proximity_intent(prompt)
        if _proximity_mode:
            system += (
                "\n- User explicitly asked for nearby travel — prefer domestic or "
                "short-haul destinations only; avoid suggesting any flight longer "
                "than 4 hours from the origin."
            )
        system += _domestic_region_hint(str(form.get("origin") or ""), self.settings)
        # Knowledge-assisted seeding: learned candidates the AI may confirm,
        # reorder, or override — the AI stays the decision-maker.
        learned = (
            _learned_seed_candidates(
                vibe=str(form.get("vibe") or ""), origin=str(form.get("origin") or "")
            )
            if seed_learned
            else []
        )
        if learned:
            system += (
                "\n- learned_candidates are destinations this traveler's past "
                "searches and feedback matched to this vibe (routes already "
                "verified rank first). They are OPTIONAL suggestions: confirm, "
                "reorder, or override them freely — still apply every rule above."
            )
        _anchor_rows = _anchor_prompt_rows(anchor_legs)
        if _anchor_rows:
            system += _ANCHOR_DIRECTIVE
        system += language_directive(detect_lang(prompt))
        # Codes only in the prompt (names bloat tokens / latency for large passport maps)
        _xp = _compute_xp(visited, avoid)
        user = json.dumps(
            {
                "today": today.isoformat(),
                "default_currency": default_currency,
                "avoid_countries": avoid,
                "visited_countries": visited,
                "traveler_comfort": _xp["rank"],
                "visited_country_count": len(visited),
                "max_candidates": form.get("max_candidates", 5),
                "form": {
                    "origin": form.get("origin"),
                    "destination": form.get("destination"),
                    "depart_date": form.get("depart"),
                    "arrive_by": form.get("arrive_by") or None,
                    "min_stop_days": form.get("min_stop_days"),
                    "max_stop_days": form.get("max_stop_days"),
                    "currency": form.get("currency") or default_currency,
                    "vibe": form.get("vibe"),
                },
                "user_prompt": prompt.strip(),
                "default_origin": (
                    str(form.get("origin") or "").upper()
                    if len(str(form.get("origin") or "")) == 3
                    else None
                ),
                "hint": (
                    "If no A→B cities are named (food/COL/safe/somewhere new), "
                    "use trip_kind=getaway. Prefer form origin, else default_origin/YVR; "
                    "origin=destination=home. candidates are NEW destinations outside "
                    "visited_countries and avoid_countries — lean cheap food + safe hubs."
                ),
                **({"learned_candidates": learned} if learned else {}),
                **({"saved_anchor_legs": _anchor_rows} if _anchor_rows else {}),
            },
            default=str,
        )
        text = await self._chat(system, user, temperature=0.45)
        payload = _extract_json(text)
        return self._adventure_from_payload(
            payload,
            form=form,
            prompt=prompt,
            default_currency=default_currency,
            avoid=avoid,
            visited=visited,
            proximity_mode=_proximity_mode,
        )

    def _adventure_from_payload(
        self,
        payload: dict,
        *,
        form: dict[str, Any],
        prompt: str,
        default_currency: str,
        avoid: list[str],
        visited: list[str],
        proximity_mode: bool,
    ) -> tuple[AdventureRequest, list[StopoverIdea]]:
        """Validate a translate-adventure payload into (AdventureRequest, ideas)."""
        currency = str(
            payload.get("currency") or form.get("currency") or default_currency
        ).upper()
        # Prefer explicit form currency when set
        if form.get("currency"):
            currency = str(form["currency"]).upper()

        try:
            origin = str(payload.get("origin") or form.get("origin") or "").upper()
            destination = str(
                payload.get("destination") or form.get("destination") or ""
            ).upper()
            trip_kind = str(payload.get("trip_kind") or "detour").lower().strip()
            # Heuristic: open-ended escape from one named home city
            if not origin:
                origin = _guess_home_iata(prompt) or "YVR"
            if trip_kind in ("getaway", "round_trip", "roundtrip", "escape", "from_home"):
                if not destination or destination == origin:
                    destination = origin
                else:
                    # Model put a sample dest — still treat as getaway home base
                    destination = origin
            elif not destination:
                # Incomplete O/D: if prompt only anchors home, fall into getaway
                home = _guess_home_iata(prompt)
                if home and (not origin or origin == home):
                    origin = home
                    destination = home
                    trip_kind = "getaway"
                else:
                    destination = origin or "YVR"
                    origin = origin or destination
            if origin == destination:
                trip_kind = "getaway"

            depart_raw = payload.get("depart_date") or form.get("depart")
            req = AdventureRequest(
                origin=origin,
                destination=destination,
                depart_date=date.fromisoformat(str(depart_raw)[:10]),
                arrive_by=(
                    date.fromisoformat(str(payload["arrive_by"])[:10])
                    if payload.get("arrive_by")
                    else (
                        date.fromisoformat(str(form["arrive_by"])[:10])
                        if form.get("arrive_by")
                        else None
                    )
                ),
                adults=1,
                currency=currency,
                cabin=CabinClass.ECONOMY,
                min_stop_days=int(
                    form.get("min_stop_days")
                    or payload.get("min_stop_days")
                    or 2
                ),
                max_stop_days=int(
                    form.get("max_stop_days")
                    or payload.get("max_stop_days")
                    or 5
                ),
                max_candidates=int(form.get("max_candidates") or 5),
                vibe=str(form.get("vibe") or payload.get("vibe") or "adventure"),
                prompt=prompt.strip(),
                avoid_countries=list(avoid),
                visited_countries=list(visited),
                trip_kind=trip_kind,
                include_direct=trip_kind != "getaway",
                proximity_mode=proximity_mode,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Invalid adventure translate: {exc}") from exc

        if req.max_stop_days < req.min_stop_days:
            req.max_stop_days = req.min_stop_days

        raw = payload.get("candidates") or []
        ideas: list[StopoverIdea] = []
        avoid_set = {a.upper() for a in avoid}
        # Only FULLY visited countries hard-drop getaway candidates
        visited_set = _fully_visited_set(visited, self.settings)
        home = {req.origin.upper(), req.destination.upper()}
        for row in raw:
            try:
                code = str(row.get("iata") or "").upper()
                if len(code) != 3 or code in home:
                    continue
                cc = str(row.get("country") or "").upper() or None
                if cc and cc in avoid_set:
                    continue
                if (
                    req.trip_kind == "getaway"
                    and visited_set
                    and cc
                    and cc in visited_set
                ):
                    continue
                stay = int(row.get("stay_days") or req.min_stop_days)
                stay = max(req.min_stop_days, min(req.max_stop_days, stay))
                ideas.append(
                    StopoverIdea(
                        iata=code,
                        city=str(row.get("city") or code),
                        stay_days=stay,
                        why=str(row.get("why") or ""),
                        vibe_tags=[str(t) for t in (row.get("vibe_tags") or [])],
                        country=cc,
                        source="grok",
                    )
                )
            except (TypeError, ValueError):
                continue
        ideas = ideas[: req.max_candidates]

        # Learning layer: archive every AI destination proposal (raw query +
        # verbatim reasoning + tags). Fire-and-forget — never delays rendering.
        try:
            from yonder.knowledge import capture_interpretations_async

            capture_interpretations_async(
                [
                    {
                        "dest_iata": i.iata,
                        "interpretation": i.why,
                        "tags": list(i.vibe_tags or []),
                    }
                    for i in ideas
                ],
                vibe=req.vibe,
                raw_query=prompt,
                origin=req.origin,
                trip_shape=req.trip_kind,
                model_source=self.model_source_label() or None,
            )
        except Exception:
            pass
        return req, ideas

    async def parse_adventure_prompt(
        self,
        prompt: str,
        *,
        default_currency: str = "CAD",
        today: date | None = None,
    ) -> AdventureRequest:
        """Legacy thin wrapper — prefers translate_adventure."""
        req, _ = await self.translate_adventure(
            prompt=prompt,
            form={"currency": default_currency},
            default_currency=default_currency,
            today=today,
        )
        return req

    async def propose_stopovers(self, req: AdventureRequest) -> list[StopoverIdea]:
        """Fallback: seed-friendly propose if translate wasn't used."""
        _, ideas = await self.translate_adventure(
            prompt=req.prompt or f"{req.origin} to {req.destination} adventure stopovers",
            form={
                "origin": req.origin,
                "destination": req.destination,
                "depart": req.depart_date.isoformat(),
                "arrive_by": req.arrive_by.isoformat() if req.arrive_by else "",
                "min_stop_days": req.min_stop_days,
                "max_stop_days": req.max_stop_days,
                "max_candidates": req.max_candidates,
                "currency": req.currency,
                "vibe": req.vibe,
                "avoid_countries": req.avoid_countries,
                "visited_countries": req.visited_countries,
            },
            default_currency=req.currency,
        )
        return ideas

    async def plan_unified(
        self,
        prompt: str,
        vibe: str,
        origin: str,
        *,
        depart_date: date,
        currency: str = "USD",
        min_stop_days: int = 2,
        max_stop_days: int = 5,
        max_candidates: int = 5,
        quest_days: int = 10,
        avoid: list[str] | None = None,
        visited: list[str] | None = None,
        exclude_iatas: list[str] | set[str] | None = None,
        today: date | None = None,
        use_cache: bool = True,
        anchor_legs: list[dict] | None = None,
        include_detour: bool = True,
        include_quest: bool = True,
    ) -> dict:
        """ONE Grok call covering the requested Find panels on a cold start.

        Replaces separate escape-parse + detour-invent + quest-pairs calls.
        Returns a typed dict:
          {"escape": ParsedTrip | None,
           "detour_cities": (AdventureRequest, list[StopoverIdea]) | None,
           "quest_pairs": list[dict]}
        Any section that comes back missing or invalid is None / [] — the
        caller falls back to the individual per-panel call for that section.

        use_cache: serve an identical prompt+settings repeat from the same
        short-lived in-process cache the per-panel escape parse uses (no AI
        call). Pass False on Refresh-for-novelty, where a repeat answer is
        exactly what the user does NOT want.
        """
        from datetime import timedelta
        from yonder.countries import country_for_iata
        from yonder.intent import has_proximity_intent

        today = today or date.today()
        avoid_codes = [str(a).upper() for a in (avoid or []) if a]
        visited_codes = [str(v).upper() for v in (visited or []) if v]
        avoid_set = set(avoid_codes)
        # Hard drops only apply to FULLY visited countries (tile-aware)
        visited_set = _fully_visited_set(visited_codes, self.settings)
        home = (origin or "").strip().upper()
        if len(home) != 3 or not home.isalpha():
            home = "YVR"
        days = max(1, int(quest_days or 10))
        outbound_date = depart_date + timedelta(days=days)
        excl = sorted({str(x).upper() for x in (exclude_iatas or []) if x})
        proximity_mode = has_proximity_intent(prompt)

        # ── Unified-plan cache: identical Find within TTL skips the AI call ──
        # Mirrors _parse_cache_key (prompt/currency/home/avoid/visited/today)
        # with vibe, depart_date and excluded IATAs folded into the backend
        # fragment — the "unified|" prefix namespaces entries away from the
        # per-panel escape-parse cache, and the backend fingerprint busts the
        # cache when the user switches models in Settings.
        cache_k = _parse_cache_key(
            prompt,
            currency,
            home,
            avoid_codes,
            visited_codes,
            today,
            backend="|".join(
                [
                    "unified",
                    self._backend_fingerprint(),
                    (vibe or "").strip().lower(),
                    depart_date.isoformat(),
                    ",".join(excl),
                    _anchor_fingerprint(anchor_legs),
                    f"detour={int(include_detour)}",
                    f"quest={int(include_quest)}",
                ]
            ),
        )
        if use_cache:
            cached = _parse_cache_get(cache_k)
            if cached is not None:
                try:
                    return self._unified_from_cache(cached)
                except Exception:  # stale/incompatible entry → live call
                    _PARSE_CACHE.pop(cache_k, None)

        _xp = _compute_xp(visited_codes, avoid_codes)
        _requested_sections = ["escape"]
        if include_detour:
            _requested_sections.append("detour")
        if include_quest:
            _requested_sections.append("quest")
        _section_count = {
            1: "ONE",
            2: "TWO",
            3: "THREE",
        }[len(_requested_sections)]
        _detour_schema = (
            ',"detour":{'
            '"trip_kind":"detour|getaway","origin":"YVR","destination":"YVR",'
            '"depart_date":"YYYY-MM-DD","arrive_by":null,"currency":"USD",'
            '"min_stop_days":3,"max_stop_days":5,"vibe":"adventure",'
            '"intent_summary":"one line",'
            '"candidates":[{"iata":"PDX","city":"Portland","country":"US",'
            '"stay_days":3,"why":"...","vibe_tags":["city","cheap"]}]}'
        ) if include_detour else ""
        _quest_schema = (
            ',"quest":{"ideas":['
            '{"entry_iata":"HAN","exit_iata":"BKK","entry_city":"Hanoi","exit_city":"Bangkok",'
            '"overland_narrative":"...","transport":["Reunification Express"],'
            '"highlights":["Hội An"]}]}'
        ) if include_quest else ""
        _quest_section_desc = (
            "SECTION quest — 1-3 open-jaw itineraries: fly INTO entry_iata, travel OVERLAND to "
            "exit_iata, fly out from there. Entry and exit MUST be in DIFFERENT countries, "
            "neither equal to home. Name SPECIFIC real transport (actual train lines, ferry "
            "routes, bus companies). The overland journey must be feasible in window_days. "
            "Vary regions across ideas.\n"
        ) if include_quest else ""
        _detour_section_desc = (
            "SECTION detour — trip_kind rules: getaway ONLY when the user names NO second city "
            "(origin=destination=home IATA; candidates are round-trip DESTINATIONS). "
            "detour when the user names two different cities (candidates are mid-route stops; "
            "a 'stop over in X' city is a candidate stop, NEVER the destination). "
            "Exactly max_candidates creative but bookable places; never use home as a candidate; "
            "country = ISO2 for each candidate. traveler_comfort rank guides boldness: "
            "Chaos Pilot/Nomadic Soul → off-beaten-path; Armchair Explorer/Day Tripper → "
            "nearby safe hubs and easy connections.\n"
        ) if include_detour else ""
        system = (
            f"You are the planning engine for a vibe-first travel app. From ONE traveler "
            f"prompt, produce {_section_count} requested section(s) in a single reply: "
            f"{', '.join(_requested_sections)}. "
            "Return STRICT JSON only (no markdown fences) with exactly this shape:\n"
            "{"
            '"escape":{'
            f'"origin":"{home}","destination":"NRT","depart_date":"YYYY-MM-DD",'
            '"return_date":"YYYY-MM-DD"|null,"currency":"USD","nonstop_only":false,'
            '"intent_summary":"one line","assumptions":["..."]}'
            + _detour_schema
            + _quest_schema
            + "}\n"
            "SECTION escape — parse the prompt as a point-to-point flight search. "
            "IATA codes only; prefer major commercial airports; resolve relative dates from today. "
            "If the user omits a from-city, origin MUST be default_origin. "
            "Open-ended / vibe-only prompts still need a concrete destination IATA.\n"
            + _detour_section_desc
            + _quest_section_desc
            + "PASSPORT RULES (hard constraints for ALL sections — ground truth is the ISO2 lists):\n"
            "- NEVER use a destination/candidate/entry/exit in avoid_countries.\n"
            "- When the user wants somewhere new / not been: NEVER pick visited_countries "
            "(escape destination and getaway candidates). Detour mid-route stops are exempt.\n"
            "- excluded_iatas are already shown or saved — do not reuse them as destinations "
            "or candidates.\n"
            "Match the traveler's vibe in every section. Always single traveler, economy."
        )
        if proximity_mode:
            system += (
                "\n- User explicitly asked for nearby travel — prefer domestic or "
                "short-haul destinations; avoid flights longer than 4 hours from origin."
            )
        system += _domestic_region_hint(home, self.settings)
        # Knowledge-assisted seeding — optional learned suggestions; AI decides.
        # Refresh-for-novelty (use_cache=False) skips injection: re-suggesting
        # the same learned destinations defeats the point of a refresh.
        learned = (
            _learned_seed_candidates(vibe=vibe, origin=home)
            if use_cache and (include_detour or include_quest)
            else []
        )
        if learned:
            system += (
                "\n- learned_candidates are destinations this traveler's past "
                "searches and feedback matched to this vibe (routes already "
                "verified rank first). They are OPTIONAL suggestions for the "
                "detour/quest sections: confirm, reorder, or override them "
                "freely — still apply every rule above."
            )
        _anchor_rows = _anchor_prompt_rows(anchor_legs)
        if _anchor_rows:
            system += _ANCHOR_DIRECTIVE
        system += language_directive(detect_lang(prompt))

        _user_payload: dict = {
            "today": today.isoformat(),
            "default_origin": home,
            "default_currency": currency.upper(),
            "depart_date": depart_date.isoformat(),
            "vibe": vibe,
            "avoid_countries": avoid_codes,
            "visited_countries": visited_codes,
            "excluded_iatas": excl[:24],
            "traveler_comfort": _xp["rank"],
            "visited_country_count": len(visited_codes),
            "user_prompt": prompt.strip()[:400],
        }
        if include_detour:
            _user_payload.update(
                {
                    "min_stop_days": min_stop_days,
                    "max_stop_days": max_stop_days,
                    "max_candidates": max_candidates,
                }
            )
        if include_quest:
            _user_payload["quest_outbound_date"] = outbound_date.isoformat()
            _user_payload["window_days"] = days
        if learned:
            _user_payload["learned_candidates"] = learned
        if _anchor_rows:
            _user_payload["saved_anchor_legs"] = _anchor_rows
        user = json.dumps(_user_payload, default=str)

        text = await self._chat(system, user, temperature=0.45)
        payload = _extract_json(text)
        out: dict = {"escape": None, "detour_cities": None, "quest_pairs": []}
        if not isinstance(payload, dict):
            return out

        # ── escape section ───────────────────────────────────────────────────
        esc_raw = payload.get("escape")
        if isinstance(esc_raw, dict):
            try:
                trip = ParsedTrip.model_validate(esc_raw)
                trip.origin = trip.origin.upper()
                trip.destination = trip.destination.upper()
                if len(trip.origin) != 3 or not trip.origin.isalpha():
                    trip = trip.model_copy(update={"origin": home})
                trip = trip.model_copy(
                    update={
                        "adults": 1,
                        "cabin": CabinClass.ECONOMY,
                        "currency": (trip.currency or currency).upper(),
                    }
                )
                cc = (country_for_iata(trip.destination) or "").upper()
                # Quality guard: passport-map or exclusion violation → drop the
                # section so the per-panel fallback (with its retry) handles it.
                if cc and (cc in avoid_set or cc in visited_set):
                    trip = None
                elif trip.destination in excl:
                    trip = None
                out["escape"] = trip
            except ValidationError:
                pass

        # ── detour section ───────────────────────────────────────────────────
        det_raw = payload.get("detour")
        if include_detour and isinstance(det_raw, dict):
            try:
                req, ideas = self._adventure_from_payload(
                    det_raw,
                    form={
                        "origin": home,
                        "destination": "",
                        "depart": depart_date.isoformat(),
                        "arrive_by": "",
                        "min_stop_days": min_stop_days,
                        "max_stop_days": max_stop_days,
                        "max_candidates": max_candidates,
                        "currency": currency,
                        "vibe": vibe,
                    },
                    prompt=prompt,
                    default_currency=currency,
                    avoid=avoid_codes,
                    visited=visited_codes,
                    proximity_mode=proximity_mode,
                )
                if ideas:
                    out["detour_cities"] = (req, ideas)
            except Exception:  # noqa: BLE001 — incomplete section → fallback
                pass

        # ── quest section (only when requested) ─────────────────────────────
        if include_quest:
            q_raw = payload.get("quest")
            if isinstance(q_raw, list):
                rows = q_raw
            elif isinstance(q_raw, dict):
                rows = q_raw.get("ideas") or []
            else:
                rows = []
            out["quest_pairs"] = _filter_quest_rows(rows, home, avoid_set)
            self._capture_quest_interpretations(
                out["quest_pairs"], vibe=vibe, prompt=prompt, home_iata=home
            )
        # Learning layer: archive the escape proposal.
        # (detour candidates were captured inside _adventure_from_payload).
        try:
            from yonder.knowledge import capture_interpretations_async

            if out["escape"] is not None:
                trip = out["escape"]
                capture_interpretations_async(
                    [
                        {
                            "dest_iata": trip.destination,
                            "interpretation": trip.intent_summary
                            or "; ".join(trip.assumptions or []),
                            "tags": [],
                        }
                    ],
                    vibe=vibe,
                    raw_query=prompt,
                    origin=trip.origin,
                    trip_shape="escape",
                    model_source=self.model_source_label() or None,
                )
        except Exception:
            pass
        # Only cache when at least one section succeeded — an all-empty result
        # must not make a transient failure "free" for the whole TTL.
        if use_cache and (out["escape"] or out["detour_cities"] or out["quest_pairs"]):
            try:
                _parse_cache_put(
                    cache_k,
                    {
                        "escape": (
                            out["escape"].model_dump(mode="json")
                            if out["escape"] is not None
                            else None
                        ),
                        "detour": (
                            {
                                "req": out["detour_cities"][0].model_dump(mode="json"),
                                "ideas": [
                                    i.model_dump(mode="json")
                                    for i in out["detour_cities"][1]
                                ],
                            }
                            if out["detour_cities"] is not None
                            else None
                        ),
                        "quest_pairs": out["quest_pairs"],
                    },
                )
            except Exception:
                pass
        return out

    @staticmethod
    def _unified_from_cache(payload: dict) -> dict:
        """Rebuild a plan_unified result from its cached JSON form."""
        out: dict = {"escape": None, "detour_cities": None, "quest_pairs": []}
        esc = payload.get("escape")
        if esc:
            out["escape"] = ParsedTrip.model_validate(esc)
        det = payload.get("detour")
        if det:
            out["detour_cities"] = (
                AdventureRequest.model_validate(det["req"]),
                [StopoverIdea.model_validate(i) for i in det.get("ideas") or []],
            )
        out["quest_pairs"] = list(payload.get("quest_pairs") or [])
        return out

    async def estimate_daily_costs_batch(
        self,
        *,
        origin_country: str,
        origin_name: str,
        stops: list[dict[str, str]],
        currency: str = "CAD",
        vibe: str = "adventure",
        origin_local_currency: str = "CAD",
        anchors: list[dict[str, Any]] | None = None,
        user_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Lean day-bag COL in **local** currency; caller FX-converts to user currency.

        Basket (per person / day):
          - 1 night decent hotel (clean 3★ / solid midscale — not hostel, not luxury)
          - food & drink for the day (normal meals, not fine dining)
          - basic local transit if it exists (metro/bus; not taxis/car hire)
          - money for 1–2 simple cultural things (museum, temple, walking tour)

        Prefer component split; daily_local should equal hotel+food+transit+culture.

        Returns JSON with local amounts:
          {
            "origin_daily_local": 160,
            "origin_currency": "CAD",
            "stops": {
              "CH": {
                "daily_local": 280,
                "local_currency": "CHF",
                "hotel_local": 160,
                "food_local": 70,
                "transit_local": 20,
                "culture_local": 30,
                "blurb": "..."
              }
            }
          }
        """
        disp = currency.upper()
        origin_loc = (origin_local_currency or "USD").upper()
        # Only treat as a real bag when total/components are set (> 0)
        ub: dict[str, Any] | None = None
        if isinstance(user_budget, dict):
            try:
                total = float(user_budget.get("total") or 0)
            except (TypeError, ValueError):
                total = 0.0
            parts_sum = 0.0
            for k in ("hotel", "food", "transit", "culture"):
                try:
                    parts_sum += float(user_budget.get(k) or 0)
                except (TypeError, ValueError):
                    pass
            if total > 0 or parts_sum > 0:
                ub = user_budget

        system = (
            "You estimate a LEAN traveler day bag — realistic, not inflated. "
            "Price each country in its **local currency** (use the local_currency given). "
            "Break the day into four COL parts and ADD THEM UP: "
            "hotel_local (decent 3★ night) + food_local (meals+drinks) + "
            "transit_local (basic metro/bus if any) + culture_local (1–2 simple activities). "
            "daily_local MUST equal hotel_local+food_local+transit_local+culture_local. "
            "NOT backpacker floor, NOT luxury, NOT taxis/shopping/flights. "
            "Country-level averages; city only if it strongly skews cost. "
            "Return STRICT JSON only (no markdown):\n"
            "{"
            f'"origin_daily_local":160,"origin_currency":"{origin_loc}",'
            '"stops":{"CH":{"daily_local":280,"local_currency":"CHF",'
            '"hotel_local":160,"food_local":70,"transit_local":20,"culture_local":30,'
            '"blurb":"one short sentence"}}'
            "}\n"
            "Rules: integers; one entry per stop ISO2; use that stop's local_currency; "
            "always output all four component fields; daily_local = their sum; "
            "2024–2026 realistic prices; do NOT convert to "
            f"{disp} yourself — leave amounts in local currency."
        )
        if ub:
            system += (
                " If the user provides a personal budget bag in display currency, that is "
                "THEIR target for later comparison (with an over-budget tolerance %) — "
                "do NOT force every country to match it; still estimate real local costs."
            )
        payload: dict[str, Any] = {
            "origin_country_iso2": origin_country.upper(),
            "origin_country_name": origin_name,
            "origin_local_currency": origin_loc,
            "display_currency_for_anchors_only": disp,
            "traveler_style": "lean day bag",
            "basket": "hotel + food/drink + basic transit + 1-2 culture (sum = daily)",
            "vibe": vibe,
            "stop_countries": stops,
            "calibration_anchors_in_display_currency": anchors or [],
            "note": (
                f"Anchors are in {disp} for calibration only. "
                "JSON money fields must be local currency for each place."
            ),
        }
        if ub:
            payload["user_budget_breakdown_in_display_currency"] = ub
            payload["user_over_budget_tolerance_pct"] = ub.get("tolerance_pct")
            payload["note"] = (
                f"Anchors/budget are in {disp} for calibration only. "
                "JSON money fields must be local currency for each place. "
                "User will score your daily_local against their bag total "
                f"with +{ub.get('tolerance_pct', 25)}% over-budget band."
            )
        user = json.dumps(payload, default=str)
        text = await self._chat(system, user, temperature=0.2)
        return _extract_json(text)


# Cheap city→IATA hints for open-ended getaways (no extra Grok tokens)
_HOME_CITY_IATA: list[tuple[str, str]] = [
    ("vancouver", "YVR"),
    ("toronto", "YYZ"),
    ("montreal", "YUL"),
    ("calgary", "YYC"),
    ("ottawa", "YOW"),
    ("edmonton", "YEG"),
    ("winnipeg", "YWG"),
    ("victoria", "YYJ"),
    ("seattle", "SEA"),
    ("portland", "PDX"),
    ("los angeles", "LAX"),
    ("san francisco", "SFO"),
    ("new york", "JFK"),
    ("nyc", "JFK"),
    ("london", "LHR"),
    ("paris", "CDG"),
]


def _guess_home_iata(prompt: str) -> str | None:
    """Best-effort home airport from free text (get out of Vancouver → YVR)."""
    p = (prompt or "").lower()
    # Prefer phrases like "out of Vancouver" / "from Vancouver"
    for city, iata in _HOME_CITY_IATA:
        if re.search(
            rf"\b(?:out of|from|leave|leaving|escape|based in|live in|in)\s+{re.escape(city)}\b",
            p,
        ):
            return iata
    for city, iata in _HOME_CITY_IATA:
        if re.search(rf"\b{re.escape(city)}\b", p):
            return iata
    return None


def detect_route_iatas(prompt: str) -> tuple[str, str] | None:
    """Map a clearly-named two-city route in the prompt to (origin, destination)
    IATA codes using the cheap city hints. None when unresolvable or same city."""
    from yonder.intent import extract_route_cities  # lazy: intent imports grok

    route = extract_route_cities(prompt)
    if not route:
        return None

    def _to_iata(token: str) -> str | None:
        from yonder.airports import iata_for_city, is_known_iata

        t = token.strip().lower()
        for city, iata in _HOME_CITY_IATA:
            if t == city or t.startswith(city + " ") or city.startswith(t + " "):
                return iata
        # Broad offline dataset for cities beyond the hint list
        resolved = iata_for_city(t)
        if resolved:
            return resolved
        if len(t) == 3 and t.isalpha() and is_known_iata(t):
            return t.upper()
        return None

    a, b = _to_iata(route[0]), _to_iata(route[1])
    if a and b and a != b:
        return a, b
    return None


def looks_like_open_getaway(prompt: str) -> bool:
    """True when the user wants somewhere new without a clear A→B flight pair.

    These belong in Detour (home → X → home), not Escape point-to-point.
    """
    p = (prompt or "").strip().lower()
    if not p:
        return False
    # Explicit A→B / IATA pair → Escape
    if re.search(r"\b[a-z]{3}\s*(?:→|->|to)\s*[a-z]{3}\b", p):
        return False
    if re.search(
        r"\b(?:from|fly(?:ing)?\s+from)\s+[a-z][a-z\s]{1,20}\s+to\s+[a-z]",
        p,
    ):
        return False
    open_markers = (
        "not anywhere i",
        "nowhere i've been",
        "nowhere ive been",
        "haven't been",
        "havent been",
        "somewhere new",
        "somewhere i haven't",
        "somewhere i havent",
        "not somewhere i",
        "get out of town",
        "get me out",
        "getaway",
        "open destination",
        "wherever",
        "cost of living",
        "cheap cost of living",
        "somewhere cheap",
        "not been before",
        "unexplored",
    )
    if any(m in p for m in open_markers):
        return True
    # No city/airport anchors at all + vibe-y wanderlust
    has_city = any(city in p for city, _ in _HOME_CITY_IATA)
    has_iata = bool(re.search(r"\b[A-Z]{3}\b", prompt or ""))
    vibe_only = any(
        w in p
        for w in (
            "food",
            "cheap",
            "beach",
            "safe",
            "security",
            "adventure",
            "relax",
            "culture",
        )
    )
    return (not has_city and not has_iata) and vibe_only


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # find first { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise RuntimeError(f"Could not parse JSON from model output: {text[:300]}")


def offer_brief(o: FlightOffer) -> str:
    return f"{o.currency} {o.price:.0f} via {o.provider} ({','.join(o.airlines) or '?'}, {o.stops_out} stops)"
