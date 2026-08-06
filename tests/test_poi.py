"""Tests for the curated POI module — import idempotency, city-name lookup,
city-extraction helpers, and brief-payload integration.

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
    closed         BOOLEAN,
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


# ---------------------------------------------------------------------------
# City-extraction helper tests (no DB required)
# ---------------------------------------------------------------------------


def test_city_from_address_city_country_style():
    """Short 'City, Country' address: the first segment is the city."""
    from yonder.poi import _city_from_address

    assert _city_from_address("Juno Beach, France") == "juno beach"
    assert _city_from_address("Jasper, AB T0E 1E0") == "jasper"
    assert _city_from_address("Manitou Beach, SK") == "manitou beach"
    assert _city_from_address("Nelson, BC") == "nelson"


def test_city_from_address_leading_postal_code():
    """Leading postal code is stripped so the city name is extracted."""
    from yonder.poi import _city_from_address

    assert _city_from_address("8001 Zürich, Switzerland") == "zurich"
    assert _city_from_address("06320 La Turbie, France") == "la turbie"
    assert _city_from_address("08024 Barcelona, Spain") == "barcelona"


def test_city_from_address_hyphenated_postal_code():
    """Hyphenated European/Japanese postal codes (00-901, 101-0021) don't
    leave stray dashes in the extracted slug.

    _city_from_address returns the raw (pre-alias) slug; alias normalisation
    happens in _extract_city.
    """
    from yonder.poi import _city_from_address

    # Polish: "00-901 Warszawa" → raw "warszawa" (no stray dash)
    assert _city_from_address("plac Defilad 1, 00-901 Warszawa, Poland") == "warszawa"
    # Japanese: "Tokyo 101-0021" → clean "tokyo" (no stray dash/digits)
    assert _city_from_address(
        "2 Chome-16-2 Sotokanda, Chiyoda City, Tokyo 101-0021, Japan"
    ) == "tokyo"
    # Portuguese: "1170-133 Lisboa" → raw "lisboa" at _city_from_address level;
    # the alias (lisboa → lisbon) is applied later by _extract_city.
    result = _city_from_address("R. Palmira 46A, 1170-133 Lisboa, Portugal")
    assert result == "lisboa"


def test_city_from_address_trailing_uk_postcode():
    """Trailing UK postcode on parts[0] is stripped to reveal the city."""
    from yonder.poi import _city_from_address

    assert _city_from_address("Stonehaven AB39 2TL, United Kingdom") == "stonehaven"
    assert _city_from_address("Edinburgh EH2 4BL, United Kingdom") == "edinburgh"


def test_city_from_address_skips_real_streets():
    """Parts[0] that look like a street (digits after stripping postal code)
    are not used as a city name."""
    from yonder.poi import _city_from_address

    # Street with embedded house number
    assert _city_from_address(
        "140 Rock Creek Church Rd NW, Washington, DC 20011, United States"
    ) == ""
    # Full street address with embedded postal+city
    # (Praça do Império 1400-206 Lisboa — digits remain after leading-code strip)
    assert _city_from_address("Praça do Império 1400-206 Lisboa, Portugal") == ""


def test_city_from_address_skips_admin_areas():
    """Administrative area names are not mistaken for cities."""
    from yonder.poi import _city_from_address

    assert _city_from_address("Improvement District No. 9, AB") == ""


def test_city_from_list_title_new_york_preserved():
    """'New York' list titles must extract 'new york', not be filtered as a
    US state.  New York is both a state AND the most common destination city
    in the dataset — the comma-split rule handles 'New York, New York' and the
    plain 'New York Places' title is accepted as a city-level list."""
    from yonder.poi import _city_from_list_title

    # "New York, New York" → split on comma → "New York" → "new york" ✓
    assert _city_from_list_title("New York, New York") == "new york"
    # Plain "New York Places" → strip suffix → "New York" → "new york" ✓
    assert _city_from_list_title("New York Places") == "new york"
    # Pure state-level lists that ARE filtered correctly
    assert _city_from_list_title("California Places") == ""
    assert _city_from_list_title("Texas") == ""


def test_city_aliases_applied():
    """_extract_city normalises local-language names to English equivalents."""
    from yonder.poi import _extract_city

    # "Warszawa" from a Polish address → "warsaw"
    assert _extract_city("Poland Places", "plac Defilad 1, 00-901 Warszawa, Poland") == "warsaw"
    # "Lisboa" from a Portuguese address → "lisbon"
    assert _extract_city("Portugal Places", "R. Palmira 46A, 1170-133 Lisboa, Portugal") == "lisbon"
    # "Köln" normalised then aliased → "cologne"
    assert _extract_city("Deutschland Places", "Some Str. 1, 50667 Köln, Germany") in ("cologne", "koln", "")


def test_picks_for_city_lisbon_via_address_backfill(poi_schema, tmp_path):
    """POIs stored with Portuguese addresses are findable via picks_for_city('Lisbon')."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row(
            "lisbon1",
            "Conserveira de Lisboa",
            list_title="Portugal Places",
            address="R. dos Bacalhoeiros 34, 1100-071 Lisboa, Portugal",
        )
    )
    poi_mod.import_pois(csv_file)

    picks = poi_mod.picks_for_city("Lisbon")
    assert any(p["name"] == "Conserveira de Lisboa" for p in picks), (
        f"Expected Lisbon entry; got {picks}"
    )


