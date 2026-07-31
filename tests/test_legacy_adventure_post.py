"""Regression tests for the legacy POST /adventure route.

adventure.html was deleted and GET /adventure redirects to /?mode=detour.
These tests confirm POST /adventure never raises TemplateNotFound or returns a
500 — every path must return 200 (success) or 400 (validation error), both
rendering index.html inside the unified compose page.

Three cases covered:
  1. Missing prompt     → 400, index.html rendered
  2. Missing depart     → 400, index.html rendered
  3. Valid form submit  → 200, index.html rendered  (plan_adventure patched)
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.vibe_signals as vs
import yonder.web as web_module
from yonder.adventure import (
    AdventureItinerary,
    AdventureRequest,
    AdventureResult,
    StopoverIdea,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Fresh signal DB, no MOCK env var, no live API keys."""
    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "signals_test.db")
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_itinerary() -> AdventureItinerary:
    return AdventureItinerary(
        kind="stopover",
        title="YVR → NRT → LHR",
        stop_city="Tokyo",
        stop_iata="NRT",
        stay_days=3,
        total_price=850.0,
        currency="USD",
    )


def _fake_result(req: AdventureRequest) -> AdventureResult:
    idea = StopoverIdea(iata="NRT", city="Tokyo", country="JP", vibe_tags=["culture"])
    return AdventureResult(
        request=req,
        ideas=[idea],
        itineraries=[_minimal_itinerary()],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLegacyAdventurePost:
    def test_missing_prompt_returns_400_not_500(self, client):
        """Empty prompt raises ValueError → 400 with index.html, not TemplateNotFound."""
        resp = client.post(
            "/adventure",
            data={"prompt": "", "depart": "2026-10-01"},
        )
        assert resp.status_code == 400, (
            f"Expected 400 for missing prompt, got {resp.status_code}"
        )
        assert "text/html" in resp.headers.get("content-type", "")
        # Must be index.html content (compose card present), not an error page
        assert "Yonder" in resp.text, "Response is not index.html"

    def test_planner_exception_returns_400_not_500(self, client):
        """If plan_adventure raises unexpectedly the error handler renders index.html at 400,
        never a 500 / TemplateNotFound from a missing adventure.html."""
        depart = "2026-10-01"

        with (
            patch("yonder.web.plan_adventure", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch(
                "yonder.web.seed_ideas",
                return_value=[
                    StopoverIdea(iata="NRT", city="Tokyo", country="JP", vibe_tags=["culture"])
                ],
            ),
            patch.object(
                web_module.get_settings().__class__,
                "grok_ready",
                return_value=False,
            ),
        ):
            resp = client.post(
                "/adventure",
                data={
                    "prompt": "fly Vancouver to Tokyo",
                    "depart": depart,
                    "vibe": "adventure",
                },
            )

        assert resp.status_code == 400, (
            f"Expected 400 for planner error, got {resp.status_code}"
        )
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Yonder" in resp.text, "Response is not index.html"

    def test_valid_form_returns_200_index_html(self, client):
        """Valid form with mocked planner returns 200 and renders index.html."""
        depart = "2026-10-01"

        fake_req = AdventureRequest(
            origin="YVR",
            destination="NRT",
            depart_date=date.fromisoformat(depart),
            vibe="adventure",
            max_candidates=3,
            include_direct=False,
            prompt="fly Vancouver to Tokyo for a stopover",
        )
        fake_result = _fake_result(fake_req)

        with (
            patch("yonder.web.plan_adventure", new=AsyncMock(return_value=fake_result)),
            patch(
                "yonder.web.seed_ideas",
                return_value=[
                    StopoverIdea(iata="NRT", city="Tokyo", country="JP", vibe_tags=["culture"])
                ],
            ),
            # Keep Grok offline so the no-Grok path triggers seed_ideas patched above
            patch.object(
                web_module.get_settings().__class__,
                "grok_ready",
                return_value=False,
            ),
        ):
            resp = client.post(
                "/adventure",
                data={
                    "prompt": "fly Vancouver to Tokyo for a stopover",
                    "depart": depart,
                    "vibe": "adventure",
                },
            )

        assert resp.status_code == 200, (
            f"Expected 200 for valid form, got {resp.status_code}\n{resp.text[:400]}"
        )
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Yonder" in resp.text, "Response is not index.html"


class TestLegacyAdventureGet:
    def test_get_adventure_redirects_to_detour_panel(self, client):
        """GET /adventure must return 302 → /?mode=detour (bookmarked URLs land on Detour)."""
        resp = client.get("/adventure", follow_redirects=False)
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}"
        )
        location = resp.headers.get("location", "")
        assert "mode=detour" in location, (
            f"Expected Location to contain '?mode=detour', got {location!r}"
        )
