"""Unit tests for Aviasales affiliate URL builders in yonder/links.py.

Spec: attached_assets/Yonder-Aviasales-Affiliate-Integration_1785538681277.md
"""

from datetime import date

import pytest

from yonder.links import (
    AVIASALES_MARKER,
    AVIASALES_SUB_ID,
    aviasales_url,
    aviasales_fallback_url,
)

MARKER_QS = f"marker={AVIASALES_MARKER}&sub_id={AVIASALES_SUB_ID}"


# ── aviasales_url ────────────────────────────────────────────────────────────

def test_aviasales_one_way():
    """Spec example: YVR→MOW Aug 18, 1 passenger → YVR1808MOW1."""
    url = aviasales_url("YVR", "MOW", date(2024, 8, 18))
    assert url == f"https://www.aviasales.com/search/YVR1808MOW1?{MARKER_QS}"


def test_aviasales_round_trip():
    """Spec example: YVR→MOW Aug 18 return Aug 28 → YVR1808MOW28081."""
    url = aviasales_url("YVR", "MOW", date(2024, 8, 18), return_date=date(2024, 8, 28))
    assert url == f"https://www.aviasales.com/search/YVR1808MOW28081?{MARKER_QS}"


def test_aviasales_multi_passenger():
    """Multiple adults produce the correct PAX digit(s) in the path."""
    url = aviasales_url("YVR", "MOW", date(2024, 8, 18), adults=3)
    assert url == f"https://www.aviasales.com/search/YVR1808MOW3?{MARKER_QS}"


def test_aviasales_round_trip_multi_passenger():
    """Round-trip + 2 adults."""
    url = aviasales_url("LHR", "JFK", date(2024, 12, 1), return_date=date(2024, 12, 15), adults=2)
    assert url == f"https://www.aviasales.com/search/LHR0112JFK15122?{MARKER_QS}"


def test_aviasales_ddmm_zero_padding():
    """Day and month are zero-padded to 2 digits: Jan 5 → '0501'."""
    url = aviasales_url("YYZ", "CDG", date(2024, 1, 5))
    assert url == f"https://www.aviasales.com/search/YYZ0501CDG1?{MARKER_QS}"


def test_aviasales_case_insensitive_input():
    """Lowercase IATA codes are uppercased."""
    url = aviasales_url("yvr", "mow", date(2024, 8, 18))
    assert url == f"https://www.aviasales.com/search/YVR1808MOW1?{MARKER_QS}"


def test_aviasales_marker_in_url():
    """Every URL contains the exact marker string."""
    url = aviasales_url("YVR", "MOW", date(2024, 8, 18))
    assert AVIASALES_MARKER in url
    assert f"sub_id={AVIASALES_SUB_ID}" in url


# ── aviasales_fallback_url ───────────────────────────────────────────────────

def test_aviasales_fallback_with_origin():
    """Spec fallback: origin only → /?marker=...&params=YVR1."""
    url = aviasales_fallback_url("YVR")
    assert url == f"https://www.aviasales.com/?{MARKER_QS}&params=YVR1"


def test_aviasales_fallback_with_origin_lowercase():
    """Origin is uppercased in fallback URL."""
    url = aviasales_fallback_url("yvr")
    assert url == f"https://www.aviasales.com/?{MARKER_QS}&params=YVR1"


def test_aviasales_fallback_no_origin():
    """No origin → plain homepage with marker."""
    url = aviasales_fallback_url()
    assert url == f"https://www.aviasales.com/?{MARKER_QS}"


def test_aviasales_fallback_none_origin():
    """Explicit None origin → plain homepage with marker."""
    url = aviasales_fallback_url(None)
    assert url == f"https://www.aviasales.com/?{MARKER_QS}"
