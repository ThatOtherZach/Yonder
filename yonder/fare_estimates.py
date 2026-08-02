"""Persistent cache of fare range estimates per route + month.

Each successful /api/leg-fare response upserts a low/high range for
(origin, destination, year_month, currency).  /api/fare-estimate reads this
cache and returns a formatted label — "~$680–$1,240" — without a live API
call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from yonder.db import get_conn


def upsert_estimate(
    origin: str,
    destination: str,
    price: float,
    currency: str,
    *,
    year_month: str | None = None,
) -> None:
    """Insert or update a fare sample for this route and month.

    Widens the low/high range and increments sample_count.
    """
    if not origin or not destination or price <= 0:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    if not year_month:
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id, price_low, price_high, sample_count FROM fare_estimates "
                "WHERE origin=%s AND destination=%s AND year_month=%s AND currency=%s",
                (origin, destination, year_month, currency),
            ).fetchone()
            if row:
                new_low = min(float(row["price_low"]), price)
                new_high = max(float(row["price_high"]), price)
                conn.execute(
                    "UPDATE fare_estimates SET price_low=%s, price_high=%s, "
                    "sample_count=%s, sampled_at=%s WHERE id=%s",
                    (new_low, new_high, row["sample_count"] + 1, now_iso, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO fare_estimates "
                    "(origin, destination, year_month, currency, price_low, price_high, sample_count, sampled_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
                    (origin, destination, year_month, currency, price, price, now_iso),
                )
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


def get_estimate(
    origin: str,
    destination: str,
    currency: str,
    *,
    year_month: str | None = None,
) -> dict | None:
    """Return cached fare estimate for a route + month, or None.

    Falls back to the previous month when the current month has no data.
    Returns a dict with keys: origin, destination, currency, year_month,
    price_low, price_high, sample_count, stale (bool), label.
    """
    if not origin or not destination:
        return None
    from datetime import date

    if not year_month:
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")

    # Previous month for fallback
    try:
        y, m = map(int, year_month.split("-"))
        if m == 1:
            prev_month = f"{y - 1:04d}-12"
        else:
            prev_month = f"{y:04d}-{m - 1:02d}"
    except Exception:  # noqa: BLE001
        prev_month = None

    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM fare_estimates "
                "WHERE origin=%s AND destination=%s AND year_month=%s AND currency=%s",
                (origin, destination, year_month, currency),
            ).fetchone()
            stale = False
            if not row and prev_month:
                row = conn.execute(
                    "SELECT * FROM fare_estimates "
                    "WHERE origin=%s AND destination=%s AND year_month=%s AND currency=%s",
                    (origin, destination, prev_month, currency),
                ).fetchone()
                stale = True
        if not row:
            return None
        low = row["price_low"]
        high = row["price_high"]
        # Format with commas for large numbers
        label = _fmt_label(currency, low, high)
        return {
            "origin": origin,
            "destination": destination,
            "currency": currency,
            "year_month": row["year_month"],
            "price_low": low,
            "price_high": high,
            "sample_count": row["sample_count"],
            "stale": stale,
            "label": label,
        }
    except Exception:  # noqa: BLE001
        return None


def _fmt_label(currency: str, low: float, high: float) -> str:
    """Format '~$680–$1,240' style label."""
    sym = _currency_symbol(currency)

    def _fmt(v: float) -> str:
        i = int(round(v))
        # Add commas
        return f"{i:,}"

    if abs(high - low) < 1:
        return f"~{sym}{_fmt(low)}"
    return f"~{sym}{_fmt(low)}–{sym}{_fmt(high)}"


def _currency_symbol(currency: str) -> str:
    _SYMS = {
        "USD": "$",
        "CAD": "$",
        "AUD": "$",
        "NZD": "$",
        "SGD": "$",
        "HKD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "INR": "₹",
        "CHF": "Fr",
        "SEK": "kr",
        "NOK": "kr",
        "DKK": "kr",
        "MXN": "$",
        "BRL": "R$",
        "ZAR": "R",
        "THB": "฿",
        "KRW": "₩",
    }
    return _SYMS.get((currency or "USD").upper(), "$")
