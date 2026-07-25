"""Currency conversion with a free daily-rate cache (Frankfurter / ECB)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from yonder.types import FlightOffer

# in-process cache: (from, to) -> (rate, expires_epoch)
_RATE_CACHE: dict[tuple[str, str], tuple[float, float]] = {}
_CACHE_TTL = 6 * 3600  # 6 hours


async def get_rate(
    client: httpx.AsyncClient,
    from_cur: str,
    to_cur: str,
) -> float | None:
    fr = from_cur.upper()
    to = to_cur.upper()
    if fr == to:
        return 1.0
    key = (fr, to)
    now = time.time()
    hit = _RATE_CACHE.get(key)
    if hit and hit[1] > now:
        return hit[0]

    # Frankfurter (ECB) — free, no key
    try:
        resp = await client.get(
            "https://api.frankfurter.app/latest",
            params={"from": fr, "to": to},
            timeout=15.0,
        )
        if resp.status_code < 400:
            data = resp.json()
            rate = float((data.get("rates") or {}).get(to) or 0)
            if rate > 0:
                _RATE_CACHE[key] = (rate, now + _CACHE_TTL)
                return rate
    except Exception:
        pass

    # Fallback: open.er-api.com (also free, no key)
    try:
        resp = await client.get(f"https://open.er-api.com/v6/latest/{fr}", timeout=15.0)
        if resp.status_code < 400:
            data = resp.json()
            rates = data.get("rates") or {}
            rate = float(rates.get(to) or 0)
            if rate > 0:
                _RATE_CACHE[key] = (rate, now + _CACHE_TTL)
                return rate
    except Exception:
        pass
    return None


async def convert_amount(
    client: httpx.AsyncClient,
    amount: float,
    from_cur: str,
    to_cur: str,
) -> tuple[float, str, bool]:
    """Returns (amount, currency, converted?)."""
    fr = (from_cur or "USD").upper()
    to = (to_cur or fr).upper()
    if fr == to:
        return amount, to, False
    rate = await get_rate(client, fr, to)
    if rate is None:
        return amount, fr, False
    return round(amount * rate, 2), to, True


async def convert_offers(
    client: httpx.AsyncClient,
    offers: list[FlightOffer],
    target: str,
) -> list[FlightOffer]:
    target = target.upper()
    out: list[FlightOffer] = []
    for o in offers:
        if (o.currency or "").upper() == target:
            out.append(o)
            continue
        new_price, new_cur, ok = await convert_amount(
            client, o.price, o.currency or "USD", target
        )
        if ok:
            note = (o.notes or "") + f" · FX {o.currency}→{new_cur} (approx mid-market, not airline rate)"
            out.append(
                o.model_copy(
                    update={
                        "price": new_price,
                        "currency": new_cur,
                        "notes": note.strip(" ·"),
                    }
                )
            )
        else:
            out.append(o)
    return out


async def convert_offer(
    client: httpx.AsyncClient,
    offer: FlightOffer,
    target: str,
) -> FlightOffer:
    converted = await convert_offers(client, [offer], target)
    return converted[0]
