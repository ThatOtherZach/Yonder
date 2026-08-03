"""End-to-end provenance & confidence validation for the knowledge graph.

Task: prove that real flows (AI parse → interpretation capture; thumbs
up/down via the web endpoint) write correct source tags, keep per-source
rows separate, move confidence the right way, and leave a traceable
evidence trail — all inside a throwaway PG schema (pg_schema fixture).
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import yonder.knowledge as knowledge
from yonder.knowledge import (
    SOURCE_MULTIPLIERS,
    compute_confidence,
    effective_attributes,
    evidence_for,
    get_interpretations,
    reinforce_from_feedback,
)


@pytest.fixture()
def isolated_db(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    # A leftover AI key must not trigger real network calls in feedback paths
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield pg_schema


def _dest_row(pg, dest: str, attribute: str, source: str):
    with pg() as conn:
        return conn.execute(
            "SELECT * FROM dest_attributes WHERE dest_iata=%s AND attribute=%s "
            "AND source=%s",
            (dest, attribute, source),
        ).fetchone()


# ── 1. End-to-end write path: search flow → interpretation + provenance ─────

@pytest.mark.anyio
async def test_search_flow_captures_interpretation_with_provenance(
    isolated_db, monkeypatch
):
    """Drive the real escape parse flow (GrokClient.parse_natural_language
    with a stubbed chat backend) and assert the learning layer captured:
    raw query verbatim, AI interpretation verbatim, model stamp, and
    source='ai_inference' rows with evidence_count ≥ 1."""
    from datetime import date, timedelta

    from yonder.config import get_settings
    from yonder.grok import GrokClient

    raw_query = "  Somewhere CHEAP with street food, wild nights!! "
    interp = "Bangkok: cheap street food and chaotic nightlife"
    depart = (date.today() + timedelta(days=40)).isoformat()

    async def fake_chat(self, system, user, *, temperature=0.2):
        return json.dumps(
            {
                "origin": "YVR",
                "destination": "BKK",
                "depart_date": depart,
                "return_date": None,
                "currency": "CAD",
                "nonstop_only": False,
                "intent_summary": interp,
                "assumptions": [],
            }
        )

    monkeypatch.setattr(GrokClient, "_chat", fake_chat)
    monkeypatch.setattr(GrokClient, "is_configured", lambda self: True)
    monkeypatch.setattr(
        GrokClient, "model_source_label", lambda self: "Grok (Server)"
    )

    settings = get_settings()
    async with GrokClient(settings) as grok:
        trip = await grok.parse_natural_language(
            raw_query, default_currency="CAD", default_origin="YVR",
            use_cache=False,
        )
    assert trip.destination == "BKK"

    # Capture is fire-and-forget on a daemon thread — poll for the row.
    rows = []
    deadline = time.time() + 10.0
    while time.time() < deadline:
        rows = get_interpretations(dest_iata="BKK")
        if rows:
            break
        time.sleep(0.05)
    assert rows, "interpretation row never appeared after the search flow"

    r = rows[0]
    assert r["raw_query"] == raw_query            # verbatim, as typed
    assert r["interpretation"] == interp          # AI's verbatim summary
    assert r["model_source"] == "Grok (Server)"   # model version stamp
    assert r["trip_shape"] == "escape"
    assert r["origin_iata"] == "YVR" and r["dest_iata"] == "BKK"
    tags = r["attribute_tags"]
    assert "budget" in tags and "food" in tags    # cheap→budget, food

    # dest_attributes rows carry source='ai_inference', evidence_count ≥ 1
    for attr in tags:
        row = _dest_row(isolated_db, "BKK", attr, "ai_inference")
        assert row is not None, f"missing ai_inference row for {attr}"
        assert row["evidence_count"] >= 1
        assert row["confidence"] > 0


# ── 2. Feedback reinforcement via the real web endpoint ─────────────────────

def test_feedback_endpoint_reinforces_without_touching_ai_rows(
    isolated_db, monkeypatch
):
    import yonder.web as web_module

    knowledge.record_interpretation(
        vibe="chaos", raw_query="cheap chaos", origin="YVR", dest_iata="BKK",
        interpretation="cheap street food chaos", tags=["food", "budget"],
    )
    ai_before = dict(_dest_row(isolated_db, "BKK", "food", "ai_inference"))

    client = TestClient(web_module.app, raise_server_exceptions=True)
    sess_up = uuid.uuid4().hex[:32]

    # ── Thumbs up ────────────────────────────────────────────────────────
    resp = client.post(
        "/api/result-feedback",
        json={"direction": "up", "vibe": "chaos", "dest_iata": "BKK",
              "query": "cheap chaos"},
        cookies={"yv_sess": sess_up},
    )
    assert resp.status_code == 200 and resp.json().get("ok")

    ub = _dest_row(isolated_db, "BKK", "food", "user_behavior")
    assert ub is not None, "thumbs-up must create a user_behavior row"
    assert ub["evidence_count"] == 1 and ub["contradiction_count"] == 0
    assert ub["confidence"] > 0

    # The ai_inference row is untouched by a thumbs-up
    ai_after_up = dict(_dest_row(isolated_db, "BKK", "food", "ai_inference"))
    for col in ("weight", "confidence", "evidence_count",
                "contradiction_count", "last_reinforced_at"):
        assert ai_after_up[col] == ai_before[col], f"{col} changed on thumbs-up"

    score_after_up = effective_attributes(dest_iata="BKK")["food"]

    # ── Thumbs down (new session so the vote isn't deduped) ─────────────
    resp = client.post(
        "/api/result-feedback",
        json={"direction": "down", "vibe": "chaos", "dest_iata": "BKK",
              "query": "cheap chaos"},
        cookies={"yv_sess": uuid.uuid4().hex[:32]},
    )
    assert resp.status_code == 200 and resp.json().get("ok")

    ub2 = _dest_row(isolated_db, "BKK", "food", "user_behavior")
    assert ub2["contradiction_count"] == 1
    assert ub2["confidence"] < ub["confidence"], (
        "thumbs-down must lower user_behavior confidence"
    )
    # AI's original claim contradicted too
    ai_after_down = _dest_row(isolated_db, "BKK", "food", "ai_inference")
    assert ai_after_down["contradiction_count"] == ai_before["contradiction_count"] + 1
    assert ai_after_down["confidence"] < ai_before["confidence"]

    # Effective combined score drops
    assert effective_attributes(dest_iata="BKK")["food"] < score_after_up

    # Evidence links tie both votes back to their result_feedback rows
    ev = evidence_for(subject_kind="dest", subject="BKK", attribute="food")
    fb_ids = [e["evidence_id"] for e in ev if e["evidence_kind"] == "feedback"]
    assert len(fb_ids) >= 2
    with isolated_db() as conn:
        for fid in fb_ids:
            row = conn.execute(
                "SELECT direction FROM result_feedback WHERE id=%s", (fid,)
            ).fetchone()
            assert row is not None, "feedback evidence_id must exist in result_feedback"
    dirs = set()
    with isolated_db() as conn:
        for fid in fb_ids:
            dirs.add(conn.execute(
                "SELECT direction FROM result_feedback WHERE id=%s", (fid,)
            ).fetchone()["direction"])
    assert dirs == {"up", "down"}


# ── 3. Read path: per-source separation + full trust ordering ───────────────

def test_per_source_rows_stay_separate_and_trust_ordering_applies(isolated_db):
    now = time.time()
    conf = compute_confidence(1, 0, now, now=now)
    with isolated_db() as conn:
        for source in ("editorial", "user_behavior", "external", "ai_inference"):
            conn.execute(
                "INSERT INTO dest_attributes (dest_iata, attribute, source, "
                "weight, confidence, evidence_count, contradiction_count, "
                "last_reinforced_at, updated_at) "
                "VALUES ('KIX','spiritual',%s,1.0,%s,1,0,%s,%s)",
                (source, conf, now, now),
            )

    # Rows stay separate when queried
    with isolated_db() as conn:
        rows = conn.execute(
            "SELECT source, weight, confidence FROM dest_attributes "
            "WHERE dest_iata='KIX' AND attribute='spiritual'"
        ).fetchall()
    assert len(rows) == 4
    assert {r["source"] for r in rows} == set(SOURCE_MULTIPLIERS)

    # Trust ordering: editorial > user_behavior > external > ai_inference
    m = SOURCE_MULTIPLIERS
    assert m["editorial"] > m["user_behavior"] > m["external"] > m["ai_inference"]

    # Combined score = Σ weight×confidence×multiplier over all four rows
    score = effective_attributes(dest_iata="KIX")["spiritual"]
    assert score == pytest.approx(conf * sum(m.values()), rel=1e-3)

    # At equal weight/confidence, each source's contribution follows the
    # trust ordering (verified by removing one source at a time).
    contributions = {}
    for source in m:
        with isolated_db() as conn:
            conn.execute(
                "DELETE FROM dest_attributes WHERE dest_iata='KIX' "
                "AND attribute='spiritual' AND source<>%s", (source,),
            )
            conn.commit()
        contributions[source] = effective_attributes(dest_iata="KIX")["spiritual"]
        with isolated_db() as conn:
            conn.execute(
                "DELETE FROM dest_attributes WHERE dest_iata='KIX'")
            for s2 in m:
                conn.execute(
                    "INSERT INTO dest_attributes (dest_iata, attribute, source, "
                    "weight, confidence, evidence_count, contradiction_count, "
                    "last_reinforced_at, updated_at) "
                    "VALUES ('KIX','spiritual',%s,1.0,%s,1,0,%s,%s)",
                    (s2, conf, now, now),
                )
    assert (
        contributions["editorial"] > contributions["user_behavior"]
        > contributions["external"] > contributions["ai_inference"]
    )


# ── 4. Evidence links: trace an aggregate back to every raw row ─────────────

def test_evidence_links_trace_aggregate_to_raw_rows(isolated_db):
    vid1 = knowledge.record_interpretation(
        vibe="chaos", raw_query="cheap food trip", origin="YVR",
        dest_iata="BKK", interpretation="street food heaven", tags=["food"],
    )
    vid2 = knowledge.record_interpretation(
        vibe="chaos", raw_query="another foodie ask", origin="YVR",
        dest_iata="BKK", interpretation="hawker stalls all night", tags=["food"],
    )
    assert reinforce_from_feedback(
        vibe="chaos", dest_iata="BKK", direction="up", feedback_id="fb-x"
    )

    # Aggregate row says evidence_count=2 for ai_inference…
    ai = _dest_row(isolated_db, "BKK", "food", "ai_inference")
    assert ai["evidence_count"] == 2

    # …and the evidence table traces to exactly those interpretation rows
    ev = evidence_for(subject_kind="dest", subject="BKK", attribute="food")
    interp_ids = {e["evidence_id"] for e in ev
                  if e["evidence_kind"] == "interpretation"
                  and e["source"] == "ai_inference"}
    assert interp_ids == {vid1, vid2}

    # Each linked interpretation id resolves to a real archive row
    with isolated_db() as conn:
        for iid in interp_ids:
            assert conn.execute(
                "SELECT 1 FROM vibe_interpretations WHERE id=%s", (iid,)
            ).fetchone()

    # Feedback link traces the user_behavior aggregate too
    fb = [e for e in ev if e["evidence_kind"] == "feedback"]
    assert fb and fb[0]["evidence_id"] == "fb-x" and fb[0]["source"] == "user_behavior"

    # Vibe-side evidence mirrors the dest side
    vev = evidence_for(subject_kind="vibe", subject="chaos", attribute="food")
    assert {e["evidence_id"] for e in vev if e["evidence_kind"] == "interpretation"} == {vid1, vid2}


@pytest.fixture
def anyio_backend():
    return "asyncio"
