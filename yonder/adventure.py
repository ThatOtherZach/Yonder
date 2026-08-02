from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from yonder.config import Settings, get_settings
from yonder.countries import (
    city_for_iata,
    country_for_iata,
    format_place,
    format_route,
    is_avoided_iata,
    normalize_avoid_list,
    normalize_country_list,
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

# Interesting hubs used as intentional stopovers (seed when Grok offline / passport filter)
SEED_STOPOVERS: list[dict[str, Any]] = [
    {"iata": "ZRH", "city": "Zurich", "country": "CH", "why": "Swiss Alps gateway — classic long-haul detour", "vibe_tags": ["alps", "city", "trains", "crisp"]},
    {"iata": "IST", "city": "Istanbul", "country": "TR", "why": "Turkish Airlines hub with easy multi-day city break", "vibe_tags": ["city", "food", "bazaar", "ancient", "nostalgic", "velvet", "vivid", "hazy", "goldenhour", "moody"]},
    {"iata": "LIS", "city": "Lisbon", "country": "PT", "why": "Atlantic TAP stopover city — cheap Europe beach vibes", "vibe_tags": ["city", "food", "coast", "cheap", "nostalgic", "whimsical", "warmnights", "tender", "sleepy", "seasalt", "goldenhour", "serene"]},
    {"iata": "KEF", "city": "Reykjavik", "country": "IS", "why": "Icelandair-style layover adventure — nature + hot springs", "vibe_tags": ["nature", "north", "moody", "stormy", "rugged", "crisp", "serene", "feral", "untamed", "luminous", "whimsical"]},
    {"iata": "DOH", "city": "Doha", "country": "QA", "why": "Qatar hub with strong long-haul deals", "vibe_tags": ["city", "modern", "safe", "opulent"]},
    {"iata": "AMS", "city": "Amsterdam", "country": "NL", "why": "KLM hub — canals, bikes, easy city hop", "vibe_tags": ["city", "culture", "whimsical", "moody"]},
    {"iata": "CDG", "city": "Paris", "country": "FR", "why": "Obvious but underrated as a *planned* multi-day stop", "vibe_tags": ["city", "food", "art", "velvet", "luminous", "opulent", "nostalgic"]},
    {"iata": "MEX", "city": "Mexico City", "country": "MX", "why": "North America detour with huge food culture", "vibe_tags": ["city", "food", "culture", "cheap", "vivid", "hazy"]},
    {"iata": "CUN", "city": "Cancun", "country": "MX", "why": "Beach break between Canadian coasts", "vibe_tags": ["beach", "relax", "warmnights", "seasalt"]},
    {"iata": "YUL", "city": "Montreal", "country": "CA", "why": "Domestic-ish culture detour if routing allows", "vibe_tags": ["city", "food"]},
    {"iata": "YYC", "city": "Calgary", "country": "CA", "why": "Rockies access between east and west Canada", "vibe_tags": ["nature", "mountains", "rugged", "crisp", "feral", "untamed"]},
    {"iata": "LAX", "city": "Los Angeles", "country": "US", "why": "Sun + sprawl stop between continents/coasts", "vibe_tags": ["city", "sun", "electric", "goldenhour"]},
    {"iata": "NRT", "city": "Tokyo Narita", "country": "JP", "why": "Japan stopover when Pacific routing is wild", "vibe_tags": ["city", "food", "neon", "safe", "whimsical", "electric", "serene"]},
    {"iata": "ICN", "city": "Seoul", "country": "KR", "why": "Incheon hub with great food and city energy", "vibe_tags": ["city", "food", "safe", "electric"]},
    {"iata": "DXB", "city": "Dubai", "country": "AE", "why": "Mega-hub desert city for long east-west routes", "vibe_tags": ["city", "modern", "safe", "electric", "opulent", "luminous"]},
    {"iata": "BCN", "city": "Barcelona", "country": "ES", "why": "Mediterranean detour worth the extra days", "vibe_tags": ["city", "beach", "food", "velvet", "warmnights", "seasalt", "goldenhour", "whimsical"]},
    {"iata": "LHR", "city": "London", "country": "GB", "why": "Classic hub — museums, pubs, easy connections", "vibe_tags": ["city", "culture", "moody", "stormy"]},
    {"iata": "FRA", "city": "Frankfurt", "country": "DE", "why": "Lufthansa mega-hub for Europe hops", "vibe_tags": ["city", "hub"]},
    {"iata": "SIN", "city": "Singapore", "country": "SG", "why": "Jewel of SE Asia stopovers — safe + food heaven", "vibe_tags": ["city", "food", "modern", "safe", "cheap", "electric", "lush", "luminous", "opulent"]},
    {"iata": "BKK", "city": "Bangkok", "country": "TH", "why": "Street food + easy multi-day chaos", "vibe_tags": ["city", "food", "cheap", "velvet", "warmnights", "vivid", "lush", "hazy", "electric"]},
    # Extra getaway seeds for "cheap + food + safe + not visited" after a full passport map
    {"iata": "HAN", "city": "Hanoi", "country": "VN", "why": "Street food capital with low daily spend", "vibe_tags": ["city", "food", "cheap", "safe", "nostalgic", "ancient", "hazy", "vivid"]},
    {"iata": "SGN", "city": "Ho Chi Minh City", "country": "VN", "why": "Vietnamese food chaos, still traveler-friendly", "vibe_tags": ["city", "food", "cheap", "gritty", "raw", "hazy", "vivid"]},
    {"iata": "KUL", "city": "Kuala Lumpur", "country": "MY", "why": "Cheap hawker food + modern safe core", "vibe_tags": ["city", "food", "cheap", "safe", "electric"]},
    {"iata": "DPS", "city": "Denpasar / Bali", "country": "ID", "why": "Island food + beach without wrecking the budget", "vibe_tags": ["beach", "food", "cheap", "relax", "serene", "lush"]},
    {"iata": "CGK", "city": "Jakarta", "country": "ID", "why": "Huge food scene, low ground costs", "vibe_tags": ["city", "food", "cheap", "gritty", "raw"]},
    {"iata": "MNL", "city": "Manila", "country": "PH", "why": "Pacific food stop with friendly spend", "vibe_tags": ["city", "food", "cheap", "gritty", "raw"]},
    {"iata": "LIM", "city": "Lima", "country": "PE", "why": "World-class food capital, strong value", "vibe_tags": ["city", "food", "cheap", "culture", "ancient"]},
    {"iata": "BOG", "city": "Bogotá", "country": "CO", "why": "Andean food city with improving safety in core areas", "vibe_tags": ["city", "food", "cheap", "culture", "gritty", "rugged", "raw", "vivid", "lush"]},
    {"iata": "MDE", "city": "Medellín", "country": "CO", "why": "Spring climate + food scene, tourist-core safety", "vibe_tags": ["city", "food", "cheap", "gritty", "raw", "lush"]},
    {"iata": "SCL", "city": "Santiago", "country": "CL", "why": "Stable South America with solid food", "vibe_tags": ["city", "food", "safe"]},
    {"iata": "EZE", "city": "Buenos Aires", "country": "AR", "why": "Steak + wine city with soft currency value", "vibe_tags": ["city", "food", "cheap", "culture", "nostalgic", "velvet", "goldenhour"]},
    {"iata": "ATH", "city": "Athens", "country": "GR", "why": "Mediterranean food + ruins, still good value", "vibe_tags": ["city", "food", "cheap", "culture", "safe", "ancient", "sacred"]},
    {"iata": "ZAG", "city": "Zagreb", "country": "HR", "why": "Quiet Central Europe value + food", "vibe_tags": ["city", "food", "cheap", "safe", "nostalgic", "tender", "sleepy"]},
    {"iata": "OTP", "city": "Bucharest", "country": "RO", "why": "Low COL European capital", "vibe_tags": ["city", "food", "cheap", "safe", "nostalgic", "gritty", "moody"]},
    {"iata": "SOF", "city": "Sofia", "country": "BG", "why": "Cheap Balkans capital with solid food", "vibe_tags": ["city", "food", "cheap", "safe", "gritty", "sleepy"]},
    {"iata": "CMN", "city": "Casablanca", "country": "MA", "why": "North Africa food + moderate spend", "vibe_tags": ["city", "food", "cheap", "culture", "ancient", "vivid", "hazy"]},
    {"iata": "CPT", "city": "Cape Town", "country": "ZA", "why": "Food + nature with well-trodden tourist circuits", "vibe_tags": ["city", "food", "nature", "cheap", "rugged", "feral", "untamed", "raw", "gritty", "stormy", "seasalt", "goldenhour"]},
    {"iata": "CMB", "city": "Colombo", "country": "LK", "why": "Island food and low daily costs", "vibe_tags": ["city", "food", "cheap", "beach", "serene", "tender", "sleepy"]},
    {"iata": "TPE", "city": "Taipei", "country": "TW", "why": "Night markets + very safe city", "vibe_tags": ["city", "food", "safe", "cheap", "whimsical", "electric"]},
    {"iata": "AKL", "city": "Auckland", "country": "NZ", "why": "Safe Pacific city break", "vibe_tags": ["city", "nature", "safe", "rugged", "feral", "untamed", "crisp", "seasalt"]},
    {"iata": "HEL", "city": "Helsinki", "country": "FI", "why": "Nordic safe city (higher COL)", "vibe_tags": ["city", "safe", "culture", "moody", "stormy", "crisp", "tender"]},
    # Domestic US hubs — surface for low-XP travellers before long-haul international
    {"iata": "ORD", "city": "Chicago", "country": "US", "why": "Midwest hub with deep food and culture scene", "vibe_tags": ["city", "food", "culture", "electric", "gritty"]},
    {"iata": "SEA", "city": "Seattle", "country": "US", "why": "Pacific Northwest city with coffee, tech, and mountains nearby", "vibe_tags": ["city", "nature", "moody", "crisp", "mountains"]},
    {"iata": "BOS", "city": "Boston", "country": "US", "why": "Historic East Coast city — walkable and safe", "vibe_tags": ["city", "culture", "safe", "ancient", "nostalgic"]},
    {"iata": "DCA", "city": "Washington DC", "country": "US", "why": "Capital with free world-class museums and safe core", "vibe_tags": ["city", "culture", "safe", "ancient", "opulent"]},
    {"iata": "ATL", "city": "Atlanta", "country": "US", "why": "Southern food and music hub, major domestic connector", "vibe_tags": ["city", "food", "culture", "electric", "warmnights"]},
    {"iata": "DEN", "city": "Denver", "country": "US", "why": "Rocky Mountain gateway — outdoors and craft beer", "vibe_tags": ["city", "nature", "mountains", "rugged", "crisp"]},
    {"iata": "DFW", "city": "Dallas", "country": "US", "why": "Texas hub with big BBQ culture and easy connections", "vibe_tags": ["city", "food", "culture", "warmnights", "goldenhour"]},
    {"iata": "HNL", "city": "Honolulu", "country": "US", "why": "Pacific island paradise — beach and sun without a passport", "vibe_tags": ["beach", "relax", "warmnights", "seasalt", "lush", "goldenhour"]},
    {"iata": "MSY", "city": "New Orleans", "country": "US", "why": "Jazz, food, and festival city — one of a kind US destination", "vibe_tags": ["city", "food", "culture", "electric", "warmnights", "vivid", "nostalgic"]},
    {"iata": "BNA", "city": "Nashville", "country": "US", "why": "Music City — live shows, hot chicken, and easy domestic hop", "vibe_tags": ["city", "food", "culture", "electric", "warmnights"]},
    {"iata": "SAN", "city": "San Diego", "country": "US", "why": "California beach city — sun, tacos, and laid-back vibes", "vibe_tags": ["beach", "city", "relax", "warmnights", "seasalt", "goldenhour"]},
    {"iata": "AUS", "city": "Austin", "country": "US", "why": "Live music capital with great food and tech energy", "vibe_tags": ["city", "food", "culture", "electric", "warmnights"]},
    # Domestic Canadian hubs — additional domestic options for CA-based travellers
    {"iata": "YOW", "city": "Ottawa", "country": "CA", "why": "Capital city with museums and safe walkable core", "vibe_tags": ["city", "culture", "safe", "crisp", "tender"]},
    {"iata": "YQB", "city": "Quebec City", "country": "CA", "why": "French-Canadian walled city — Europe feel without the flight", "vibe_tags": ["city", "culture", "food", "nostalgic", "ancient", "moody", "crisp"]},
    {"iata": "YEG", "city": "Edmonton", "country": "CA", "why": "Alberta hub with festivals and northern-lights access", "vibe_tags": ["city", "culture", "nature", "crisp", "rugged"]},
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
    max_candidates: int = Field(default=5, ge=2, le=5)
    vibe: str = "adventure"
    prompt: str = ""
    include_direct: bool = True
    avoid_countries: list[str] = Field(default_factory=list)  # ISO2, max 10
    # detour = A → stop → B · getaway = home → X → home (open destination)
    trip_kind: str = "detour"
    visited_countries: list[str] = Field(default_factory=list)  # ISO2 passport map
    proximity_mode: bool = False  # True when query contains "not too far" / "nearby" etc.


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
    # Multi-stop: ordered list of intermediate stops (empty for single-stop/direct)
    # Each entry: {iata, city, country, stay_days}
    stops: list[dict] = Field(default_factory=list)
    # True when this card is the rescue fallback chain
    rescue: bool = False


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

    # No live health probe — too slow for the 30s budget
    await choose_providers(
        settings,
        client,
        mode="adventure_leg",
        need=2,
        include_mock=include_mock,
        force_all=False,
        probe=False,
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
    # Only try primary + mock — long fallback chains blow the 30s budget
    short_chain = chain[:1]
    if include_mock and "mock" not in short_chain:
        short_chain.append("mock")
    for provider_name in short_chain:
        q = SearchQuery(
            origin=origin.upper(),
            destination=dest.upper(),
            depart_date=depart,
            return_date=None,
            adults=req.adults,
            cabin=req.cabin,
            currency=req.currency,
            max_results=3,
            nonstop_only=False,
        )
        try:
            result = await search_flights(
                q,
                settings=settings,
                include_mock=include_mock or provider_name == "mock",
                only=[provider_name],
                timeout=8.0,
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
                        model_source=settings.model_source_label() or None,
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


# Well-connected global hubs used as rescue chain candidates (ordered by connectivity)
_RESCUE_HUBS = [
    "LHR", "CDG", "FRA", "AMS", "IST", "DXB", "DOH",
    "SIN", "ICN", "NRT", "LAX", "JFK", "ATL", "ORD",
]


async def _price_multi_stop_chain(
    req: AdventureRequest,
    stop_ideas: list[StopoverIdea],
    *,
    settings: Settings,
    include_mock: bool,
    http: httpx.AsyncClient,
    only: list[str] | None,
    fallback_chain: list[str],
    direct_price: float | None,
    ground_batch: dict,
    cancel_id: str | None = None,
    rescue: bool = False,
    pricing_name: str | None = None,
) -> "AdventureItinerary | None":
    """Price a multi-stop chain: origin → stop1 → stop2 → … → destination.

    Builds N+1 legs with cumulative depart dates from each stop's stay_days.
    Caps at 5 intermediate stops (6 legs total — affiliate multi-city limit).
    Honours arrive_by by trimming stays from last stop backwards.
    Returns None when stop_ideas is empty.
    """
    settings = settings or get_settings()
    if not stop_ideas:
        return None
    # Cap at 5 intermediate stops = 6 legs
    stop_ideas = stop_ideas[:5]

    # Build cumulative dates, trimming to fit arrive_by
    stays: list[int] = []
    trimmed: list[StopoverIdea] = []
    current_date = req.depart_date
    for idea in stop_ideas:
        stay = max(req.min_stop_days, min(req.max_stop_days, idea.stay_days))
        if req.arrive_by:
            remaining = (req.arrive_by - current_date).days
            # Need at least min_stop_days per remaining stop after this one
            remaining_stops_after = len(stop_ideas) - len(trimmed) - 1
            buffer = req.min_stop_days * remaining_stops_after
            if remaining < req.min_stop_days + buffer:
                break  # can't fit this stop; truncate chain
            stay = min(stay, remaining - buffer)
            stay = max(req.min_stop_days, stay)
        stays.append(stay)
        trimmed.append(idea.model_copy(update={"stay_days": stay}))
        current_date = current_date + timedelta(days=stay)

    if not trimmed:
        return None

    # Build leg definitions: (from_iata, to_iata, depart_date)
    leg_defs: list[tuple[str, str, date]] = []
    current_date = req.depart_date
    prev_iata = req.origin
    for idea, stay in zip(trimmed, stays):
        leg_defs.append((prev_iata, idea.iata, current_date))
        current_date = current_date + timedelta(days=stay)
        prev_iata = idea.iata
    leg_defs.append((prev_iata, req.destination, current_date))

    # Bail early if the user has already skipped/cancelled before we start pricing.
    if cancel_id:
        from yonder.search_cancel import is_cancelled as _is_canc
        if _is_canc(cancel_id):
            return None

    # Price all legs concurrently
    priced_legs: list[PricedLeg] = list(await asyncio.gather(*[
        _price_leg(
            fr, to, dep, req,
            settings=settings, include_mock=include_mock,
            only=only, http=http, fallback_chain=fallback_chain,
        )
        for fr, to, dep in leg_defs
    ]))

    # Multi-city booking URL covering all legs
    multi_url = google_flights_multi(
        [(fr, to, dep) for fr, to, dep in leg_defs],
        currency=req.currency,
    )

    # Stops metadata for the new `stops` field (backward-compat: first stop → stop_iata)
    stops_data = [
        {
            "iata": idea.iata,
            "city": idea.city,
            "country": idea.country,
            "stay_days": stay,
        }
        for idea, stay in zip(trimmed, stays)
    ]
    first_stop = trimmed[0]

    # Combined vibe tags from all stops
    all_vibe_tags: list[str] = list({
        tag for idea in trimmed for tag in (idea.vibe_tags or [])
    })

    # Title: "YVR → Tokyo (3n) → HKG (2n) → Bangkok"
    title_parts = [format_place(req.origin)]
    for idea, stay in zip(trimmed, stays):
        title_parts.append(f"{format_place(idea.iata, idea.city)} ({stay}n)")
    title_parts.append(format_place(req.destination))
    title = " → ".join(title_parts)

    errors_in = [lg.error for lg in priced_legs if lg.error]
    all_priced = all(lg.offer for lg in priced_legs)

    # Notes
    notes: list[str] = []
    if rescue:
        notes.append("No direct route? Take up to six flights to get you there.")
    stop_summary = ", ".join(
        f"{idea.city} ({stay}d)" for idea, stay in zip(trimmed, stays)
    )
    notes.append(f"Multi-hop via {stop_summary}")
    notes.append(f"{len(priced_legs)}-leg chain — book each leg separately, self-transfer risk")
    notes.append(f"Priced via {pricing_name or 'providers'} in {req.currency}")

    kind = "rescue" if rescue else "multi-stop"

    if not all_priced:
        return _apply_theme(
            AdventureItinerary(
                kind=kind,
                title=title,
                total_price=None,
                currency=req.currency,
                stop_city=first_stop.city,
                stop_iata=first_stop.iata,
                stay_days=first_stop.stay_days,
                why=first_stop.why,
                vibe_tags=all_vibe_tags,
                legs=priced_legs,
                adventure_score=0.0,
                notes=notes + (
                    [f"Pricing incomplete: {'; '.join(errors_in[:2])}"]
                    if errors_in else []
                ),
                google_flights_url=multi_url,
                booking_url=multi_url,
                theme_country=first_stop.country,
                stops=stops_data,
                rescue=rescue,
            )
        )

    total = round(sum(float(lg.offer.price) for lg in priced_legs if lg.offer), 2)
    cur = req.currency
    delta = (total - direct_price) if direct_price is not None else None

    # Ground cost for first stop only (multi-stop ground totals are complex)
    ground_fields: dict = {}
    col_delta = 0.0
    try:
        from yonder.daily_costs import compare_for_stop, settings_ground_fields

        gcmp = (
            compare_for_stop(
                ground_batch, stop_iata=first_stop.iata, stay_days=first_stop.stay_days
            )
            if ground_batch
            else None
        )
        if gcmp:
            col_delta = float(gcmp.rank_delta or 0.0)
            ground_fields = {
                "ground_daily_stop": gcmp.daily_stop,
                "ground_daily_origin": gcmp.daily_origin,
                "ground_total": gcmp.ground_total,
                "ground_display": (
                    f"+{gcmp.display_ground} "
                    f"({gcmp.display_daily_stop} per Day for {first_stop.stay_days} Days)"
                ),
                "ground_compare_line": gcmp.ground_compare_line,
                "ground_budget_status": gcmp.budget_status,
                "ground_budget_line": gcmp.budget_line or None,
                "ground_rank_delta": col_delta,
            }
    except Exception:
        pass

    if delta is not None and delta <= 0:
        notes.append(
            f"Flight signal {format_approx(abs(delta), cur)} under direct (same source)"
        )
    elif delta is not None and direct_price:
        notes.append(
            f"Flight signal {format_approx(delta, cur)} over direct "
            f"(~{delta / max(direct_price, 1) * 100:.0f}% multi-hop premium)"
        )

    score = _adventure_score(first_stop, total, direct_price, col_rank_delta=col_delta)
    # Bonus for multi-stop — more adventurous by definition
    score += len(trimmed) * 5.0
    tot_pd = price_display(total, cur, vs_delta=delta)

    all_in = None
    if ground_fields.get("ground_total") is not None:
        all_in = format_approx(total + float(ground_fields["ground_total"]), cur)
        ground_fields["all_in_display"] = all_in

    return _apply_theme(
        AdventureItinerary(
            kind=kind,
            title=title,
            total_price=total,
            currency=cur,
            stop_city=first_stop.city,
            stop_iata=first_stop.iata,
            stay_days=first_stop.stay_days,
            why=first_stop.why,
            vibe_tags=all_vibe_tags,
            legs=priced_legs,
            vs_direct_delta=round(delta, 2) if delta is not None else None,
            adventure_score=round(score, 1),
            notes=notes,
            bookable_separately=True,
            google_flights_url=multi_url,
            booking_url=multi_url,
            display_price=tot_pd.full,
            display_price_base=tot_pd.base,
            price_sign=tot_pd.sign or None,
            price_glyph=tot_pd.glyph or None,
            price_tone=tot_pd.tone,
            theme_country=first_stop.country,
            stops=stops_data,
            rescue=rescue,
            **ground_fields,
        )
    )


async def _try_rescue_chain(
    req: AdventureRequest,
    *,
    settings: Settings | None = None,
    include_mock: bool = False,
    http: httpx.AsyncClient,
    only: list[str] | None,
    fallback_chain: list[str],
    pricing_name: str | None,
    cancel_id: str | None = None,
    max_legs: int = 6,
    rescue_budget: float = 25.0,
) -> "AdventureItinerary | None":
    """Try hub chains when direct + single-stop pricing both failed.

    Attempts progressively longer chains through well-known connecting hubs:
      1 hub  → 2 legs  (origin → hub → dest)
      2 hubs → 3 legs
      …
      5 hubs → 6 legs  (the affiliate multi-city hard cap)

    ``max_legs`` controls the maximum chain length (default 6).  ``cancel_id``
    is checked before every attempt; ``rescue_budget`` (seconds, default 25) is
    a hard wall-clock cutoff — once exceeded the search returns whatever it has.
    Chains are tried shortest-first; the first successful pricing is returned.
    The number of permutations tried per length is bounded to keep runtime
    within the budget even when providers are slow.
    """
    import itertools
    import time as _time

    settings = settings or get_settings()

    from yonder.search_cancel import is_cancelled as _is_canc

    o, d = req.origin.upper(), req.destination.upper()
    hubs = [h for h in _RESCUE_HUBS if h != o and h != d]
    # intermediate stops = total legs − 1; cap at 5 (= 6 legs, affiliate limit)
    max_hubs = min(max_legs - 1, 5)

    def _idea_for(iata: str) -> StopoverIdea:
        seed = next((s for s in SEED_STOPOVERS if s["iata"] == iata), None)
        return StopoverIdea(
            iata=iata,
            city=(seed["city"] if seed else iata),
            stay_days=1,  # transit — minimum stay
            why="rescue hub connection",
            vibe_tags=list(seed.get("vibe_tags", [])) if seed else [],
            country=(seed.get("country") if seed else None),
            source="rescue",
        )

    # Per-length caps: shorter chains are tried more exhaustively; longer chains
    # are bounded so the total search stays within the rescue_budget wall time.
    _per_len_limit: dict[int, int] = {1: 10, 2: 10, 3: 8, 4: 5, 5: 3}

    deadline = _time.monotonic() + rescue_budget

    for n_hubs in range(1, max_hubs + 1):
        limit = _per_len_limit.get(n_hubs, 2)
        attempted = 0
        for combo in itertools.permutations(hubs, n_hubs):
            # Hard budget check — bail out if wall clock exceeded.
            if _time.monotonic() >= deadline:
                return None
            # Respect user Skip / cancellation signal.
            if cancel_id and _is_canc(cancel_id):
                return None
            if attempted >= limit:
                break
            attempted += 1
            ideas = [_idea_for(h) for h in combo]
            result = await _price_multi_stop_chain(
                req,
                ideas,
                settings=settings,
                include_mock=include_mock,
                http=http,
                only=only,
                fallback_chain=fallback_chain,
                direct_price=None,
                ground_batch={},
                cancel_id=cancel_id,
                rescue=True,
                pricing_name=pricing_name,
            )
            if result and result.total_price is not None:
                return result

    return None


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
    visited = {
        c.upper()
        for c in normalize_country_list(req.visited_countries or [], max_n=250)
    }
    origin = req.origin.upper()
    dest = req.destination.upper()
    getaway = _is_getaway(req)
    out: list[StopoverIdea] = []
    seen: set[str] = set()
    for idea in ideas:
        code = idea.iata.upper()
        if code in (origin, dest) or code in seen:
            # getaway home==dest: still exclude home via origin
            if not (getaway and origin == dest and code == origin):
                if code in (origin, dest) or code in seen:
                    continue
        if code == origin or code in seen:
            continue
        if not getaway and code == dest:
            continue
        if is_avoided_iata(code, avoid):
            continue
        cc = (idea.country or country_for_iata(code) or "").upper()
        if cc and cc in avoid:
            continue
        # Passport map: for getaway trips, never land somewhere already stamped visited.
        # Detour stopovers are connection cities, not novelty destinations, so the
        # visited-country filter does not apply.
        if getaway and visited and cc and cc in visited:
            continue
        seen.add(code)
        out.append(idea.model_copy(update={"iata": code, "country": cc or idea.country}))
        if len(out) >= req.max_candidates:
            break
    return out


def _is_getaway(req: AdventureRequest) -> bool:
    kind = (req.trip_kind or "").lower().strip()
    if kind in ("getaway", "round_trip", "roundtrip", "escape", "from_home"):
        return True
    return req.origin.upper() == req.destination.upper()


# ---------------------------------------------------------------------------
# Vibe → seed tag mapping (module-level so tests can import and validate it)
# ---------------------------------------------------------------------------
# Maps each vibe id to the set of seed vibe_tags that signal a good match.
# Each overlapping tag scores +2 so tag-rich cities rise naturally.
# Any vibe id present in vibes.json MUST have an entry here (even one tag);
# missing entries fall back to a weak generic score and produce bland results.
VIBE_TAG_MAP: dict[str, frozenset[str]] = {
    "chaos":      frozenset({"gritty", "raw", "vivid", "neon", "electric", "hazy"}),
    "wild":       frozenset({"nature", "rugged", "feral", "untamed", "mountains", "crisp", "stormy", "raw"}),
    "party":      frozenset({"neon", "electric", "vivid", "warmnights"}),
    "romance":    frozenset({"velvet", "goldenhour", "tender", "serene", "luminous", "nostalgic"}),
    "whimsical":  frozenset({"whimsical", "neon", "luminous", "tender"}),
    "neon":       frozenset({"neon", "electric"}),
    "night":      frozenset({"neon", "electric", "moody", "velvet"}),
    "soul":       frozenset({"velvet", "nostalgic", "moody", "ancient"}),
    "art":        frozenset({"art", "culture"}),
    "culture":    frozenset({"culture", "art", "ancient", "sacred", "nostalgic"}),
    "city":       frozenset({"city"}),
    "future":     frozenset({"modern", "electric", "neon", "luminous"}),
    "ocean":      frozenset({"coast", "seasalt", "beach"}),
    "islands":    frozenset({"beach", "seasalt", "serene", "coast"}),
    "beach":      frozenset({"beach", "seasalt", "relax", "warmnights", "coast"}),
    "jungle":     frozenset({"lush", "hazy"}),
    "nature":     frozenset({"nature", "lush", "serene", "rugged", "crisp"}),
    "mountains":  frozenset({"mountains", "alps", "rugged", "crisp", "north"}),
    "adventure":  frozenset({"nature", "rugged", "feral", "untamed", "mountains", "raw"}),
    "trains":     frozenset({"trains"}),
    "food":       frozenset({"food", "bazaar"}),
    "street":     frozenset({"food", "gritty", "vivid", "cheap"}),
    "desert":     frozenset({"hazy", "sun", "ancient"}),
    "sun":        frozenset({"sun", "goldenhour", "warmnights", "seasalt"}),
    "luxury":     frozenset({"opulent", "luminous", "velvet"}),
    "nostalgic":  frozenset({"nostalgic", "ancient", "sleepy", "tender", "moody", "velvet"}),
    "spa":        frozenset({"serene", "tender", "sleepy", "crisp", "nature"}),
    "cozy":       frozenset({"tender", "sleepy", "moody", "serene"}),
    "history":    frozenset({"ancient", "sacred", "nostalgic", "culture"}),
    "snow":       frozenset({"crisp", "north", "stormy", "mountains"}),
    "quiet":      frozenset({"serene", "sleepy", "tender", "crisp"}),
    "gritty":     frozenset({"gritty", "raw", "hazy"}),
    "cheap":      frozenset({"cheap"}),
    "fire":       frozenset({"electric", "vivid", "neon", "warmnights"}),
    "velvet":     frozenset({"velvet", "moody", "nostalgic", "goldenhour"}),
    "ember":      frozenset({"goldenhour", "warmnights", "tender", "lush"}),
    "rose":       frozenset({"tender", "goldenhour", "serene", "whimsical"}),
    "blush":      frozenset({"tender", "sleepy", "serene", "whimsical"}),
    "petal":      frozenset({"tender", "serene", "whimsical"}),
    "carnival":   frozenset({"vivid", "electric", "warmnights", "neon"}),
    "festival":   frozenset({"vivid", "electric", "warmnights", "neon"}),
    "warmnights": frozenset({"warmnights", "seasalt", "beach"}),
    "tender":     frozenset({"tender", "sleepy", "serene"}),
    "dream":      frozenset({"serene", "tender", "whimsical", "moody"}),
    "magic":      frozenset({"whimsical", "luminous", "ancient"}),
    "gothic":     frozenset({"moody", "ancient", "gritty"}),
    "moody":      frozenset({"moody", "stormy", "velvet"}),
    "indie":      frozenset({"gritty", "moody", "electric"}),
    "lavender":   frozenset({"serene", "tender", "nature"}),
    "vivid":      frozenset({"vivid", "neon", "electric"}),
    "dusk":       frozenset({"goldenhour", "moody", "velvet"}),
    "twilight":   frozenset({"moody", "velvet", "goldenhour"}),
    "cosmic":     frozenset({"north", "crisp", "serene"}),
    "sleepy":     frozenset({"sleepy", "tender", "serene"}),
    "retro":      frozenset({"nostalgic", "moody", "gritty"}),
    "seasalt":    frozenset({"seasalt", "coast", "beach"}),
    "navy":       frozenset({"coast", "seasalt", "city"}),
    "stormy":     frozenset({"stormy", "moody", "rugged"}),
    "lakeside":   frozenset({"serene", "nature", "crisp"}),
    "electric":   frozenset({"electric", "neon"}),
    "fog":        frozenset({"moody", "stormy", "hazy"}),
    "rugged":     frozenset({"rugged", "mountains", "feral"}),
    "flow":       frozenset({"serene", "cheap", "gritty"}),
    "sail":       frozenset({"coast", "seasalt", "serene"}),
    "crisp":      frozenset({"crisp", "north", "mountains"}),
    "reef":       frozenset({"beach", "seasalt", "coast"}),
    "dive":       frozenset({"gritty", "ancient", "city"}),
    "tropical":   frozenset({"lush", "warmnights", "beach", "cheap"}),
    "serene":     frozenset({"serene", "tender", "sleepy"}),
    "botanic":    frozenset({"nature", "lush", "serene"}),
    "forest":     frozenset({"nature", "lush", "crisp"}),
    "valley":     frozenset({"nature", "serene", "mountains"}),
    "meadow":     frozenset({"nature", "serene", "tender"}),
    "canopy":     frozenset({"lush", "nature", "hazy"}),
    "lush":       frozenset({"lush", "hazy", "warmnights"}),
    "savanna":    frozenset({"feral", "rugged", "untamed"}),
    "wellbeing":  frozenset({"serene", "nature", "tender"}),
    "golf":       frozenset({"serene", "opulent", "coast"}),
    "feral":      frozenset({"feral", "untamed", "rugged", "raw"}),
    "untamed":    frozenset({"untamed", "feral", "rugged", "nature"}),
    "golden":     frozenset({"goldenhour", "warmnights", "sun"}),
    "luminous":   frozenset({"luminous", "goldenhour", "opulent"}),
    "opulent":    frozenset({"opulent", "luminous", "velvet"}),
    "dunes":      frozenset({"hazy", "ancient", "rugged"}),
    "glow":       frozenset({"goldenhour", "warmnights", "tender"}),
    "goldenhour": frozenset({"goldenhour", "sun", "warmnights"}),
    "hazy":       frozenset({"hazy", "warmnights", "vivid"}),
    "spice":      frozenset({"food", "bazaar", "ancient", "vivid"}),
    "canyon":     frozenset({"rugged", "ancient", "hazy"}),
    "ancient":    frozenset({"ancient", "sacred", "culture"}),
    "sacred":     frozenset({"sacred", "ancient", "serene"}),
    "raw":        frozenset({"raw", "gritty", "feral"}),
    "road":       frozenset({"rugged", "raw", "feral"}),
    "folklore":   frozenset({"ancient", "culture", "nostalgic"}),
    # Personality vibes
    "billiards":  frozenset({"gritty", "raw", "cheap", "moody", "city"}),
    "gecko":      frozenset({"lush", "hazy", "warmnights", "cheap", "raw", "vivid"}),
    "wildwest":   frozenset({"rugged", "raw", "feral", "untamed", "hazy", "ancient"}),
    "nomad":      frozenset({"cheap", "gritty", "raw", "city", "hazy", "feral"}),
    "meltdown":   frozenset({"electric", "vivid", "neon", "hazy", "warmnights", "gritty"}),
    "vanish":     frozenset({"sleepy", "tender", "serene", "moody", "cheap"}),
    "dissociate": frozenset({"sleepy", "tender", "serene", "moody", "hazy"}),
    "spiral":     frozenset({"vivid", "gritty", "electric", "city", "ancient"}),
    "delirium":   frozenset({"electric", "vivid", "neon", "hazy", "gritty", "city"}),
    "burnout":    frozenset({"serene", "sleepy", "tender", "crisp", "nature"}),
    "apocalypse": frozenset({"ancient", "gritty", "raw", "moody", "rugged"}),
}


# Mild score decay applied to cities already present in the user's recent
# trip history (★ Saved trips). Strong enough to demote a perennial top-seed
# (e.g. BKK's 5-tag gecko match) below the next-best fresh city, but small
# enough that an *unsaved* strong match still wins.
RECENT_HISTORY_DECAY = 3


def _recent_history_iatas() -> set[str]:
    """IATA codes from the user's recent trip history (saved + recycled trips).

    Unions destination IATAs from:
    - ★ Saved trips (explicit saves)
    - Recycled-result pool (non-mock saved trips surfaced via the recycle path)

    Best-effort — any storage error on either source is swallowed so ranking
    never breaks on a broken saves DB.
    """
    iatas: set[str] = set()
    try:
        from yonder.saved import saved_destination_iatas

        iatas |= saved_destination_iatas(limit=200) or set()
    except Exception:  # noqa: BLE001
        pass
    try:
        from yonder.recycle import recycled_destination_iatas

        iatas |= recycled_destination_iatas(limit=200) or set()
    except Exception:  # noqa: BLE001
        pass
    return iatas


def _sort_by_comfort(
    ideas: list[StopoverIdea],
    req: AdventureRequest,
    *,
    shuffle: bool = False,
    recent_iatas: set[str] | None = None,
) -> list[StopoverIdea]:
    """Reorder *ideas* by vibe-tag overlap + traveler comfort fit.

    Extracted so both seed ideas and Grok-sourced candidates get the same
    scoring pass.  Does **not** truncate; callers slice to max_candidates.

    Cities appearing in the user's recent trip history (``recent_iatas``,
    defaulting to saved-trip destinations) get a mild score decay so a
    single tag-rich city (e.g. Bangkok for gecko/meltdown) does not top the
    list on every single search.
    """
    import random

    if recent_iatas is None:
        recent_iatas = _recent_history_iatas()
    _recent = {c.upper() for c in recent_iatas if c}

    vibe = (req.vibe or "").lower()
    prompt_l = (req.prompt or "").lower()
    want_food = vibe in ("food",) or "food" in prompt_l
    want_cheap = vibe in ("cheap", "budget") or any(
        w in prompt_l for w in ("cheap", "cost of living", "budget", "affordable")
    )
    want_safe = any(
        w in prompt_l for w in ("safe", "security", "secure", "personal security")
    )

    # Use the module-level VIBE_TAG_MAP so the mapping is importable by tests.
    _related_tags = VIBE_TAG_MAP.get(vibe, frozenset())

    # Travel comfort factor: 0.0 (no stamps) → 1.0 (100+ countries).
    # Used as a subtle nudge — adventurous tags rise for seasoned travelers,
    # approachable tags rise for newcomers. Never overrides an explicit vibe.
    _comfort = min(1.0, len(req.visited_countries or []) / 100.0)
    _ADVENTUROUS_TAGS = frozenset({"gritty", "raw", "feral", "untamed", "electric", "neon", "hazy", "cheap"})
    _APPROACHABLE_TAGS = frozenset({"serene", "sleepy", "tender", "safe", "opulent"})

    def _score(idea: StopoverIdea) -> int:
        tags = {t.lower() for t in (idea.vibe_tags or [])}
        s = 0
        # Count how many of the idea's tags overlap with the vibe's related tags;
        # each matching tag is worth 2 points so tag-rich cities naturally lead.
        if _related_tags:
            s += len(tags & _related_tags) * 2
        elif vibe and vibe in tags:
            # Vibe id not in the map but present verbatim as a tag → small boost
            s += 2
        # Additional boosts for explicit prompt/vibe intent signals
        if want_food and "food" in tags:
            s += 3
        if want_cheap and "cheap" in tags:
            s += 3
        if want_safe and "safe" in tags:
            s += 2
        # Comfort nudge: seasoned travelers get a gentle push toward wilder
        # destinations; newcomers get a gentle push toward approachable ones.
        # Max ±2 pts — subtle, never overrides a strong vibe match.
        adv_overlap = len(tags & _ADVENTUROUS_TAGS)
        app_overlap = len(tags & _APPROACHABLE_TAGS)
        if adv_overlap:
            s += round(_comfort * min(adv_overlap, 2))
        if app_overlap:
            s += round((1.0 - _comfort) * min(app_overlap, 1))
        # Domestic boost: always applied when proximity_mode is set (user asked
        # for "not too far" / "nearby" / "short flight" etc.), otherwise only
        # for low-XP travellers (at least one stamp but fewer than 25) to nudge
        # them toward home-country options.  Zero-stamp users without proximity
        # intent keep the open "go anywhere" nudge.
        _prox = getattr(req, "proximity_mode", False)
        if _prox or (req.visited_countries and _comfort < 0.25):
            origin_country = country_for_iata(req.origin or "")
            idea_country = idea.country or ""
            if origin_country and idea_country and origin_country.upper() == idea_country.upper():
                s += 3
        # Diversity nudge: decay cities already in recent trip history so
        # results feel fresh instead of surfacing the same top seed each time.
        if _recent and (idea.iata or "").upper() in _recent:
            s -= RECENT_HISTORY_DECAY
        return s

    # Sort by vibe-tag overlap + comfort fit so best-matched cities lead.
    if shuffle:
        random.shuffle(ideas)
        ideas = sorted(ideas, key=lambda i: (_score(i), random.random()), reverse=True)
    else:
        ideas = sorted(ideas, key=_score, reverse=True)
    return ideas


def seed_ideas(
    req: AdventureRequest,
    *,
    exclude_iatas: set[str] | None = None,
    shuffle: bool = False,
) -> list[StopoverIdea]:
    import random

    origin = req.origin.upper()
    dest = req.destination.upper()
    avoid = normalize_avoid_list(req.avoid_countries)
    visited = {
        c.upper()
        for c in normalize_country_list(req.visited_countries or [], max_n=250)
    }
    ban = {c.upper() for c in (exclude_iatas or set()) if c}
    getaway = _is_getaway(req)
    stay = max(
        req.min_stop_days,
        min(req.max_stop_days, (req.min_stop_days + req.max_stop_days) // 2),
    )
    ideas: list[StopoverIdea] = []
    for row in SEED_STOPOVERS:
        code = row["iata"]
        if code in ban:
            continue
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
    # Sort by vibe-tag overlap + comfort fit (same pass used for Grok candidates).
    return _sort_by_comfort(ideas, req, shuffle=shuffle)[: req.max_candidates]


async def plan_adventure(
    req: AdventureRequest,
    ideas: list[StopoverIdea],
    *,
    named_stop_chain: list[StopoverIdea] | None = None,
    settings: Settings | None = None,
    include_mock: bool = False,
    cancel_id: str | None = None,
    exclude_iatas: set[str] | None = None,
) -> AdventureResult:
    """Plan an adventure itinerary.

    ``named_stop_chain`` — when the user explicitly named 2+ intermediate cities
    in the prompt (e.g. "stopping in Tokyo and Hong Kong"), pass those as an
    ordered list of StopoverIdeas.  A multi-stop itinerary covering all of them
    in sequence will be priced first and prepended to the results.
    """
    settings = settings or get_settings()
    trip_kind = (req.trip_kind or "detour").lower().strip()
    if req.origin.upper() == req.destination.upper():
        trip_kind = "getaway"
    ban = {c.upper() for c in (exclude_iatas or set()) if c and len(str(c)) == 3}
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
    if ban:
        ideas = [i for i in ideas if (i.iata or "").upper() not in ban]
    ideas = filter_ideas(ideas, req)
    if ban:
        ideas = [i for i in ideas if (i.iata or "").upper() not in ban]
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
    # One idea per destination city (IATA first; collapse same city name)
    unique_ideas: list[StopoverIdea] = []
    seen_dest: set[str] = set()
    for idea in ideas:
        key = (idea.iata or "").upper() or (idea.city or "").strip().lower()
        if not key or key in seen_dest:
            continue
        seen_dest.add(key)
        # Fill missing city/country so boarding passes never show bare "DPS"
        code = (idea.iata or "").upper()
        city = (idea.city or "").strip()
        if not city or city.upper() == code:
            city = city_for_iata(code) or city or code
        cc = (idea.country or country_for_iata(code) or "").upper() or None
        if city != idea.city or cc != idea.country:
            idea = idea.model_copy(update={"city": city, "country": cc})
        unique_ideas.append(idea)
    ideas = unique_ideas
    # Apply comfort-fit reranking — same scoring pass as seeds — so
    # Grok-sourced candidates are ordered by comfort fit before pricing.
    ideas = _sort_by_comfort(ideas, req)
    errors: list[str] = []
    itineraries: list[AdventureItinerary] = []
    direct_price: float | None = None
    if _is_getaway(req):
        errors.append(
            "Getaway mode: home base → new place → home "
            f"({req.origin} round-trip). Candidates are destinations, not mid-route stops."
        )

    # Soft aim for COL pacing; Skip (cancel_id) ends pricing early. No hard kill.
    import time as _time

    from yonder.search_cancel import is_cancelled

    aim, _mx = settings.search_timing()
    soft_deadline = _time.monotonic() + max(5.0, aim - 1.0)
    ground_batch: dict = {}
    bag_daily, bag_tol, _bag_parts = settings.col_budget()
    try:
        from yonder.daily_costs import estimate_batch_for_stops

        stop_tuples = [
            (i.iata, i.country, i.city)
            for i in ideas[: max(1, int(req.max_candidates or 5))]
        ]
        col_timeout = min(7.0, max(3.0, aim * 0.25))
        try:
            ground_batch = await asyncio.wait_for(
                estimate_batch_for_stops(
                    settings,
                    origin_iata=req.origin,
                    stops=stop_tuples,
                    currency=req.currency,
                    vibe=req.vibe or "adventure",
                    live_grok=True,
                ),
                timeout=col_timeout,
            )
            if bag_daily and bag_daily > 0:
                errors.append(
                    f"Grok COL vs Settings bag {format_approx(bag_daily, req.currency)}/day "
                    f"(+{bag_tol:.0f}% over-budget band)"
                )
            else:
                errors.append(
                    "Grok COL attached (set Settings cost/day to score under/over budget)"
                )
        except asyncio.TimeoutError:
            ground_batch = await estimate_batch_for_stops(
                settings,
                origin_iata=req.origin,
                stops=stop_tuples,
                currency=req.currency,
                vibe=req.vibe or "adventure",
                live_grok=False,
            )
            errors.append(
                "COL from cache/static (Grok timed out) — still scored vs Settings bag"
            )
        except Exception as col_exc:  # noqa: BLE001
            try:
                ground_batch = await estimate_batch_for_stops(
                    settings,
                    origin_iata=req.origin,
                    stops=stop_tuples,
                    currency=req.currency,
                    vibe=req.vibe or "adventure",
                    live_grok=False,
                )
                errors.append(f"COL fallback after error: {str(col_exc)[:72]}")
            except Exception:
                ground_batch = {}
                errors.append("COL unavailable")
    except Exception as col_outer:  # noqa: BLE001
        ground_batch = {}
        errors.append(f"COL skipped: {str(col_outer)[:72]}")
    errors.append(f"Soft aim ~{aim:.0f}s: skipped AviationStack enrich + direct baseline")

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
        only = await pick_pricing_provider(settings, http, include_mock)
        if only:
            only = only[:1]
        pricing_name = only[0] if only else None
        fallback_chain: list[str] = []
        if pricing_name:
            errors.append(f"Primary fare API: {pricing_name} (single provider)")
        else:
            errors.append(
                "No active fare providers — add Duffel live / SerpAPI / Amadeus or enable mock"
            )

        async def price_stopover(idea: StopoverIdea) -> AdventureItinerary | None:
            if cancel_id and is_cancelled(cancel_id):
                return None
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
                from yonder.daily_costs import compare_for_stop, settings_ground_fields

                gcmp = None
                if ground_batch:
                    gcmp = compare_for_stop(
                        ground_batch, stop_iata=idea.iata, stay_days=stay
                    )
                if gcmp:
                    notes.extend(gcmp.note_lines)
                    col_delta = float(gcmp.rank_delta or 0.0)
                    ground_fields = {
                        "ground_daily_stop": gcmp.daily_stop,
                        "ground_daily_origin": gcmp.daily_origin,
                        "ground_total": gcmp.ground_total,
                        "ground_display": (
                            f"+{gcmp.display_ground} ({gcmp.display_daily_stop} per Day for {stay} Days)"
                        ),
                        "ground_compare_line": gcmp.ground_compare_line
                        or (
                            f"{gcmp.display_daily_stop}/Day vs. "
                            f"{gcmp.display_daily_origin}/Day ({gcmp.origin_name})"
                        ),
                        "ground_budget_status": gcmp.budget_status,
                        "ground_budget_line": gcmp.budget_line or None,
                        "ground_rank_delta": col_delta,
                    }
                else:
                    # No city estimate — still surface Settings bag as planning ground
                    stop_label = format_place(idea.iata, idea.city)
                    ground_fields, col_notes = settings_ground_fields(
                        settings,
                        stay_days=stay,
                        currency=req.currency,
                        stop_label=stop_label,
                    )
                    if col_notes:
                        notes.extend(col_notes)
                    col_delta = float(ground_fields.get("ground_rank_delta") or 0.0)
            except Exception:
                ground_fields = {}
                col_delta = 0.0
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
                    f"{format_place(idea.iata, idea.city)}"
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
                ground_fields["all_in_display"] = all_in

            title = (
                f"{format_place(req.origin)} ↺ "
                f"{format_place(idea.iata, idea.city)}"
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

        # ── Multi-stop chain: user named 2+ intermediate cities ────────────────
        # Price before single-stop alternatives so it leads the results.
        if named_stop_chain and len(named_stop_chain) >= 2:
            if not (cancel_id and is_cancelled(cancel_id)):
                try:
                    ms_it = await _price_multi_stop_chain(
                        req,
                        named_stop_chain,
                        settings=settings,
                        include_mock=include_mock,
                        http=http,
                        only=only,
                        fallback_chain=fallback_chain,
                        direct_price=direct_price,
                        ground_batch=ground_batch,
                        cancel_id=cancel_id,
                        rescue=False,
                        pricing_name=pricing_name,
                    )
                    if ms_it is not None:
                        itineraries.insert(0, ms_it)
                        errors.append(
                            f"Multi-stop chain: {' → '.join(s.iata for s in named_stop_chain)}"
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Multi-stop chain pricing error: {exc}")

        # At most five ideas priced per search — sequential so Skip can stop mid-list
        ideas = ideas[: max(2, min(5, req.max_candidates))]
        for idea in ideas:
            if cancel_id and is_cancelled(cancel_id):
                errors.append("Skipped early — showing options priced so far")
                break
            try:
                it = await price_stopover(idea)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Stopover pricing error: {exc}")
                continue
            if it is not None:
                itineraries.append(it)
        if (
            not (cancel_id and is_cancelled(cancel_id))
            and _time.monotonic() > soft_deadline
            and itineraries
        ):
            errors.append(f"Past soft aim (~{aim:.0f}s) — still finished pricing")

        # ── Rescue routing ──────────────────────────────────────────────────────
        # When no normally-priced itinerary succeeded, attempt hub chains (up to
        # 6 legs) as a last resort. Skipped for getaways (no fixed destination).
        #
        # VIBE GATE: rescue is an AI-proposed multi-hop so it must be gated.
        # - Wander vibes (adventure, chaotic, budget, slow-travel …) → allowed.
        # - Comfort vibes (luxury, romantic, relaxing …) prefer clean direct
        #   routes; rescue chains don't fit their intent → blocked.
        # - Exception: user explicitly named 2+ stops (named_stop_chain) →
        #   rescue is also allowed even for comfort vibes, because the traveler
        #   has already opted into a multi-hop journey.
        from yonder.intent import is_wander_vibe as _is_wander_vibe
        _rescue_allowed = _is_wander_vibe(req.vibe) or bool(
            named_stop_chain and len(named_stop_chain) >= 2
        )
        priced_so_far = [i for i in itineraries if i.total_price is not None]
        if (
            not priced_so_far
            and not _is_getaway(req)
            and not (cancel_id and is_cancelled(cancel_id))
            and req.origin.upper() != req.destination.upper()
            and _rescue_allowed
        ):
            try:
                rescue_it = await _try_rescue_chain(
                    req,
                    settings=settings,
                    include_mock=include_mock,
                    http=http,
                    only=only,
                    fallback_chain=fallback_chain,
                    pricing_name=pricing_name,
                    cancel_id=cancel_id,
                )
                if rescue_it is not None:
                    itineraries.append(rescue_it)
                    errors.append("Rescue chain: no direct route — chained via hubs")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Rescue routing error: {exc}")

    complete = [i for i in itineraries if i.total_price is not None]
    # Never surface banned stops (prior Saves / already shown)
    if ban:
        complete = [
            i
            for i in complete
            if (i.stop_iata or "").upper() not in ban
        ]
    # Rank cheapest → most expensive; one package per destination city.
    # Multi-stop and rescue cards use their title as the dedup key since
    # they may share stop_iata with single-stop alternatives.
    complete.sort(key=lambda i: (i.total_price or 1e12, -i.adventure_score))
    top: list[AdventureItinerary] = []
    seen_cities: set[str] = set()
    for it in complete:
        if it.kind in ("multi-stop", "rescue"):
            # Multi-stop/rescue cards use title as dedup key so they don't
            # collapse with single-stop alternatives at the same first stop.
            city_key = it.title.strip().lower()
        else:
            city_key = (
                (it.stop_iata or "").upper()
                or (it.stop_city or "").strip().lower()
                or it.title.strip().lower()
            )
        if not city_key or city_key in seen_cities:
            continue
        if ban and (it.stop_iata or "").upper() in ban and it.kind not in ("multi-stop", "rescue"):
            continue
        seen_cities.add(city_key)
        top.append(it)
        if len(top) >= 5:
            break
    if req.avoid_countries:
        errors.append(
            "Avoiding countries: " + ", ".join(req.avoid_countries)
        )
    return AdventureResult(
        request=req,
        ideas=ideas[:5],
        itineraries=top,
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
        all_in_display = format_approx(total + float(itinerary.ground_total), cur)

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
