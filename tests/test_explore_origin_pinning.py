"""Regression tests for /explore origin-pinning logic (Task 76 fix).

Covers three cases from web.py's _do_escape / _do_detour / _local_getaway_fallback:
  1. Initial search  — form origin field is the default when prompt names no city.
  2. Initial search  — a prompt-named city beats the form origin field (no refresh).
  3. Refresh escape  — form origin is pinned over whatever Grok parsed.
  4. Refresh detour  — form origin is pinned over translate_adventure's returned city.
  5. Getaway fallback + refresh — local_getaway_fallback picks origin_override when pinned.
  6. Getaway fallback + initial  — local_getaway_fallback picks home_iata when not pinned.

All tests run in mock mode (XAI_API_KEY cleared / no live providers so mock=True is
forced).  GrokClient methods are patched to return controlled origins so no real HTTP
call is made.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.grok as grok_module
import yonder.last_search as ls_module
import yonder.web as web_module
from yonder.adventure import AdventureRequest, AdventureResult, StopoverIdea
from yonder.config import Settings
from yonder.grok import ParsedTrip
from yonder.types import CabinClass, SearchQuery, UnifiedSearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEPART = (date.today() + timedelta(days=30)).isoformat()


def _make_settings(*, grok_ready: bool = True) -> Settings:
    """Settings with no live flight providers (forces mock=True) and optional Grok."""
    return Settings(
        testing=True,
        xai_api_key="test-key" if grok_ready else "",
    )


def _make_parsed_trip(origin: str, destination: str = "NRT") -> ParsedTrip:
    return ParsedTrip(
        origin=origin,
        destination=destination,
        depart_date=date.today() + timedelta(days=30),
        currency="USD",
    )


def _make_search_result(origin: str, destination: str = "NRT") -> UnifiedSearchResult:
    q = SearchQuery(
        origin=origin,
        destination=destination,
        depart_date=date.today() + timedelta(days=30),
    )
    return UnifiedSearchResult(query=q, results=[], offers=[])


def _make_adventure_result(origin: str, destination: str = "NRT") -> AdventureResult:
    req = AdventureRequest(
        origin=origin,
        destination=destination,
        depart_date=date.today() + timedelta(days=30),
    )
    idea = StopoverIdea(iata="TYO", city="Tokyo", stay_days=3)
    return AdventureResult(request=req, ideas=[idea], itineraries=[])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _patch_last_search(monkeypatch):
    """No-op disk I/O so tests never clobber the real .last_search.json."""
    monkeypatch.setattr(ls_module, "save_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_last", lambda *a, **kw: None)
    monkeypatch.setattr(ls_module, "load_first", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _clear_xai_env(monkeypatch):
    """Ensure XAI_API_KEY env var never leaks into tests."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Shared patch helpers
# ---------------------------------------------------------------------------


def _patch_escape(
    monkeypatch,
    *,
    grok_parsed_origin: str,
    search_result_origin: str | None = None,
    grok_ready: bool = True,
) -> dict[str, list]:
    """Patch Grok + search_flights for escape-path tests.

    Returns a captures dict with 'search_calls' so tests can assert the
    origin that actually reached search_flights.
    """
    captures: dict[str, list] = {"search_calls": []}

    settings = _make_settings(grok_ready=grok_ready)
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    parsed = _make_parsed_trip(grok_parsed_origin)

    async def _fake_parse(self, *a: Any, **kw: Any) -> ParsedTrip:
        return parsed

    monkeypatch.setattr(grok_module.GrokClient, "parse_natural_language", _fake_parse)

    used_origin = search_result_origin or grok_parsed_origin

    async def _fake_search(query, *, settings=None, **kw: Any) -> UnifiedSearchResult:  # type: ignore[override]
        captures["search_calls"].append(query)
        return _make_search_result(query.origin, query.destination)

    monkeypatch.setattr(web_module, "search_flights", _fake_search)

    return captures


def _patch_detour(
    monkeypatch,
    *,
    translate_origin: str,
    translate_dest: str = "NRT",
    grok_ready: bool = True,
) -> dict[str, list]:
    """Patch Grok + plan_adventure for detour-path tests."""
    captures: dict[str, list] = {"plan_calls": []}

    settings = _make_settings(grok_ready=grok_ready)
    monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

    adv_req = AdventureRequest(
        origin=translate_origin,
        destination=translate_dest,
        depart_date=date.today() + timedelta(days=30),
        trip_kind="getaway",
    )
    ideas = [StopoverIdea(iata="TYO", city="Tokyo", stay_days=3)]

    async def _fake_translate(self, *a: Any, **kw: Any):
        return adv_req, ideas

    monkeypatch.setattr(grok_module.GrokClient, "translate_adventure", _fake_translate)

    async def _fake_plan(req, idea_list, *, settings=None, **kw: Any) -> AdventureResult:
        captures["plan_calls"].append(req)
        return _make_adventure_result(req.origin, req.destination)

    monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)

    return captures


