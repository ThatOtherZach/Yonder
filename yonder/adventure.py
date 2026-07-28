from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from yonder.config import Settings, get_settings
from yonder.countries import (
    format_place,
    format_route,
    is_avoided_iata,
    normalize_avoid_list,
    normalize_country_list,
    country_for_iata,
)
from yonder.currency import convert_offer
from yonder.engine import search_flights
from yonder.history import record_leg, route_stats
from yonder.links import (
    attach_links_from_leg,
    google_flights_multi,
    google_flights_url,
    kayak_url,
)
from yonder.money import format_approx, price_display
from yonder.themes import theme_css_vars, theme_for_iata
from yonder.types import CabinClass, FlightOffer, SearchQuery


def _apply_theme(it: AdventureItinerary) -> AdventureItinerary:
    from yonder.themes import theme_for_country

    if it.kind == "direct":
        t = theme_for_iata(None, kind="direct")
    elif it.theme_country:
        t = theme_for_country(it.theme_country)
        t["country"] = it.theme_country
    else:
        # getaway destinations use the same stopover theme palette
        t = theme_for_iata(it.stop_iata, kind="stopover")
    return it.model_copy(
        update={
            "theme_country": t.get("country") or it.theme_country,
            "theme_flag": t.get("flag") or "🌍",
            "theme_flag_img": t.get("flag_img") or "",
            "theme_label": t["label"],
            "theme_primary": t["primary"],
            "theme_accent": t["accent"],
            "theme_gradient": t["gradient"],
            "theme_style": theme_css_vars(t),
        }
    )

# Interesting hubs used as intentional stopovers (seed when Grok offline)
SEED_STOPOVERS: list[dict[str, Any]] = [
    {"iata": "ZRH", "city": "Zurich", "country": "CH", "why": "Swiss Alps gateway — classic long-haul detour", "vibe_tags": ["alps", "city", "trains"]},
    {"iata": "IST", "city": "Istanbul", "country": "TR", "why": "Turkish Airlines hub with easy multi-day city break", "vibe_tags": ["city", "food", "bazaar"]},
    {"iata": "LIS", "city": "Lisbon", "country": "PT", "why": "Atlantic TAP stopover city — cheap Europe beach vibes", "vibe_tags": ["city", "food", "coast"]},
    {"iata": "KEF", "city": "Reykjavik", "country": "IS", "why": "Icelandair-style layover adventure — nature + hot springs", "vibe_tags": ["nature", "north"]},
    {"iata": "DOH", "city": "Doha", "country": "QA", "why": "Qatar hub with strong long-haul deals", "vibe_tags": ["city", "modern"]},
    {"iata": "AMS", "city": "Amsterdam", "country": "NL", "why": "KLM hub — canals, bikes, easy city hop", "vibe_tags": ["city", "culture"]},
    {"iata": "CDG", "city": "Paris", "country": "FR", "why": "Obvious but underrated as a *planned* multi-day stop", "vibe_tags": ["city", "food", "art"]},
    {"iata": "MEX", "city": "Mexico City", "country": "MX", "why": "North America detour with huge food culture", "vibe_tags": ["city", "food", "culture"]},
    {"iata": "CUN", "city": "Cancun", "country": "MX", "why": "Beach break between Canadian coasts", "vibe_tags": ["beach", "relax"]},
    {"iata": "YUL", "city": "Montreal", "country": "CA", "why": "Domestic-ish culture detour if routing allows", "vibe_tags": ["city", "food"]},
    {"iata": "YYC", "city": "Calgary", "country": "CA", "why": "Rockies access between east and west Canada", "vibe_tags": ["nature", "mountains"]},
    {"iata": "LAX", "city": "Los Angeles", "country": "US", "why": "Sun + sprawl stop between continents/coasts", "vibe_tags": ["city", "sun"]},
    {"iata": "NRT", "city": "Tokyo Narita", "country": "JP", "why": "Japan stopover when Pacific routing is wild", "vibe_tags": ["city", "food", "neon"]},
    {"iata": "ICN", "city": "Seoul", "country": "KR", "why": "Incheon hub with great food and city energy", "vibe_tags": ["city", "food"]},
    {"iata": "DXB", "city": "Dubai", "country": "AE", "why": "Mega-hub desert city for long east-west routes", "vibe_tags": ["city", "modern"]},
    {"iata": "BCN", "city": "Barcelona", "country": "ES", "why": "Mediterranean detour worth the extra days", "vibe_tags": ["city", "beach", "food"]},
    {"iata": "LHR", "city": "London", "country": "GB", "why": "Classic hub — museums, pubs, easy connections", "vibe_tags": ["city", "culture"]},
    {"iata": "FRA", "city": "Frankfurt", "country": "DE", "why": "Lufthansa mega-hub for Europe hops", "vibe_tags": ["city", "hub"]},
    {"iata": "SIN", "city": "Singapore", "country": "SG", "why": "Jewel of SE Asia stopovers", "vibe_tags": ["city", "food", "modern"]},
    {"iata": "BKK", "city": "Bangkok", "country": "TH", "why": "Street food + easy multi-day chaos", "vibe_tags": ["city", "food"]},
]


class AdventureRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    depart_date: date
    arrive_by: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    currency: str = "CAD"
    cabin: CabinClass = CabinClass.ECONOMY
    min_stop_days: int = Field(default=2, ge=1, le=21)
    max_stop_days: int = Field(default=5, ge=1, le=30)
    max_candidates: int = Field(default=5, ge=2, le=10)
    vibe: str = "adventure"
    prompt: str = ""
    include_direct: bool = True
    avoid_countries: list[str] = Field(default_factory=list)  # ISO2, max 10
    # detour = A → stop → B · getaway = home → X → home (open destination)
    trip_kind: str = "detour"
    visited_countries: list[str] = Field(default_factory=list)  # ISO2 passport map


class StopoverIdea(BaseModel):
    iata: str
    city: str
    stay_days: int = 3
    why: str = ""
    vibe_tags: list[str] = Field(default_factory=list)
    country: str | None = None
    source: str = "seed"  # seed | grok


class PricedLeg(BaseModel):
    from_iata: str
    to_iata: str
    depart_date: date
    offer: FlightOffer | None = None
    error: str | None = None
    google_flights_url: str | None = None
    booking_url: str | None = None

    @property
    def price(self) -> float | None:
        return self.offer.price if self.offer else None


class AdventureItinerary(BaseModel):
    kind: str  # direct | stopover
    title: str
    total_price: float | None = None
    currency: str = "CAD"
    stop_city: str | None = None
    stop_iata: str | None = None
    stay_days: int | None = None
    why: str = ""
    vibe_tags: list[str] = Field(default_factory=list)
    legs: list[PricedLeg] = Field(default_factory=list)
    vs_direct_delta: float | None = None
    adventure_score: float = 0.0
    notes: list[str] = Field(default_factory=list)
    bookable_separately: bool = True
    google_flights_url: str | None = None  # multi-city style for whole trip
    booking_url: str | None = None
    kayak_url: str | None = None
    display_price: str | None = None  # ~C$786▼ / ~C$786▲
    display_price_base: str | None = None  # ~C$786
    price_sign: str | None = None  # up | down
    price_glyph: str | None = None  # ▲ | ▼
    price_tone: str | None = None  # good | bad | neutral
    # Flag-inspired theme for the card
    theme_country: str | None = None
    theme_flag: str = "🌍"  # emoji (may render as letters on Windows)
    theme_flag_img: str = ""  # PNG URL — reliable on Windows
    theme_label: str = "Adventure"
    theme_primary: str = "#e6b450"
    theme_accent: str = "#f0c96a"
    theme_gradient: str = "linear-gradient(165deg, #1a2332 0%, #2a2218 100%)"
    theme_style: str = ""
    # Ground cost vs origin (Grok mid-range estimate, cached)
    ground_daily_stop: float | None = None
    ground_daily_origin: float | None = None
    ground_total: float | None = None
    ground_display: str | None = None  # e.g. + ~C$720 ground
    ground_compare_line: str | None = None
    all_in_display: str | None = None
    ground_budget_status: str | None = None  # under | within | over
    ground_budget_line: str | None = None
    ground_rank_delta: float | None = None


class AdventureResult(BaseModel):
    request: AdventureRequest
    ideas: list[StopoverIdea]
    itineraries: list[AdventureItinerary]
    direct_price: float | None = None
    narrative: str = ""
    errors: list[str] = Field(default_factory=list)
    pricing_provider: str | None = None


def _cheapest(offers: list[FlightOffer]) -> FlightOffer | None:
    if not offers:
        return None
    return min(offers, key=lambda o: o.price)


