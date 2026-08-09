"""Regression test: branded 500 page when the database is unreachable.

Confirms that ``unhandled_exception_handler`` in yonder/web.py catches
database exceptions and renders the branded 500.html template instead of
letting a raw crash or unbranded "Internal Server Error" reach the user.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import yonder.web as web_module


@pytest.fixture()
def client():
    # raise_server_exceptions=False lets FastAPI's exception handlers run
    # rather than re-raising the exception into the test process.
    return TestClient(web_module.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _patch_db_unreachable(monkeypatch):
    """Make list_saved raise an OperationalError, simulating a DB outage.

    web.py imports list_saved by name at startup, so we patch the reference
    that lives in the web module's own namespace.
    """
    import psycopg2

    def _broken(*args, **kwargs):
        raise psycopg2.OperationalError("could not connect to server: Connection refused")

    # Patch the name as it exists in web_module's namespace (imported at startup)
    monkeypatch.setattr(web_module, "list_saved", _broken)
    # Also silence count_saved used in _error_ctx so the error page itself renders
    monkeypatch.setattr(web_module, "count_saved", lambda: 0)


class TestDatabaseError500Page:
    """The /saved route calls list_saved() at the top level (no try/except),
    so a DB failure propagates to FastAPI's exception handler."""

    def test_returns_http_500(self, client):
        """DB outage on a route → HTTP 500 response."""
        resp = client.get("/saved")
        assert resp.status_code == 500, (
            f"Expected HTTP 500 when DB is unreachable, got {resp.status_code}"
        )

    def test_renders_branded_headline(self, client):
        """500 page must show the 'Something went wrong' headline from 500.html."""
        resp = client.get("/saved")
        assert "Something went wrong" in resp.text, (
            "Expected branded 'Something went wrong' headline in 500 response, "
            "got an unbranded page or raw error"
        )

    def test_contains_nav(self, client):
        """500 page must include the 'Back to Explore' link so users can recover."""
        resp = client.get("/saved")
        assert "Back to Explore" in resp.text, (
            "Expected 'Back to Explore' nav link in branded 500 response"
        )

    def test_is_html_not_json(self, client):
        """Response must be HTML, not a raw FastAPI JSON error body."""
        resp = client.get("/saved")
        content_type = resp.headers.get("content-type", "")
        assert content_type.startswith("text/html"), (
            f"Expected text/html for branded 500, got content-type: {content_type}"
        )
        assert '"detail"' not in resp.text, (
            "Response looks like a raw FastAPI JSON error — expected branded HTML"
        )
