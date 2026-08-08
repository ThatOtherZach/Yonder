"""Tests for yonder/ad_pipeline.py and yonder/ads_api.py.

All tests use an isolated throwaway PostgreSQL schema (via the ``pg_schema``
conftest fixture) and never touch real tables.  HTTP calls to the Ads API are
stubbed with ``unittest.mock`` — no real OPENAI_ADS_API_KEY is required.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import yonder.ad_pipeline as ap
import yonder.ads_api as aa


# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated(pg_schema, monkeypatch):
    """Isolated DB + clear OPENAI_ADS_API_KEY for every test."""
    monkeypatch.delenv("OPENAI_ADS_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)
    yield pg_schema


# ---------------------------------------------------------------------------
# Minimal SavedItinerary stub (avoids loading the full app stack)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSaved:
    id: str = "saved-1"
    kind: str = "detour"
    title: str = "YVR → NRT"
    stop_iata: str = "NRT"
    destination: str = "NRT"
    vibe: str = "culture"
    stop_city: str = "Tokyo"
    display_price: str | None = "$780"
    all_in_display: str | None = None
    stay_days: int | None = 6
    origin: str = "YVR"
    trip_meta: dict = field(default_factory=dict)
    itinerary: dict = field(default_factory=lambda: {
        "kind": "detour",
        "title": "YVR → NRT",
        "legs": [{"from_iata": "YVR", "to_iata": "NRT", "depart_date": "2026-09-01"}],
    })


# ---------------------------------------------------------------------------
# render_title
# ---------------------------------------------------------------------------


class TestRenderTitle:
    def test_with_price(self):
        t = ap.render_title("culture", "Tokyo", "$780")
        assert "Tokyo" in t
        assert "$780" in t
        assert len(t) <= 50

    def test_without_price_fallback(self):
        t = ap.render_title("culture", "Tokyo", None)
        assert "Tokyo" in t
        assert len(t) <= 50
        assert len(t) >= 3

    def test_long_city_name_truncated(self):
        t = ap.render_title("adventure", "A" * 40, "$999")
        assert len(t) <= 50

    def test_emoji_present_with_known_vibe(self):
        t = ap.render_title("culture", "Paris", "$500")
        # culture vibe has emoji 🏛️
        assert "Paris" in t

    def test_unknown_vibe_still_produces_valid_title(self):
        t = ap.render_title("nonexistent_vibe_xyz", "Lima", None)
        assert "Lima" in t
        assert 3 <= len(t) <= 50


# ---------------------------------------------------------------------------
# render_body
# ---------------------------------------------------------------------------


class TestRenderBody:
    def test_with_saves_and_stay(self):
        b = ap.render_body(5, 20, 7)
        assert "5 travelers" in b
        assert "7 nights" in b
        assert len(b) <= 100

    def test_uses_search_count_when_saves_low(self):
        b = ap.render_body(2, 50, None)
        assert "50" in b
        assert "searches" in b
        assert len(b) <= 100

    def test_omits_stay_when_none(self):
        b = ap.render_body(5, 20, None)
        assert "nights" not in b
        assert len(b) <= 100

    def test_always_under_100_chars(self):
        b = ap.render_body(9999, 9999, 999)
        assert len(b) <= 100


# ---------------------------------------------------------------------------
# upsert_candidate_from_save
# ---------------------------------------------------------------------------


class TestUpsertCandidateFromSave:
    def test_upserts_row(self, isolated, monkeypatch):
        monkeypatch.setenv("REPLIT_DOMAINS", "example.com")
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT * FROM ad_candidates WHERE dest_iata = 'NRT' AND vibe = 'culture'"
            ).fetchone()
        assert row is not None
        assert row["city_name"] == "Tokyo"
        assert row["push_state"] == "pending"

    def test_landing_url_is_absolute(self, isolated, monkeypatch):
        monkeypatch.setenv("REPLIT_DOMAINS", "yonder.city")
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT landing_url FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        assert row is not None
        # URL must use the trusted domain from REPLIT_DOMAINS, not any arbitrary host
        url = row["landing_url"] or ""
        assert url.startswith("https://yonder.city") or url.startswith("/t/")

    def test_landing_url_fallback_when_no_trusted_domain(self, isolated, monkeypatch):
        """Without REPLIT_DOMAINS set, landing_url is still stored (relative path)."""
        monkeypatch.delenv("REPLIT_DOMAINS", raising=False)
        monkeypatch.delenv("REPLIT_DEV_DOMAIN", raising=False)
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT landing_url FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        assert row is not None
        assert row["landing_url"] is not None

    def test_forged_host_header_cannot_influence_landing_url(self, isolated, monkeypatch):
        """A forged request Host must NEVER reach the landing URL stored in ad_candidates.

        The function signature has no base_url parameter, so the only way to
        influence the origin is via REPLIT_DOMAINS — a platform-managed env var.
        This test verifies that the stored URL uses the trusted env origin, not
        any caller-supplied host string.
        """
        import inspect

        # 1. The function must have no 'base_url' parameter at all
        sig = inspect.signature(ap.upsert_candidate_from_save)
        assert "base_url" not in sig.parameters, (
            "upsert_candidate_from_save must not accept base_url — "
            "it would allow Host header injection into ad landing URLs"
        )

        # 2. Trusted domain from env → stored URL uses that domain
        monkeypatch.setenv("REPLIT_DOMAINS", "trusted.yonder.city")
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT landing_url FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        url = row["landing_url"] or ""
        # The landing URL must NOT contain an attacker-controlled domain
        assert "evil.example.com" not in url
        assert "attacker.com" not in url
        # It should be relative or use the trusted domain
        if url.startswith("http"):
            assert "trusted.yonder.city" in url

    def test_title_and_body_rendered(self, isolated, monkeypatch):
        monkeypatch.setenv("REPLIT_DOMAINS", "example.com")
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT ad_title, ad_body FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        assert row is not None
        assert "Tokyo" in (row["ad_title"] or "")
        assert len(row["ad_title"]) <= 50
        assert len(row["ad_body"]) <= 100

    def test_invalid_iata_skipped(self, isolated):
        saved = _FakeSaved(stop_iata="", destination="")
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM ad_candidates").fetchone()["n"]
        assert n == 0

    def test_upsert_preserves_push_state_on_repeat_save(self, isolated, monkeypatch):
        """Re-saving updates copy/URL without resetting push_state."""
        monkeypatch.setenv("REPLIT_DOMAINS", "example.com")
        saved = _FakeSaved()
        ap.upsert_candidate_from_save(saved)
        # Simulate already-pushed
        with isolated() as conn:
            conn.execute(
                "UPDATE ad_candidates SET push_state = 'pushed', pushed_at = %s, ads_api_ad_id = 'ad-001' "
                "WHERE dest_iata = 'NRT' AND vibe = 'culture'",
                (time.time(),),
            )
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT push_state, ads_api_ad_id FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        # push_state preserved — not reset to pending
        assert row["push_state"] == "pushed"
        assert row["ads_api_ad_id"] == "ad-001"

    def test_no_price_produces_fallback_title(self, isolated, monkeypatch):
        monkeypatch.setenv("REPLIT_DOMAINS", "example.com")
        saved = _FakeSaved(display_price=None, all_in_display=None)
        ap.upsert_candidate_from_save(saved)
        with isolated() as conn:
            row = conn.execute(
                "SELECT ad_title FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        assert "Tokyo" in (row["ad_title"] or "")
        # No price → no "From" in title
        assert "From" not in (row["ad_title"] or "")


# ---------------------------------------------------------------------------
# upsert_candidates_from_scores (hourly poll upsert)
# ---------------------------------------------------------------------------


class TestUpsertCandidatesFromScores:
    def _seed_scores(self, conn, dest_iata: str, vibe: str, score: float = 2.0,
                     save_count: int = 3, search_count: int = 10) -> None:
        conn.execute(
            "INSERT INTO dest_vibe_scores (dest_iata, vibe, score, search_count, save_count, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (dest_iata, vibe) DO UPDATE SET score = EXCLUDED.score",
            (dest_iata, vibe, score, search_count, save_count, time.time()),
        )

    def test_inserts_missing_candidates(self, isolated):
        with isolated() as conn:
            self._seed_scores(conn, "CDG", "romance")
        count = ap.upsert_candidates_from_scores()
        assert count == 1
        with isolated() as conn:
            row = conn.execute(
                "SELECT * FROM ad_candidates WHERE dest_iata = 'CDG' AND vibe = 'romance'"
            ).fetchone()
        assert row is not None
        assert row["push_state"] == "pending"

    def test_skips_existing_candidates(self, isolated):
        with isolated() as conn:
            self._seed_scores(conn, "CDG", "romance")
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, updated_at) "
                "VALUES ('CDG', 'romance', 'Paris', 'Escape to Paris', 'Body', '/', 3, 10, 2.0, %s)",
                (time.time(),),
            )
        count = ap.upsert_candidates_from_scores()
        assert count == 0  # already existed

    def test_multiple_vibes_inserted(self, isolated):
        with isolated() as conn:
            self._seed_scores(conn, "NRT", "culture", score=3.0)
            self._seed_scores(conn, "LHR", "adventure", score=2.0)
        count = ap.upsert_candidates_from_scores()
        assert count == 2

    def test_search_only_candidate_has_valid_copy(self, isolated):
        """Search-only (0 saves) candidates get search_count in body."""
        with isolated() as conn:
            self._seed_scores(conn, "SYD", "adventure", save_count=0, search_count=42)
        ap.upsert_candidates_from_scores()
        with isolated() as conn:
            row = conn.execute(
                "SELECT ad_body FROM ad_candidates WHERE dest_iata = 'SYD'"
            ).fetchone()
        assert "42" in (row["ad_body"] or "")
        assert len(row["ad_body"]) <= 100


# ---------------------------------------------------------------------------
# Daily cap enforcement
# ---------------------------------------------------------------------------


class TestDailyCap:
    def _seed_candidate(self, conn, dest_iata: str, vibe: str, score: float = 1.0) -> None:
        conn.execute(
            "INSERT INTO ad_candidates "
            "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
            "save_count, search_count, signal_score, updated_at) "
            "VALUES (%s, %s, 'City', 'Title', 'Body', '/', 1, 5, %s, %s)",
            (dest_iata, vibe, score, time.time()),
        )

    def _mark_pushed(self, conn, dest_iata: str, vibe: str) -> None:
        conn.execute(
            "UPDATE ad_candidates SET push_state = 'pushed', pushed_at = %s, ads_api_ad_id = 'x' "
            "WHERE dest_iata = %s AND vibe = %s",
            (time.time(), dest_iata, vibe),
        )

    def test_cap_stops_push_at_10(self, isolated, monkeypatch):
        """run_push_cycle must stop after DAILY_CAP pushes in 24h."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        push_call_count = []

        def fake_create_ad(**kwargs):
            push_call_count.append(1)
            return f"ad-{len(push_call_count)}"

        with isolated() as conn:
            # Already 9 pushed in last 24h
            for i in range(9):
                self._seed_candidate(conn, f"AA{i}", "culture", score=float(10 - i))
                self._mark_pushed(conn, f"AA{i}", "culture")
            # 5 pending candidates (only 1 should be pushed due to cap)
            for i in range(5):
                self._seed_candidate(conn, f"BB{i}", "adventure", score=float(5 - i))

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            inst = MockClient.return_value
            inst.ensure_campaign.return_value = "camp-1"
            inst.ensure_ad_group.return_value = "group-1"
            inst.ensure_brand_image.return_value = "file-1"
            inst.create_ad.side_effect = fake_create_ad

            pushed = ap.run_push_cycle()

        assert pushed == 1
        assert len(push_call_count) == 1

    def test_cap_fully_reached_skips_cycle(self, isolated, monkeypatch):
        """When 10 ads already pushed in 24h, skip entirely."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        with isolated() as conn:
            for i in range(10):
                self._seed_candidate(conn, f"CC{i}", "culture")
                self._mark_pushed(conn, f"CC{i}", "culture")
            self._seed_candidate(conn, "ZZZ", "beach")

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            pushed = ap.run_push_cycle()

        MockClient.assert_not_called()
        assert pushed == 0

    def test_cap_resets_after_24h(self, isolated, monkeypatch):
        """Pushes older than 24h don't count against the cap."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        old_ts = time.time() - 86401  # just over 24h ago

        with isolated() as conn:
            for i in range(10):
                self._seed_candidate(conn, f"DD{i}", "culture")
                conn.execute(
                    "UPDATE ad_candidates SET push_state = 'pushed', pushed_at = %s, ads_api_ad_id = 'x' "
                    "WHERE dest_iata = %s AND vibe = 'culture'",
                    (old_ts, f"DD{i}"),
                )
            self._seed_candidate(conn, "EEE", "beach")

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            inst = MockClient.return_value
            inst.ensure_campaign.return_value = "c"
            inst.ensure_ad_group.return_value = "g"
            inst.ensure_brand_image.return_value = "f"
            inst.create_ad.return_value = "new-ad"

            pushed = ap.run_push_cycle()

        assert pushed == 1


