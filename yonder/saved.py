"""Local saved itineraries — snapshot prices, refresh on demand."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from yonder.db import get_conn
from yonder.money import format_approx

SAVE_LIMIT: int = int(os.getenv("SAVED_TRIP_LIMIT", "8"))


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
    owner_sess: str | None = None

    @property
    def model_source(self) -> str:
        """AI backend label ("Grok (Server)", "BYOM, …") or "" for legacy/unknown."""
        val = self.trip_meta.get("model_source") or self.itinerary.get("model_source")
        return str(val or "").strip()

    @property
    def saved_at_iso(self) -> str:
        return datetime.fromtimestamp(self.saved_at, tz=timezone.utc).strftime(
            "%B %d, %Y"
        )

    @property
    def priced_at_iso(self) -> str | None:
        if self.priced_at is None:
            return None
        return datetime.fromtimestamp(self.priced_at, tz=timezone.utc).strftime(
            "%B %d, %Y"
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
            return f"~{max(1, int(age_h * 60))} Minutes"
        if age_h < 48:
            h = int(age_h)
            return f"~{h} Hour" if h == 1 else f"~{h} Hours"
        days = int(age_h / 24)
        return f"~{days} Day" if days == 1 else f"~{days} Days"

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

    @property
    def itinerary_dates(self) -> list[date]:
        return itinerary_dates(self.itinerary)

    @property
    def is_expired(self) -> bool:
        dates = self.itinerary_dates
        return bool(dates) and max(dates) < date.today()

    @property
    def first_departure_date(self) -> str | None:
        dates = self.itinerary_dates
        return min(dates).isoformat() if dates else None

    @property
    def minimum_reschedule_date(self) -> str:
        return date.today().isoformat()


def _row_to_saved(row: dict[str, Any]) -> SavedItinerary:
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
        owner_sess=row.get("owner_sess") or None,
    )


_SHIFT_DATE_KEYS = {"depart_date", "outbound_date", "return_date"}
_DATE_BOUND_URL_KEYS = {"booking_url", "google_flights_url", "kayak_url"}
_STALE_FARE_KEYS = {
    "all_in_display",
    "display_price",
    "display_price_base",
    "display_total",
    "offer",
    "price_glyph",
    "price_sign",
    "price_tone",
    "total_price",
}


def itinerary_dates(itinerary: dict[str, Any]) -> list[date]:
    """Return the actual travel dates represented by an itinerary."""
    found: list[date] = []

    def walk(value: Any, key: str = "") -> None:
        if key in _SHIFT_DATE_KEYS and value:
            try:
                found.append(date.fromisoformat(str(value)[:10]))
            except (TypeError, ValueError):
                pass
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(itinerary or {})
    return sorted(set(found))


def shift_itinerary_dates(
    itinerary: dict[str, Any], new_departure: date
) -> dict[str, Any]:
    """Shift every travel date by the first departure's offset."""
    dates = itinerary_dates(itinerary)
    if not dates:
        raise ValueError("This trip has no travel dates to update")
    offset = new_departure - min(dates)
    shifted = deepcopy(itinerary)

    def walk(value: Any, key: str = "") -> Any:
        if key in _SHIFT_DATE_KEYS and value:
            try:
                return (date.fromisoformat(str(value)[:10]) + offset).isoformat()
            except (TypeError, ValueError):
                return value
        if isinstance(value, dict):
            return {
                child_key: (
                    None
                    if str(child_key) in (_DATE_BOUND_URL_KEYS | _STALE_FARE_KEYS)
                    else walk(child, str(child_key))
                )
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [walk(child) for child in value]
        return value

    shifted = walk(shifted)
    if str(shifted.get("kind") or "").lower() == "quest":
        shifted["inbound_fare_missing"] = True
        shifted["outbound_fare_missing"] = True
    return shifted


def _anchor_city(iata: str) -> str:
    try:
        from yonder.countries import city_for_iata

        return city_for_iata(iata) or iata
    except Exception:
        return iata


def upcoming_anchor_legs(
    *, today: "datetime.date | None" = None, limit: int = 3,
    owner_sess: str | None = None,
) -> list[dict[str, Any]]:
    """Future-dated legs from saved trips, usable as planning anchors.

    Each anchor: {saved_id, title, kind, from_iata, to_iata, from_city,
    to_city, depart_date (ISO), label}. Sorted soonest-first, capped at
    ``limit``. Past-dated legs are never returned; users with no upcoming
    saved trips get []. Never raises.
    """
    from datetime import date as _date

    today = today or _date.today()
    try:
        saves = list_saved(limit=25, owner_sess=owner_sess)
    except Exception:
        return []

    anchors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(save: SavedItinerary, o: Any, d: Any, dep: Any) -> None:
        o = str(o or "").strip().upper()
        d = str(d or "").strip().upper()
        if len(o) != 3 or not o.isalpha() or len(d) != 3 or not d.isalpha() or o == d:
            return
        try:
            dep_d = _date.fromisoformat(str(dep)[:10])
        except (TypeError, ValueError):
            return
        if dep_d <= today:
            return  # past-dated saved legs are never anchors
        key = (o, d, dep_d.isoformat())
        if key in seen:
            return
        seen.add(key)
        from_city = _anchor_city(o)
        to_city = _anchor_city(d)
        anchors.append(
            {
                "saved_id": save.id,
                "title": save.title,
                "kind": save.kind,
                "from_iata": o,
                "to_iata": d,
                "from_city": from_city,
                "to_city": to_city,
                "depart_date": dep_d.isoformat(),
                "label": f"Connects to your saved {from_city} → {to_city} flight",
            }
        )

    for s in saves:
        try:
            it = s.itinerary or {}
            kind = (s.kind or "").lower()
            if kind == "quest":
                home = (s.origin or (s.trip_meta or {}).get("origin") or "").upper()
                entry = (it.get("entry_iata") or "").upper()
                exit_ = (it.get("exit_iata") or "").upper()
                _add(s, home, entry, it.get("depart_date"))
                _add(s, exit_, home, it.get("outbound_date"))
            else:
                for leg in it.get("legs") or []:
                    if isinstance(leg, dict):
                        _add(
                            s,
                            leg.get("from_iata") or leg.get("from"),
                            leg.get("to_iata") or leg.get("to"),
                            leg.get("depart_date"),
                        )
        except Exception:
            continue  # a bad save row never breaks anchor extraction

    anchors.sort(key=lambda a: a["depart_date"])
    return anchors[: max(0, int(limit))]


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


def _find_duplicate_id(
    itinerary: dict[str, Any],
    *,
    origin: str | None,
    dest: str | None,
    owner_sess: str | None = None,
) -> str | None:
    """Existing row id matching this itinerary's route signature, if any.

    Uses the same key as import dedup: kind + origin + dest + first-leg
    depart date + title. Prevents recycled results from piling up as
    duplicate rows when re-saved.

    Scoped to ``owner_sess`` so a stranger's matching save never triggers
    dedup for this user.
    """
    legs = itinerary.get("legs") or []
    depart = legs[0].get("depart_date") if legs and isinstance(legs[0], dict) else None
    key = (
        str(itinerary.get("kind") or "stopover"),
        str(origin or "").upper(),
        str(dest or "").upper(),
        str(depart or ""),
        str(itinerary.get("title") or "Saved trip"),
    )
    if not key[1] and not key[2]:
        return None
    with get_conn() as conn:
        if owner_sess:
            rows = conn.execute(
                "SELECT id, kind, origin, destination, title, itinerary_json "
                "FROM saved_itineraries WHERE owner_sess = %s",
                (owner_sess,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, kind, origin, destination, title, itinerary_json "
                "FROM saved_itineraries WHERE owner_sess IS NULL"
            ).fetchall()
    for r in rows:
        k = _route_key(
            {
                "kind": r["kind"],
                "origin": r["origin"],
                "destination": r["destination"],
                "title": r["title"],
                "itinerary_json": r["itinerary_json"],
            }
        )
        if k == key:
            return str(r["id"])
    return None


def save_itinerary(
    itinerary: dict[str, Any],
    *,
    trip_meta: dict[str, Any] | None = None,
    replace_id: str | None = None,
    owner_sess: str | None = None,
) -> SavedItinerary:
    """Persist an adventure itinerary snapshot. Prices are frozen until refresh.

    ``owner_sess`` scopes the row to a specific browser session so that each
    browser has its own private /saved list.  Pass the ``yv_sess`` cookie value
    from the request.  Omit (or pass ``None``) only for internal/background
    callers (e.g. quest recycling) where no browser session is available.
    """
    meta = dict(trip_meta or {})
    owner = (owner_sess or "").strip()[:64] or None
    origin, dest = _legs_origin_dest(itinerary)
    # Quest itineraries carry entry_iata/exit_iata instead of legs[].
    # Make origin/dest extraction explicit so the DB columns are always populated.
    if str(itinerary.get("kind") or "").lower() == "quest":
        if not origin:
            origin = (
                str(meta.get("origin") or "").strip().upper()
                or str(itinerary.get("origin") or "").strip().upper()
                or None
            )
        if not dest:
            dest = str(itinerary.get("entry_iata") or "").strip().upper() or None
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

    # Model-source label ("Grok (Server)" / "BYOM, <model>") — keep it on both
    # the meta and the frozen itinerary JSON so share pages can surface it.
    # Legacy rows without it simply read back with no model_source key.
    model_source = str(
        meta.get("model_source") or itinerary.get("model_source") or ""
    ).strip()
    if model_source:
        meta["model_source"] = model_source
        if itinerary.get("model_source") != model_source:
            itinerary = dict(itinerary)
            itinerary["model_source"] = model_source

    now = time.time()
    # Re-saving an identical trip (e.g. a recycled result) updates the existing
    # row instead of inserting a visible duplicate.  Scoped to this owner so a
    # stranger's matching row never hijacks or collapses into this user's save.
    dedup_id = None
    if not replace_id:
        dedup_id = _find_duplicate_id(
            itinerary, origin=origin, dest=dest, owner_sess=owner
        )
    sid = replace_id or dedup_id or str(uuid.uuid4())
    priced_at = now if total is not None else None

    # Keep prior saved_at / priced_at when updating after refresh
    existing = get(sid) if (replace_id or dedup_id) else None
    # Explicit re-save of a duplicate bumps saved_at; refresh replaces keep it
    saved_at = existing.saved_at if (existing and replace_id) else now
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
        owner,
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO saved_itineraries (
                id, saved_at, priced_at, title, kind, currency, total_price, display_price, stop_city, stop_iata, stay_days, origin, destination, adults, cabin, vibe, trip_prompt, theme_country, theme_primary, theme_accent, theme_gradient, theme_flag_img, theme_label, google_flights_url, kayak_url, ground_display, ground_compare_line, all_in_display, notes_json, itinerary_json, trip_meta_json, owner_sess
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
                saved_at = EXCLUDED.saved_at,
                priced_at = EXCLUDED.priced_at,
                title = EXCLUDED.title,
                kind = EXCLUDED.kind,
                currency = EXCLUDED.currency,
                total_price = EXCLUDED.total_price,
                display_price = EXCLUDED.display_price,
                stop_city = EXCLUDED.stop_city,
                stop_iata = EXCLUDED.stop_iata,
                stay_days = EXCLUDED.stay_days,
                origin = EXCLUDED.origin,
                destination = EXCLUDED.destination,
                adults = EXCLUDED.adults,
                cabin = EXCLUDED.cabin,
                vibe = EXCLUDED.vibe,
                trip_prompt = EXCLUDED.trip_prompt,
                theme_country = EXCLUDED.theme_country,
                theme_primary = EXCLUDED.theme_primary,
                theme_accent = EXCLUDED.theme_accent,
                theme_gradient = EXCLUDED.theme_gradient,
                theme_flag_img = EXCLUDED.theme_flag_img,
                theme_label = EXCLUDED.theme_label,
                google_flights_url = EXCLUDED.google_flights_url,
                kayak_url = EXCLUDED.kayak_url,
                ground_display = EXCLUDED.ground_display,
                ground_compare_line = EXCLUDED.ground_compare_line,
                all_in_display = EXCLUDED.all_in_display,
                notes_json = EXCLUDED.notes_json,
                itinerary_json = EXCLUDED.itinerary_json,
                trip_meta_json = EXCLUDED.trip_meta_json,
                owner_sess = EXCLUDED.owner_sess
            """,
            row,
        )
        conn.commit()
    # FIFO eviction: only for genuinely new rows (not refreshes or duplicate re-saves).
    # Quests are exempt — they form a public library that must grow beyond SAVE_LIMIT,
    # and they are not subject to the per-user private saved-trip cap.
    # The cap is per-owner so one busy browser can't evict another browser's trips.
    is_quest = str(itinerary.get("kind") or "").lower() == "quest"
    if existing is None and not is_quest:
        with get_conn() as conn:
            if owner:
                cnt_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM saved_itineraries"
                    " WHERE kind != 'quest' AND owner_sess = %s",
                    (owner,),
                ).fetchone()
                cnt = int(cnt_row["n"]) if cnt_row else 0
                if cnt > SAVE_LIMIT:
                    conn.execute(
                        "DELETE FROM saved_itineraries"
                        " WHERE kind != 'quest' AND owner_sess = %s AND id = ("
                        "  SELECT id FROM saved_itineraries"
                        "  WHERE kind != 'quest' AND owner_sess = %s"
                        "  ORDER BY saved_at ASC LIMIT 1"
                        ")",
                        (owner, owner),
                    )
                    conn.commit()
            else:
                cnt_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM saved_itineraries"
                    " WHERE kind != 'quest' AND owner_sess IS NULL"
                ).fetchone()
                cnt = int(cnt_row["n"]) if cnt_row else 0
                if cnt > SAVE_LIMIT:
                    conn.execute(
                        "DELETE FROM saved_itineraries"
                        " WHERE kind != 'quest' AND owner_sess IS NULL AND id = ("
                        "  SELECT id FROM saved_itineraries"
                        "  WHERE kind != 'quest' AND owner_sess IS NULL"
                        "  ORDER BY saved_at ASC LIMIT 1"
                        ")"
                    )
                    conn.commit()
    saved = get(sid)
    assert saved is not None
    return saved


def get(saved_id: str) -> SavedItinerary | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM saved_itineraries WHERE id = %s", (saved_id,)
        ).fetchone()
    return _row_to_saved(row) if row else None


def list_saved(*, limit: int = 50, owner_sess: str | None = None) -> list[SavedItinerary]:
    """Return saved trips for *owner_sess* only.

    Rows with a NULL ``owner_sess`` (legacy pre-migration rows) are hidden and
    NOT returned; a one-time log statement reports the orphan count so we can
    track them.
    """
    owner = (owner_sess or "").strip()[:64] or None
    with get_conn() as conn:
        if owner:
            rows = conn.execute(
                """
                SELECT * FROM saved_itineraries
                WHERE owner_sess = %s
                ORDER BY saved_at DESC
                LIMIT %s
                """,
                (owner, max(1, min(200, limit))),
            ).fetchall()
        else:
            # No session: return nothing.  Also log orphan count for visibility.
            try:
                orphan_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM saved_itineraries"
                    " WHERE owner_sess IS NULL AND kind != 'quest'"
                ).fetchone()
                orphan_n = int(orphan_row["n"]) if orphan_row else 0
                if orphan_n:
                    import logging as _logging
                    _logging.getLogger(__name__).info(
                        "saved: %d orphaned rows with owner_sess=NULL (pre-migration)",
                        orphan_n,
                    )
            except Exception:
                pass
            rows = []
    return [_row_to_saved(r) for r in rows]


def delete(saved_id: str, *, owner_sess: str | None = None) -> bool:
    """Delete a saved itinerary by id.

    When ``owner_sess`` is provided the DELETE is scoped to that owner so one
    browser cannot remove another browser's saved trips.
    """
    owner = (owner_sess or "").strip()[:64] or None
    with get_conn() as conn:
        if owner:
            cur = conn.execute(
                "DELETE FROM saved_itineraries WHERE id = %s AND owner_sess = %s",
                (saved_id, owner),
            )
        else:
            cur = conn.execute(
                "DELETE FROM saved_itineraries WHERE id = %s AND owner_sess IS NULL",
                (saved_id,),
            )
        conn.commit()
        return cur.rowcount > 0


def clear_all_saves(*, owner_sess: str | None = None) -> int:
    """Delete all saved itineraries for *owner_sess*.  Returns the number deleted.

    When ``owner_sess`` is provided only that browser's rows are removed.
    """
    owner = (owner_sess or "").strip()[:64] or None
    with get_conn() as conn:
        if owner:
            cur = conn.execute(
                "DELETE FROM saved_itineraries WHERE owner_sess = %s AND kind != 'quest'",
                (owner,),
            )
        else:
            cur = conn.execute(
                "DELETE FROM saved_itineraries WHERE owner_sess IS NULL AND kind != 'quest'"
            )
        conn.commit()
        return cur.rowcount


_EXPORT_COLUMNS = (
    "id", "saved_at", "priced_at", "title", "kind", "currency", "total_price",
    "display_price", "stop_city", "stop_iata", "stay_days", "origin", "destination",
    "adults", "cabin", "vibe", "trip_prompt", "theme_country", "theme_primary",
    "theme_accent", "theme_gradient", "theme_flag_img", "theme_label",
    "google_flights_url", "kayak_url", "ground_display", "ground_compare_line",
    "all_in_display", "notes_json", "itinerary_json", "trip_meta_json", "owner_sess",
)


def export_all(owner_sess: str | None = None) -> list[dict[str, Any]]:
    """Saved trips as raw row dicts — for backup export.

    When *owner_sess* is given, only that browser session's trips are
    exported (per-browser backup); None keeps the legacy full export.
    """
    with get_conn() as conn:
        if owner_sess:
            rows = conn.execute(
                "SELECT * FROM saved_itineraries WHERE owner_sess = %s ORDER BY saved_at",
                (owner_sess,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM saved_itineraries ORDER BY saved_at"
            ).fetchall()
    return [{k: r[k] for k in _EXPORT_COLUMNS} for r in rows]


def _route_key(row: dict[str, Any]) -> tuple | None:
    """Dedupe key beyond id: kind + route + first-leg depart date + title."""
    try:
        it = json.loads(row.get("itinerary_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        it = {}
    legs = it.get("legs") or [] if isinstance(it, dict) else []
    depart = legs[0].get("depart_date") if legs and isinstance(legs[0], dict) else None
    origin = str(row.get("origin") or "").upper()
    dest = str(row.get("destination") or "").upper()
    if not origin and not dest:
        return None
    return (
        str(row.get("kind") or ""),
        origin,
        dest,
        str(depart or ""),
        str(row.get("title") or ""),
    )


def import_rows(
    items: list[dict[str, Any]], owner_sess: str | None = None
) -> tuple[int, int]:
    """Restore exported saved trips. Returns (imported, skipped).

    Dedupes by id first, then by kind+route+depart-date+title so re-imports
    and cross-device merges never duplicate a trip.  When *owner_sess* is
    given, imported trips are stamped as that browser session's private
    trips (regardless of the owner recorded in the backup file).
    """
    imported = skipped = 0
    with get_conn() as conn:
        existing_ids = {
            r["id"] for r in conn.execute("SELECT id FROM saved_itineraries")
        }
        existing_keys = set()
        for r in conn.execute("SELECT * FROM saved_itineraries").fetchall():
            k = _route_key({c: r[c] for c in _EXPORT_COLUMNS})
            if k:
                existing_keys.add(k)
        placeholders = ",".join(["%s"] * len(_EXPORT_COLUMNS))
        for raw in items:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            sid = str(raw.get("id") or "").strip()
            it_json = raw.get("itinerary_json")
            if not sid or not it_json or raw.get("saved_at") is None:
                skipped += 1
                continue
            key = _route_key(raw)
            if sid in existing_ids or (key and key in existing_keys):
                skipped += 1
                continue
            try:
                saved_at = float(raw["saved_at"])
                priced_at = (
                    float(raw["priced_at"]) if raw.get("priced_at") is not None else None
                )
            except (TypeError, ValueError):
                skipped += 1
                continue
            row = dict(raw)
            row["saved_at"] = saved_at
            row["priced_at"] = priced_at
            row["title"] = str(raw.get("title") or "Saved trip")
            row["kind"] = str(raw.get("kind") or "stopover")
            row["currency"] = str(raw.get("currency") or "USD").upper()
            if owner_sess:
                row["owner_sess"] = owner_sess
            conn.execute(
                f"INSERT INTO saved_itineraries ({', '.join(_EXPORT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                tuple(row.get(c) for c in _EXPORT_COLUMNS),
            )
            existing_ids.add(sid)
            if key:
                existing_keys.add(key)
            imported += 1
        conn.commit()
    return imported, skipped


def count_saved(*, owner_sess: str | None = None) -> int:
    """Total saved non-quest rows, optionally scoped to a browser session."""
    owner = (owner_sess or "").strip()[:64] or None
    with get_conn() as conn:
        if owner:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM saved_itineraries"
                " WHERE kind != 'quest' AND owner_sess = %s",
                (owner,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM saved_itineraries WHERE kind != 'quest'"
            ).fetchone()
    return int(row["n"]) if row else 0


def list_quests(
    *,
    origin: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> list[SavedItinerary]:
    """Return quest itineraries ordered newest → oldest, with optional origin filter."""
    limit = max(1, min(50, limit))
    offset = max(0, offset)
    origin_n = (origin or "").strip().upper() or None
    with get_conn() as conn:
        if origin_n:
            rows = conn.execute(
                """
                SELECT * FROM saved_itineraries
                WHERE kind = 'quest' AND UPPER(origin) = %s
                ORDER BY saved_at DESC
                LIMIT %s OFFSET %s
                """,
                (origin_n, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM saved_itineraries
                WHERE kind = 'quest'
                ORDER BY saved_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
    return [_row_to_saved(r) for r in rows]


def count_quests(*, origin: str | None = None) -> int:
    """Count saved quest itineraries, optionally filtered by origin airport."""
    origin_n = (origin or "").strip().upper() or None
    with get_conn() as conn:
        if origin_n:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM saved_itineraries WHERE kind = 'quest' AND UPPER(origin) = %s",
                (origin_n,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM saved_itineraries WHERE kind = 'quest'"
            ).fetchone()
    return int(row["n"]) if row else 0


def bookmark_quest(saved_id: str, *, owner_sess: str | None) -> bool:
    """Bookmark an existing global quest row for a browser session.

    Idempotent — repeat clicks are absorbed by the unique index.  Returns
    True when the id refers to an existing quest row (already-bookmarked
    counts as success), False otherwise.  Bookmarks are exempt from
    SAVE_LIMIT: they reference shared library rows, they do not create
    per-user itinerary rows.
    """
    owner = (owner_sess or "").strip()[:64] or None
    if not owner or not saved_id:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM saved_itineraries WHERE id = %s AND kind = 'quest'",
            (saved_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "INSERT INTO quest_bookmarks (owner_sess, saved_id, created_at)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (owner_sess, saved_id) DO NOTHING",
            (owner, saved_id, time.time()),
        )
        conn.commit()
    return True


def unbookmark_quest(saved_id: str, *, owner_sess: str | None) -> bool:
    """Remove a session's bookmark on a quest. Never deletes the quest row."""
    owner = (owner_sess or "").strip()[:64] or None
    if not owner or not saved_id:
        return False
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM quest_bookmarks WHERE owner_sess = %s AND saved_id = %s",
            (owner, saved_id),
        )
        conn.commit()
        return cur.rowcount > 0


def bookmarked_quest_ids(*, owner_sess: str | None) -> set[str]:
    """Ids of quest rows bookmarked by this browser session."""
    owner = (owner_sess or "").strip()[:64] or None
    if not owner:
        return set()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT saved_id FROM quest_bookmarks WHERE owner_sess = %s",
            (owner,),
        ).fetchall()
    return {str(r["saved_id"]) for r in rows}


def list_bookmarked_quests(
    *, owner_sess: str | None, limit: int = 50
) -> list[SavedItinerary]:
    """Quest rows bookmarked by this session, newest bookmark first.

    ``saved_at`` on the returned objects is the bookmark's created_at so the
    /saved page can interleave bookmarks with private saves by save time.
    """
    owner = (owner_sess or "").strip()[:64] or None
    if not owner:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.*, qb.created_at AS bookmark_at,
                   qb.itinerary_override_json, qb.override_updated_at
            FROM quest_bookmarks qb
            JOIN saved_itineraries s ON s.id = qb.saved_id
            WHERE qb.owner_sess = %s AND s.kind = 'quest'
            ORDER BY qb.created_at DESC
            LIMIT %s
            """,
            (owner, max(1, min(200, limit))),
        ).fetchall()
    out: list[SavedItinerary] = []
    for r in rows:
        s = _row_to_saved(r)
        try:
            override_raw = r["itinerary_override_json"]
        except (KeyError, TypeError):
            override_raw = None
        if override_raw:
            try:
                override = json.loads(override_raw)
                if isinstance(override, dict):
                    s.itinerary = override
            except (TypeError, json.JSONDecodeError):
                pass
        try:
            s.saved_at = float(r["bookmark_at"])
        except (TypeError, ValueError, KeyError):
            pass
        out.append(s)
    return out


def update_quest_bookmark_override(
    saved_id: str, itinerary: dict[str, Any], *, owner_sess: str | None
) -> bool:
    """Persist one browser's personal Quest itinerary without touching the Quest."""
    owner = (owner_sess or "").strip()[:64] or None
    if not owner or not saved_id or not isinstance(itinerary, dict):
        return False
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE quest_bookmarks
            SET itinerary_override_json = %s, override_updated_at = %s
            WHERE owner_sess = %s AND saved_id = %s
            """,
            (json.dumps(itinerary, separators=(",", ":")), time.time(), owner, saved_id),
        )
        conn.commit()
        return cur.rowcount > 0


_QUEST_ID_EXCLUDED_FIELDS = {
    "anchor_label",
    "currency",
    "display_total",
    "gap_label",
    "inbound_fare_missing",
    "inbound_leg",
    "model_source",
    "outbound_fare_missing",
    "outbound_leg",
    "theme_accent",
    "theme_country",
    "theme_flag_img",
    "theme_gradient",
    "theme_label",
    "theme_primary",
    "total_price",
    "vibe",
}


def quest_canonical_key(
    itinerary: dict[str, Any], *, origin: str | None
) -> str:
    """Stable identity for a Quest's route, dates, and creative content.

    Fare snapshots, generated booking links, display currency, and theme
    decoration are intentionally excluded so repricing the same Quest does not
    create another global row. Narrative/highlight content remains in the key,
    keeping similar same-route Quests distinct.
    """
    idea = {
        str(k): v
        for k, v in (itinerary or {}).items()
        if str(k) not in _QUEST_ID_EXCLUDED_FIELDS
    }
    idea["kind"] = "quest"
    payload = {
        "origin": str(origin or "").strip().upper(),
        "idea": idea,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonical_quest_id(
    itinerary: dict[str, Any], *, origin: str | None
) -> str:
    """Deterministic primary key used for newly canonicalized Quest rows."""
    return f"quest-{quest_canonical_key(itinerary, origin=origin)[:24]}"


def find_global_quest_id(
    itinerary: dict[str, Any], *, origin: str | None, dest: str | None
) -> str | None:
    """Existing global Quest row with the same complete canonical content.

    This is the compatibility path for old share links that predate saved_id.
    Route/title-only matching is deliberately unsafe: two generated Quests can
    share those fields while carrying different narratives and stops.
    """
    o = str(origin or "").strip().upper()
    d = str(dest or itinerary.get("entry_iata") or "").strip().upper()
    if not d and not itinerary:
        return None
    wanted_key = quest_canonical_key(itinerary, origin=o)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, origin, destination, title, itinerary_json"
            " FROM saved_itineraries"
            " WHERE kind = 'quest' AND owner_sess IS NULL"
        ).fetchall()
    matches: list[str] = []
    for r in rows:
        try:
            it = json.loads(r["itinerary_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            it = {}
        row_origin = str(r["origin"] or "").strip().upper()
        row_dest = str(r["destination"] or "").strip().upper()
        if o and row_origin != o:
            continue
        if d and row_dest != d:
            continue
        if quest_canonical_key(it or {}, origin=row_origin) == wanted_key:
            matches.append(str(r["id"]))
    if not matches:
        return None
    preferred = canonical_quest_id(itinerary, origin=o)
    return preferred if preferred in matches else sorted(matches)[0]


def ensure_global_quest(
    itinerary: dict[str, Any],
    *,
    trip_meta: dict[str, Any] | None = None,
    origin: str | None = None,
) -> SavedItinerary:
    """Return the one canonical global row for this exact Quest.

    Legacy UUID rows are reused when their full content matches. Otherwise a
    deterministic primary key is inserted/upserted, so concurrent creators of
    the same Quest converge on one row without a check-then-insert race.
    """
    idea = dict(itinerary or {})
    idea["kind"] = "quest"
    meta = dict(trip_meta or {})
    home = (
        str(origin or "").strip().upper()
        or str(meta.get("origin") or "").strip().upper()
        or str(idea.get("origin") or "").strip().upper()
    )
    meta["origin"] = home

    existing_id = find_global_quest_id(
        idea,
        origin=home or None,
        dest=str(idea.get("entry_iata") or "").strip().upper() or None,
    )
    if existing_id:
        existing = get(existing_id)
        if existing is not None:
            return existing

    return save_itinerary(
        idea,
        trip_meta=meta,
        replace_id=canonical_quest_id(idea, origin=home),
        owner_sess=None,
    )


def top_quest_routes(*, limit: int = 12, origin: str | None = None) -> list[dict]:
    """Return top saved quest routes grouped by (origin, entry city, exit city).

    When *origin* is given (a normalised IATA code) only routes from that
    departure airport are returned.  Results are ordered by save count
    descending, then by recency.

    Each row is a dict with keys: entry, exit, origin, vibe, accent.
    """
    limit = max(1, min(50, limit))
    origin_n = (origin or "").strip().upper() or None
    params: list = [limit]
    origin_clause = ""
    if origin_n:
        origin_clause = "AND UPPER(COALESCE(s.origin, '')) = %s"
        params = [origin_n, limit]

    # Rank = quest rows + personal bookmarks, so ★ Save feeds popularity.
    sql = f"""
        SELECT
            UPPER(COALESCE(NULLIF(TRIM(s.origin), ''), '')) AS origin,
            COALESCE(
                NULLIF(TRIM(s.itinerary_json::json->>'entry_city'), ''),
                NULLIF(TRIM(s.itinerary_json::json->>'entry_iata'), '')
            ) AS entry,
            COALESCE(
                NULLIF(TRIM(s.itinerary_json::json->>'exit_city'), ''),
                NULLIF(TRIM(s.itinerary_json::json->>'exit_iata'), '')
            ) AS exit_city,
            MAX(s.vibe) AS vibe,
            MAX(s.theme_accent) AS accent,
            MAX(s.theme_label) AS vibe_label,
            COUNT(DISTINCT s.id) + COUNT(qb.saved_id) AS save_count
        FROM saved_itineraries s
        LEFT JOIN quest_bookmarks qb ON qb.saved_id = s.id
        WHERE s.kind = 'quest'
          {origin_clause}
          AND COALESCE(
                NULLIF(TRIM(s.itinerary_json::json->>'entry_city'), ''),
                NULLIF(TRIM(s.itinerary_json::json->>'entry_iata'), '')
              ) IS NOT NULL
          AND COALESCE(
                NULLIF(TRIM(s.itinerary_json::json->>'exit_city'), ''),
                NULLIF(TRIM(s.itinerary_json::json->>'exit_iata'), '')
              ) IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY COUNT(DISTINCT s.id) + COUNT(qb.saved_id) DESC, MAX(s.saved_at) DESC
        LIMIT %s
    """
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    def _vibe_emoji(vibe: str | None) -> str:
        """Vibe emoji for the board's ✦ column — '' when unresolvable."""
        try:
            from yonder.vibe_theme import resolve_vibe

            return resolve_vibe(vibe).get("emoji") or ""
        except Exception:
            return ""

    result = []
    for r in rows:
        result.append(
            {
                "entry": str(r["entry"] or ""),
                "exit": str(r["exit_city"] or ""),
                "origin": str(r["origin"] or ""),
                "vibe": str(r["vibe_label"] or r["vibe"] or ""),
                "vibe_emoji": _vibe_emoji(r["vibe"]),
                "accent": str(r["accent"] or "#f5a800"),
            }
        )
    return result


def update_from_itinerary(
    saved_id: str,
    itinerary: dict[str, Any],
    *,
    trip_meta: dict[str, Any] | None = None,
    owner_sess: str | None = None,
) -> SavedItinerary | None:
    """Update an existing saved row with a repriced itinerary.

    ``owner_sess`` is forwarded to ``save_itinerary`` so the UPSERT preserves
    (or sets) the row's owner.  Always pass the request session here so a
    refresh can never clobber another browser's ownership or orphan the row
    under NULL.
    """
    existing = get(saved_id)
    if not existing:
        return None
    meta = {**existing.trip_meta, **(trip_meta or {})}
    # Preserve the row's original owner if the caller did not provide one.
    effective_owner = (owner_sess or "").strip()[:64] or None
    return save_itinerary(
        itinerary, trip_meta=meta, replace_id=saved_id, owner_sess=effective_owner
    )


def escape_offer_to_itinerary(
    *,
    query: dict[str, Any],
    offer: dict[str, Any],
    ask: str = "",
    vibe: str | None = None,
) -> dict[str, Any]:
    """Normalize an Escape fare into the same itinerary shape Detour saves use."""
    origin = str(query.get("origin") or "").upper()
    dest = str(query.get("destination") or "").upper()
    currency = (
        str(offer.get("currency") or query.get("currency") or "USD")
    ).upper()
    price = offer.get("price")
    title = f"{origin} → {dest}" if origin and dest else "Escape flight"
    leg = {
        "from_iata": origin,
        "to_iata": dest,
        "depart_date": query.get("depart_date"),
        "offer": offer,
        "google_flights_url": offer.get("google_flights_url"),
    }
    return {
        "kind": "escape",
        "title": title,
        "currency": currency,
        "total_price": price,
        "display_price": offer.get("display_price"),
        "display_price_base": offer.get("display_price_base"),
        "price_glyph": offer.get("price_glyph"),
        "price_tone": offer.get("price_tone"),
        "legs": [leg],
        "google_flights_url": offer.get("google_flights_url"),
        "kayak_url": offer.get("kayak_url"),
        "why": ask[:280] if ask else "",
        "vibe": vibe,
        "notes": [
            "Escape straight-shot fare signal",
            f"Provider: {offer.get('provider') or '—'}",
        ],
    }


def similar_saves(
    prompt: str,
    *,
    origin: str | None = None,
    vibe: str | None = None,
    limit: int = 8,
    owner_sess: str | None = None,
) -> list[SavedItinerary]:
    """v1 keyword retrieval — only over explicit user Saves (never raw searches)."""
    tokens = {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (prompt or "").lower())
        if t not in _STOP_WORDS
    }
    origin_u = (origin or "").strip().upper() or None
    vibe_l = (vibe or "").strip().lower() or None
    items = list_saved(limit=100, owner_sess=owner_sess)
    scored: list[tuple[float, SavedItinerary]] = []
    for s in items:
        score = 0.0
        text = (s.trip_prompt or s.title or "").lower()
        if not text and not s.origin:
            continue
        words = set(re.findall(r"[a-z0-9]{3,}", text))
        if tokens:
            overlap = len(tokens & words) / max(1, len(tokens))
            score += overlap * 4.0
        if origin_u and (s.origin or "").upper() == origin_u:
            score += 2.0
        if vibe_l and (s.vibe or "").lower() == vibe_l:
            score += 1.5
        # Prefer saved destinations that look intentional
        if s.kind in ("escape", "getaway", "stopover", "detour"):
            score += 0.25
        if score <= 0:
            continue
        scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], -x[1].saved_at))
    return [s for _, s in scored[: max(1, min(20, limit))]]


