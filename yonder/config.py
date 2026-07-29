from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _empty_as(default: Any):
    """Treat blank .env values as missing so field defaults apply."""

    def _parse(v: Any) -> Any:
        if v is None:
            return default
        if isinstance(v, str) and not v.strip():
            return default
        return v

    return _parse


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""
    amadeus_env: str = "test"  # test | production

    travelpayouts_token: str = ""
    duffel_access_token: str = ""
    serpapi_key: str = ""
    aviationstack_key: str = ""

    # xAI / Grok — OpenAI-compatible API
    xai_api_key: str = ""
    xai_model: str = "grok-4.5"

    default_currency: str = "USD"
    # Home / default origin airport (IATA). Blank → first visited map country → YVR
    home_iata: str = ""
    # Comma-separated ISO2 codes, max 10 (e.g. US,RU,CN)
    avoid_countries: str = ""
    # Comma-separated ISO2 codes — personal travel passport map (unlimited-ish)
    visited_countries: str = ""
    # Lean day-bag components (default_currency) — legacy split; kept for migration.
    # Settings UI writes only col_expected_daily and zeros these.
    col_hotel: float = 0.0
    col_food: float = 0.0
    col_transit: float = 0.0
    col_culture: float = 0.0
    # Primary daily target (default_currency). 0 = COL under/over ranking off.
    col_expected_daily: float = 0.0
    # Acceptable % over expected before hard penalty (e.g. 25 → budget + 25%).
    col_tolerance_pct: float = 25.0
    # scan_all | smart (default smart uses quota router)
    provider_mode: str = "smart"
    # When true, Search/Adventure may show "Test Data" (mock fares)
    testing: bool = False
    # Detour defaults (editable in Settings) — blank .env uses these
    detour_min_stop_days: int = 4
    detour_max_stop_days: int = 5
    # How many detour ideas to invent/price (results always capped at 5 cheapest)
    detour_max_candidates: int = 5
    # Soft aim for Escape + Detour pacing (seconds) — try to finish by this
    search_budget_seconds: float = 30.0
    # When the progress Skip button appears (seconds). Not a hard kill:
    # without Skip the search may run as long as needed.
    search_max_seconds: float = 42.0
    # Affiliate / partner tag for outbound booking links (product attribution)
    affiliate_tag: str = ""
    # When false (default), the tag is suppressed in deployed/production contexts
    # (detected via REPLIT_DOMAINS env var). Set true to stamp in production too.
    affiliate_tag_live: bool = False

    @field_validator(
        "detour_min_stop_days",
        "detour_max_stop_days",
        "detour_max_candidates",
        mode="before",
    )
    @classmethod
    def _blank_int_defaults(cls, v: Any, info: Any) -> Any:
        defaults = {
            "detour_min_stop_days": 4,
            "detour_max_stop_days": 5,
            "detour_max_candidates": 5,
        }
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return defaults.get(getattr(info, "field_name", ""), 5)
        return v

    @field_validator(
        "col_hotel",
        "col_food",
        "col_transit",
        "col_culture",
        "col_expected_daily",
        "col_tolerance_pct",
        "search_budget_seconds",
        "search_max_seconds",
        mode="before",
    )
    @classmethod
    def _blank_float_defaults(cls, v: Any, info: Any) -> Any:
        defaults = {
            "col_hotel": 0.0,
            "col_food": 0.0,
            "col_transit": 0.0,
            "col_culture": 0.0,
            "col_expected_daily": 0.0,
            "col_tolerance_pct": 25.0,
            "search_budget_seconds": 30.0,
            "search_max_seconds": 42.0,
        }
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return defaults.get(getattr(info, "field_name", ""), 0.0)
        return v

    def search_timing(self) -> tuple[float, float]:
        """(soft_aim_seconds, skip_after_seconds) clamped to safe ranges."""
        try:
            aim = float(self.search_budget_seconds or 30.0)
        except (TypeError, ValueError):
            aim = 30.0
        try:
            mx = float(self.search_max_seconds or 42.0)
        except (TypeError, ValueError):
            mx = 42.0
        aim = max(8.0, min(180.0, aim))
        mx = max(aim, min(600.0, mx))
        return aim, mx

    def detour_stop_defaults(self) -> tuple[int, int, int]:
        """(min_stop_days, max_stop_days, max_candidates) clamped to safe ranges."""
        try:
            lo = int(self.detour_min_stop_days or 3)
        except (TypeError, ValueError):
            lo = 3
        try:
            hi = int(self.detour_max_stop_days or 5)
        except (TypeError, ValueError):
            hi = 5
        try:
            n = int(self.detour_max_candidates or 5)
        except (TypeError, ValueError):
            n = 5
        lo = max(1, min(21, lo))
        hi = max(lo, min(30, hi))
        n = max(2, min(5, n))  # at most 5 results per search
        return lo, hi, n

    @property
    def amadeus_base(self) -> str:
        if self.amadeus_env.lower() == "production":
            return "https://api.amadeus.com"
        return "https://test.api.amadeus.com"

    def configured_providers(self) -> list[str]:
        names: list[str] = []
        if self.amadeus_client_id and self.amadeus_client_secret:
            names.append("amadeus")
        if self.travelpayouts_token:
            names.append("travelpayouts")
        if self.duffel_access_token:
            names.append("duffel")
        if self.serpapi_key:
            names.append("serpapi_google_flights")
        if self.aviationstack_key:
            names.append("aviationstack")
        return names

    def grok_ready(self) -> bool:
        return bool(self.xai_api_key)

    def avoid_country_list(self) -> list[str]:
        from yonder.countries import normalize_avoid_list

        return normalize_avoid_list(self.avoid_countries)

    def visited_country_list(self) -> list[str]:
        from yonder.countries import normalize_country_list

        return normalize_country_list(self.visited_countries, max_n=250)

    def resolve_home_iata(self) -> str:
        """Home origin for Escape/Detour when the prompt doesn't name one.

        Order:
          1. HOME_IATA setting (explicit override)
          2. primary airport of the **first** passport-map country
             (selection order — first stamp = home)
          3. primary airport for default_currency's country
          4. USA (JFK)
        """
        from yonder.countries import country_for_currency, primary_iata_for_country

        raw = (self.home_iata or "").strip().upper()
        if len(raw) == 3 and raw.isalpha():
            return raw
        # VISITED_COUNTRIES is stored in stamp order (not alphabetized)
        visited = self.visited_country_list()
        if visited:
            for cc in visited:
                iata = primary_iata_for_country(cc)
                if iata:
                    return iata
        cur = (self.default_currency or "").strip().upper()
        if cur:
            cc = country_for_currency(cur)
            if cc:
                iata = primary_iata_for_country(cc)
                if iata:
                    return iata
        # Final fallback: USA
        return primary_iata_for_country("US") or "JFK"

    def col_components(self) -> dict[str, float]:
        """Hotel / food / transit / culture expected amounts (0 if unset)."""

        def _f(v: object) -> float:
            try:
                return max(0.0, float(v or 0))
            except (TypeError, ValueError):
                return 0.0

        return {
            "hotel": _f(self.col_hotel),
            "food": _f(self.col_food),
            "transit": _f(self.col_transit),
            "culture": _f(self.col_culture),
        }

    def col_budget(self) -> tuple[float | None, float, dict[str, float]]:
        """(expected_daily or None, tolerance_pct, components dict).

        Daily target prefers col_expected_daily when > 0; otherwise falls back
        to sum of legacy component fields (hotel+food+transit+culture).
        Percentage is the over-target band for under/within/over ranking.
        """
        parts = self.col_components()
        try:
            total = float(self.col_expected_daily or 0)
        except (TypeError, ValueError):
            total = 0.0
        if total <= 0:
            total = sum(parts.values())
        try:
            pct = float(self.col_tolerance_pct if self.col_tolerance_pct is not None else 25)
        except (TypeError, ValueError):
            pct = 25.0
        pct = max(0.0, min(100.0, pct))
        if total <= 0:
            return None, pct, parts
        return total, pct, parts

    @property
    def duffel_is_test(self) -> bool:
        """Sandbox tokens start with duffel_test_ — fares are NOT live market."""
        t = (self.duffel_access_token or "").strip()
        return t.startswith("duffel_test") or t.startswith("duffel_test_")

    @property
    def amadeus_is_test(self) -> bool:
        return (self.amadeus_env or "test").lower() != "production"


