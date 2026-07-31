"""Tests for activity catalog health-check loader robustness."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yonder.activity_health import (
    CatalogLoadError,
    HealthReport,
    UrlResult,
    _load_all_rows,
    check_catalog,
)


# ---------------------------------------------------------------------------
# _load_all_rows — failure modes
# ---------------------------------------------------------------------------

def test_load_raises_when_csv_missing(tmp_path):
    """Missing catalog raises CatalogLoadError, not a silent empty list."""
    missing = tmp_path / "nonexistent.csv"
    with patch("yonder.activity_health.CSV_PATH", missing):
        with pytest.raises(CatalogLoadError, match="not found"):
            _load_all_rows()


def test_load_raises_when_csv_is_empty(tmp_path):
    """An empty (or header-only) CSV raises CatalogLoadError rather than
    returning [] and letting the health check report a false-clean result."""
    empty = tmp_path / "activities.csv"
    empty.write_text("CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n", encoding="utf-8")
    with patch("yonder.activity_health.CSV_PATH", empty):
        with pytest.raises(CatalogLoadError, match="zero valid rows"):
            _load_all_rows()


def test_load_raises_when_csv_is_unreadable(tmp_path):
    """An unreadable file (permission error) raises CatalogLoadError."""
    locked = tmp_path / "activities.csv"
    locked.write_text(
        "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"
        "Amsterdam,AMS,https://www.getyourguide.com/test-t1/,explorer,🗺,Test Title\n",
        encoding="utf-8",
    )
    locked.chmod(0o000)
    try:
        with patch("yonder.activity_health.CSV_PATH", locked):
            with pytest.raises(CatalogLoadError):
                _load_all_rows()
    finally:
        locked.chmod(0o644)  # restore so tmp_path cleanup works


def test_load_skips_rows_without_https(tmp_path):
    """Rows whose URL doesn't start with https:// are silently skipped;
    only https rows count toward the loaded total."""
    csv_file = tmp_path / "activities.csv"
    csv_file.write_text(
        "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"
        "Amsterdam,AMS,http://insecure.example.com/,explorer,🗺,Insecure Link\n"
        "Berlin,BER,https://www.getyourguide.com/berlin-l17/test-t1/,history,🏛,Valid Link\n",
        encoding="utf-8",
    )
    with patch("yonder.activity_health.CSV_PATH", csv_file):
        rows = _load_all_rows()
    assert len(rows) == 1
    assert rows[0]["city"] == "Berlin"


def test_load_happy_path(tmp_path):
    """Valid CSV returns correct row dicts with provider inferred from URL."""
    csv_file = tmp_path / "activities.csv"
    csv_file.write_text(
        "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"
        "Paris,CDG,https://www.getyourguide.com/paris-l16/tour-t123/,history,🏛,Paris Night Louvre\n"
        "Paris,CDG,https://www.viator.com/tours/Paris/big-bus/d479-123,explorer,🗺,Big Bus Paris Hop\n",
        encoding="utf-8",
    )
    with patch("yonder.activity_health.CSV_PATH", csv_file):
        rows = _load_all_rows()
    assert len(rows) == 2
    providers = {r["provider"] for r in rows}
    assert providers == {"getyourguide", "viator"}


# ---------------------------------------------------------------------------
# UrlResult — dead-link classification
# ---------------------------------------------------------------------------

def _make_result(**kwargs) -> UrlResult:
    defaults = dict(
        url="https://example.com/tour",
        city="Paris",
        title="Test Tour",
        provider="getyourguide",
        status=200,
        final_url="https://example.com/tour",
        elapsed_ms=100.0,
        error=None,
    )
    defaults.update(kwargs)
    return UrlResult(**defaults)


def test_404_is_dead():
    r = _make_result(status=404)
    assert r.is_dead
    assert r.label == "DEAD"


def test_410_is_dead():
    r = _make_result(status=410)
    assert r.is_dead


def test_200_is_ok():
    r = _make_result(status=200)
    assert not r.is_dead
    assert r.label == "ok"


def test_gyg_homepage_redirect_is_dead():
    """A redirect that lands on GYG city landing page = listing removed."""
    r = _make_result(
        status=200,
        final_url="https://www.getyourguide.com/amsterdam-l36/",
    )
    assert r.is_dead


def test_viator_homepage_redirect_is_dead():
    r = _make_result(status=200, final_url="https://www.viator.com/")
    assert r.is_dead


def test_viator_search_redirect_is_dead():
    r = _make_result(status=200, final_url="https://www.viator.com/search?q=tour")
    assert r.is_dead


def test_tour_redirect_is_not_dead():
    """A small URL slug change (e.g. slug normalisation) is still alive."""
    r = _make_result(
        status=200,
        final_url="https://www.getyourguide.com/paris-l16/louvre-night-tour-t123/",
    )
    assert not r.is_dead


def test_network_error_is_not_dead():
    """Timeouts are inconclusive — they must not be counted as confirmed dead."""
    r = _make_result(status=None, final_url=None, error="timeout")
    assert r.is_error
    assert not r.is_dead
    assert r.label == "ERR"


# ---------------------------------------------------------------------------
# HealthReport — aggregate stats
# ---------------------------------------------------------------------------

def test_health_report_dead_pct():
    results = [
        _make_result(status=200),
        _make_result(status=200),
        _make_result(status=404),
        _make_result(status=200),
    ]
    report = HealthReport(total=4, results=results)
    assert len(report.dead) == 1
    assert abs(report.dead_pct - 25.0) < 0.01


def test_health_report_empty_results_dead_pct_is_zero():
    report = HealthReport(total=0, results=[])
    assert report.dead_pct == 0.0


# ---------------------------------------------------------------------------
# check_catalog — integration (uses real CSV, no network)
# ---------------------------------------------------------------------------

def test_check_catalog_loads_real_csv_without_error():
    """check_catalog with sample=3 should load the real CSV without raising."""
    report = check_catalog(sample=3, seed=0)
    assert report.total > 0
    assert len(report.results) == 3
