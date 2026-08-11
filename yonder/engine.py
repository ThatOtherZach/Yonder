from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import httpx

from yonder.config import Settings, get_settings
from yonder.currency import convert_offers
from yonder.history import record_offers, route_stats
from yonder.links import attach_links_to_offer, aviasales_url
from yonder.money import price_display
from yonder.providers import build_providers
from yonder.quota import choose_providers
from yonder.types import FlightOffer, SearchQuery, UnifiedSearchResult

logger = logging.getLogger("yonder.engine")


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
            # No providers available — still return a card with an affiliate link
            fallback = _build_fallback_offer(query, target, results=[])
            return UnifiedSearchResult(query=query, results=[], offers=[fallback])

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

        # ── Owner alerting: quota/auth failures on any provider ──────────────────
        # Run unconditionally so the owner sees the alert even when another provider
        # supplied live offers.  Quota-exhausted and auth failures need prompt
        # attention regardless of whether the search returned results overall.
        _real_results = [r for r in results if r.provider != "mock"]
        _failed = [r for r in _real_results if not r.ok]
        if _failed:
            _failure_summary = "; ".join(
                f"{r.provider}[{r.failure_kind or 'error'}]: {(r.error or '')[:80]}"
                for r in _failed
            )
            _quota_or_auth = [
                r for r in _failed
                if r.failure_kind in ("quota_exhausted", "inactive")
            ]
            if _quota_or_auth:
                logger.error(
                    "⚠ FLIGHT DATA UNAVAILABLE — quota/auth failure for %s→%s: %s",
                    query.origin,
                    query.destination,
                    _failure_summary,
                )
            elif not all_offers:
                # All real providers failed but none with quota/auth — transient
                logger.warning(
                    "ALL PROVIDERS DOWN for %s→%s: %s",
                    query.origin,
                    query.destination,
                    _failure_summary,
                )
            # Record for owner settings page (survives server restart, not per-session)
            from yonder.quota import record_last_search_errors
            record_last_search_errors(
                _failed,
                origin=query.origin,
                destination=query.destination,
            )

        # ── Pricing failed / no offers: build a fallback card with affiliate link ──
        if not all_offers:
            fallback = _build_fallback_offer(query, target, results=list(results))
            return UnifiedSearchResult(
                query=query, results=list(results), offers=[fallback]
            )

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


# ── Currency symbol helper (avoids importing the full money._SYMBOLS dict) ───
_CURRENCY_SYMS: dict[str, str] = {
    "USD": "$", "CAD": "C$", "EUR": "€", "GBP": "£",
    "AUD": "A$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "MXN": "MX$", "JPY": "¥", "CHF": "CHF ", "INR": "₹",
}


def _build_fallback_offer(
    query: SearchQuery,
    currency: str,
    *,
    results: list,
) -> FlightOffer:
    """Return a fare-missing offer that carries an affiliate link and a gentle note.

    Tier 2 — route has price history: "recently ~$420"
    Tier 3 — no history at all:       "No fare history for this exact route — check live prices"

    The offer is never shown as a real fare (fare_missing=True); its only purpose
    is to ensure the booking CTA renders even when all providers are down.
    """
    try:
        hist = route_stats(query.origin, query.destination, currency=currency)
    except Exception:
        hist = None  # type: ignore[assignment]

    has_hist = hist is not None and hist.n > 0 and hist.median is not None
    if has_hist:
        sym = _CURRENCY_SYMS.get(currency, currency + " ")
        fare_note = f"recently ~{sym}{hist.median:.0f}"
        hist_price = hist.median
    else:
        fare_note = "No fare history for this exact route — check live prices"
        hist_price = 0.0

    # Affiliate link — the whole point of this fallback
    aff_url = aviasales_url(
        query.origin,
        query.destination,
        query.depart_date,
        return_date=query.return_date,
        adults=query.adults,
    )

    return FlightOffer(
        provider="fallback",
        price=float(hist_price or 0.0),
        currency=currency,
        price_kind="live",
        fare_missing=True,
        fare_note=fare_note,
        google_flights_url=aff_url,
        booking_url=aff_url,
    )
