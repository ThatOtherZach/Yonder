"""Tests for the intent confidence signal loop.

Verifies that:
1. record_search accepts intent_confidence + intent_rationale and stores them.
2. low_confidence_misses() finds zero-result rows with confidence < threshold.
3. high-confidence rows and rows with results are NOT returned by the audit.
4. Thumbs-down cross-reference from result_feedback works (via PG, not SQLite).

All tests use a throwaway PostgreSQL schema so they never touch real tables.
"""
from __future__ import annotations

import time

import pytest

import yonder.vibe_signals as vs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(pg_schema, monkeypatch):
    """Each test gets its own isolated PG schema."""
    monkeypatch.delenv("MOCK", raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. record_search stores intent fields
# ---------------------------------------------------------------------------


def test_record_search_stores_intent_confidence():
    sid = vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="NRT",
        result_count=0,
        intent_confidence=0.55,
        intent_rationale="ambiguous → try both shapes",
        prompt="somehow going somewhere interesting maybe",
    )
    assert sid is not None
    with vs.get_conn() as conn:
        row = conn.execute(
            "SELECT intent_confidence, intent_rationale FROM search_signals WHERE id = %s",
            (sid,),
        ).fetchone()
    assert row is not None
    assert abs(float(row["intent_confidence"]) - 0.55) < 1e-6
    assert "ambiguous" in (row["intent_rationale"] or "")


def test_record_search_zero_result_stored():
    sid = vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="CDG",
        result_count=0,
        intent_confidence=0.5,
        intent_rationale="test rationale",
    )
    assert sid is not None
    with vs.get_conn() as conn:
        row = conn.execute(
            "SELECT result_count, intent_confidence FROM search_signals WHERE id = %s",
            (sid,),
        ).fetchone()
    assert int(row["result_count"]) == 0
    assert float(row["intent_confidence"]) == pytest.approx(0.5)


def test_record_search_intent_fields_nullable():
    """Omitting intent fields must still write a valid row (backwards compat)."""
    sid = vs.record_search(vibe="adventure", origin="YVR", dest_iata="LHR")
    assert sid is not None
    with vs.get_conn() as conn:
        row = conn.execute(
            "SELECT intent_confidence, intent_rationale FROM search_signals WHERE id = %s",
            (sid,),
        ).fetchone()
    assert row is not None
    assert row["intent_confidence"] is None
    assert row["intent_rationale"] is None


# ---------------------------------------------------------------------------
# 2. low_confidence_misses — core audit query
# ---------------------------------------------------------------------------


def test_low_confidence_misses_returns_zero_result_row():
    """A zero-result row with low confidence must appear in the audit."""
    vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="NRT",
        result_count=0,
        intent_confidence=0.55,
        intent_rationale="ambiguous prompt",
        prompt="pop into somewhere maybe",
    )
    misses = vs.low_confidence_misses(confidence_threshold=0.7)
    assert len(misses) == 1
    m = misses[0]
    assert m["prompt_hash"] is not None
    assert m["intent_confidence"] == pytest.approx(0.55, abs=1e-3)
    assert m["search_count"] >= 1
    assert m["thumbs_down"] is False


def test_low_confidence_misses_excludes_rows_with_results():
    """Rows that returned ≥ 1 result must NOT appear in the audit."""
    vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="NRT",
        result_count=3,
        intent_confidence=0.55,
        prompt="pop into somewhere maybe",
    )
    misses = vs.low_confidence_misses(confidence_threshold=0.7)
    assert misses == []


def test_low_confidence_misses_excludes_high_confidence_rows():
    """Rows with confidence ≥ threshold must NOT appear in the audit."""
    vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="NRT",
        result_count=0,
        intent_confidence=0.85,
        prompt="direct flight Vancouver to Tokyo",
    )
    misses = vs.low_confidence_misses(confidence_threshold=0.7)
    assert misses == []


def test_low_confidence_misses_excludes_null_confidence():
    """Rows without intent_confidence (legacy rows) must NOT appear."""
    vs.record_search(
        vibe="adventure",
        origin="YVR",
        dest_iata="NRT",
        result_count=0,
        # No intent_confidence supplied → NULL in DB
    )
    misses = vs.low_confidence_misses(confidence_threshold=0.7)
    assert misses == []


