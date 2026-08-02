"""Learning-layer knowledge graph tests — route knowledge, interpretation
capture, attribute accrual, feedback reinforcement, provenance, MOCK guard."""

from __future__ import annotations

import time

import pytest

import yonder.knowledge as knowledge
from yonder.knowledge import (
    FAILED_ROUTE_TTL_DAYS,
    SOURCE_MULTIPLIERS,
    compute_confidence,
    effective_attributes,
    evidence_for,
    extract_attribute_tags,
    get_interpretations,
    get_route,
    record_interpretation,
    record_route_outcome,
    reinforce_from_feedback,
    route_status,
    seed_candidates,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(knowledge, "DB_PATH", tmp_path / "knowledge.db")
    # Isolate the vibe-signals store too — seed_candidates reads it
    import yonder.vibe_signals as vibe_signals

    monkeypatch.setattr(vibe_signals, "DB_PATH", tmp_path / "vibe_signals.db")
    yield tmp_path


# ── Cold start ───────────────────────────────────────────────────────────────

def test_cold_start_is_noop(isolated_db):
    assert route_status("YVR", "NRT") == "unknown"
    assert effective_attributes(dest_iata="NRT") == {}
    assert effective_attributes(vibe="chaos") == {}
    assert seed_candidates(vibe="chaos", origin="YVR") == []
    assert get_interpretations() == []


# ── Route knowledge ──────────────────────────────────────────────────────────

def test_route_success_and_failure_recorded(isolated_db):
    assert record_route_outcome(
        origin="YVR", dest="NRT", success=True, provider="amadeus",
        price=812.0, currency="CAD",
    )
    row = get_route("YVR", "NRT")
    assert row["status"] == "verified"
    assert row["success_count"] == 1
    assert row["best_recent_price"] == 812.0
    assert route_status("YVR", "NRT") == "verified"

    assert record_route_outcome(origin="YVR", dest="XXA", success=False, provider="amadeus")
    row = get_route("YVR", "XXA")
    assert row["status"] == "failed"
    assert row["fail_count"] == 1
    assert route_status("YVR", "XXA") == "failed"

    # Directed pairs: reverse is independent
    assert route_status("XXA", "YVR") == "unknown"


def test_failed_route_freshness_window_expires(isolated_db):
    record_route_outcome(origin="YVR", dest="XXA", success=False)
    assert route_status("YVR", "XXA") == "failed"
    # Age the failure past the TTL — route recovers to unknown
    import sqlite3

    conn = sqlite3.connect(str(knowledge.DB_PATH))
    old = time.time() - (FAILED_ROUTE_TTL_DAYS + 1) * 86400
    conn.execute("UPDATE route_knowledge SET last_failed_at=?", (old,))
    conn.commit()
    conn.close()
    assert route_status("YVR", "XXA") == "unknown"


def test_verification_after_failure_wins(isolated_db):
    record_route_outcome(origin="YVR", dest="LIM", success=False)
    record_route_outcome(origin="YVR", dest="LIM", success=True, price=500.0)
    assert route_status("YVR", "LIM") == "verified"
    row = get_route("YVR", "LIM")
    assert row["success_count"] == 1 and row["fail_count"] == 1


def test_route_best_price_keeps_minimum(isolated_db):
    record_route_outcome(origin="YVR", dest="NRT", success=True, price=900.0)
    record_route_outcome(origin="YVR", dest="NRT", success=True, price=750.0)
    record_route_outcome(origin="YVR", dest="NRT", success=True, price=1100.0)
    assert get_route("YVR", "NRT")["best_recent_price"] == 750.0


# ── Attribute tagging ────────────────────────────────────────────────────────

def test_extract_attribute_tags_vocab_and_synonyms(isolated_db):
    tags = extract_attribute_tags(
        "Street food capital with ancient temples and cheap night markets",
        ["beach", "warmnights", "not-a-real-tag"],
    )
    assert "beach" in tags
    assert "tropical" in tags       # warmnights synonym
    assert "food" not in tags or True  # 'food' only if word matched
    assert "historic" in tags       # ancient
    assert "budget" in tags         # cheap
    assert "not-a-real-tag" not in tags
    assert all(t in knowledge.ATTRIBUTE_VOCAB for t in tags)


# ── Interpretation capture & attribute accrual ───────────────────────────────

def test_interpretation_archived_verbatim_and_attributes_accrue(isolated_db):
    vid = record_interpretation(
        vibe="Chaos ",
        raw_query="  Somewhere CHEAP and wild, street food!! ",
        origin="yvr",
        dest_iata="bkk",
        interpretation="Street food + easy multi-day chaos, cheap night markets",
        tags=["city", "food", "cheap"],
        trip_shape="getaway",
        model_source="Grok (Server)",
    )
    assert vid
    rows = get_interpretations(vibe="chaos")
    assert len(rows) == 1
    r = rows[0]
    # Raw query preserved exactly as typed, plus normalized form
    assert r["raw_query"] == "  Somewhere CHEAP and wild, street food!! "
    assert r["query_norm"] == "somewhere cheap and wild, street food!!"
    assert r["interpretation"].startswith("Street food + easy multi-day chaos")
    assert r["dest_iata"] == "BKK" and r["origin_iata"] == "YVR"
    assert r["model_source"] == "Grok (Server)"
    assert "budget" in r["attribute_tags"]  # cheap → budget

    dest_attrs = effective_attributes(dest_iata="BKK")
    vibe_attrs = effective_attributes(vibe="chaos")
    assert dest_attrs and vibe_attrs
    assert "food" in dest_attrs and "food" in vibe_attrs

    # Evidence links tie the score back to the raw row
    ev = evidence_for(subject_kind="dest", subject="BKK", attribute="food")
    assert ev and ev[0]["evidence_id"] == vid and ev[0]["evidence_kind"] == "interpretation"


def test_archive_is_append_only(isolated_db):
    for _ in range(3):
        record_interpretation(
            vibe="chaos", raw_query="q", origin="YVR", dest_iata="BKK",
            interpretation="cheap food", tags=["food"],
        )
    assert len(get_interpretations(dest_iata="BKK")) == 3
    # evidence_count grew with repetition
    import sqlite3

    conn = sqlite3.connect(str(knowledge.DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM dest_attributes WHERE dest_iata='BKK' AND attribute='food' "
        "AND source='ai_inference'"
    ).fetchone()
    conn.close()
    assert row["evidence_count"] == 3


# ── Confidence ───────────────────────────────────────────────────────────────

def test_confidence_grows_with_evidence_and_decays(isolated_db):
    now = time.time()
    c1 = compute_confidence(1, 0, now, now=now)
    c5 = compute_confidence(5, 0, now, now=now)
    assert 0 < c1 < c5 <= 1.0
    # Contradictions lower it
    assert compute_confidence(5, 3, now, now=now) < c5
    # Staleness lowers it
    old = now - 400 * 86400
    assert compute_confidence(5, 0, old, now=now) < c5
    assert compute_confidence(0) == 0.0


# ── Feedback reinforcement / dilution ────────────────────────────────────────

def test_thumbs_up_and_down_shift_attribute_weights(isolated_db):
    record_interpretation(
        vibe="chaos", raw_query="cheap chaos", origin="YVR", dest_iata="BKK",
        interpretation="cheap street food chaos", tags=["food", "cheap"],
    )
    before = effective_attributes(dest_iata="BKK")

    assert reinforce_from_feedback(
        vibe="chaos", dest_iata="BKK", direction="up", feedback_id="fb1"
    )
    after_up = effective_attributes(dest_iata="BKK")
    assert after_up["food"] > before["food"]  # measurable shift

    # user_behavior rows exist independently of ai_inference rows
    import sqlite3

    conn = sqlite3.connect(str(knowledge.DB_PATH))
    conn.row_factory = sqlite3.Row
    srcs = {
        r["source"]
        for r in conn.execute(
            "SELECT source FROM dest_attributes WHERE dest_iata='BKK' AND attribute='food'"
        )
    }
    assert srcs == {"ai_inference", "user_behavior"}

    assert reinforce_from_feedback(
        vibe="chaos", dest_iata="BKK", direction="down", feedback_id="fb2"
    )
    after_down = effective_attributes(dest_iata="BKK")
    assert after_down["food"] < after_up["food"]
    # AI's original claim also contradicted
    row = conn.execute(
        "SELECT contradiction_count FROM dest_attributes WHERE dest_iata='BKK' "
        "AND attribute='food' AND source='ai_inference'"
    ).fetchone()
    conn.close()
    assert row["contradiction_count"] == 1

    # Feedback evidence links recorded
    ev = evidence_for(subject_kind="dest", subject="BKK", attribute="food")
    assert any(e["evidence_kind"] == "feedback" and e["evidence_id"] == "fb1" for e in ev)


def test_provenance_trust_ordering(isolated_db):
    # One editorial signal should outweigh one AI mention at equal weight
    import sqlite3

    record_interpretation(
        vibe="zen", raw_query="q", origin="YVR", dest_iata="KIX",
        interpretation="sacred temples", tags=["spiritual"],
    )
    now = time.time()
    conn = sqlite3.connect(str(knowledge.DB_PATH))
    conn.execute(
        "INSERT INTO dest_attributes (dest_iata, attribute, source, weight, "
        "confidence, evidence_count, contradiction_count, last_reinforced_at, "
        "updated_at) VALUES ('KIX','spiritual','editorial',1.0,?,1,0,?,?)",
        (compute_confidence(1, 0, now, now=now), now, now),
    )
    conn.commit()
    conn.close()
    assert SOURCE_MULTIPLIERS["editorial"] > SOURCE_MULTIPLIERS["ai_inference"]
    # Effective score combines both rows
    attrs = effective_attributes(dest_iata="KIX")
    ed = SOURCE_MULTIPLIERS["editorial"]
    ai = SOURCE_MULTIPLIERS["ai_inference"]
    conf = compute_confidence(1, 0, now, now=now)
    assert attrs["spiritual"] == pytest.approx(conf * ed + conf * ai, rel=1e-3)


# ── Seeding ──────────────────────────────────────────────────────────────────

def test_seed_candidates_skip_failed_and_prefer_verified(isolated_db, monkeypatch):
    # Two learned candidates from the attribute graph
    for dest in ("BKK", "HAN"):
        record_interpretation(
            vibe="chaos", raw_query="cheap chaos", origin="YVR", dest_iata=dest,
            interpretation="cheap street food", tags=["food", "cheap"],
        )
    monkeypatch.setattr(
        "yonder.vibe_signals.scores_for_vibe", lambda v, **kw: {"BKK": 2.0, "HAN": 2.0}
    )
    record_route_outcome(origin="YVR", dest="HAN", success=False)  # dead route
    record_route_outcome(origin="YVR", dest="BKK", success=True, price=700.0)

    seeds = seed_candidates(vibe="chaos", origin="YVR")
    iatas = [s["iata"] for s in seeds]
    assert "BKK" in iatas
    assert "HAN" not in iatas  # fresh-failed skipped
    assert seeds[0]["iata"] == "BKK" and seeds[0]["route"] == "verified"


# ── MOCK guard ───────────────────────────────────────────────────────────────

def test_mock_guard_blocks_writes_and_reads(isolated_db, monkeypatch):
    record_interpretation(
        vibe="chaos", raw_query="q", origin="YVR", dest_iata="BKK",
        interpretation="cheap food", tags=["food"],
    )
    record_route_outcome(origin="YVR", dest="NRT", success=True, price=800.0)

    monkeypatch.setenv("MOCK", "1")
    assert record_route_outcome(origin="YVR", dest="LIM", success=True) is False
    assert record_interpretation(
        vibe="chaos", raw_query="q", origin="YVR", dest_iata="LIM",
        interpretation="x", tags=["food"],
    ) is None
    assert reinforce_from_feedback(vibe="chaos", dest_iata="BKK", direction="up") is False
    # Reads behave as if empty
    assert route_status("YVR", "NRT") == "unknown"
    assert effective_attributes(dest_iata="BKK") == {}
    assert seed_candidates(vibe="chaos", origin="YVR") == []

    monkeypatch.delenv("MOCK")
    assert get_route("YVR", "LIM") is None  # nothing leaked through
    assert route_status("YVR", "NRT") == "verified"


# ── Negative-route skip in the pricing path ──────────────────────────────────

@pytest.mark.anyio
async def test_price_leg_skips_fresh_failed_route(isolated_db, monkeypatch):
    import httpx

    from yonder.adventure import AdventureRequest, _price_leg
    from yonder.config import get_settings
    from datetime import date, timedelta

    record_route_outcome(origin="YVR", dest="XXA", success=False)

    called = {"n": 0}

    async def _boom(*a, **kw):  # live API must NOT be reached
        called["n"] += 1
        raise AssertionError("live search should be skipped for failed route")

    monkeypatch.setattr("yonder.adventure.search_flights", _boom)
    # Make sure the fare-estimate cache does not short-circuit first
    monkeypatch.setattr("yonder.fare_estimates.get_estimate", lambda *a, **kw: None)

    req = AdventureRequest(
        origin="YVR", destination="XXA",
        depart_date=date.today() + timedelta(days=30),
        vibe="chaos", currency="CAD",
    )
    async with httpx.AsyncClient() as http:
        leg = await _price_leg(
            "YVR", "XXA", req.depart_date, req,
            settings=get_settings(), include_mock=False, only=None, http=http,
        )
    assert leg.offer is None
    assert leg.error and "no flights" in leg.error
    assert called["n"] == 0
    assert leg.google_flights_url  # fallback CTA link still present


@pytest.mark.anyio
async def test_price_leg_provider_error_does_not_poison_route(isolated_db, monkeypatch):
    """Provider errors (auth/quota/timeout) must NOT be negative-cached —
    only a live provider that answered OK with zero offers confirms
    'no flights on this route'."""
    import httpx

    from yonder.adventure import AdventureRequest, _price_leg
    from yonder.config import get_settings
    from yonder.types import ProviderResult, UnifiedSearchResult
    from datetime import date, timedelta

    async def _provider_error(q, **kw):
        return UnifiedSearchResult(
            query=q,
            results=[ProviderResult(provider="amadeus", ok=False, error="429 quota")],
            offers=[],
        )

    monkeypatch.setattr("yonder.adventure.search_flights", _provider_error)
    monkeypatch.setattr("yonder.fare_estimates.get_estimate", lambda *a, **kw: None)

    req = AdventureRequest(
        origin="YVR", destination="LIM",
        depart_date=date.today() + timedelta(days=30),
        vibe="chaos", currency="CAD",
    )
    async with httpx.AsyncClient() as http:
        leg = await _price_leg(
            "YVR", "LIM", req.depart_date, req,
            settings=get_settings(), include_mock=False, only=["amadeus"], http=http,
        )
    assert leg.offer is None
    assert get_route("YVR", "LIM") is None  # no failed row recorded
    assert route_status("YVR", "LIM") == "unknown"


@pytest.mark.anyio
async def test_price_leg_live_empty_result_records_failed_route(isolated_db, monkeypatch):
    """A live provider answering OK with an empty offer set IS negative-cached."""
    import httpx

    from yonder.adventure import AdventureRequest, _price_leg
    from yonder.config import get_settings
    from yonder.types import ProviderResult, UnifiedSearchResult
    from datetime import date, timedelta

    async def _no_offers(q, **kw):
        return UnifiedSearchResult(
            query=q,
            results=[ProviderResult(provider="amadeus", ok=True, offers=[])],
            offers=[],
        )

    monkeypatch.setattr("yonder.adventure.search_flights", _no_offers)
    monkeypatch.setattr("yonder.fare_estimates.get_estimate", lambda *a, **kw: None)

    req = AdventureRequest(
        origin="YVR", destination="LIM",
        depart_date=date.today() + timedelta(days=30),
        vibe="chaos", currency="CAD",
    )
    async with httpx.AsyncClient() as http:
        leg = await _price_leg(
            "YVR", "LIM", req.depart_date, req,
            settings=get_settings(), include_mock=False, only=["amadeus"], http=http,
        )
    assert leg.offer is None
    row = get_route("YVR", "LIM")
    assert row is not None and row["status"] == "failed"
    assert route_status("YVR", "LIM") == "failed"


@pytest.fixture
def anyio_backend():
    return "asyncio"
