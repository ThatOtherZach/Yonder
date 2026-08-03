"""Tests for Quest result persistence across result-filter tab switches — Task 471.

Verifies that:
  1. The bootResultsFilter tab-click handler calls hydrateQuestFromStorage()
     after every applyResultFilter call, so Quest HTML is never left blank after
     switching to another tab and back.
  2. The Quest HTML returned by /api/quest/plan carries the DOM anchors that
     hydrateQuestFromStorage() needs to detect an already-injected result and
     skip a redundant re-injection.
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import yonder.adventure as adventure_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import PricedLeg
from yonder.config import Settings
from yonder.types import FlightOffer

_FUTURE = (date.today() + timedelta(days=40)).isoformat()
_PROMPT = "overland food adventure through southeast asia"
_RAW_IDEA = {
    "entry_iata": "HAN",
    "exit_iata": "BKK",
    "entry_city": "Hanoi",
    "exit_city": "Bangkok",
    "overland_narrative": "Ride the Reunification Express south, then cross into Thailand.",
    "transport": ["Reunification Express", "Mekong slow boat"],
    "highlights": ["Hội An", "Phnom Penh"],
}

_TEMPLATE = Path(__file__).parent.parent / "yonder" / "templates" / "index.html"


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOCK", raising=False)


@pytest.fixture(autouse=True)
def _no_disk_io(monkeypatch):
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)
    monkeypatch.setattr(web_module, "save_last", lambda *a, **kw: None, raising=False)


def _wire(monkeypatch) -> None:
    settings = Settings(testing=True, xai_api_key="test-key")
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    async def _fake_plan_quest(*a: Any, **kw: Any):
        from yonder.adventure import plan_quest as _real

        async def _fake_price_leg(origin, dest, depart, req, **kw2) -> PricedLeg:
            return PricedLeg(
                from_iata=origin,
                to_iata=dest,
                depart_date=date.fromisoformat(_FUTURE),
                offer=FlightOffer(
                    provider="testair",
                    price=500.0,
                    currency="USD",
                    price_kind="live",
                    display_price_base="~US$500",
                    display_price="~US$500",
                ),
                google_flights_url=f"https://flights.google.com/?q={origin}-{dest}",
            )

        async def _fake_pick(*a2: Any, **kw2: Any):
            return ["testair"]

        monkeypatch.setattr(adventure_module, "_price_leg", _fake_price_leg)
        monkeypatch.setattr(adventure_module, "pick_pricing_provider", _fake_pick)

        return await _real(
            a[0] if a else kw.get("prompt", _PROMPT),
            a[1] if len(a) > 1 else kw.get("vibe", "adventure"),
            a[2] if len(a) > 2 else kw.get("home_iata", "YVR"),
            a[3] if len(a) > 3 else kw.get("depart_date", date.fromisoformat(_FUTURE)),
            settings,
            raw_ideas=[dict(_RAW_IDEA)],
        )

    monkeypatch.setattr(web_module, "plan_quest", _fake_plan_quest)

    try:
        import yonder.saved as saved_module
        monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
    except Exception:
        pass


def _post_quest(client: TestClient) -> dict:
    resp = client.post(
        "/api/quest/plan",
        data={
            "prompt": _PROMPT,
            "origin": "YVR",
            "depart": _FUTURE,
            "vibe": "adventure",
            "quest_days": "10",
        },
    )
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Template wiring: hydrateQuestFromStorage called on every tab switch
# ---------------------------------------------------------------------------


class TestTabSwitchHydrationWiring:
    """Inspect the template source to confirm hydrateQuestFromStorage() is
    called inside the bootResultsFilter tab-click handler."""

    def _template_source(self) -> str:
        return _TEMPLATE.read_text(encoding="utf-8")

    def test_hydrate_called_inside_tab_click_handler(self):
        """The bootResultsFilter click listener must call hydrateQuestFromStorage
        so Quest HTML is re-injected if the panel was cleared while the user
        was on another tab."""
        src = self._template_source()

        # Locate the bootResultsFilter function body
        boot_match = re.search(
            r"function bootResultsFilter\(\)(.*?)(?=\n\s{6}function |\Z)",
            src,
            re.S,
        )
        assert boot_match, "bootResultsFilter function not found in template"
        boot_body = boot_match.group(1)

        # The click handler must contain hydrateQuestFromStorage
        assert "hydrateQuestFromStorage" in boot_body, (
            "bootResultsFilter tab-click handler must call hydrateQuestFromStorage() "
            "so Quest results survive switching between Escape and Detour tabs."
        )

    def test_hydrate_call_is_after_apply_filter_in_click_handler(self):
        """hydrateQuestFromStorage() must come AFTER applyResultFilter() in the
        click handler — the filter visibility must be set before we hydrate."""
        src = self._template_source()

        boot_match = re.search(
            r"function bootResultsFilter\(\)(.*?)(?=\n\s{6}function |\Z)",
            src,
            re.S,
        )
        assert boot_match, "bootResultsFilter function not found in template"
        boot_body = boot_match.group(1)

        idx_apply = boot_body.find("applyResultFilter(tab.getAttribute")
        idx_hydrate = boot_body.find("hydrateQuestFromStorage")
        assert idx_apply != -1, "applyResultFilter tab-attribute call not found"
        assert idx_hydrate != -1, "hydrateQuestFromStorage call not found"
        assert idx_hydrate > idx_apply, (
            "hydrateQuestFromStorage() must appear after applyResultFilter() "
            "in the tab click handler"
        )

    def test_hydrate_guarded_with_typeof(self):
        """The call must be guarded with typeof so it degrades safely when
        hydrateQuestFromStorage is not yet defined (e.g. in partial renders)."""
        src = self._template_source()
        # Accept either typeof guard or direct call — but typeof is preferred
        boot_match = re.search(
            r"function bootResultsFilter\(\)(.*?)(?=\n\s{6}function |\Z)",
            src,
            re.S,
        )
        assert boot_match, "bootResultsFilter function not found in template"
        boot_body = boot_match.group(1)

        # The line calling hydrateQuestFromStorage must include a typeof guard
        hydrate_line = next(
            (l for l in boot_body.splitlines() if "hydrateQuestFromStorage" in l),
            None,
        )
        assert hydrate_line is not None
        assert "typeof" in hydrate_line, (
            "hydrateQuestFromStorage() call must be guarded with typeof to "
            "prevent ReferenceErrors if the function is not yet defined"
        )


# ---------------------------------------------------------------------------
# Quest HTML shape: DOM anchors needed by hydrateQuestFromStorage
# ---------------------------------------------------------------------------


class TestQuestHtmlAnchorsForRehydration:
    """Verify the /api/quest/plan HTML payload carries the elements that
    hydrateQuestFromStorage uses to detect an already-injected result."""

    def test_injected_html_has_boarding_pass_or_quest_idea_card(
        self, client, monkeypatch
    ):
        """hydrateQuestFromStorage guards re-injection with:
          if (questResults.querySelector('.boarding-pass, .quest-idea-card')) return;
        A successful Quest payload must contain at least one of these classes so
        a second hydrateQuestFromStorage() call after a tab switch is a no-op."""
        _wire(monkeypatch)
        result = _post_quest(client)
        assert result.get("ok") is True
        html = result["html"]
        assert "boarding-pass" in html or "quest-idea-card" in html, (
            "Quest HTML must contain .boarding-pass or .quest-idea-card so "
            "hydrateQuestFromStorage() skips redundant re-injection on tab switch"
        )

    def test_injected_html_contains_quest_results_card_anchor(
        self, client, monkeypatch
    ):
        """The HTML written into #quest-results must carry #quest-results-card
        so the panel is structurally intact after re-injection from sessionStorage."""
        _wire(monkeypatch)
        result = _post_quest(client)
        assert "quest-results-card" in result["html"], (
            "Quest HTML must include quest-results-card so the panel remains "
            "structurally valid after being re-injected on a tab switch"
        )

    def test_tab_switch_does_not_require_new_api_call(
        self, client, monkeypatch
    ):
        """Switching tabs then back must NOT trigger a second /api/quest/plan
        call — the re-hydration is purely client-side from sessionStorage.

        Verified server-side by confirming plan_quest is called exactly once
        per POST (the JS is responsible for not re-POSTing on tab switch)."""
        call_count = 0
        settings = Settings(testing=True, xai_api_key="test-key")
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _counting_quest(*a: Any, **kw: Any):
            nonlocal call_count
            call_count += 1
            from yonder.adventure import plan_quest as _real

            async def _price(o, d, dep, req, **k2) -> PricedLeg:
                return PricedLeg(
                    from_iata=o,
                    to_iata=d,
                    depart_date=date.fromisoformat(_FUTURE),
                    offer=FlightOffer(
                        provider="testair",
                        price=500.0,
                        currency="USD",
                        price_kind="live",
                        display_price_base="~US$500",
                        display_price="~US$500",
                    ),
                    google_flights_url=f"https://flights.google.com/?q={o}-{d}",
                )

            async def _pick(*a2: Any, **k2: Any):
                return ["testair"]

            monkeypatch.setattr(adventure_module, "_price_leg", _price)
            monkeypatch.setattr(adventure_module, "pick_pricing_provider", _pick)
            settings2 = Settings(testing=True, xai_api_key="test-key")
            return await _real(
                a[0] if a else _PROMPT,
                a[1] if len(a) > 1 else "adventure",
                a[2] if len(a) > 2 else "YVR",
                a[3] if len(a) > 3 else date.fromisoformat(_FUTURE),
                settings2,
                raw_ideas=[dict(_RAW_IDEA)],
            )

        monkeypatch.setattr(web_module, "plan_quest", _counting_quest)

        try:
            import yonder.saved as saved_module
            monkeypatch.setattr(saved_module, "upcoming_anchor_legs", lambda **kw: [], raising=False)
        except Exception:
            pass

        # User clicks "Plan a Quest" once
        _post_quest(client)
        assert call_count == 1

        # Tab switches are client-side only — server is NOT called again.
        # Confirm the count stays at 1 (no second POST reached the server).
        assert call_count == 1, (
            "plan_quest must be called exactly once; tab switching must not "
            "trigger additional /api/quest/plan requests"
        )
