"""Unit tests for yonder/vibe_signals.py.

All tests use a temporary SQLite file (via monkeypatch on DB_PATH) so they
never touch any real database on disk.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import yonder.vibe_signals as vs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite database."""
    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "signals_test.db")
    # Also clear any cached MOCK state by unsetting the env var
    monkeypatch.delenv("MOCK", raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. MOCK mode — all writes are no-ops
# ---------------------------------------------------------------------------


def test_mock_record_search_returns_none(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    sid = vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT")
    assert sid is None


def test_mock_upsert_signal_returns_none(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    sid = vs.upsert_signal(dest_iata="NRT", vibe="beach", signal_strength=vs.ENGAGED)
    assert sid is None


def test_mock_recompute_scores_returns_false(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    ran = vs.recompute_scores(force=True)
    assert ran is False


def test_mock_db_stays_empty(monkeypatch):
    """With MOCK set, no rows are written even when called multiple times."""
    monkeypatch.setenv("MOCK", "1")
    vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT")
    vs.upsert_signal(dest_iata="NRT", vibe="beach", signal_strength=vs.ENGAGED)
    # Temporarily lift MOCK to inspect the DB
    monkeypatch.delenv("MOCK")
    with vs._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM search_signals").fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# 2. record_search — basic write
# ---------------------------------------------------------------------------


def test_record_search_returns_id():
    sid = vs.record_search(vibe="adventure", origin="YVR", dest_iata="CDG")
    assert isinstance(sid, str) and len(sid) > 0


def test_record_search_rejects_invalid_iata():
    sid = vs.record_search(vibe="adventure", origin="YVR", dest_iata="XX")
    assert sid is None


def test_record_search_idempotent_same_id():
    """INSERT OR IGNORE means re-inserting the same id is silently skipped."""
    sid = vs.record_search(vibe="adventure", origin="YVR", dest_iata="CDG", signal_id="abc123")
    sid2 = vs.record_search(vibe="adventure", origin="YVR", dest_iata="CDG", signal_id="abc123")
    assert sid == "abc123"
    assert sid2 == "abc123"
    with vs._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM search_signals WHERE id = 'abc123'").fetchone()[0]
    assert n == 1


def test_record_search_strength_clamped():
    sid = vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="CDG", signal_strength=99
    )
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
    assert row["signal_strength"] == 4  # clamped to max 4


# ---------------------------------------------------------------------------
# 3. upsert_signal — upgrade-only semantics
# ---------------------------------------------------------------------------


def test_upsert_upgrades_existing_signal():
    # Plant a tier-1 row
    sid = vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT", signal_strength=vs.SEARCHED)
    # Upgrade to tier-3
    vs.upsert_signal(signal_id=sid, dest_iata="NRT", signal_strength=vs.ENGAGED)
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
    assert row["signal_strength"] == vs.ENGAGED


def test_upsert_does_not_downgrade():
    sid = vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT", signal_strength=vs.ENGAGED)
    vs.upsert_signal(signal_id=sid, dest_iata="NRT", signal_strength=vs.SEARCHED)
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
    assert row["signal_strength"] == vs.ENGAGED  # unchanged


def test_upsert_same_strength_is_noop():
    sid = vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT", signal_strength=vs.ENGAGED)
    returned = vs.upsert_signal(signal_id=sid, dest_iata="NRT", signal_strength=vs.ENGAGED)
    assert returned == sid
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
    assert row["signal_strength"] == vs.ENGAGED


def test_upsert_dest_mismatch_inserts_new_row():
    """When dest_iata doesn't match the existing signal, a fresh row is inserted."""
    sid = vs.record_search(vibe="beach", origin="YVR", dest_iata="NRT", signal_strength=vs.SEARCHED)
    new_sid = vs.upsert_signal(signal_id=sid, dest_iata="CDG", signal_strength=vs.ENGAGED)
    # Should have created a brand-new row, not touched the original
    assert new_sid is not None
    assert new_sid != sid
    with vs._connect() as conn:
        orig = conn.execute(
            "SELECT signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
        new = conn.execute(
            "SELECT signal_strength, dest_iata FROM search_signals WHERE id = ?", (new_sid,)
        ).fetchone()
    assert orig["signal_strength"] == vs.SEARCHED  # original untouched
    assert new["dest_iata"] == "CDG"
    assert new["signal_strength"] == vs.ENGAGED


def test_upsert_no_signal_id_no_dest_returns_none():
    result = vs.upsert_signal(signal_strength=vs.ENGAGED)
    assert result is None


def test_upsert_standalone_insert_without_prior_row():
    """upsert_signal with only dest_iata (no signal_id) creates a fresh row."""
    sid = vs.upsert_signal(dest_iata="LHR", vibe="culture", signal_strength=vs.ENGAGED)
    assert sid is not None
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT dest_iata, signal_strength FROM search_signals WHERE id = ?", (sid,)
        ).fetchone()
    assert row["dest_iata"] == "LHR"
    assert row["signal_strength"] == vs.ENGAGED


# ---------------------------------------------------------------------------
# 4. recompute_scores — lazy vs forced
# ---------------------------------------------------------------------------


def test_recompute_force_runs_even_when_fresh():
    """force=True always runs even if last_recompute was just now."""
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT")
    # Seed a recent timestamp so lazy would skip
    with vs._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO signals_meta (key, value) VALUES ('last_recompute', ?)",
            (str(time.time()),),
        )
        conn.commit()
    ran = vs.recompute_scores(force=True)
    assert ran is True


def test_recompute_lazy_skips_when_fresh():
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT")
    # Set last_recompute to now
    with vs._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO signals_meta (key, value) VALUES ('last_recompute', ?)",
            (str(time.time()),),
        )
        conn.commit()
    ran = vs.recompute_scores(force=False)
    assert ran is False


def test_recompute_runs_when_stale():
    """With no prior timestamp, lazy recompute should run."""
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT")
    ran = vs.recompute_scores(force=False)
    assert ran is True


def test_recompute_populates_scores_table():
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT")
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="CDG")
    vs.recompute_scores(force=True)
    with vs._connect() as conn:
        rows = conn.execute("SELECT dest_iata FROM dest_vibe_scores WHERE vibe = 'adventure'").fetchall()
    iatas = {r["dest_iata"] for r in rows}
    assert "NRT" in iatas
    assert "CDG" in iatas


