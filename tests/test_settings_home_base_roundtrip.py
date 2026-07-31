"""Task 277 — Home Base card round-trip smoke test.

HOME_IATA and DEFAULT_CURRENCY were merged into a single fieldset in
settings.html. Confirm the POST form still picks up both values, they persist
through the settings save, and the reloaded page shows them in both fields
plus the "Flying from [IATA] · fares in [CCY]" hint line.

Uses a temporary .env so the developer's real settings are untouched.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import yonder.config as config_module
import yonder.settings_store as store_module
import yonder.web as web_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import os

    env_file = tmp_path / ".env"
    # Settings save writes here…
    monkeypatch.setattr(store_module, "ENV_PATH", env_file)
    # …and Settings() reads from here on reload.
    monkeypatch.setitem(config_module.Settings.model_config, "env_file", str(env_file))
    # Real process env must not shadow the file values. write_env also mirrors
    # saved values into os.environ, so snapshot & restore all managed keys.
    saved_env = {k: os.environ.get(k) for k, *_ in store_module.MANAGED_KEYS}
    for key in saved_env:
        monkeypatch.delenv(key, raising=False)
    config_module.reload_settings()
    try:
        yield TestClient(web_module.app, raise_server_exceptions=True)
    finally:
        for key, val in saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        config_module.reload_settings()


# The real settings form always submits the TESTING select alongside the Home
# Base fieldset; without it write_env mirrors TESTING="" into os.environ and
# Settings() bool parsing fails.
_FORM_BASE = {"TESTING": "false"}


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_home_base_fields_round_trip(client):
    resp = client.post(
        "/settings",
        data={**_FORM_BASE, "HOME_IATA": "yvr", "DEFAULT_CURRENCY": "cad"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.request.url.path == "/settings"

    # Persisted (normalized to upper-case) in the env store
    env = store_module.read_env()
    assert env.get("HOME_IATA") == "YVR"
    assert env.get("DEFAULT_CURRENCY") == "CAD"

    html = resp.text

    # IATA input prefilled
    iata_input = re.search(r'<input[^>]*name="HOME_IATA"[^>]*>', html)
    assert iata_input, "HOME_IATA input missing from settings page"
    assert 'value="YVR"' in iata_input.group(0)

    # Currency select shows CAD as selected
    select = re.search(
        r'<select[^>]*name="DEFAULT_CURRENCY"[^>]*>.*?</select>', html, re.S
    )
    assert select, "DEFAULT_CURRENCY select missing from settings page"
    selected = re.search(
        r'<option value="([A-Z]{3})"\s+selected', select.group(0)
    )
    assert selected and selected.group(1) == "CAD"

    # Hint line reads "Flying from YVR · fares in CAD"
    hint = re.search(
        r'<p class="home-base-hint" id="home-base-hint">.*?</p>', html, re.S
    )
    assert hint, "home-base hint line missing"
    hint_text = re.sub(r"\s+", " ", _strip_tags(hint.group(0))).strip()
    assert hint_text == "Flying from YVR · fares in CAD"


def test_home_base_second_save_replaces_values(client):
    client.post(
        "/settings",
        data={**_FORM_BASE, "HOME_IATA": "YVR", "DEFAULT_CURRENCY": "CAD"},
        follow_redirects=True,
    )
    resp = client.post(
        "/settings",
        data={**_FORM_BASE, "HOME_IATA": "LHR", "DEFAULT_CURRENCY": "GBP"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    env = store_module.read_env()
    assert env.get("HOME_IATA") == "LHR"
    assert env.get("DEFAULT_CURRENCY") == "GBP"
    hint = re.search(
        r'<p class="home-base-hint" id="home-base-hint">.*?</p>', resp.text, re.S
    )
    hint_text = re.sub(r"\s+", " ", _strip_tags(hint.group(0))).strip()
    assert hint_text == "Flying from LHR · fares in GBP"
