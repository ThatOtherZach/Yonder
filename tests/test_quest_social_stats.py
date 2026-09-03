"""Canonical Quest bookmark and feedback social proof."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import yonder.feedback as feedback
import yonder.saved as saved
import yonder.share as share
import yonder.web as web


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _quest(entry: str, exit_: str):
    return saved.ensure_global_quest(
        {
            "kind": "quest",
            "title": f"{entry} to {exit_}",
            "entry_iata": entry,
            "exit_iata": exit_,
            "entry_city": entry,
            "exit_city": exit_,
            "depart_date": "2026-11-01",
            "outbound_date": "2026-11-10",
            "overland_narrative": f"Travel from {entry} to {exit_}.",
        },
        trip_meta={"origin": "YVR", "vibe": "adventure", "prompt": "Go overland"},
        origin="YVR",
    )


def test_exact_quest_votes_dedup_without_colliding_with_another_quest():
    first = _quest("LIS", "OPO")
    second = _quest("LIS", "MAD")

    assert feedback.record_feedback(
        direction="up", vibe="adventure", dest_iata="LIS",
        session_hash="same-browser", quest_saved_id=first.id,
    )
    assert feedback.record_feedback(
        direction="up", vibe="altered", dest_iata="ZZZ",
        session_hash="same-browser", quest_saved_id=first.id,
    ) == ""
    assert feedback.record_feedback(
        direction="up", vibe="adventure", dest_iata="LIS",
        session_hash="same-browser", quest_saved_id=second.id,
    )

    assert saved.quest_social_stats([first.id, second.id]) == {
        first.id: {
            "save_count": 0, "up_count": 1, "down_count": 0,
            "vote_count": 1, "positive_pct": 100,
        },
        second.id: {
            "save_count": 0, "up_count": 1, "down_count": 0,
            "vote_count": 1, "positive_pct": 100,
        },
    }


def test_batched_stats_count_bookmarks_and_distinguish_no_feedback_from_zero_percent():
    liked = _quest("NRT", "KIX")
    disliked = _quest("HAN", "BKK")
    empty = _quest("CDG", "AMS")
    for owner in ("one", "two", "three"):
        assert saved.bookmark_quest(liked.id, owner_sess=owner)
    assert saved.bookmark_quest(disliked.id, owner_sess="one")
    assert feedback.record_feedback(
        direction="up", vibe="culture", dest_iata="NRT",
        session_hash="a", quest_saved_id=liked.id,
    )
    assert feedback.record_feedback(
        direction="down", vibe="culture", dest_iata="NRT",
        session_hash="b", quest_saved_id=liked.id,
    )
    assert feedback.record_feedback(
        direction="down", vibe="adventure", dest_iata="HAN",
        session_hash="c", quest_saved_id=disliked.id,
    )

    stats = saved.quest_social_stats([liked.id, disliked.id, empty.id])
    assert stats[liked.id]["save_count"] == 3
    assert stats[liked.id]["positive_pct"] == 50
    assert stats[liked.id]["vote_count"] == 2
    assert stats[disliked.id]["positive_pct"] == 0
    assert stats[disliked.id]["vote_count"] == 1
    assert stats[empty.id]["positive_pct"] is None
    assert stats[empty.id]["vote_count"] == 0


def test_quest_library_puts_stats_in_actions_without_save_control(monkeypatch):
    quest = _quest("LHR", "EDI")
    calls: list[list[str]] = []
    real = saved.quest_social_stats

    def tracked(ids):
        calls.append(list(ids))
        return real(ids)

    monkeypatch.setattr(saved, "quest_social_stats", tracked)
    client = TestClient(web.app, raise_server_exceptions=True)
    response = client.get("/quests?origin=YVR")

    assert response.status_code == 200
    assert calls == [[quest.id]]
    assert "★ 0 saves" in response.text
    assert "No feedback yet" in response.text
    assert "0% positive" not in response.text
    assert response.text.count('class="quest-social-proof"') == 1
    actions = response.text.split('class="bp-actions"', 1)[1].split("</div>", 2)[0]
    assert 'class="quest-social-proof"' in actions
    assert "Open Quest" in actions
    assert "btn-ql-save" not in response.text
    assert "✓ Saved" not in response.text


@pytest.mark.parametrize(
    ("bookmarks", "votes", "save_label", "feedback_label"),
    [
        (1, ("up",), "★ 1 save", "100% positive</strong> (1 vote)"),
        (2, ("down",), "★ 2 saves", "0% positive</strong> (1 vote)"),
        (0, ("up", "down"), "★ 0 saves", "50% positive</strong> (2 votes)"),
    ],
)
def test_quest_library_action_stats_cover_counts_and_feedback(
    bookmarks, votes, save_label, feedback_label
):
    quest = _quest("LIS", "OPO")
    for number in range(bookmarks):
        assert saved.bookmark_quest(quest.id, owner_sess=f"fan-{number}")
    for number, direction in enumerate(votes):
        assert feedback.record_feedback(
            direction=direction,
            vibe="adventure",
            dest_iata="LIS",
            session_hash=f"voter-{number}",
            quest_saved_id=quest.id,
        )

    response = TestClient(web.app, raise_server_exceptions=True).get(
        "/quests?origin=YVR"
    )

    assert response.status_code == 200
    assert save_label in response.text
    assert feedback_label in response.text
    assert response.text.count('class="quest-social-proof"') == 1
    assert "btn-ql-save" not in response.text


def test_quest_library_social_sorts_rank_cards_and_departure_board():
    best_rated = _quest("LIS", "OPO")
    most_saved = _quest("HAN", "BKK")
    unrated = _quest("CDG", "AMS")

    assert saved.bookmark_quest(best_rated.id, owner_sess="one")
    for owner in ("one", "two", "three"):
        assert saved.bookmark_quest(most_saved.id, owner_sess=owner)

    assert feedback.record_feedback(
        direction="up",
        vibe="adventure",
        dest_iata="LIS",
        session_hash="lis-fan",
        quest_saved_id=best_rated.id,
    )
    for session_hash, direction in (("han-fan", "up"), ("han-critic", "down")):
        assert feedback.record_feedback(
            direction=direction,
            vibe="adventure",
            dest_iata="HAN",
            session_hash=session_hash,
            quest_saved_id=most_saved.id,
        )

    assert [q.id for q in saved.list_quests(sort_by="most_saved")] == [
        most_saved.id,
        best_rated.id,
        unrated.id,
    ]
    assert [q.id for q in saved.list_quests(sort_by="top_rated")] == [
        best_rated.id,
        most_saved.id,
        unrated.id,
    ]
    assert [r["entry"] for r in saved.top_quest_routes(sort_by="top_rated")] == [
        "LIS",
        "HAN",
        "CDG",
    ]

    response = TestClient(web.app, raise_server_exceptions=True).get(
        "/quests?origin=YVR&sort=most_saved"
    )
    assert response.status_code == 200
    assert '>Most Saved</a>' in response.text
    assert 'aria-current="page">Most Saved</a>' in response.text
    assert 'name="sort" value="most_saved"' in response.text
    cards = response.text.split('class="quest-grid"', 1)[1]
    assert cards.index("HAN") < cards.index("LIS") < cards.index("CDG")

    top_rated = TestClient(web.app, raise_server_exceptions=True).get(
        "/quests?origin=YVR&sort=top_rated"
    )
    assert top_rated.status_code == 200
    assert 'aria-current="page">Top Rated</a>' in top_rated.text
    board_json = top_rated.text.split(
        '<script id="fb-data" type="application/json">', 1
    )[1].split("</script>", 1)[0]
    assert [route["entry"] for route in json.loads(board_json)] == [
        "LIS",
        "HAN",
        "CDG",
    ]


def test_feedback_endpoint_accepts_only_a_real_canonical_quest_id(monkeypatch):
    quest = _quest("SFO", "LAX")
    client = TestClient(web.app, raise_server_exceptions=True)
    monkeypatch.setattr("yonder.knowledge.reinforce_from_feedback", lambda **_: True)
    monkeypatch.setattr("yonder.vibe_signals.upsert_signal", lambda **_: None)

    response = client.post(
        "/api/result-feedback",
        json={
            "direction": "up", "vibe": "adventure", "dest_iata": "SFO",
            "query": "California rail", "quest_saved_id": quest.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["quest_social_stats"]["positive_pct"] == 100
    with feedback.get_conn() as conn:
        row = conn.execute(
            "SELECT quest_saved_id, vibe, dest_iata, query FROM result_feedback"
        ).fetchone()
    assert row["quest_saved_id"] == quest.id
    assert row["vibe"] == "adventure"
    assert row["dest_iata"] == "SFO"
    assert row["query"] == "go overland"

    bypass = client.post(
        "/api/result-feedback",
        json={
            "direction": "up", "vibe": "different", "dest_iata": "ZZZ",
            "query": "Different", "quest_saved_id": quest.id,
        },
    )
    assert bypass.status_code == 200
    assert bypass.json()["deduped"] is True
    assert bypass.json()["quest_social_stats"]["vote_count"] == 1

    invalid = client.post(
        "/api/result-feedback",
        json={
            "direction": "down", "vibe": "adventure", "dest_iata": "SFO",
            "query": "California rail", "quest_saved_id": "not-a-quest",
        },
    )
    assert invalid.status_code == 200
    assert "quest_social_stats" not in invalid.json()
    with feedback.get_conn() as conn:
        rows = conn.execute(
            "SELECT quest_saved_id FROM result_feedback ORDER BY created_at"
        ).fetchall()
    assert [row["quest_saved_id"] for row in rows] == [quest.id, None]


def test_share_cannot_claim_another_quests_canonical_identity():
    claimed = _quest("SEA", "PDX")
    actual = _quest("BOS", "NYC")
    forged = share.create_share(
        kind="quest",
        title="Forged",
        payload={
            "idea": actual.itinerary,
            "home_iata": "YVR",
            "trip_meta": {**actual.trip_meta, "saved_id": claimed.id},
        },
    )
    client = TestClient(web.app, raise_server_exceptions=True)
    page = client.get(forged.path)

    assert page.status_code == 200
    assert f'data-quest-saved-id="{actual.id}"' in page.text
    assert f'data-quest-saved-id="{claimed.id}"' not in page.text


def test_resolved_share_renders_same_exact_stats_and_legacy_share_stays_unscored():
    quest = _quest("MEX", "GDL")
    saved.bookmark_quest(quest.id, owner_sess="fan")
    feedback.record_feedback(
        direction="down", vibe="adventure", dest_iata="MEX",
        session_hash="critic", quest_saved_id=quest.id,
    )
    client = TestClient(web.app, raise_server_exceptions=True)

    resolved = share.create_share(
        kind="quest",
        title=quest.title,
        payload={
            "idea": quest.itinerary,
            "home_iata": "YVR",
            "trip_meta": {**quest.trip_meta, "saved_id": quest.id},
        },
    )
    page = client.get(resolved.path)
    assert page.status_code == 200
    assert "★ 1 save" in page.text
    assert "0% positive" in page.text
    assert "(1 vote)" in page.text
    assert f'data-quest-saved-id="{quest.id}"' in page.text

    legacy = share.create_share(
        kind="quest",
        title="Unresolved legacy Quest",
        payload={
            "idea": {
                "kind": "quest", "entry_iata": "XXX", "exit_iata": "YYY",
                "entry_city": "Unknown", "exit_city": "Elsewhere",
                "inbound_fare_missing": True, "outbound_fare_missing": True,
            },
            "home_iata": "YVR",
            "trip_meta": {"vibe": "adventure", "prompt": "Old share"},
        },
    )
    legacy_page = client.get(legacy.path)
    assert legacy_page.status_code == 200
    assert '<div class="quest-social-proof"' not in legacy_page.text
    assert 'data-quest-saved-id=""' in legacy_page.text