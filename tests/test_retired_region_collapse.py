"""MX/BR/AU return to single-country tiles — retired-region collapse.

Covers:
1. Whitelist shrink — only US/CA/GB subdivided; MX/BR/AU are single tiles
   with full whole-country land areas.
2. Read tolerance — normalize_tile_list collapses stale retired region
   codes to the country tile instead of dropping them.
3. Stored-pref migration — collapse_retired_region_prefs: visited regions
   → country visited, avoided regions → country avoided, visited wins.
4. km² / XP consistency and unchanged US/CA/GB behaviour.
5. Map payload — tiles_admin1.json carries no MX/BR/AU features.
"""
from __future__ import annotations

import json
from pathlib import Path

from yonder import tiles as T
from yonder.country_size import COUNTRY_SIZE
from yonder.xp import compute_xp

# ---------------------------------------------------------------------------
# 1. Whitelist + areas
# ---------------------------------------------------------------------------


def test_whitelist_is_us_ca_gb_only():
    assert set(T.SUBDIVIDED_COUNTRIES) == {"US", "CA", "GB"}
    assert T.RETIRED_SUBDIVIDED_COUNTRIES == {"MX", "BR", "AU"}
    for cc in ("MX", "BR", "AU"):
        assert not T.is_subdivided(cc)
        # Single tile credits the full country land area
        assert T.tile_area(cc) == COUNTRY_SIZE[cc][0]
        assert T.country_total_area(cc) == COUNTRY_SIZE[cc][0]
        assert not any(k.startswith(cc + "-") for k in T.SUBDIVISION_TILES)


def test_no_home_regions_for_retired_countries():
    assert T.unvisited_home_regions("MX", []) == []
    assert T.unvisited_home_regions("BR", ["BR"]) == []
    assert T.unvisited_home_regions("AU", []) == []
    # Subdivided countries still report regions
    assert len(T.unvisited_home_regions("CA", [])) == 13


# ---------------------------------------------------------------------------
# 2. Read tolerance for stale codes
# ---------------------------------------------------------------------------


def test_normalize_collapses_retired_region_codes():
    out = T.normalize_tile_list(["MX-JAL", "MX-OAX", "US-CA", "BR-SP", "AU"])
    assert out == ["MX", "US-CA", "BR", "AU"]
    # Plain country + retired region dedupe to one entry, stamp order kept
    assert T.normalize_tile_list("MX,MX-JAL,FR") == ["MX", "FR"]
    assert T.normalize_tile_list("MX-JAL,MX") == ["MX"]
    # Genuinely unknown subdivision codes still drop
    assert T.normalize_tile_list(["FR-IDF", "XX-YY"]) == []


def test_fully_visited_and_km2_with_stale_codes():
    tiles = T.normalize_tile_list(["MX-JAL", "MX-YUC"])
    assert tiles == ["MX"]
    assert T.fully_visited_countries(tiles) == {"MX"}
    assert T.unlocked_km2(tiles) == COUNTRY_SIZE["MX"][0]
    # Even un-normalized stale codes never crash km² totals
    assert T.unlocked_km2(["MX-JAL", "MX"]) == COUNTRY_SIZE["MX"][0]


# ---------------------------------------------------------------------------
# 3. Stored-pref migration
# ---------------------------------------------------------------------------


def test_collapse_visited_regions_to_country():
    changed = T.collapse_retired_region_prefs(
        {"visited_tiles": "US-NY,MX-JAL,MX-OAX,FR", "avoid_tiles": "", "avoid_countries": ""}
    )
    assert changed["visited_tiles"] == "US-NY,MX,FR"
    assert changed["visited_countries"] == "US,MX,FR"
    assert "avoid_tiles" not in changed and "avoid_countries" not in changed


def test_collapse_avoided_regions_to_country_avoid():
    changed = T.collapse_retired_region_prefs(
        {"visited_tiles": "", "avoid_tiles": "CA-ON,BR-SP,BR-RJ", "avoid_countries": "RU"}
    )
    assert changed["avoid_tiles"] == "CA-ON"
    assert changed["avoid_countries"] == "RU,BR"


def test_visited_wins_over_avoid():
    changed = T.collapse_retired_region_prefs(
        {
            "visited_tiles": "AU-NSW",
            "avoid_tiles": "AU-QLD,AU-WA",
            "avoid_countries": "",
        }
    )
    assert changed["visited_tiles"] == "AU"
    assert changed["avoid_tiles"] == ""
    # AU never lands in avoid_countries — visited wins
    assert "avoid_countries" not in changed


def test_clean_prefs_are_a_noop():
    assert (
        T.collapse_retired_region_prefs(
            {"visited_tiles": "US-NY,CA-ON,GB-ENG,MX", "avoid_tiles": "CA-BC", "avoid_countries": "RU"}
        )
        == {}
    )


def test_user_prefs_migration_persists(tmp_path, monkeypatch):
    import yonder.user_prefs as up

    monkeypatch.setattr(up, "DB_PATH", tmp_path / "prefs.db")
    up._invalidate()
    with up._connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)",
            [
                ("visited_tiles", "MX-JAL,US-NY"),
                ("avoid_tiles", "BR-SP,CA-ON"),
                ("avoid_countries", ""),
            ],
        )
        conn.commit()
    prefs = up.get_all_prefs()
    assert prefs["visited_tiles"] == "MX,US-NY"
    assert prefs["visited_countries"] == "MX,US"
    assert prefs["avoid_tiles"] == "CA-ON"
    assert prefs["avoid_countries"] == "BR"
    # Persisted: a fresh read from the DB (cache cleared) sees collapsed values
    up._invalidate()
    assert up.get_all_prefs()["visited_tiles"] == "MX,US-NY"


# ---------------------------------------------------------------------------
# 4. XP / tiers consistent after collapse
# ---------------------------------------------------------------------------


def test_xp_after_collapse_counts_full_country():
    profile = compute_xp(T.normalize_tile_list(["MX-JAL", "BR-SP"]), [])
    assert profile["km2"] == COUNTRY_SIZE["MX"][0] + COUNTRY_SIZE["BR"][0]
    assert profile["visited_count"] == 2
    assert profile["rank"]  # tier resolves without crashing


# ---------------------------------------------------------------------------
# 5. Map payload
# ---------------------------------------------------------------------------


def test_tiles_admin1_json_has_no_retired_regions():
    data = json.loads(
        (Path(__file__).resolve().parent.parent / "yonder/static/tiles_admin1.json").read_text()
    )
    ccs = {f["properties"]["t"].split("-")[0] for f in data["features"]}
    assert ccs == {"US", "CA", "GB"}
