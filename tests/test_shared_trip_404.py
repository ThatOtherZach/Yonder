"""Regression tests: branded 404 page for broken shared trip links.

Confirms that requesting /t/<nonexistent-id> (or the pretty-URL variant
/t/escape/slug/<nonexistent-id>) returns a 404 response that renders
yonder/templates/404.html rather than a raw FastAPI default or the trip
error state inside trip.html.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    """Isolated PG schema and no live API keys for every test in this module."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Tests — /t/{id} short form
# ---------------------------------------------------------------------------


class TestShortFormMissingTrip:
    """Short-form URL /t/{id} with a nonexistent share ID."""

    def test_returns_404_status(self, client):
        """Missing share ID → HTTP 404."""
        resp = client.get("/t/nonexistent-id")
        assert resp.status_code == 404, (
            f"Expected 404 for missing share ID, got {resp.status_code}"
        )

    def test_renders_branded_404_html(self, client):
        """Missing share ID → branded 404.html content (Yonder nav, 'Off the map')."""
        resp = client.get("/t/nonexistent-id")
        html = resp.text
        # 404.html headline
        assert "Off the map" in html, (
            "Expected '404.html' headline 'Off the map' in response, not an unbranded page"
        )

    def test_contains_nav_link(self, client):
        """Branded 404 includes the 'Back to Explore' link so users can recover."""
        resp = client.get("/t/nonexistent-id")
        html = resp.text
        assert "Back to Explore" in html, (
            "Expected 'Back to Explore' link in branded 404 response"
        )

    def test_no_raw_fastapi_default(self, client):
        """Response must not be the plain FastAPI/Starlette default JSON error."""
        resp = client.get("/t/nonexistent-id")
        assert resp.headers.get("content-type", "").startswith("text/html"), (
            "Expected text/html content-type for branded 404, "
            f"got: {resp.headers.get('content-type')}"
        )
        assert '"detail"' not in resp.text, (
            "Response looks like a raw FastAPI JSON error — expected branded HTML"
        )

    def test_no_trip_error_state(self, client):
        """Response must NOT be the old trip.html error state."""
        resp = client.get("/t/nonexistent-id")
        assert "missing or expired" not in resp.text, (
            "Response contains 'missing or expired' error from trip.html — "
            "expected the branded 404.html page instead"
        )


# ---------------------------------------------------------------------------
# Tests — /t/{kind}/{slug}/{id} pretty-URL form
# ---------------------------------------------------------------------------


class TestPrettyUrlMissingTrip:
    """Pretty-URL form /t/escape/slug/<id> with a nonexistent share ID."""

    def test_returns_404_status(self, client):
        """Missing share ID via pretty URL → HTTP 404."""
        resp = client.get("/t/escape/YVR-NRT-2026-08-20/nonexistent-id")
        assert resp.status_code == 404, (
            f"Expected 404 for missing pretty-URL share, got {resp.status_code}"
        )

    def test_renders_branded_404_html(self, client):
        """Missing share ID via pretty URL → branded 404.html."""
        resp = client.get("/t/escape/YVR-NRT-2026-08-20/nonexistent-id")
        html = resp.text
        assert "Off the map" in html, (
            "Expected branded 404 headline 'Off the map' for missing pretty-URL share"
        )

    def test_no_trip_error_state(self, client):
        """Pretty URL with bad ID must not fall through to the trip.html error view."""
        resp = client.get("/t/escape/YVR-NRT-2026-08-20/nonexistent-id")
        assert "missing or expired" not in resp.text, (
            "Response contains trip.html error state — expected branded 404.html"
        )
