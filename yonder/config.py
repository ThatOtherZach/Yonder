from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


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

    default_currency: str = "CAD"
    # Comma-separated ISO2 codes, max 10 (e.g. US,RU,CN)
    avoid_countries: str = ""
    # Comma-separated ISO2 codes — personal travel passport map (unlimited-ish)
    visited_countries: str = ""
    # Lean day-bag components (default_currency). Sum = daily target; 0s = COL ranking off
    # unless col_expected_daily is set (legacy single total).
    col_hotel: float = 0.0
    col_food: float = 0.0
    col_transit: float = 0.0
    col_culture: float = 0.0
    # Legacy / computed total (kept in sync on save when components used)
    col_expected_daily: float = 0.0
    # Acceptable % over expected before hard penalty (e.g. 25 → budget + 25%).
    col_tolerance_pct: float = 25.0
    # scan_all | smart (default smart uses quota router)
    provider_mode: str = "smart"
    # When true, Search/Adventure may show "Test Data" (mock fares)
    testing: bool = False

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

        Daily target = sum of component fields when any are set; else legacy
        col_expected_daily. Percentage is the over-target band for ranking.
        """
        parts = self.col_components()
        summed = sum(parts.values())
        try:
            legacy = float(self.col_expected_daily or 0)
        except (TypeError, ValueError):
            legacy = 0.0
        expected = summed if summed > 0 else legacy
        try:
            pct = float(self.col_tolerance_pct if self.col_tolerance_pct is not None else 25)
        except (TypeError, ValueError):
            pct = 25.0
        pct = max(0.0, min(100.0, pct))
        if expected <= 0:
            return None, pct, parts
        return expected, pct, parts

    @property
    def duffel_is_test(self) -> bool:
        """Sandbox tokens start with duffel_test_ — fares are NOT live market."""
        t = (self.duffel_access_token or "").strip()
        return t.startswith("duffel_test") or t.startswith("duffel_test_")

    @property
    def amadeus_is_test(self) -> bool:
        return (self.amadeus_env or "test").lower() != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop cache so next read picks up .env changes."""
    get_settings.cache_clear()
    return get_settings()
