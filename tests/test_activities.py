"""Partner activity links (GetYourGuide / Viator) woven into field notes."""

from __future__ import annotations

import asyncio
import random

import pytest

from yonder import activities
from yonder.activities import (
    activity_links_for,
    links_for,
    pick_activity_links,
    resolve_pill_titles,
)


def test_loader_parses_rows_by_city_and_iata():
    by_city = links_for(city="Bangkok")
    by_iata = links_for(iata="bkk")
    assert by_city and by_city == by_iata
    providers = {r["provider"] for r in by_city}
    assert providers == {"getyourguide", "viator"}
    for r in by_city:
        assert r["url"].startswith("https://")
        assert r["title"]
        assert r["iata"] == "BKK"


def test_sibling_airports_of_a_metro_match_by_city_name():
    # CSV lists London under LHR / Tokyo under NRT / Paris under CDG — other
    # airports of the same metro must serve the same rows.
    anchor = links_for(iata="LHR")
    assert anchor
    assert links_for(iata="LGW") == anchor
    assert links_for(iata="STN") == anchor
    assert links_for(iata="HND") == links_for(iata="NRT") != []
    assert links_for(iata="ORY") == links_for(iata="CDG") != []
    assert links_for(iata="JFK") == links_for(iata="LGA") != []


def test_namesake_city_in_another_country_never_matches():
    # London, Ontario (YXU) must not borrow London UK's links.
    assert links_for(iata="YXU") == []
    # Sydney, Nova Scotia (YQY) must not borrow Sydney AU's links.
    assert links_for(iata="YQY") == []


def test_unmatched_city_returns_nothing():
    assert links_for(city="Nowheresville", iata="XXX") == []
    assert pick_activity_links(city="Nowheresville", iata="XXX", vibe="foodie") == []


def test_pick_returns_one_link_per_provider():
    picks = pick_activity_links(iata="AMS", vibe=None, rng=random.Random(7))
    assert len(picks) == 2
    assert {p["provider"] for p in picks} == {"getyourguide", "viator"}


def test_vibe_preference_wins_when_provider_has_match():
    # Bangkok Viator rows include exactly one foodie row → always chosen.
    for seed in range(12):
        picks = pick_activity_links(iata="BKK", vibe="foodie", rng=random.Random(seed))
        viator = next(p for p in picks if p["provider"] == "viator")
        assert viator["vibe"] == "foodie"


def test_app_vibe_alias_maps_to_grokvibe():
    # App vibe "food" → GROKVIBE "foodie"; "party" → "nightlife".
    for seed in range(12):
        picks = pick_activity_links(iata="BKK", vibe="food", rng=random.Random(seed))
        viator = next(p for p in picks if p["provider"] == "viator")
        assert viator["vibe"] == "foodie"
    for seed in range(12):
        picks = pick_activity_links(iata="AMS", vibe="party", rng=random.Random(seed))
        viator = next(p for p in picks if p["provider"] == "viator")
        assert viator["vibe"] == "nightlife"


def test_unknown_vibe_falls_back_to_random_within_provider():
    seen = set()
    for seed in range(30):
        picks = pick_activity_links(iata="AMS", vibe="dissociate", rng=random.Random(seed))
        assert {p["provider"] for p in picks} == {"getyourguide", "viator"}
        seen.update(p["url"] for p in picks)
    assert len(seen) > 2  # actually random, not pinned to one row


def test_picks_are_copies_not_cache_rows():
    a = pick_activity_links(iata="AMS", rng=random.Random(1))
    a[0]["title"] = "mutated"
    fresh = links_for(iata="AMS")
    assert all(r["title"] != "mutated" for r in fresh)


def test_resolve_titles_without_ai_keeps_csv_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "TITLE_DB_PATH", tmp_path / "titles.db")
    links = pick_activity_links(iata="BKK", vibe="foodie", rng=random.Random(3))
    before = [dict(l) for l in links]
    out = asyncio.run(resolve_pill_titles(links, None))
    assert [l["title"] for l in out] == [l["title"] for l in before]
    assert [l["url"] for l in out] == [l["url"] for l in before]


def test_cached_ai_title_is_served(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "TITLE_DB_PATH", tmp_path / "titles.db")
    links = pick_activity_links(iata="BKK", vibe="foodie", rng=random.Random(3))
    url = links[0]["url"]
    activities.put_cached_title(url, None, "Tuk-tuk feast after dark")
    out = asyncio.run(resolve_pill_titles([dict(l) for l in links], None))
    got = next(l for l in out if l["url"] == url)
    assert got["title"] == "Tuk-tuk feast after dark"


def test_activity_links_for_unmatched_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "TITLE_DB_PATH", tmp_path / "titles.db")
    assert asyncio.run(activity_links_for(None, city="Nowhere", iata="XXX")) == []


def test_loader_skips_urlless_rows(tmp_path, monkeypatch):
    csv_path = tmp_path / "activities.csv"
    csv_path.write_text(
        "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"
        "Testville,TST,,foodie,X,No Url Row\n"
        "Testville,TST,not-a-url,foodie,X,Bad Url Row\n"
        "Testville,TST,https://example.com/other,foodie,X,Unknown Partner\n"
        "Testville,TST,https://www.viator.com/tours/x?pid=1,foodie,X,Good Row\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(activities, "CSV_PATH", csv_path)
    monkeypatch.setitem(activities._cache, "mtime", None)
    try:
        rows = links_for(iata="TST")
        assert len(rows) == 1
        assert rows[0]["title"] == "Good Row"
        assert rows[0]["provider"] == "viator"
        picks = pick_activity_links(iata="TST", vibe="foodie")
        assert len(picks) == 1  # only Viator available — never an empty pill
    finally:
        monkeypatch.setitem(activities._cache, "mtime", None)


def test_hot_reload_on_mtime_change(tmp_path, monkeypatch):
    csv_path = tmp_path / "activities.csv"
    header = "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"
    csv_path.write_text(
        header + "Testville,TST,https://www.viator.com/tours/a,foodie,X,First\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(activities, "CSV_PATH", csv_path)
    monkeypatch.setitem(activities._cache, "mtime", None)
    try:
        assert links_for(iata="TST")[0]["title"] == "First"
        csv_path.write_text(
            header + "Testville,TST,https://www.viator.com/tours/b,foodie,X,Second\n",
            encoding="utf-8",
        )
        import os

        os.utime(csv_path, (1e9, 1e9))  # force a distinct mtime
        assert links_for(iata="TST")[0]["title"] == "Second"
    finally:
        monkeypatch.setitem(activities._cache, "mtime", None)


@pytest.mark.parametrize(
    ("app_vibe", "grok"),
    [
        ("adventure", "adventure"),
        ("history", "history"),
        ("food", "foodie"),
        ("street", "foodie"),
        ("party", "nightlife"),
        ("neon", "nightlife"),
        ("jungle", "nature"),
        ("art", "culture"),
        ("city", "explorer"),
        ("luxury", "experience"),
        ("whimsical", "vibe"),
        ("", None),
        ("meltdown", None),
    ],
)
def test_grok_vibe_mapping(app_vibe, grok):
    assert activities._grok_vibe_for(app_vibe) == grok