def test_recompute_score_formula():
    """Score = sum(strength * recency) / count.  With a fresh row the recency ≈ 1."""
    vs.record_search(vibe="surf", origin="YVR", dest_iata="SYD", signal_strength=vs.ENGAGED)
    vs.recompute_scores(force=True)
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT score, search_count FROM dest_vibe_scores WHERE dest_iata='SYD' AND vibe='surf'"
        ).fetchone()
    assert row is not None
    assert row["search_count"] == 1
    # score ≈ 3 * 1.0 / 1 = 3.0  (recency very close to 1 for a brand-new row)
    assert 2.9 < row["score"] <= 3.0


def test_recompute_save_count():
    vs.record_search(vibe="surf", origin="YVR", dest_iata="SYD", signal_strength=vs.SAVED)
    vs.record_search(vibe="surf", origin="YVR", dest_iata="SYD", signal_strength=vs.SEARCHED)
    vs.recompute_scores(force=True)
    with vs._connect() as conn:
        row = conn.execute(
            "SELECT save_count, search_count FROM dest_vibe_scores WHERE dest_iata='SYD' AND vibe='surf'"
        ).fetchone()
    assert row["save_count"] == 1
    assert row["search_count"] == 2


# ---------------------------------------------------------------------------
# 5. top_for_vibe grouping
# ---------------------------------------------------------------------------


def test_top_for_vibe_returns_list():
    vs.record_search(vibe="culture", origin="YVR", dest_iata="CDG", signal_strength=vs.ENGAGED)
    vs.record_search(vibe="culture", origin="YVR", dest_iata="NRT", signal_strength=vs.SEARCHED)
    result = vs.top_for_vibe("culture", limit=10)
    assert isinstance(result, list)
    iatas = [r["iata"] for r in result]
    assert "CDG" in iatas
    assert "NRT" in iatas


