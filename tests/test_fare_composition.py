from __future__ import annotations

from datetime import date
import json
import re
import socket

import pytest
from fastapi.testclient import TestClient

from yonder.composition import (
    FareCompositionError,
    compose_detour,
    compose_quest,
    sign_selection,
    verify_selection,
)
from yonder.types import FlightOffer
from yonder.vibe_theme import vibe_theme
from yonder.web import app


@pytest.fixture(autouse=True)
def _public_test_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )


def _fare(
    origin: str,
    destination: str,
    depart: str,
    *,
    price: float = 100,
    currency: str = "USD",
    vibe: str = "adventure",
    provider: str = "duffel",
    missing: bool = False,
    return_date: str | None = None,
):
    offer = FlightOffer(
        provider=provider,
        price=price,
        currency=currency,
        deep_link=f"https://airline.test/{origin}-{destination}",
        google_flights_url=f"https://flights.test/{origin}-{destination}",
        booking_url=f"https://book.test/{origin}-{destination}",
        fare_missing=missing,
    )
    return {
        "kind": "escape",
        "query": {
            "origin": origin,
            "destination": destination,
            "depart_date": depart,
            "return_date": return_date,
            "currency": currency,
        },
        "offer": offer.model_dump(mode="json"),
        "vibe": vibe,
        "prompt": f"{vibe} trip",
    }


def _signed(fare):
    return {
        "kind": fare.get("kind", "escape"),
        "token": sign_selection(fare),
    }


def test_quest_uses_ordered_open_jaw_fares_and_first_vibe():
    first = _fare("YVR", "LIS", "2026-10-01", price=320, vibe="romance")
    second = _fare("OPO", "YVR", "2026-10-10", price=280, vibe="city")

    quest, meta = compose_quest([first, second])

    assert (quest.entry_iata, quest.exit_iata) == ("LIS", "OPO")
    assert quest.depart_date == date(2026, 10, 1)
    assert quest.outbound_date == date(2026, 10, 10)
    assert quest.total_price == 600
    assert quest.inbound_leg.offer.provider == "duffel"
    assert quest.outbound_leg.booking_url == "https://book.test/OPO-YVR"
    assert quest.theme_primary == vibe_theme("romance")["color"]
    assert quest.theme_label == vibe_theme("romance")["label"]
    assert meta["vibe"] == "romance"
    assert meta["composition"] == "selected-fares"


@pytest.mark.parametrize(
    ("fares", "message"),
    [
        (
            [
                _fare("YVR", "LIS", "2026-10-01"),
                _fare("OPO", "SEA", "2026-10-10"),
            ],
            "return to YVR",
        ),
        (
            [
                _fare("YVR", "LIS", "2026-10-01"),
                _fare("LIS", "YVR", "2026-10-10"),
            ],
            "different entry and exit",
        ),
        (
            [
                _fare("YVR", "LIS", "2026-10-10"),
                _fare("OPO", "YVR", "2026-10-01"),
            ],
            "chronological",
        ),
        (
            [
                _fare("YVR", "LIS", "2026-10-01", return_date="2026-10-08"),
                _fare("OPO", "YVR", "2026-10-10"),
            ],
            "Only one-way",
        ),
    ],
)
def test_quest_rejects_invalid_routes_dates_and_round_trips(fares, message):
    with pytest.raises(FareCompositionError, match=message):
        compose_quest(fares)


def test_detour_requires_chain_and_uses_middle_fare_vibe():
    fares = [
        _fare("YVR", "LIS", "2026-10-01", price=300, vibe="serene"),
        _fare("LIS", "OPO", "2026-10-05", price=80, vibe="city"),
        _fare("OPO", "YVR", "2026-10-10", price=250, vibe="romance"),
    ]

    detour, meta = compose_detour(fares)

    assert [(leg.from_iata, leg.to_iata) for leg in detour.legs] == [
        ("YVR", "LIS"),
        ("LIS", "OPO"),
        ("OPO", "YVR"),
    ]
    assert detour.total_price == 630
    assert detour.theme_primary == vibe_theme("city")["color"]
    assert detour.theme_label == vibe_theme("city")["label"]
    assert detour.vibe_tags == ["city"]
    assert meta["vibe"] == "city"
    assert detour.legs[1].offer.deep_link == "https://airline.test/LIS-OPO"


