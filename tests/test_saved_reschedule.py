from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
from yonder.config import Settings
from yonder.adventure import AdventureItinerary
from yonder.saved import (
    bookmark_quest,
    ensure_global_quest,
    get,
    list_bookmarked_quests,
    save_itinerary,
    shift_itinerary_dates,
)
from yonder.types import FlightOffer


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(web_module, "reload_settings", lambda: Settings(testing=True))


@pytest.fixture
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _detour(start: date, *, title: str = "Old route"):
    return {
        "kind": "stopover",
        "title": title,
        "currency": "CAD",
        "legs": [
            {"from_iata": "YVR", "to_iata": "LHR", "depart_date": start.isoformat()},
            {
                "from_iata": "LHR",
                "to_iata": "YVR",
                "depart_date": (start + timedelta(days=9)).isoformat(),
            },
        ],
    }


def _quest(start: date):
    return {
        "kind": "quest",
        "title": "Bangkok overland to Saigon",
        "entry_iata": "BKK",
        "exit_iata": "SGN",
        "entry_city": "Bangkok",
        "exit_city": "Saigon",
        "depart_date": start.isoformat(),
        "outbound_date": (start + timedelta(days=14)).isoformat(),
        "inbound_leg": {
            "from_iata": "YVR",
            "to_iata": "BKK",
            "depart_date": start.isoformat(),
        },
        "outbound_leg": {
            "from_iata": "SGN",
            "to_iata": "YVR",
            "depart_date": (start + timedelta(days=14)).isoformat(),
        },
    }


def test_shift_dates_preserves_escape_detour_and_quest_spacing():
    old = date(2024, 1, 10)
    new = date(2027, 5, 20)
    detour = shift_itinerary_dates(_detour(old), new)
    assert detour["legs"][0]["depart_date"] == new.isoformat()
    assert detour["legs"][1]["depart_date"] == (new + timedelta(days=9)).isoformat()

    escape = shift_itinerary_dates(
        {
            "kind": "escape",
            "title": "YVR to LAX",
            "legs": [{"from_iata": "YVR", "to_iata": "LAX", "depart_date": old.isoformat()}],
        },
        new,
    )
    assert escape["legs"][0]["depart_date"] == new.isoformat()

    quest = shift_itinerary_dates(_quest(old), new)
    assert quest["depart_date"] == new.isoformat()
    assert quest["outbound_date"] == (new + timedelta(days=14)).isoformat()
    assert quest["outbound_leg"]["depart_date"] == (new + timedelta(days=14)).isoformat()


def test_shift_clears_old_date_fares_and_booking_links():
    old = date(2024, 1, 10)
    itinerary = _detour(old)
    itinerary["total_price"] = 999
    itinerary["display_price"] = "C$999"
    itinerary["google_flights_url"] = "https://example.com/old-date"
    itinerary["legs"][0]["offer"] = {"price": 999, "currency": "CAD"}
    itinerary["legs"][0]["booking_url"] = "https://example.com/old-ticket"

    shifted = shift_itinerary_dates(itinerary, date(2027, 1, 10))

    assert shifted["total_price"] is None
    assert shifted["display_price"] is None
    assert shifted["google_flights_url"] is None
    assert shifted["legs"][0]["offer"] is None
    assert shifted["legs"][0]["booking_url"] is None

    legacy = shift_itinerary_dates(
        {
            "kind": "escape",
            "title": "Legacy round trip",
            "query": {
                "depart_date": old.isoformat(),
                "return_date": (old + timedelta(days=6)).isoformat(),
            },
            "legs": [
                {
                    "from_iata": "YVR",
                    "to_iata": "LAX",
                    "depart_date": old.isoformat(),
                }
            ],
        },
        date(2027, 1, 10),
    )
    assert legacy["query"]["return_date"] == date(2027, 1, 16).isoformat()


