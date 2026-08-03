"""Staleness decay validation for the knowledge graph read path.

Task: prove that months-old, unreinforced knowledge gracefully loses
influence — aged dest_attributes rows must score lower in
effective_attributes and rank lower in seed_candidates than otherwise
identical fresh rows, once any write recomputes their confidence.

Runs inside a throwaway PG schema (pg_schema fixture in conftest).
"""

from __future__ import annotations

import time
import types

import pytest

import yonder.knowledge as knowledge
from yonder.knowledge import (
    CONFIDENCE_STALE_HALFLIFE_DAYS,
    compute_confidence,
    effective_attributes,
    reinforce_from_feedback,
    seed_candidates,
)

DAY = 86400.0
STALE_AGE_DAYS = 300.0  # "hasn't been reinforced in ~10 months"

FRESH_DEST = "HNL"
STALE_DEST = "PPT"
VIBE = "beachy"


@pytest.fixture()
def isolated_db(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield pg_schema


def _dest_row(pg, dest: str, attribute: str, source: str):
    with pg() as conn:
        return conn.execute(
            "SELECT * FROM dest_attributes WHERE dest_iata=%s AND attribute=%s "
            "AND source=%s",
            (dest, attribute, source),
        ).fetchone()


# ── 1. Formula: confidence decays monotonically with age ────────────────────

def test_compute_confidence_decays_with_half_life():
    now = time.time()
    fresh = compute_confidence(3, 0, now, now=now)
    at_half_life = compute_confidence(
        3, 0, now - CONFIDENCE_STALE_HALFLIFE_DAYS * DAY, now=now
    )
    at_two_half_lives = compute_confidence(
        3, 0, now - 2 * CONFIDENCE_STALE_HALFLIFE_DAYS * DAY, now=now
    )
    years_old = compute_confidence(3, 0, now - 5 * 365 * DAY, now=now)

    assert fresh > at_half_life > at_two_half_lives > years_old
    assert at_half_life == pytest.approx(fresh * 0.5, rel=1e-2)
    assert at_two_half_lives == pytest.approx(fresh * 0.25, rel=1e-2)
    assert years_old < 0.01  # months-old guesses fade toward zero


# ── 2. Read path: aged rows lose influence after any recomputing write ──────

def _seed_two_ages(monkeypatch):
    """Capture identical AI interpretations for two destinations — one
    written STALE_AGE_DAYS ago (clock patched inside the knowledge module),
    one written now. Both rows start with the same stored confidence,
    because each was fresh at its own write time."""
    old_ts = time.time() - STALE_AGE_DAYS * DAY
    with monkeypatch.context() as m:
        m.setattr(knowledge, "time", types.SimpleNamespace(time=lambda: old_ts))
        vid_old = knowledge.record_interpretation(
            vibe=VIBE, raw_query="beach trip", origin="YVR",
            dest_iata=STALE_DEST, interpretation="lagoon beaches",
            tags=["beach"],
        )
    vid_new = knowledge.record_interpretation(
        vibe=VIBE, raw_query="beach trip", origin="YVR",
        dest_iata=FRESH_DEST, interpretation="lagoon beaches",
        tags=["beach"],
    )
    assert vid_old and vid_new


def test_aged_rows_score_lower_after_reupsert(isolated_db, monkeypatch):
    _seed_two_ages(monkeypatch)

    stale = _dest_row(isolated_db, STALE_DEST, "beach", "ai_inference")
    fresh = _dest_row(isolated_db, FRESH_DEST, "beach", "ai_inference")
    # Stored state is identical except the reinforcement timestamp:
    # confidence was computed when each row was fresh.
    assert stale["confidence"] == fresh["confidence"]
    assert stale["last_reinforced_at"] < fresh["last_reinforced_at"]

    # Any write recomputes confidence. Apply the SAME contradiction write
    # (thumbs-down) to both destinations — it keeps last_reinforced_at, so
    # only staleness differs afterwards.
    assert reinforce_from_feedback(
        vibe=VIBE, dest_iata=STALE_DEST, direction="down", feedback_id="fb-old"
    )
    assert reinforce_from_feedback(
        vibe=VIBE, dest_iata=FRESH_DEST, direction="down", feedback_id="fb-new"
    )

    stale2 = _dest_row(isolated_db, STALE_DEST, "beach", "ai_inference")
    fresh2 = _dest_row(isolated_db, FRESH_DEST, "beach", "ai_inference")

    # Same evidence, same contradictions — only age differs.
    assert stale2["evidence_count"] == fresh2["evidence_count"]
    assert stale2["contradiction_count"] == fresh2["contradiction_count"]
    assert stale2["confidence"] < fresh2["confidence"], (
        "months-old knowledge must lose confidence relative to fresh"
    )
    # Decay matches the half-life curve (~10 months ≈ ×0.5^(300/180)).
    expected = fresh2["confidence"] * 0.5 ** (
        STALE_AGE_DAYS / CONFIDENCE_STALE_HALFLIFE_DAYS
    )
    assert stale2["confidence"] == pytest.approx(expected, rel=0.05)

    # effective_attributes: the aged destination's score is lower.
    stale_score = effective_attributes(dest_iata=STALE_DEST)["beach"]
    fresh_score = effective_attributes(dest_iata=FRESH_DEST)["beach"]
    assert stale_score < fresh_score


def test_aged_rows_rank_lower_in_seed_candidates(isolated_db, monkeypatch):
    _seed_two_ages(monkeypatch)
    # Recompute both via the same write so staleness is the only difference.
    reinforce_from_feedback(vibe=VIBE, dest_iata=STALE_DEST, direction="down")
    reinforce_from_feedback(vibe=VIBE, dest_iata=FRESH_DEST, direction="down")

    cands = seed_candidates(vibe=VIBE, origin=None)
    by_iata = {c["iata"]: c for c in cands}
    assert FRESH_DEST in by_iata and STALE_DEST in by_iata

    order = [c["iata"] for c in cands]
    assert order.index(FRESH_DEST) < order.index(STALE_DEST), (
        "fresh knowledge must outrank months-old knowledge in seeding"
    )
    assert by_iata[STALE_DEST]["score"] < by_iata[FRESH_DEST]["score"]
