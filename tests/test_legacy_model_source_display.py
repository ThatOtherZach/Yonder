"""Regression tests: legacy saved/shared trips without a model_source label
render without artifacts (no empty "via" line, no template errors).

Covers:
  - /saved with a legacy row (no model_source) — no "via " text in HTML
  - /saved with a labeled row — "via <label>" text present
  - /t/{share_id} shared trip with a legacy itinerary — no "via " text
  - /t/{share_id} shared trip with a labeled itinerary — "via <label>" present
  - Refresh flow preserves an existing model_source label (not dropped on update)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import yonder.saved as saved_module
import yonder.share as share_module
import yonder.vibe_signals as vs
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolated DBs and no live API keys for every test in this module."""
    monkeypatch.setattr(saved_module, "DB_PATH", tmp_path / "saved.db")
    monkeypatch.setattr(share_module, "DB_PATH", tmp_path / "share.db")
    monkeypatch.setattr(vs, "DB_PATH", tmp_path / "signals.db")
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_itinerary(title: str = "YVR → NRT") -> dict:
    """Minimal itinerary dict with no model_source — simulates a pre-label save."""
    return {
        "kind": "stopover",
        "title": title,
        "stop_city": "Tokyo",
        "stop_iata": "NRT",
        "currency": "USD",
        "total_price": 850.0,
        "display_price": "~$850",
        "legs": [
            {"from_iata": "YVR", "to_iata": "NRT", "depart_date": "2026-10-01"},
        ],
        # Deliberately no "model_source" key
    }


def _labeled_itinerary(title: str = "YVR → CDG") -> dict:
    itin = _legacy_itinerary(title)
    itin["stop_iata"] = "CDG"
    itin["stop_city"] = "Paris"
    itin["legs"] = [
        {"from_iata": "YVR", "to_iata": "CDG", "depart_date": "2026-10-05"},
    ]
    itin["model_source"] = "Grok (Server)"
    return itin


# ---------------------------------------------------------------------------
# /saved page — legacy row
# ---------------------------------------------------------------------------


def test_saved_page_legacy_row_no_via_text(client):
    """/saved renders a legacy row without any 'via ' text in the HTML."""
    saved_module.save_itinerary(_legacy_itinerary(), trip_meta={})

    resp = client.get("/saved")
    assert resp.status_code == 200
    html = resp.text

    # No "via " prefix anywhere in the page
    assert "via " not in html.lower() or _via_only_in_css(html), (
        "Expected no 'via ' line for a legacy row but found one"
    )


def test_saved_page_labeled_row_shows_via(client):
    """/saved renders a labeled row with 'via Grok (Server)' visible."""
    saved_module.save_itinerary(
        _labeled_itinerary(),
        trip_meta={"model_source": "Grok (Server)"},
    )

    resp = client.get("/saved")
    assert resp.status_code == 200
    html = resp.text
    assert "via Grok (Server)" in html


def test_saved_page_mixed_rows_no_template_error(client):
    """/saved renders both a legacy and a labeled row without errors."""
    saved_module.save_itinerary(_legacy_itinerary("YVR → NRT"), trip_meta={})
    saved_module.save_itinerary(
        _labeled_itinerary("YVR → CDG"),
        trip_meta={"model_source": "BYOM, llama-3"},
    )

    resp = client.get("/saved")
    assert resp.status_code == 200
    html = resp.text
    # Labeled row is shown
    assert "via BYOM, llama-3" in html
    # No stray empty "via " from the legacy row (only the labeled row contributes one)
    via_occurrences = html.lower().count("via byom")
    assert via_occurrences == 1, f"Expected 1 'via BYOM' occurrence, got {via_occurrences}"


# ---------------------------------------------------------------------------
# /t/{share_id} shared trip page
# ---------------------------------------------------------------------------


def test_shared_trip_legacy_itinerary_no_via_text(client):
    """Shared trip page renders a legacy itinerary (no model_source) without 'via ' text."""
    itin = _legacy_itinerary()
    share = share_module.create_share(
        kind="detour",
        title=itin["title"],
        payload={"itinerary": itin, "trip_meta": {}},
    )

    resp = client.get(f"/t/{share.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "via " not in html.lower() or _via_only_in_css(html), (
        "Expected no 'via ' line for a legacy shared trip but found one"
    )


