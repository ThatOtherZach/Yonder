"""End-to-end storage tests for the thumbs-up/down feedback pipeline.

Covers three data paths:
  1. Thumbs-up  → row in result_feedback(direction='up') + signal in vibe_signals.db
  2. Thumbs-down → row in result_feedback(direction='down') + rejection signal
                   (strength=0) in vibe_signals.db + row in vibe_questions
  3. GET /api/vibe-suggestions → returns previously answered vibe questions
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import yonder.feedback as fb
import yonder.vibe_signals as vs
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_dbs(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite databases, MOCK env cleared."""
    monkeypatch.setattr(fb, "DB_PATH", tmp_path / "feedback_test.db")
    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "signals_test.db")
    monkeypatch.delenv("MOCK", raising=False)


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


def _post_vote(client, direction: str, dest: str = "LIS", vibe: str = "adventure",
               query: str = "cheap flights") -> dict:
    resp = client.post(
        "/api/result-feedback",
        json={"direction": direction, "dest_iata": dest, "vibe": vibe, "query": query},
    )
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Thumbs-up stores a result_feedback row and a signal row
# ---------------------------------------------------------------------------


class TestThumbsUp:
    def test_response_ok(self, client):
        body = _post_vote(client, "up")
        assert body["ok"] is True
        assert body.get("direction") == "up"
        assert "deduped" not in body

    def test_result_feedback_row_written(self):
        fb.record_feedback(direction="up", vibe="adventure", dest_iata="LIS",
                           query="cheap flights", session_hash="sess_up_test")
        stats = fb.feedback_stats()
        assert stats["up"] == 1
        assert stats["down"] == 0

    def test_result_feedback_row_has_correct_direction(self):
        fb.record_feedback(direction="up", vibe="adventure", dest_iata="LIS",
                           session_hash="sess_dir_test")
        with fb._connect() as conn:
            row = conn.execute(
                "SELECT direction, vibe, dest_iata FROM result_feedback"
            ).fetchone()
        assert row is not None
        assert row["direction"] == "up"
        assert row["vibe"] == "adventure"
        assert row["dest_iata"] == "LIS"

    def test_signal_row_written_to_vibe_signals(self, client):
        _post_vote(client, "up", dest="NRT", vibe="culture")
        with vs._connect() as conn:
            rows = conn.execute(
                "SELECT signal_strength, search_type FROM search_signals"
                " WHERE dest_iata = 'NRT'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["search_type"] == "thumb_up"
        # upsert_signal uses ENGAGED (3) for thumbs-up
        assert rows[0]["signal_strength"] == vs.ENGAGED

    def test_signal_row_linked_to_correct_vibe(self, client):
        _post_vote(client, "up", dest="CDG", vibe="culture")
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT vibe FROM search_signals WHERE dest_iata = 'CDG'"
            ).fetchone()
        assert row is not None
        assert row["vibe"] == "culture"


# ---------------------------------------------------------------------------
# 2. Thumbs-down stores result_feedback, a rejection (strength=0), and vibe_questions
# ---------------------------------------------------------------------------


class TestThumbsDown:
    def test_response_ok(self, client):
        body = _post_vote(client, "down")
        assert body["ok"] is True
        assert body.get("direction") == "down"

    def test_result_feedback_row_written(self):
        fb.record_feedback(direction="down", vibe="adventure", dest_iata="LIS",
                           query="beach escape", session_hash="sess_down_test")
        stats = fb.feedback_stats()
        assert stats["down"] == 1
        assert stats["up"] == 0

    def test_result_feedback_direction_is_down(self):
        fb.record_feedback(direction="down", vibe="adventure", dest_iata="LIS",
                           session_hash="sess_down_dir")
        with fb._connect() as conn:
            row = conn.execute("SELECT direction FROM result_feedback").fetchone()
        assert row is not None
        assert row["direction"] == "down"

    def test_rejection_signal_written_with_strength_zero(self, client):
        _post_vote(client, "down", dest="SYD", vibe="adventure")
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength, search_type FROM search_signals"
                " WHERE dest_iata = 'SYD'"
            ).fetchone()
        assert row is not None
        assert row["signal_strength"] == 0
        assert row["search_type"] == "thumb_down"

    def test_rejection_signal_via_record_rejection_directly(self):
        sid = vs.record_rejection(dest_iata="LHR", vibe="culture",
                                  session_hash="sess_reject")
        assert sid is not None
        with vs._connect() as conn:
            row = conn.execute(
                "SELECT signal_strength, search_type FROM search_signals WHERE id = ?",
                (sid,),
            ).fetchone()
        assert row["signal_strength"] == 0
        assert row["search_type"] == "thumb_down"

    def test_vibe_question_row_created(self, client):
        _post_vote(client, "down", vibe="beach", query="Caribbean island hopping")
        with fb._connect() as conn:
            row = conn.execute(
                "SELECT vibe, query_norm FROM vibe_questions"
            ).fetchone()
        assert row is not None
        assert row["vibe"] == "beach"
        assert "caribbean" in row["query_norm"].lower()

    def test_vibe_question_upsert_returns_new_id(self):
        qid, is_new = fb.upsert_vibe_question(
            vibe="adventure", query="mountain hiking in autumn"
        )
        assert qid
        assert is_new is True

    def test_vibe_question_upsert_idempotent(self):
        qid1, new1 = fb.upsert_vibe_question(vibe="adventure",
                                              query="jungle trek")
        qid2, new2 = fb.upsert_vibe_question(vibe="adventure",
                                              query="jungle trek")
        assert qid1 == qid2
        assert new1 is True
        assert new2 is False
        with fb._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM vibe_questions").fetchone()["c"]
        assert n == 1