async def pick_pricing_provider(
    settings: Settings,
    client: httpx.AsyncClient,
    include_mock: bool,
) -> list[str] | None:
    """Probe active keys, pick best *fare* provider (never AviationStack)."""
    from yonder.quota import FARE_PROVIDERS, choose_providers, get_registry

    await choose_providers(
        settings,
        client,
        mode="adventure_leg",
        need=5,
        include_mock=include_mock,
        force_all=False,
        probe=True,
    )
    reg = get_registry()
    candidates: list[tuple[float, str]] = []
    for name in settings.configured_providers():
        if name not in FARE_PROVIDERS:
            continue
        b = reg.ensure(name, configured=True)
        sc = b.score("adventure_leg", settings=settings)
        if sc > -1e8:
            candidates.append((sc, name))
    if include_mock:
        candidates.append((-50.0, "mock"))
    candidates.sort(reverse=True)
    if candidates:
        return [c[1] for c in candidates]  # full ranked list for fallbacks
    return ["mock"] if include_mock else None


async def _price_leg(
    origin: str,
    dest: str,
    depart: date,
    req: AdventureRequest,
    *,
    settings: Settings,
    include_mock: bool,
    only: list[str] | None,
    http: httpx.AsyncClient,
    fallback_chain: list[str] | None = None,
) -> PricedLeg:
    """Price one leg; fall through fare providers if primary fails."""
    from yonder.quota import FARE_PROVIDERS

    chain: list[str] = []
    for n in list(only or []) + list(fallback_chain or []):
        if n not in chain and (n in FARE_PROVIDERS or n == "mock"):
            chain.append(n)
    for n in settings.configured_providers():
        if n in FARE_PROVIDERS and n not in chain:
            chain.append(n)
    if include_mock and "mock" not in chain:
        chain.append("mock")
    if not chain:
        return PricedLeg(
            from_iata=origin,
            to_iata=dest,
            depart_date=depart,
            error="no fare providers available",
        )

    errors: list[str] = []
    for provider_name in chain:
        q = SearchQuery(
            origin=origin.upper(),
            destination=dest.upper(),
            depart_date=depart,
            return_date=None,
            adults=req.adults,
            cabin=req.cabin,
            currency=req.currency,
            max_results=5,
            nonstop_only=False,
        )
        try:
            result = await search_flights(
                q,
                settings=settings,
                include_mock=include_mock or provider_name == "mock",
                only=[provider_name],
                timeout=60.0,
                convert_currency=True,
                client=http,
                smart_route=False,
            )
            offer = _cheapest(result.offers)
            if offer:
                if offer.currency.upper() != req.currency.upper():
                    offer = await convert_offer(http, offer, req.currency)
                offer = attach_links_from_leg(
                    offer,
                    origin=origin,
                    destination=dest,
                    depart=depart,
                    currency=req.currency,
                    adults=req.adults,
                )
                try:
                    stats = route_stats(origin, dest, currency=req.currency)
                    score, label = stats.deal_score(offer.price)
                    pd = price_display(
                        offer.price,
                        offer.currency,
                        deal_label=label,
                        deal_score=score,
                        median=stats.median,
                    )
                except Exception:
                    pd = price_display(offer.price, offer.currency)
                    score, label = None, None
                offer = offer.model_copy(
                    update={
                        "display_price": pd.full,
                        "display_price_base": pd.base,
                        "price_sign": pd.sign or None,
                        "price_glyph": pd.glyph or None,
                        "price_tone": pd.tone,
                        "deal_score": score,
                        "deal_label": label,
                    }
                )
                try:
                    record_leg(
                        origin=origin,
                        destination=dest,
                        depart=depart,
                        offer=offer,
                        currency=req.currency,
                    )
                except Exception:
                    pass
                gurl = offer.google_flights_url or google_flights_url(
                    origin, dest, depart, currency=req.currency, adults=req.adults
                )
                return PricedLeg(
                    from_iata=origin,
                    to_iata=dest,
                    depart_date=depart,
                    offer=offer,
                    google_flights_url=gurl,
                    booking_url=offer.booking_url or gurl,
                )
            failed = "; ".join(
                f"{r.provider}: {r.error or 'no offers'}" for r in result.results
            )
            errors.append(failed or f"{provider_name}: no offers")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider_name}: {exc}")

    gurl = google_flights_url(
        origin, dest, depart, currency=req.currency, adults=req.adults
    )
    return PricedLeg(
        from_iata=origin,
        to_iata=dest,
        depart_date=depart,
        error=" | ".join(errors) if errors else "no offers",
        google_flights_url=gurl,
        booking_url=gurl,
    )


def _adventure_score(
    idea: StopoverIdea,
    total: float,
    direct: float | None,
    *,
    col_rank_delta: float = 0.0,
) -> float:
    score = 55.0
    score += min(25.0, len(idea.vibe_tags) * 6)
    if idea.why:
        score += 5
    if direct and direct > 0 and total > 0:
        ratio = total / direct
        if ratio < 0.85:
            score += 20
        elif ratio < 1.05:
            score += 12
        elif ratio < 1.25:
            score += 4
        else:
            score -= min(25.0, (ratio - 1.25) * 30)
    if 2 <= idea.stay_days <= 5:
        score += 5
    # Settings COL budget: under target boosts, over tolerance band penalizes
    score += float(col_rank_delta or 0.0)
    return max(0.0, min(100.0, score))


