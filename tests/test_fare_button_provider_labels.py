"""Regression coverage for user-facing Aviasales fare button labels."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK_FARES_JS = ROOT / "yonder" / "static" / "check_fares.js"
BOARDING_PASS_TEMPLATE = ROOT / "yonder" / "templates" / "_boarding_pass.html"


def test_check_fares_fallback_labels_use_full_provider_name():
    """Both promoted Check Fares states must identify Aviasales Flights."""
    source = CHECK_FARES_JS.read_text()

    assert source.count('"Aviasales Flights"') == 2
    assert ': "Aviasales")' not in source


def test_direct_aviasales_result_has_full_accessible_provider_label():
    """Direct fare results must expose the full provider name to assistive tech."""
    source = BOARDING_PASS_TEMPLATE.read_text()

    assert 'aria-label="Aviasales Flights ↗"' in source
    assert 'aria-label="Aviasales ↗"' not in source
