"""Tests for the curated POI module — import idempotency, city-name lookup,
and brief-payload integration.

POI table operations are isolated in a throwaway PostgreSQL schema so the real
``pois`` table is never touched.
"""
from __future__ import annotations

import io
import os
import textwrap
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Throwaway-schema fixture
# ---------------------------------------------------------------------------

_POIS_DDL = """
CREATE TABLE pois (
    feature_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    emoji          TEXT,
    category       TEXT,
    note           TEXT,
    google_maps_url TEXT,
    lat            DOUBLE PRECISION,
    lon            DOUBLE PRECISION,
    city_slug      TEXT NOT NULL DEFAULT '',
    address        TEXT,
    list_title     TEXT,
    imported_at    TIMESTAMPTZ DEFAULT NOW()
)
"""


@pytest.fixture()
def poi_schema(monkeypatch):
    """Isolated pois table in a throwaway PG schema.

    Monkeypatches ``yonder.poi.get_conn`` so the module reads/writes only
    the throwaway schema — never the real ``pois`` table.
    """
    import psycopg2

    import yonder.poi as poi_mod
    from yonder.db import Conn

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    schema = f"test_poi_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(_POIS_DDL)

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    monkeypatch.setattr(poi_mod, "get_conn", _get_conn)
    yield schema

    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


# ---------------------------------------------------------------------------
# Tiny CSV helpers
# ---------------------------------------------------------------------------

_CSV_HEADER = (
    "feature_id,name,emoji,category,status,note,google_maps_url,"
    "lat,lon,address,list_title,drop_reason\n"
)


def _csv_row(
    fid: str,
    name: str,
    emoji: str = "☕",
    category: str = "cafe",
    note: str = "",
    lat: str = "48.8566",
    lon: str = "2.3522",
    address: str = "1 Rue de Rivoli, Paris, France",
    list_title: str = "Paris Places",
    drop_reason: str = "",
) -> str:
    return (
        f"{fid},{name},{emoji},{category},visited,{note},"
        f"https://maps.google.com/?cid={fid},{lat},{lon},"
        f'"{address}","{list_title}",{drop_reason}\n'
    )


def _make_csv(*rows: str) -> "io.StringIO":
    return io.StringIO(_CSV_HEADER + "".join(rows))


# ---------------------------------------------------------------------------
# Importer tests
# ---------------------------------------------------------------------------


def test_import_pois_basic(poi_schema, tmp_path):
    """Basic import: inserts rows and returns count."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row("f1", "Café de Flore")
        + _csv_row("f2", "Les Deux Magots", list_title="Paris Places"),
    )
    n = poi_mod.import_pois(csv_file)
    assert n == 2


def test_import_pois_idempotent(poi_schema, tmp_path):
    """Re-importing the same CSV leaves the row count unchanged."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row("f1", "Café de Flore")
        + _csv_row("f2", "Les Deux Magots"),
    )
    poi_mod.import_pois(csv_file)
    poi_mod.import_pois(csv_file)  # second run — should upsert, not duplicate

    with poi_mod.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM pois").fetchone()
    assert row["cnt"] == 2


def test_import_pois_updates_existing_row(poi_schema, tmp_path):
    """Re-importing with a changed name updates the row."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "v1.csv"
    csv_file.write_text(_CSV_HEADER + _csv_row("f1", "Old Name"))
    poi_mod.import_pois(csv_file)

    csv_v2 = tmp_path / "v2.csv"
    csv_v2.write_text(_CSV_HEADER + _csv_row("f1", "New Name"))
    poi_mod.import_pois(csv_v2)

    with poi_mod.get_conn() as conn:
        row = conn.execute("SELECT name FROM pois WHERE feature_id = 'f1'").fetchone()
    assert row["name"] == "New Name"


def test_import_pois_skips_drop_reason_rows(poi_schema, tmp_path):
    """Rows with a non-empty drop_reason are not imported."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row("f1", "Good Place")
        + _csv_row("f2", "Bad Place", drop_reason="duplicate"),
    )
    n = poi_mod.import_pois(csv_file)
    assert n == 1


