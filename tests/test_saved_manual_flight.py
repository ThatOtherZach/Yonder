from __future__ import annotations

import socket
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
from yonder.saved import count_quests, list_saved
from yonder.types import FlightOffer, UnifiedSearchResult


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _payload(
    origin: str,
    destination: str,
    departure_date: str,
    *,
    travelers: int = 1,
    cabin: str = "economy",
    vibe: str = "adventure",
) -> dict:
    return {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "travelers": travelers,
        "cabin": cabin,
        "vibe": vibe,
    }


def test_saved_page_renders_manual_form_when_empty(client):
    response = client.get("/saved")

    assert response.status_code == 200
    assert "Add Flight" in response.text
    assert 'action="/saved/manual-flight"' in response.text
    assert 'name="departure_time"' not in response.text
    assert 'name="vibe"' in response.text
    assert 'data-vibe-slider' in response.text
    assert 'data-input-id="manual-flight-vibe"' in response.text
    assert 'data-submit-type="button"' in response.text
    assert 'data-apply-page-theme="0"' in response.text
    assert '<button class="btn manual-flight-submit" type="submit">Save</button>' in response.text
    assert 'vibe: slot.getAttribute("data-vibe") || ""' in response.text
    assert "Nothing in the jacket pocket" in response.text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"origin": "YY"}, "three-letter airport code"),
        ({"destination": "YVR"}, "must be different"),
        ({"departure_date": "2020-01-01"}, "future departure date"),
        ({"departure_date": "not-a-date"}, "valid future departure date"),
        ({"travelers": 10}, "number from 1 to 9"),
        ({"cabin": "deckchair"}, "valid cabin"),
        ({"vibe": "not-a-vibe"}, "Choose one vibe"),
    ],
)
def test_manual_flight_validation(client, changes, message):
    payload = _payload("YVR", "LIS", "2099-10-01")
    payload.update(changes)

    response = client.post("/api/saved/manual-flight", json=payload)

    assert response.status_code == 400
    assert message in response.json()["error"]


def test_manual_flight_saves_live_fare_and_metadata(client, monkeypatch):
    owner = "manual-live-owner"
    client.cookies.set("yv_sess", owner)

    async def fake_search(query, **kwargs):
        offer = FlightOffer(
            provider="duffel",
            price=412.0,
            currency="USD",
            booking_url="https://book.test/yvr-lis",
            google_flights_url="https://flights.test/yvr-lis",
        )
        return UnifiedSearchResult(query=query, results=[], offers=[offer])

    monkeypatch.setattr(web_module, "search_flights", fake_search)
    response = client.post(
        "/api/saved/manual-flight",
        json=_payload(
            "YVR",
            "LIS",
            "2099-10-01",
            travelers=2,
            cabin="business",
            vibe="food",
        ),
    )

    assert response.status_code == 200, response.text
    saved = list_saved(owner_sess=owner)
    assert len(saved) == 1
    ticket = saved[0]
    assert ticket.kind == "escape"
    assert ticket.total_price == 412.0
    assert ticket.adults == 2
    assert ticket.cabin == "business"
    assert ticket.vibe == "food"
    assert ticket.trip_meta["manual_flight"] is True
    assert "manual_departure_local" not in ticket.trip_meta
    assert ticket.trip_meta["vibe_airport"] == "LIS"
    assert len(ticket.itinerary["legs"]) == 1


def test_manual_flight_fare_missing_is_saved_with_check_fares(client, monkeypatch):
    owner = "manual-fallback-owner"
    client.cookies.set("yv_sess", owner)

    async def failed_search(query, **kwargs):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(web_module, "search_flights", failed_search)
    response = client.post(
        "/api/saved/manual-flight",
        json=_payload(
            "YVR",
            "LIS",
            "2099-10-01",
            cabin="business",
            vibe="culture",
        ),
    )

    assert response.status_code == 200
    assert response.json()["fare_missing"] is True
    ticket = list_saved(owner_sess=owner)[0]
    assert ticket.total_price is None
    assert ticket.itinerary["legs"][0]["offer"]["fare_missing"] is True
    page = client.get("/saved")
    assert "Check Fares" in page.text
    manual_card = re.search(
        r'<article class="boarding-pass is-adventure ticket--detour".*?</article>',
        page.text,
        re.DOTALL,
    )
    assert manual_card is not None
    assert "field-note-slot" not in manual_card.group(0)
    assert 'data-cf-cabin="business"' in manual_card.group(0)
    check_fares_js = (
        Path(__file__).parents[1] / "yonder" / "static" / "check_fares.js"
    ).read_text()
    assert 'cabin: d.cfCabin || "economy"' in check_fares_js


def test_manual_flight_is_idempotent_and_private_per_browser(client, monkeypatch):
    calls = 0

    async def fake_search(query, **kwargs):
        nonlocal calls
        calls += 1
        return UnifiedSearchResult(
            query=query,
            results=[],
            offers=[
                FlightOffer(
                    provider="amadeus",
                    price=200.0,
                    currency="USD",
                    booking_url="https://book.test/flight",
                )
            ],
        )

    monkeypatch.setattr(web_module, "search_flights", fake_search)
    payload = _payload("YVR", "LIS", "2099-10-01", vibe="city")

    client.cookies.set("yv_sess", "manual-owner-a")
    first = client.post("/api/saved/manual-flight", json=payload)
    second = client.post("/api/saved/manual-flight", json=payload)
    client.cookies.set("yv_sess", "manual-owner-b")
    other = client.post("/api/saved/manual-flight", json=payload)

    assert first.json()["saved_id"] == second.json()["saved_id"]
    assert first.json()["saved_id"] != other.json()["saved_id"]
    assert len(list_saved(owner_sess="manual-owner-a")) == 1
    assert len(list_saved(owner_sess="manual-owner-b")) == 1
    assert calls == 2


