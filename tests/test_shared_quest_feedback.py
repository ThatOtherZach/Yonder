"""Regression coverage for feedback from shared Quest cards.

Shared Quest cards use the common result-feedback endpoint, but their context
comes from the share payload rather than the Explore page.  Keep this test at
the shared-page boundary so changes to either the share template or the
feedback request cannot silently drop the Quest vibe, entry airport, or
original prompt.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

import yonder.share as share_module
import yonder.web as web_module


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


class _FeedbackParser(HTMLParser):
    """Capture rendered shared-Quest feedback controls without extra deps."""

    def __init__(self) -> None:
        super().__init__()
        self.feedback_attrs: list[dict[str, str]] = []
        self.buttons: list[str] = []
        self._in_feedback = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag == "div" and attrs_dict.get("data-quest-feedback") == "1":
            self.feedback_attrs.append(attrs_dict)
            self._in_feedback = True
        elif tag == "button" and self._in_feedback:
            self.buttons.append(attrs_dict.get("class", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_feedback:
            self._in_feedback = False


_PROMPT = "Find a slow rail-and-river Quest from Vancouver"


def _quest_share():
    return share_module.create_share(
        kind="quest",
        title="Vancouver to Hanoi Quest",
        payload={
            "idea": {
                "kind": "quest",
                "entry_iata": "HAN",
                "exit_iata": "BKK",
                "entry_city": "Hanoi",
                "exit_city": "Bangkok",
                "theme_label": "Adventure",
                "theme_primary": "#e6b450",
                "inbound_fare_missing": True,
                "outbound_fare_missing": True,
            },
            "home_iata": "YVR",
            "trip_meta": {"vibe": "adventure", "prompt": _PROMPT},
        },
    )


def _feedback_attrs(html: str) -> dict[str, str]:
    parser = _FeedbackParser()
    parser.feed(html)
    assert len(parser.feedback_attrs) == 1
    assert {"thumb-up", "thumb-down"} <= {
        class_name
        for class_attr in parser.buttons
        for class_name in class_attr.split()
    }
    return parser.feedback_attrs[0]


def test_shared_quest_votes_post_rendered_context_for_both_directions(
    client, monkeypatch
):
    """Thumbs-up and thumbs-down preserve the shared Quest's full context."""
    share = _quest_share()
    page = client.get(f"/t/{share.id}")
    assert page.status_code == 200
    attrs = _feedback_attrs(page.text)
    assert attrs["data-vibe"] == "adventure"
    assert attrs["data-dest"] == "HAN"
    assert attrs["data-query"] == _PROMPT

    captured: list[dict] = []

    def record_feedback(**payload):
        captured.append(payload)
        return f"feedback-{len(captured)}"

    # Avoid coupling this template regression to the learning-store side
    # effects; the endpoint call itself is the integration boundary under test.
    monkeypatch.setattr("yonder.feedback.record_feedback", record_feedback)
    monkeypatch.setattr("yonder.knowledge.reinforce_from_feedback", lambda **_: True)
    monkeypatch.setattr("yonder.vibe_signals.upsert_signal", lambda **_: None)
    monkeypatch.setattr("yonder.vibe_signals.record_rejection", lambda **_: None)
    monkeypatch.setattr(
        "yonder.feedback.upsert_vibe_question", lambda **_: ("question-1", False)
    )

    for direction in ("up", "down"):
        response = client.post(
            "/api/result-feedback",
            json={
                "direction": direction,
                "vibe": attrs["data-vibe"],
                "dest_iata": attrs["data-dest"],
                "query": attrs["data-query"],
            },
        )
        assert response.status_code == 200
        assert response.json()["direction"] == direction

    assert captured == [
        {
            "direction": "up",
            "vibe": "adventure",
            "dest_iata": "HAN",
            "query": _PROMPT,
            "session_hash": captured[0]["session_hash"],
            "quest_saved_id": None,
        },
        {
            "direction": "down",
            "vibe": "adventure",
            "dest_iata": "HAN",
            "query": _PROMPT,
            "session_hash": captured[1]["session_hash"],
            "quest_saved_id": None,
        },
    ]


def test_shared_quest_feedback_handler_restores_both_buttons_on_failure():
    """The shared-page handler must undo its optimistic state on fetch failure."""
    template = Path("yonder/templates/trip.html").read_text()
    handler = template.split("// Shared Quest feedback uses", 1)[1].split(
        "// Field note expand", 1
    )[0]
    failure_path = handler.split("}).catch(function () {", 1)[1].split(
        "});", 1
    )[0]

    assert 'fetch("/api/result-feedback"' in handler
    assert "upBtn.classList.remove(\"voted-up\")" in failure_path
    assert "downBtn.classList.remove(\"voted-down\")" in failure_path
    assert "upBtn.disabled = false" in failure_path
    assert "downBtn.disabled = false" in failure_path