def test_detour_hides_total_for_missing_or_mixed_currency_fares():
    missing = [
        _fare("YVR", "LIS", "2026-10-01"),
        _fare("LIS", "OPO", "2026-10-05", missing=True),
        _fare("OPO", "YVR", "2026-10-10"),
    ]
    mixed = [
        _fare("YVR", "LIS", "2026-10-01", currency="CAD"),
        _fare("LIS", "OPO", "2026-10-05", currency="USD"),
        _fare("OPO", "YVR", "2026-10-10", currency="USD"),
    ]

    assert compose_detour(missing)[0].total_price is None
    assert compose_detour(mixed)[0].total_price is None


def test_detour_rejects_broken_chain_repeated_stops_and_wrong_end():
    with pytest.raises(FareCompositionError, match="Route breaks"):
        compose_detour(
            [
                _fare("YVR", "LIS", "2026-10-01"),
                _fare("MAD", "OPO", "2026-10-05"),
                _fare("OPO", "YVR", "2026-10-10"),
            ]
        )
    with pytest.raises(FareCompositionError, match="return to YVR"):
        compose_detour(
            [
                _fare("YVR", "LIS", "2026-10-01"),
                _fare("LIS", "OPO", "2026-10-05"),
                _fare("OPO", "SEA", "2026-10-10"),
            ]
        )
    with pytest.raises(FareCompositionError, match="different airports"):
        compose_detour(
            [
                _fare("YVR", "LIS", "2026-10-01"),
                _fare("LIS", "LIS", "2026-10-05"),
                _fare("LIS", "YVR", "2026-10-10"),
            ]
        )


def test_quest_plus_bridge_escape_builds_detour_with_bridge_vibe():
    quest, _ = compose_quest(
        [
            _fare("YVR", "LIS", "2026-10-01", vibe="romance"),
            _fare("OPO", "YVR", "2026-10-10", vibe="romance"),
        ]
    )
    selection = {
        "kind": "quest",
        "idea": quest.model_dump(mode="json"),
        "home_iata": "YVR",
        "vibe": "romance",
        "prompt": "Portugal",
    }
    bridge = _fare("LIS", "OPO", "2026-10-05", vibe="city")

    detour, meta = compose_detour([selection, bridge])

    assert len(detour.legs) == 3
    assert detour.total_price == 300
    assert meta["vibe"] == "city"
    assert detour.theme_primary == vibe_theme("city")["color"]


def test_quest_plus_escape_explains_incompatible_bridge():
    quest, _ = compose_quest(
        [
            _fare("YVR", "LIS", "2026-10-01"),
            _fare("OPO", "YVR", "2026-10-10"),
        ]
    )
    selected = {
        "kind": "quest",
        "idea": quest.model_dump(mode="json"),
        "home_iata": "YVR",
    }
    with pytest.raises(FareCompositionError, match="depart from LIS"):
        compose_detour([selected, _fare("MAD", "OPO", "2026-10-05")])