def test_return_home_vibe_belongs_to_departure_airport(client, monkeypatch):
    owner = "manual-return-owner"
    client.cookies.set("yv_sess", owner)
    settings = web_module.reload_settings()
    monkeypatch.setattr(
        web_module,
        "_session_settings",
        lambda request: settings.model_copy(update={"home_iata": "YVR"}),
    )

    response = client.post(
        "/api/saved/manual-flight",
        json=_payload("OPO", "YVR", "2099-10-10", vibe="romance"),
    )

    assert response.status_code == 200
    assert list_saved(owner_sess=owner)[0].trip_meta["vibe_airport"] == "OPO"


def test_manual_flight_html_form_redirects_to_saved(client):
    client.cookies.set("yv_sess", "manual-form-owner")

    response = client.post(
        "/saved/manual-flight",
        data=_payload("YVR", "LIS", "2099-10-01", vibe="nature"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/saved?flash=")
    assert len(list_saved(owner_sess="manual-form-owner")) == 1


def test_two_manual_flights_create_public_quest_with_destination_vibes(
    client, monkeypatch
):
    owner = "manual-quest-owner"
    client.cookies.set("yv_sess", owner)

    async def fake_search(query, **kwargs):
        return UnifiedSearchResult(
            query=query,
            results=[],
            offers=[
                FlightOffer(
                    provider="duffel",
                    price=250.0,
                    currency="USD",
                    booking_url=f"https://book.test/{query.origin}-{query.destination}",
                )
            ],
        )

    monkeypatch.setattr(web_module, "search_flights", fake_search)
    outbound = client.post(
        "/api/saved/manual-flight",
        json=_payload("YVR", "LIS", "2099-10-01", vibe="food"),
    ).json()
    inbound = client.post(
        "/api/saved/manual-flight",
        json=_payload("OPO", "YVR", "2099-10-10", vibe="romance"),
    ).json()

    response = client.post(
        "/api/compose-saved",
        json={
            "kind": "quest",
            "saved_ids": [outbound["saved_id"], inbound["saved_id"]],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["bookmarked"] is True
    assert data["trip_meta"]["destination_vibes"] == [
        {"iata": "LIS", "vibe": "food"},
        {"iata": "OPO", "vibe": "romance"},
    ]
    assert "Lisbon · Food" in data["html"]
    assert "Porto · Romance" in data["html"]
    assert count_quests() == 1
    assert list_saved(owner_sess=owner)[0].owner_sess == owner


def test_matching_public_quest_keeps_each_browser_destination_vibes(client, monkeypatch):
    async def fake_search(query, **kwargs):
        return UnifiedSearchResult(
            query=query,
            results=[],
            offers=[
                FlightOffer(
                    provider="duffel",
                    price=250.0,
                    currency="USD",
                    booking_url=f"https://book.test/{query.origin}-{query.destination}",
                )
            ],
        )

    monkeypatch.setattr(web_module, "search_flights", fake_search)
    canonical_ids = []
    for owner, first_vibe, second_vibe in [
        ("manual-quest-a", "food", "romance"),
        ("manual-quest-b", "culture", "nature"),
    ]:
        client.cookies.set("yv_sess", owner)
        first = client.post(
            "/api/saved/manual-flight",
            json=_payload("YVR", "LIS", "2099-11-01", vibe=first_vibe),
        ).json()["saved_id"]
        second = client.post(
            "/api/saved/manual-flight",
            json=_payload("OPO", "YVR", "2099-11-10", vibe=second_vibe),
        ).json()["saved_id"]
        composed = client.post(
            "/api/compose-saved",
            json={"kind": "quest", "saved_ids": [first, second]},
        ).json()
        canonical_ids.append(composed["saved_id"])

    assert canonical_ids[0] == canonical_ids[1]
    assert count_quests() == 1
    for owner, labels in [
        ("manual-quest-a", ("Lisbon · Food", "Porto · Romance")),
        ("manual-quest-b", ("Lisbon · Culture", "Porto · Nature")),
    ]:
        client.cookies.set("yv_sess", owner)
        saved_page = client.get("/saved")
        assert labels[0] in saved_page.text
        assert labels[1] in saved_page.text


@pytest.mark.parametrize(
    ("kind", "routes"),
    [
        ("quest", [("YVR", "LIS"), ("OPO", "YVR")]),
        ("detour", [("YVR", "LIS"), ("LIS", "OPO"), ("OPO", "YVR")]),
    ],
)
def test_composed_manual_fare_retries_keep_each_leg_cabin(
    client, monkeypatch, kind, routes
):
    owner = f"manual-{kind}-cabin-owner"
    client.cookies.set("yv_sess", owner)

    async def failed_search(query, **kwargs):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(web_module, "search_flights", failed_search)
    saved_ids = []
    for index, (origin, destination) in enumerate(routes):
        saved_ids.append(
            client.post(
                "/api/saved/manual-flight",
                json=_payload(
                    origin,
                    destination,
                    f"2099-12-{index + 1:02d}",
                    travelers=2,
                    cabin="business",
                    vibe="culture",
                ),
            ).json()["saved_id"]
        )

    response = client.post(
        "/api/compose-saved",
        json={"kind": kind, "saved_ids": saved_ids},
    )

    assert response.status_code == 200, response.text
    html = response.json()["html"]
    assert html.count('data-cf-cabin="business"') == len(routes)
    assert html.count('data-cf-adults="2"') == len(routes)