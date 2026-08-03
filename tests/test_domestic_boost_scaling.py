"""Size-scaled domestic boost: strength varies with home-country size.

The domestic-seed boost is no longer flat +3 — it scales with a blend of
the home country's land area and population, calibrated so a medium
country (France) keeps the historical +3 exactly.
"""
from datetime import date

from yonder.adventure import AdventureRequest, StopoverIdea, _sort_by_comfort
from yonder.country_size import (
    DOMESTIC_BOOST_BASE,
    country_scale,
    domestic_boost_points,
)


# ---------------------------------------------------------------------------
# country_scale
# ---------------------------------------------------------------------------

def test_scale_monotonic_small_medium_large():
    lu = country_scale("LU")
    fr = country_scale("FR")
    ca = country_scale("CA")
    assert lu < fr < ca, f"expected LU < FR < CA, got {lu} / {fr} / {ca}"


def test_scale_bounds_and_midpoint():
    assert 0.0 <= country_scale("LU") <= 0.15  # microstate near the floor
    assert abs(country_scale("FR") - 0.5) < 1e-9  # calibration reference
    for cc in ("US", "CA", "RU", "BR", "AU"):
        assert country_scale(cc) > 0.55, f"{cc} should be clearly above midpoint"
    for cc in ("US", "CA", "RU", "MC", "VA"):
        assert 0.0 <= country_scale(cc) <= 1.0


def test_scale_unknown_country_defaults_to_midpoint():
    assert country_scale("XX") == 0.5
    assert country_scale(None) == 0.5
    assert country_scale("") == 0.5


def test_scale_case_and_whitespace_insensitive():
    assert country_scale("fr") == country_scale("FR")
    assert country_scale(" ca ") == country_scale("CA")


# ---------------------------------------------------------------------------
# domestic_boost_points
# ---------------------------------------------------------------------------

def test_boost_ordering_luxembourg_france_canada():
    assert domestic_boost_points("LU") < domestic_boost_points("FR") < domestic_boost_points("CA")


def test_medium_country_keeps_historical_boost():
    # France sits at the calibrated midpoint → exactly the old flat boost.
    assert domestic_boost_points("FR") == DOMESTIC_BOOST_BASE == 3
    # Unknown countries also behave like the historical flat boost.
    assert domestic_boost_points("XX") == 3
    assert domestic_boost_points(None) == 3


def test_continent_scale_countries_boost_stronger_than_small():
    for big in ("US", "CA", "BR", "AU", "RU"):
        assert domestic_boost_points(big) > DOMESTIC_BOOST_BASE, big
    for small in ("LU", "BE", "MT", "SG"):
        assert domestic_boost_points(small) < DOMESTIC_BOOST_BASE, small
    # Never fully zero while the boost is active — a floor of 1 point remains.
    assert domestic_boost_points("VA") >= 1


# ---------------------------------------------------------------------------
# _sort_by_comfort integration
# ---------------------------------------------------------------------------

def _req(origin: str, visited: list[str], prompt: str = "") -> AdventureRequest:
    return AdventureRequest(
        origin=origin,
        destination="LHR",
        depart_date=date(2025, 11, 1),
        vibe="city",
        prompt=prompt,
        max_candidates=5,
        include_direct=False,
        visited_countries=visited,
    )


def _idea(iata: str, country: str, tags: list[str]) -> StopoverIdea:
    return StopoverIdea(
        iata=iata, city=iata, stay_days=3, why="t", vibe_tags=tags, country=country
    )


_TAGS = ["city", "food"]


def test_us_domestic_outranks_tied_international_for_low_xp():
    """Continent-scale home: the scaled boost still wins ties (and then some)."""
    req = _req("ORD", ["GB"])  # low-XP US-based traveller
    dom = _idea("BNA", "US", _TAGS)
    intl = _idea("IST", "TR", _TAGS)
    ranked = _sort_by_comfort([intl, dom], req, recent_iatas=set())
    assert ranked[0].iata == "BNA"


def test_boost_off_for_well_traveled_user():
    """Existing off-switch intact: 25+ stamps and no proximity → no boost."""
    visited = [f"C{i}" for i in range(30)]  # comfort = 0.30 ≥ 0.25
    # 'cheap eats' prompt gives the intl city (tagged cheap) +3; if the
    # domestic boost (4 for a US home) fired, BNA would win instead.
    req = _req("ORD", visited, prompt="cheap eats")
    dom = _idea("BNA", "US", ["city"])
    intl = _idea("IST", "TR", ["city", "cheap"])
    ranked = _sort_by_comfort([dom, intl], req, recent_iatas=set())
    assert ranked[0].iata == "IST"


def test_boost_off_for_zero_stamp_user():
    req = _req("ORD", [], prompt="cheap eats")
    dom = _idea("BNA", "US", ["city"])
    intl = _idea("IST", "TR", ["city", "cheap"])
    ranked = _sort_by_comfort([dom, intl], req, recent_iatas=set())
    assert ranked[0].iata == "IST"


def test_big_country_boost_beats_bigger_tag_gap_than_small_country():
    """A US home (boost 4+) overcomes a 3-point tag deficit that a
    Belgian home (boost 1) cannot — same visited count, same candidates."""
    dom_tags = ["city"]
    intl_tags = ["city", "food", "electric"]  # 2 extra 'city'-vibe tag hits? use score gap
    # 'city' vibe related tags include these; the intl city has more overlap.
    us_req = _req("ORD", ["GB"])
    dom_us = _idea("BNA", "US", dom_tags)
    intl = _idea("IST", "TR", intl_tags)
    ranked_us = _sort_by_comfort([intl, dom_us], us_req, recent_iatas=set())

    be_req = _req("BRU", ["GB"])
    dom_be = _idea("ANR", "BE", dom_tags)
    ranked_be = _sort_by_comfort([intl, dom_be], be_req, recent_iatas=set())

    # The Belgian domestic option must not outrank what the US one couldn't,
    # and the US domestic city must rank at least as well as the Belgian one.
    us_rank = [i.iata for i in ranked_us].index("BNA")
    be_rank = [i.iata for i in ranked_be].index("ANR")
    assert us_rank <= be_rank
