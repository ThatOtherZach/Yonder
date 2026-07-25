"""Display helpers for approximate fares.

Schema:  ~[C]$XXX[▲/▼]
  ~     = approximate / signal (not a locked fare)
  C$    = currency (CAD→C$, USD→$, EUR→€, …)
  XXX   = rounded whole units
  ▼     = green: below typical history (or under baseline)
  ▲     = red:   above typical history (or over baseline)
  (no triangle when no history / near median)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Common currency → symbol prefix before the amount
_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "CAD": "C$",
    "EUR": "€",
    "GBP": "£",
    "AUD": "A$",
    "JPY": "¥",
    "CHF": "CHF ",
    "MXN": "MX$",
    "INR": "₹",
    "NZD": "NZ$",
    "SGD": "S$",
    "HKD": "HK$",
}

# History direction (internal) + glyphs for UI
Tone = Literal["good", "bad", "neutral"]
Sign = Literal["up", "down", ""]  # up=▲ pricier · down=▼ cheaper

GLYPH_UP = "▲"
GLYPH_DOWN = "▼"


@dataclass(frozen=True)
class PriceDisplay:
    """Structured ~C$420▼ price signal for UI."""

    amount: float | None
    currency: str
    base: str  # ~C$420
    sign: Sign  # up | down | ""
    tone: Tone  # good=cheaper | bad=pricier | neutral
    glyph: str  # ▲ | ▼ | ""
    full: str  # ~C$420▼
    deal_label: str | None = None
    deal_score: int | None = None

    @property
    def html_class(self) -> str:
        if self.tone == "good":
            return "price-sign good"
        if self.tone == "bad":
            return "price-sign bad"
        return "price-sign neutral"


def currency_symbol(code: str | None) -> str:
    c = (code or "CAD").upper()
    return _SYMBOLS.get(c, f"{c} ")


def glyph_for_sign(sign: Sign | str | None) -> str:
    if sign in ("down", "-"):
        return GLYPH_DOWN
    if sign in ("up", "+"):
        return GLYPH_UP
    return ""


def format_money_amount(
    amount: float | None,
    currency: str | None = "CAD",
    *,
    round_to: int = 1,
) -> str:
    """Amount only with symbol: C$420 (no tilde, no hist glyph)."""
    if amount is None:
        return "—"
    sym = currency_symbol(currency)
    n = int(round(float(amount) / max(round_to, 1)) * max(round_to, 1))
    if sym.endswith(" "):
        return f"{sym}{n:,}"
    return f"{sym}{n:,}"


def format_approx(
    amount: float | None,
    currency: str | None = "CAD",
    *,
    round_to: int = 1,
    sign: Sign | str | None = None,
) -> str:
    """e.g. ~C$420 or ~C$420▼ / ~C$420▲ (tilde = not a locked fare)."""
    if amount is None:
        return "—"
    base = f"~{format_money_amount(amount, currency, round_to=round_to)}"
    g = glyph_for_sign(sign)
    return base + g if g else base


def hist_sign_from_deal(
    *,
    deal_label: str | None = None,
    deal_score: int | None = None,
    price: float | None = None,
    median: float | None = None,
) -> tuple[Sign, Tone]:
    """Map history / deal score → (up/down, tone). Cheaper = green ▼, pricier = red ▲."""
    label = (deal_label or "").lower()
    if label in ("great", "good"):
        return "down", "good"
    if label in ("high",):
        return "up", "bad"
    if label in ("new",) or not label:
        pass
    elif label == "ok":
        if price is not None and median is not None and median > 0:
            if price < median * 0.98:
                return "down", "good"
            if price > median * 1.02:
                return "up", "bad"
        return "", "neutral"

    if price is not None and median is not None and median > 0:
        if price <= median * 0.97:
            return "down", "good"
        if price >= median * 1.03:
            return "up", "bad"
        return "", "neutral"

    if deal_score is not None:
        if deal_score >= 60:
            return "down", "good"
        if deal_score <= 35:
            return "up", "bad"

    return "", "neutral"


def hist_sign_from_delta(delta: float | None, *, deadband: float = 5.0) -> tuple[Sign, Tone]:
    """Negative delta (cheaper than baseline) → green ▼; positive → red ▲."""
    if delta is None:
        return "", "neutral"
    if delta < -deadband:
        return "down", "good"
    if delta > deadband:
        return "up", "bad"
    return "", "neutral"


def price_display(
    amount: float | None,
    currency: str | None = "CAD",
    *,
    deal_label: str | None = None,
    deal_score: int | None = None,
    median: float | None = None,
    vs_delta: float | None = None,
    round_to: int = 1,
) -> PriceDisplay:
    """Build full display: prefer deal/history; else vs_delta (e.g. vs direct)."""
    cur = (currency or "CAD").upper()
    base = format_approx(amount, cur, round_to=round_to) if amount is not None else "—"
    sign: Sign = ""
    tone: Tone = "neutral"

    if amount is not None and (
        deal_label or deal_score is not None or median is not None
    ):
        sign, tone = hist_sign_from_deal(
            deal_label=deal_label,
            deal_score=deal_score,
            price=float(amount),
            median=median,
        )
    elif vs_delta is not None:
        sign, tone = hist_sign_from_delta(vs_delta)

    glyph = glyph_for_sign(sign)
    full = format_approx(amount, cur, round_to=round_to, sign=sign or None)
    return PriceDisplay(
        amount=float(amount) if amount is not None else None,
        currency=cur,
        base=base if amount is not None else "—",
        sign=sign,
        tone=tone,
        glyph=glyph,
        full=full,
        deal_label=deal_label,
        deal_score=deal_score,
    )
