"""Per-IP rate limiting and global daily budget guard.

Protects expensive AI and flight-API calls from traffic spikes and bots.

Design
------
- In-memory sliding-window counters (no Redis needed for single-process uvicorn).
  State resets on server restart, which is fine — the daily budget is a soft
  circuit-breaker, not an accounting ledger.
- Client key = SHA-256(direct-connection IP only).
  * Session cookie is intentionally excluded — it is unsigned; rotating it gives a
    fresh window on every request, trivially bypassing the limit.
  * User-Agent is excluded — it is freely rotatable by any HTTP client.
  * X-Forwarded-For is trusted **only** when the direct connection comes from the
    address in the ``TRUSTED_PROXY`` env var.  Without it, XFF is ignored to prevent
    header-spoofing bypass.
- MOCK mode bypass: each call site passes ``mock=True`` when neither an AI key
  (``grok_ready()``) nor a fare provider is reachable — zero backend spend possible.
  AI-only endpoints use ``mock=not grok_ready()``; mixed endpoints use
  ``mock=not (grok_ready() or bool(configured_providers()))``.
- ``DAILY_AI_BUDGET=0`` means zero budget (block all); use a negative value to
  disable the budget guard entirely.
- Master off-switch via env var ``RATE_LIMIT_ENABLED=false`` for tests / dev.

Configuration (all optional, set in .env or environment):
  RATE_LIMIT_ENABLED   — master switch (default: true)
  DAILY_AI_BUDGET      — max expensive search-equivalents per UTC day (default: 500;
                          0=block all; negative=disabled)
  TRUSTED_PROXY        — IP of a trusted upstream proxy whose XFF header is accepted
                          (e.g. "10.0.0.1").  Leave blank to ignore XFF entirely.
  RATE_SEARCH_LIMIT    — /explore calls per window per IP (default: 8)
  RATE_SEARCH_WINDOW   — window length in seconds for searches (default: 60)
  RATE_PLAN_LIMIT      — /api/quest|detour/plan calls per window (default: 5)
  RATE_PLAN_WINDOW     — window length in seconds for plan calls (default: 60)
  RATE_FARE_LIMIT      — fare-refresh calls per window per IP (default: 10)
  RATE_FARE_WINDOW     — window length in seconds for fare calls (default: 60)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections import deque
from datetime import date
from typing import NamedTuple

from fastapi import Request


# ── Configuration (read once at import; env changes need a restart) ───────────


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", True)

# Search: /explore submit
SEARCH_LIMIT: int = _env_int("RATE_SEARCH_LIMIT", 8)    # max calls per window
SEARCH_WINDOW: int = _env_int("RATE_SEARCH_WINDOW", 60)  # seconds

# Plan: /api/quest/plan and /api/detour/plan
PLAN_LIMIT: int = _env_int("RATE_PLAN_LIMIT", 5)
PLAN_WINDOW: int = _env_int("RATE_PLAN_WINDOW", 60)

# Fare: /api/price-refresh, /api/leg-fare, /saved/*/refresh
FARE_LIMIT: int = _env_int("RATE_FARE_LIMIT", 10)
FARE_WINDOW: int = _env_int("RATE_FARE_WINDOW", 60)

# Global daily budget: each search = 1.0, each plan = 1.0, each fare = 0.5
DAILY_AI_BUDGET: int = _env_int("DAILY_AI_BUDGET", 500)


# ── Internal state ────────────────────────────────────────────────────────────

_lock: asyncio.Lock = asyncio.Lock()

# sliding-window stores: scoped-key → deque of monotonic timestamps
_windows: dict[str, deque[float]] = {}

# daily budget: reset each UTC day
_budget_date: str = ""
_budget_count: float = 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────


class RateLimitResult(NamedTuple):
    """Outcome of a rate limit check."""

    allowed: bool
    retry_after: int  # seconds until the oldest call expires (0 when allowed)


# Only trust X-Forwarded-For from a known, configured upstream proxy.
_TRUSTED_PROXY: str = os.environ.get("TRUSTED_PROXY", "").strip()


def _client_key(request: Request, _sess: str) -> str:
    """IP-only rate-limit key.

    User-Agent and session cookie are intentionally excluded — both are freely
    rotatable by any HTTP client and must not form part of the quota key.

    X-Forwarded-For is accepted only when the direct TCP connection comes from
    the IP configured in the ``TRUSTED_PROXY`` env var.  Without that setting
    every request is keyed on the direct connection IP, preventing clients from
    spoofing XFF to obtain a fresh quota window per request.
    """
    direct_ip: str = getattr(request.client, "host", None) or "unknown"
    if _TRUSTED_PROXY and direct_ip == _TRUSTED_PROXY:
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = xff or direct_ip
    else:
        ip = direct_ip
    return "ip:" + hashlib.sha256(ip.encode()).hexdigest()[:24]


def _sliding_check(key: str, limit: int, window: int, now: float) -> RateLimitResult:
    """Non-locking sliding-window check — must be called inside ``_lock``."""
    q: deque[float] = _windows.setdefault(key, deque())
    cutoff = now - window
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        # When limit=0 the deque may be empty — fall back to the full window delay.
        oldest = q[0] if q else (now - window + 1.0)
        retry_after = max(1, int(oldest + window - now) + 1)
        return RateLimitResult(allowed=False, retry_after=retry_after)
    q.append(now)
    return RateLimitResult(allowed=True, retry_after=0)


# ── Public API ────────────────────────────────────────────────────────────────


async def check_search(
    request: Request, sess: str, *, mock: bool = False
) -> RateLimitResult:
    """Per-session rate check for ``/explore`` (search submit)."""
    if mock or not RATE_LIMIT_ENABLED:
        return RateLimitResult(allowed=True, retry_after=0)
    async with _lock:
        key = f"search:{_client_key(request, sess)}"
        return _sliding_check(key, SEARCH_LIMIT, SEARCH_WINDOW, time.monotonic())


async def check_plan(
    request: Request, sess: str, *, mock: bool = False
) -> RateLimitResult:
    """Per-session rate check for ``/api/quest/plan`` and ``/api/detour/plan``."""
    if mock or not RATE_LIMIT_ENABLED:
        return RateLimitResult(allowed=True, retry_after=0)
    async with _lock:
        key = f"plan:{_client_key(request, sess)}"
        return _sliding_check(key, PLAN_LIMIT, PLAN_WINDOW, time.monotonic())


async def check_fare(
    request: Request, sess: str, *, mock: bool = False
) -> RateLimitResult:
    """Per-session rate check for fare-refresh endpoints."""
    if mock or not RATE_LIMIT_ENABLED:
        return RateLimitResult(allowed=True, retry_after=0)
    async with _lock:
        key = f"fare:{_client_key(request, sess)}"
        return _sliding_check(key, FARE_LIMIT, FARE_WINDOW, time.monotonic())


def check_daily_budget(*, mock: bool = False, cost: float = 1.0) -> bool:
    """Return True when the global daily budget permits this call.

    Admission is based on the *projected* total: the call is allowed only when
    ``current_count + cost <= DAILY_AI_BUDGET``.  This prevents fractional-cost
    paths from silently overspending when the remaining balance is smaller than
    the cost of a single call (e.g. budget=1, cost=0.5: allows exactly 2 calls,
    not 3).

    If ``cost`` alone exceeds the full daily budget the call is always denied
    (zero-balance semantics — deliberately strict).

    Increments the counter when allowed.  Not async — single ``+=`` on a float
    is atomic under CPython's GIL, so no extra lock is needed here.
    Always returns True in MOCK mode or when ``RATE_LIMIT_ENABLED=false``.
    """
    global _budget_date, _budget_count  # noqa: PLW0603

    # DAILY_AI_BUDGET < 0 disables the budget guard entirely (env opt-out).
    # 0 means "zero budget" — every call is denied when not in mock/disabled mode.
    if mock or not RATE_LIMIT_ENABLED or DAILY_AI_BUDGET < 0:
        return True

    today = date.today().isoformat()
    if _budget_date != today:
        _budget_date = today
        _budget_count = 0.0

    # Reject when adding this cost would push the counter past the ceiling.
    if _budget_count + cost > DAILY_AI_BUDGET:
        return False

    _budget_count += cost
    return True


def daily_budget_status() -> dict:
    """Current daily budget snapshot (useful for admin/debug endpoints)."""
    return {
        "date": _budget_date or date.today().isoformat(),
        "used": _budget_count,
        "limit": DAILY_AI_BUDGET,
        "enabled": RATE_LIMIT_ENABLED,
    }