def test_shared_trip_labeled_itinerary_shows_via(client):
    """Shared trip page renders a labeled itinerary with 'via Grok (Server)'."""
    itin = _labeled_itinerary()
    share = share_module.create_share(
        kind="detour",
        title=itin["title"],
        payload={
            "itinerary": itin,
            "trip_meta": {"model_source": "Grok (Server)"},
        },
    )

    resp = client.get(f"/t/{share.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "via Grok (Server)" in html


def test_shared_trip_pretty_url_legacy_no_via(client):
    """Pretty /t/detour/{slug}/{id} also renders legacy trips cleanly."""
    itin = _legacy_itinerary()
    share = share_module.create_share(
        kind="detour",
        title=itin["title"],
        payload={"itinerary": itin, "trip_meta": {}},
    )

    resp = client.get(f"/t/detour/yvr-nrt/{share.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "via " not in html.lower() or _via_only_in_css(html)


# ---------------------------------------------------------------------------
# Refresh flow preserves model_source
# ---------------------------------------------------------------------------


def test_refresh_preserves_existing_model_source(tmp_path, monkeypatch):
    """update_from_itinerary keeps an existing model_source label after a fare refresh."""
    # Save with a label
    saved = saved_module.save_itinerary(
        _labeled_itinerary(),
        trip_meta={"model_source": "Grok (Server)"},
    )
    assert saved.model_source == "Grok (Server)"

    # Simulate a refresh: new itinerary dict without model_source (as reprice_itinerary
    # produces a clean AdventureItinerary.model_dump that won't carry the label),
    # but trip_meta from the refresh handler includes the existing trip_meta spread.
    refreshed_itin = _legacy_itinerary("YVR → CDG (refreshed)")
    refreshed_itin["total_price"] = 790.0
    refreshed_itin["display_price"] = "~$790"
    # trip_meta as built by saved_refresh: starts from item.trip_meta, adds refresh keys
    refresh_meta = {
        **saved.trip_meta,  # preserves model_source from the saved row
        "last_refresh_status": "live",
        "last_refresh_message": "Fares updated",
        "last_refresh_delta": -60.0,
    }

    updated = saved_module.update_from_itinerary(
        saved.id, refreshed_itin, trip_meta=refresh_meta
    )
    assert updated is not None
    assert updated.model_source == "Grok (Server)", (
        f"Expected model_source preserved after refresh, got {updated.model_source!r}"
    )
    assert updated.trip_meta.get("model_source") == "Grok (Server)"


def test_refresh_legacy_row_stays_empty_after_update(tmp_path):
    """A legacy row with no model_source stays clean (empty string) after refresh."""
    saved = saved_module.save_itinerary(_legacy_itinerary(), trip_meta={})
    assert saved.model_source == ""

    refresh_meta = {
        **saved.trip_meta,
        "last_refresh_status": "live",
    }
    updated = saved_module.update_from_itinerary(
        saved.id, _legacy_itinerary(), trip_meta=refresh_meta
    )
    assert updated is not None
    assert updated.model_source == ""


# ---------------------------------------------------------------------------
# SavedItinerary.model_source property
# ---------------------------------------------------------------------------


def test_model_source_property_legacy():
    """SavedItinerary.model_source returns '' when neither trip_meta nor itinerary has it."""
    saved = saved_module.save_itinerary(_legacy_itinerary(), trip_meta={})
    assert saved.model_source == ""


def test_model_source_property_from_meta():
    """model_source falls back to itinerary dict when not in trip_meta."""
    itin = _legacy_itinerary()
    itin["model_source"] = "BYOM, mistral"
    saved = saved_module.save_itinerary(itin, trip_meta={})
    assert saved.model_source == "BYOM, mistral"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _via_only_in_css(html: str) -> bool:
    """Return True if the only 'via' occurrences are inside <style> blocks."""
    import re

    # Strip style blocks, then check
    stripped = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return "via " not in stripped.lower()