def filter_ideas(
    ideas: list[StopoverIdea],
    req: AdventureRequest,
) -> list[StopoverIdea]:
    avoid = normalize_avoid_list(req.avoid_countries)
    origin = req.origin.upper()
    dest = req.destination.upper()
    out: list[StopoverIdea] = []
    seen: set[str] = set()
    for idea in ideas:
        code = idea.iata.upper()
        if code in (origin, dest) or code in seen:
            continue
        if is_avoided_iata(code, avoid):
            continue
        if idea.country and idea.country.upper() in avoid:
            continue
        seen.add(code)
        out.append(idea.model_copy(update={"iata": code}))
        if len(out) >= req.max_candidates:
            break
    return out


def _is_getaway(req: AdventureRequest) -> bool:
    kind = (req.trip_kind or "").lower().strip()
    if kind in ("getaway", "round_trip", "roundtrip", "escape", "from_home"):
        return True
    return req.origin.upper() == req.destination.upper()


def seed_ideas(req: AdventureRequest) -> list[StopoverIdea]:
    origin = req.origin.upper()
    dest = req.destination.upper()
    avoid = normalize_avoid_list(req.avoid_countries)
    visited = {
        c.upper()
        for c in normalize_country_list(req.visited_countries or [], max_n=250)
    }
    getaway = _is_getaway(req)
    stay = max(
        req.min_stop_days,
        min(req.max_stop_days, (req.min_stop_days + req.max_stop_days) // 2),
    )
    ideas: list[StopoverIdea] = []
    for row in SEED_STOPOVERS:
        code = row["iata"]
        if code == origin or (not getaway and code == dest):
            continue
        if getaway and code == dest and dest == origin:
            pass  # home is already excluded via origin
        cc = (row.get("country") or "").upper()
        if cc and cc in avoid:
            continue
        if is_avoided_iata(code, avoid):
            continue
        # Getaways: respect passport map ("not anywhere I've been")
        if getaway and visited:
            stop_cc = cc or (country_for_iata(code) or "")
            if stop_cc and stop_cc.upper() in visited:
                continue
        ideas.append(
            StopoverIdea(
                iata=code,
                city=row["city"],
                stay_days=stay,
                why=row["why"],
                vibe_tags=list(row.get("vibe_tags") or []),
                country=cc or None,
                source="seed",
            )
        )
    vibe = (req.vibe or "").lower()
    if vibe and vibe not in ("adventure", "any", "chaos", ""):
        tagged = [i for i in ideas if any(vibe in t or t in vibe for t in i.vibe_tags)]
        if tagged:
            ideas = tagged + [i for i in ideas if i not in tagged]
    return ideas[: req.max_candidates]


async def plan_adventure(
    req: AdventureRequest,
    ideas: list[StopoverIdea],
    *,
    settings: Settings | None = None,
    include_mock: bool = False,
) -> AdventureResult:
    settings = settings or get_settings()
    trip_kind = (req.trip_kind or "detour").lower().strip()
    if req.origin.upper() == req.destination.upper():
        trip_kind = "getaway"
    req = req.model_copy(
        update={
            "origin": req.origin.upper(),
            "destination": req.destination.upper(),
            "currency": req.currency.upper(),
            "avoid_countries": normalize_avoid_list(req.avoid_countries),
            "visited_countries": normalize_country_list(
                req.visited_countries or [], max_n=250
            ),
            "trip_kind": trip_kind,
            # No meaningful A→A direct baseline for getaways
            "include_direct": False if trip_kind == "getaway" else req.include_direct,
        }
    )
    ideas = filter_ideas(ideas, req)
    # Extra filter for getaway: drop visited countries even if Grok suggested them
    if _is_getaway(req) and req.visited_countries:
        visited = {c.upper() for c in req.visited_countries}
        filtered: list[StopoverIdea] = []
        for idea in ideas:
            cc = (idea.country or country_for_iata(idea.iata) or "").upper()
            if cc and cc in visited:
                continue
            filtered.append(idea)
        ideas = filtered
    errors: list[str] = []
    itineraries: list[AdventureItinerary] = []
    direct_price: float | None = None
    if _is_getaway(req):
        errors.append(
            "Getaway mode: home base → new place → home "
            f"({req.origin} round-trip). Candidates are destinations, not mid-route stops."
        )

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
        # Enrich stopovers with AviationStack airport metadata (free-tier friendly)
        if settings.aviationstack_key and ideas:
            try:
                from yonder.providers.aviationstack import AviationStackProvider

                av = AviationStackProvider(settings, http)
                ideas = await av.enrich_stopovers(ideas)
                errors.append(
                    "AviationStack: enriched stopover airports (not used for fares)"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"AviationStack enrichment skipped: {exc}")

        # Batch ground-cost estimates (Grok + 60-day cache) — one call for all stops
        ground_batch: dict = {}
        if ideas:
            try:
                from yonder.daily_costs import estimate_batch_for_stops

                ground_batch = await estimate_batch_for_stops(
                    settings,
                    origin_iata=req.origin,
                    stops=[
                        (i.iata, i.country, i.city) for i in ideas
                    ],
                    currency=req.currency,
                    vibe=req.vibe,
                )
                errors.append(
                    "Ground costs: lean day bag (hotel+food+transit+1–2 culture; "
                    "local→your currency FX; cached 60d)"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Ground cost estimates skipped: {exc}")
                ground_batch = {}

        only = await pick_pricing_provider(settings, http, include_mock)
        pricing_name = only[0] if only else None
        fallback_chain = list(only or [])
        if settings.duffel_is_test and pricing_name == "serpapi_google_flights":
            errors.append(
                "Using SerpAPI (live Google Flights snapshots) — Duffel is a "
                "duffel_test sandbox token so its fares would not match Google"
            )
        elif settings.duffel_is_test and pricing_name == "duffel":
            errors.append(
                "WARNING: Duffel SANDBOX token — prices are demo data, not real market. "
                "Google Flights links show live fares and will disagree."
            )

        if pricing_name:
            errors.append(
                f"Primary fare API: {pricing_name} "
                f"(fallbacks: {', '.join(fallback_chain[1:4]) or 'none'})"
            )
        else:
            errors.append(
                "No active fare providers — add Duffel live / SerpAPI / Amadeus or enable mock"
            )

        if req.include_direct:
            direct_leg = await _price_leg(
                req.origin,
                req.destination,
                req.depart_date,
                req,
                settings=settings,
                include_mock=include_mock,
                only=only,
                http=http,
                fallback_chain=fallback_chain,
            )
            if direct_leg.offer:
                direct_price = direct_leg.offer.price
                g_direct = direct_leg.google_flights_url or google_flights_url(
                    req.origin,
                    req.destination,
                    req.depart_date,
                    currency=req.currency,
                    adults=req.adults,
                )
                d_off = direct_leg.offer
                d_pd = price_display(
                    d_off.price,
                    d_off.currency,
                    deal_label=d_off.deal_label,
                    deal_score=d_off.deal_score,
                )
                itineraries.append(
                    _apply_theme(
                        AdventureItinerary(
                            kind="direct",
                            title=f"Direct-ish {format_route(req.origin, req.destination)}",
                            total_price=d_off.price,
                            currency=d_off.currency,
                            legs=[direct_leg],
                            why="Baseline: get there without a multi-day detour",
                            adventure_score=15.0,
                            notes=[
                                "Signal from free APIs — confirm on Google before buying.",
                                f"Source: {pricing_name or 'providers'}",
                                "▼ green = under typical history · ▲ red = over",
                            ],
                            bookable_separately=False,
                            google_flights_url=g_direct,
                            booking_url=direct_leg.booking_url or g_direct,
                            kayak_url=kayak_url(
                                req.origin, req.destination, req.depart_date
                            ),
                            display_price=d_pd.full,
                            display_price_base=d_pd.base,
                            price_sign=d_pd.sign or None,
                            price_glyph=d_pd.glyph or None,
                            price_tone=d_pd.tone,
                        )
                    )
                )
            elif direct_leg.error:
                errors.append(f"Direct: {direct_leg.error}")

        async def price_stopover(idea: StopoverIdea) -> AdventureItinerary | None:
            stay = max(req.min_stop_days, min(req.max_stop_days, idea.stay_days))
            leg2_date = req.depart_date + timedelta(days=stay)
            if req.arrive_by and leg2_date > req.arrive_by:
                max_stay = (req.arrive_by - req.depart_date).days
                if max_stay < req.min_stop_days:
                    return None
                stay = min(stay, max_stay)
                leg2_date = req.depart_date + timedelta(days=stay)
                idea = idea.model_copy(update={"stay_days": stay})

            leg1, leg2 = await asyncio.gather(
                _price_leg(
                    req.origin,
                    idea.iata,
                    req.depart_date,
                    req,
                    settings=settings,
                    include_mock=include_mock,
                    only=only,
                    http=http,
                    fallback_chain=fallback_chain,
                ),
                _price_leg(
                    idea.iata,
                    req.destination,
                    leg2_date,
                    req,
                    settings=settings,
                    include_mock=include_mock,
                    only=only,
                    http=http,
                    fallback_chain=fallback_chain,
                ),
            )
            getaway = _is_getaway(req)
            if getaway:
                notes = [
                    f"{stay}-day getaway in {format_place(idea.iata, idea.city)}",
                    f"Round-trip signal: {req.origin} → stop → {req.origin}",
                    "Two separate one-way tickets — confirm on Google before buying",
                    f"Priced via {pricing_name or 'providers'} in {req.currency}",
                ]
            else:
                notes = [
                    f"{stay}-day intentional stop in {format_place(idea.iata, idea.city)}",
                    "Two separate one-way tickets — self-transfer risk",
                    f"Priced via {pricing_name or 'providers'} in {req.currency}",
                ]
            ground_fields: dict = {}
            col_delta = 0.0
            try:
                from yonder.daily_costs import compare_for_stop

                if ground_batch:
                    gcmp = compare_for_stop(
                        ground_batch, stop_iata=idea.iata, stay_days=stay
                    )
                    if gcmp:
                        notes.extend(gcmp.note_lines)
                        col_delta = float(gcmp.rank_delta or 0.0)
                        compare_line = gcmp.ground_compare_line or (
                            f"{gcmp.display_daily_stop}/day in {gcmp.stop_name} vs "
                            f"{gcmp.display_daily_origin}/day at home ({gcmp.origin_name})"
                        )
                        ground_fields = {
                            "ground_daily_stop": gcmp.daily_stop,
                            "ground_daily_origin": gcmp.daily_origin,
                            "ground_total": gcmp.ground_total,
                            "ground_display": (
                                f"+ {gcmp.display_ground} ground "
                                f"({stay}× {gcmp.display_daily_stop}/day)"
                            ),
                            "ground_compare_line": compare_line,
                            "ground_budget_status": gcmp.budget_status,
                            "ground_budget_line": gcmp.budget_line or None,
                            "ground_rank_delta": col_delta,
                        }
            except Exception:
                pass
            multi_url = google_flights_multi(
                [
                    (req.origin, idea.iata, req.depart_date),
                    (idea.iata, req.destination, leg2_date),
                ],
                currency=req.currency,
            )
            if leg1.error or leg2.error:
                err_bits = [x for x in (leg1.error, leg2.error) if x]
                title = (
                    f"{format_place(req.origin)} ↺ "
                    f"{format_place(idea.iata, idea.city)} ({stay}d getaway)"
                    if getaway
                    else (
                        f"{format_place(req.origin)} → "
                        f"{format_place(idea.iata, idea.city)} ({stay}d) → "
                        f"{format_place(req.destination)}"
                    )
                )
                return _apply_theme(
                    AdventureItinerary(
                        kind="getaway" if getaway else "stopover",
                        title=title,
                        total_price=None,
                        currency=req.currency,
                        stop_city=idea.city,
                        stop_iata=idea.iata,
                        stay_days=stay,
                        why=idea.why,
                        vibe_tags=idea.vibe_tags,
                        legs=[leg1, leg2],
                        adventure_score=0,
                        notes=notes + [f"Pricing incomplete: {'; '.join(err_bits)}"],
                        google_flights_url=multi_url,
                        booking_url=multi_url,
                        theme_country=idea.country,
                        **ground_fields,
                    )
                )

            assert leg1.offer and leg2.offer
            # Both should already be in req.currency
            total = leg1.offer.price + leg2.offer.price
            cur = req.currency
            if leg1.offer.currency.upper() != cur or leg2.offer.currency.upper() != cur:
                notes.append("Currency conversion applied where providers returned other FX")
            delta = (total - direct_price) if direct_price is not None else None
            score = _adventure_score(
                idea, total, direct_price, col_rank_delta=col_delta
            )
            if delta is not None and delta <= 0:
                notes.append(
                    f"Flight signal {format_approx(abs(delta), cur)} under direct (same source)"
                )
            elif delta is not None:
                notes.append(
                    f"Flight signal {format_approx(delta, cur)} over direct "
                    f"(~{delta / max(direct_price, 1) * 100:.0f}% adventure premium)"
                )
            notes.append("Book each leg separately — confirm on Google before buying")

            # ▼ green under direct · ▲ red over direct
            tot_pd = price_display(total, cur, vs_delta=delta)

            all_in = None
            if ground_fields.get("ground_total") is not None:
                all_in = format_approx(
                    total + float(ground_fields["ground_total"]), cur
                )
                ground_fields["all_in_display"] = (
                    f"All-in signal {all_in} (flights + ground est.)"
                )

            title = (
                f"{format_place(req.origin)} ↺ "
                f"{format_place(idea.iata, idea.city)} ({stay}d getaway)"
                if getaway
                else (
                    f"{format_place(req.origin)} → "
                    f"{format_place(idea.iata, idea.city)} ({stay} nights) → "
                    f"{format_place(req.destination)}"
                )
            )
            return _apply_theme(
                AdventureItinerary(
                    kind="getaway" if getaway else "stopover",
                    title=title,
                    total_price=round(total, 2),
                    currency=cur,
                    stop_city=idea.city,
                    stop_iata=idea.iata,
                    stay_days=stay,
                    why=idea.why,
                    vibe_tags=idea.vibe_tags,
                    legs=[leg1, leg2],
                    vs_direct_delta=round(delta, 2) if delta is not None else None,
                    adventure_score=round(score, 1),
                    notes=notes,
                    bookable_separately=True,
                    google_flights_url=multi_url,
                    booking_url=multi_url,
                    kayak_url=kayak_url(req.origin, idea.iata, req.depart_date),
                    display_price=tot_pd.full,
                    display_price_base=tot_pd.base,
                    price_sign=tot_pd.sign or None,
                    price_glyph=tot_pd.glyph or None,
                    price_tone=tot_pd.tone,
                    theme_country=idea.country,
                    **ground_fields,
                )
            )

        stop_results = await asyncio.gather(*(price_stopover(i) for i in ideas))
        for it in stop_results:
            if it is not None:
                itineraries.append(it)

    complete = [i for i in itineraries if i.total_price is not None]
    incomplete = [i for i in itineraries if i.total_price is None]
    complete.sort(
        key=lambda i: (
            0 if i.kind == "stopover" else 1,
            -i.adventure_score,
            i.total_price or 1e12,
        )
    )
    if req.avoid_countries:
        errors.append(
            "Avoiding countries: " + ", ".join(req.avoid_countries)
        )
    return AdventureResult(
        request=req,
        ideas=ideas,
        itineraries=complete + incomplete,
        direct_price=direct_price,
        errors=errors,
        pricing_provider=pricing_name,
    )


async def reprice_itinerary(
    itinerary: AdventureItinerary,
    *,
    adults: int = 1,
    currency: str | None = None,
    cabin: CabinClass = CabinClass.ECONOMY,
    settings: Settings | None = None,
    include_mock: bool = False,
) -> tuple[AdventureItinerary, dict[str, Any]]:
    """Re-fetch fares for each leg. Never wipe a good snapshot.

    Per leg: use live offer when available; otherwise keep the previous offer
    and mark that leg as fallback. Returns (itinerary, refresh_meta).
    """
    settings = settings or get_settings()
    cur = (currency or itinerary.currency or settings.default_currency or "CAD").upper()
    prev_total = itinerary.total_price
    meta: dict[str, Any] = {
        "ok": False,
        "status": "failed",  # live | mixed | snapshot | failed
        "provider": None,
        "prev_total": prev_total,
        "new_total": None,
        "delta": None,
        "live_legs": 0,
        "fallback_legs": 0,
        "message": "",
    }

    if not itinerary.legs:
        notes = list(itinerary.notes) + ["Refresh failed: no legs on this itinerary"]
        meta["message"] = "No legs to reprice"
        return (
            itinerary.model_copy(update={"notes": notes}),
            meta,
        )

    first = itinerary.legs[0]
    last = itinerary.legs[-1]
    req = AdventureRequest(
        origin=first.from_iata,
        destination=last.to_iata,
        depart_date=first.depart_date,
        adults=max(1, min(9, adults)),
        currency=cur,
        cabin=cabin,
        trip_kind="getaway" if first.from_iata == last.to_iata else "detour",
    )

    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as http:
        only = await pick_pricing_provider(settings, http, include_mock)
        fallback_chain = list(only or [])
        pricing_name = only[0] if only else None
        meta["provider"] = pricing_name
        merged_legs: list[PricedLeg] = []
        live_n = 0
        fallback_n = 0

        for leg in itinerary.legs:
            pl = await _price_leg(
                leg.from_iata,
                leg.to_iata,
                leg.depart_date,
                req,
                settings=settings,
                include_mock=include_mock,
                only=only,
                http=http,
                fallback_chain=fallback_chain,
            )
            if pl.offer:
                live_n += 1
                merged_legs.append(pl)
            elif leg.offer:
                # Keep last-known fare for this leg
                fallback_n += 1
                gurl = (
                    pl.google_flights_url
                    or leg.google_flights_url
                    or google_flights_url(
                        leg.from_iata,
                        leg.to_iata,
                        leg.depart_date,
                        currency=cur,
                        adults=req.adults,
                    )
                )
                err = pl.error or "no live offer"
                merged_legs.append(
                    leg.model_copy(
                        update={
                            "error": f"live failed ({err[:120]}) — showing last known",
                            "google_flights_url": gurl,
                            "booking_url": leg.booking_url or gurl,
                        }
                    )
                )
            else:
                fallback_n += 1
                merged_legs.append(pl)

    meta["live_legs"] = live_n
    meta["fallback_legs"] = fallback_n

    notes = [
        n
        for n in itinerary.notes
        if not n.startswith("Fare refreshed")
        and not n.startswith("Refresh incomplete")
        and not n.startswith("Priced via")
        and not n.startswith("Last known")
        and not n.startswith("Price check")
    ]
    prov = pricing_name or "providers"

    priced = [pl for pl in merged_legs if pl.offer]
    multi_url = itinerary.google_flights_url
    if len(merged_legs) >= 2:
        multi_url = (
            google_flights_multi(
                [(pl.from_iata, pl.to_iata, pl.depart_date) for pl in merged_legs],
                currency=cur,
            )
            or multi_url
        )
    elif merged_legs and merged_legs[0].google_flights_url:
        multi_url = merged_legs[0].google_flights_url

    if not priced:
        notes.insert(
            0,
            f"Price check failed via {prov} — kept empty; try again or open Google Flights",
        )
        meta["status"] = "failed"
        meta["message"] = "No live fares; no snapshot to fall back to"
        meta["ok"] = False
        return (
            _apply_theme(
                itinerary.model_copy(
                    update={
                        "legs": merged_legs,
                        "notes": notes,
                        "google_flights_url": multi_url,
                        "booking_url": multi_url or itinerary.booking_url,
                    }
                )
            ),
            meta,
        )

    total = round(sum(float(pl.offer.price) for pl in priced if pl.offer), 2)
    meta["new_total"] = total
    if prev_total is not None:
        meta["delta"] = round(total - float(prev_total), 2)

    if live_n == len(merged_legs) and fallback_n == 0:
        meta["status"] = "live"
        meta["ok"] = True
        notes.insert(0, f"Fare refreshed via {prov} in {cur} (all legs live)")
        if meta["delta"] is None:
            meta["message"] = f"Live {format_approx(total, cur)}"
        elif meta["delta"] == 0:
            meta["message"] = f"Live {format_approx(total, cur)} · unchanged"
        elif meta["delta"] < 0:
            meta["message"] = (
                f"Live {format_approx(total, cur)} · "
                f"down {format_approx(abs(meta['delta']), cur)}"
            )
        else:
            meta["message"] = (
                f"Live {format_approx(total, cur)} · "
                f"up {format_approx(meta['delta'], cur)}"
            )
    elif live_n > 0:
        meta["status"] = "mixed"
        meta["ok"] = True
        notes.insert(
            0,
            f"Partial refresh via {prov}: {live_n} live leg(s), "
            f"{fallback_n} last-known — total mixes both",
        )
        meta["message"] = (
            f"Mixed {format_approx(total, cur)} "
            f"({live_n} live / {fallback_n} snapshot)"
        )
    else:
        # All legs fell back to previous offers
        meta["status"] = "snapshot"
        meta["ok"] = True
        notes.insert(
            0,
            f"Live check via {prov} failed — showing last-known fares "
            f"({format_approx(total, cur)})",
        )
        meta["message"] = f"Last known {format_approx(total, cur)} (live failed)"

    all_in_display = itinerary.all_in_display
    if itinerary.ground_total is not None:
        all_in_display = (
            f"All-in signal {format_approx(total + float(itinerary.ground_total), cur)} "
            f"(flights + ground est.)"
        )

    vs_delta = meta["delta"]
    tot_pd = price_display(total, cur, vs_delta=vs_delta)

    return (
        _apply_theme(
            itinerary.model_copy(
                update={
                    "legs": merged_legs,
                    "total_price": total,
                    "display_price": tot_pd.full,
                    "display_price_base": tot_pd.base,
                    "price_sign": tot_pd.sign or None,
                    "price_glyph": tot_pd.glyph or None,
                    "price_tone": tot_pd.tone,
                    "currency": cur,
                    "notes": notes,
                    "google_flights_url": multi_url,
                    "booking_url": multi_url or itinerary.booking_url,
                    "all_in_display": all_in_display,
                }
            )
        ),
        meta,
    )
