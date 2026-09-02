"""Regression tests: quest share pages must not 500 on string segment times.

Quest legs come from the share DB as JSON, so segment departure/arrival
fields arrive as ISO strings, not datetimes. The leg_stops macro used to
subtract them directly (`seg.departure - prev.arrival`) and call
`.strftime()` on them, crashing with a TypeError and breaking every
"Open Quest" button in the Quest Library.

Covers:
  - quest share page returns 200 with ISO-string segment times
  - layover + arrive/depart text renders correctly for string times
  - macro renders correctly with real datetime objects (escape/detour path)
  - missing/unparseable times degrade gracefully (no layover text, no crash)
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import yonder.share as share_module
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield


@pytest.fixture()
def client():
    return TestClient(web_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEGMENTS_STR = [
    {
        "origin": "YVR",
        "destination": "FRA",
        "departure": "2026-11-15T10:00:00",
        "arrival": "2026-11-16T05:30:00",
    },
    {
        "origin": "FRA",
        "destination": "IST",
        "departure": "2026-11-16T07:45:00",
        "arrival": "2026-11-16T11:50:00",
    },
]


def _quest_share(segments) -> object:
    """Quest share whose inbound leg offer carries multi-segment routing."""
    return share_module.create_share(
        kind="quest",
        title="Vancouver → overland → Istanbul",
        payload={
            "idea": {
                "kind": "quest",
                "entry_iata": "FRA",
                "entry_city": "Frankfurt",
                "exit_iata": "IST",
                "exit_city": "Istanbul",
                "theme_label": "Rails & Ruins",
                "theme_primary": "#e6b450",
                "depart_date": "2026-11-15",
                "outbound_date": "2026-11-29",
                "currency": "CAD",
                "inbound_fare_missing": False,
                "outbound_fare_missing": True,
                "inbound_leg": {
                    "offer": {
                        "price": 780.0,
                        "currency": "CAD",
                        "price_kind": "mock",
                        "airlines": ["LH", "TK"],
                        "stops_out": 1,
                        "segments_out": segments,
                    }
                },
                "outbound_leg": None,
            },
            "home_iata": "YVR",
            "trip_meta": {"vibe": "adventure"},
        },
    )


def _render_leg_stops(segments) -> str:
    """Render the leg_stops macro directly through the app's Jinja env."""
    tpl = web_module.templates.env.from_string(
        "{% from '_boarding_pass.html' import leg_stops %}{{ leg_stops(segments) }}"
    )
    return tpl.render(segments=segments)


# ---------------------------------------------------------------------------
# Tests — quest share route
# ---------------------------------------------------------------------------


class TestQuestSharePage:
    def test_quest_share_with_string_segment_times_returns_200(self, client):
        share = _quest_share(_SEGMENTS_STR)
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert '<span class="trip-title-part">to</span>' in resp.text
        assert '<span class="trip-title-part">overland</span>' not in resp.text

    def test_layover_text_rendered_from_string_times(self, client):
        share = _quest_share(_SEGMENTS_STR)
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        # FRA arrive 05:30 → depart 07:45 = 2h 15m layover
        assert "2h 15m layover" in resp.text
        assert "arrive 05:30" in resp.text
        assert "→ depart 07:45" in resp.text

    def test_quest_share_with_missing_times_returns_200(self, client):
        segs = [
            {"origin": "YVR", "destination": "FRA"},
            {"origin": "FRA", "destination": "IST"},
        ]
        share = _quest_share(segs)
        resp = client.get(f"/t/{share.id}")
        assert resp.status_code == 200
        assert "m layover" not in resp.text


# ---------------------------------------------------------------------------
# Tests — leg_stops macro directly (all input types)
# ---------------------------------------------------------------------------


class TestLegStopsMacro:
    def test_string_times_no_exception(self):
        html = _render_leg_stops(_SEGMENTS_STR)
        assert "2h 15m layover" in html
        assert "arrive 05:30" in html

    def test_datetime_objects_still_work(self):
        segs = [
            {
                "origin": "YVR",
                "departure": datetime(2026, 11, 15, 10, 0),
                "arrival": datetime(2026, 11, 16, 5, 30),
            },
            {
                "origin": "FRA",
                "departure": datetime(2026, 11, 16, 6, 15),
                "arrival": datetime(2026, 11, 16, 11, 50),
            },
        ]
        html = _render_leg_stops(segs)
        assert "45m layover" in html
        assert "arrive 05:30" in html
        assert "→ depart 06:15" in html

    def test_unparseable_times_degrade_gracefully(self):
        segs = [
            {"origin": "YVR", "arrival": "not-a-time"},
            {"origin": "FRA", "departure": "also-bad"},
        ]
        html = _render_leg_stops(segs)
        assert "layover" not in html
        assert "arrive" not in html
        assert "FRA" in html

    def test_missing_fields_render_stop_without_times(self):
        segs = [{"origin": "YVR"}, {"origin": "FRA"}]
        html = _render_leg_stops(segs)
        assert "FRA" in html
        assert "layover" not in html

    def test_z_suffix_iso_strings_parse(self):
        segs = [
            {"origin": "YVR", "arrival": "2026-11-16T05:30:00Z"},
            {"origin": "FRA", "departure": "2026-11-16T07:00:00Z"},
        ]
        html = _render_leg_stops(segs)
        assert "1h 30m layover" in html
