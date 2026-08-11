"""Passport map must accept ANY number of visited stamps (>16 lockout bug).

Regression coverage for the ">15 countries" stamp lockout: the server
round-trip must echo back every stamp for large mixed payloads (plain
country tiles + US/CA/GB subdivision tiles), preserve stamp order, and
collapse retired MX/BR/AU region codes to country tiles without losing
anything else.

The client-side half of the fix (stale save-echo guard in
yonder/static/country_map.js) is exercised implicitly: the echo applied on
repaint is exactly what these tests assert.
"""
from __future__ import annotations

import pytest

from yonder import tiles as T

US = T.SUBDIVIDED_COUNTRIES["US"]
CA = T.SUBDIVIDED_COUNTRIES["CA"]
GB = T.SUBDIVIDED_COUNTRIES["GB"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from yonder import user_prefs as UP

    monkeypatch.setattr(UP, "DB_PATH", tmp_path / "prefs.db")
    UP._invalidate()
    from fastapi.testclient import TestClient
    from yonder import web as web_module

    yield TestClient(web_module.app, raise_server_exceptions=True)
    UP._invalidate()


PLAIN = [
    "FR", "DE", "ES", "IT", "PT", "NL", "BE", "CH", "AT", "PL",
    "CZ", "SK", "HU", "RO", "BG", "GR", "HR", "SI", "SE", "NO",
    "FI", "DK", "IE", "JP", "KR",
]


def test_twenty_plus_plain_countries_round_trip_intact(client):
    # One stamp at a time, like the map's autosave: nothing ever drops.
    for i in range(1, len(PLAIN) + 1):
        r = client.post("/api/travel-map", json={"visited": PLAIN[:i], "avoid": []})
        assert r.status_code == 200
        j = r.json()
        assert j["tiles"] == PLAIN[:i], f"stamp #{i} lost"
        assert j["visited"] == PLAIN[:i]
        assert j["xp"]["visited_count"] == i


def test_large_mixed_country_and_region_payload_round_trip(client):
    tiles = PLAIN[:18] + [US[0], US[1], CA[0], GB[0]]  # 22 entries
    r = client.post("/api/travel-map", json={"visited": tiles, "avoid": ["RU"]})
    j = r.json()
    assert j["ok"] is True
    assert j["tiles"] == tiles  # stamp order preserved, nothing dropped
    assert j["visited"] == PLAIN[:18] + ["US", "CA", "GB"]
    # Echo → save again (client repaint round-trip) is a fixed point
    r2 = client.post(
        "/api/travel-map",
        json={"visited": j["tiles"], "avoid": j["avoid"] + j["avoid_tiles"]},
    )
    j2 = r2.json()
    assert j2["tiles"] == j["tiles"]
    assert j2["avoid"] == j["avoid"] and j2["avoid_tiles"] == j["avoid_tiles"]


def test_retired_region_codes_collapse_without_losing_other_stamps(client):
    tiles = PLAIN[:16] + ["MX-JAL", "BR-SP", "AU-NSW", "CA-NB", "US-CA"]
    r = client.post("/api/travel-map", json={"visited": tiles, "avoid": []})
    j = r.json()
    # Retired MX/BR/AU regions → country tiles; merged-alias codes remap.
    assert j["tiles"] == PLAIN[:16] + ["MX", "BR", "AU", "CA-ATL", "US-PAC"]
    assert j["visited"][-5:] == ["MX", "BR", "AU", "CA", "US"]
    assert len(j["visited"]) == 21


def test_avoid_tiles_survive_alongside_large_visited_list(client):
    r = client.post(
        "/api/travel-map",
        json={"visited": PLAIN[:20], "avoid": ["RU", US[9]]},
    )
    j = r.json()
    assert j["tiles"] == PLAIN[:20]
    assert j["avoid"] == ["RU"]
    assert j["avoid_tiles"] == [US[9]]
