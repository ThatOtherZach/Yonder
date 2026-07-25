from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any  # noqa: F401 — used by translate_adventure form dict

import httpx
from pydantic import BaseModel, Field, ValidationError

from yonder.adventure import AdventureRequest, AdventureResult, StopoverIdea
from yonder.config import Settings
from yonder.types import CabinClass, FlightOffer, SearchQuery, UnifiedSearchResult

XAI_BASE = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.5"


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


class GrokAnalysis(BaseModel):
    headline: str = ""
    pick_index: int | None = None  # 1-based index into displayed offers
    pick_reason: str = ""
    tradeoffs: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    raw_markdown: str = ""


class GrokClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def is_configured(self) -> bool:
        return bool(self.settings.xai_api_key)

    async def __aenter__(self) -> GrokClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
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

    async def _chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        if not self.is_configured():
            raise RuntimeError("XAI_API_KEY not set — add it in Settings")
        model = self.settings.xai_model or DEFAULT_MODEL
        resp = await self.client.post(
            f"{XAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.xai_api_key}",
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
            raise RuntimeError(f"xAI HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected xAI response: {data!r}"[:400]) from exc

    async def parse_natural_language(
        self,
        prompt: str,
        *,
        default_currency: str = "USD",
        today: date | None = None,
    ) -> ParsedTrip:
        today = today or date.today()
        system = (
            "You are a flight-search query parser for a personal multi-provider scanner. "
            "Convert the user's free-text trip request into STRICT JSON only (no markdown fences). "
            "Use IATA airport codes (3 letters). Prefer major commercial airports. "
            "If the user names a city with multiple airports, pick the most common one and note it in assumptions. "
            "Resolve relative dates using the provided 'today'. "
            "Schema:\n"
            "{"
            '"origin":"YVR","destination":"NRT","depart_date":"YYYY-MM-DD",'
            '"return_date":"YYYY-MM-DD"|null,"adults":1,'
            '"cabin":"economy|premium_economy|business|first",'
            '"currency":"USD","nonstop_only":false,'
            '"intent_summary":"one line","assumptions":["..."]'
            "}"
        )
        user = (
            f"today: {today.isoformat()}\n"
            f"default_currency: {default_currency}\n"
            f"request: {prompt.strip()}"
        )
        text = await self._chat(system, user, temperature=0.1)
        payload = _extract_json(text)
        try:
            trip = ParsedTrip.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(f"Grok returned invalid trip JSON: {exc}") from exc
        trip.origin = trip.origin.upper()
        trip.destination = trip.destination.upper()
        trip.currency = (trip.currency or default_currency).upper()
        return trip

    async def analyze_results(
        self,
        *,
        prompt: str | None,
        query: SearchQuery,
        result: UnifiedSearchResult,
        max_offers: int = 12,
    ) -> GrokAnalysis:
        offers = result.offers[:max_offers]
        if not offers:
            return GrokAnalysis(
                headline="No offers to analyze",
                tips=["Try different dates or disable nonstop-only."],
            )

        compact = []
        for i, o in enumerate(offers, 1):
            compact.append(
                {
                    "i": i,
                    "provider": o.provider,
                    "price": o.price,
                    "currency": o.currency,
                    "airlines": o.airlines,
                    "stops_out": o.stops_out,
                    "duration_out_minutes": o.duration_out_minutes,
                    "notes": o.notes,
                    "route": o.summary_route(),
                }
            )

        system = (
            "You are a sharp, concise travel co-pilot helping someone pick among real flight price "
            "snapshots from multiple APIs. Be practical, slightly opinionated, not salesy. "
            "Prices may be cached/sandbox — say so if notes mention that. "
            "Respond with STRICT JSON only (no fences):\n"
            "{"
            '"headline":"short verdict",'
            '"pick_index":1,'
            '"pick_reason":"why this row",'
            '"tradeoffs":["..."],'
            '"tips":["..."],'
            '"raw_markdown":"2-4 short paragraphs of advice in markdown"'
            "}"
        )
        user = json.dumps(
            {
                "user_prompt": prompt,
                "query": {
                    "origin": query.origin,
                    "destination": query.destination,
                    "depart": query.depart_date.isoformat(),
                    "return": query.return_date.isoformat() if query.return_date else None,
                    "adults": query.adults,
                    "cabin": query.cabin.value,
                    "currency": query.currency,
                    "nonstop_only": query.nonstop_only,
                },
                "providers_ok": result.providers_ok,
                "providers_failed": [
                    {"provider": r.provider, "error": r.error}
                    for r in result.results
                    if not r.ok
                ],
                "offers": compact,
            },
            default=str,
        )
        text = await self._chat(system, user, temperature=0.4)
        try:
            payload = _extract_json(text)
            analysis = GrokAnalysis.model_validate(payload)
        except (ValidationError, RuntimeError, json.JSONDecodeError):
            # Fall back to free text if model didn't JSON
            analysis = GrokAnalysis(
                headline="Grok's take",
                raw_markdown=text.strip(),
            )
        if not analysis.raw_markdown and analysis.headline:
            parts = [f"**{analysis.headline}**"]
            if analysis.pick_reason:
                parts.append(analysis.pick_reason)
            if analysis.tradeoffs:
                parts.append("**Tradeoffs**\n" + "\n".join(f"- {t}" for t in analysis.tradeoffs))
            if analysis.tips:
                parts.append("**Tips**\n" + "\n".join(f"- {t}" for t in analysis.tips))
            analysis.raw_markdown = "\n\n".join(parts)
        return analysis

    def to_search_query(self, trip: ParsedTrip, max_results: int = 25) -> SearchQuery:
        return SearchQuery(
            origin=trip.origin,
            destination=trip.destination,
            depart_date=trip.depart_date,
            return_date=trip.return_date,
            adults=trip.adults,
            cabin=trip.cabin,
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
    ) -> tuple[AdventureRequest, list[StopoverIdea]]:
        """ONE Grok call: normalize the trip for APIs + propose detour cities.

        Token-efficient: replaces separate parse + propose calls.
        """
        today = today or date.today()
        avoid = form.get("avoid_countries") or []
        system = (
            "You are a flight API translator + adventure detour planner. "
            "Turn messy human travel text into CLEAN structured data for flight APIs, "
            "and propose intentional MULTI-DAY stopover cities (not 90-min layovers). "
            "Return STRICT JSON only (no markdown):\n"
            "{"
            '"origin":"YYZ","destination":"YVR","depart_date":"YYYY-MM-DD",'
            '"arrive_by":null,"adults":1,"currency":"CAD","cabin":"economy",'
            '"min_stop_days":3,"max_stop_days":5,"vibe":"adventure",'
            '"intent_summary":"one line",'
            '"candidates":[{"iata":"ZRH","city":"Zurich","country":"CH",'
            '"stay_days":4,"why":"...","vibe_tags":["alps"]}]'
            "}\n"
            "Rules:\n"
            "- IATA 3-letter codes only for airports\n"
            "- Prefer form fields when provided; fill gaps from the free-text prompt\n"
            "- Resolve relative dates from today\n"
            "- currency MUST match form/default (e.g. CAD not USD unless asked)\n"
            "- NEVER propose stopovers in avoid_countries (ISO2 list)\n"
            "- Never use origin/destination as stopovers\n"
            "- Exactly max_candidates creative but bookable detours\n"
            "- Mix hub / food / nature when vibe allows\n"
            "- country = ISO2 for each candidate"
        )
        user = json.dumps(
            {
                "today": today.isoformat(),
                "default_currency": default_currency,
                "avoid_countries": avoid,
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
            },
            default=str,
        )
        text = await self._chat(system, user, temperature=0.45)
        payload = _extract_json(text)

        currency = str(
            payload.get("currency") or form.get("currency") or default_currency
        ).upper()
        # Prefer explicit form currency when set
        if form.get("currency"):
            currency = str(form["currency"]).upper()

        try:
            origin = str(payload.get("origin") or form.get("origin") or "YYZ").upper()
            destination = str(
                payload.get("destination") or form.get("destination") or "YVR"
            ).upper()
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
                adults=int(payload.get("adults") or 1),
                currency=currency,
                cabin=CabinClass(str(payload.get("cabin") or "economy")),
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
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Invalid adventure translate: {exc}") from exc

        if req.max_stop_days < req.min_stop_days:
            req.max_stop_days = req.min_stop_days

        raw = payload.get("candidates") or []
        ideas: list[StopoverIdea] = []
        avoid_set = {a.upper() for a in avoid}
        for row in raw:
            try:
                code = str(row.get("iata") or "").upper()
                if len(code) != 3 or code in (req.origin, req.destination):
                    continue
                cc = str(row.get("country") or "").upper() or None
                if cc and cc in avoid_set:
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
        return req, ideas[: req.max_candidates]

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
            },
            default_currency=req.currency,
        )
        return ideas

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
        system = (
            "You estimate a LEAN traveler day bag — realistic, not inflated. "
            "Price each country in its **local currency** (use the local_currency given). "
            "Break the day into: hotel_local (decent 3★ night), food_local (meals+drinks), "
            "transit_local (basic metro/bus if any), culture_local (1–2 simple activities). "
            "daily_local MUST be the sum of those four. "
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
            "2024–2026 realistic prices; do NOT convert to "
            f"{disp} yourself — leave amounts in local currency. "
            "If the user provides a budget breakdown in display currency, treat it as "
            "their personal target style (not a price floor for every country)."
        )
        user = json.dumps(
            {
                "origin_country_iso2": origin_country.upper(),
                "origin_country_name": origin_name,
                "origin_local_currency": origin_loc,
                "display_currency_for_anchors_only": disp,
                "traveler_style": "lean day bag",
                "basket": "hotel + food/drink + basic transit + 1-2 culture",
                "vibe": vibe,
                "stop_countries": stops,
                "calibration_anchors_in_display_currency": anchors or [],
                "user_budget_breakdown_in_display_currency": user_budget or {},
                "note": (
                    f"Anchors/budget are in {disp} for calibration only. "
                    "JSON money fields must be local currency for each place."
                ),
            },
            default=str,
        )
        text = await self._chat(system, user, temperature=0.2)
        return _extract_json(text)

    async def narrate_adventure(self, result: AdventureResult) -> str:
        """Short story-style summary of priced adventure options."""
        compact = []
        for i, it in enumerate(result.itineraries[:8], 1):
            compact.append(
                {
                    "i": i,
                    "kind": it.kind,
                    "title": it.title,
                    "total": it.total_price,
                    "currency": it.currency,
                    "vs_direct": it.vs_direct_delta,
                    "score": it.adventure_score,
                    "why": it.why,
                    "stop": it.stop_city,
                    "stay_days": it.stay_days,
                    "leg_prices": [
                        {
                            "route": f"{leg.from_iata}-{leg.to_iata}",
                            "date": leg.depart_date.isoformat(),
                            "price": leg.price,
                            "airline": (leg.offer.airlines if leg.offer else None),
                        }
                        for leg in it.legs
                    ],
                }
            )
        system = (
            "You are a witty adventure travel co-pilot. Given priced multi-leg detour "
            "options (separate one-way tickets), write 3 short paragraphs of markdown: "
            "1) which adventure is most compelling and why, 2) best value vs direct, "
            "3) practical warnings (visa, bags, missed-connection risk). "
            "No JSON — just markdown prose. Be concrete with numbers."
        )
        user = json.dumps(
            {
                "request": result.request.model_dump(mode="json"),
                "direct_price": result.direct_price,
                "itineraries": compact,
            },
            default=str,
        )
        return (await self._chat(system, user, temperature=0.5)).strip()


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
