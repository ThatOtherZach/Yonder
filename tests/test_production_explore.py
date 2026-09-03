from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient

from yonder.config import Settings
from yonder.grok import ParsedTrip
from yonder.types import FlightOffer, Segment, UnifiedSearchResult
from yonder import web as web_module
from yonder.saved import list_saved


FUTURE = "2032-06-15"


def _offer(
    *,
    provider: str,
    price: float,
    stops: int,
    duration: int,
    raw_id: str,
    deal_score: int,
) -> FlightOffer:
    return FlightOffer(
        provider=provider,
        price=price,
        currency="USD",
        airlines=[provider.upper()],
        segments_out=[
            Segment(
                origin="YVR",
                destination="NRT",
                departure=datetime(2032, 6, 15, 8, 0),
            )
        ],
        stops_out=stops,
        duration_out_minutes=duration,
        raw_id=raw_id,
        deal_score=deal_score,
    )


def test_role_selection_is_distinct_deterministic_and_honest():
    cheapest = _offer(
        provider="cheap",
        price=300,
        stops=2,
        duration=900,
        raw_id="cheap",
        deal_score=90,
    )
    direct = _offer(
        provider="direct",
        price=500,
        stops=0,
        duration=600,
        raw_id="direct",
        deal_score=20,
    )
    vibe = _offer(
        provider="vibe",
        price=400,
        stops=1,
        duration=700,
        raw_id="vibe",
        deal_score=80,
    )

    slots = web_module._select_escape_role_slots([vibe, direct, cheapest])

    assert [slot["role"] for slot in slots] == [
        "Cheapest",
        "Most direct",
        "Best vibe match",
    ]
    assert [slot["offer"].raw_id for slot in slots] == ["cheap", "direct", "vibe"]
    assert len({web_module._escape_offer_identity(slot["offer"]) for slot in slots}) == 3

    partial = web_module._select_escape_role_slots([cheapest])
    assert [slot["state"] for slot in partial] == ["ready", "unavailable", "unavailable"]


def test_history_fallback_occupies_one_slot_only():
    fallback = FlightOffer(
        provider="fallback",
        price=0,
        currency="USD",
        fare_missing=True,
        fare_note="recently ~$420",
    )
    slots = web_module._select_escape_role_slots([fallback])

    assert slots[0]["state"] == "ready"
    assert slots[0]["fallback"] is True
    assert slots[0]["offer"] is fallback
    assert [slot["state"] for slot in slots[1:]] == ["unavailable", "unavailable"]


def test_production_explore_runs_one_fresh_scan_and_no_quest_or_recycle(
    monkeypatch,
):
    settings = Settings(testing=False, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
    monkeypatch.setattr(web_module, "detect_trip_gaps", lambda *a, **kw: [])
    monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None)

    parsed = ParsedTrip(
        origin="YVR",
        destination="NRT",
        depart_date=date.fromisoformat(FUTURE),
        currency="USD",
    )

    async def fake_unified(self, *args, **kwargs):
        return {"escape": parsed, "detour_cities": None, "quest_pairs": []}

    async def fake_search(query, **kwargs):
        search_calls.append(kwargs)
        return UnifiedSearchResult(query=query, results=[], offers=offers)

    def forbidden(*args, **kwargs):
        raise AssertionError("production Explore must not start saved or Quest work")

    import yonder.encyclopedia as encyclopedia
    import yonder.grok as grok
    import yonder.quest_jobs as quest_jobs
    import yonder.recycle as recycle

    async def no_brief(*args, **kwargs):
        return None

    monkeypatch.setattr(grok.GrokClient, "plan_unified", fake_unified)
    monkeypatch.setattr(encyclopedia, "get_cached", lambda *a, **kw: None)
    monkeypatch.setattr(encyclopedia, "get_place_brief", no_brief)
    monkeypatch.setattr(quest_jobs, "create_job", forbidden)
    monkeypatch.setattr(recycle, "find_recycled_escape", forbidden)

    offers = [
        _offer(
            provider="cheap",
            price=300,
            stops=2,
            duration=900,
            raw_id="cheap",
            deal_score=90,
        ),
        _offer(
            provider="direct",
            price=500,
            stops=0,
            duration=600,
            raw_id="direct",
            deal_score=20,
        ),
        _offer(
            provider="vibe",
            price=400,
            stops=1,
            duration=700,
            raw_id="vibe",
            deal_score=80,
        ),
    ]
    search_calls: list[dict] = []
    monkeypatch.setattr(web_module, "search_flights", fake_search)

    client = TestClient(web_module.app, raise_server_exceptions=True)
    response = client.post(
        "/explore",
        data={
            "prompt": "YVR to Tokyo",
            "origin": "YVR",
            "depart": FUTURE,
            "vibe": "adventure",
            "force_mode": "mix",
        },
    )

    assert response.status_code == 200
    assert len(search_calls) == 1
    assert search_calls[0]["return_all_offers"] is True
    assert 'data-escape-role="cheapest"' in response.text
    assert 'data-escape-role="most-direct"' in response.text
    assert 'data-escape-role="best-vibe-match"' in response.text
    assert response.text.count('class="btn secondary btn-save-escape"') == 3
    assert 'id="quest-results"' not in response.text
    assert 'id="detour-results"' not in response.text
    assert 'id="fare-composition"' not in response.text
    assert "bootQuestJobPoll();" not in response.text
    assert "bootDetourCriteriaStrip();\n        bootPlanQuestButton();" not in response.text


def test_explore_laboratory_keeps_planning_panels(monkeypatch):
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
    client = TestClient(web_module.app, raise_server_exceptions=True)

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="quest-results"' in response.text
    assert 'id="detour-results"' in response.text
    assert 'id="fare-composition"' in response.text
    assert "bootQuestJobPoll();" in response.text
    assert "bootPlanDetourButton();" in response.text


def test_three_escape_offers_save_as_distinct_rows_and_retry_idempotently(
    monkeypatch,
):
    settings = Settings(testing=False, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)
    owner = "production-explore-three-save"
    query = {
        "origin": "YVR",
        "destination": "NRT",
        "depart_date": FUTURE,
        "return_date": None,
        "currency": "USD",
    }
    offers = [
        _offer(
            provider=f"provider-{index}",
            price=300 + index * 50,
            stops=index,
            duration=600 + index * 60,
            raw_id=f"role-{index}",
            deal_score=90 - index,
        ).model_dump(mode="json")
        for index in range(3)
    ]

    with TestClient(web_module.app, raise_server_exceptions=True) as client:
        client.cookies.set("yv_sess", owner)
        responses = [
            client.post(
                "/api/saved",
                json={
                    "kind": "escape",
                    "ask": "YVR to Tokyo",
                    "query": query,
                    "offer": offer,
                    "trip_meta": {"vibe": "adventure"},
                },
            )
            for offer in offers
        ]
        retry = client.post(
            "/api/saved",
            json={
                "kind": "escape",
                "ask": "YVR to Tokyo",
                "query": query,
                "offer": offers[1],
                "trip_meta": {"vibe": "adventure"},
            },
        )

    assert all(response.status_code == 200 for response in responses)
    ids = [response.json()["id"] for response in responses]
    assert len(set(ids)) == 3
    assert retry.status_code == 200
    assert retry.json()["id"] == ids[1]
    saved_ids = {row.id for row in list_saved(owner_sess=owner)}
    assert set(ids) <= saved_ids