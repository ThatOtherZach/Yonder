"""Reply-language matching — community suggestions follow the prompt language.

The /api/vibe-suggestions endpoint detects the language of the current prompt
text (q) server-side and only returns suggestions written in that language, so
an English prompt never surfaces a Chinese/Spanish suggestion and vice versa.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import yonder.feedback as feedback
from yonder.lang import detect_lang
from yonder.web import app


@pytest.fixture()
def client(pg_schema, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    return TestClient(app)


def _seed(vibe: str, query: str, suggestion: str, lang: str | None) -> None:
    answer: dict = {"suggestion": suggestion, "dest_iata": None}
    if lang:
        answer["lang"] = lang
    with feedback.get_conn() as conn:
        conn.execute(
            "INSERT INTO vibe_questions (id, vibe, query_norm, created_at, answer_json, answer_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4().hex,
                vibe,
                feedback._norm_query(query),
                time.time(),
                json.dumps(answer),
                time.time(),
            ),
        )


def test_detect_lang_basics():
    assert detect_lang("Vancouver to Toronto in September") == "en"
    assert detect_lang("从温哥华到多伦多，九月出发") == "zh"
    assert detect_lang("Quiero un vuelo barato desde Madrid a la playa") == "es"
    assert detect_lang("Je veux un vol pas cher vers la plage la semaine prochaine") == "fr"
    assert detect_lang("") == "en"


def test_spanish_prompt_gets_spanish_suggestions_only(client):
    _seed("adventure", "cheap beach trip somewhere warm", "Try Lisbon for sun (LIS).", "en")
    _seed(
        "adventure",
        "quiero una playa barata con buena comida",
        "Prueba Lisboa, sol y marisco barato (LIS).",
        "es",
    )
    resp = client.get(
        "/api/vibe-suggestions",
        params={"vibe": "adventure", "q": "quiero un vuelo barato a la playa con mis días libres"},
    )
    assert resp.status_code == 200
    rows = resp.json()["suggestions"]
    assert rows, "Spanish prompt should surface the Spanish suggestion"
    assert all((r["answer"] or {}).get("lang") == "es" for r in rows)
    assert all("Prueba" in (r["answer"] or {}).get("suggestion", "") for r in rows)


def test_english_prompt_never_surfaces_foreign_suggestions(client):
    _seed("adventure", "cheap beach trip somewhere warm", "Try Lisbon for sun (LIS).", "en")
    _seed("adventure", "quiero una playa barata", "Prueba Lisboa (LIS).", "es")
    _seed("adventure", "便宜的海滩旅行", "试试冲绳 (OKA)。", "zh")
    # Legacy row without a stored lang, foreign query text → filtered by detection
    _seed("adventure", "un vol pas cher vers la plage avec une semaine", "Essaie Nice (NCE).", None)

    resp = client.get(
        "/api/vibe-suggestions",
        params={"vibe": "adventure", "q": "cheap flight to a warm beach in September"},
    )
    assert resp.status_code == 200
    rows = resp.json()["suggestions"]
    assert rows, "English suggestion should still be returned"
    texts = [(r["answer"] or {}).get("suggestion", "") for r in rows]
    assert all("Lisbon" in t for t in texts)
    assert not any("Prueba" in t or "冲绳" in t or "Essaie" in t for t in texts)


def test_no_prompt_defaults_to_english(client):
    _seed("adventure", "便宜的海滩旅行怎么样", "试试冲绳 (OKA)。", "zh")
    _seed("adventure", "cheap beach trip", "Try Lisbon (LIS).", "en")
    resp = client.get("/api/vibe-suggestions", params={"vibe": "adventure"})
    rows = resp.json()["suggestions"]
    assert all((r["answer"] or {}).get("lang") != "zh" for r in rows)
