"""Model-source labeling — resolution + round-trip persistence."""
from __future__ import annotations

import sqlite3

import pytest

from yonder.config import Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


# ── Label resolution ─────────────────────────────────────────────────────────

def test_label_xai_only():
    s = _settings(xai_api_key="k")
    assert s.model_source_label() == "Grok (Server)"


def test_label_byom_with_name():
    s = _settings(
        byom_base_url="https://my.model/v1",
        byom_api_key="k",
        byom_model="llama-3.3-70b",
    )
    assert s.model_source_label() == "BYOM, llama-3.3-70b"


def test_label_byom_unnamed():
    s = _settings(byom_base_url="https://my.model/v1", byom_api_key="k", byom_model="")
    assert s.model_source_label() == "BYOM"


def test_label_byom_wins_over_xai():
    s = _settings(
        xai_api_key="x",
        byom_base_url="https://my.model/v1",
        byom_api_key="k",
        byom_model="gpt-4o",
    )
    assert s.model_source_label() == "BYOM, gpt-4o"


def test_label_nothing_configured():
    s = _settings()
    assert s.model_source_label() == ""


def test_grok_client_label_matches_settings():
    from yonder.grok import GrokClient

    s = _settings(byom_base_url="https://m/v1", byom_api_key="k", byom_model="m1")
    assert GrokClient(s).model_source_label() == "BYOM, m1"
    s2 = _settings(xai_api_key="x")
    assert GrokClient(s2).model_source_label() == "Grok (Server)"


# ── Saved-trip round trip ────────────────────────────────────────────────────

def test_saved_trip_round_trip(tmp_path, monkeypatch):
    import yonder.saved as saved

    monkeypatch.setattr(saved, "DB_PATH", tmp_path / "saved.db")
    itin = {"title": "Test trip", "kind": "stopover", "currency": "USD"}
    row = saved.save_itinerary(
        itin, trip_meta={"model_source": "BYOM, m1", "vibe": "chaos"}
    )
    got = saved.get(row.id)
    assert got is not None
    assert got.model_source == "BYOM, m1"
    assert got.trip_meta["model_source"] == "BYOM, m1"
    # Also stamped into the frozen itinerary JSON (share pages read this)
    assert got.itinerary["model_source"] == "BYOM, m1"


def test_saved_trip_legacy_no_label(tmp_path, monkeypatch):
    import yonder.saved as saved

    monkeypatch.setattr(saved, "DB_PATH", tmp_path / "saved.db")
    row = saved.save_itinerary({"title": "Old trip"})
    got = saved.get(row.id)
    assert got is not None
    assert got.model_source == ""  # unknown/legacy, no error


# ── AI usage log round trip ──────────────────────────────────────────────────

def test_usage_log_round_trip(tmp_path, monkeypatch):
    import yonder.ai_usage as au

    monkeypatch.setattr(au, "DB_PATH", tmp_path / "usage.db")
    au._log_sync(
        "escape",
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "model": "grok-4.5",
            "model_source": "Grok (Server)",
        },
    )
    conn = sqlite3.connect(au.DB_PATH)
    row = conn.execute("SELECT route, model_source FROM ai_usage").fetchone()
    conn.close()
    assert row == ("escape", "Grok (Server)")


def test_usage_log_legacy_db_migrates(tmp_path, monkeypatch):
    """Old ai_usage.db without model_source gets the column added, old rows NULL."""
    import yonder.ai_usage as au

    db = tmp_path / "usage.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, route TEXT NOT NULL, model TEXT,
            prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0, est_cost_usd REAL DEFAULT 0,
            calls INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO ai_usage (ts, route, total_tokens) VALUES ('t', 'old', 9)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(au, "DB_PATH", db)
    au._log_sync("new", {"total_tokens": 3, "model_source": "BYOM"})
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT route, model_source FROM ai_usage ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [("old", None), ("new", "BYOM")]


def test_merge_usage_keeps_model_source():
    from yonder.ai_usage import merge_usage

    merged = merge_usage(
        {"total_tokens": 5, "prompt_tokens": 3, "completion_tokens": 2},
        {"total_tokens": 4, "model_source": "BYOM, m1", "model": "m1"},
    )
    assert merged["model_source"] == "BYOM, m1"


# ── Price history + vibe signals accept the label ────────────────────────────

def test_price_history_model_source(tmp_path, monkeypatch):
    from datetime import date

    import yonder.history as history
    from yonder.types import FlightOffer, SearchQuery

    monkeypatch.setattr(history, "DB_PATH", tmp_path / "hist.db")
    q = SearchQuery(origin="YVR", destination="NRT", depart_date=date(2026, 9, 1))
    offer = FlightOffer(provider="amadeus", price=500.0, currency="USD")
    history.record_offer(q, offer, model_source="Grok (Server)")
    history.record_offer(q, offer)  # legacy/no label
    conn = sqlite3.connect(history.DB_PATH)
    rows = conn.execute(
        "SELECT model_source FROM price_samples ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [("Grok (Server)",), (None,)]


def test_vibe_signal_model_source(tmp_path, monkeypatch):
    import yonder.vibe_signals as vs

    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "sig.db")
    monkeypatch.delenv("MOCK", raising=False)
    sid = vs.record_search(
        vibe="chaos",
        origin="YVR",
        dest_iata="NRT",
        model_source="BYOM, m1",
    )
    assert sid
    conn = sqlite3.connect(tmp_path / "sig.db")
    row = conn.execute(
        "SELECT model_source FROM search_signals WHERE id = ?", (sid,)
    ).fetchone()
    conn.close()
    assert row == ("BYOM, m1",)
