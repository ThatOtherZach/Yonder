"""80/20 avoid-saturation rule.

Marking >=80% of a subdivided country's regions as "avoid" makes the whole
country behave as avoided everywhere the country-level avoid list applies.

Covers:
1. Threshold edges (just below vs at 80%), visited-tile exclusion, and the
   visited-wins precedence.
2. Settings.effective_avoid_country_list — derived set, stored prefs
   untouched.
3. Consumers — stopover filtering (filter_ideas) and is_avoided_iata see
   the saturated country.
4. /api/travel-map — region avoid tiles persist, round-trip, and the
   response reports the effective avoid set.
"""
from __future__ import annotations

from datetime import date

import pytest

from yonder import tiles as T
from yonder.config import Settings

US = T.SUBDIVIDED_COUNTRIES["US"]  # 51 tiles → 80% = 40.8 → needs 41
CA = T.SUBDIVIDED_COUNTRIES["CA"]  # 13 tiles → 80% = 10.4 → needs 11
GB = T.SUBDIVIDED_COUNTRIES["GB"]  # 4 tiles → needs 4 (3/4 = 75%)


# ---------------------------------------------------------------------------
# 1. Saturation helper
# ---------------------------------------------------------------------------


def test_below_threshold_not_saturated():
    assert T.avoid_saturated_countries(list(US[:40]), []) == set()  # 78.4%
    assert T.avoid_saturated_countries(list(CA[:10]), []) == set()  # 76.9%
    assert T.avoid_saturated_countries(list(GB[:3]), []) == set()  # 75%


def test_at_or_above_threshold_saturates():
    assert T.avoid_saturated_countries(list(US[:41]), []) == {"US"}  # 80.4%
    assert T.avoid_saturated_countries(list(CA[:11]), []) == {"CA"}
    assert T.avoid_saturated_countries(list(GB), []) == {"GB"}
    # Retired region codes (MX/BR/AU) are no longer subdivision tiles and
    # can never saturate — whole-country avoid is direct now.
    assert T.avoid_saturated_countries(["MX-JAL", "BR-SP", "AU-NSW"], []) == set()


def test_visited_tiles_excluded_from_avoid_tally():
    # 41 avoided US states would saturate, but one of them is also visited:
    # visited wins per tile → only 40 count → below threshold.
    avoid = list(US[:41])
    assert T.avoid_saturated_countries(avoid, [US[0]]) == set()
    # A visited tile OUTSIDE the avoid set does not change the tally
    assert T.avoid_saturated_countries(avoid, [US[45]]) == {"US"}


def test_unmarking_below_threshold_restores_normal_behavior():
    avoid = list(CA[:11])
    assert T.avoid_saturated_countries(avoid, []) == {"CA"}
    avoid.pop()  # user un-marks one region
    assert T.avoid_saturated_countries(avoid, []) == set()


def test_country_codes_and_junk_ignored():
    # Country-level codes and unknown tiles never count toward saturation
    assert T.avoid_saturated_countries(["US", "FR", "XX-ZZ", ""], []) == set()
    # Duplicates counted once
    assert T.avoid_saturated_countries(list(CA[:10]) + [CA[0]] * 5, []) == set()


def test_fully_visited_country_cannot_saturate():
    # Every region visited → avoid tally is zero regardless of avoid marks
    assert T.avoid_saturated_countries(list(GB), list(GB)) == set()
    assert T.fully_visited_countries(list(GB)) == {"GB"}


# ---------------------------------------------------------------------------
# 2. Settings.effective_avoid_country_list
# ---------------------------------------------------------------------------


def _settings(**kw) -> Settings:
    return Settings(testing=True, **kw)


def test_effective_avoid_adds_saturated_country():
    s = _settings(avoid_countries="RU", avoid_tiles=",".join(CA[:11]))
    assert s.avoid_country_list() == ["RU"]  # stored list untouched
    assert s.effective_avoid_country_list() == ["RU", "CA"]


def test_effective_avoid_below_threshold_unchanged():
    s = _settings(avoid_countries="RU", avoid_tiles=",".join(CA[:10]))
    assert s.effective_avoid_country_list() == ["RU"]


def test_effective_avoid_no_duplicate_when_country_already_avoided():
    s = _settings(avoid_countries="CA", avoid_tiles=",".join(CA[:11]))
    assert s.effective_avoid_country_list() == ["CA"]


def test_effective_avoid_respects_visited_precedence():
    s = _settings(
        avoid_tiles=",".join(CA[:11]),
        visited_tiles=CA[0],  # one avoided region is actually visited
    )
    assert s.effective_avoid_country_list() == []


def test_avoid_tile_list_drops_country_codes():
    s = _settings(avoid_tiles="CA-ON,FR,US-TX")
    assert s.avoid_tile_list() == ["CA-ON", "US-TX"]


