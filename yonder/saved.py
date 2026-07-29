"""Local saved itineraries — snapshot prices, refresh on demand."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from yonder.config import ROOT
from yonder.money import format_approx

DB_PATH = ROOT / "saved_itineraries.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_itineraries (
            id TEXT PRIMARY KEY,
            saved_at REAL NOT NULL,
            priced_at REAL,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            currency TEXT NOT NULL,
            total_price REAL,
            display_price TEXT,
            stop_city TEXT,
            stop_iata TEXT,
            stay_days INTEGER,
            origin TEXT,
            destination TEXT,
            adults INTEGER DEFAULT 1,
            cabin TEXT DEFAULT 'economy',
            vibe TEXT,
            trip_prompt TEXT,
            theme_country TEXT,
            theme_primary TEXT,
            theme_accent TEXT,
            theme_gradient TEXT,
            theme_flag_img TEXT,
            theme_label TEXT,
            google_flights_url TEXT,
            kayak_url TEXT,
            ground_display TEXT,
            ground_compare_line TEXT,
            all_in_display TEXT,
            notes_json TEXT,
            itinerary_json TEXT NOT NULL,
            trip_meta_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_saved_at
        ON saved_itineraries(saved_at DESC)
        """
    )
    conn.commit()
    return conn


@dataclass
class SavedItinerary:
    id: str
    saved_at: float
    priced_at: float | None
    title: str
    kind: str
    currency: str
    total_price: float | None
    display_price: str | None
    stop_city: str | None
    stop_iata: str | None
    stay_days: int | None
    origin: str | None
    destination: str | None
    adults: int
    cabin: str
    vibe: str | None
    trip_prompt: str | None
    theme_country: str | None
    theme_primary: str | None
    theme_accent: str | None
    theme_gradient: str | None
    theme_flag_img: str | None
    theme_label: str | None
    google_flights_url: str | None
    kayak_url: str | None
    ground_display: str | None
    ground_compare_line: str | None
    all_in_display: str | None
    notes: list[str]
    itinerary: dict[str, Any]
    trip_meta: dict[str, Any]

    @property
    def saved_at_iso(self) -> str:
        return datetime.fromtimestamp(self.saved_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    @property
    def priced_at_iso(self) -> str | None:
        if self.priced_at is None:
            return None
        return datetime.fromtimestamp(self.priced_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    @property
    def price_age_hours(self) -> float | None:
        if self.priced_at is None:
            return None
        return max(0.0, (time.time() - self.priced_at) / 3600)

    @property
    def price_freshness(self) -> str:
        """fresh | aging | stale | unknown — for UI badge color."""
        h = self.price_age_hours
        if h is None:
            return "unknown"
        if h < 6:
            return "fresh"
        if h < 48:
            return "aging"
        return "stale"

    @property
    def price_stale_label(self) -> str:
        """Human age of the snapshot fare."""
        if self.priced_at is None:
            return "no fare yet"
        age_h = self.price_age_hours or 0.0
        if age_h < 0.2:
            return "just now"
        if age_h < 1:
            return f"~{max(1, int(age_h * 60))}m ago"
        if age_h < 48:
            return f"~{int(age_h)}h ago"
        days = int(age_h / 24)
        return f"~{days}d ago"

    @property
    def last_refresh_status(self) -> str | None:
        return (self.trip_meta or {}).get("last_refresh_status")

    @property
    def last_refresh_message(self) -> str | None:
        return (self.trip_meta or {}).get("last_refresh_message")

    @property
    def last_refresh_delta(self) -> float | None:
        d = (self.trip_meta or {}).get("last_refresh_delta")
        if d is None:
            return None
        try:
            return float(d)
        except (TypeError, ValueError):
            return None


def _row_to_saved(row: sqlite3.Row) -> SavedItinerary:
    notes_raw = row["notes_json"] or "[]"
    meta_raw = row["trip_meta_json"] or "{}"
    it_raw = row["itinerary_json"] or "{}"
    try:
        notes = json.loads(notes_raw)
        if not isinstance(notes, list):
            notes = []
    except json.JSONDecodeError:
        notes = []
    try:
        trip_meta = json.loads(meta_raw)
        if not isinstance(trip_meta, dict):
            trip_meta = {}
    except json.JSONDecodeError:
        trip_meta = {}
    try:
        itinerary = json.loads(it_raw)
        if not isinstance(itinerary, dict):
            itinerary = {}
    except json.JSONDecodeError:
        itinerary = {}
    return SavedItinerary(
        id=row["id"],
        saved_at=float(row["saved_at"]),
        priced_at=float(row["priced_at"]) if row["priced_at"] is not None else None,
        title=row["title"] or "Untitled",
        kind=row["kind"] or "stopover",
        currency=(row["currency"] or "CAD").upper(),
        total_price=float(row["total_price"]) if row["total_price"] is not None else None,
        display_price=row["display_price"],
        stop_city=row["stop_city"],
        stop_iata=row["stop_iata"],
        stay_days=int(row["stay_days"]) if row["stay_days"] is not None else None,
        origin=row["origin"],
        destination=row["destination"],
        adults=int(row["adults"] or 1),
        cabin=row["cabin"] or "economy",
        vibe=row["vibe"],
        trip_prompt=row["trip_prompt"],
        theme_country=row["theme_country"],
        theme_primary=row["theme_primary"],
        theme_accent=row["theme_accent"],
        theme_gradient=row["theme_gradient"],
        theme_flag_img=row["theme_flag_img"],
        theme_label=row["theme_label"],
        google_flights_url=row["google_flights_url"],
        kayak_url=row["kayak_url"],
        ground_display=row["ground_display"],
        ground_compare_line=row["ground_compare_line"],
        all_in_display=row["all_in_display"],
        notes=[str(n) for n in notes],
        itinerary=itinerary,
        trip_meta=trip_meta,
    )


def _legs_origin_dest(it: dict[str, Any]) -> tuple[str | None, str | None]:
    legs = it.get("legs") or []
    if not legs:
        return None, None
    first, last = legs[0], legs[-1]
    o = first.get("from_iata") or first.get("from")
    d = last.get("to_iata") or last.get("to")
    return (
        str(o).upper() if o else None,
        str(d).upper() if d else None,
    )


def save_itinerary(
    itinerary: dict[str, Any],
    *,
    trip_meta: dict[str, Any] | None = None,
    replace_id: str | None = None,
) -> SavedItinerary:
    """Persist an adventure itinerary snapshot. Prices are frozen until refresh."""
    meta = dict(trip_meta or {})
    origin, dest = _legs_origin_dest(itinerary)
    currency = (itinerary.get("currency") or meta.get("currency") or "USD").upper()
    total = itinerary.get("total_price")
    display = itinerary.get("display_price")
    if total is not None and not display:
        display = format_approx(float(total), currency)
    notes = itinerary.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]

    # Prefer explicit vibe color from trip_meta (page theme) over country theme
    vibe_slug = meta.get("vibe") or itinerary.get("vibe")
    vibe_color = meta.get("vibe_color") or itinerary.get("theme_primary")
    vibe_label = meta.get("vibe_label")
    if vibe_slug and not vibe_color:
        try:
            from yonder.vibe_theme import vibe_theme as _vt

            vt = _vt(str(vibe_slug))
            vibe_color = vt["color"]
            vibe_label = vibe_label or vt["label"]
        except Exception:
            pass
    theme_primary = vibe_color or itinerary.get("theme_primary")
    theme_accent = itinerary.get("theme_accent")
    if vibe_color and not theme_accent:
        try:
            from yonder.vibe_theme import darken

            theme_accent = darken(str(vibe_color), 0.32)
        except Exception:
            theme_accent = vibe_color
    theme_label = vibe_label or itinerary.get("theme_label")

    now = time.time()
    sid = replace_id or str(uuid.uuid4())
    priced_at = now if total is not None else None

    # Keep prior saved_at / priced_at when updating after refresh
    existing = get(sid) if replace_id else None
    saved_at = existing.saved_at if existing else now
    refresh_status = str(meta.get("last_refresh_status") or "")
    if replace_id and total is not None:
        # Pure snapshot fallback = still "out of date" — don't reset freshness clock
        if refresh_status == "snapshot" and existing and existing.priced_at is not None:
            priced_at = existing.priced_at
        else:
            priced_at = now  # live or mixed: we learned something new
    elif existing and total is None:
        priced_at = existing.priced_at

    # Keep vibe color on the frozen itinerary JSON too
    if vibe_color:
        itinerary = dict(itinerary)
        itinerary["theme_primary"] = theme_primary
        itinerary["theme_accent"] = theme_accent
        if theme_label:
            itinerary["theme_label"] = theme_label
        if vibe_slug:
            itinerary["vibe"] = vibe_slug
        meta.setdefault("vibe_color", vibe_color)
        if vibe_slug:
            meta.setdefault("vibe", vibe_slug)

    row = (
        sid,
        saved_at,
        priced_at,
        str(itinerary.get("title") or "Saved trip"),
        str(itinerary.get("kind") or "stopover"),
        currency,
        float(total) if total is not None else None,
        display,
        itinerary.get("stop_city"),
        itinerary.get("stop_iata"),
        itinerary.get("stay_days"),
        origin or meta.get("origin"),
        dest or meta.get("destination"),
        int(meta.get("adults") or 1),
        str(meta.get("cabin") or "economy"),
        vibe_slug or meta.get("vibe") or itinerary.get("why"),
        meta.get("prompt") or meta.get("trip_prompt"),
        itinerary.get("theme_country"),
        theme_primary,
        theme_accent,
        itinerary.get("theme_gradient"),
        itinerary.get("theme_flag_img"),
        theme_label,
        itinerary.get("google_flights_url"),
        itinerary.get("kayak_url"),
        itinerary.get("ground_display"),
        itinerary.get("ground_compare_line"),
        itinerary.get("all_in_display"),
        json.dumps(notes, default=str),
        json.dumps(itinerary, default=str),
        json.dumps(meta, default=str),
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO saved_itineraries (
                id, saved_at, priced_at, title, kind, currency, total_price,
                display_price, stop_city, stop_iata, stay_days, origin, destination,
                adults, cabin, vibe, trip_prompt, theme_country, theme_primary,
                theme_accent, theme_gradient, theme_flag_img, theme_label,
                google_flights_url, kayak_url, ground_display, ground_compare_line,
                all_in_display, notes_json, itinerary_json, trip_meta_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        conn.commit()
    saved = get(sid)
    assert saved is not None
    return saved


def get(saved_id: str) -> SavedItinerary | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM saved_itineraries WHERE id = ?", (saved_id,)
        ).fetchone()
    return _row_to_saved(row) if row else None


def list_saved(*, limit: int = 50) -> list[SavedItinerary]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM saved_itineraries
            ORDER BY saved_at DESC
            LIMIT ?
            """,
            (max(1, min(200, limit)),),
        ).fetchall()
    return [_row_to_saved(r) for r in rows]


def delete(saved_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM saved_itineraries WHERE id = ?", (saved_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def count_saved() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM saved_itineraries").fetchone()
    return int(row["n"]) if row else 0


def update_from_itinerary(
    saved_id: str,
    itinerary: dict[str, Any],
    *,
    trip_meta: dict[str, Any] | None = None,
) -> SavedItinerary | None:
    existing = get(saved_id)
    if not existing:
        return None
    meta = {**existing.trip_meta, **(trip_meta or {})}
    return save_itinerary(itinerary, trip_meta=meta, replace_id=saved_id)