def test_expired_and_non_expired_saved_rendering(client):
    client.cookies.set("yv_sess", "render-owner")
    expired = save_itinerary(
        _detour(date.today() - timedelta(days=30), title="Expired route"),
        owner_sess="render-owner",
    )
    save_itinerary(
        _detour(date.today() + timedelta(days=30), title="Future route"),
        owner_sess="render-owner",
    )

    html = client.get("/saved").text
    assert "Dates passed" in html
    assert f'action="/saved/{expired.id}/reschedule"' in html
    assert 'min="' + date.today().isoformat() + '"' in html
    assert "Refresh fares" in html


def test_private_reschedule_persists_dates_when_provider_fails(client, monkeypatch):
    owner = "private-owner"
    client.cookies.set("yv_sess", owner)
    old_trip = _detour(date.today() - timedelta(days=20))
    old_trip["total_price"] = 987
    old_trip["display_price"] = "OLDFARE987"
    old_trip["all_in_display"] = "OLDALLIN987"
    old_trip["google_flights_url"] = "https://example.com/old-date"
    saved = save_itinerary(old_trip, owner_sess=owner)
    future = date.today() + timedelta(days=20)

    seen_dates = []

    async def fail(itinerary, *args, **kwargs):
        seen_dates.extend(leg.depart_date for leg in itinerary.legs)
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(web_module, "reprice_itinerary", fail)
    response = client.post(
        f"/saved/{saved.id}/reschedule",
        data={"new_departure_date": future.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    reloaded = get(saved.id)
    assert reloaded is not None
    assert reloaded.id == saved.id
    assert reloaded.itinerary["legs"][0]["depart_date"] == future.isoformat()
    assert reloaded.itinerary["legs"][1]["depart_date"] == (
        future + timedelta(days=9)
    ).isoformat()
    assert seen_dates == [future, future + timedelta(days=9)]
    html = client.get("/saved").text
    assert "OLDFARE987" not in html
    assert "OLDALLIN987" not in html
    assert "https://example.com/old-date" not in html


def test_rate_limit_keeps_new_dates_without_old_fare(client, monkeypatch):
    owner = "limited-owner"
    client.cookies.set("yv_sess", owner)
    old_trip = _detour(date.today() - timedelta(days=20))
    old_trip["total_price"] = 654
    old_trip["display_price"] = "OLDFARE654"
    old_trip["all_in_display"] = "OLDALLIN654"
    saved = save_itinerary(old_trip, owner_sess=owner)
    future = date.today() + timedelta(days=20)

    async def denied(*args, **kwargs):
        return SimpleNamespace(allowed=False)

    monkeypatch.setattr(web_module._rate_limit, "check_fare", denied)
    response = client.post(
        f"/saved/{saved.id}/reschedule",
        data={"new_departure_date": future.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert get(saved.id).itinerary["legs"][0]["depart_date"] == future.isoformat()
    html = client.get("/saved").text
    assert "OLDFARE654" not in html
    assert "OLDALLIN654" not in html


def test_partial_detour_reprice_does_not_publish_one_leg_as_total(
    client, monkeypatch
):
    owner = "partial-owner"
    client.cookies.set("yv_sess", owner)
    saved = save_itinerary(
        _detour(date.today() - timedelta(days=20)), owner_sess=owner
    )

    async def partial(itinerary, *args, **kwargs):
        live_offer = FlightOffer(provider="test", price=222, currency="CAD")
        legs = [
            itinerary.legs[0].model_copy(update={"offer": live_offer}),
            itinerary.legs[1],
        ]
        return (
            itinerary.model_copy(
                update={
                    "legs": legs,
                    "total_price": 222,
                    "display_price": "PARTIAL222",
                    "all_in_display": "PARTIALALLIN222",
                }
            ),
            {"status": "mixed"},
        )

    monkeypatch.setattr(web_module, "reprice_itinerary", partial)
    response = client.post(
        f"/saved/{saved.id}/reschedule",
        data={
            "new_departure_date": (
                date.today() + timedelta(days=20)
            ).isoformat()
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    reloaded = get(saved.id)
    assert reloaded.total_price is None
    assert reloaded.itinerary["total_price"] is None
    html = client.get("/saved").text
    assert "PARTIAL222" not in html
    assert "PARTIALALLIN222" not in html


def test_legacy_escape_round_trip_survives_successful_reschedule(
    client, monkeypatch
):
    owner = "legacy-escape-owner"
    client.cookies.set("yv_sess", owner)
    old = date.today() - timedelta(days=30)
    legacy = {
        "kind": "escape",
        "title": "Legacy Escape round trip",
        "currency": "CAD",
        "query": {
            "origin": "YVR",
            "destination": "LAX",
            "depart_date": old.isoformat(),
            "return_date": (old + timedelta(days=6)).isoformat(),
        },
        "legs": [
            {
                "from_iata": "YVR",
                "to_iata": "LAX",
                "depart_date": old.isoformat(),
            }
        ],
    }
    saved = save_itinerary(legacy, owner_sess=owner)
    seen = []

    async def priced(itinerary, *args, **kwargs):
        seen.extend(
            (leg.from_iata, leg.to_iata, leg.depart_date) for leg in itinerary.legs
        )
        offers = [
            FlightOffer(provider="test", price=100, currency="CAD"),
            FlightOffer(provider="test", price=120, currency="CAD"),
        ]
        return (
            itinerary.model_copy(
                update={
                    "legs": [
                        leg.model_copy(update={"offer": offer})
                        for leg, offer in zip(itinerary.legs, offers)
                    ],
                    "total_price": 220,
                    "display_price": "C$220",
                }
            ),
            {"status": "live"},
        )

    monkeypatch.setattr(web_module, "reprice_itinerary", priced)
    new = date.today() + timedelta(days=30)
    response = client.post(
        f"/saved/{saved.id}/reschedule",
        data={"new_departure_date": new.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert seen == [
        ("YVR", "LAX", new),
        ("LAX", "YVR", new + timedelta(days=6)),
    ]
    reloaded = get(saved.id)
    assert reloaded.itinerary["query"]["depart_date"] == new.isoformat()
    assert reloaded.itinerary["query"]["return_date"] == (
        new + timedelta(days=6)
    ).isoformat()
    assert len(reloaded.itinerary["legs"]) == 2
    assert reloaded.total_price == 220


def test_legacy_escape_round_trip_survives_provider_failure(client, monkeypatch):
    owner = "legacy-failure-owner"
    client.cookies.set("yv_sess", owner)
    old = date.today() - timedelta(days=30)
    saved = save_itinerary(
        {
            "kind": "escape",
            "title": "Legacy Escape failure",
            "currency": "CAD",
            "query": {
                "origin": "YVR",
                "destination": "LAX",
                "depart_date": old.isoformat(),
                "return_date": (old + timedelta(days=5)).isoformat(),
            },
            "legs": [
                {
                    "from_iata": "YVR",
                    "to_iata": "LAX",
                    "depart_date": old.isoformat(),
                }
            ],
        },
        owner_sess=owner,
    )

    async def fail(*args, **kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(web_module, "reprice_itinerary", fail)
    new = date.today() + timedelta(days=25)
    response = client.post(
        f"/saved/{saved.id}/reschedule",
        data={"new_departure_date": new.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    reloaded = get(saved.id)
    assert reloaded.itinerary["query"]["depart_date"] == new.isoformat()
    assert reloaded.itinerary["query"]["return_date"] == (
        new + timedelta(days=5)
    ).isoformat()
    assert [
        (leg["from_iata"], leg["to_iata"], leg["depart_date"])
        for leg in reloaded.itinerary["legs"]
    ] == [
        ("YVR", "LAX", new.isoformat()),
        ("LAX", "YVR", (new + timedelta(days=5)).isoformat()),
    ]


def test_invalid_or_past_date_does_not_mutate(client):
    owner = "validation-owner"
    client.cookies.set("yv_sess", owner)
    saved = save_itinerary(_detour(date(2024, 1, 1)), owner_sess=owner)
    before = saved.itinerary
    for replacement in ("not-a-date", (date.today() - timedelta(days=1)).isoformat()):
        response = client.post(
            f"/saved/{saved.id}/reschedule",
            data={"new_departure_date": replacement},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "err=" in response.headers["location"]
        assert get(saved.id).itinerary == before


def test_non_expired_and_wrong_owner_posts_do_not_mutate(client):
    owner = "boundary-owner"
    future_trip = save_itinerary(
        _detour(date.today() + timedelta(days=10)), owner_sess=owner
    )
    before = future_trip.itinerary
    client.cookies.set("yv_sess", owner)
    response = client.post(
        f"/saved/{future_trip.id}/reschedule",
        data={"new_departure_date": (date.today() + timedelta(days=50)).isoformat()},
        follow_redirects=False,
    )
    assert "err=" in response.headers["location"]
    assert get(future_trip.id).itinerary == before

    expired = save_itinerary(
        _detour(date.today() - timedelta(days=30)), owner_sess=owner
    )
    expired_before = expired.itinerary
    client.cookies.set("yv_sess", "stranger")
    response = client.post(
        f"/saved/{expired.id}/reschedule",
        data={"new_departure_date": (date.today() + timedelta(days=50)).isoformat()},
        follow_redirects=False,
    )
    assert "err=" in response.headers["location"]
    assert get(expired.id).itinerary == expired_before


def test_quest_override_is_isolated_and_legacy_bookmark_uses_canonical(
    client, monkeypatch
):
    canonical_start = date(2024, 2, 1)
    quest = ensure_global_quest(
        _quest(canonical_start),
        trip_meta={"origin": "YVR"},
        origin="YVR",
    )
    assert bookmark_quest(quest.id, owner_sess="quest-a")
    assert bookmark_quest(quest.id, owner_sess="quest-b")
    future = date.today() + timedelta(days=40)

    async def fail(*args, **kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(web_module, "reprice_itinerary", fail)
    client.cookies.set("yv_sess", "quest-a")
    response = client.post(
        f"/saved/{quest.id}/reschedule",
        data={"new_departure_date": future.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302

    a = list_bookmarked_quests(owner_sess="quest-a")[0]
    b = list_bookmarked_quests(owner_sess="quest-b")[0]
    canonical = get(quest.id)
    assert a.itinerary["depart_date"] == future.isoformat()
    assert b.itinerary["depart_date"] == canonical_start.isoformat()
    assert canonical.itinerary["depart_date"] == canonical_start.isoformat()
    assert a.id == b.id == canonical.id


def test_successful_quest_reschedule_restores_combined_fare(client, monkeypatch):
    start = date(2024, 2, 1)
    quest = ensure_global_quest(
        _quest(start), trip_meta={"origin": "YVR"}, origin="YVR"
    )
    owner = "priced-quest-owner"
    assert bookmark_quest(quest.id, owner_sess=owner)
    client.cookies.set("yv_sess", owner)

    async def priced(itinerary, *args, **kwargs):
        offers = [
            FlightOffer(provider="test", price=125, currency="CAD"),
            FlightOffer(provider="test", price=175, currency="CAD"),
        ]
        legs = [
            leg.model_copy(update={"offer": offer})
            for leg, offer in zip(itinerary.legs, offers)
        ]
        return (
            AdventureItinerary(
                kind="quest",
                title=itinerary.title,
                currency="CAD",
                total_price=300,
                legs=legs,
            ),
            {"status": "live"},
        )

    monkeypatch.setattr(web_module, "reprice_itinerary", priced)
    future = date.today() + timedelta(days=45)
    response = client.post(
        f"/saved/{quest.id}/reschedule",
        data={"new_departure_date": future.isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    override = list_bookmarked_quests(owner_sess=owner)[0]
    assert override.itinerary["total_price"] == 300
    assert "300" in override.itinerary["display_total"]
    assert "300" in client.get("/saved").text