# ---------------------------------------------------------------------------
# 3. Consumers
# ---------------------------------------------------------------------------


def test_is_avoided_iata_blocks_saturated_country():
    from yonder.countries import is_avoided_iata

    s = _settings(avoid_tiles=",".join(US[:41]))
    effective = s.effective_avoid_country_list()
    assert is_avoided_iata("JFK", effective)
    assert not is_avoided_iata("CDG", effective)


def test_filter_ideas_drops_stopovers_in_saturated_country():
    from yonder.adventure import AdventureRequest, StopoverIdea, filter_ideas

    s = _settings(avoid_tiles=",".join(US[:41]))
    req = AdventureRequest(
        origin="YVR",
        destination="CDG",
        depart_date=date(2026, 9, 1),
        avoid_countries=s.effective_avoid_country_list(),
    )
    ideas = [
        StopoverIdea(iata="ORD", city="Chicago", country="US"),
        StopoverIdea(iata="KEF", city="Reykjavik", country="IS"),
    ]
    out = filter_ideas(ideas, req)
    assert [i.iata for i in out] == ["KEF"]


def test_filter_ideas_keeps_stopovers_below_threshold():
    from yonder.adventure import AdventureRequest, StopoverIdea, filter_ideas

    s = _settings(avoid_tiles=",".join(US[:40]))
    req = AdventureRequest(
        origin="YVR",
        destination="CDG",
        depart_date=date(2026, 9, 1),
        avoid_countries=s.effective_avoid_country_list(),
    )
    ideas = [StopoverIdea(iata="ORD", city="Chicago", country="US")]
    assert [i.iata for i in filter_ideas(ideas, req)] == ["ORD"]


def test_filter_ideas_effective_set_survives_ten_stored_avoids():
    # 10 stored country avoids + a saturated country = 11 entries; the
    # saturated country must not fall off the end of the normalized list.
    from yonder.adventure import AdventureRequest, StopoverIdea, filter_ideas

    stored = ["RU", "CN", "IR", "AF", "SY", "LY", "SO", "YE", "KP", "ML"]
    s = _settings(
        avoid_countries=",".join(stored), avoid_tiles=",".join(US[:41])
    )
    effective = s.effective_avoid_country_list()
    assert "US" in effective and len(effective) == 11
    req = AdventureRequest(
        origin="YVR",
        destination="CDG",
        depart_date=date(2026, 9, 1),
        avoid_countries=effective,
    )
    ideas = [StopoverIdea(iata="ORD", city="Chicago", country="US")]
    assert filter_ideas(ideas, req) == []


# ---------------------------------------------------------------------------
# 4. /api/travel-map persistence round-trip
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from yonder import user_prefs as UP

    monkeypatch.setattr(UP, "DB_PATH", tmp_path / "prefs.db")
    UP._invalidate()
    from fastapi.testclient import TestClient
    from yonder import web as web_module

    yield TestClient(web_module.app, raise_server_exceptions=True)
    UP._invalidate()


def test_travel_map_saves_avoid_tiles_and_reports_effective(client):
    payload = {"visited": ["FR"], "avoid": ["RU"] + list(CA[:11])}
    r = client.post("/api/travel-map", json=payload)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["avoid"] == ["RU"]  # stored country avoids stay country-level
    assert set(j["avoid_tiles"]) == set(CA[:11])
    assert set(j["effective_avoid"]) == {"RU", "CA"}
    assert j["tiles"] == ["FR"]

    from yonder.user_prefs import get_pref

    assert get_pref("avoid_countries") == "RU"
    assert set(get_pref("avoid_tiles").split(",")) == set(CA[:11])


def test_travel_map_visited_tile_wins_over_avoid_mark(client):
    # A region marked both visited and avoided: visited wins per tile
    payload = {"visited": [CA[0]], "avoid": list(CA[:11])}
    r = client.post("/api/travel-map", json=payload)
    j = r.json()
    assert CA[0] in j["tiles"]
    assert CA[0] not in j["avoid_tiles"]
    # Only 10 avoid tiles remain → below threshold → CA not effective-avoided
    assert "CA" not in j["effective_avoid"]


def test_travel_map_country_avoid_clears_its_region_avoid_tiles(client):
    payload = {"avoid": ["CA"] + list(CA[:3]), "visited": []}
    r = client.post("/api/travel-map", json=payload)
    j = r.json()
    assert j["avoid"] == ["CA"]
    assert j["avoid_tiles"] == []


def test_travel_map_unmark_below_threshold_round_trip(client):
    client.post("/api/travel-map", json={"visited": [], "avoid": list(CA[:11])})
    r1 = client.post("/api/travel-map", json={"avoid": list(CA[:10])})
    j1 = r1.json()
    assert "CA" not in j1["effective_avoid"]
    assert set(j1["avoid_tiles"]) == set(CA[:10])
