"""Persistent cache of fare range estimates per route + month.

Each successful /api/leg-fare response upserts a low/high range for
(origin, destination, year_month, currency).  /api/fare-estimate reads this
cache and returns a formatted label — "~$680–$1,240" — without a live API
call.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from yonder.config import ROOT

DB_PATH = ROOT / "fare_estimates.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fare_estimates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            origin       TEXT NOT NULL,
            destination  TEXT NOT NULL,
            year_month   TEXT NOT NULL,
            currency     TEXT NOT NULL DEFAULT 'USD',
            price_low    REAL NOT NULL,
            price_high   REAL NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 1,
            sampled_at   TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS fare_estimates_route_month
            ON fare_estimates(origin, destination, year_month, currency);
        """
    )
    conn.commit()
    return conn


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
        conn = _connect()
        # Check for existing row
        row = conn.execute(
            "SELECT id, price_low, price_high, sample_count FROM fare_estimates "
            "WHERE origin=? AND destination=? AND year_month=? AND currency=?",
            (origin, destination, year_month, currency),
        ).fetchone()
        if row:
            new_low = min(float(row["price_low"]), price)
            new_high = max(float(row["price_high"]), price)
            conn.execute(
                "UPDATE fare_estimates SET price_low=?, price_high=?, "
                "sample_count=?, sampled_at=? WHERE id=?",
                (new_low, new_high, row["sample_count"] + 1, now_iso, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO fare_estimates "
                "(origin, destination, year_month, currency, price_low, price_high, sample_count, sampled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (origin, destination, year_month, currency, price, price, now_iso),
            )
        conn.commit()
        conn.close()
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
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM fare_estimates "
            "WHERE origin=? AND destination=? AND year_month=? AND currency=?",
            (origin, destination, year_month, currency),
        ).fetchone()
        stale = False
        if not row and prev_month:
            row = conn.execute(
                "SELECT * FROM fare_estimates "
                "WHERE origin=? AND destination=? AND year_month=? AND currency=?",
                (origin, destination, prev_month, currency),
            ).fetchone()
            stale = True
        conn.close()
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
