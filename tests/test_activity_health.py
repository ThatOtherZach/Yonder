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
    RetireResult,
    UrlResult,
    _load_all_rows,
    check_catalog,
    retire_dead_rows,
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


# ---------------------------------------------------------------------------
# retire_dead_rows — dry-run and write behaviour
# ---------------------------------------------------------------------------

_CSV_HEADER = "CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE\n"


def _csv_with_rows(tmp_path, rows: list[str]) -> "Path":
    """Write a minimal CSV and return its path."""
    csv_file = tmp_path / "activities.csv"
    csv_file.write_text(_CSV_HEADER + "".join(rows), encoding="utf-8")
    return csv_file


def _make_dead_report(*urls: str) -> HealthReport:
    """Build a HealthReport whose dead list contains the given URLs."""
    results = [
        UrlResult(
            url=url,
            city="Test",
            title="Test Tour",
            provider="getyourguide",
            status=404,
            final_url=url,
            elapsed_ms=50.0,
        )
        for url in urls
    ]
    return HealthReport(total=len(results), results=results)


def test_retire_dry_run_does_not_modify_file(tmp_path):
    """Dry run (write=False) must not touch the CSV."""
    url = "https://www.getyourguide.com/paris-l16/dead-t1/"
    csv_file = _csv_with_rows(
        tmp_path,
        [f"Paris,CDG,{url},history,🏛,Dead Tour\n"],
    )
    original_text = csv_file.read_text(encoding="utf-8")
    report = _make_dead_report(url)

    result = retire_dead_rows(report, write=False, csv_path=csv_file)

    assert csv_file.read_text(encoding="utf-8") == original_text, "file was modified in dry run"
    assert len(result.removed) == 1
    assert result.removed[0]["URL"] == url
    assert result.written is False


def test_retire_write_removes_dead_rows(tmp_path):
    """write=True should strip dead URLs and rewrite the CSV."""
    dead_url = "https://www.getyourguide.com/paris-l16/dead-t1/"
    live_url = "https://www.viator.com/tours/Paris/live-tour/d479-999"
    csv_file = _csv_with_rows(
        tmp_path,
        [
            f"Paris,CDG,{dead_url},history,🏛,Dead Tour\n",
            f"Paris,CDG,{live_url},explorer,🗺,Live Tour\n",
        ],
    )
    report = _make_dead_report(dead_url)

    result = retire_dead_rows(report, write=True, csv_path=csv_file)

    assert result.written is True
    assert len(result.removed) == 1
    assert result.removed[0]["URL"] == dead_url
    assert result.kept == 1

    # The rewritten file must contain the live row but not the dead one.
    content = csv_file.read_text(encoding="utf-8")
    assert live_url in content
    assert dead_url not in content


def test_retire_preserves_all_columns(tmp_path):
    """Rewritten CSV must keep all original columns and the header row."""
    live_url = "https://www.viator.com/tours/Berlin/live-t99"
    csv_file = _csv_with_rows(
        tmp_path,
        [f"Berlin,BER,{live_url},adventure,🏔,Live Berlin Tour\n"],
    )
    # Report with no dead URLs — file should be unchanged in structure.
    report = HealthReport(total=1, results=[])

    result = retire_dead_rows(report, write=True, csv_path=csv_file)

    assert result.written is False  # nothing to remove, no write needed
    content = csv_file.read_text(encoding="utf-8")
    assert "CITY,IATA,URL" in content


def test_retire_network_errors_are_not_removed(tmp_path):
    """URLs that timed out (inconclusive) must not be removed."""
    url = "https://www.getyourguide.com/amsterdam-l36/timeout-t1/"
    csv_file = _csv_with_rows(
        tmp_path,
        [f"Amsterdam,AMS,{url},explorer,🗺,Timeout Tour\n"],
    )
    # Build a report where the URL has a network error, not a 404.
    result_with_error = UrlResult(
        url=url,
        city="Amsterdam",
        title="Timeout Tour",
        provider="getyourguide",
        status=None,
        final_url=None,
        elapsed_ms=12000.0,
        error="timeout",
    )
    report = HealthReport(total=1, results=[result_with_error])
    assert not report.dead, "timeout should not be classified as dead"

    retire = retire_dead_rows(report, write=True, csv_path=csv_file)

    assert retire.removed == []
    assert retire.written is False
    assert url in csv_file.read_text(encoding="utf-8")


def test_retire_no_dead_urls_returns_empty_removed(tmp_path):
    """When the report has no dead links, removed list is empty."""
    url = "https://www.viator.com/tours/London/alive-t1"
    csv_file = _csv_with_rows(
        tmp_path,
        [f"London,LHR,{url},explorer,🗺,Alive Tour\n"],
    )
    result_ok = UrlResult(
        url=url,
        city="London",
        title="Alive Tour",
        provider="viator",
        status=200,
        final_url=url,
        elapsed_ms=80.0,
    )
    report = HealthReport(total=1, results=[result_ok])

    retire = retire_dead_rows(report, write=False, csv_path=csv_file)

    assert retire.removed == []
    assert retire.written is False


def test_retire_multiple_dead_all_removed(tmp_path):
    """All dead URLs in the report are stripped; live rows survive."""
    dead1 = "https://www.getyourguide.com/berlin-l17/dead-t1/"
    dead2 = "https://www.viator.com/tours/Berlin/dead-t2/d488-999"
    live = "https://www.getyourguide.com/berlin-l17/live-t3/"
    csv_file = _csv_with_rows(
        tmp_path,
        [
            f"Berlin,BER,{dead1},history,🏛,Dead One\n",
            f"Berlin,BER,{dead2},explorer,🗺,Dead Two\n",
            f"Berlin,BER,{live},adventure,🏔,Live Three\n",
        ],
    )
    report = _make_dead_report(dead1, dead2)

    retire = retire_dead_rows(report, write=True, csv_path=csv_file)

    assert retire.written is True
    assert len(retire.removed) == 2
    assert retire.kept == 1
    content = csv_file.read_text(encoding="utf-8")
    assert live in content
    assert dead1 not in content
    assert dead2 not in content
