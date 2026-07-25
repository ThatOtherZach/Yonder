"""Country flag-inspired color themes for adventure cards & results."""

from __future__ import annotations

from yonder.countries import IATA_COUNTRY, country_for_iata

# ISO2 → (primary, secondary, accent, emoji, vibe label)
# Colors tuned for dark UI (rich, not neon-garish)
FLAG_THEMES: dict[str, dict[str, str]] = {
    "CH": {
        "primary": "#c41e3a",
        "secondary": "#1a0a0c",
        "accent": "#ff4d6d",
        "flag": "🇨🇭",
        "label": "Swiss",
        "gradient": "linear-gradient(135deg, #2a1014 0%, #1a2332 55%, #3d1219 100%)",
    },
    "TR": {
        "primary": "#e30a17",
        "secondary": "#0d1b2a",
        "accent": "#ffd60a",
        "flag": "🇹🇷",
        "label": "Turkish",
        "gradient": "linear-gradient(135deg, #2b0a0e 0%, #1a2332 50%, #1a2744 100%)",
    },
    "PT": {
        "primary": "#046a38",
        "secondary": "#da291c",
        "accent": "#ffc72c",
        "flag": "🇵🇹",
        "label": "Portuguese",
        "gradient": "linear-gradient(135deg, #0a2e1c 0%, #1a2332 45%, #3a1210 100%)",
    },
    "IS": {
        "primary": "#02529c",
        "secondary": "#dc1e35",
        "accent": "#7eb8f0",
        "flag": "🇮🇸",
        "label": "Icelandic",
        "gradient": "linear-gradient(145deg, #0a1e38 0%, #1a2332 50%, #3a1018 100%)",
    },
    "QA": {
        "primary": "#8a1538",
        "secondary": "#1a0f14",
        "accent": "#e8b4c4",
        "flag": "🇶🇦",
        "label": "Qatari",
        "gradient": "linear-gradient(135deg, #2a0f1c 0%, #1a2332 100%)",
    },
    "NL": {
        "primary": "#ae1c28",
        "secondary": "#21468b",
        "accent": "#f5a9b0",
        "flag": "🇳🇱",
        "label": "Dutch",
        "gradient": "linear-gradient(160deg, #2a0e12 0%, #1a2332 50%, #0e1a35 100%)",
    },
    "FR": {
        "primary": "#002395",
        "secondary": "#ed2939",
        "accent": "#6b9fff",
        "flag": "🇫🇷",
        "label": "French",
        "gradient": "linear-gradient(125deg, #0a1540 0%, #1a2332 50%, #3a1015 100%)",
    },
    "MX": {
        "primary": "#006847",
        "secondary": "#ce1126",
        "accent": "#5ddea0",
        "flag": "🇲🇽",
        "label": "Mexican",
        "gradient": "linear-gradient(140deg, #0a2a1c 0%, #1a2332 50%, #3a0e14 100%)",
    },
    "CA": {
        "primary": "#ff0000",
        "secondary": "#1a0a0a",
        "accent": "#ff6b6b",
        "flag": "🇨🇦",
        "label": "Canadian",
        "gradient": "linear-gradient(135deg, #2a0808 0%, #1a2332 60%, #2a1010 100%)",
    },
    "US": {
        "primary": "#3c3b6e",
        "secondary": "#b22234",
        "accent": "#7b9cff",
        "flag": "🇺🇸",
        "label": "American",
        "gradient": "linear-gradient(135deg, #12142e 0%, #1a2332 50%, #2a1014 100%)",
    },
    "JP": {
        "primary": "#bc002d",
        "secondary": "#1a1a1a",
        "accent": "#ff6b8a",
        "flag": "🇯🇵",
        "label": "Japanese",
        "gradient": "linear-gradient(160deg, #1a080e 0%, #1a2332 55%, #121212 100%)",
    },
    "KR": {
        "primary": "#cd2e3a",
        "secondary": "#0047a0",
        "accent": "#ff8a94",
        "flag": "🇰🇷",
        "label": "Korean",
        "gradient": "linear-gradient(135deg, #2a0e12 0%, #1a2332 50%, #0a1a38 100%)",
    },
    "AE": {
        "primary": "#00732f",
        "secondary": "#ff0000",
        "accent": "#5dff9a",
        "flag": "🇦🇪",
        "label": "Emirati",
        "gradient": "linear-gradient(135deg, #0a2414 0%, #1a2332 50%, #2a0808 100%)",
    },
    "ES": {
        "primary": "#aa151b",
        "secondary": "#f1bf00",
        "accent": "#ffd54f",
        "flag": "🇪🇸",
        "label": "Spanish",
        "gradient": "linear-gradient(135deg, #2a0a0c 0%, #1a2332 45%, #2a2408 100%)",
    },
    "GB": {
        "primary": "#012169",
        "secondary": "#c8102e",
        "accent": "#6b9fff",
        "flag": "🇬🇧",
        "label": "British",
        "gradient": "linear-gradient(135deg, #0a1535 0%, #1a2332 50%, #2a0a12 100%)",
    },
    "DE": {
        "primary": "#dd0000",
        "secondary": "#ffce00",
        "accent": "#ff6b6b",
        "flag": "🇩🇪",
        "label": "German",
        "gradient": "linear-gradient(160deg, #1a0808 0%, #1a2332 40%, #2a2400 100%)",
    },
    "IT": {
        "primary": "#009246",
        "secondary": "#ce2b37",
        "accent": "#5dff9a",
        "flag": "🇮🇹",
        "label": "Italian",
        "gradient": "linear-gradient(135deg, #0a2a18 0%, #1a2332 50%, #2a0e12 100%)",
    },
    "AT": {
        "primary": "#ed2939",
        "secondary": "#1a0a0c",
        "accent": "#ff8a94",
        "flag": "🇦🇹",
        "label": "Austrian",
        "gradient": "linear-gradient(135deg, #2a0c10 0%, #1a2332 100%)",
    },
    "DK": {
        "primary": "#c8102e",
        "secondary": "#1a0a0c",
        "accent": "#ff6b8a",
        "flag": "🇩🇰",
        "label": "Danish",
        "gradient": "linear-gradient(135deg, #2a0a10 0%, #1a2332 100%)",
    },
    "SE": {
        "primary": "#006aa7",
        "secondary": "#fecc00",
        "accent": "#5db8ff",
        "flag": "🇸🇪",
        "label": "Swedish",
        "gradient": "linear-gradient(135deg, #0a2038 0%, #1a2332 50%, #2a2400 100%)",
    },
    "NO": {
        "primary": "#ba0c2f",
        "secondary": "#00205b",
        "accent": "#ff6b8a",
        "flag": "🇳🇴",
        "label": "Norwegian",
        "gradient": "linear-gradient(135deg, #2a0a12 0%, #1a2332 50%, #0a1530 100%)",
    },
    "FI": {
        "primary": "#003580",
        "secondary": "#1a2332",
        "accent": "#5b9fff",
        "flag": "🇫🇮",
        "label": "Finnish",
        "gradient": "linear-gradient(135deg, #0a1838 0%, #1a2332 100%)",
    },
    "IE": {
        "primary": "#169b62",
        "secondary": "#ff883e",
        "accent": "#5dff9a",
        "flag": "🇮🇪",
        "label": "Irish",
        "gradient": "linear-gradient(135deg, #0a2a1c 0%, #1a2332 50%, #2a1a0a 100%)",
    },
    "SG": {
        "primary": "#ef3340",
        "secondary": "#1a0a0c",
        "accent": "#ff8a94",
        "flag": "🇸🇬",
        "label": "Singaporean",
        "gradient": "linear-gradient(135deg, #2a0a0e 0%, #1a2332 100%)",
    },
    "TH": {
        "primary": "#a51931",
        "secondary": "#2d2a4a",
        "accent": "#ff8a94",
        "flag": "🇹🇭",
        "label": "Thai",
        "gradient": "linear-gradient(135deg, #2a0a12 0%, #1a2332 50%, #1a1830 100%)",
    },
    "HK": {
        "primary": "#de2910",
        "secondary": "#1a0a0a",
        "accent": "#ff6b4a",
        "flag": "🇭🇰",
        "label": "Hong Kong",
        "gradient": "linear-gradient(135deg, #2a0a08 0%, #1a2332 100%)",
    },
    "CN": {
        "primary": "#de2910",
        "secondary": "#ffde00",
        "accent": "#ff6b4a",
        "flag": "🇨🇳",
        "label": "Chinese",
        "gradient": "linear-gradient(135deg, #2a0a08 0%, #1a2332 50%, #2a2400 100%)",
    },
    "IN": {
        "primary": "#ff9933",
        "secondary": "#138808",
        "accent": "#ffc14d",
        "flag": "🇮🇳",
        "label": "Indian",
        "gradient": "linear-gradient(135deg, #2a1a08 0%, #1a2332 50%, #0a2a14 100%)",
    },
    "AU": {
        "primary": "#00008b",
        "secondary": "#ff0000",
        "accent": "#6b9fff",
        "flag": "🇦🇺",
        "label": "Australian",
        "gradient": "linear-gradient(135deg, #0a0a2e 0%, #1a2332 50%, #2a0808 100%)",
    },
    "BR": {
        "primary": "#009c3b",
        "secondary": "#ffdf00",
        "accent": "#5dff9a",
        "flag": "🇧🇷",
        "label": "Brazilian",
        "gradient": "linear-gradient(135deg, #0a2a16 0%, #1a2332 50%, #2a2400 100%)",
    },
    "AR": {
        "primary": "#74acdf",
        "secondary": "#f6b40e",
        "accent": "#a8d4f5",
        "flag": "🇦🇷",
        "label": "Argentine",
        "gradient": "linear-gradient(135deg, #0e2438 0%, #1a2332 50%, #2a2008 100%)",
    },
    "GR": {
        "primary": "#0d5eaf",
        "secondary": "#1a2332",
        "accent": "#6bb3ff",
        "flag": "🇬🇷",
        "label": "Greek",
        "gradient": "linear-gradient(135deg, #0a2040 0%, #1a2332 100%)",
    },
    "PL": {
        "primary": "#dc143c",
        "secondary": "#1a0a0c",
        "accent": "#ff6b8a",
        "flag": "🇵🇱",
        "label": "Polish",
        "gradient": "linear-gradient(135deg, #2a0a10 0%, #1a2332 100%)",
    },
    "CZ": {
        "primary": "#d7141a",
        "secondary": "#11457e",
        "accent": "#ff6b8a",
        "flag": "🇨🇿",
        "label": "Czech",
        "gradient": "linear-gradient(135deg, #2a0a0c 0%, #1a2332 50%, #0a1a30 100%)",
    },
    "HU": {
        "primary": "#436f4d",
        "secondary": "#c8102e",
        "accent": "#7dce8e",
        "flag": "🇭🇺",
        "label": "Hungarian",
        "gradient": "linear-gradient(135deg, #0e2414 0%, #1a2332 50%, #2a0a10 100%)",
    },
    "EG": {
        "primary": "#ce1126",
        "secondary": "#c09300",
        "accent": "#ff6b8a",
        "flag": "🇪🇬",
        "label": "Egyptian",
        "gradient": "linear-gradient(135deg, #2a0a0e 0%, #1a2332 50%, #2a1e08 100%)",
    },
    "ZA": {
        "primary": "#007749",
        "secondary": "#de3831",
        "accent": "#5dff9a",
        "flag": "🇿🇦",
        "label": "South African",
        "gradient": "linear-gradient(135deg, #0a2a1a 0%, #1a2332 50%, #2a0e10 100%)",
    },
    "IL": {
        "primary": "#0038b8",
        "secondary": "#1a2332",
        "accent": "#6b9fff",
        "flag": "🇮🇱",
        "label": "Israeli",
        "gradient": "linear-gradient(135deg, #0a1840 0%, #1a2332 100%)",
    },
    "SA": {
        "primary": "#006c35",
        "secondary": "#1a0a0a",
        "accent": "#5dff9a",
        "flag": "🇸🇦",
        "label": "Saudi",
        "gradient": "linear-gradient(135deg, #0a2a16 0%, #1a2332 100%)",
    },
    "BE": {
        "primary": "#fdda24",
        "secondary": "#ef3340",
        "accent": "#ffe566",
        "flag": "🇧🇪",
        "label": "Belgian",
        "gradient": "linear-gradient(135deg, #2a2408 0%, #1a2332 50%, #2a0a0e 100%)",
    },
    "DIRECT": {
        "primary": "#3d9cf0",
        "secondary": "#1a2332",
        "accent": "#7ec8ff",
        "flag": "✈️",
        "label": "Direct",
        "gradient": "linear-gradient(145deg, #0f1a28 0%, #1a2332 55%, #122030 100%)",
    },
    "DEFAULT": {
        "primary": "#e6b450",
        "secondary": "#1a2332",
        "accent": "#f0c96a",
        "flag": "🌍",
        "label": "Adventure",
        "gradient": "linear-gradient(165deg, #1a2332 0%, #2a2218 100%)",
    },
}


