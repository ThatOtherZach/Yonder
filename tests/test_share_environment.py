"""Regression tests for environment-safe Saved-page share links."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import yonder.web as web_module
from yonder.saved import bookmark_quest, ensure_global_quest
from yonder.types import SearchQuery


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    monkeypatch.setenv("REPLIT_DEV_DOMAIN", "preview.example.replit.dev")
    monkeypatch.setattr(web_module, "_IS_HTTPS", False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _quest():
    return ensure_global_quest(
        {
            "kind": "quest",
            "title": "Osaka → overland → Seoul",
            "entry_iata": "KIX",
            "exit_iata": "ICN",
            "entry_city": "Osaka",
            "exit_city": "Seoul",
            "depart_date": "2026-11-01",
        },
        trip_meta={"origin": "YVR", "vibe": "adventure"},
        origin="YVR",
    )


def _request(host: str = "preview.example.replit.dev") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": (host, 443),
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
    )


def test_saved_quest_share_uses_preview_origin_and_loads_from_same_database(client):
    quest = _quest()
    client.cookies.set("yv_sess", "share-preview-session")
    assert bookmark_quest(quest.id, owner_sess="share-preview-session")

    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    marker = 'href="https://preview.example.replit.dev/t/quest/'
    assert marker in saved_page.text
    assert 'href="https://yonder.city/t/quest/' not in saved_page.text

    start = saved_page.text.index(marker) + len('href="https://preview.example.replit.dev')
    path = saved_page.text[start:].split('"', 1)[0]
    shared = client.get(path)
    assert shared.status_code == 200
    assert "Osaka" in shared.text
    assert "Seoul" in shared.text


def test_preview_share_routes_for_escape_detour_and_quest(client):
    """Every share kind gets a Preview URL that resolves in this database."""
    escape = web_module._share_escape(
        _request(),
        SimpleNamespace(
            query=SearchQuery(
                origin="YVR",
                destination="NRT",
                depart_date="2026-11-01",
            )
        ),
        {"price": 500, "currency": "USD"},
    )
    detour = web_module._share_detour(
        _request(),
        {
            "kind": "detour",
            "title": "YVR → DXB detour",
            "stop_iata": "DXB",
            "legs": [{"from_iata": "YVR", "to_iata": "DXB", "depart_date": "2026-11-01"}],
        },
    )
    quest = web_module._share_quest(_request(), _quest().itinerary, "YVR")

    for kind, share in (("escape", escape), ("detour", detour), ("quest", quest)):
        assert share is not None, kind
        assert share["url"].startswith("https://preview.example.replit.dev/t/")
        response = client.get(share["path"])
        assert response.status_code == 200


def test_forged_host_does_not_control_preview_share_origin(client):
    response = client.get("/saved", headers={"host": "attacker.example"})
    assert response.status_code == 200
    assert "https://attacker.example/t/" not in response.text


def test_production_share_keeps_canonical_origin_even_when_preview_env_exists(client, monkeypatch):
    monkeypatch.setenv("REPLIT_DOMAINS", "yonder.city")
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    monkeypatch.setattr(web_module, "_IS_HTTPS", True)
    quest = _quest()
    client.cookies.set("yv_sess", "share-production-session")
    assert bookmark_quest(quest.id, owner_sess="share-production-session")

    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    assert 'href="https://yonder.city/t/quest/' in saved_page.text
    assert 'href="https://preview.example.replit.dev/t/quest/' not in saved_page.text


def test_forged_published_host_does_not_switch_preview_to_production(client, monkeypatch):
    monkeypatch.setenv("REPLIT_DOMAINS", "yonder.city")
    monkeypatch.setattr(web_module, "_IS_HTTPS", False)
    share = web_module._share_detour(
        _request("yonder.city"),
        {"kind": "detour", "legs": [{"from_iata": "YVR", "to_iata": "DXB"}]},
    )
    assert share is not None
    assert share["url"].startswith("https://preview.example.replit.dev/t/detour/")