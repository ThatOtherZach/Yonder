"""Detour candidate pool — quest-seeded flight legs with connection stops.

Quest priced legs that have one or more connection stops are harvested here
so the Detour section can surface them as ready-made options without a fresh
live search.  Candidates are deduplicated by route key (origin|stop|dest)
so re-runs refresh rather than duplicate.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Any

from yonder.db import get_conn


# ── helpers ───────────────────────────────────────────────────────────────────

def _route_key(origin: str, stop_iata: str, destination: str) -> str:
    return f"{origin.upper()}|{stop_iata.upper()}|{destination.upper()}"


def _age_label(harvested_at: float) -> str:
    age_s = time.time() - harvested_at
    if age_s < 3600:
        return "just now"
    if age_s < 86400:
        return f"{int(age_s / 3600)}h ago"
    if age_s < 7 * 86400:
        return f"{int(age_s / 86400)}d ago"
    return f"{int(age_s / 604800)}w ago"


# ── extraction ────────────────────────────────────────────────────────────────

def harvest_from_quest(
    quest_ideas: list,
    home_iata: str,  # noqa: ARG001 — kept for caller symmetry
    vibe: str = "",
) -> list[dict]:
    """Extract detour candidate dicts from a finished list of QuestIdea objects.

    Only legs that have at least one connection stop (stops_out >= 1) with a
    priced, non-fare-missing FlightOffer are harvested.  Returns candidate
    dicts ready for ``store_candidates()``.
    """
    candidates: list[dict] = []
    now = time.time()

    for qi in quest_ideas:
        for leg, direction in [
            (getattr(qi, "inbound_leg", None), "inbound"),
            (getattr(qi, "outbound_leg", None), "outbound"),
        ]:
            if leg is None:
                continue
            offer = getattr(leg, "offer", None)
            if offer is None:
                continue
            # Mock/sandbox skeletons never surface as options (demo prices
            # are internal-only).  Real fare-missing legs ARE kept: the API
            # confirmed the flight exists, we just lack a held fare — those
            # render as Check Fares cards pushing the affiliate link.
            price_kind = (getattr(offer, "price_kind", None) or "live").lower()
            if price_kind in ("mock", "sandbox"):
                continue
            fare_missing = bool(getattr(offer, "fare_missing", False))
            stops_out = getattr(offer, "stops_out", 0) or 0
            if stops_out < 1:
                continue  # direct flight — not a detour candidate
            segments_out = getattr(offer, "segments_out", None) or []
            if len(segments_out) < 2:
                continue  # need ≥ 2 segments for a connection
            # First segment's destination is the connection / stop city
            first_seg = segments_out[0]
            stop_iata = (getattr(first_seg, "destination", None) or "").upper()
            if not stop_iata or len(stop_iata) != 3 or not stop_iata.isalpha():
                continue
            origin = (getattr(leg, "from_iata", None) or "").upper()
            destination = (getattr(leg, "to_iata", None) or "").upper()
            if not origin or not destination or origin == destination:
                continue
            if stop_iata in (origin, destination):
                continue

            depart_date_val = getattr(leg, "depart_date", None)
            depart_date_iso = depart_date_val.isoformat() if depart_date_val else None

            price = None if fare_missing else getattr(offer, "price", None)
            currency = (getattr(offer, "currency", None) or "USD").upper()
            display_price = None if fare_missing else getattr(offer, "display_price", None)
            booking_url = (
                getattr(leg, "booking_url", None)
                or getattr(offer, "booking_url", None)
                or getattr(offer, "deep_link", None)
            )
            google_flights_url = (
                getattr(leg, "google_flights_url", None)
                or getattr(offer, "google_flights_url", None)
            )
            fare_note = getattr(offer, "fare_note", None)

            candidates.append({
                "route_key": _route_key(origin, stop_iata, destination),
                "origin": origin,
                "stop_iata": stop_iata,
                "stop_city": None,
                "destination": destination,
                "depart_date": depart_date_iso,
                "price": price,
                "currency": currency,
                "display_price": display_price,
                "booking_url": booking_url,
                "google_flights_url": google_flights_url,
                "fare_note": fare_note,
                "vibe": vibe or "",
                "source": "quest_eager",
                "leg_direction": direction,
                "harvested_at": now,
            })

    # Dedup within this batch by route key — keep the cheapest priced entry
    # so a route repeated across quest ideas renders exactly one card.
    best: dict[str, dict] = {}
    for c in candidates:
        k = c["route_key"]
        prev = best.get(k)
        if prev is None:
            best[k] = c
            continue
        c_price = c.get("price")
        p_price = prev.get("price")
        if p_price is None or (c_price is not None and c_price < p_price):
            best[k] = c
    return list(best.values())


def _harvest_from_saved_dict(
    data: dict[str, Any],
    vibe: str = "",
    harvested_at: float | None = None,
) -> list[dict]:
    """Extract detour candidates from a serialised QuestIdea dict (saved_itineraries).

    ``harvested_at`` should be the time the fare was actually priced/saved so
    snapshot age reflects fare freshness, not the backfill run time.
    """
    now = harvested_at or time.time()
    candidates: list[dict] = []
    for direction, leg_key in [("inbound", "inbound_leg"), ("outbound", "outbound_leg")]:
        leg_data = data.get(leg_key)
        if not leg_data or not isinstance(leg_data, dict):
            continue
        offer_data = leg_data.get("offer")
        if not offer_data or not isinstance(offer_data, dict):
            continue
        # Mock/sandbox skeletons never surface; real fare-missing legs are
        # kept as unpriced Check Fares candidates (flight confirmed to exist).
        price_kind = (offer_data.get("price_kind") or "live").lower()
        if price_kind in ("mock", "sandbox"):
            continue
        fare_missing = bool(offer_data.get("fare_missing", False))
        stops_out = offer_data.get("stops_out", 0) or 0
        if stops_out < 1:
            continue
        segments_out = offer_data.get("segments_out") or []
        if len(segments_out) < 2:
            continue
        first_seg = segments_out[0]
        if not isinstance(first_seg, dict):
            continue
        stop_iata = (first_seg.get("destination") or "").upper()
        if not stop_iata or len(stop_iata) != 3 or not stop_iata.isalpha():
            continue
        origin = (leg_data.get("from_iata") or "").upper()
        destination = (leg_data.get("to_iata") or "").upper()
        if not origin or not destination or origin == destination:
            continue
        if stop_iata in (origin, destination):
            continue
        price = None if fare_missing else offer_data.get("price")
        candidates.append({
            "route_key": _route_key(origin, stop_iata, destination),
            "origin": origin,
            "stop_iata": stop_iata,
            "stop_city": None,
            "destination": destination,
            "depart_date": leg_data.get("depart_date"),
            "price": price,
            "currency": (offer_data.get("currency") or "USD").upper(),
            "display_price": None if fare_missing else offer_data.get("display_price"),
            "booking_url": leg_data.get("booking_url") or offer_data.get("booking_url"),
            "google_flights_url": (
                leg_data.get("google_flights_url") or offer_data.get("google_flights_url")
            ),
            "fare_note": offer_data.get("fare_note"),
            "vibe": vibe,
            "source": "quest_saved",
            "leg_direction": direction,
            "harvested_at": now,
        })
    return candidates


# ── storage ───────────────────────────────────────────────────────────────────

def store_candidates(candidates: list[dict]) -> int:
    """Upsert detour candidates into the DB. Returns number stored."""
    if not candidates:
        return 0
    stored = 0
    try:
        with get_conn() as conn:
            for c in candidates:
                try:
                    conn.execute(
                        """
                        INSERT INTO detour_candidates
                            (route_key, origin, stop_iata, stop_city, destination,
                             depart_date, price, currency, display_price,
                             booking_url, google_flights_url, fare_note,
                             vibe, source, leg_direction, harvested_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (route_key) DO UPDATE SET
                            depart_date        = EXCLUDED.depart_date,
                            price              = EXCLUDED.price,
                            currency           = EXCLUDED.currency,
                            display_price      = EXCLUDED.display_price,
                            booking_url        = EXCLUDED.booking_url,
                            google_flights_url = EXCLUDED.google_flights_url,
                            fare_note          = EXCLUDED.fare_note,
                            vibe               = EXCLUDED.vibe,
                            source             = EXCLUDED.source,
                            leg_direction      = EXCLUDED.leg_direction,
                            harvested_at       = EXCLUDED.harvested_at
                        WHERE EXCLUDED.harvested_at >= detour_candidates.harvested_at
                        """,
                        (
                            c["route_key"],
                            c["origin"],
                            c["stop_iata"],
                            c.get("stop_city"),
                            c["destination"],
                            c.get("depart_date"),
                            c.get("price"),
                            c.get("currency", "USD"),
                            c.get("display_price"),
                            c.get("booking_url"),
                            c.get("google_flights_url"),
                            c.get("fare_note"),
                            c.get("vibe", ""),
                            c.get("source", "quest"),
                            c.get("leg_direction"),
                            c.get("harvested_at", time.time()),
                        ),
                    )
                    stored += 1
                except Exception:  # noqa: BLE001
                    pass
            conn.commit()
    except Exception:  # noqa: BLE001
        return stored
    return stored


# ── query ─────────────────────────────────────────────────────────────────────

def find_candidates(
    origin: str,
    *,
    destinations: list[str] | None = None,
    depart_date_iso: str | None = None,
    date_window_days: int = 30,
    max_age_days: int = 90,
    limit: int = 5,
) -> list[dict]:
    """Return detour candidates matching the current search.

    Matching is route + date proximity + max snapshot age:
    - ``origin`` must match exactly (required)
    - ``destinations`` — when given, the candidate's destination must be one
      of these IATAs (the current query's relevant endpoints)
    - ``depart_date_iso`` — when given, candidate depart_date must fall
      within ±``date_window_days`` (NULL depart_date passes)
    - candidates older than ``max_age_days`` never match

    Ordered by freshness (newest first).  Each returned dict has an
    ``age_label`` field computed at call time.
    """
    origin_n = (origin or "").strip().upper()
    if not origin_n:
        return []
    cutoff_age = time.time() - max_age_days * 86400

    dest_list = [
        d.strip().upper() for d in (destinations or [])
        if d and len(d.strip()) == 3 and d.strip().isalpha()
    ]

    where = ["origin = %s", "harvested_at >= %s"]
    params: list = [origin_n, cutoff_age]

    if dest_list:
        where.append("destination = ANY(%s)")
        params.append(dest_list)

    if depart_date_iso:
        try:
            center = date.fromisoformat(depart_date_iso)
            lo = (center - timedelta(days=date_window_days)).isoformat()
            hi = (center + timedelta(days=date_window_days)).isoformat()
            where.append("(depart_date IS NULL OR (depart_date >= %s AND depart_date <= %s))")
            params.extend([lo, hi])
        except ValueError:
            pass

    params.append(limit)
    sql = (
        "SELECT * FROM detour_candidates WHERE "
        + " AND ".join(where)
        + " ORDER BY harvested_at DESC LIMIT %s"
    )
    try:
        with get_conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception:  # noqa: BLE001
        return []

    result = []
    for r in rows:
        d = dict(r)
        d["age_label"] = _age_label(d.get("harvested_at") or 0.0)
        result.append(d)
    return result


# ── backfill ──────────────────────────────────────────────────────────────────

def backfill_from_saved_quests() -> int:
    """Populate detour_candidates from saved_itineraries WHERE kind='quest'.

    Safe to run multiple times — UPSERT dedup ensures re-runs just refresh
    existing entries.  Returns total candidates stored.
    """
    total = 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT itinerary_json, vibe, priced_at, saved_at FROM saved_itineraries"
                " WHERE kind = 'quest' LIMIT 500"
            ).fetchall()
    except Exception:  # noqa: BLE001
        return 0

    for row in rows:
        try:
            raw = row["itinerary_json"]
            if not raw:
                continue
            data = json.loads(raw)
            vibe = row.get("vibe") or ""
            # Snapshot age must reflect when the fare was priced, not when
            # this backfill ran — otherwise old fares look like fresh ones.
            _ts = row.get("priced_at") or row.get("saved_at") or None
            candidates = _harvest_from_saved_dict(data, vibe=vibe, harvested_at=_ts)
            if candidates:
                total += store_candidates(candidates)
        except Exception:  # noqa: BLE001
            pass
    return total