def test_picks_for_city_warsaw_via_address_backfill(poi_schema, tmp_path):
    """POIs stored with Polish addresses are findable via picks_for_city('Warsaw')."""
    import yonder.poi as poi_mod

    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        _CSV_HEADER
        + _csv_row(
            "warsaw1",
            "Palace of Culture and Science",
            list_title="Poland Places",
            address="plac Defilad 1, 00-901 Warszawa, Poland",
        )
    )
    poi_mod.import_pois(csv_file)

    picks = poi_mod.picks_for_city("Warsaw")
    assert any(p["name"] == "Palace of Culture and Science" for p in picks), (
        f"Expected Warsaw entry; got {picks}"
    )


# ---------------------------------------------------------------------------
# backfill_city_slugs integration tests
# ---------------------------------------------------------------------------


def _direct_insert(conn, feature_id: str, name: str, city_slug: str,
                   address: str = "", list_title: str = "") -> None:
    """Insert a raw row directly, bypassing _extract_city, to simulate legacy data."""
    conn.execute(
        """
        INSERT INTO pois (feature_id, name, city_slug, address, list_title)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (feature_id) DO UPDATE
            SET city_slug  = EXCLUDED.city_slug,
                address    = EXCLUDED.address,
                list_title = EXCLUDED.list_title
        """,
        (feature_id, name, city_slug, address, list_title),
    )


def test_backfill_fills_empty_city_slug(poi_schema):
    """backfill_city_slugs() populates city_slug for rows that were stored blank."""
    import yonder.poi as poi_mod

    # Seed a row with empty city_slug but a parseable address
    with poi_mod.get_conn() as conn:
        _direct_insert(
            conn,
            "bf-empty",
            "Park Güell",
            city_slug="",
            address="08024 Barcelona, Spain",
            list_title="Spain",
        )
        conn.commit()

    changed = poi_mod.backfill_city_slugs()
    assert changed >= 1

    with poi_mod.get_conn() as conn:
        row = conn.execute(
            "SELECT city_slug FROM pois WHERE feature_id = 'bf-empty'"
        ).fetchone()
    assert row["city_slug"] == "barcelona", (
        f"Expected 'barcelona', got {row['city_slug']!r}"
    )


def test_backfill_fixes_stray_dash_slug(poi_schema):
    """backfill_city_slugs() fixes legacy '- warszawa' and 'tokyo -' slugs."""
    import yonder.poi as poi_mod

    with poi_mod.get_conn() as conn:
        _direct_insert(
            conn, "bf-dash1", "Palace of Culture", "- warszawa",
            address="plac Defilad 1, 00-901 Warszawa, Poland",
            list_title="Poland Places",
        )
        _direct_insert(
            conn, "bf-dash2", "Kanda Shrine", "tokyo -",
            address="2 Chome-16-2 Sotokanda, Chiyoda City, Tokyo 101-0021, Japan",
            list_title="Japan Places",
        )
        conn.commit()

    changed = poi_mod.backfill_city_slugs()
    assert changed >= 2

    with poi_mod.get_conn() as conn:
        rows = {
            r["feature_id"]: r["city_slug"]
            for r in conn.execute(
                "SELECT feature_id, city_slug FROM pois WHERE feature_id IN ('bf-dash1','bf-dash2')"
            ).fetchall()
        }
    assert rows["bf-dash1"] == "warsaw", f"Expected 'warsaw', got {rows['bf-dash1']!r}"
    assert rows["bf-dash2"] == "tokyo", f"Expected 'tokyo', got {rows['bf-dash2']!r}"


def test_backfill_applies_city_aliases(poi_schema):
    """backfill_city_slugs() translates local-language slugs to English equivalents."""
    import yonder.poi as poi_mod

    with poi_mod.get_conn() as conn:
        _direct_insert(
            conn, "bf-alias1", "Conserveira de Lisboa", "lisboa",
            address="R. dos Bacalhoeiros 34, 1100-071 Lisboa, Portugal",
            list_title="Portugal Places",
        )
        conn.commit()

    changed = poi_mod.backfill_city_slugs()
    assert changed >= 1

    with poi_mod.get_conn() as conn:
        row = conn.execute(
            "SELECT city_slug FROM pois WHERE feature_id = 'bf-alias1'"
        ).fetchone()
    assert row["city_slug"] == "lisbon", f"Expected 'lisbon', got {row['city_slug']!r}"


def test_backfill_is_idempotent(poi_schema):
    """Calling backfill_city_slugs() twice does not double-update clean rows."""
    import yonder.poi as poi_mod

    with poi_mod.get_conn() as conn:
        _direct_insert(
            conn, "bf-clean", "Sagrada Família", "",
            address="08013 Barcelona, Spain",
            list_title="Spain",
        )
        conn.commit()

    first = poi_mod.backfill_city_slugs()
    assert first >= 1
    second = poi_mod.backfill_city_slugs()
    assert second == 0, "Second backfill should be a no-op"


def test_backfill_then_picks_for_city_lisbon(poi_schema):
    """Lisbon POIs stored with legacy '- lisboa' slug are findable after backfill."""
    import yonder.poi as poi_mod

    with poi_mod.get_conn() as conn:
        _direct_insert(
            conn, "bf-lisbon1", "Jerónimos Monastery", "- lisboa",
            address="Praça do Império, 1400-038 Lisboa, Portugal",
            list_title="Portugal Places",
        )
        conn.commit()

    poi_mod.backfill_city_slugs()

    picks = poi_mod.picks_for_city("Lisbon")
    assert any(p["name"] == "Jerónimos Monastery" for p in picks), (
        f"Expected Lisbon entry after backfill; got {picks}"
    )