@lru_cache
def _load_base_settings() -> Settings:
    """Load env/dotenv settings only (cached separately from user prefs)."""
    return Settings()


def _merge_user_prefs(base: Settings) -> Settings:
    """Return a copy of *base* with user-pref fields overwritten from user_prefs.db."""
    try:
        from yonder.user_prefs import get_all_prefs

        prefs = get_all_prefs()

        def _f(v: Any, default: float = 0.0) -> float:
            try:
                return max(0.0, float(v or default))
            except (TypeError, ValueError):
                return default

        def _i(v: Any, default: int = 0) -> int:
            try:
                return max(0, int(float(v or default)))
            except (TypeError, ValueError):
                return default

        return base.model_copy(
            update={
                "avoid_countries": prefs.get("avoid_countries", ""),
                "visited_countries": prefs.get("visited_countries", ""),
                "col_expected_daily": _f(prefs.get("col_expected_daily", "0")),
                "col_tolerance_pct": _f(prefs.get("col_tolerance_pct", "25"), 25.0),
                "col_hotel": _f(prefs.get("col_hotel", "0")),
                "col_food": _f(prefs.get("col_food", "0")),
                "col_transit": _f(prefs.get("col_transit", "0")),
                "col_culture": _f(prefs.get("col_culture", "0")),
                "detour_min_stop_days": _i(prefs.get("detour_min_stop_days", "4"), 4),
                "detour_max_stop_days": _i(prefs.get("detour_max_stop_days", "5"), 5),
            }
        )
    except Exception:
        return base


_merged_settings: Settings | None = None


def get_settings() -> Settings:
    """Settings with user prefs merged in. Cached until reload_settings()."""
    global _merged_settings
    if _merged_settings is None:
        _merged_settings = _merge_user_prefs(_load_base_settings())
    return _merged_settings


def reload_settings() -> Settings:
    """Drop all caches so next read picks up .env and user-pref changes."""
    global _merged_settings
    _load_base_settings.cache_clear()
    _merged_settings = None
    from yonder.user_prefs import _invalidate as _inv_prefs

    _inv_prefs()
    return get_settings()
