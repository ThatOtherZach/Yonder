"""Vibe id → color theme tokens. Definitions live in yonder/vibes.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_VIBES_PATH = Path(__file__).parent / "vibes.json"

# Load canonical vibe list once at import time.
# (id, label, hex) — order matches the hue slider palette
def _load_palette() -> list[tuple[str, str, str]]:
    data = json.loads(_VIBES_PATH.read_text(encoding="utf-8"))
    return [(v["id"], v["label"], v["color"]) for v in data]


VIBE_PALETTE: list[tuple[str, str, str]] = _load_palette()

_BY_ID: dict[str, tuple[str, str, str]] = {v[0]: v for v in VIBE_PALETTE}

VIBE_EMOJI: dict[str, str] = {
    "chaos": "💥", "wild": "🦁", "party": "🎉", "romance": "💕", "neon": "⚡",
    "night": "🌙", "soul": "🎵", "art": "🎨", "culture": "🏛️", "city": "🏙️",
    "future": "🚀", "ocean": "🌊", "islands": "🏝️", "beach": "🏖️", "jungle": "🌿",
    "nature": "🌲", "mountains": "⛰️", "adventure": "🧗", "trains": "🚂", "food": "🍜",
    "street": "🛵", "desert": "🏜️", "sun": "☀️", "luxury": "💎", "spa": "🧖",
    "cozy": "🧣", "history": "🏺", "snow": "❄️", "quiet": "🌾", "cheap": "💸",
    "fire": "🔥", "ember": "🌋", "rose": "🌹", "blush": "🌸", "petal": "🌷",
    "carnival": "🎡", "festival": "🎪", "dream": "💭", "magic": "✨", "gothic": "🦇",
    "indie": "🎸", "lavender": "💜", "dusk": "🌆", "twilight": "🌃", "cosmic": "🌌",
    "retro": "📼", "navy": "🧭", "lakeside": "🚣", "fog": "🌫️", "flow": "🌬️",
    "sail": "⛵", "reef": "🐡", "dive": "🤿", "tropical": "🦜", "botanic": "🌱",
    "forest": "🌳", "valley": "🏞️", "meadow": "🌾", "canopy": "🎋", "savanna": "🦒",
    "wellbeing": "🧘", "golf": "⛳", "golden": "🏅", "dunes": "🐪", "glow": "🌅",
    "spice": "🌶️", "canyon": "🏜️", "road": "🛣️", "folklore": "🧙",
    # adjective & hybrid vibes
    "whimsical": "🎠", "velvet": "🎭", "warmnights": "🌴", "tender": "🪷",
    "moody": "🌧️", "vivid": "🦚", "sleepy": "🌛", "stormy": "⛈️",
    "electric": "💡", "seasalt": "🐚", "crisp": "🫧", "serene": "🕊️",
    "lush": "🌺", "feral": "🐾", "untamed": "🦅", "luminous": "💫",
    "opulent": "👑", "goldenhour": "🌇", "hazy": "🌤️", "nostalgic": "🕯️",
    "ancient": "🗿", "sacred": "⛩️", "raw": "🪵", "rugged": "🪨", "gritty": "🏗️",
}


def _clamp(n: float, a: float = 0.0, b: float = 255.0) -> int:
    return int(max(a, min(b, round(n))))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (42, 111, 173)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (42, 111, 173)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def darken(hex_color: str, amount: float = 0.28) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    f = 1.0 - amount
    return _rgb_to_hex(r * f, g * f, b * f)


def lighten(hex_color: str, amount: float = 0.82) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(
        r + (255 - r) * amount,
        g + (255 - g) * amount,
        b + (255 - b) * amount,
    )


def resolve_vibe(vibe_id: str | None) -> dict[str, str]:
    """Return id, label, color, emoji for a vibe slug (fallback adventure)."""
    key = (vibe_id or "").strip().lower()
    if key not in _BY_ID:
        # try label match
        for vid, label, color in VIBE_PALETTE:
            if label.lower() == key:
                key = vid
                break
        else:
            key = "adventure"
    vid, label, color = _BY_ID[key]
    return {"id": vid, "label": label, "color": color, "emoji": VIBE_EMOJI.get(vid, "")}


def vibe_theme(vibe_id: str | None) -> dict[str, Any]:
    """CSS tokens for page chrome + boarding-pass accent."""
    v = resolve_vibe(vibe_id)
    color = v["color"]
    deep = darken(color, 0.32)
    mist = lighten(color, 0.88)
    style = (
        f"--vibe-now:{color};"
        f"--sky-bright:{color};"
        f"--sky:{deep};"
        f"--sky-mist:{mist};"
        f"--brass:{color};"
        f"--brass-deep:{deep};"
        f"--brass-mist:{mist};"
        f"--accent:{color};"
        f"--accent-soft:{mist};"
        f"--gold:{color};"
        f"--gold-soft:{mist};"
    )
    return {
        **v,
        "deep": deep,
        "mist": mist,
        "style": style,
    }