def test_composition_endpoint_renders_existing_quest_card():
    with TestClient(app) as client:
        response = client.post(
            "/api/compose-fares",
            json={
                "kind": "quest",
                "selection": [
                    _signed(_fare("YVR", "LIS", "2026-10-01", vibe="romance")),
                    _signed(_fare("OPO", "YVR", "2026-10-10", vibe="city")),
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["kind"] == "quest"
    assert 'class="boarding-pass is-adventure is-quest"' in payload["html"]
    assert 'data-compose-select="quest"' in payload["html"]
    assert "LIS" in payload["html"] and "OPO" in payload["html"]


def test_composition_endpoint_rejects_before_rendering_or_saving():
    with TestClient(app) as client:
        response = client.post(
            "/api/compose-fares",
            json={
                "kind": "quest",
                "selection": [
                    _signed(_fare("YVR", "LIS", "2026-10-01")),
                    _signed(_fare("OPO", "SEA", "2026-10-10")),
                ],
            },
        )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "return to YVR" in response.json()["error"]


def test_detour_endpoint_renders_and_saves_through_existing_flow():
    selection = [
        _signed(_fare("YVR", "LIS", "2026-10-01", price=300, vibe="serene")),
        _signed(_fare("LIS", "OPO", "2026-10-05", price=80, vibe="city")),
        _signed(_fare("OPO", "YVR", "2026-10-10", price=250, vibe="romance")),
    ]
    with TestClient(app) as client:
        client.cookies.set("yv_sess", "composed-detour")
        response = client.post(
            "/api/compose-fares",
            json={"kind": "detour", "selection": selection},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["kind"] == "detour"
        assert 'id="adv-results"' in payload["html"]
        assert '"vibe": "city"' in payload["html"]
        match = re.search(
            r'<script type="application/json" id="it-json-0">(.*?)</script>',
            payload["html"],
        )
        assert match
        itinerary = json.loads(match.group(1))
        saved = client.post(
            "/api/saved",
            json={"itinerary": itinerary, "trip_meta": payload["trip_meta"]},
        )

    assert saved.status_code == 200
    assert saved.json()["ok"] is True


def test_composition_endpoint_rejects_tampered_selection_token():
    fare = _fare("YVR", "LIS", "2026-10-01")
    token = sign_selection(fare)
    payload, signature = token.split(".", 1)
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + signature

    with TestClient(app) as client:
        response = client.post(
            "/api/compose-fares",
            json={
                "kind": "quest",
                "selection": [
                    {"kind": "escape", "token": tampered},
                    _signed(_fare("OPO", "YVR", "2026-10-10")),
                ],
            },
        )

    assert response.status_code == 400
    assert "expired" in response.json()["error"]


def test_selection_token_round_trip_and_unsafe_links_are_removed():
    fare = _fare("YVR", "LIS", "2026-10-01")
    fare["offer"]["deep_link"] = "javascript:alert(1)"
    fare["offer"]["booking_url"] = "http://localhost/phish"
    fare["offer"]["google_flights_url"] = "https://10.0.0.8/private"

    verified = verify_selection(sign_selection(fare))
    quest, _ = compose_quest(
        [verified, _fare("OPO", "YVR", "2026-10-10")]
    )

    assert quest.inbound_leg.offer.deep_link is None
    assert quest.inbound_leg.offer.booking_url is None
    assert quest.inbound_leg.offer.google_flights_url is None


def test_private_dns_alias_link_is_removed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    fare = _fare("YVR", "LIS", "2026-10-01")
    fare["offer"]["booking_url"] = "https://127.0.0.1.nip.io/private"

    quest, _ = compose_quest(
        [fare, _fare("OPO", "YVR", "2026-10-10")]
    )

    assert quest.inbound_leg.offer.booking_url is None


@pytest.mark.parametrize("price", [-1, float("inf"), float("-inf"), float("nan")])
def test_composition_rejects_invalid_prices(price):
    with pytest.raises(FareCompositionError, match="invalid price"):
        compose_quest(
            [
                _fare("YVR", "LIS", "2026-10-01", price=price),
                _fare("OPO", "YVR", "2026-10-10"),
            ]
        )


def test_explore_template_exposes_accessible_selection_controls():
    source = open("yonder/templates/_boarding_pass.html", encoding="utf-8").read()
    index_source = open("yonder/templates/index.html", encoding="utf-8").read()

    assert 'data-compose-select="escape"' in source
    assert 'data-compose-select="quest"' in source
    assert 'aria-pressed="false"' in source
    assert 'aria-live="polite"' in index_source
    assert "Clear selection" in index_source