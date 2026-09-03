from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
from yonder.saved import (
    bookmarked_quest_ids,
    count_quests,
    list_bookmarked_quests,
    list_saved,
    save_itinerary,
    update_quest_bookmark_override,
)
from yonder.types import FlightOffer


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


def _save_escape(
    owner: str,
    origin: str,
    destination: str,
    depart: str,
    *,
    price: float = 100,
):
    offer = FlightOffer(
        provider="duffel",
        price=price,
        currency="USD",
        deep_link=f"https://airline.test/{origin}-{destination}",
        google_flights_url=f"https://flights.test/{origin}-{destination}",
        booking_url=f"https://book.test/{origin}-{destination}",
    )
    return save_itinerary(
        {
            "kind": "escape",
            "title": f"{origin} → {destination}",
            "currency": "USD",
            "total_price": price,
            "legs": [
                {
                    "from_iata": origin,
                    "to_iata": destination,
                    "depart_date": depart,
                    "offer": offer.model_dump(mode="json"),
                    "google_flights_url": offer.google_flights_url,
                    "booking_url": offer.booking_url,
                }
            ],
        },
        trip_meta={"origin": origin, "vibe": "adventure"},
        owner_sess=owner,
    )


def _compose(client, owner: str, kind: str, saved_ids: list[str]):
    client.cookies.set("yv_sess", owner)
    return client.post(
        "/api/compose-saved",
        json={"kind": kind, "saved_ids": saved_ids},
    )


def test_saved_quest_creation_is_canonical_bookmarked_and_idempotent(client):
    owner = "saved-compose-quest"
    outbound = _save_escape(owner, "YVR", "LIS", "2099-10-01", price=320)
    inbound = _save_escape(owner, "OPO", "YVR", "2099-10-10", price=280)

    first = _compose(client, owner, "quest", [outbound.id, inbound.id])
    second = _compose(client, owner, "quest", [outbound.id, inbound.id])

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["saved_id"] == second.json()["saved_id"]
    assert first.json()["bookmarked"] is True
    assert count_quests() == 1
    assert bookmarked_quest_ids(owner_sess=owner) == {first.json()["saved_id"]}
    browse = client.get("/quests?origin=")
    assert "Lisbon" in browse.text
    assert "Porto" in browse.text


def test_saved_detour_creation_is_private_and_idempotent(client):
    owner = "saved-compose-detour"
    fares = [
        _save_escape(owner, "YVR", "LIS", "2099-10-01", price=300),
        _save_escape(owner, "LIS", "OPO", "2099-10-05", price=80),
        _save_escape(owner, "OPO", "YVR", "2099-10-10", price=250),
    ]

    first = _compose(client, owner, "detour", [fare.id for fare in fares])
    second = _compose(client, owner, "detour", [fare.id for fare in fares])

    assert first.status_code == 200, first.text
    assert first.json()["private"] is True
    assert first.json()["saved_id"] == second.json()["saved_id"]
    saved = list_saved(owner_sess=owner)
    composed = [item for item in saved if item.id == first.json()["saved_id"]]
    assert len(composed) == 1
    assert composed[0].owner_sess == owner
    assert count_quests() == 0


