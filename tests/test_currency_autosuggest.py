"""Task 278 — currency auto-suggest regression test.

The Home Base card in settings.html has a JS blur handler that maps IATA
codes to currencies and auto-fills the DEFAULT_CURRENCY select — but only
when it is still at the USD default. These tests execute the *actual*
script block from the template under Node with a tiny DOM stub, so both
the IATA_TO_CCY table and the USD-only guard are exercised as shipped.

Covers:
- representative codes for every currency region (CAD, GBP, EUR, AUD,
  JPY, CHF, MXN)
- the guard: no override when the select is already non-USD
- unknown codes leave the select unchanged
- the Y-prefix → CAD fallback for unlisted Canadian airports
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "yonder" / "templates" / "settings.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _extract_home_base_iife() -> str:
    """Pull the self-invoking script block that contains IATA_TO_CCY."""
    src = TEMPLATE.read_text(encoding="utf-8")
    anchor = src.index("IATA_TO_CCY")
    start = src.rindex("(function () {", 0, anchor)
    end = src.index("})();", anchor) + len("})();")
    block = src[start:end]
    # Neutralise the one Jinja expression inside the block.
    block = re.sub(r"\{\{[^}]*home_resolved[^}]*\}\}", '"JFK"', block)
    assert "{{" not in block and "{%" not in block, "unexpected Jinja left in JS block"
    return block


DOM_STUB = """
function makeEl(value) {
  return {
    value: value,
    textContent: "",
    handlers: {},
    addEventListener: function (ev, fn) { this.handlers[ev] = fn; },
    fire: function (ev) { if (this.handlers[ev]) this.handlers[ev](); },
  };
}
var els = {
  HOME_IATA: makeEl(""),
  DEFAULT_CURRENCY: makeEl("USD"),
  "hb-iata-display": makeEl(""),
  "hb-ccy-display": makeEl(""),
};
var document = { getElementById: function (id) { return els[id] || null; } };
"""


def run_blur(iata: str, initial_ccy: str = "USD") -> str:
    """Set the select to initial_ccy, blur the IATA field, return final ccy."""
    script = (
        DOM_STUB
        + _extract_home_base_iife()
        + f"""
els.DEFAULT_CURRENCY.value = {json.dumps(initial_ccy)};
els.HOME_IATA.value = {json.dumps(iata)};
els.HOME_IATA.fire("blur");
console.log(JSON.stringify({{ ccy: els.DEFAULT_CURRENCY.value, iata: els.HOME_IATA.value }}));
"""
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["ccy"]


# ── mapping table: representative codes per currency region ─────────
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("YVR", "CAD"),
        ("YYZ", "CAD"),
        ("LHR", "GBP"),
        ("MAN", "GBP"),
        ("CDG", "EUR"),
        ("FRA", "EUR"),
        ("SYD", "AUD"),
        ("MEL", "AUD"),
        ("NRT", "JPY"),
        ("HND", "JPY"),
        ("ZRH", "CHF"),
        ("GVA", "CHF"),
        ("MEX", "MXN"),
        ("CUN", "MXN"),
    ],
)
def test_autosuggest_fires_from_usd_default(code, expected):
    assert run_blur(code) == expected


def test_lowercase_input_is_normalised():
    assert run_blur("yvr") == "CAD"


def test_y_prefix_fallback_maps_to_cad():
    # YXX is not in the table but Canadian Y-prefix codes fall back to CAD.
    assert run_blur("YXX") == "CAD"


# ── guard: never override a non-USD choice ──────────────────────────
@pytest.mark.parametrize("initial", ["EUR", "GBP", "JPY", "CAD"])
def test_no_override_when_currency_already_changed(initial):
    assert run_blur("LHR", initial_ccy=initial) == initial


def test_no_override_even_for_mapped_code_matching_region():
    # User picked CHF manually; blurring NRT must not flip it to JPY.
    assert run_blur("NRT", initial_ccy="CHF") == "CHF"


# ── unknown codes leave the select unchanged ─────────────────────────
@pytest.mark.parametrize("code", ["JFK", "XXX", "ABC", "", "LH"])
def test_unknown_codes_leave_usd_untouched(code):
    assert run_blur(code) == "USD"
