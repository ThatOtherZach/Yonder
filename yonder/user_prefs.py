"""Per-user preferences stored in a local SQLite database.

Keeps user-specific data (passport map, daily budget, COL preferences)
separate from app-level configuration (API keys, provider routing, etc.)
which lives in .env / environment variables.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "user_prefs.db"

# All keys managed here, with their factory defaults
PREF_DEFAULTS: dict[str, str] = {
    "visited_countries": "",
    # Tile-level visited places (ISO 3166-2 subdivision codes for the
    # subdivided whitelist countries + plain ISO2 country tiles).  When
    # blank but visited_countries is set, reads fall back to the legacy
    # country list (each country becomes its country-level tile).
    "visited_tiles": "",
    "avoid_countries": "",
    # Tile-level avoided regions (ISO 3166-2 subdivision codes for the
    # subdivided whitelist countries).  When >=80% of a country's regions
    # are avoided, the whole country behaves as avoided (derived — the
    # stored per-tile choices are never mutated by that rule).
    "avoid_tiles": "",
    "col_expected_daily": "0",
    "col_tolerance_pct": "25",
    # Legacy per-category fields — kept so migration from .env works
    "col_hotel": "0",
    "col_food": "0",
    "col_transit": "0",
    "col_culture": "0",
    # Detour stop-length preferences
    "detour_min_stop_days": "4",
    "detour_max_stop_days": "5",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prefs (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Module-level read cache — invalidated on every write
# ---------------------------------------------------------------------------
_cache: dict[str, str] | None = None


def _load_from_db() -> dict[str, str]:
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT key, value FROM prefs").fetchall()
            result = dict(PREF_DEFAULTS)
            result.update(
                {r["key"]: r["value"] for r in rows if r["key"] in PREF_DEFAULTS}
            )
        result = _migrate_retired_region_tiles(result)
        return result
    except Exception:
        return dict(PREF_DEFAULTS)


def _migrate_retired_region_tiles(prefs: dict[str, str]) -> dict[str, str]:
    """Collapse retired MX/BR/AU region tiles into country-level marks.

    Visited regions collapse to the country tile visited (full-country
    credit); avoided regions collapse to a country-level avoid; visited
    wins when both exist (see yonder.tiles.collapse_retired_region_prefs).
    The collapsed values are persisted so the migration runs once.
    """
    try:
        from yonder.tiles import collapse_retired_region_prefs

        changed = collapse_retired_region_prefs(prefs)
        if changed:
            with _connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)",
                    list(changed.items()),
                )
                conn.commit()
            prefs.update(changed)
    except Exception:
        pass
    return prefs


def _invalidate() -> None:
    global _cache
    _cache = None


def get_all_prefs() -> dict[str, str]:
    """Return all user prefs (with defaults filled in for missing keys)."""
    global _cache
    if _cache is None:
        _cache = _load_from_db()
    return _cache


def get_pref(key: str) -> str:
    return get_all_prefs().get(key, PREF_DEFAULTS.get(key, ""))


def set_prefs(updates: dict[str, str]) -> None:
    """Persist one or more user pref values and invalidate the read cache."""
    valid = {k: str(v) for k, v in updates.items() if k in PREF_DEFAULTS}
    if not valid:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)",
            list(valid.items()),
        )
        conn.commit()
    _invalidate()


def migrate_from_env(env: dict[str, str]) -> None:
    """One-time migration: copy any non-empty pref values found in the env dict
    into user_prefs.db, without overwriting existing db entries."""
    key_map = {
        "VISITED_COUNTRIES": "visited_countries",
        "AVOID_COUNTRIES": "avoid_countries",
        "COL_EXPECTED_DAILY": "col_expected_daily",
        "COL_TOLERANCE_PCT": "col_tolerance_pct",
        "COL_HOTEL": "col_hotel",
        "COL_FOOD": "col_food",
        "COL_TRANSIT": "col_transit",
        "COL_CULTURE": "col_culture",
        "DETOUR_MIN_STOP_DAYS": "detour_min_stop_days",
        "DETOUR_MAX_STOP_DAYS": "detour_max_stop_days",
    }
    existing = _load_from_db()
    to_migrate = {}
    for env_key, pref_key in key_map.items():
        env_val = (env.get(env_key) or "").strip()
        if env_val and not existing.get(pref_key, "").strip():
            to_migrate[pref_key] = env_val
    if to_migrate:
        set_prefs(to_migrate)
