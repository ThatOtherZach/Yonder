"""Local price journal — every search builds your dataset."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from yonder.config import ROOT
from yonder.types import FlightOffer, SearchQuery

DB_PATH = ROOT / "price_history.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            depart_date TEXT NOT NULL,
            return_date TEXT,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            source TEXT NOT NULL,
            price_kind TEXT,
            stops INTEGER,
            airlines TEXT,
            duration_minutes INTEGER,
            notes TEXT,
            google_flights_url TEXT,
            deep_link TEXT,
            raw_id TEXT,
            observed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_route_date
        ON price_samples(origin, destination, depart_date, observed_at)
        """
    )
    conn.commit()
    return conn


def record_offer(
    query: SearchQuery,
    offer: FlightOffer,
    *,
    observed_at: datetime | None = None,
) -> None:
    """Append one fare observation."""
    if offer.price_kind == "mock":
        return  # don't pollute history with demo
    ts = (observed_at or datetime.now(timezone.utc)).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO price_samples (
                origin, destination, depart_date, return_date,
                price, currency, source, price_kind, stops, airlines,
                duration_minutes, notes, google_flights_url, deep_link, raw_id, observed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                query.origin.upper(),
                query.destination.upper(),
                query.depart_date.isoformat(),
                query.return_date.isoformat() if query.return_date else None,
                float(offer.price),
                (offer.currency or query.currency or "CAD").upper(),
                offer.provider,
                offer.price_kind or "live",
                int(offer.stops_out or 0),
                ",".join(offer.airlines or []),
                offer.duration_out_minutes,
                offer.notes,
                offer.google_flights_url,
                offer.deep_link,
                offer.raw_id,
                ts,
            ),
        )
        conn.commit()


def record_offers(query: SearchQuery, offers: Iterable[FlightOffer]) -> int:
    n = 0
    for o in offers:
        try:
            record_offer(query, o)
            n += 1
        except Exception:
            continue
    return n


def record_leg(
    *,
    origin: str,
    destination: str,
    depart: date,
    offer: FlightOffer,
    currency: str = "CAD",
) -> None:
    """Log a one-way adventure leg."""
    q = SearchQuery(
        origin=origin.upper(),
        destination=destination.upper(),
        depart_date=depart,
        currency=currency.upper(),
        max_results=1,
    )
    record_offer(q, offer)


@dataclass
class RouteStats:
    origin: str
    destination: str
    n: int
    min_price: float | None
    p25: float | None
    median: float | None
    p75: float | None
    max_price: float | None
    currency: str
    last_seen: str | None

    def deal_score(self, price: float) -> tuple[int, str]:
        """0–100 (higher = better deal) + label, vs stored samples for this route."""
        if self.n < 1 or self.median is None:
            return 50, "new"  # no history yet
        if self.n < 3:
            # thin history — simple vs min/median
            if self.min_price and price <= self.min_price * 1.05:
                return 85, "great"
            if price <= self.median:
                return 65, "good"
            if self.p75 and price <= self.p75:
                return 45, "ok"
            return 25, "high"

        # Percentile-ish: fraction of samples this price beats
        # Approximate with quartiles
        if self.min_price is not None and price <= self.min_price:
            return 95, "great"
        if self.p25 is not None and price <= self.p25:
            return 80, "great"
        if self.median is not None and price <= self.median:
            return 60, "good"
        if self.p75 is not None and price <= self.p75:
            return 40, "ok"
        return 20, "high"


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def route_stats(
    origin: str,
    destination: str,
    *,
    currency: str | None = None,
    days_back: int = 365,
    exclude_sandbox: bool = True,
) -> RouteStats:
    o, d = origin.upper(), destination.upper()
    cur = (currency or "").upper() or None
    with _connect() as conn:
        sql = """
            SELECT price, currency, observed_at, price_kind FROM price_samples
            WHERE origin = ? AND destination = ?
              AND observed_at >= datetime('now', ?)
        """
        params: list[Any] = [o, d, f"-{int(days_back)} days"]
        if cur:
            sql += " AND currency = ?"
            params.append(cur)
        if exclude_sandbox:
            sql += " AND IFNULL(price_kind, '') NOT IN ('sandbox', 'mock')"
        rows = conn.execute(sql, params).fetchall()

    prices = sorted(float(r["price"]) for r in rows)
    curr = cur or (rows[0]["currency"] if rows else "CAD")
    last = rows[0]["observed_at"] if rows else None
    # order by observed for last_seen
    if rows:
        last = max(r["observed_at"] for r in rows)

    return RouteStats(
        origin=o,
        destination=d,
        n=len(prices),
        min_price=prices[0] if prices else None,
        p25=_percentile(prices, 0.25),
        median=_percentile(prices, 0.5),
        p75=_percentile(prices, 0.75),
        max_price=prices[-1] if prices else None,
        currency=curr,
        last_seen=last,
    )


def count_samples() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM price_samples").fetchone()
        return int(row["c"] if row else 0)


def recent_samples(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT origin, destination, depart_date, price, currency, source,
                   price_kind, observed_at
            FROM price_samples
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def export_jsonl(path: Path | None = None) -> Path:
    out = path or (ROOT / "price_history_export.jsonl")
    with _connect() as conn, out.open("w", encoding="utf-8") as f:
        for row in conn.execute("SELECT * FROM price_samples ORDER BY id"):
            f.write(json.dumps(dict(row), default=str) + "\n")
    return out