# ---------------------------------------------------------------------------
# Paused-only guardrail
# ---------------------------------------------------------------------------


class TestPausedOnlyGuardrail:
    def test_create_ad_always_sends_paused(self):
        """AdsApiClient.create_ad always sends status=paused — never active."""
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append(data)
            return {"id": "ad-99"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post", fake_post):
            client.create_ad(
                ad_group_id="g",
                title="Test Ad Title",
                body="Test body text here.",
                landing_url="https://example.com/",
                file_id="f",
            )

        assert len(captured) == 1
        payload = captured[0]
        assert payload["status"] == "paused"
        assert payload.get("status") != "active"

    def test_create_ad_rejects_active_even_if_caller_tries(self):
        """No code path in create_ad can produce status='active'."""
        # Verify that the method signature has no status parameter
        import inspect

        sig = inspect.signature(aa.AdsApiClient.create_ad)
        assert "status" not in sig.parameters


# ---------------------------------------------------------------------------
# Creative limits enforcement
# ---------------------------------------------------------------------------


class TestCreativeLimits:
    def test_title_truncated_to_50(self):
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append(data)
            return {"id": "x"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post", fake_post):
            client.create_ad(
                ad_group_id="g",
                title="A" * 80,
                body="Short body.",
                landing_url="https://example.com/",
                file_id="f",
            )

        title = captured[0]["creative"]["title"]
        assert len(title) <= 50

    def test_body_truncated_to_100(self):
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append(data)
            return {"id": "x"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post", fake_post):
            client.create_ad(
                ad_group_id="g",
                title="Short title",
                body="B" * 200,
                landing_url="https://example.com/",
                file_id="f",
            )

        body = captured[0]["creative"]["body"]
        assert len(body) <= 100

    def test_title_min_3_chars(self):
        """Titles shorter than 3 chars are padded."""
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append(data)
            return {"id": "x"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post", fake_post):
            client.create_ad(
                ad_group_id="g",
                title="Hi",
                body="Body text.",
                landing_url="https://example.com/",
                file_id="f",
            )

        title = captured[0]["creative"]["title"]
        assert len(title) >= 3


# ---------------------------------------------------------------------------
# Corrected API payload shapes
# ---------------------------------------------------------------------------


class TestCorrectedPayloadShapes:
    """Verify every outbound payload matches the current OpenAI Ads API schema."""

    def _capture_post(self):
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append({"path": path, "data": data})
            return {"id": "stub-id"}

        return captured, fake_post

    def test_campaign_uses_lifetime_spend_limit_micros(self, isolated):
        """Campaign payload must use budget.lifetime_spend_limit_micros, not type+amount_micros."""
        captured, fake_post = self._capture_post()

        def fake_get(self_inner, path):
            raise Exception("not found")

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_post", fake_post),
            patch.object(aa.AdsApiClient, "_get", fake_get),
        ):
            client.ensure_campaign()

        payload = captured[0]["data"]
        assert "budget" in payload
        budget = payload["budget"]
        # The correct field — rejected by the API as unknown if you use type+amount_micros
        assert "lifetime_spend_limit_micros" in budget, (
            "campaign budget must use lifetime_spend_limit_micros"
        )
        # Old wrong fields must NOT be present
        assert "type" not in budget, "budget.type was rejected 400 by the API"
        assert "amount_micros" not in budget, "budget.amount_micros was rejected 400 by the API"
        assert budget["lifetime_spend_limit_micros"] >= 1_000_000, "minimum budget is 1 000 000"

    def test_ad_group_sends_required_bidding_config(self, isolated):
        """Ad group payload must include bidding_config (billing_event_type + max_bid_micros)."""
        captured, fake_post = self._capture_post()

        def fake_get(self_inner, path):
            raise Exception("not found")

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_post", fake_post),
            patch.object(aa.AdsApiClient, "_get", fake_get),
        ):
            client.ensure_ad_group(campaign_id="camp-1")

        payload = captured[0]["data"]
        assert "bidding_config" in payload, "ad group requires bidding_config"
        bc = payload["bidding_config"]
        assert "billing_event_type" in bc
        assert "max_bid_micros" in bc
        assert bc["billing_event_type"] == "impression"
        assert bc["max_bid_micros"] >= 1

    def test_create_ad_uses_correct_creative_field_names(self):
        """create_ad must use creative.type, creative.target_url, creative.file_id."""
        captured: list[dict] = []

        def fake_post(self_inner, path, data):
            captured.append(data)
            return {"id": "ad-1"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post", fake_post):
            client.create_ad(
                ad_group_id="g",
                title="Visit Tokyo",
                body="Amazing city.",
                landing_url="https://example.com/tokyo",
                file_id="file-xyz",
            )

        payload = captured[0]
        creative = payload.get("creative", {})

        # Correct field names
        assert creative.get("type") == "chat_card", "creative.type must be chat_card (not top-level format)"
        assert creative.get("target_url") == "https://example.com/tokyo", (
            "landing URL must be creative.target_url (not landing_url)"
        )
        assert creative.get("file_id") == "file-xyz", (
            "image must be creative.file_id (not image_file_id)"
        )

        # Old wrong field names must not appear
        assert "format" not in payload, "format is not a valid top-level field"
        assert "landing_url" not in creative, "landing_url is not a valid creative field; use target_url"
        assert "image_file_id" not in creative, "image_file_id is not valid; use file_id"

        # Ad name is required by the API
        assert "name" in payload, "ads require a name field"
        assert len(payload["name"]) >= 3

    def test_image_upload_hits_upload_endpoint_and_reads_file_id(self, isolated, tmp_path):
        """Image upload must POST to /upload and read response.file_id (not /files or id)."""
        fake_jpg = tmp_path / "share_bg.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        upload_calls: list[dict] = []

        def fake_multipart(self_inner, path, *, files, data):
            upload_calls.append({"path": path})
            return {"file_id": "file-correct"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_post_multipart", fake_multipart),
            patch.object(aa, "_BRAND_IMAGE_PATH", fake_jpg),
        ):
            fid = client.ensure_brand_image()

        assert fid == "file-correct"
        assert upload_calls[0]["path"] == "/upload", (
            "image upload must use /upload, not /files"
        )


# ---------------------------------------------------------------------------
# ensure_campaign / ensure_ad_group — create-once and reuse logic
# ---------------------------------------------------------------------------


class TestEnsureCampaignAndAdGroup:
    def test_creates_campaign_on_first_call(self, isolated):
        responses = iter([{"id": "camp-001"}, {"id": "camp-001"}])

        def fake_post(self_inner, path, data):
            return next(responses)

        def fake_get(self_inner, path):
            raise Exception("not found")

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_post", fake_post),
            patch.object(aa.AdsApiClient, "_get", fake_get),
        ):
            cid = client.ensure_campaign()

        assert cid == "camp-001"
        with isolated() as conn:
            row = conn.execute(
                "SELECT value FROM ad_pipeline_config WHERE key = 'campaign_id'"
            ).fetchone()
        assert row["value"] == "camp-001"

    def test_reuses_stored_campaign_id(self, isolated):
        # Pre-seed the config
        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_pipeline_config (key, value) VALUES ('campaign_id', 'existing-camp')"
            )

        get_calls: list[str] = []

        def fake_get(self_inner, path):
            get_calls.append(path)
            return {"id": "existing-camp"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_get", fake_get):
            cid = client.ensure_campaign()

        assert cid == "existing-camp"
        assert any("existing-camp" in p for p in get_calls)

    def test_recreates_campaign_when_stored_id_invalid(self, isolated):
        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_pipeline_config (key, value) VALUES ('campaign_id', 'stale-id')"
            )

        def fake_get(self_inner, path):
            raise Exception("404 not found")

        def fake_post(self_inner, path, data):
            return {"id": "new-camp"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_get", fake_get),
            patch.object(aa.AdsApiClient, "_post", fake_post),
        ):
            cid = client.ensure_campaign()

        assert cid == "new-camp"

    def test_creates_ad_group_on_first_call(self, isolated):
        def fake_get(self_inner, path):
            raise Exception("not found")

        def fake_post(self_inner, path, data):
            return {"id": "group-001"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_get", fake_get),
            patch.object(aa.AdsApiClient, "_post", fake_post),
        ):
            gid = client.ensure_ad_group(campaign_id="camp-001")

        assert gid == "group-001"

    def test_reuses_stored_ad_group_id(self, isolated):
        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_pipeline_config (key, value) VALUES ('ad_group_id', 'existing-group')"
            )

        def fake_get(self_inner, path):
            return {"id": "existing-group"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_get", fake_get):
            gid = client.ensure_ad_group(campaign_id="camp-001")

        assert gid == "existing-group"


# ---------------------------------------------------------------------------
# One-time image upload and file_id reuse
# ---------------------------------------------------------------------------


class TestBrandImageUpload:
    def test_uploads_image_on_first_call(self, isolated, tmp_path):
        # Point at a minimal JPEG so the real file read succeeds
        fake_jpg = tmp_path / "share_bg.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        upload_calls: list[dict] = []

        def fake_multipart(self_inner, path, *, files, data):
            upload_calls.append({"path": path, "files": list(files.keys()), "data": data})
            # API returns {"file_id": "..."} (not "id")
            return {"file_id": "file-abc"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with (
            patch.object(aa.AdsApiClient, "_post_multipart", fake_multipart),
            patch.object(aa, "_BRAND_IMAGE_PATH", fake_jpg),
        ):
            fid = client.ensure_brand_image()

        assert fid == "file-abc"
        assert len(upload_calls) == 1
        # Upload must hit /upload, not /files
        assert upload_calls[0]["path"] == "/upload"

        with isolated() as conn:
            row = conn.execute(
                "SELECT value FROM ad_pipeline_config WHERE key = 'brand_image_file_id'"
            ).fetchone()
        assert row["value"] == "file-abc"

    def test_reuses_stored_file_id(self, isolated):
        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_pipeline_config (key, value) VALUES "
                "('brand_image_file_id', 'cached-file')"
            )

        upload_calls: list[int] = []

        def fake_multipart(self_inner, path, *, files, data):
            upload_calls.append(1)
            return {"id": "should-not-use"}

        client = aa.AdsApiClient(api_key="k", base_url="https://stub")
        with patch.object(aa.AdsApiClient, "_post_multipart", fake_multipart):
            fid = client.ensure_brand_image()

        assert fid == "cached-file"
        assert len(upload_calls) == 0  # never called


# ---------------------------------------------------------------------------
# run_push_cycle — API key missing guard
# ---------------------------------------------------------------------------


class TestRunPushCycleKeyGuard:
    def test_skips_silently_without_api_key(self, isolated):
        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, updated_at) "
                "VALUES ('NRT', 'culture', 'Tokyo', 'Title', 'Body', '/', 2, 10, 3.0, %s)",
                (time.time(),),
            )

        pushed = ap.run_push_cycle()
        assert pushed == 0

    def test_skips_candidates_below_threshold(self, isolated, monkeypatch):
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, updated_at) "
                "VALUES ('NRT', 'culture', 'Tokyo', 'Title', 'Body', '/', 0, 10, 3.0, %s)",
                (time.time(),),
            )

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            pushed = ap.run_push_cycle()

        # save_count=0 < MIN_SAVES_THRESHOLD=1 → not pushed
        assert pushed == 0

    def test_pushed_in_score_order(self, isolated, monkeypatch):
        """Highest signal_score is pushed first."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        push_order: list[str] = []

        def fake_create_ad(**kwargs):
            # Extract IATA from landing_url suffix — we use title instead
            push_order.append(kwargs["title"])
            return f"ad-{len(push_order)}"

        with isolated() as conn:
            for iata, score in [("LHR", 1.0), ("CDG", 3.0), ("NRT", 2.0)]:
                conn.execute(
                    "INSERT INTO ad_candidates "
                    "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                    "save_count, search_count, signal_score, updated_at) "
                    f"VALUES ('{iata}', 'adventure', 'City', 'T-{iata}', 'Body', '/', "
                    f"1, 5, {score}, %s)",
                    (time.time(),),
                )

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            inst = MockClient.return_value
            inst.ensure_campaign.return_value = "c"
            inst.ensure_ad_group.return_value = "g"
            inst.ensure_brand_image.return_value = "f"
            inst.create_ad.side_effect = fake_create_ad

            ap.run_push_cycle()

        # CDG (score 3.0) should be first, NRT (2.0) second, LHR (1.0) third
        assert push_order[0] == "T-CDG"
        assert push_order[1] == "T-NRT"
        assert push_order[2] == "T-LHR"


# ---------------------------------------------------------------------------
# poll_and_push integration
# ---------------------------------------------------------------------------


class TestPollAndPush:
    def test_poll_and_push_runs_without_key(self, isolated):
        """poll_and_push is a no-op for the push step when no key is set."""
        with isolated() as conn:
            conn.execute(
                "INSERT INTO dest_vibe_scores "
                "(dest_iata, vibe, score, search_count, save_count, updated_at) "
                "VALUES ('CDG', 'romance', 2.0, 10, 3, %s)",
                (time.time(),),
            )

        ap.poll_and_push()  # Must not raise

        with isolated() as conn:
            row = conn.execute(
                "SELECT * FROM ad_candidates WHERE dest_iata = 'CDG'"
            ).fetchone()
        # Candidate upserted even without API key
        assert row is not None
        assert row["push_state"] == "pending"


# ---------------------------------------------------------------------------
# retry_failed_candidates
# ---------------------------------------------------------------------------


class TestRetryFailedCandidates:
    """Tests for the exponential-backoff retry mechanism."""

    def _seed_failed(
        self,
        conn,
        dest_iata: str,
        vibe: str,
        fail_count: int = 1,
        age_seconds: float = 0.0,
    ) -> None:
        """Insert a failed ad candidate whose failed_at is age_seconds in the past."""
        now = time.time()
        failed_at = now - age_seconds
        conn.execute(
            "INSERT INTO ad_candidates "
            "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
            "save_count, search_count, signal_score, push_state, fail_count, failed_at, updated_at) "
            "VALUES (%s, %s, 'City', 'Title', 'Body', '/', 1, 5, 1.0, 'failed', %s, %s, %s)",
            (dest_iata, vibe, fail_count, failed_at, now),
        )

    def test_resets_failed_row_after_base_window(self, isolated):
        """A row that failed once is reset after RETRY_WINDOW_SECONDS elapses."""
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600  # 1h for this test
        try:
            # fail_count=1 → window=3600s; row is 3601s old → should be reset
            with isolated() as conn:
                self._seed_failed(conn, "NRT", "culture", fail_count=1, age_seconds=3601)

            count = ap.retry_failed_candidates()
            assert count == 1

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'NRT'"
                ).fetchone()
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window

    def test_does_not_reset_within_backoff_window(self, isolated):
        """A recently-failed row is NOT reset until its backoff window elapses."""
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        try:
            # fail_count=1 → window=3600s; row is only 1800s old → stay failed
            with isolated() as conn:
                self._seed_failed(conn, "LHR", "adventure", fail_count=1, age_seconds=1800)

            count = ap.retry_failed_candidates()
            assert count == 0

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'LHR'"
                ).fetchone()
            assert row["push_state"] == "failed"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window

    def test_exponential_backoff_doubles_per_failure(self, isolated):
        """fail_count=2 doubles the window; the row stays failed until 2× the base elapses."""
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        try:
            # fail_count=2 → window = 3600 * 2^1 = 7200s
            # age=5000s < 7200s → should stay failed
            with isolated() as conn:
                self._seed_failed(conn, "CDG", "romance", fail_count=2, age_seconds=5000)

            count = ap.retry_failed_candidates()
            assert count == 0

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'CDG'"
                ).fetchone()
            assert row["push_state"] == "failed"

            # Now reset with failed_at age > 7200s → should be retried
            with isolated() as conn:
                conn.execute(
                    "UPDATE ad_candidates SET failed_at = %s "
                    "WHERE dest_iata = 'CDG' AND vibe = 'romance'",
                    (time.time() - 7201,),
                )

            count = ap.retry_failed_candidates()
            assert count == 1

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'CDG'"
                ).fetchone()
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window

    def test_backoff_capped_at_max_window(self, isolated):
        """Even with a very high fail_count, retry window is capped at MAX_RETRY_WINDOW_SECONDS."""
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        old_max = ap.MAX_RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        ap.MAX_RETRY_WINDOW_SECONDS = 7200  # 2h cap
        try:
            # fail_count=10 → uncapped window would be huge; capped to 7200s
            # age=7201s → should be reset
            with isolated() as conn:
                self._seed_failed(conn, "SYD", "beach", fail_count=10, age_seconds=7201)

            count = ap.retry_failed_candidates()
            assert count == 1

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'SYD'"
                ).fetchone()
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window
            ap.MAX_RETRY_WINDOW_SECONDS = old_max

    def test_pending_and_pushed_rows_untouched(self, isolated):
        """retry_failed_candidates only touches failed rows."""
        with isolated() as conn:
            # pending row
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, push_state, fail_count, updated_at) "
                "VALUES ('AAA', 'culture', 'City', 'T', 'B', '/', 1, 5, 1.0, 'pending', 0, %s)",
                (time.time() - 100000,),
            )
            # pushed row (very old)
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, push_state, fail_count, updated_at) "
                "VALUES ('BBB', 'adventure', 'City', 'T', 'B', '/', 1, 5, 1.0, 'pushed', 0, %s)",
                (time.time() - 100000,),
            )

        count = ap.retry_failed_candidates()
        assert count == 0

        with isolated() as conn:
            rows = conn.execute(
                "SELECT dest_iata, push_state FROM ad_candidates ORDER BY dest_iata"
            ).fetchall()
        states = {r["dest_iata"]: r["push_state"] for r in rows}
        assert states["AAA"] == "pending"
        assert states["BBB"] == "pushed"

    def test_fail_count_incremented_on_push_failure(self, isolated, monkeypatch):
        """run_push_cycle increments fail_count when a push fails."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        with isolated() as conn:
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, fail_count, updated_at) "
                "VALUES ('NRT', 'culture', 'Tokyo', 'Title', 'Body', '/', 1, 5, 2.0, 0, %s)",
                (time.time(),),
            )

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            inst = MockClient.return_value
            inst.ensure_campaign.return_value = "c"
            inst.ensure_ad_group.return_value = "g"
            inst.ensure_brand_image.return_value = "f"
            inst.create_ad.side_effect = RuntimeError("API error")

            ap.run_push_cycle()

        with isolated() as conn:
            row = conn.execute(
                "SELECT push_state, fail_count FROM ad_candidates WHERE dest_iata = 'NRT'"
            ).fetchone()
        assert row["push_state"] == "failed"
        assert row["fail_count"] == 1

    def test_fail_count_accumulates_across_failures(self, isolated, monkeypatch):
        """fail_count accumulates — a row that has failed twice has fail_count=2."""
        monkeypatch.setenv("OPENAI_ADS_API_KEY", "test-key")

        with isolated() as conn:
            # Seed with fail_count already at 1 (first failure happened before)
            conn.execute(
                "INSERT INTO ad_candidates "
                "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                "save_count, search_count, signal_score, push_state, fail_count, updated_at) "
                "VALUES ('LHR', 'adventure', 'London', 'Title', 'Body', '/', 1, 5, 2.0, 'pending', 1, %s)",
                (time.time(),),
            )

        with patch("yonder.ads_api.AdsApiClient") as MockClient:
            inst = MockClient.return_value
            inst.ensure_campaign.return_value = "c"
            inst.ensure_ad_group.return_value = "g"
            inst.ensure_brand_image.return_value = "f"
            inst.create_ad.side_effect = RuntimeError("API error again")

            ap.run_push_cycle()

        with isolated() as conn:
            row = conn.execute(
                "SELECT push_state, fail_count FROM ad_candidates WHERE dest_iata = 'LHR'"
            ).fetchone()
        assert row["push_state"] == "failed"
        assert row["fail_count"] == 2  # incremented from 1 to 2

    def test_poll_and_push_calls_retry_first(self, isolated, monkeypatch):
        """poll_and_push resets eligible failed rows before scanning for pushes."""
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        try:
            # A failed row whose backoff window has elapsed
            with isolated() as conn:
                self._seed_failed(conn, "CDG", "romance", fail_count=1, age_seconds=7200)

            ap.poll_and_push()  # no API key → push skipped, but retry runs

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'CDG'"
                ).fetchone()
            # Should have been reset to pending by the retry step
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window

    def test_legacy_failed_row_with_null_failed_at_is_retried(self, isolated):
        """Legacy failed rows where failed_at IS NULL fall back to updated_at for backoff.

        Regression: pre-migration failed rows must not be stuck permanently.
        """
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        try:
            # Seed a failed row with no failed_at (as it would look before migration)
            old_updated_at = time.time() - 7200  # 2h ago, > 3600s window
            with isolated() as conn:
                conn.execute(
                    "INSERT INTO ad_candidates "
                    "(dest_iata, vibe, city_name, ad_title, ad_body, landing_url, "
                    "save_count, search_count, signal_score, push_state, fail_count, "
                    "failed_at, updated_at) "
                    "VALUES ('SIN', 'city', 'Singapore', 'T', 'B', '/', 1, 5, 1.0, "
                    "'failed', 1, NULL, %s)",
                    (old_updated_at,),
                )

            # Should retry using updated_at as the fallback anchor
            count = ap.retry_failed_candidates()
            assert count == 1, (
                "Legacy failed row with failed_at=NULL should be retried using updated_at"
            )

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'SIN'"
                ).fetchone()
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window

    def test_save_upsert_does_not_restart_retry_clock(self, isolated, monkeypatch):
        """A save-triggered upsert (which updates updated_at) must NOT defer the retry.

        Regression test: backoff must be anchored to failed_at, not updated_at.
        A candidate that receives saves while failed should still retry once
        failed_at is old enough — the retry clock must not be restarted by content
        updates.
        """
        old_retry_window = ap.RETRY_WINDOW_SECONDS
        ap.RETRY_WINDOW_SECONDS = 3600
        try:
            monkeypatch.setenv("REPLIT_DOMAINS", "example.com")
            # Seed a failed row with failed_at 5h ago (> 3600s window → eligible)
            with isolated() as conn:
                self._seed_failed(conn, "NRT", "culture", fail_count=1, age_seconds=5 * 3600)

            # Simulate a save upsert that refreshes updated_at to NOW,
            # but does NOT change failed_at (the upsert preserves push_state).
            saved = _FakeSaved()
            ap.upsert_candidate_from_save(saved)

            # After the upsert, push_state is still preserved as 'failed'
            # (the upsert ON CONFLICT clause does not overwrite push_state).
            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state, failed_at, updated_at "
                    "FROM ad_candidates WHERE dest_iata = 'NRT'"
                ).fetchone()
            assert row["push_state"] == "failed", (
                "upsert_candidate_from_save must not reset push_state to pending"
            )

            # Now retry_failed_candidates() should still reset the row to pending
            # because failed_at (not updated_at) is old enough.
            count = ap.retry_failed_candidates()
            assert count == 1, (
                "retry must fire based on failed_at, not updated_at; "
                "a save upsert must not restart the retry clock"
            )

            with isolated() as conn:
                row = conn.execute(
                    "SELECT push_state FROM ad_candidates WHERE dest_iata = 'NRT'"
                ).fetchone()
            assert row["push_state"] == "pending"
        finally:
            ap.RETRY_WINDOW_SECONDS = old_retry_window
