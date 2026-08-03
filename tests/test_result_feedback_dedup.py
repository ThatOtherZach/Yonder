"""Vote-stuffing guard for POST /api/result-feedback.

One up vote and one down vote max per (session, vibe, destination); repeats
are silently ignored (ok:true, deduped:true) and skip signal-store writes.
DB errors must NOT be mislabeled as dedup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import yonder.feedback as fb
import yonder.vibe_signals as vs
import yonder.web as web_module


@pytest.fixture(autouse=True)
def isolated_db(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _vote(client, direction="up", dest="LIS", vibe="calm", **extra):
    return client.post(
        "/api/result-feedback",
        json={"direction": direction, "dest_iata": dest, "vibe": vibe, "query": "", **extra},
    )


class TestDedup:
    def test_repeat_same_session_vote_deduped(self, client):
        r1 = _vote(client)  # TestClient persists the yv_sess cookie
        assert r1.status_code == 200
        assert "deduped" not in r1.json()
        r2 = _vote(client)
        assert r2.status_code == 200
        assert r2.json().get("deduped") is True
        assert fb.feedback_stats()["up"] == 1

    def test_up_and_down_each_allowed_once(self, client):
        assert "deduped" not in _vote(client, "up").json()
        assert "deduped" not in _vote(client, "down").json()
        assert _vote(client, "up").json().get("deduped") is True
        assert _vote(client, "down").json().get("deduped") is True
        stats = fb.feedback_stats()
        assert stats["up"] == 1 and stats["down"] == 1

    def test_different_sessions_both_counted(self, client):
        c1 = TestClient(web_module.app)
        c2 = TestClient(web_module.app)
        c1.cookies.set("yv_sess", "a" * 32)
        c2.cookies.set("yv_sess", "b" * 32)
        assert "deduped" not in _vote(c1).json()
        assert "deduped" not in _vote(c2).json()
        assert fb.feedback_stats()["up"] == 2

    def test_different_dest_not_deduped(self, client):
        assert "deduped" not in _vote(client, dest="LIS").json()
        assert "deduped" not in _vote(client, dest="OPO").json()
        assert fb.feedback_stats()["up"] == 2

    def test_duplicate_skips_signal_write(self, client):
        _vote(client)
        with patch("yonder.vibe_signals.upsert_signal") as up:
            _vote(client)
            up.assert_not_called()

    def test_db_error_not_mislabeled_as_dedup(self, client):
        # record_feedback returning None (error/MOCK) must not short-circuit
        with patch("yonder.feedback.record_feedback", return_value=None):
            resp = _vote(client)
        body = resp.json()
        assert body["ok"] is True
        assert "deduped" not in body


class TestConcurrency:
    def test_parallel_identical_votes_persist_once(self, monkeypatch):
        """DB-level unique index makes dedup atomic under concurrency."""
        import threading

        results: list[str | None] = []
        barrier = threading.Barrier(8)

        def vote():
            barrier.wait()
            results.append(
                fb.record_feedback(direction="up", vibe="calm", dest_iata="LIS", session_hash="s1")
            )

        threads = [threading.Thread(target=vote) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wins = [r for r in results if r]  # non-empty row ids
        assert len(wins) == 1
        assert fb.feedback_stats()["up"] == 1

    def test_parallel_http_votes_write_one_signal(self, client):
        from concurrent.futures import ThreadPoolExecutor

        with patch("yonder.vibe_signals.upsert_signal") as up:
            client.cookies.set("yv_sess", "c" * 32)
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda _: _vote(client), range(6)))
            assert up.call_count == 1
        assert fb.feedback_stats()["up"] == 1


class TestRecordFeedbackReturnContract:
    def test_duplicate_returns_empty_string(self):
        first = fb.record_feedback(direction="up", vibe="calm", dest_iata="LIS", session_hash="s1")
        assert first
        dup = fb.record_feedback(direction="up", vibe="calm", dest_iata="LIS", session_hash="s1")
        assert dup == ""

    def test_invalid_direction_returns_none(self):
        result = fb.record_feedback(direction="sideways", vibe="calm", dest_iata="LIS")
        assert result is None