def test_shared_quest_feedback_works_in_a_real_browser(client):
    """Shared Quest votes use the live handler and recover from a rejected vote."""
    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("Chromium is required for shared Quest feedback regressions")

    share = _quest_share()
    page_response = client.get(f"/t/{share.id}")
    assert page_response.status_code == 200

    captured: list[dict] = []
    pending_routes = []

    def intercept_feedback(route, request):
        assert request.method == "POST"
        captured.append(json.loads(request.post_data or "{}"))
        # Reject the first vote so the browser must clear the optimistic state
        # before the opposite-direction vote can be submitted.
        if len(captured) == 1:
            pending_routes.append(route)
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chromium,
            args=["--no-sandbox"],
        )
        page = browser.new_page()
        # set_content keeps this test on the exact rendered shared-page HTML.
        # The base makes the relative fetch an actual browser request, which
        # Playwright can observe and reject/accept through the route above.
        html = page_response.text.replace(
            "<head>", '<head><base href="http://shared-quest.test/">', 1
        )
        page.route("**/api/result-feedback", intercept_feedback)
        page.set_content(html, wait_until="domcontentloaded")

        thumbs = page.locator('.bp-thumbs[data-quest-feedback="1"]')
        assert thumbs.count() == 1
        up_button = thumbs.locator(".thumb-up")
        down_button = thumbs.locator(".thumb-down")

        with page.expect_request("**/api/result-feedback") as first_request:
            up_button.click()
        assert first_request.value.method == "POST"
        assert up_button.is_disabled()
        assert down_button.is_disabled()
        assert up_button.evaluate(
            "(button) => button.classList.contains('voted-up')"
        )
        assert not down_button.evaluate(
            "(button) => button.classList.contains('voted-down')"
        )
        assert len(pending_routes) == 1
        pending_routes[0].fulfill(
            status=500,
            content_type="application/json",
            body="{}",
        )
        page.wait_for_function(
            """() => {
                const container = document.querySelector(
                  '.bp-thumbs[data-quest-feedback="1"]'
                );
                const buttons = container.querySelectorAll(".thumb-btn");
                return [...buttons].every((button) => !button.disabled) &&
                  !container.querySelector(".voted-up") &&
                  !container.querySelector(".voted-down");
            }"""
        )
        assert captured[0] == {
            "direction": "up",
            "vibe": "adventure",
            "dest_iata": "HAN",
            "query": _PROMPT,
            "quest_saved_id": "",
        }
        assert up_button.is_enabled()
        assert down_button.is_enabled()
        assert not up_button.evaluate(
            "(button) => button.classList.contains('voted-up')"
        )
        assert not down_button.evaluate(
            "(button) => button.classList.contains('voted-down')"
        )

        with page.expect_request("**/api/result-feedback") as second_request:
            down_button.click()
        assert second_request.value.method == "POST"
        page.wait_for_function(
            """() => document.querySelector(
                '.bp-thumbs[data-quest-feedback="1"] .thumb-down'
            ).disabled"""
        )
        assert captured[1] == {
            "direction": "down",
            "vibe": "adventure",
            "dest_iata": "HAN",
            "query": _PROMPT,
            "quest_saved_id": "",
        }
        assert up_button.is_disabled()
        assert down_button.is_disabled()
        assert down_button.evaluate(
            "(button) => button.classList.contains('voted-down')"
        )
        browser.close()


def test_shared_quest_feedback_uses_shared_explore_button_styles():
    """Shared Quest feedback controls must use the Explore result presentation."""
    base = Path("yonder/templates/base.html").read_text()
    assert ".bp-thumbs {" in base
    assert ".thumb-btn.thumb-up" in base
    assert ".thumb-btn.thumb-down" in base
    assert "@media (max-width: 575.98px)" in base


def test_shared_escape_and_detour_cards_still_have_no_feedback_controls(client):
    """The shared-Quest feedback addition must not change other share cards."""
    for kind, payload in (
        (
            "escape",
            {
                "query": {"origin": "YVR", "destination": "NRT"},
                "offer": {"price": 450, "currency": "USD"},
                "vibe": "adventure",
            },
        ),
        (
            "detour",
            {
                "itinerary": {
                    "kind": "stopover",
                    "title": "Tokyo Stopover",
                    "stop_iata": "TYO",
                    "stop_city": "Tokyo",
                    "legs": [
                        {
                            "from_iata": "YVR",
                            "to_iata": "TYO",
                            "depart_date": "2026-10-01",
                        }
                    ],
                },
                "trip_meta": {"vibe": "adventure"},
            },
        ),
    ):
        share = share_module.create_share(
            kind=kind, title=f"Shared {kind}", payload=payload
        )
        page = client.get(f"/t/{share.id}")
        assert page.status_code == 200
        assert 'data-quest-feedback="1"' not in page.text