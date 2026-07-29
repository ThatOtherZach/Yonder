from __future__ import annotations

import asyncio
from typing import Iterable

import httpx

from yonder.config import Settings, get_settings
from yonder.currency import convert_offers
from yonder.history import record_offers, route_stats
from yonder.links import attach_links_to_offer
from yonder.money import price_display
from yonder.providers import build_providers
from yonder.quota import choose_providers
from yonder.types import FlightOffer, SearchQuery, UnifiedSearchResult


def _dedupe_key(o: FlightOffer) -> tuple:
    out_air = tuple(o.airlines)
    dep = None
    if o.segments_out and o.segments_out[0].departure:
        dep = o.segments_out[0].departure.replace(second=0, microsecond=0)
    return (o.provider, round(o.price, 2), o.currency, out_air, o.stops_out, dep)


def merge_offers(offers: Iterable[FlightOffer]) -> list[FlightOffer]:
    seen: set[tuple] = set()
    merged: list[FlightOffer] = []
    for o in sorted(offers, key=lambda x: (x.price, x.total_stops)):
        key = _dedupe_key(o)
        if key in seen:
            continue
        seen.add(key)
        merged.append(o)
    return merged


async def search_flights(
    query: SearchQuery,
    *,
    settings: Settings | None = None,
    include_mock: bool = False,
    only: list[str] | None = None,
    timeout: float = 12.0,
    convert_currency: bool = True,
    client: httpx.AsyncClient | None = None,
    force_all: bool = False,
    smart_route: bool = True,
    max_providers: int = 1,
) -> UnifiedSearchResult:
    settings = settings or get_settings()
    target = (query.currency or settings.default_currency or "USD").upper()
    query = query.model_copy(update={"currency": target})

    owns = client is None
    # Cap HTTP client timeout so a hung API cannot exceed the search budget
    http_timeout = min(float(timeout), 12.0)
    http = client or httpx.AsyncClient(timeout=http_timeout, follow_redirects=True)
    try:
        # Prefer configured keys without slow health probes (30s budget)
        selected = only
        if selected is None and smart_route and not force_all:
            mode = "scan"
            if (settings.provider_mode or "smart").lower() == "scan_all":
                force_all = True
            selected = await choose_providers(
                settings,
                http,
                mode=mode,
                need=1 if not force_all else 99,
                include_mock=include_mock,
                force_all=force_all,
                probe=False,
            )
            from yonder.quota import FARE_PROVIDERS

            fare = [n for n in selected if n in FARE_PROVIDERS or n == "mock"]
            if fare:
                selected = fare if force_all else fare[: max(1, max_providers)]
        elif selected is None:
            selected = settings.configured_providers()
            if include_mock:
                selected = list(selected) + ["mock"]
            from yonder.quota import FARE_PROVIDERS

            fare = [n for n in selected if n in FARE_PROVIDERS or n == "mock"]
            selected = fare[: max(1, max_providers)] if fare else selected

        providers = build_providers(
            settings, http, include_mock=include_mock, only=selected
        )
        if not providers:
            return UnifiedSearchResult(query=query, results=[], offers=[])

        results = await asyncio.wait_for(
            asyncio.gather(*(p.safe_search(query) for p in providers)),
            timeout=http_timeout,
        )
        all_offers = merge_offers(o for r in results for o in r.offers)

        if convert_currency and all_offers:
            all_offers = await convert_offers(http, all_offers, target)
            all_offers = merge_offers(all_offers)

        # Booking links: Google Flights + Kayak/airline backup
        all_offers = [attach_links_to_offer(o, query) for o in all_offers]

        # Persist samples (builds your local historical dataset)
        try:
            record_offers(query, all_offers)
        except Exception:
            pass

        # Deal scores vs your journal → ~C$420▼ (good) / ~C$420▲ (high)
        stats = route_stats(query.origin, query.destination, currency=target)
        decorated: list[FlightOffer] = []
        for o in all_offers:
            score, label = stats.deal_score(o.price)
            pd = price_display(
                o.price,
                o.currency,
                deal_label=label,
                deal_score=score,
                median=stats.median,
            )
            decorated.append(
                o.model_copy(
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
            )

        # One fare per destination city (Escape is a single O/D) — keep cheapest only
        decorated = decorated[:1]

        return UnifiedSearchResult(
            query=query,
            results=list(results),
            offers=decorated,
        )
    finally:
        if owns:
            await http.aclose()