# ---------------------------------------------------------------------------
# 3. GET /api/vibe-suggestions returns answered vibe questions
# ---------------------------------------------------------------------------


class TestVibeSuggestions:
    def test_empty_when_no_answers(self, client):
        resp = client.get("/api/vibe-suggestions?vibe=adventure")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["suggestions"] == []

    def test_answered_question_appears_in_suggestions(self, client):
        # Insert and answer a question directly via the module helpers
        qid, _ = fb.upsert_vibe_question(
            vibe="adventure", query="volcano trek south america"
        )
        fb.save_vibe_answer(
            qid,
            {"suggestion": "Try hiking Cotopaxi in Ecuador (UIO).", "dest_iata": "UIO"},
        )

        resp = client.get("/api/vibe-suggestions?vibe=adventure")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["vibe"] == "adventure"
        assert len(body["suggestions"]) == 1
        s = body["suggestions"][0]
        assert s["query"] == "volcano trek south america"
        assert s["answer"]["dest_iata"] == "UIO"

    def test_unanswered_question_excluded(self, client):
        # Unanswered (no answer_json)
        fb.upsert_vibe_question(vibe="adventure", query="no answer yet")
        # Answered
        qid2, _ = fb.upsert_vibe_question(vibe="adventure", query="answered query")
        fb.save_vibe_answer(qid2, {"suggestion": "Go to Queenstown (ZQN).",
                                   "dest_iata": "ZQN"})

        resp = client.get("/api/vibe-suggestions?vibe=adventure")
        body = resp.json()
        queries = [s["query"] for s in body["suggestions"]]
        assert "answered query" in queries
        assert "no answer yet" not in queries

    def test_vibe_filter_is_applied(self, client):
        # Insert one beach and one adventure answer
        qid_b, _ = fb.upsert_vibe_question(vibe="beach", query="tropical beach")
        fb.save_vibe_answer(qid_b, {"suggestion": "Maldives (MLE).", "dest_iata": "MLE"})

        qid_a, _ = fb.upsert_vibe_question(vibe="adventure", query="mountain summit")
        fb.save_vibe_answer(qid_a, {"suggestion": "Nepal (KTM).", "dest_iata": "KTM"})

        resp = client.get("/api/vibe-suggestions?vibe=beach")
        body = resp.json()
        assert body["vibe"] == "beach"
        queries = [s["query"] for s in body["suggestions"]]
        assert "tropical beach" in queries
        assert "mountain summit" not in queries

    def test_multiple_answers_returned_newest_first(self, client):
        import time

        qid1, _ = fb.upsert_vibe_question(vibe="adventure", query="query one")
        fb.save_vibe_answer(qid1, {"suggestion": "First answer", "dest_iata": "NRT"})
        time.sleep(0.02)
        qid2, _ = fb.upsert_vibe_question(vibe="adventure", query="query two")
        fb.save_vibe_answer(qid2, {"suggestion": "Second answer", "dest_iata": "CDG"})

        resp = client.get("/api/vibe-suggestions?vibe=adventure&limit=10")
        body = resp.json()
        assert len(body["suggestions"]) == 2
        # Newest answer first (answer_at DESC)
        assert body["suggestions"][0]["query"] == "query two"
        assert body["suggestions"][1]["query"] == "query one"