def seed_cities_from_saves(saves: list[SavedItinerary]) -> list[dict[str, str]]:
    """Unique stop/destination hints for invent seeding (legacy — prefer exclude)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for s in saves:
        code = (s.stop_iata or s.destination or "").upper()
        if not code or len(code) != 3 or code in seen:
            continue
        seen.add(code)
        out.append(
            {
                "iata": code,
                "city": s.stop_city or code,
                "kind": s.kind or "",
                "why": f"from prior Save: {(s.title or '')[:80]}",
            }
        )
    return out


def saved_destination_iatas(*, limit: int = 200, owner_sess: str | None = None) -> set[str]:
    """IATA codes the user has already ★ Saved — never re-offer on invent/board.

    Prefer stop_iata (getaway X / stopover city). Only use destination when it
    is not the same as origin (so getaway home=YVR is not banned as a hub).
    """
    out: set[str] = set()
    for s in list_saved(limit=limit, owner_sess=owner_sess):
        stop = (s.stop_iata or "").strip().upper()
        dest = (s.destination or "").strip().upper()
        origin = (s.origin or "").strip().upper()
        if len(stop) == 3 and stop.isalpha():
            out.add(stop)
        if len(dest) == 3 and dest.isalpha() and dest != origin:
            out.add(dest)
    return out


def ranking_from_saves(
    *,
    vibe: str | None = None,
    origin: str | None = None,
    visited: list[str] | None = None,
    avoid: list[str] | None = None,
    demo: bool = False,
    owner_sess: str | None = None,
) -> dict[str, Any]:
    """★ Save metrics for ranking dataset-completion chips (not chip content).

    Chips themselves fill prompt slots (shape, map, timing, vibe constraints).
    Saves tell us which completed patterns + dest seeds work for this vibe /
    origin / passport context — used to re-order and lightly seed invent.
    """
    from yonder.countries import country_for_iata

    vibe_l = (vibe or "").strip().lower() or None
    origin_u = (origin or "").strip().upper() or None
    visited_set = {c.upper() for c in (visited or []) if c}
    avoid_set = {c.upper() for c in (avoid or []) if c}
    items = list_saved(limit=100, owner_sess=owner_sess)

    # Pattern keys align with client dataset-completion chips
    pattern_w: dict[str, float] = {
        "getaway_new": 0.0,
        "stopover": 0.0,
        "escape_city": 0.0,
        "timed": 0.0,
        "map_aware": 0.0,
        "budget_col": 0.0,
    }
    dest_scores: dict[str, float] = {}
    dest_city: dict[str, str] = {}
    n_match = 0

    for s in items:
        kind = (s.kind or "").lower()
        text = f"{s.trip_prompt or ''} {s.title or ''}".lower()
        vibe_hit = bool(
            vibe_l
            and (
                (s.vibe or "").lower() == vibe_l
                or vibe_l in text
            )
        )
        origin_hit = bool(origin_u and (s.origin or "").upper() == origin_u)
        if not vibe_hit and not origin_hit and vibe_l:
            continue
        n_match += 1
        w = 1.0
        if vibe_hit:
            w += 2.0
        if origin_hit:
            w += 1.0
        # Recency mild boost
        age_days = max(0.0, (time.time() - float(s.saved_at or 0)) / 86400.0)
        w *= max(0.35, 1.0 - min(0.65, age_days / 180.0))

        if kind in ("getaway", "round_trip", "escape") or "getaway" in text:
            pattern_w["getaway_new"] += w
        if kind in ("stopover", "detour") or "stopover" in text or "via" in text:
            pattern_w["stopover"] += w
        if kind == "escape" or "→" in (s.title or "") or " to " in text:
            pattern_w["escape_city"] += w * 0.8
        if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|20\d{2})\b", text):
            pattern_w["timed"] += w * 0.6
        if "not been" in text or "haven't been" in text or "somewhere new" in text:
            pattern_w["map_aware"] += w
        if any(x in text for x in ("cheap", "budget", "col", "cost of living", "affordable")):
            pattern_w["budget_col"] += w

        dest = (s.stop_iata or s.destination or "").upper()
        if len(dest) == 3 and dest.isalpha():
            cc = country_for_iata(dest) or ""
            if cc and cc in avoid_set:
                continue
            # Soft seed: destinations that worked (even if visited — return trips)
            dest_scores[dest] = dest_scores.get(dest, 0.0) + w
            if s.stop_city:
                dest_city[dest] = s.stop_city

    # Vibe-learning blend: accumulated usage signals (searches, clicks, reviews)
    # nudge destination seeds — weighted lower than explicit ★ Saves.
    signal_dest_count = 0
    if vibe_l:
        try:
            from yonder.vibe_signals import scores_for_vibe

            sig_scores = scores_for_vibe(vibe_l, demo=demo)
            for code, sc in sig_scores.items():
                cc = country_for_iata(code) or ""
                if cc and cc in avoid_set:
                    continue
                dest_scores[code] = dest_scores.get(code, 0.0) + 0.5 * float(sc)
                signal_dest_count += 1
        except Exception:
            pass

    # Normalize pattern weights to ~0..3 for client sort keys
    mx = max(pattern_w.values()) if pattern_w else 0.0
    if mx > 0:
        pattern_w = {k: round(3.0 * v / mx, 3) for k, v in pattern_w.items()}

    seeds = sorted(dest_scores.items(), key=lambda x: -x[1])[:6]
    seed_list = [
        {
            "iata": code,
            "city": dest_city.get(code) or code,
            "weight": round(sc, 3),
            "visited": bool(
                (country_for_iata(code) or "") in visited_set
            ),
        }
        for code, sc in seeds
    ]

    return {
        "vibe": vibe_l,
        "origin": origin_u,
        "save_count": n_match,
        "signal_dest_count": signal_dest_count,
        "signals_bypassed": bool(demo),
        "pattern_weights": pattern_w,
        "dest_seeds": seed_list,
    }


def suggest_chips_from_saves(
    *,
    vibe: str | None = None,
    origin: str | None = None,
    visited: list[str] | None = None,
    avoid: list[str] | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Legacy no-op — chips are dataset-completion on the client; use ranking_from_saves."""
    _ = (vibe, origin, visited, avoid, limit)
    return []


_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "want",
        "need",
        "somewhere",
        "anywhere",
        "flight",
        "flights",
        "trip",
        "travel",
        "cheap",
        "please",
        "days",
        "week",
        "into",
        "over",
        "just",
        "like",
        "have",
        "been",
    }
)
