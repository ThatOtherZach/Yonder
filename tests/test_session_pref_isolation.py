"""Per-browser preference isolation (Task: visitor prefs scoped by yv_sess).

Two browsers (two yv_sess cookies) must be able to hold different home
airports, currencies, visited/avoid maps, budgets, and stop-length prefs
without affecting each other, and a brand-new browser must start from
defaults — never another visitor's data.

The session_prefs table is isolated in a throwaway PostgreSQL schema so
the test never touches real rows.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import yonder.session_prefs as session_prefs
from yonder.config import apply_session_prefs, get_settings

_DDL = """
CREATE TABLE "{schema}".session_prefs (
    session_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    updated_at DOUBLE PRECISION,
    PRIMARY KEY (session_id, key)
)
"""


@pytest.fixture()
def isolated_session_prefs(monkeypatch):
    """Point yonder.session_prefs at a throwaway PG schema."""
    import psycopg2

    from yonder.db import Conn

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL required")

    schema = f"test_sp_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(_DDL.format(schema=schema))

    @contextmanager
    def _get_conn():
        raw = psycopg2.connect(url)
        try:
            with raw.cursor() as c:
                c.execute(f'SET search_path TO "{schema}"')
            yield Conn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    monkeypatch.setattr(session_prefs, "get_conn", _get_conn)
    yield
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    admin.close()


def test_store_isolates_sessions(isolated_session_prefs):
    a, b = "sess_a_" + uuid.uuid4().hex[:8], "sess_b_" + uuid.uuid4().hex[:8]
    session_prefs.set_session_prefs(a, {"home_iata": "YVR", "default_currency": "CAD"})
    session_prefs.set_session_prefs(b, {"home_iata": "LHR", "default_currency": "GBP"})

    pa = session_prefs.get_session_prefs(a)
    pb = session_prefs.get_session_prefs(b)
    assert pa["home_iata"] == "YVR" and pa["default_currency"] == "CAD"
    assert pb["home_iata"] == "LHR" and pb["default_currency"] == "GBP"

    # Brand-new session → factory defaults, not another visitor's data
    fresh = session_prefs.get_session_prefs("sess_new_" + uuid.uuid4().hex[:8])
    assert fresh["home_iata"] == ""
    assert fresh["default_currency"] == "USD"
    assert fresh["visited_countries"] == ""
    assert fresh["avoid_countries"] == ""


def test_settings_overlay_per_session(isolated_session_prefs):
    a, b = "sess_a_" + uuid.uuid4().hex[:8], "sess_b_" + uuid.uuid4().hex[:8]
    session_prefs.set_session_prefs(
        a,
        {
            "home_iata": "YVR",
            "default_currency": "CAD",
            "visited_countries": "CA,JP",
            "visited_tiles": "CA,JP",
            "avoid_countries": "US",
            "col_expected_daily": "150",
            "detour_min_stop_days": "2",
            "detour_max_stop_days": "3",
            "return_days": "12",
        },
    )
    base = get_settings()
    sa = apply_session_prefs(base, a)
    sb = apply_session_prefs(base, b)

    assert sa.resolve_home_iata() == "YVR"
    assert sa.default_currency == "CAD"
    assert sa.visited_country_list() == ["CA", "JP"]
    assert sa.avoid_country_list() == ["US"]
    assert sa.col_budget()[0] == 150.0
    assert sa.detour_stop_defaults()[:2] == (2, 3)
    assert sa.return_days == 12

    # Session B is untouched by A's writes
    assert sb.home_iata == ""
    assert sb.default_currency == "USD"
    assert sb.visited_country_list() == []
    assert sb.avoid_country_list() == []
    assert sb.col_budget()[0] is None
    assert sb.return_days == 0

    # BYOM is per-browser: A's endpoint never leaks to B
    session_prefs.set_session_prefs(
        a, {"byom_base_url": "https://api.openai.com/v1", "byom_api_key": "sk-x"}
    )
    sa2 = apply_session_prefs(base, a)
    sb2 = apply_session_prefs(base, b)
    assert sa2.byom_base_url == "https://api.openai.com/v1"
    assert sb2.byom_base_url == ""


def test_two_browsers_end_to_end(isolated_session_prefs, monkeypatch):
    """Settings save + travel-map edits from two cookies stay separate."""
    import yonder.web as web

    client = TestClient(web.app)
    a, b = "browser_a_" + uuid.uuid4().hex[:8], "browser_b_" + uuid.uuid4().hex[:8]

    # Browser A saves home base + budget via the public Settings form
    r = client.post(
        "/settings",
        data={
            "HOME_IATA": "YVR",
            "DEFAULT_CURRENCY": "CAD",
            "COL_EXPECTED_DAILY": "120",
            "DETOUR_MIN_STOP_DAYS": "2",
            "DETOUR_MAX_STOP_DAYS": "6",
        },
        cookies={"yv_sess": a},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Browser B saves a different home base
    r = client.post(
        "/settings",
        data={"HOME_IATA": "LHR", "DEFAULT_CURRENCY": "GBP"},
        cookies={"yv_sess": b},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Browser A marks countries on the map; B avoids one
    r = client.post(
        "/api/travel-map",
        json={"visited": ["CA", "JP"]},
        cookies={"yv_sess": a},
    )
    assert r.status_code == 200 and r.json()["ok"]
    r = client.post(
        "/api/travel-map",
        json={"avoid": ["US"]},
        cookies={"yv_sess": b},
    )
    assert r.status_code == 200 and r.json()["ok"]

    pa = session_prefs.get_session_prefs(a)
    pb = session_prefs.get_session_prefs(b)
    assert pa["home_iata"] == "YVR" and pa["default_currency"] == "CAD"
    assert pb["home_iata"] == "LHR" and pb["default_currency"] == "GBP"
    assert pa["visited_countries"] == "CA,JP" and pa["avoid_countries"] == ""
    assert pb["visited_countries"] == "" and pb["avoid_countries"] == "US"
    assert pa["col_expected_daily"] == "120.0" or pa["col_expected_daily"] == "120"
    assert pa["detour_min_stop_days"] == "2" and pa["detour_max_stop_days"] == "6"

    # Per-browser backup export reflects only that session
    ea = client.get("/api/backup/export", cookies={"yv_sess": a}).json()
    eb = client.get("/api/backup/export", cookies={"yv_sess": b}).json()
    assert ea["travel_map"]["visited"] == ["CA", "JP"]
    assert ea["settings"]["HOME_IATA"] == "YVR"
    assert eb["travel_map"]["visited"] == []
    assert eb["travel_map"]["avoid"] == ["US"]
    assert eb["settings"]["DEFAULT_CURRENCY"] == "GBP"


def test_public_settings_save_cannot_touch_env(isolated_session_prefs, monkeypatch, tmp_path):
    """Provider/API server config is not editable from the public page."""
    import yonder.settings_store as settings_store
    import yonder.web as web

    env_path = tmp_path / ".env"
    monkeypatch.setattr(settings_store, "ENV_PATH", env_path)
    # Force non-testing mode: the env gate must reject server-config writes
    base = get_settings().model_copy(update={"testing": False})
    monkeypatch.setattr(web, "get_settings", lambda: base)

    client = TestClient(web.app)
    sess = "browser_x_" + uuid.uuid4().hex[:8]
    r = client.post(
        "/settings",
        data={
            "HOME_IATA": "AMS",
            "XAI_API_KEY": "sk-evil",
            "AMADEUS_CLIENT_ID": "evil-id",
            "PROVIDER_MODE": "scan_all",
            "TESTING": "true",
        },
        cookies={"yv_sess": sess},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # No .env write happened (testing mode off), personal pref did save
    assert not env_path.exists()
    assert session_prefs.get_session_prefs(sess)["home_iata"] == "AMS"
