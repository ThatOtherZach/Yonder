from __future__ import annotations

import json

import pytest

from yonder.encyclopedia import get_any_cached_for_iata, get_cached


@pytest.fixture(autouse=True)
def _isolated(pg_schema):
    yield


def _insert(pg_schema, key: str, payload: object, *, fetched_at: float = 1.0) -> None:
    with pg_schema() as conn:
        conn.execute(
            """
            INSERT INTO place_briefs (cache_key, payload_json, fetched_at)
            VALUES (%s, %s, %s)
            """,
            (key, json.dumps(payload), fetched_at),
        )
        conn.commit()


def test_exact_lookup_returns_very_old_valid_field_note(pg_schema):
    payload = {"title": "Lisbon", "tagline": "Old knowledge still travels."}
    _insert(pg_schema, "LIS|PT|lisbon", payload)

    assert get_cached("LIS|PT|lisbon") == payload


def test_iata_fallback_returns_very_old_valid_field_note(pg_schema):
    payload = {"title": "Lisbon", "tagline": "Shared across every trip."}
    _insert(pg_schema, "LIS|PT|lisbon", payload)

    assert get_any_cached_for_iata("LIS") == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Legacy Lisbon", "era_note": "No merged tagline yet."},
        ["not", "a", "field", "note"],
    ],
)
def test_old_invalid_entries_remain_rejected(pg_schema, payload):
    _insert(pg_schema, "LIS|PT|lisbon", payload)

    assert get_cached("LIS|PT|lisbon") is None
    assert get_any_cached_for_iata("LIS") is None