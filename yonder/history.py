"""Local price journal — every search builds your dataset."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from yonder.db import get_conn
from yonder.types import FlightOffer, SearchQuery

def record_offer(
    query: SearchQuery,
    offer: FlightOffer,
    *,
    observed_at: datetime | None = None,
    model_source: str | None = None,
) -> bool:
    """Append one fare observation.  Returns True when written, False when skipped.

    model_source: AI backend label ("Grok (Server)", "BYOM, …") when this row
    came from an AI-planned search; None for legacy/non-AI rows.
    """
    if offer.price_kind == "mock":
        return False  # don't pollute history with demo
    ts = (observed_at or datetime.now(timezone.utc)).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO price_samples (
                origin, destination, depart_date, return_date,
                price, currency, source, price_kind, stops, airlines,
                duration_minutes, notes, google_flights_url, deep_link, raw_id, observed_at,
                model_source
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                (model_source or "").strip() or None,
            ),
        )
        conn.commit()
    return True


def record_offers(query: SearchQuery, offers: Iterable[FlightOffer]) -> int:
    n = 0
    for o in offers:
        try:
            if record_offer(query, o):
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
    model_source: str | None = None,
) -> None:
    """Log a one-way adventure leg."""
    q = SearchQuery(
        origin=origin.upper(),
        destination=destination.upper(),
        depart_date=depart,
        currency=currency.upper(),
        max_results=1,
    )
    record_offer(q, offer, model_source=model_source)


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
    with get_conn() as conn:
        from datetime import timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(days_back))
        ).isoformat()
        sql = """
            SELECT price, currency, observed_at, price_kind FROM price_samples
            WHERE origin = %s AND destination = %s
              AND observed_at >= %s
        """
        params: list[Any] = [o, d, cutoff]
        if cur:
            sql += " AND currency = %s"
            params.append(cur)
        if exclude_sandbox:
            sql += " AND COALESCE(price_kind, '') NOT IN ('sandbox', 'mock')"
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
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM price_samples").fetchone()
        return int(row["c"] if row else 0)


def recent_samples(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT origin, destination, depart_date, price, currency, source,
                   price_kind, observed_at
            FROM price_samples
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


_EXPORT_COLUMNS = (
    "origin", "destination", "depart_date", "return_date", "price", "currency",
    "source", "price_kind", "stops", "airlines", "duration_minutes", "notes",
    "google_flights_url", "deep_link", "raw_id", "observed_at", "model_source",
)


def export_all() -> list[dict[str, Any]]:
    """All price samples as raw row dicts (without local ids) — for backup export."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM price_samples ORDER BY id").fetchall()
    return [{k: r[k] for k in _EXPORT_COLUMNS} for r in rows]


def _sample_key(row: dict[str, Any]) -> tuple:
    try:
        price = round(float(row.get("price") or 0), 2)
    except (TypeError, ValueError):
        price = 0.0
    return (
        str(row.get("origin") or "").upper(),
        str(row.get("destination") or "").upper(),
        str(row.get("depart_date") or ""),
        str(row.get("observed_at") or ""),
        price,
        str(row.get("source") or ""),
    )


def import_samples(items: list[dict[str, Any]]) -> tuple[int, int]:
    """Restore exported price samples. Returns (imported, skipped).

    Dedupes on (origin, destination, depart_date, observed_at, price, source)
    so re-imports never double-count observations.
    """
    imported = skipped = 0
    with get_conn() as conn:
        existing = {
            _sample_key({c: r[c] for c in _EXPORT_COLUMNS})
            for r in conn.execute("SELECT * FROM price_samples").fetchall()
        }
        placeholders = ",".join(["%s"] * len(_EXPORT_COLUMNS))
        for raw in items:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            origin = str(raw.get("origin") or "").strip().upper()
            dest = str(raw.get("destination") or "").strip().upper()
            if not origin or not dest or not raw.get("depart_date") or not raw.get("observed_at"):
                skipped += 1
                continue
            try:
                price = float(raw["price"])
            except (TypeError, KeyError, ValueError):
                skipped += 1
                continue
            row = dict(raw)
            row["origin"] = origin
            row["destination"] = dest
            row["price"] = price
            row["currency"] = str(raw.get("currency") or "USD").upper()
            row["source"] = str(raw.get("source") or "import")
            key = _sample_key(row)
            if key in existing:
                skipped += 1
                continue
            conn.execute(
                f"INSERT INTO price_samples ({', '.join(_EXPORT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                tuple(row.get(c) for c in _EXPORT_COLUMNS),
            )
            existing.add(key)
            imported += 1
        conn.commit()
    return imported, skipped


def export_jsonl(path: Path | None = None) -> Path:
    out = path or (Path(__file__).resolve().parent.parent / "price_history_export.jsonl")
    with get_conn() as conn, out.open("w", encoding="utf-8") as f:
        for row in conn.execute("SELECT * FROM price_samples ORDER BY id").fetchall():
            f.write(json.dumps(dict(row), default=str) + "\n")
    return out
