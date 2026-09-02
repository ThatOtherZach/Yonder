"""Regression tests for environment-safe Saved-page share links."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module
from yonder.saved import bookmark_quest, ensure_global_quest


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
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


def test_production_share_keeps_canonical_origin(client, monkeypatch):
    monkeypatch.delenv("REPLIT_DEV_DOMAIN", raising=False)
    monkeypatch.setattr(web_module, "_IS_HTTPS", True)
    quest = _quest()
    client.cookies.set("yv_sess", "share-production-session")
    assert bookmark_quest(quest.id, owner_sess="share-production-session")

    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    assert 'href="https://yonder.city/t/quest/' in saved_page.text
    assert 'href="https://preview.example.replit.dev/t/quest/' not in saved_page.text