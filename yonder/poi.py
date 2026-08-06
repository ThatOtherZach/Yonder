"""Curated POI (Place of Interest) dataset — import, lookup, and picks.

The ~2,400-row dataset lives in ``attached_assets/YONDER_POI_production_*.csv``
and is imported idempotently via :func:`import_pois`.  Matching against a
destination city is done by :func:`picks_for_city`; each result is a compact
dict suitable for direct template / JSON use.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any

from yonder.db import get_conn

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lowercase, decompose accents, collapse whitespace."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())


# Tokens that indicate a list_title is country- or region-level, not a city.
_REGION_SLUGS: frozenset[str] = frozenset(
    {
        # Countries
        "japan", "thailand", "france", "germany", "spain", "ireland",
        "ukraine", "poland", "switzerland", "belgium", "netherlands",
        "hungary", "cyprus", "portugal", "italy", "austria", "czech republic",
        "south korea", "korea",
        # US states
        "california", "illinois", "texas", "utah", "washington", "oregon",
        "south dakota", "florida", "georgia", "colorado", "nevada",
        # Canadian provinces
        "bc", "alberta", "ontario", "british columbia",
        # UK regions
        "scotland", "england", "wales",
        # Special/meta titles
        "churono", "land of scots", "o-re-going", "murica", "van beer list",
        "want to go", "deutschland",
    }
)

# Country / region names found in addresses that are NOT city names.
_ADDRESS_NON_CITY: frozenset[str] = frozenset(
    {
        "united states", "usa", "canada", "united kingdom", "uk",
        "france", "germany", "spain", "italy", "portugal", "belgium",
        "netherlands", "switzerland", "austria", "poland", "czech republic",
        "hungary", "ukraine", "ireland", "scotland", "japan", "thailand",
        "south korea", "korea", "china", "india", "cyprus", "australia",
        "new zealand", "mexico", "brazil", "turkey",
    }
)

# ---------------------------------------------------------------------------
# City extraction
# ---------------------------------------------------------------------------

_TITLE_SUFFIXES = [
    r"\s*\|.*$",              # "BC Places | Van Beer List" → strip after |
    r"\s*\([^)]+\)\s*",       # "London (The British One)"
    r"\s*&\s*beyond.*$",      # "Las Vegas & Beyond"
    r"\s+places?\s*$",        # "Japan Places", "Cyprus Places"
    r"\s+photography\s*$",    # "Toronto Photography"
    r"\s+road\s+trip.*$",     # "'Murica Road Trip"
    r"^'",                    # Leading apostrophe
]


def _city_from_list_title(title: str) -> str:
    """Return a normalised city slug from a Google Maps list title, or '' if
    the title refers to a country / region rather than a single city."""
    t = title
    for pattern in _TITLE_SUFFIXES:
        t = re.sub(pattern, "", t, flags=re.IGNORECASE).strip()
    # "New York, New York" → take the first part
    if "," in t:
        t = t.split(",")[0].strip()
    slug = _normalize(t)
    if not slug or slug in _REGION_SLUGS:
        return ""
    return slug


def _city_from_address(address: str) -> str:
    """Heuristically extract a city slug from a comma-separated address."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return ""

    # Candidates: everything except the first part (street) — walk right-to-left
    # skipping postal codes, state codes, and country names.
    candidates = parts[1:] if len(parts) > 1 else parts

    for part in reversed(candidates):
        clean = part
        # Strip UK postcodes: "NW1 0ND", "AB33 8JF"
        clean = re.sub(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", "", clean)
        # Strip Canadian postcodes: "M4L 1H9"
        clean = re.sub(r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b", "", clean)
        # Strip US ZIPs: "10024" or "97477-1234"
        clean = re.sub(r"\b\d{5}(-\d{4})?\b", "", clean)
        # Strip 2-letter state/province abbreviation tokens
        clean = re.sub(r"\b[A-Z]{2}\b", "", clean)
        # Strip remaining stand-alone digit groups (Thai, Korean postcodes, etc.)
        clean = re.sub(r"\b\d+\b", "", clean)
        clean = clean.strip(" ,.")

        if not clean:
            continue

        slug = _normalize(clean)
        if not slug:
            continue
        if slug in _ADDRESS_NON_CITY or slug in _REGION_SLUGS:
            continue
        # Skip very short fragments (state/province abbreviation leftovers)
        if len(slug) <= 2:
            continue
        return slug

    return ""


def _extract_city(list_title: str, address: str) -> str:
    """Best-effort city slug for a POI row: list_title first, address fallback."""
    city = _city_from_list_title(list_title)
    if not city:
        city = _city_from_address(address)
    return city


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_pois(csv_path: str | Path | None = None) -> int:
    """Idempotent CSV importer.  Returns number of rows upserted.

    Deduplicates on ``feature_id``; existing rows are overwritten so
    re-running is safe after CSV updates.  The ``status`` column is
    silently discarded — all POIs are treated as a flat collection.
    """
    if csv_path is None:
        # Default: look for the file relative to the project root.
        csv_path = (
            Path(__file__).resolve().parent.parent
            / "attached_assets"
            / "YONDER_POI_production_1786025728691.csv"
        )
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"POI CSV not found: {path}")

    rows: list[tuple] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            feature_id = (row.get("feature_id") or "").strip()
            if not feature_id:
                continue  # skip rows without a stable identifier
            name = (row.get("name") or "").strip()
            if not name:
                continue
            # Discard rows explicitly marked with a drop_reason
            if (row.get("drop_reason") or "").strip():
                continue
            address = (row.get("address") or "").strip()
            list_title = (row.get("list_title") or "").strip()
            lat_s = (row.get("lat") or "").strip()
            lon_s = (row.get("lon") or "").strip()
            try:
                lat: float | None = float(lat_s) if lat_s else None
                lon: float | None = float(lon_s) if lon_s else None
            except ValueError:
                lat = lon = None
            rows.append((
                feature_id,
                name,
                (row.get("emoji") or "").strip(),
                (row.get("category") or "").strip(),
                (row.get("note") or "").strip(),
                (row.get("google_maps_url") or "").strip(),
                lat,
                lon,
                _extract_city(list_title, address),
                address,
                list_title,
            ))

    if not rows:
        return 0

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO pois
                (feature_id, name, emoji, category, note, google_maps_url,
                 lat, lon, city_slug, address, list_title)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (feature_id) DO UPDATE SET
                name          = EXCLUDED.name,
                emoji         = EXCLUDED.emoji,
                category      = EXCLUDED.category,
                note          = EXCLUDED.note,
                google_maps_url = EXCLUDED.google_maps_url,
                lat           = EXCLUDED.lat,
                lon           = EXCLUDED.lon,
                city_slug     = EXCLUDED.city_slug,
                address       = EXCLUDED.address,
                list_title    = EXCLUDED.list_title
            """,
            rows,
        )
        conn.commit()

    return len(rows)


# ---------------------------------------------------------------------------
# City-name lookup
# ---------------------------------------------------------------------------

def picks_for_city(
    city: str | None,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return up to *limit* POIs whose city_slug matches *city*.

    Matching is normalised + case-insensitive.  Rows that have a personal
    note are preferred; within each group the order is stable (DB insert
    order).  Returns [] when city is blank or no matches exist — callers
    must never show an empty "Curator's picks" section.
    """
    if not city:
        return []
    slug = _normalize(city)
    if not slug:
        return []

    with get_conn() as conn:
        # Exact match first, then prefix match (handles "washington" ↔ "washington dc")
        rows = conn.execute(
            """
            SELECT name, emoji, category, note, google_maps_url
            FROM pois
            WHERE NOT COALESCE(closed, FALSE)
              AND (city_slug = %s
               OR city_slug LIKE %s
               OR %s LIKE city_slug || '%%')
            ORDER BY (note <> '') DESC, name
            LIMIT %s
            """,
            (slug, slug + " %", slug, limit),
        ).fetchall()

    return [
        {
            "name": r["name"],
            "emoji": r["emoji"] or "📍",
            "category": r["category"],
            "note": r["note"],
            "url": r["google_maps_url"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Full POI export (for map endpoint)
# ---------------------------------------------------------------------------

def all_pois_for_map() -> list[dict[str, Any]]:
    """Slim POI list for the interactive map JSON endpoint.

    Returns only rows with valid coordinates.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT name, emoji, category, note, google_maps_url, lat, lon
            FROM pois
            WHERE lat IS NOT NULL AND lon IS NOT NULL
              AND NOT COALESCE(closed, FALSE)
            ORDER BY name
            """,
        ).fetchall()
    return [
        {
            "n": r["name"],
            "e": r["emoji"] or "📍",
            "c": r["category"],
            "t": r["note"],
            "u": r["google_maps_url"],
            "lat": r["lat"],
            "lon": r["lon"],
        }
        for r in rows
    ]
