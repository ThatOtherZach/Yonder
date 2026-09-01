"""Regression coverage for the vibe slider's selected-label contrast."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


SLIDER_JS = Path(__file__).parents[1] / "yonder" / "static" / "vibe_slider.js"
INDEX_TEMPLATE = Path(__file__).parents[1] / "yonder" / "templates" / "index.html"
VIBES_JSON = Path(__file__).parents[1] / "yonder" / "vibes.json"


def _relative_luminance(hex_color: str) -> float:
    channels = [
        int(hex_color[index : index + 2], 16) / 255
        for index in (1, 3, 5)
    ]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


def test_selected_vibe_label_uses_contrast_ink_across_palette() -> None:
    """Exercise the browser helper for every configured vibe color."""
    node_script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(SLIDER_JS))}, "utf8");
const vibes = JSON.parse(fs.readFileSync({json.dumps(str(VIBES_JSON))}, "utf8"));
const document = {{
  readyState: "loading",
  addEventListener: function () {{}}
}};
global.document = document;
global.window = {{ __VIBES__: [], document: document }};
eval(source);
console.log(JSON.stringify(vibes.map((vibe) => ({{
  id: vibe.id,
  color: vibe.color,
  ink: window.YonderVibe.contrastInk(vibe.color)
}}))));
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    palette = json.loads(result.stdout)
    assert len(palette) == len(json.loads(VIBES_JSON.read_text(encoding="utf-8")))

    for vibe in palette:
        ratio = _contrast_ratio(vibe["color"], vibe["ink"])
        assert ratio >= 4.5, (
            f'{vibe["id"]} {vibe["color"]} uses {vibe["ink"]} '
            f"at only {ratio:.2f}:1"
        )

    inks_by_color = {vibe["color"]: vibe["ink"] for vibe in palette}
    assert inks_by_color["#fef9c3"] == "#000000"  # luminous: light
    assert inks_by_color["#f43f5e"] == "#000000"  # wild: saturated mid-light
    assert inks_by_color["#1e1b4b"] == "#ffffff"  # vanish: dark

    source = SLIDER_JS.read_text(encoding="utf-8")
    assert "nameOut.style.color = contrastInk(show);" in source
    assert source.index("nameOut.style.color = contrastInk(show);") < source.index(
        "if (!nameRow.classList.contains(\"is-busy\"))"
    )

    template = INDEX_TEMPLATE.read_text(encoding="utf-8")
    name_css = template[template.index(".vibe-name {") : template.index(".vibe-hue:focus-visible")]
    assert "color: #fff" not in name_css