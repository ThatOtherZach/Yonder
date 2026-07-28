from __future__ import annotations

import re
from pathlib import Path

# Project root = parent of package
ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

# Keys we manage in the settings UI (order = form order)
MANAGED_KEYS: list[tuple[str, str, str, bool]] = [
    # (env_name, label, help, is_secret)
    ("AMADEUS_CLIENT_ID", "Amadeus Client ID (API Key)", "From developers.amadeus.com → your app", True),
    ("AMADEUS_CLIENT_SECRET", "Amadeus Client Secret", "Paired with the client ID", True),
    ("AMADEUS_ENV", "Amadeus environment", "test (sandbox) or production (live)", False),
    ("TRAVELPAYOUTS_TOKEN", "Travelpayouts API token", "travelpayouts.com → Profile → API token", True),
    ("DUFFEL_ACCESS_TOKEN", "Duffel access token", "duffel.com developer dashboard (sandbox ok)", True),
    ("SERPAPI_KEY", "SerpAPI key", "serpapi.com — Google Flights scrape quota", True),
    ("AVIATIONSTACK_KEY", "AviationStack access key", "aviationstack.com — schedules; free ~100/mo", True),
    ("XAI_API_KEY", "xAI / Grok API key", "console.x.ai — powers natural-language search + analysis", True),
    ("XAI_MODEL", "Grok model", "Default grok-4.5", False),
    ("DEFAULT_CURRENCY", "Default currency", "e.g. USD, CAD, EUR", False),
    (
        "AVOID_COUNTRIES",
        "Avoid countries (ISO2, max 10)",
        "Comma-separated e.g. US,RU,CN — never used as adventure stopovers",
        False,
    ),
    (
        "VISITED_COUNTRIES",
        "Visited countries (ISO2)",
        "Personal passport map — edit on Search CRT map",
        False,
    ),
    (
        "COL_HOTEL",
        "COL hotel / night",
        "Decent-enough hotel night (3★/midscale) in default currency",
        False,
    ),
    (
        "COL_FOOD",
        "COL food & drink / day",
        "Normal meals + drinks for the day",
        False,
    ),
    (
        "COL_TRANSIT",
        "COL local transit / day",
        "Basic metro/bus/tram if the place has it (not taxis)",
        False,
    ),
    (
        "COL_CULTURE",
        "COL culture / day",
        "Budget for 1–2 simple cultural things (museum, temple, walking tour)",
        False,
    ),
    (
        "COL_EXPECTED_DAILY",
        "Expected COL per day (total)",
        "Auto-sum of hotel+food+transit+culture when those are set",
        False,
    ),
    (
        "COL_TOLERANCE_PCT",
        "COL over-budget %",
        "How far over the summed daily target still counts as OK (e.g. 25 = +25%)",
        False,
    ),
    (
        "PROVIDER_MODE",
        "Provider routing mode",
        "smart = probe active + budget routing · scan_all = hit every key",
        False,
    ),
    (
        "TESTING",
        "Testing mode",
        "true = show Test Data (mock fares) on Escape/Detour · false = live only",
        False,
    ),
]

PROVIDER_META = [
    {
        "id": "amadeus",
        "name": "Amadeus",
        "keys": ["AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET"],
        "signup": "https://developers.amadeus.com/",
        "blurb": "Best free live flight offers. Monthly free quota.",
    },
    {
        "id": "travelpayouts",
        "name": "Travelpayouts / Aviasales",
        "keys": ["TRAVELPAYOUTS_TOKEN"],
        "signup": "https://www.travelpayouts.com/",
        "blurb": "Free cached market prices — great for deal scanning.",
    },
    {
        "id": "duffel",
        "name": "Duffel",
        "keys": ["DUFFEL_ACCESS_TOKEN"],
        "signup": "https://duffel.com/",
        "blurb": "Modern flights API. Free sandbox token.",
    },
    {
        "id": "serpapi_google_flights",
        "name": "SerpAPI → Google Flights",
        "keys": ["SERPAPI_KEY"],
        "signup": "https://serpapi.com/",
        "blurb": "Google Flights snapshots. Free monthly search quota.",
    },
    {
        "id": "aviationstack",
        "name": "AviationStack",
        "keys": ["AVIATIONSTACK_KEY"],
        "signup": "https://aviationstack.com/",
        "blurb": "Airport enrichment (city/country). Free tier — not used for ticket prices.",
    },
    {
        "id": "grok",
        "name": "Grok (xAI)",
        "keys": ["XAI_API_KEY"],
        "signup": "https://console.x.ai/",
        "blurb": "Natural-language search + picks the best offer after scan.",
    },
]


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[7:].strip()
        if "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        values[key] = val
    return values


def read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    return _parse_env(ENV_PATH.read_text(encoding="utf-8"))


