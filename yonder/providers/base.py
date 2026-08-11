from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from yonder.quota import get_registry
from yonder.types import FlightOffer, ProviderResult, SearchQuery


class FlightProvider(ABC):
    """Adapter interface — each pricing source implements this."""

    name: str = "base"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HTTP client not injected")
        return self._client

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        ...

    async def safe_search(self, query: SearchQuery) -> ProviderResult:
        if not self.is_configured():
            return ProviderResult(
                provider=self.name,
                ok=False,
                error="not configured (missing API credentials)",
                failure_kind="not_configured",
            )
        reg = get_registry()
        budget = reg.ensure(self.name, configured=True)
        if budget.in_cooldown():
            return ProviderResult(
                provider=self.name,
                ok=False,
                error=f"cooldown ({budget.last_error or 'rate limit'})",
                failure_kind="cooldown",
            )
        if budget.monthly_remaining is not None and budget.monthly_remaining <= 0:
            return ProviderResult(
                provider=self.name,
                ok=False,
                error="monthly quota exhausted",
                failure_kind="quota_exhausted",
            )
        if budget.active is False:
            return ProviderResult(
                provider=self.name,
                ok=False,
                error=f"inactive: {budget.last_error or 'health probe failed'}",
                failure_kind="inactive",
            )

        started = time.perf_counter()
        try:
            offers = await self.search(query)
            ms = int((time.perf_counter() - started) * 1000)
            # Distinguish genuine empty results from error
            fk = "no_offers" if not offers else None
            return ProviderResult(
                provider=self.name,
                ok=True,
                offers=offers,
                latency_ms=ms,
                failure_kind=fk,
            )
        except Exception as exc:  # noqa: BLE001
            ms = int((time.perf_counter() - started) * 1000)
            msg = str(exc)
            status = None
            if "HTTP 429" in msg or "rate limit" in msg.lower():
                status = 429
            elif "HTTP 401" in msg or "HTTP 403" in msg:
                status = 401
            # Provider may have already recorded; still ensure failure is noted
            reg.record_call(
                self.name,
                ok=False,
                status_code=status,
                error=msg,
                burned_quota=False,
            )
            # Classify failure_kind from HTTP status and error text so the engine
            # can distinguish quota/auth failures from transient network errors.
            msg_lower = msg.lower()
            if status in (401, 403) or any(
                x in msg_lower
                for x in (
                    "unauthorized",
                    "forbidden",
                    "invalid api key",
                    "invalid key",
                    "authentication failed",
                    "api key",
                    "invalid_api_key",
                )
            ):
                fk = "inactive"
            elif (
                "quota" in msg_lower
                and any(x in msg_lower for x in ("exhausted", "exceeded", "limit", "depleted"))
            ) or "monthly limit" in msg_lower:
                fk = "quota_exhausted"
            elif status == 429 or "rate limit" in msg_lower or "too many requests" in msg_lower:
                fk = "cooldown"
            else:
                fk = "error"
            return ProviderResult(
                provider=self.name,
                ok=False,
                error=msg,
                latency_ms=ms,
                failure_kind=fk,
            )
