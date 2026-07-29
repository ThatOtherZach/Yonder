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
        "HOME_IATA",
        "Home airport (IATA)",
        "Default origin (e.g. YVR). Blank → first passport stamp (selection order) → currency country → USA",
        False,
    ),
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
        "COL_EXPECTED_DAILY",
        "Expected COL per day",
        "Your personal daily target (default currency). 0 = ranking off",
        False,
    ),
    (
        "COL_TOLERANCE_PCT",
        "COL over-budget %",
        "How far over the daily target still counts as OK (e.g. 25 = +25%)",
        False,
    ),
    # Legacy component keys — kept so write_env can zero them; not shown in UI
    ("COL_HOTEL", "COL hotel / night (legacy)", "Deprecated; use COL_EXPECTED_DAILY", False),
    ("COL_FOOD", "COL food / day (legacy)", "Deprecated; use COL_EXPECTED_DAILY", False),
    ("COL_TRANSIT", "COL transit / day (legacy)", "Deprecated; use COL_EXPECTED_DAILY", False),
    ("COL_CULTURE", "COL culture / day (legacy)", "Deprecated; use COL_EXPECTED_DAILY", False),
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
    (
        "DETOUR_MIN_STOP_DAYS",
        "Detour min stop days",
        "Shortest multi-day stopover stay (default 3)",
        False,
    ),
    (
        "DETOUR_MAX_STOP_DAYS",
        "Detour max stop days",
        "Longest multi-day stopover stay (default 5)",
        False,
    ),
    (
        "DETOUR_MAX_CANDIDATES",
        "Detour options to price",
        "How many stopover/getaway ideas to invent and price (2–5, default 5). Each search returns at most 5 results.",
        False,
    ),
    (
        "SEARCH_BUDGET_SECONDS",
        "Search soft aim (seconds)",
        "Try to finish Escape/Detour by this time (default 30). Not a hard kill.",
        False,
    ),
    (
        "SEARCH_MAX_SECONDS",
        "Skip button after (seconds)",
        "When the progress Skip control appears (default 42). Without Skip, search may run longer.",
        False,
    ),
    (
        "AFFILIATE_TAG",
        "Affiliate / partner tag",
        "Optional partner id stamped on outbound booking links (product attribution)",
        False,
    ),
    (
        "AFFILIATE_TAG_LIVE",
        "Apply affiliate tag in published / production app",
        "false = tag is suppressed when the app is deployed publicly (safe default); true = always stamp",
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
    """Read managed keys from .env file, falling back to os.environ for any missing/blank values.

    This lets the Settings page show correct values when keys were set via Replit
    env-var tooling (os.environ) rather than a .env file, while the .env file
    remains the canonical store after a Settings save.
    """
    import os

    file_values: dict[str, str] = {}
    if ENV_PATH.exists():
        file_values = _parse_env(ENV_PATH.read_text(encoding="utf-8"))

    managed = {k for k, *_ in MANAGED_KEYS}
    result: dict[str, str] = {}

    # For managed keys: prefer .env, fall back to os.environ
    for key in managed:
        file_val = file_values.get(key, "")
        if file_val:
            result[key] = file_val
        elif key in os.environ:
            result[key] = os.environ[key]
        else:
            result[key] = ""

    # Preserve non-managed keys from the file
    for key, val in file_values.items():
        if key not in result:
            result[key] = val

    return result


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

    # Numeric Detour defaults — never leave blank (Settings UI / pydantic ints)
    _detour_defaults = {
        "DETOUR_MIN_STOP_DAYS": "3",
        "DETOUR_MAX_STOP_DAYS": "5",
        "DETOUR_MAX_CANDIDATES": "5",
        "SEARCH_BUDGET_SECONDS": "30",
        "SEARCH_MAX_SECONDS": "42",
    }
    for key, default in _detour_defaults.items():
        if not str(current.get(key) or "").strip():
            current[key] = default
        # Cap options at 5 results per search
        if key == "DETOUR_MAX_CANDIDATES":
            try:
                n = int(str(current.get(key) or "5").strip() or "5")
            except ValueError:
                n = 5
            current[key] = str(max(2, min(5, n)))
        if key == "SEARCH_BUDGET_SECONDS":
            try:
                n = float(str(current.get(key) or "30").strip() or "30")
            except ValueError:
                n = 30.0
            current[key] = str(max(8, min(180, n)))
        if key == "SEARCH_MAX_SECONDS":
            try:
                n = float(str(current.get(key) or "42").strip() or "42")
            except ValueError:
                n = 42.0
            try:
                aim = float(str(current.get("SEARCH_BUDGET_SECONDS") or "30").strip() or "30")
            except ValueError:
                aim = 30.0
            current[key] = str(max(aim, min(600, n)))

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
                "COL_EXPECTED_DAILY",
                "COL_TOLERANCE_PCT",
                "COL_HOTEL",
                "COL_FOOD",
                "COL_TRANSIT",
                "COL_CULTURE",
                "AVOID_COUNTRIES",
                "VISITED_COUNTRIES",
                "PROVIDER_MODE",
                "TESTING",
                "DETOUR_MIN_STOP_DAYS",
                "DETOUR_MAX_STOP_DAYS",
                "DETOUR_MAX_CANDIDATES",
                "SEARCH_BUDGET_SECONDS",
                "SEARCH_MAX_SECONDS",
                "AFFILIATE_TAG",
                "AFFILIATE_TAG_LIVE",
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

    # Mirror written values into os.environ so the running process reflects changes
    # immediately without a restart (pydantic-settings reads os.environ first).
    import os as _os
    for key, val in current.items():
        if key in managed:
            _os.environ[key] = val

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


def _float_env(env: dict, key: str) -> float:
    try:
        return max(0.0, float(str(env.get(key) or "0").strip() or "0"))
    except (TypeError, ValueError):
        return 0.0


def _effective_col_daily(env: dict) -> str:
    """Display value for Settings: total first, else legacy component sum."""
    total = _float_env(env, "COL_EXPECTED_DAILY")
    if total <= 0:
        total = (
            _float_env(env, "COL_HOTEL")
            + _float_env(env, "COL_FOOD")
            + _float_env(env, "COL_TRANSIT")
            + _float_env(env, "COL_CULTURE")
        )
    if total <= 0:
        return "0"
    if abs(total - round(total)) < 1e-9:
        return str(int(round(total)))
    return str(round(total, 2))


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
        "default_currency": env.get("DEFAULT_CURRENCY") or "USD",
        "home_iata": (env.get("HOME_IATA") or "").strip().upper(),
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
        # Prefer total; migrate old component-only .env into the single field for display
        "col_expected_daily": _effective_col_daily(env),
        "col_tolerance_pct": env.get("COL_TOLERANCE_PCT") or "25",
        "provider_mode": (env.get("PROVIDER_MODE") or "smart").lower(),
        "testing": str(env.get("TESTING") or "false").strip().lower()
        in ("1", "true", "yes", "on"),
        "detour_min_stop_days": env.get("DETOUR_MIN_STOP_DAYS") or "3",
        "detour_max_stop_days": env.get("DETOUR_MAX_STOP_DAYS") or "5",
        "detour_max_candidates": env.get("DETOUR_MAX_CANDIDATES") or "5",
        "search_budget_seconds": env.get("SEARCH_BUDGET_SECONDS") or "30",
        "search_max_seconds": env.get("SEARCH_MAX_SECONDS") or "42",
        "affiliate_tag": env.get("AFFILIATE_TAG") or "",
        "affiliate_tag_live": str(env.get("AFFILIATE_TAG_LIVE") or "false").strip().lower()
        in ("1", "true", "yes", "on"),
    }
