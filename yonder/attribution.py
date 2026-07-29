"""Funnel attribution for product + affiliate outbound clicks.

Chip → explore → ★ Save → book link is the conversion path.
Preference learning stays Save-only; this module tracks *marketing* signals
(which chip/source led to a search, which outbound link was opened).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from yonder.config import ROOT

DB_PATH = ROOT / "attribution.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS funnel_events (
            id TEXT PRIMARY KEY,
            ts REAL NOT NULL,
            event TEXT NOT NULL,
            click_id TEXT,
            chip_id TEXT,
            chip_source TEXT,
            vibe TEXT,
            origin TEXT,
            search_id TEXT,
            saved_id TEXT,
            dest TEXT,
            url TEXT,
            meta_json TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_funnel_ts ON funnel_events(ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_funnel_click ON funnel_events(click_id)"
    )
    conn.commit()
    return conn


def new_click_id() -> str:
    return "c" + uuid.uuid4().hex[:16]


def new_chip_id(source: str = "template") -> str:
    return f"{source}:{uuid.uuid4().hex[:10]}"


def log_event(
    event: str,
    *,
    click_id: str | None = None,
    chip_id: str | None = None,
    chip_source: str | None = None,
    vibe: str | None = None,
    origin: str | None = None,
    search_id: str | None = None,
    saved_id: str | None = None,
    dest: str | None = None,
    url: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Append a funnel event. Returns event id."""
    import json

    eid = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO funnel_events (
                id, ts, event, click_id, chip_id, chip_source, vibe, origin,
                search_id, saved_id, dest, url, meta_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                eid,
                time.time(),
                (event or "unknown")[:64],
                (click_id or "")[:64] or None,
                (chip_id or "")[:80] or None,
                (chip_source or "")[:32] or None,
                (vibe or "")[:40] or None,
                (origin or "")[:8] or None,
                (search_id or "")[:80] or None,
                (saved_id or "")[:64] or None,
                (dest or "")[:8] or None,
                (url or "")[:2000] or None,
                json.dumps(meta or {}, ensure_ascii=False) if meta else None,
            ),
        )
        conn.commit()
    return eid


def stamp_outbound_url(
    url: str | None,
    *,
    click_id: str | None = None,
    chip_id: str | None = None,
    chip_source: str | None = None,
    affiliate_tag: str | None = None,
    campaign: str = "yonder",
) -> str | None:
    """Attach UTM (+ optional affiliate tag) for partner/affiliate attribution."""
    if not url or not str(url).startswith("http"):
        return url
    try:
        parts = urlparse(url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q.setdefault("utm_source", "yonder")
        q.setdefault("utm_medium", (chip_source or "app")[:32] or "app")
        q.setdefault("utm_campaign", (campaign or "yonder")[:64])
        if click_id:
            q["utm_content"] = click_id[:64]
        if chip_id:
            q["utm_term"] = chip_id[:64]
        # Partner-specific tags (Kayak, etc.) — product config later
        tag = (affiliate_tag or "").strip()
        if tag and "kayak." in (parts.netloc or "").lower():
            q.setdefault("a", tag)
        new_query = urlencode(q)
        return urlunparse(
            (parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment)
        )
    except Exception:
        return url
