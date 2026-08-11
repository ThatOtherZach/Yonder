"""Provider budgets, health, and smart routing.

Rules of thumb:
1. Only consider providers that are *configured* and *active* (health probe passed).
2. Prefer low cost / high remaining quota for bulk multi-leg pricing.
3. Prefer higher quality (live GDS/metasearch) when user wants full scan/confirm.
4. Honor 429 cooldowns and live remaining (SerpAPI account API, Duffel headers).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from yonder.config import ROOT, Settings

CACHE_PATH = ROOT / ".quota_cache.json"
SERPAPI_REFRESH_TTL = 10 * 60  # seconds
HEALTH_TTL = 15 * 60

Mode = Literal["scan", "adventure_leg", "confirm"]

# cost: lower is cheaper to burn; quality: higher = closer to live bookable market
# can_price: False = enrichment only (never selected for ticket pricing)
# live_default: True if typically real market (overridden for test tokens at runtime)
PROVIDER_META: dict[str, dict[str, Any]] = {
    "travelpayouts": {"cost": 0.15, "quality": 0.50, "can_price": True, "live": True},
    "amadeus": {"cost": 0.35, "quality": 0.80, "can_price": True, "live": True},
    "duffel": {"cost": 0.40, "quality": 0.85, "can_price": True, "live": True},
    "aviationstack": {
        "cost": 0.25,
        "quality": 0.20,
        "can_price": False,
        "live": False,
    },
    # Closest to Google Flights links — prefer when accuracy matters
    "serpapi_google_flights": {
        "cost": 0.85,
        "quality": 0.95,
        "can_price": True,
        "live": True,
    },
    "mock": {"cost": 0.0, "quality": 0.0, "can_price": True, "live": False},
}

FARE_PROVIDERS = {n for n, m in PROVIDER_META.items() if m.get("can_price", True)}


@dataclass
class ProviderBudget:
    name: str
    configured: bool = False
    active: bool | None = None  # None = not probed yet
    monthly_limit: int | None = None
    monthly_remaining: int | None = None
    rpm_limit: int | None = None
    rpm_remaining: int | None = None
    cost_weight: float = 0.5
    quality_weight: float = 0.5
    healthy: bool = True
    cooldown_until: float | None = None  # epoch
    last_error: str | None = None
    last_ok_at: float | None = None
    calls_this_session: int = 0
    last_probe_at: float | None = None
    last_quota_refresh: float | None = None
    notes: str = ""

    def in_cooldown(self) -> bool:
        return bool(self.cooldown_until and time.time() < self.cooldown_until)

    def remaining_ratio(self) -> float:
        if self.monthly_limit and self.monthly_remaining is not None:
            if self.monthly_limit <= 0:
                return 0.0
            return max(0.0, min(1.0, self.monthly_remaining / self.monthly_limit))
        if self.rpm_limit and self.rpm_remaining is not None:
            if self.rpm_limit <= 0:
                return 0.0
            return max(0.0, min(1.0, self.rpm_remaining / self.rpm_limit))
        return 0.75  # unknown → assume ok-ish

    def is_usable(self) -> bool:
        if not self.configured:
            return False
        if self.active is False:
            return False
        if not self.healthy or self.in_cooldown():
            return False
        if self.monthly_remaining is not None and self.monthly_remaining <= 0:
            return False
        return True

    def can_price(self) -> bool:
        return bool(PROVIDER_META.get(self.name, {}).get("can_price", True))

    def score(self, mode: Mode, *, settings: Settings | None = None) -> float:
        if not self.is_usable():
            return -1e9
        # Never select enrichment-only APIs for fare modes
        if mode in ("adventure_leg", "scan", "confirm") and not self.can_price():
            return -1e9
        meta = PROVIDER_META.get(self.name, {"cost": 0.5, "quality": 0.5, "live": True})
        cost = float(meta["cost"])
        quality = float(meta["quality"])
        is_live = bool(meta.get("live", True))

        # Runtime: sandbox tokens are NOT live market — deprioritize hard
        if settings is not None:
            if self.name == "duffel" and settings.duffel_is_test:
                is_live = False
                quality = 0.12
                cost = 0.2  # cheap but useless for accuracy
            if self.name == "amadeus" and settings.amadeus_is_test:
                is_live = False
                quality = 0.15

        rem = self.remaining_ratio()
        s = rem * 25.0
        # Prefer live market data so prices match Google Flights links
        if is_live:
            s += 35.0
        else:
            s -= 40.0  # sandbox/mock last resort only

        if mode == "adventure_leg":
            # Accuracy first (SerpAPI/live), then cost among live sources
            s += quality * 40.0
            s += (1.0 - cost) * 15.0
        elif mode == "confirm":
            s += quality * 50.0
            s += (1.0 - cost) * 10.0
        else:
            s += quality * 35.0
            s += (1.0 - cost) * 15.0
        if self.last_ok_at and (time.time() - self.last_ok_at) < 3600:
            s += 3.0
        return s

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d["in_cooldown"] = self.in_cooldown()
        d["usable"] = self.is_usable()
        d["can_price"] = self.can_price()
        d["remaining_ratio"] = round(self.remaining_ratio(), 3)
        d["score_adventure"] = round(self.score("adventure_leg"), 1)
        d["score_scan"] = round(self.score("scan"), 1)
        d["is_sandbox_hint"] = "sandbox" in (self.notes or "").lower() or "test" in (
            self.notes or ""
        ).lower()
        if self.cooldown_until:
            d["cooldown_until_iso"] = datetime.fromtimestamp(
                self.cooldown_until, tz=timezone.utc
            ).isoformat()
        return d


class QuotaRegistry:
    def __init__(self) -> None:
        self._budgets: dict[str, ProviderBudget] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if not CACHE_PATH.exists():
            return
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            for name, data in (raw.get("budgets") or {}).items():
                b = ProviderBudget(name=name)
                for k, v in data.items():
                    if hasattr(b, k) and k != "name":
                        setattr(b, k, v)
                self._budgets[name] = b
        except Exception:
            pass

    def save_cache(self) -> None:
        try:
            payload = {
                "saved_at": time.time(),
                "budgets": {
                    n: {
                        k: v
                        for k, v in asdict(b).items()
                        if k
                        not in ()  # keep all serializable fields
                    }
                    for n, b in self._budgets.items()
                },
            }
            CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def ensure(self, name: str, *, configured: bool) -> ProviderBudget:
        meta = PROVIDER_META.get(name, {"cost": 0.5, "quality": 0.5})
        b = self._budgets.get(name)
        if b is None:
            b = ProviderBudget(
                name=name,
                configured=configured,
                cost_weight=float(meta["cost"]),
                quality_weight=float(meta["quality"]),
            )
            self._budgets[name] = b
        else:
            b.configured = configured
            b.cost_weight = float(meta["cost"])
            b.quality_weight = float(meta["quality"])
        return b

    def get(self, name: str) -> ProviderBudget | None:
        return self._budgets.get(name)

    def all_public(self) -> list[dict[str, Any]]:
        return [b.to_public() for b in sorted(self._budgets.values(), key=lambda x: x.name)]

    def record_call(
        self,
        name: str,
        *,
        ok: bool,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
        error: str | None = None,
        burned_quota: bool = True,
    ) -> None:
        b = self.ensure(name, configured=True)
        b.calls_this_session += 1 if burned_quota else 0
        headers = {k.lower(): v for k, v in (headers or {}).items()}

        # Duffel-style
        if "ratelimit-limit" in headers:
            try:
                b.rpm_limit = int(float(headers["ratelimit-limit"]))
            except ValueError:
                pass
        if "ratelimit-remaining" in headers:
            try:
                b.rpm_remaining = int(float(headers["ratelimit-remaining"]))
            except ValueError:
                pass
        if "ratelimit-reset" in headers:
            # RFC date or seconds — try epoch first
            reset = headers["ratelimit-reset"]
            try:
                b.cooldown_until = float(reset)
            except ValueError:
                pass

        # Generic x-ratelimit-*
        if "x-ratelimit-remaining" in headers:
            try:
                b.rpm_remaining = int(float(headers["x-ratelimit-remaining"]))
            except ValueError:
                pass

        if status_code == 429 or (error and "429" in error):
            b.healthy = False
            b.cooldown_until = time.time() + 60
            b.last_error = error or "rate limited (429)"
        elif status_code and status_code >= 500:
            b.last_error = error or f"HTTP {status_code}"
            b.cooldown_until = time.time() + 30
        elif ok:
            b.healthy = True
            b.active = True
            b.last_ok_at = time.time()
            b.last_error = None
            if b.monthly_remaining is not None and burned_quota:
                b.monthly_remaining = max(0, b.monthly_remaining - 1)
        else:
            b.last_error = error
            # auth failures → inactive
            if status_code in (401, 403) or (
                error and any(x in error.lower() for x in ("invalid", "unauthorized", "forbidden", "api key"))
            ):
                b.active = False
                b.healthy = False

        self.save_cache()

    def mark_inactive(self, name: str, error: str) -> None:
        b = self.ensure(name, configured=True)
        b.active = False
        b.healthy = False
        b.last_error = error
        self.save_cache()

    def mark_active(self, name: str, notes: str = "") -> None:
        b = self.ensure(name, configured=True)
        b.active = True
        b.healthy = True
        b.last_error = None
        b.last_ok_at = time.time()
        b.last_probe_at = time.time()
        if notes:
            b.notes = notes
        self.save_cache()

    async def refresh_serpapi(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if not settings.serpapi_key:
            return
        b = self.ensure("serpapi_google_flights", configured=True)
        now = time.time()
        if b.last_quota_refresh and now - b.last_quota_refresh < SERPAPI_REFRESH_TTL:
            return
        try:
            resp = await client.get(
                "https://serpapi.com/account.json",
                params={"api_key": settings.serpapi_key},
                timeout=20.0,
            )
            if resp.status_code >= 400:
                self.mark_inactive(
                    "serpapi_google_flights",
                    f"account API HTTP {resp.status_code}",
                )
                return
            data = resp.json()
            # common field names across SerpAPI account responses
            left = (
                data.get("total_searches_left")
                or data.get("plan_searches_left")
                or data.get("searches_left")
            )
            limit = (
                data.get("plan_monthly_limit")
                or data.get("searches_per_month")
                or data.get("plan_searches_limit")
            )
            if left is not None:
                b.monthly_remaining = int(left)
            if limit is not None:
                b.monthly_limit = int(limit)
            # this month used
            used = data.get("this_month_usage") or data.get("plan_searches_used")
            if b.monthly_limit is not None and used is not None and b.monthly_remaining is None:
                b.monthly_remaining = max(0, int(b.monthly_limit) - int(used))
            b.active = True
            b.healthy = (b.monthly_remaining is None) or (b.monthly_remaining > 0)
            b.last_quota_refresh = now
            b.notes = f"plan={data.get('plan_name') or data.get('plan_id') or '?'}"
            b.last_error = None
            self.save_cache()
        except Exception as exc:  # noqa: BLE001
            b.last_error = f"quota refresh failed: {exc}"

    async def probe_active(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        force: bool = False,
    ) -> list[str]:
        """Lightweight health checks — only configured providers. Returns active names."""
        now = time.time()
        active: list[str] = []

        # Travelpayouts
        if settings.travelpayouts_token:
            b = self.ensure("travelpayouts", configured=True)
            if force or b.active is None or not b.last_probe_at or now - b.last_probe_at > HEALTH_TTL:
                try:
                    r = await client.get(
                        "https://api.travelpayouts.com/v2/prices/latest",
                        params={
                            "currency": "usd",
                            "period_type": "year",
                            "page": 1,
                            "limit": 1,
                            "token": settings.travelpayouts_token,
                        },
                        timeout=15.0,
                    )
                    if r.status_code < 400 and (r.json().get("success") is not False):
                        self.mark_active("travelpayouts", "probe ok")
                        active.append("travelpayouts")
                    else:
                        self.mark_inactive(
                            "travelpayouts",
                            f"probe HTTP {r.status_code}: {r.text[:120]}",
                        )
                except Exception as exc:  # noqa: BLE001
                    self.mark_inactive("travelpayouts", str(exc))
            elif b.is_usable():
                active.append("travelpayouts")

        # Amadeus — token only
        if settings.amadeus_client_id and settings.amadeus_client_secret:
            b = self.ensure("amadeus", configured=True)
            if force or b.active is None or not b.last_probe_at or now - b.last_probe_at > HEALTH_TTL:
                try:
                    r = await client.post(
                        f"{settings.amadeus_base}/v1/security/oauth2/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": settings.amadeus_client_id,
                            "client_secret": settings.amadeus_client_secret,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=15.0,
                    )
                    if r.status_code < 400 and r.json().get("access_token"):
                        self.mark_active("amadeus", f"env={settings.amadeus_env}")
                        # free tier rough monthly allotment (varies; track usage locally)
                        if b.monthly_limit is None:
                            b.monthly_limit = 2000  # soft default free-tier-ish
                            if b.monthly_remaining is None:
                                b.monthly_remaining = 2000
                        active.append("amadeus")
                    else:
                        self.mark_inactive("amadeus", f"auth HTTP {r.status_code}")
                except Exception as exc:  # noqa: BLE001
                    self.mark_inactive("amadeus", str(exc))
            elif b.is_usable():
                active.append("amadeus")

        # Duffel — cheap GET
        if settings.duffel_access_token:
            b = self.ensure("duffel", configured=True)
            if force or b.active is None or not b.last_probe_at or now - b.last_probe_at > HEALTH_TTL:
                try:
                    r = await client.get(
                        "https://api.duffel.com/air/airlines",
                        headers={
                            "Authorization": f"Bearer {settings.duffel_access_token}",
                            "Duffel-Version": "v2",
                            "Accept": "application/json",
                        },
                        params={"limit": 1},
                        timeout=15.0,
                    )
                    self.record_call(
                        "duffel",
                        ok=r.status_code < 400,
                        status_code=r.status_code,
                        headers=dict(r.headers),
                        error=None if r.status_code < 400 else r.text[:200],
                        burned_quota=False,
                    )
                    if r.status_code < 400:
                        note = (
                            "SANDBOX token (duffel_test) — fares are fake/demo, not Google market"
                            if settings.duffel_is_test
                            else "probe ok · live token"
                        )
                        self.mark_active("duffel", note)
                        active.append("duffel")
                    else:
                        self.mark_inactive("duffel", f"probe HTTP {r.status_code}")
                except Exception as exc:  # noqa: BLE001
                    self.mark_inactive("duffel", str(exc))
            elif b.is_usable():
                active.append("duffel")

        # AviationStack — free tier: airports OK; not a fare API
        if settings.aviationstack_key:
            b = self.ensure("aviationstack", configured=True)
            if force or b.active is None or not b.last_probe_at or now - b.last_probe_at > HEALTH_TTL:
                try:
                    r = await client.get(
                        "http://api.aviationstack.com/v1/airports",
                        params={"access_key": settings.aviationstack_key, "limit": 1},
                        timeout=15.0,
                    )
                    data = r.json() if r.content else {}
                    err = (data.get("error") or {}) if isinstance(data, dict) else {}
                    if r.status_code < 400 and not err:
                        self.mark_active(
                            "aviationstack",
                            "airports OK · enrichment only (not used for fares)",
                        )
                        if b.monthly_limit is None:
                            b.monthly_limit = 100
                            if b.monthly_remaining is None:
                                b.monthly_remaining = 100
                        b.last_error = None
                        active.append("aviationstack")
                    else:
                        msg = (
                            (err.get("info") if isinstance(err, dict) else None)
                            or (err.get("message") if isinstance(err, dict) else None)
                            or f"HTTP {r.status_code}"
                        )
                        self.mark_inactive("aviationstack", str(msg))
                except Exception as exc:  # noqa: BLE001
                    self.mark_inactive("aviationstack", str(exc))
            elif b.is_usable():
                active.append("aviationstack")

        # SerpAPI — account API (also refreshes remaining)
        if settings.serpapi_key:
            b = self.ensure("serpapi_google_flights", configured=True)
            await self.refresh_serpapi(settings, client)
            b = self.ensure("serpapi_google_flights", configured=True)
            b.last_probe_at = time.time()
            if b.is_usable():
                active.append("serpapi_google_flights")
            elif b.active is not False and (b.monthly_remaining is None or b.monthly_remaining > 0):
                # refresh failed but key present — optimistically allow once
                if b.active is None:
                    b.active = True
                    active.append("serpapi_google_flights")

        return active

    def choose(
        self,
        settings: Settings,
        *,
        mode: Mode = "scan",
        need: int = 1,
        include_mock: bool = False,
        active_names: list[str] | None = None,
        force_all: bool = False,
    ) -> list[str]:
        """Pick provider names. Only configured + active + healthy unless force_all."""
        configured = set(settings.configured_providers())
        if include_mock:
            configured.add("mock")
            self.ensure("mock", configured=True)
            self.mark_active("mock", "demo")

        # Seed budgets for configured keys even if not probed
        for name in configured:
            self.ensure(name, configured=True)

        candidates: list[ProviderBudget] = []
        for name in configured:
            b = self.ensure(name, configured=True)
            # Fare modes never pick enrichment-only providers
            if mode in ("adventure_leg", "scan", "confirm") and not b.can_price():
                continue
            if active_names is not None and name not in active_names and name != "mock":
                if b.active is not True and not force_all:
                    continue
            if force_all:
                if b.configured and not b.in_cooldown() and b.can_price():
                    candidates.append(b)
            elif b.is_usable():
                candidates.append(b)

        if not candidates and include_mock:
            self.ensure("mock", configured=True)
            self.mark_active("mock")
            return ["mock"]

        ranked = sorted(
            candidates, key=lambda b: b.score(mode, settings=settings), reverse=True
        )
        names = [b.name for b in ranked if b.score(mode, settings=settings) > -1e8]
        if mode == "adventure_leg":
            if not names:
                return ["mock"] if include_mock else []
            return names[: max(1, need)]
        if force_all:
            return names
        return names[: max(1, need)]

    def estimate_cost(self, provider: str, calls: int) -> dict[str, Any]:
        b = self.get(provider)
        left = b.monthly_remaining if b else None
        return {
            "provider": provider,
            "calls_needed": calls,
            "monthly_remaining": left,
            "enough": left is None or left >= calls,
        }


_REGISTRY: QuotaRegistry | None = None


def get_registry() -> QuotaRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = QuotaRegistry()
    return _REGISTRY


async def choose_providers(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    mode: Mode = "scan",
    need: int = 1,
    include_mock: bool = False,
    force_all: bool = False,
    probe: bool = True,
) -> list[str]:
    reg = get_registry()
    active: list[str] | None = None
    if probe:
        active = await reg.probe_active(settings, client)
    return reg.choose(
        settings,
        mode=mode,
        need=need,
        include_mock=include_mock,
        active_names=active,
        force_all=force_all,
    )


def budgets_snapshot(settings: Settings) -> list[dict[str, Any]]:
    reg = get_registry()
    for name in settings.configured_providers():
        reg.ensure(name, configured=True)
    if settings.xai_api_key:
        reg.ensure("grok", configured=True)
    return reg.all_public()


# ── Last-search provider error snapshot (owner alerting) ─────────────────────
#
# A server-wide (not per-session) record of provider failures from the most
# recent search that returned no live offers.  Stored separately from the quota
# registry so the owner can see what actually happened even after a key swap
# clears the stale "exhausted" TTL from the registry.

_LAST_PROVIDER_ERR_PATH = ROOT / ".last_provider_errors.json"
_last_provider_errors: dict[str, Any] = {}


def record_last_search_errors(
    results: "list[Any]",
    *,
    origin: str = "",
    destination: str = "",
) -> None:
    """Persist provider failure details from the most recent failed search.

    Call this whenever a search returns no live offers due to provider failures.
    ``results`` is a list of ``ProviderResult``-like objects (need .ok, .provider,
    .failure_kind, .error attributes).
    """
    global _last_provider_errors
    snapshot: dict[str, Any] = {
        "recorded_at": time.time(),
        "origin": origin,
        "destination": destination,
        "providers": [
            {
                "provider": r.provider,
                "failure_kind": r.failure_kind or "error",
                "error": (r.error or "")[:200],
            }
            for r in results
            if not r.ok
        ],
    }
    _last_provider_errors = snapshot
    try:
        _LAST_PROVIDER_ERR_PATH.write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get_last_search_errors() -> dict[str, Any]:
    """Return the last-search provider error snapshot, loading from disk if cold."""
    global _last_provider_errors
    if _last_provider_errors:
        return dict(_last_provider_errors)
    if _LAST_PROVIDER_ERR_PATH.exists():
        try:
            data = json.loads(_LAST_PROVIDER_ERR_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _last_provider_errors = data
                return dict(data)
        except Exception:
            pass
    return {}
