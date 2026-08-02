"""Endpoint tests for vibe-signal HTTP surfaces.

Uses FastAPI's TestClient (synchronous) with a temporary SQLite DB so no
real on-disk state is touched.

Surfaces covered:
  POST /api/signal-event  — clamps strength ≤3, rejects empty payloads
  GET  /api/vibe-stats    — returns expected JSON shape
  GET  /saved             — sets yv_sess cookie, writes tier-2 rows once per
                            session+destination pair
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.vibe_signals as vs
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite database and no MOCK env var."""
    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "signals_test.db")
    monkeypatch.delenv("MOCK", raising=False)
    # Clear the module-level session cache between tests
    web_module._REVIEWED_SEEN.clear()
    yield
    web_module._REVIEWED_SEEN.clear()


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# POST /api/signal-event
# ---------------------------------------------------------------------------


class TestSignalEvent:
    def test_empty_body_rejected(self, client):
        resp = client.post("/api/signal-event", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_missing_both_signal_id_and_dest(self, client):
        resp = client.post("/api/signal-event", json={"vibe": "beach"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_strength_clamped_to_3(self, client):
        """Tier 4 (SAVED) must never be written from client-side events."""
        resp = client.post(
            "/api/signal-event",
            json={"dest_iata": "NRT", "vibe": "adventure", "strength": 99},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        sid = data["signal_id"]
        assert sid is not None
        # Verify strength in DB is ≤ 3
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
            ).fetchone()
        assert row["signal_strength"] <= 3

    def test_strength_4_clamped(self, client):
        resp = client.post(
            "/api/signal-event",
            json={"dest_iata": "CDG", "vibe": "culture", "strength": 4},
        )
        assert resp.status_code == 200
        sid = resp.json()["signal_id"]
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
            ).fetchone()
        assert row["signal_strength"] == 3

    def test_valid_dest_returns_signal_id(self, client):
        resp = client.post(
            "/api/signal-event",
            json={"dest_iata": "LHR", "vibe": "culture", "strength": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["signal_id"], str)

    def test_existing_signal_upgraded(self, client):
        """Sending the same signal_id with higher strength upgrades the row."""
        # First create a row via record_search
        sid = vs.record_search(
            vibe="adventure", origin="YVR", dest_iata="NRT", signal_strength=vs.SEARCHED
        )
        resp = client.post(
            "/api/signal-event",
            json={"signal_id": sid, "dest_iata": "NRT", "strength": 3},
        )
        assert resp.status_code == 200
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
            ).fetchone()
        assert row["signal_strength"] == 3

    def test_non_json_body_rejected(self, client):
        resp = client.post(
            "/api/signal-event",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_strength_default_is_3(self, client):
        """Omitting strength should default to 3."""
        resp = client.post(
            "/api/signal-event",
            json={"dest_iata": "SYD", "vibe": "adventure"},
        )
        assert resp.status_code == 200
        sid = resp.json()["signal_id"]
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
            ).fetchone()
        assert row["signal_strength"] == 3


# ---------------------------------------------------------------------------
# GET /api/vibe-stats
# ---------------------------------------------------------------------------


class TestVibeStats:
    def test_returns_ok_shape(self, client):
        resp = client.get("/api/vibe-stats?vibe=adventure")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "vibe" in data
        assert "top" in data
        assert "grouped_by_country" in data

    def test_vibe_echoed_in_response(self, client):
        resp = client.get("/api/vibe-stats?vibe=beach")
        assert resp.json()["vibe"] == "beach"

    def test_top_is_list_by_default(self, client):
        resp = client.get("/api/vibe-stats?vibe=adventure")
        assert isinstance(resp.json()["top"], list)

    def test_grouped_false_by_default(self, client):
        resp = client.get("/api/vibe-stats?vibe=adventure")
        assert resp.json()["grouped_by_country"] is False

    def test_group_by_country_returns_dict(self, client):
        # Seed at least one row so grouping has something to bucket
        vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT")
        vs.recompute_scores(force=True)
        resp = client.get("/api/vibe-stats?vibe=adventure&group=country")
        data = resp.json()
        assert data["grouped_by_country"] is True
        assert isinstance(data["top"], dict)

    def test_top_items_have_expected_keys(self, client):
        vs.record_search(vibe="culture", origin="YVR", dest_iata="CDG", signal_strength=vs.ENGAGED)
        vs.recompute_scores(force=True)
        resp = client.get("/api/vibe-stats?vibe=culture&limit=5")
        items = resp.json()["top"]
        assert len(items) >= 1
        for key in ("iata", "score", "search_count", "save_count"):
            assert key in items[0]

    def test_limit_param_respected(self, client):
        for iata in ["NRT", "CDG", "LHR", "JFK", "SYD", "BKK"]:
            vs.record_search(vibe="adventure", origin="YVR", dest_iata=iata, signal_strength=vs.ENGAGED)
        vs.recompute_scores(force=True)
        resp = client.get("/api/vibe-stats?vibe=adventure&limit=3")
        assert len(resp.json()["top"]) <= 3

    def test_empty_vibe_defaults_to_adventure(self, client):
        resp = client.get("/api/vibe-stats")
        data = resp.json()
        assert data["ok"] is True
        assert data["vibe"] == "adventure"


# ---------------------------------------------------------------------------
# GET /saved — cookie + tier-2 signal
# ---------------------------------------------------------------------------


class TestSavedPage:
    def test_sets_yv_sess_cookie_for_new_session(self, client):
        resp = client.get("/saved", follow_redirects=True)
        assert resp.status_code == 200
        assert "yv_sess" in resp.cookies

    def test_keeps_existing_yv_sess_cookie(self, client):
        # First visit creates cookie
        resp1 = client.get("/saved", follow_redirects=True)
        sess = resp1.cookies["yv_sess"]
        # Second visit with that cookie should not re-set a new one
        resp2 = client.get("/saved", cookies={"yv_sess": sess}, follow_redirects=True)
        # Cookie should not change (or absent from Set-Cookie, i.e. same value)
        if "yv_sess" in resp2.cookies:
            assert resp2.cookies["yv_sess"] == sess

    def test_tier2_signal_written_for_saved_destination(self, client, tmp_path, monkeypatch):
        """Visiting /saved should write a REVIEWED (tier-2) signal for each saved dest."""
        from yonder.saved import save_itinerary

        # Save one itinerary with a known IATA
        save_itinerary(
            {
                "title": "Test Trip",
                "kind": "escape",
                "currency": "USD",
                "total_price": 500.0,
                "display_price": "$500",
                "stop_iata": "NRT",
                "stop_city": "Tokyo",
                "stay_days": 7,
                "vibe": "adventure",
            },
            trip_meta={"mock": False, "origin": "YVR", "destination": "NRT"},
        )

        resp = client.get("/saved", follow_redirects=True)
        assert resp.status_code == 200

        # Give the executor a moment to flush (TestClient runs sync but executor is threaded)
        import time as _time
        _time.sleep(0.1)

        with vs._connect() as conn:
            rows = conn.execute(
                "SELECT signal_strength, dest_iata FROM search_signals WHERE dest_iata = 'NRT'"
            ).fetchall()

        assert len(rows) >= 1
        strengths = [r["signal_strength"] for r in rows]
        assert vs.REVIEWED in strengths

    def test_tier2_written_only_once_per_session_per_dest(self, client):
        """Second visit in the same session must not insert a duplicate tier-2 row."""
        from yonder.saved import save_itinerary

        save_itinerary(
            {
                "title": "Test Trip",
                "kind": "escape",
                "currency": "USD",
                "total_price": 500.0,
                "display_price": "$500",
                "stop_iata": "CDG",
                "stop_city": "Paris",
                "stay_days": 5,
                "vibe": "culture",
            },
            trip_meta={"mock": False, "origin": "YVR", "destination": "CDG"},
        )

        resp1 = client.get("/saved", follow_redirects=True)
        sess = resp1.cookies.get("yv_sess", "")

        import time as _time
        _time.sleep(0.1)

        resp2 = client.get("/saved", cookies={"yv_sess": sess}, follow_redirects=True)

        _time.sleep(0.1)

        with vs._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM search_signals WHERE dest_iata = 'CDG' AND signal_strength = ?",
                (vs.REVIEWED,),
            ).fetchall()

        # Only one REVIEWED signal for CDG regardless of how many times /saved is visited
        assert len(rows) == 1

