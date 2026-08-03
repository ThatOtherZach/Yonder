"""Tiled world map + km²-unlocked XP.

Covers:
1. Area totals — subdivision credit, single-tile countries, the documented
   country-level "some coverage" partial-credit rule, no double counting.
2. Migration — legacy visited-country lists read back as country tiles.
3. Tier recalibration — km² thresholds keep the rank names and land
   existing travellers in sensible tiers.
4. Partial-coverage behaviour of the getaway visited filter.
"""
from __future__ import annotations

from datetime import date

import pytest

from yonder import tiles as T
from yonder.adventure import AdventureRequest, StopoverIdea, filter_ideas
from yonder.xp import RANKS, compute_xp

# ---------------------------------------------------------------------------
# 1. Tile registry + area totals
# ---------------------------------------------------------------------------


def test_whitelist_countries_are_subdivided():
    assert set(T.SUBDIVIDED_COUNTRIES) == {"US", "CA", "GB"}
    assert len(T.SUBDIVIDED_COUNTRIES["US"]) == 10  # 8 continental regions + AK + HI
    assert len(T.SUBDIVIDED_COUNTRIES["CA"]) == 7
    assert set(T.SUBDIVIDED_COUNTRIES["GB"]) == {
        "GB-ENG", "GB-SCT", "GB-WLS", "GB-NIR",
    }


def test_subdivision_areas_are_plausible():
    # Rough sanity on land areas (km², approximate geometry-derived figures)
    assert 800_000 < T.tile_area("US-TEX") < 1_000_000  # TX + OK merged region
    assert 900_000 < T.tile_area("CA-ON") < 1_200_000
    assert T.tile_area("GB-ENG") == 130_279
    assert T.tile_area("US-NEC") > 400_000  # 12-state Northeast region
    # Country totals ballpark
    assert 9_000_000 < T.country_total_area("US") < 10_500_000
    assert 9_000_000 < T.country_total_area("CA") < 10_500_000


def test_single_tile_country_area_comes_from_country_size():
    from yonder.country_size import COUNTRY_SIZE

    assert T.tile_area("FR") == COUNTRY_SIZE["FR"][0]
    assert T.tile_area("JP") == COUNTRY_SIZE["JP"][0]


def test_country_level_entry_gets_partial_credit():
    # Documented rule: plain "CA" credits ONE average region, not all of Canada
    mean = round(T.country_total_area("CA") / len(T.SUBDIVIDED_COUNTRIES["CA"]))
    assert T.tile_area("CA") == mean
    assert T.tile_area("CA") < T.country_total_area("CA") / 2


def test_unlocked_km2_sums_and_never_double_counts():
    on = T.tile_area("CA-ON")
    qc = T.tile_area("CA-QC")
    fr = T.tile_area("FR")
    assert T.unlocked_km2(["CA-ON", "CA-QC", "FR"]) == on + qc + fr
    # Duplicate entries count once
    assert T.unlocked_km2(["FR", "FR", "CA-ON", "CA-ON"]) == fr + on
    # Country entry + subdivisions: at least the partial-credit floor,
    # never mean + subdivisions stacked once subs exceed the floor
    both = T.unlocked_km2(["CA", "CA-ON"])
    assert both == max(on, T.tile_area("CA"))


def test_full_subdivision_sweep_credits_full_country():
    all_ca = list(T.SUBDIVIDED_COUNTRIES["CA"])
    assert T.unlocked_km2(all_ca) == T.country_total_area("CA")


def test_normalize_tile_list_validates_and_keeps_stamp_order():
    # us-tx is an alias → normalises to US-TEX
    got = T.normalize_tile_list("ca-on, us-tx, ZZ, GB-ENG, fr, US-XX, CA-ON")
    assert got == ["CA-ON", "US-TEX", "GB-ENG", "FR"]


# ---------------------------------------------------------------------------
# 2. Migration of legacy visited-country lists
# ---------------------------------------------------------------------------


def test_settings_fall_back_to_legacy_country_list():
    from yonder.config import Settings

    s = Settings()
    s.visited_countries = "CA,FR,JP"  # legacy data, no tiles stored
    s.visited_tiles = ""
    assert s.visited_tile_list() == ["CA", "FR", "JP"]
    # Legacy whitelist country = partial coverage → not fully visited
    assert s.fully_visited_country_list() == {"FR", "JP"}


def test_settings_prefer_stored_tiles():
    from yonder.config import Settings

    s = Settings()
    s.visited_countries = "CA"
    s.visited_tiles = "CA-ON,CA-QC"
    assert s.visited_tile_list() == ["CA-ON", "CA-QC"]