def test_top_for_vibe_ordered_by_score():
    vs.record_search(vibe="culture", origin="YVR", dest_iata="NRT", signal_strength=vs.SEARCHED)
    vs.record_search(vibe="culture", origin="YVR", dest_iata="CDG", signal_strength=vs.SAVED)
    result = vs.top_for_vibe("culture", limit=10)
    # CDG has higher strength so should rank first
    assert result[0]["iata"] == "CDG"


def test_top_for_vibe_group_by_country_returns_dict():
    vs.record_search(vibe="culture", origin="YVR", dest_iata="CDG", signal_strength=vs.ENGAGED)
    result = vs.top_for_vibe("culture", group_by_country=True)
    assert isinstance(result, dict)


def test_top_for_vibe_shape():
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT", signal_strength=vs.ENGAGED)
    items = vs.top_for_vibe("adventure", limit=5)
    assert len(items) >= 1
    item = items[0]
    for key in ("iata", "vibe", "score", "search_count", "save_count"):
        assert key in item


def test_top_for_vibe_limit_respected():
    for iata in ["NRT", "CDG", "LHR", "JFK", "SYD"]:
        vs.record_search(vibe="adventure", origin="YVR", dest_iata=iata, signal_strength=vs.ENGAGED)
    result = vs.top_for_vibe("adventure", limit=3)
    assert len(result) <= 3


def test_top_for_vibe_empty_vibe_defaults_to_adventure():
    vs.record_search(vibe="adventure", origin="YVR", dest_iata="NRT", signal_strength=vs.ENGAGED)
    result = vs.top_for_vibe(None, limit=10)
    assert isinstance(result, list)


def test_top_for_vibe_no_cross_vibe_leakage():
    vs.record_search(vibe="beach", origin="YVR", dest_iata="CUN", signal_strength=vs.SAVED)
    vs.record_search(vibe="culture", origin="YVR", dest_iata="CDG", signal_strength=vs.ENGAGED)
    beach = vs.top_for_vibe("beach")
    culture = vs.top_for_vibe("culture")
    beach_iatas = [r["iata"] for r in beach]
    culture_iatas = [r["iata"] for r in culture]
    assert "CDG" not in beach_iatas
    assert "CUN" not in culture_iatas


# ---------------------------------------------------------------------------
# Learned shape lean (Escape vs Detour prior adjustment)
# ---------------------------------------------------------------------------


class TestShapeLeanForVibe:
    def test_empty_store_returns_zero(self):
        assert vs.shape_lean_for_vibe("adventure") == 0.0

    def test_detour_heavy_vibe_leans_positive(self):
        for i in range(4):
            vs.record_search(vibe="adventure", origin="YVR", dest_iata="LIS",
                             search_type="detour", signal_strength=4)
        vs.record_search(vibe="adventure", origin="YVR", dest_iata="LIS",
                         search_type="escape", signal_strength=1)
        assert vs.shape_lean_for_vibe("adventure") > 0.5

    def test_escape_heavy_vibe_leans_negative(self):
        for i in range(4):
            vs.record_search(vibe="luxury", origin="YVR", dest_iata="CDG",
                             search_type="escape", signal_strength=4)
        assert vs.shape_lean_for_vibe("luxury") < -0.5

    def test_demo_and_mock_return_zero(self, monkeypatch):
        vs.record_search(vibe="adventure", origin="YVR", dest_iata="LIS",
                         search_type="detour", signal_strength=4)
        assert vs.shape_lean_for_vibe("adventure", demo=True) == 0.0
        monkeypatch.setenv("MOCK", "1")
        assert vs.shape_lean_for_vibe("adventure") == 0.0

    def test_other_search_types_ignored(self):
        vs.record_search(vibe="adventure", origin="YVR", dest_iata="LIS",
                         search_type="save", signal_strength=4)
        assert vs.shape_lean_for_vibe("adventure") == 0.0