def write_env(updates: dict[str, str], *, clear_keys: set[str] | None = None) -> Path:
    """Merge updates into .env. Empty string in updates = leave existing (unless in clear_keys)."""
    clear_keys = clear_keys or set()
    existing_text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    current = _parse_env(existing_text)

    managed = {k for k, *_ in MANAGED_KEYS}
    for key, value in updates.items():
        if key not in managed:
            continue
        if key in clear_keys:
            current[key] = ""
            continue
        # blank means "keep existing secret/value"
        if value == "" or value is None:
            continue
        # ignore masked placeholders submitted unchanged
        if value.startswith("••••"):
            continue
        current[key] = value.strip()

    # Ensure all managed keys exist as keys (even empty)
    for key, *_ in MANAGED_KEYS:
        current.setdefault(key, "")

    lines: list[str] = [
        "# Yonder settings — local only, do not commit",
        f"# Path: {ENV_PATH}",
        "",
    ]
    sections = [
        ("Amadeus", ["AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET", "AMADEUS_ENV"]),
        ("Travelpayouts", ["TRAVELPAYOUTS_TOKEN"]),
        ("Duffel", ["DUFFEL_ACCESS_TOKEN"]),
        ("SerpAPI", ["SERPAPI_KEY"]),
        ("AviationStack", ["AVIATIONSTACK_KEY"]),
        ("Grok / xAI", ["XAI_API_KEY", "XAI_MODEL"]),
        (
            "Defaults",
            [
                "DEFAULT_CURRENCY",
                "COL_HOTEL",
                "COL_FOOD",
                "COL_TRANSIT",
                "COL_CULTURE",
                "COL_EXPECTED_DAILY",
                "COL_TOLERANCE_PCT",
                "AVOID_COUNTRIES",
                "VISITED_COUNTRIES",
                "PROVIDER_MODE",
                "TESTING",
            ],
        ),
    ]
    for title, keys in sections:
        lines.append(f"# --- {title} ---")
        for key in keys:
            val = current.get(key, "")
            lines.append(f"{key}={_quote_if_needed(val)}")
        lines.append("")

    # Preserve any non-managed keys that were already present
    for key, val in sorted(current.items()):
        if key not in managed:
            lines.append(f"{key}={_quote_if_needed(val)}")

    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ENV_PATH


def _quote_if_needed(val: str) -> str:
    if not val:
        return ""
    if re.search(r'[\s#"\']', val):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return val


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••••••" + value[-4:]


def settings_view() -> dict:
    """UI-safe snapshot of current settings."""
    env = read_env()
    fields = []
    for key, label, help_text, is_secret in MANAGED_KEYS:
        raw = env.get(key, "")
        fields.append(
            {
                "key": key,
                "label": label,
                "help": help_text,
                "is_secret": is_secret,
                "set": bool(raw),
                "display": mask_secret(raw) if is_secret and raw else raw,
                "value": "" if is_secret else raw,  # non-secrets prefilled
            }
        )

    # Provider ready status from raw env (not cached Settings)
    providers = []
    for p in PROVIDER_META:
        ready = all(bool(env.get(k)) for k in p["keys"])
        providers.append({**p, "ready": ready})

    return {
        "env_path": str(ENV_PATH),
        "fields": fields,
        "providers": providers,
        "amadeus_env": env.get("AMADEUS_ENV") or "test",
        "default_currency": env.get("DEFAULT_CURRENCY") or "CAD",
        "xai_model": env.get("XAI_MODEL") or "grok-4.5",
        "grok_ready": bool(env.get("XAI_API_KEY")),
        "avoid_countries": env.get("AVOID_COUNTRIES") or "",
        "avoid_list": [
            c.strip().upper()
            for c in (env.get("AVOID_COUNTRIES") or "").replace(";", ",").split(",")
            if c.strip()
        ][:10],
        "visited_countries": env.get("VISITED_COUNTRIES") or "",
        "visited_list": [
            c.strip().upper()
            for c in (env.get("VISITED_COUNTRIES") or "").replace(";", ",").split(",")
            if c.strip() and len(c.strip()) == 2
        ][:250],
        "col_hotel": env.get("COL_HOTEL") or "0",
        "col_food": env.get("COL_FOOD") or "0",
        "col_transit": env.get("COL_TRANSIT") or "0",
        "col_culture": env.get("COL_CULTURE") or "0",
        "col_expected_daily": env.get("COL_EXPECTED_DAILY") or "0",
        "col_tolerance_pct": env.get("COL_TOLERANCE_PCT") or "25",
        "provider_mode": (env.get("PROVIDER_MODE") or "smart").lower(),
        "testing": str(env.get("TESTING") or "false").strip().lower()
        in ("1", "true", "yes", "on"),
    }
