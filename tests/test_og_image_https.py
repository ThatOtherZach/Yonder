"""Regression tests: og:image meta tags use an environment-safe https URL.

General pages use the production image. Share pages use the same environment
origin as their share record so Preview records never point at production.

A missing tag or an http:// URL would break social previews on Twitter/X, Slack,
and iMessage.  These tests catch a route that forgets to call _base_ctx() or
overrides og_image with a relative or http:// value.

Pages covered:
  - /          home page  (index.html via _compose_page_ctx)
  - /saved      saved trips (saved.html via _base_ctx)
  - /t/{id}    shared trip (trip.html, explicit og_image override + _base_ctx)
  - /t/{kind}/{slug}/{id}  SEO pretty-URL alias for shared trips
  - /t/missing  404 state of shared trip page (still uses _base_ctx)
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import yonder.share as share_module
import yonder.web as web_module

# Canonical general-page image and deterministic Preview share image.
_EXPECTED_OG_IMAGE = "https://yonder.city/static/share_bg.jpg"
_EXPECTED_PREVIEW_OG_IMAGE = "https://preview.example.replit.dev/static/share_bg.jpg"

# Regex to extract content="…" from the og:image meta tag.
_OG_IMAGE_RE = re.compile(
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    """Throwaway PG schema + no live API keys for every test in this module."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    monkeypatch.setenv("REPLIT_DEV_DOMAIN", "preview.example.replit.dev")
    monkeypatch.setattr(web_module, "_IS_HTTPS", False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_og_image(html: str) -> str | None:
    """Return the content attribute of the og:image meta tag, or None."""
    m = _OG_IMAGE_RE.search(html)
    return m.group(1) if m else None


def _assert_og_image(
    html: str,
    page: str,
    expected: str = _EXPECTED_OG_IMAGE,
) -> str:
    """Assert og:image is present and equals the expected HTTPS URL."""
    og = _extract_og_image(html)
    assert og is not None, (
        f"og:image meta tag is missing on {page!r}"
    )
    assert og == expected, (
        f"og:image on {page!r}: expected {expected!r}, got {og!r}"
    )
    assert og.startswith("https://"), (
        f"og:image on {page!r} must use https://, got {og!r}"
    )
    assert not og.startswith("http://"), (
        f"og:image on {page!r} must not use plain http://, got {og!r}"
    )
    return og


# ---------------------------------------------------------------------------
# Home page  /
# ---------------------------------------------------------------------------


class TestHomeOgImage:
    """The home page must include a valid https:// og:image tag."""

    def test_og_image_present(self, client):
        resp = client.get("/")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        og = _extract_og_image(resp.text)
        assert og is not None, "og:image meta tag is missing from the home page ('/')"

    def test_og_image_is_https_yonder_city(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        _assert_og_image(resp.text, "/")

    def test_og_image_not_http(self, client):
        """Must never be a plain http:// URL (crawlers may reject it)."""
        resp = client.get("/")
        assert resp.status_code == 200
        og = _extract_og_image(resp.text)
        assert og is not None, "og:image meta tag missing on '/'"
        assert not og.startswith("http://"), (
            f"og:image on '/' must not use http://, got {og!r}"
        )


# ---------------------------------------------------------------------------
# Saved trips page  /saved
# ---------------------------------------------------------------------------


class TestSavedOgImage:
    """The saved trips page uses _base_ctx and must emit a valid og:image."""

    def test_og_image_present(self, client):
        resp = client.get("/saved")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        og = _extract_og_image(resp.text)
        assert og is not None, "og:image meta tag is missing from '/saved'"

    def test_og_image_is_https_yonder_city(self, client):
        resp = client.get("/saved")
        assert resp.status_code == 200
        _assert_og_image(resp.text, "/saved")

    def test_og_image_not_http(self, client):
        resp = client.get("/saved")
        assert resp.status_code == 200
        og = _extract_og_image(resp.text)
        assert og is not None, "og:image meta tag missing on '/saved'"
        assert not og.startswith("http://"), (
            f"og:image on '/saved' must not use http://, got {og!r}"
        )


# ---------------------------------------------------------------------------
# Shared trip page  /t/{share_id}
# ---------------------------------------------------------------------------


def _make_escape_share() -> object:
    """Create a minimal escape share for og:image tests."""
    return share_module.create_share(
        kind="escape",
        title="YVR → NRT",
        payload={
            "query": {
                "origin": "YVR",
                "destination": "NRT",
                "depart": "2026-11-15",
                "adults": 1,
                "currency": "USD",
                "nonstop": False,
            },
            "offer": {
                "price": 750.0,
                "currency": "USD",
                "price_kind": "mock",
            },
            "vibe": "adventure",
        },
    )


class TestSharedTripOgImage:
    """The shared trip page sets og:image explicitly and must emit https://."""

    def test_og_image_present(self, client):
        share = _make_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        og = _extract_og_image(resp.text)
        assert og is not None, (
            f"og:image meta tag is missing from shared trip page '/t/{share.id}'"
        )

    def test_og_image_uses_preview_share_origin(self, client):
        share = _make_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        _assert_og_image(
            resp.text,
            f"/t/{share.id}",
            expected=_EXPECTED_PREVIEW_OG_IMAGE,
        )

    def test_og_image_not_http(self, client):
        share = _make_escape_share()
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        og = _extract_og_image(resp.text)
        assert og is not None, "og:image meta tag missing on shared trip page"
        assert not og.startswith("http://"), (
            f"og:image on '/t/{{share_id}}' must not use http://, got {og!r}"
        )

    def test_pretty_url_og_image(self, client):
        """The SEO-friendly /t/{kind}/{slug}/{share_id} alias must also emit og:image."""
        share = _make_escape_share()
        resp = client.get(f"/t/escape/YVR-NRT/{share.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        _assert_og_image(
            resp.text,
            f"/t/escape/YVR-NRT/{share.id}",
            expected=_EXPECTED_PREVIEW_OG_IMAGE,
        )


# ---------------------------------------------------------------------------
# 404 state of shared trip page (missing / expired share)
# ---------------------------------------------------------------------------


class TestMissingShareOgImage:
    """Even the 404 state of the shared trip page must emit og:image.

    The 404 branch of _render_shared_trip spreads _base_ctx into the template
    context, so og:image must still appear.
    """

    def test_og_image_present_on_404(self, client):
        resp = client.get("/t/nonexistent-share-id-xyz")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        og = _extract_og_image(resp.text)
        assert og is not None, (
            "og:image meta tag is missing from the 404 shared trip page"
        )

    def test_og_image_is_https_yonder_city_on_404(self, client):
        resp = client.get("/t/nonexistent-share-id-xyz")
        assert resp.status_code == 404
        _assert_og_image(resp.text, "/t/<missing>")
