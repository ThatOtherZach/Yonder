"""Shareable trip pages for QR codes — human-readable paths from trip data."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from yonder.config import ROOT

DB_PATH = ROOT / "shared_trips.db"
DEFAULT_TTL_SEC = 90 * 24 * 3600  # 90 days


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_trips (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            expires_at REAL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            slug TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    # Older DBs may lack slug — add if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shared_trips)").fetchall()}
    if "slug" not in cols:
        try:
            conn.execute("ALTER TABLE shared_trips ADD COLUMN slug TEXT")
        except Exception:
            pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_created ON shared_trips(created_at DESC)"
    )
    conn.commit()
    return conn


def _slug_token(s: str, *, max_len: int = 48) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip()).strip("-")
    return (t or "trip")[:max_len]


def route_slug_escape(query: dict[str, Any], offer: dict[str, Any] | None = None) -> str:
    """e.g. YVR-NRT-2026-08-20 or YVR-NRT-2026-08-20-rt-2026-09-03"""
    o = (query.get("origin") or "XXX").upper()
    d = (query.get("destination") or "XXX").upper()
    dep = str(query.get("depart_date") or "")[:10]
    parts = [o, d]
    if dep:
        parts.append(dep)
    ret = query.get("return_date")
    if ret:
        parts.append("rt")
        parts.append(str(ret)[:10])
    if offer:
        price = offer.get("price")
        cur = (offer.get("currency") or "").upper()
        if price is not None:
            try:
                parts.append(f"{cur}{int(round(float(price)))}")
            except (TypeError, ValueError):
                pass
        airlines = offer.get("airlines") or []
        if airlines:
            parts.append(str(airlines[0]).upper()[:3])
    return _slug_token("-".join(parts), max_len=80)


def route_slug_detour(itinerary: dict[str, Any]) -> str:
    """e.g. YVR-KEF-YVR-4d-2026-09-11 or YVR-IST-LHR-3d"""
    legs = itinerary.get("legs") or []
    codes: list[str] = []
    if legs:
        codes.append(str(legs[0].get("from_iata") or "").upper())
        stop = itinerary.get("stop_iata")
        if stop:
            codes.append(str(stop).upper())
        codes.append(str((legs[-1] if legs else {}).get("to_iata") or "").upper())
        # drop empties / dup consecutive
        cleaned: list[str] = []
        for c in codes:
            if c and (not cleaned or cleaned[-1] != c):
                cleaned.append(c)
        codes = cleaned
    if not codes:
        stop = (itinerary.get("stop_iata") or itinerary.get("stop_city") or "TRIP")
        codes = [_slug_token(str(stop), max_len=12).upper()]
    parts = ["-".join(codes)]
    stay = itinerary.get("stay_days")
    if stay:
        parts.append(f"{int(stay)}d")
    if legs and legs[0].get("depart_date"):
        parts.append(str(legs[0]["depart_date"])[:10])
    price = itinerary.get("total_price")
    cur = (itinerary.get("currency") or "").upper()
    if price is not None:
        try:
            parts.append(f"{cur}{int(round(float(price)))}")
        except (TypeError, ValueError):
            pass
    kind = (itinerary.get("kind") or "").lower()
    if kind in ("getaway", "stopover", "detour"):
        parts.append(kind)
    return _slug_token("-".join(parts), max_len=90)


@dataclass
class SharedTrip:
    id: str
    created_at: float
    expires_at: float | None
    kind: str  # escape | detour
    title: str
    payload: dict[str, Any]
    slug: str = ""

    @property
    def path(self) -> str:
        """Human-readable share path with trip data in the URL."""
        sl = self.slug or "trip"
        return f"/t/{self.kind}/{sl}/{self.id}"


def _stable_id(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"k": kind, "p": payload},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def create_share(
    *,
    kind: str,
    title: str,
    payload: dict[str, Any],
    slug: str | None = None,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> SharedTrip:
    """Create or refresh a shareable trip (stable id for identical payloads)."""
    kind_n = (kind or "trip").strip().lower()[:32]
    title_n = (title or "Trip")[:200]
    payload = payload if isinstance(payload, dict) else {}
    if not slug:
        if kind_n == "escape":
            slug = route_slug_escape(payload.get("query") or {}, payload.get("offer") or {})
        elif kind_n == "detour":
            slug = route_slug_detour(payload.get("itinerary") or {})
        else:
            slug = _slug_token(title_n)
    slug_n = _slug_token(slug, max_len=90)
    sid = _stable_id(kind_n, payload)
    now = time.time()
    expires = now + max(3600, int(ttl_sec)) if ttl_sec else None
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO shared_trips (id, created_at, expires_at, kind, title, slug, payload_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                expires_at = excluded.expires_at,
                kind = excluded.kind,
                title = excluded.title,
                slug = excluded.slug,
                payload_json = excluded.payload_json
            """,
            (sid, now, expires, kind_n, title_n, slug_n, blob),
        )
        conn.commit()
    return SharedTrip(
        id=sid,
        created_at=now,
        expires_at=expires,
        kind=kind_n,
        title=title_n,
        payload=payload,
        slug=slug_n,
    )


def get_share(share_id: str) -> SharedTrip | None:
    sid = (share_id or "").strip()
    if not sid or len(sid) > 64:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM shared_trips WHERE id = ?", (sid,)
        ).fetchone()
    if not row:
        return None
    exp = row["expires_at"]
    if exp is not None and float(exp) < time.time():
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    slug = ""
    try:
        slug = row["slug"] or ""
    except (KeyError, IndexError):
        slug = ""
    return SharedTrip(
        id=row["id"],
        created_at=float(row["created_at"]),
        expires_at=float(exp) if exp is not None else None,
        kind=row["kind"] or "trip",
        title=row["title"] or "Trip",
        payload=payload,
        slug=slug or "",
    )


def qr_png_data_uri(url: str, *, scale: int = 6, border: int = 2) -> str:
    """Scannable PNG QR as a data URI (crisper than scaled stroke SVG)."""
    import base64

    try:
        import segno

        qr = segno.make(url, error="m")
        buff = io.BytesIO()
        # Fixed pixel modules + quiet zone — scanners need this, not CSS-squashed SVG strokes
        qr.save(
            buff,
            kind="png",
            scale=max(4, min(10, int(scale))),
            border=max(2, min(6, int(border))),
            dark="#141c28",
            light="#ffffff",
        )
        b64 = base64.b64encode(buff.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


def qr_svg_for_url(url: str, *, scale: int = 3) -> str:
    """Legacy helper — prefer qr_png_data_uri for scannable codes."""
    # Keep a simple square placeholder if PNG fails (should not happen with segno)
    uri = qr_png_data_uri(url, scale=max(4, scale + 2), border=2)
    if uri:
        # Embed as image so callers that expect markup still work
        return (
            f'<img class="bp-qr-img" src="{uri}" alt="QR code" width="120" height="120" '
            f'style="width:100%;height:100%;object-fit:contain;image-rendering:pixelated;'
            f'image-rendering:crisp-edges;display:block" />'
        )
    safe = (
        (url or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">'
        f'<rect width="96" height="96" rx="8" fill="#fff" stroke="#d9d0c2"/>'
        f'<text x="48" y="52" text-anchor="middle" font-size="10" fill="#141c28">Open link</text>'
        f"</svg><!-- {safe[:80]} -->"
    )


def dump_obj(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: dump_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [dump_obj(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)