def test_saved_quest_plus_escape_extends_to_private_detour(client):
    owner = "saved-compose-extension"
    first = _save_escape(owner, "YVR", "LIS", "2099-10-01")
    last = _save_escape(owner, "OPO", "YVR", "2099-10-10")
    quest_response = _compose(client, owner, "quest", [first.id, last.id])
    assert quest_response.status_code == 200, quest_response.text
    bridge = _save_escape(owner, "LIS", "OPO", "2099-10-05")

    response = _compose(
        client,
        owner,
        "detour",
        [quest_response.json()["saved_id"], bridge.id],
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "detour"
    assert response.json()["private"] is True
    assert any(
        item.id == response.json()["saved_id"]
        for item in list_saved(owner_sess=owner)
    )


def test_saved_quest_extension_uses_personal_reschedule_override(client):
    owner = "saved-compose-override"
    first = _save_escape(owner, "YVR", "LIS", "2099-10-01")
    last = _save_escape(owner, "OPO", "YVR", "2099-10-10")
    quest_response = _compose(client, owner, "quest", [first.id, last.id])
    assert quest_response.status_code == 200, quest_response.text
    quest_id = quest_response.json()["saved_id"]
    quest = list_bookmarked_quests(owner_sess=owner)[0]
    override = dict(quest.itinerary)
    override["depart_date"] = "2099-11-01"
    override["outbound_date"] = "2099-11-10"
    override["inbound_leg"] = dict(override["inbound_leg"])
    override["outbound_leg"] = dict(override["outbound_leg"])
    override["inbound_leg"]["depart_date"] = "2099-11-01"
    override["outbound_leg"]["depart_date"] = "2099-11-10"
    assert update_quest_bookmark_override(quest_id, override, owner_sess=owner)
    bridge = _save_escape(owner, "LIS", "OPO", "2099-11-05")

    response = _compose(client, owner, "detour", [quest_id, bridge.id])

    assert response.status_code == 200, response.text
    composed = next(
        item
        for item in list_saved(owner_sess=owner)
        if item.id == response.json()["saved_id"]
    )
    assert [leg["depart_date"] for leg in composed.itinerary["legs"]] == [
        "2099-11-01",
        "2099-11-05",
        "2099-11-10",
    ]


def test_saved_quest_extension_rejects_expired_personal_override(client):
    owner = "saved-compose-expired-quest"
    first = _save_escape(owner, "YVR", "LIS", "2099-10-01")
    last = _save_escape(owner, "OPO", "YVR", "2099-10-10")
    quest_response = _compose(client, owner, "quest", [first.id, last.id])
    quest_id = quest_response.json()["saved_id"]
    quest = list_bookmarked_quests(owner_sess=owner)[0]
    override = dict(quest.itinerary)
    override["depart_date"] = "2020-01-01"
    override["outbound_date"] = "2020-01-10"
    override["inbound_leg"] = dict(override["inbound_leg"])
    override["outbound_leg"] = dict(override["outbound_leg"])
    override["inbound_leg"]["depart_date"] = "2020-01-01"
    override["outbound_leg"]["depart_date"] = "2020-01-10"
    assert update_quest_bookmark_override(quest_id, override, owner_sess=owner)
    bridge = _save_escape(owner, "LIS", "OPO", "2099-11-05")

    response = _compose(client, owner, "detour", [quest_id, bridge.id])

    assert response.status_code == 400
    assert response.json()["error"] == (
        "This Saved Quest has expired. Update its dates before composing it."
    )


def test_saved_composition_rejects_other_browser_and_bad_order(client):
    owner = "saved-compose-owner"
    first = _save_escape(owner, "YVR", "LIS", "2099-10-01")
    last = _save_escape(owner, "OPO", "YVR", "2099-10-10")

    isolated = _compose(client, "different-browser", "quest", [first.id, last.id])
    reversed_route = _compose(client, owner, "quest", [last.id, first.id])

    assert isolated.status_code == 400
    assert "another browser" in isolated.json()["error"]
    assert reversed_route.status_code == 400
    assert "return to OPO" in reversed_route.json()["error"]


def test_saved_composition_rejects_expired_and_duplicate_tickets(client):
    owner = "saved-compose-invalid"
    expired = _save_escape(owner, "YVR", "LIS", "2020-01-01")
    valid = _save_escape(owner, "OPO", "YVR", "2099-10-10")

    expired_response = _compose(client, owner, "quest", [expired.id, valid.id])
    duplicate_response = _compose(client, owner, "quest", [valid.id, valid.id])

    assert expired_response.status_code == 400
    assert "expired" in expired_response.json()["error"]
    assert "Update its dates" in expired_response.json()["error"]
    assert duplicate_response.status_code == 400
    assert "only once" in duplicate_response.json()["error"]


def test_saved_page_renders_accessible_builder_controls(client):
    owner = "saved-compose-page"
    _save_escape(owner, "YVR", "LIS", "2099-10-01")
    client.cookies.set("yv_sess", owner)

    response = client.get("/saved")

    assert response.status_code == 200
    assert 'id="saved-composition-builder"' in response.text
    assert 'class="btn secondary saved-compose-select"' in response.text
    assert 'aria-pressed="false"' in response.text
    assert "Choose tickets in route order" in response.text
    assert "Move up" in response.text
    assert 'fetch("/api/compose-saved"' in response.text