def test_visited_countries_from_tiles_keeps_stamp_order():
    assert T.visited_countries_from_tiles(["CA-ON", "FR", "CA-QC", "US-NY"]) == [
        "CA", "FR", "US",
    ]


# ---------------------------------------------------------------------------
# 3. Tier recalibration
# ---------------------------------------------------------------------------


def test_rank_names_preserved():
    assert [r[1] for r in RANKS] == [
        "Armchair Explorer", "Day Tripper", "Weekend Wanderer",
        "Seasoned Traveller", "Globe-Trotter", "Nomadic Soul",
        "Expedition Regular", "Chaos Pilgrim", "Chaos Pilot",
    ]


def test_zero_tiles_is_armchair():
    p = compute_xp([], [])
    assert p["xp"] == 0 and p["rank"] == "Armchair Explorer"


def test_first_stamp_leaves_armchair():
    # Even a tiny country beats the 100 km² first-stamp threshold
    p = compute_xp(["MT"], [])
    assert p["rank"] != "Armchair Explorer"


def test_typical_traveller_lands_in_sensible_tier():
    # Old ladder: 5-9 countries → Weekend Wanderer. A 5-country European
    # passport should land at Weekend Wanderer or ABOVE (never regress).
    p = compute_xp(["FR", "ES", "IT", "DE", "PT"], [])
    names = [r[1] for r in RANKS]
    assert names.index(p["rank"]) >= names.index("Weekend Wanderer")


def test_avoid_list_no_longer_subtracts_xp():
    with_avoid = compute_xp(["FR"], ["RU", "US"])
    without = compute_xp(["FR"], [])
    assert with_avoid["xp"] == without["xp"]
    assert with_avoid["avoid_count"] == 2


def test_profile_exposes_km2_fields_and_ladder():
    p = compute_xp(["CA-ON"], [])
    assert p["km2"] == p["xp"] == T.tile_area("CA-ON")
    assert p["km2_label"]
    assert p["visited_count"] == 1  # one country with coverage
    assert p["tile_count"] == 1
    assert len(p["ladder"]) == len(RANKS)


# ---------------------------------------------------------------------------
# 4. Partial-coverage behaviour of the visited filter
# ---------------------------------------------------------------------------


def _getaway_req(**kw) -> AdventureRequest:
    base = dict(
        origin="YVR",
        destination="YVR",
        depart_date=date(2026, 9, 1),
        trip_kind="getaway",
        max_candidates=5,
    )
    base.update(kw)
    return AdventureRequest(**base)


def _idea(iata: str, cc: str) -> StopoverIdea:
    return StopoverIdea(iata=iata, city=iata, country=cc)


def test_partial_tile_coverage_keeps_country_eligible():
    req = _getaway_req(
        visited_countries=["CA"], visited_tiles=["CA-ON"]
    )
    out = filter_ideas([_idea("YUL", "CA"), _idea("NRT", "JP")], req)
    assert {i.iata for i in out} == {"YUL", "NRT"}


def test_fully_tiled_country_is_suppressed():
    all_ca = list(T.SUBDIVIDED_COUNTRIES["CA"])
    req = _getaway_req(visited_countries=["CA"], visited_tiles=all_ca)
    out = filter_ideas([_idea("YUL", "CA"), _idea("NRT", "JP")], req)
    assert {i.iata for i in out} == {"NRT"}


def test_non_subdivided_visited_country_still_suppressed():
    req = _getaway_req(visited_countries=["JP"], visited_tiles=["JP"])
    out = filter_ideas([_idea("NRT", "JP"), _idea("BKK", "TH")], req)
    assert {i.iata for i in out} == {"BKK"}


def test_legacy_country_list_treats_whitelist_as_partial():
    # No tiles at all (legacy request): "CA" is a country-level stamp →
    # some coverage → Canada stays eligible; Japan is fully visited.
    req = _getaway_req(visited_countries=["CA", "JP"])
    out = filter_ideas([_idea("YUL", "CA"), _idea("NRT", "JP")], req)
    assert {i.iata for i in out} == {"YUL"}


def test_fully_visited_countries_helper():
    assert T.fully_visited_countries(["FR", "CA-ON", "CA"]) == {"FR"}
    assert T.fully_visited_countries(list(T.SUBDIVIDED_COUNTRIES["GB"])) == {"GB"}


def test_unvisited_home_regions():
    regs = T.unvisited_home_regions("CA", ["CA-ON", "CA-QC"])
    codes = {c for c, _ in regs}
    assert "CA-ON" not in codes and "CA-QC" not in codes
    assert len(regs) == len(T.SUBDIVIDED_COUNTRIES["CA"]) - 2
    assert T.unvisited_home_regions("FR", ["FR"]) == []
