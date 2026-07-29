"""Vibe id → color theme tokens (keep in sync with static/vibe_slider.js palette)."""

from __future__ import annotations

from typing import Any

# (id, label, hex) — order matches the hue slider palette
VIBE_PALETTE: list[tuple[str, str, str]] = [
    ("chaos", "Chaos", "#e11d48"),
    ("wild", "Wild", "#f43f5e"),
    ("party", "Party", "#ec4899"),
    ("romance", "Romance", "#d946ef"),
    ("neon", "Neon", "#c026d3"),
    ("night", "Night", "#9333ea"),
    ("soul", "Soul", "#7c3aed"),
    ("art", "Art", "#6366f1"),
    ("culture", "Culture", "#4f46e5"),
    ("city", "City", "#2563eb"),
    ("future", "Future", "#0284c7"),
    ("ocean", "Ocean", "#0891b2"),
    ("islands", "Islands", "#0d9488"),
    ("beach", "Beach", "#14b8a6"),
    ("jungle", "Jungle", "#16a34a"),
    ("nature", "Nature", "#22c55e"),
    ("mountains", "Mountains", "#65a30d"),
    ("adventure", "Adventure", "#84cc16"),
    ("trains", "Trains", "#ca8a04"),
    ("food", "Food", "#eab308"),
    ("street", "Street", "#f59e0b"),
    ("desert", "Desert", "#f97316"),
    ("sun", "Sun", "#ea580c"),
    ("luxury", "Luxury", "#d97706"),
    ("spa", "Spa", "#b45309"),
    ("cozy", "Cozy", "#a16207"),
    ("history", "History", "#92400e"),
    ("snow", "Snow", "#64748b"),
    ("quiet", "Quiet", "#475569"),
    ("cheap", "Cheap", "#0f766e"),
    # ── new vibes ────────────────────────────────────────────────────────────
    ("fire", "Fire", "#dc2626"),
    ("ember", "Ember", "#f87171"),
    ("rose", "Rose", "#fb7185"),
    ("blush", "Blush", "#fda4af"),
    ("petal", "Petal", "#f9a8d4"),
    ("carnival", "Carnival", "#f472b6"),
    ("festival", "Festival", "#db2777"),
    ("dream", "Dream", "#c084fc"),
    ("magic", "Magic", "#a855f7"),
    ("gothic", "Gothic", "#581c87"),
    ("indie", "Indie", "#7e22ce"),
    ("lavender", "Lavender", "#c4b5fd"),
    ("dusk", "Dusk", "#6d28d9"),
    ("twilight", "Twilight", "#4338ca"),
    ("cosmic", "Cosmic", "#818cf8"),
    ("retro", "Retro", "#60a5fa"),
    ("navy", "Navy", "#1e3a8a"),
    ("lakeside", "Lakeside", "#1d4ed8"),
    ("fog", "Fog", "#94a3b8"),
    ("flow", "Flow", "#0ea5e9"),
    ("sail", "Sail", "#38bdf8"),
    ("reef", "Reef", "#06b6d4"),
    ("dive", "Dive", "#0e7490"),
    ("tropical", "Tropical", "#10b981"),
    ("botanic", "Botanic", "#34d399"),
    ("forest", "Forest", "#15803d"),
    ("valley", "Valley", "#4ade80"),
    ("meadow", "Meadow", "#86efac"),
    ("canopy", "Canopy", "#166534"),
    ("savanna", "Savanna", "#a3e635"),
    ("wellbeing", "Wellbeing", "#bef264"),
    ("golf", "Golf", "#4d7c0f"),
    ("golden", "Golden", "#fcd34d"),
    ("dunes", "Dunes", "#fbbf24"),
    ("glow", "Glow", "#fb923c"),
    ("spice", "Spice", "#c2410c"),
    ("canyon", "Canyon", "#9a3412"),
    ("road", "Road", "#78350f"),
    ("folklore", "Folklore", "#854d0e"),
]

_BY_ID: dict[str, tuple[str, str, str]] = {v[0]: v for v in VIBE_PALETTE}


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
    """Return id, label, color for a vibe slug (fallback adventure)."""
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
    return {"id": vid, "label": label, "color": color}


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