def flag_img_url(code: str | None, *, width: int = 40) -> str | None:
    """PNG flag from flagcdn — Windows often can't render flag *emoji* (shows CH/TR)."""
    if not code:
        return None
    c = code.strip().upper()
    if len(c) != 2 or not c.isalpha() or c in {"XX"}:
        return None
    # w40 / w80 / 24x18 etc. supported by flagcdn.com
    return f"https://flagcdn.com/w{width}/{c.lower()}.png"


def theme_for_country(code: str | None) -> dict[str, str]:
    if not code:
        t = dict(FLAG_THEMES["DEFAULT"])
        t["country"] = ""
        t["flag_img"] = ""
        return t
    c = code.upper()
    if c in FLAG_THEMES:
        t = dict(FLAG_THEMES[c])
    else:
        t = dict(FLAG_THEMES["DEFAULT"])
        # Still try real flag image for unknown themed countries
        t["flag"] = ""  # avoid broken 2-letter tiles on Windows
    t["country"] = c if len(c) == 2 and c.isalpha() else ""
    img = flag_img_url(t["country"] or None, width=80)
    t["flag_img"] = img or ""
    return t


def theme_for_iata(iata: str | None, *, kind: str = "stopover") -> dict[str, str]:
    if kind == "direct":
        t = dict(FLAG_THEMES["DIRECT"])
        t["country"] = ""
        t["flag_img"] = ""
        return t
    if not iata:
        return theme_for_country(None)
    cc = country_for_iata(iata) or IATA_COUNTRY.get(iata.upper())
    t = theme_for_country(cc)
    t["iata"] = iata.upper()
    return t


def theme_css_vars(theme: dict[str, str]) -> str:
    """Inline style string for a themed card."""
    return (
        f"--theme-primary:{theme['primary']};"
        f"--theme-secondary:{theme['secondary']};"
        f"--theme-accent:{theme['accent']};"
        f"background:{theme['gradient']};"
        f"border-color:{theme['primary']}66;"
        f"box-shadow:0 0 0 1px {theme['primary']}33, 0 12px 40px {theme['primary']}14;"
    )