# ---------------------------------------------------------------------------
# 1.  Escape — initial search: form origin acts as default when prompt has no city
# ---------------------------------------------------------------------------


class TestEscapeInitialOriginDefault:
    """On initial (non-refresh) escape, the form's origin field is forwarded to
    Grok as default_origin.  When the prompt names no city Grok echoes it back,
    and search_flights receives that same IATA."""

    def test_field_origin_reaches_search_when_grok_echoes_it(
        self, client, monkeypatch
    ):
        # Grok returns the same origin as the field (YVR) — no city in prompt
        captures = _patch_escape(monkeypatch, grok_parsed_origin="YVR")

        resp = client.post(
            "/explore",
            data={
                "prompt": "somewhere sunny and cheap",
                "origin": "YVR",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "YVR"

    def test_different_field_origin_reaches_search(self, client, monkeypatch):
        """Form says YYZ and Grok echoes it — search sees YYZ, not a hardcoded default."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="YYZ")

        resp = client.post(
            "/explore",
            data={
                "prompt": "beach holiday somewhere warm",
                "origin": "YYZ",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "YYZ"


# ---------------------------------------------------------------------------
# 2.  Escape — initial search: prompt-named city beats the field (no refresh)
# ---------------------------------------------------------------------------


class TestEscapeInitialPromptCityWins:
    """On an initial (non-refresh) escape, if Grok resolves a different origin
    from the prompt the Grok result is used — origin_pinned is False."""

    def test_prompt_city_used_when_different_from_field(self, client, monkeypatch):
        # Grok parsed NRT from "fly from Tokyo" — field says YVR
        captures = _patch_escape(monkeypatch, grok_parsed_origin="NRT")

        resp = client.post(
            "/explore",
            data={
                "prompt": "fly from Tokyo to somewhere in Europe",
                "origin": "YVR",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # Grok's NRT (from "Tokyo") must win over the field's YVR
        assert captures["search_calls"][0].origin == "NRT"


# ---------------------------------------------------------------------------
# 3.  Escape — refresh: form origin is pinned over Grok's returned city
# ---------------------------------------------------------------------------


class TestEscapeRefreshOriginPinned:
    """On a Refresh, origin_pinned=True — the form origin overrides whatever
    Grok happened to parse, preventing the 'field says YVR, results depart YYZ'
    regression."""

    def test_grok_origin_overridden_on_refresh(self, client, monkeypatch):
        # Grok returns YYZ, but form says YVR and refresh=1
        captures = _patch_escape(monkeypatch, grok_parsed_origin="YYZ")

        resp = client.post(
            "/explore",
            data={
                "prompt": "somewhere warm and sunny",
                "origin": "YVR",
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "escape",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # Must be pinned to the form field, not Grok's YYZ
        assert captures["search_calls"][0].origin == "YVR"

    def test_grok_lax_origin_overridden_to_field_on_refresh(self, client, monkeypatch):
        """Regression anchor: field=JFK, Grok returns LAX, refresh=1 → JFK wins."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="LAX")

        resp = client.post(
            "/explore",
            data={
                "prompt": "give me something new",
                "origin": "JFK",
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "escape",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "JFK"

    def test_no_pin_without_refresh_flag(self, client, monkeypatch):
        """Without refresh=1, Grok's origin must not be overridden."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="LAX")

        resp = client.post(
            "/explore",
            data={
                "prompt": "fly from LA to Tokyo",
                "origin": "JFK",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                # NO refresh flag
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # Grok's LAX must be preserved (no pin)
        assert captures["search_calls"][0].origin == "LAX"


# ---------------------------------------------------------------------------
# 4.  Detour — refresh: form origin is pinned over translate_adventure result
# ---------------------------------------------------------------------------


class TestDetourRefreshOriginPinned:
    """Refresh + detour path: origin_pinned overrides translate_adventure's
    returned origin, including updating the destination when trip is a getaway."""

    def test_translate_adventure_origin_overridden_on_refresh(
        self, client, monkeypatch
    ):
        # translate_adventure returns YYZ; form says YVR; refresh=1 → YVR wins
        captures = _patch_detour(
            monkeypatch, translate_origin="YYZ", translate_dest="YYZ"
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": "a quick getaway somewhere new",
                "origin": "YVR",
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "detour",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        # Pin must have overridden the YYZ that translate_adventure returned
        assert captures["plan_calls"][0].origin == "YVR"

    def test_no_pin_without_refresh_flag(self, client, monkeypatch):
        """Without refresh, translate_adventure's origin is preserved."""
        captures = _patch_detour(
            monkeypatch, translate_origin="SYD", translate_dest="SYD"
        )

        resp = client.post(
            "/explore",
            data={
                "prompt": "Australian getaway from Sydney",
                "origin": "YVR",
                "depart": _DEPART,
                "force_mode": "detour",
                "vibe": "adventure",
                # NO refresh flag
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        # translate_adventure's SYD survives because origin_pinned is False
        assert captures["plan_calls"][0].origin == "SYD"


# ---------------------------------------------------------------------------
# 5 & 6.  Detour — local_getaway_fallback origin selection (Grok offline)
# ---------------------------------------------------------------------------


class TestDetourLocalGetawayFallback:
    """When Grok is offline, _local_getaway_fallback builds a home-city getaway.
    With refresh=1 and a valid origin field, it must use origin_override (pinned).
    Without refresh, it uses home_iata from settings (resolved home airport).
    """

    def _patch_offline_detour(self, monkeypatch) -> dict[str, list]:
        """Settings with grok_ready=False to force local fallback path."""
        captures: dict[str, list] = {"plan_calls": []}

        settings = _make_settings(grok_ready=False)
        monkeypatch.setattr(web_module, "reload_settings", lambda: settings)

        async def _fake_plan(req, idea_list, *, settings=None, **kw: Any) -> AdventureResult:
            captures["plan_calls"].append(req)
            return _make_adventure_result(req.origin, req.destination)

        monkeypatch.setattr(web_module, "plan_adventure", _fake_plan)
        return captures

    def test_pinned_origin_used_in_fallback_on_refresh(self, client, monkeypatch):
        """refresh=1 + origin=YVR + Grok offline → fallback getaway departs YVR."""
        captures = self._patch_offline_detour(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": "quick getaway",
                "origin": "YVR",
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "detour",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        assert captures["plan_calls"][0].origin == "YVR"

    def test_different_pinned_origin_in_fallback(self, client, monkeypatch):
        """refresh=1 + origin=JFK → fallback getaway departs JFK."""
        captures = self._patch_offline_detour(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": "quick getaway",
                "origin": "JFK",
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "detour",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        assert captures["plan_calls"][0].origin == "JFK"

    def test_no_refresh_no_origin_field_uses_settings_home_iata(
        self, client, monkeypatch
    ):
        """Initial search with NO origin field + Grok offline.

        When the form origin is blank (or absent), origin_override is empty so:
          • home_iata stays as settings.resolve_home_iata() (not a form override)
          • origin_pinned is False

        The fallback must still produce a valid 3-letter IATA from settings/guess,
        and the request must reach plan_adventure (no crash).
        """
        captures = self._patch_offline_detour(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": "quick getaway somewhere",
                # origin intentionally omitted — tests the blank-field path
                "depart": _DEPART,
                "force_mode": "detour",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        used = captures["plan_calls"][0].origin
        # Must be a plausible IATA resolved from settings (not a crash/empty string)
        assert len(used) == 3 and used.isalpha(), f"Expected IATA, got {used!r}"

    def test_pin_requires_valid_three_letter_origin_field(self, client, monkeypatch):
        """origin_pinned is only set when the form field is a valid 3-letter IATA.

        A malformed field (e.g. two letters) must NOT pin the origin even with refresh=1.
        """
        captures = self._patch_offline_detour(monkeypatch)

        resp = client.post(
            "/explore",
            data={
                "prompt": "quick getaway",
                "origin": "YV",   # invalid — only 2 letters
                "depart": _DEPART,
                "refresh": "1",
                "force_mode": "detour",
                "vibe": "adventure",
            },
        )
        assert resp.status_code == 200
        assert captures["plan_calls"], "plan_adventure was never called"
        used = captures["plan_calls"][0].origin
        # Malformed origin cannot pin; result must still be a valid IATA
        assert len(used) == 3 and used.isalpha(), f"Expected IATA, got {used!r}"
        # and specifically NOT the malformed "YV"
        assert used != "YV"


# ---------------------------------------------------------------------------
# 7.  Escape — chip-driven origin correction
# ---------------------------------------------------------------------------


class TestEscapeChipOriginCorrection:
    """When a search is driven by a dataset/template/save chip, Grok may echo a
    stale "From XYZ:" prefix from the chip text.  The server must pin the result
    back to the user's resolved home airport (home_iata) regardless of what Grok
    returned.  A plain prompt (chip_source=prompt) must NOT apply this correction.
    """

    # ------------------------------------------------------------------
    # Positive cases — chip_source triggers the guard
    # ------------------------------------------------------------------

    def test_dataset_chip_corrects_mismatched_origin(self, client, monkeypatch):
        """chip_source=dataset + Grok returns wrong origin → home_iata wins."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="LAX")

        resp = client.post(
            "/explore",
            data={
                "prompt": "From LAX: beaches",
                "origin": "JFK",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "dataset",
                "chip_id": "ds:abc123",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "JFK", (
            "chip correction should have overridden Grok's LAX with home JFK"
        )

    def test_template_chip_corrects_mismatched_origin(self, client, monkeypatch):
        """chip_source=template + Grok returns wrong origin → home_iata wins."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="SYD")

        resp = client.post(
            "/explore",
            data={
                "prompt": "From SYD: island hopping",
                "origin": "YVR",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "template",
                "chip_id": "tmpl:summer",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "YVR", (
            "chip correction should have overridden Grok's SYD with home YVR"
        )

    def test_save_chip_corrects_mismatched_origin(self, client, monkeypatch):
        """chip_source=save + Grok returns wrong origin → home_iata wins."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="CDG")

        resp = client.post(
            "/explore",
            data={
                "prompt": "From CDG: city break",
                "origin": "LHR",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "save",
                "chip_id": "saved:weekend",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "LHR", (
            "chip correction should have overridden Grok's CDG with home LHR"
        )

    def test_ds_chip_id_prefix_corrects_mismatched_origin(self, client, monkeypatch):
        """chip_id starting with 'ds:' triggers correction even if chip_source is empty."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="ORD")

        resp = client.post(
            "/explore",
            data={
                "prompt": "From ORD: warm escape",
                "origin": "BOS",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "",   # no explicit chip_source
                "chip_id": "ds:warmweather",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        assert captures["search_calls"][0].origin == "BOS", (
            "ds: chip_id prefix should have triggered correction to home BOS"
        )

    def test_chip_correction_skipped_when_grok_already_matches_home(
        self, client, monkeypatch
    ):
        """No spurious notes when Grok already returned the home airport."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="JFK")

        resp = client.post(
            "/explore",
            data={
                "prompt": "beaches",
                "origin": "JFK",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "dataset",
                "chip_id": "ds:beaches",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # Origin was already correct — must still be JFK
        assert captures["search_calls"][0].origin == "JFK"

    # ------------------------------------------------------------------
    # Negative case — prompt source must NOT apply the correction
    # ------------------------------------------------------------------

    def test_prompt_source_does_not_apply_chip_correction(self, client, monkeypatch):
        """chip_source=prompt is a regular user query — Grok's origin must be used."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="NRT")

        resp = client.post(
            "/explore",
            data={
                "prompt": "fly from Tokyo to Europe",
                "origin": "YVR",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                "chip_source": "prompt",
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # chip_source=prompt — no correction, Grok's NRT must survive
        assert captures["search_calls"][0].origin == "NRT", (
            "prompt source should never apply chip correction"
        )

    def test_absent_chip_source_does_not_apply_chip_correction(
        self, client, monkeypatch
    ):
        """When chip_source is omitted, the server defaults it to 'prompt', so no
        chip correction should be applied to a plain search."""
        captures = _patch_escape(monkeypatch, grok_parsed_origin="DXB")

        resp = client.post(
            "/explore",
            data={
                "prompt": "desert luxury from Dubai",
                "origin": "YYZ",
                "depart": _DEPART,
                "force_mode": "escape",
                "vibe": "adventure",
                # chip_source intentionally absent — defaults to "prompt"
            },
        )
        assert resp.status_code == 200
        assert captures["search_calls"], "search_flights was never called"
        # No chip, no correction — Grok's DXB must survive
        assert captures["search_calls"][0].origin == "DXB"