def test_import_pois_discards_status_column(poi_schema, tmp_path):
    """The status column (visited / want_to_go) is silently discarded."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    # Vary status across rows — all should be imported equally.
    content = (
        _CSV_HEADER
        + _csv_row("f1", "Place A")  # status=visited (default)
        + "f2,Place B,🍜,food,want_to_go,,https://maps.google.com/?cid=f2,"
        + "35.6762,139.6503,Shibuya Tokyo Japan,Tokyo Places,\n"
    )
    csv_file.write_text(content)
    n = poi_mod.import_pois(csv_file)
    assert n == 2


# ---------------------------------------------------------------------------
# City-name lookup tests
# ---------------------------------------------------------------------------


def test_picks_for_city_exact_match(poi_schema, tmp_path):
    """picks_for_city returns POIs whose city_slug matches the destination."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row("f1", "Café de Flore", list_title="Paris Places")
        + _csv_row("f2", "Louvre", category="museum", list_title="Paris Places")
        + _csv_row(
            "f3",
            "Ramen Ichiran",
            list_title="Tokyo Places",
            lat="35.6762",
            lon="139.6503",
            address="Shibuya, Tokyo, Japan",
        ),
    )
    poi_mod.import_pois(csv_file)

    paris = poi_mod.picks_for_city("Paris")
    names = {p["name"] for p in paris}
    assert "Café de Flore" in names
    assert "Louvre" in names
    # Tokyo POI must not appear in Paris results
    assert "Ramen Ichiran" not in names


def test_picks_for_city_case_insensitive(poi_schema, tmp_path):
    """City lookup is case-insensitive and accent-agnostic."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(_CSV_HEADER + _csv_row("f1", "Café de Flore", list_title="Paris Places"))
    poi_mod.import_pois(csv_file)

    assert poi_mod.picks_for_city("PARIS")
    assert poi_mod.picks_for_city("paris")
    assert poi_mod.picks_for_city("Páris")  # accent variant


def test_picks_for_city_unknown_city_returns_empty(poi_schema, tmp_path):
    """picks_for_city returns [] for a city with no POIs."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(_CSV_HEADER + _csv_row("f1", "Café de Flore", list_title="Paris Places"))
    poi_mod.import_pois(csv_file)

    result = poi_mod.picks_for_city("Nowheresville")
    assert result == []


def test_picks_for_city_none_returns_empty(poi_schema):
    """picks_for_city(None) returns [] without hitting the database."""
    import yonder.poi as poi_mod

    assert poi_mod.picks_for_city(None) == []


def test_picks_for_city_prefers_notes(poi_schema, tmp_path):
    """Rows with a personal note appear before rows without one."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row("f1", "Plain Café", note="", list_title="Paris Places")
        + _csv_row("f2", "Noted Café", note="Best croissant in Paris", list_title="Paris Places"),
    )
    poi_mod.import_pois(csv_file)

    picks = poi_mod.picks_for_city("Paris")
    assert picks[0]["name"] == "Noted Café"


def test_picks_for_city_respects_limit(poi_schema, tmp_path):
    """picks_for_city never returns more than limit rows."""
    import yonder.poi as poi_mod

    rows = "".join(_csv_row(f"f{i}", f"Place {i}", list_title="Paris Places") for i in range(20))
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(_CSV_HEADER + rows)
    poi_mod.import_pois(csv_file)

    picks = poi_mod.picks_for_city("Paris", limit=5)
    assert len(picks) <= 5


# ---------------------------------------------------------------------------
# PlaceBrief integration
# ---------------------------------------------------------------------------


def test_place_brief_includes_poi_picks(poi_schema, tmp_path):
    """picks_for_city is called by encyclopedia._poi_picks and its result
    appears in PlaceBrief.to_dict()."""
    import yonder.poi as poi_mod
    from yonder.encyclopedia import _poi_picks

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER + _csv_row("f1", "Le Marais", list_title="Paris Places")
    )
    poi_mod.import_pois(csv_file)

    picks = _poi_picks("Paris")
    assert any(p["name"] == "Le Marais" for p in picks)


def test_place_brief_no_picks_for_unknown_city(poi_schema, tmp_path):
    """Brief payload has an empty poi_picks list for unmatched cities."""
    import yonder.poi as poi_mod
    from yonder.encyclopedia import _poi_picks

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(_CSV_HEADER + _csv_row("f1", "Le Marais", list_title="Paris Places"))
    poi_mod.import_pois(csv_file)

    picks = _poi_picks("Atlantis")
    assert picks == []


def test_place_brief_to_dict_has_poi_picks_key(poi_schema, tmp_path):
    """PlaceBrief.to_dict() always includes a poi_picks key."""
    from yonder.encyclopedia import PlaceBrief

    brief = PlaceBrief(title="Paris", poi_picks=[{"name": "Louvre", "emoji": "🎨",
                                                   "category": "museum",
                                                   "note": "", "url": "https://maps.google.com"}])
    d = brief.to_dict()
    assert "poi_picks" in d
    assert d["poi_picks"][0]["name"] == "Louvre"


def test_place_brief_to_dict_empty_poi_picks():
    """PlaceBrief.to_dict() returns [] for poi_picks when none are set."""
    from yonder.encyclopedia import PlaceBrief

    brief = PlaceBrief(title="Nowhere")
    d = brief.to_dict()
    assert d["poi_picks"] == []