def test_low_confidence_misses_deduplicates_by_prompt_hash():
    """Multiple zero-result rows for the same prompt → one audit entry."""
    prompt = "pop into somewhere maybe again"
    for _ in range(3):
        vs.record_search(
            vibe="adventure",
            origin="YVR",
            dest_iata="NRT",
            result_count=0,
            intent_confidence=0.55,
            prompt=prompt,
        )
    misses = vs.low_confidence_misses(confidence_threshold=0.7)
    assert len(misses) == 1
    assert misses[0]["search_count"] == 3


def test_low_confidence_misses_custom_threshold():
    """Only rows strictly below the threshold appear."""
    vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="NRT",
        result_count=0, intent_confidence=0.55, prompt="prompt a",
    )
    vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="CDG",
        result_count=0, intent_confidence=0.65, prompt="prompt b",
    )
    # threshold=0.6 → only first row qualifies
    misses = vs.low_confidence_misses(confidence_threshold=0.6)
    assert len(misses) == 1
    assert misses[0]["intent_confidence"] == pytest.approx(0.55, abs=1e-3)


def test_low_confidence_misses_mock_mode_returns_empty(monkeypatch):
    """MOCK mode must suppress the audit query (same as all other reads)."""
    vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="NRT",
        result_count=0, intent_confidence=0.55, prompt="test",
    )
    monkeypatch.setenv("MOCK", "1")
    misses = vs.low_confidence_misses()
    assert misses == []


# ---------------------------------------------------------------------------
# 3. Thumbs-down cross-reference (now via PG result_feedback table)
# ---------------------------------------------------------------------------


def test_prompt_hash_is_case_and_space_insensitive():
    """prompt_hash must produce the same digest regardless of case or spacing."""
    base = vs.prompt_hash("Pop into Tokyo then go to Seoul")
    assert base == vs.prompt_hash("pop into tokyo then go to seoul")
    assert base == vs.prompt_hash("  Pop  into  Tokyo  then  go  to  Seoul  ")
    assert base == vs.prompt_hash("POP INTO TOKYO THEN GO TO SEOUL")
    assert base is not None


def test_low_confidence_misses_thumbs_down_case_spacing_mismatch(pg_schema):
    """Thumbs-down match works even when feedback query differs by case/spacing."""
    raw_prompt = "Pop Into  Tokyo  Then Go To Seoul"
    vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="NRT",
        result_count=0, intent_confidence=0.55, prompt=raw_prompt,
    )

    # Insert thumbs-down with a differently-cased/spaced variant into result_feedback
    feedback_query_variant = "pop into tokyo then go to seoul"
    norm_q = " ".join(feedback_query_variant.lower().split())[:200]
    with pg_schema() as conn:
        conn.execute(
            "INSERT INTO result_feedback"
            " (id, session_hash, vibe, dest_iata, query, direction, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("fbX", None, "adventure", "NRT", norm_q, "down", time.time()),
        )

    misses = vs.low_confidence_misses()
    assert len(misses) == 1
    assert misses[0]["thumbs_down"] is True, (
        "thumbs_down must be True even when prompt case/spacing differs"
    )


def test_low_confidence_misses_flags_thumbs_down(pg_schema):
    """A matching thumbs-down in result_feedback sets thumbs_down=True."""
    prompt_text = "going nowhere fast maybe"

    vs.record_search(
        vibe="adventure", origin="YVR", dest_iata="NRT",
        result_count=0, intent_confidence=0.55, prompt=prompt_text,
    )

    # Insert a matching thumbs-down into the PG test schema
    norm_q = " ".join(prompt_text.lower().split())[:200]
    with pg_schema() as conn:
        conn.execute(
            "INSERT INTO result_feedback"
            " (id, session_hash, vibe, dest_iata, query, direction, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("fb1", None, "adventure", "NRT", norm_q, "down", time.time()),
        )

    misses = vs.low_confidence_misses()
    assert any(m["thumbs_down"] for m in misses), (
        "Expected at least one miss flagged as thumbs_down"
    )
