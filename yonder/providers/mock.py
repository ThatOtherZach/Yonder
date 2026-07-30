from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta

import httpx

from yonder.providers.base import FlightProvider
from yonder.types import FlightOffer, SearchQuery, Segment

log = logging.getLogger(__name__)

_AIRLINES = ["AC", "UA", "DL", "AA", "WS", "BA", "LH", "AF", "EK", "QR"]


class MockProvider(FlightProvider):
    """Deterministic fake prices so you can try the scanner with zero keys."""

    name = "mock"

    def is_configured(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        seed = hashlib.md5(
            f"{query.origin}{query.destination}{query.depart_date}{query.return_date}".encode()
        ).hexdigest()
        rng = random.Random(seed)
        base = 120 + rng.randint(0, 600)
        offers: list[FlightOffer] = []
        for i in range(min(query.max_results, 8)):
            airline = rng.choice(_AIRLINES)
            stops = 0 if query.nonstop_only else rng.choice([0, 0, 1, 1, 2])
            price = round(base + i * rng.uniform(15, 80) + stops * 40, 2)
            dep = datetime.combine(query.depart_date, datetime.min.time()) + timedelta(
                hours=rng.randint(6, 20)
            )
            segs = [
                Segment(
                    origin=query.origin.upper(),
                    destination=query.destination.upper() if stops == 0 else "X" + str(i % 9),
                    departure=dep,
                    arrival=dep + timedelta(hours=rng.randint(2, 8)),
                    airline=airline,
                    flight_number=f"{airline}{rng.randint(100, 999)}",
                    duration_minutes=rng.randint(90, 600),
                )
            ]
            if stops:
                mid = segs[0].destination
                segs.append(
                    Segment(
                        origin=mid,
                        destination=query.destination.upper(),
                        departure=dep + timedelta(hours=3),
                        arrival=dep + timedelta(hours=rng.randint(5, 12)),
                        airline=airline,
                        flight_number=f"{airline}{rng.randint(100, 999)}",
                    )
                )
            ret: list[Segment] = []
            if query.return_date:
                rdep = datetime.combine(query.return_date, datetime.min.time()) + timedelta(
                    hours=rng.randint(8, 18)
                )
                ret = [
                    Segment(
                        origin=query.destination.upper(),
                        destination=query.origin.upper(),
                        departure=rdep,
                        arrival=rdep + timedelta(hours=rng.randint(2, 9)),
                        airline=airline,
                        flight_number=f"{airline}{rng.randint(100, 999)}",
                    )
                ]
            offers.append(
                FlightOffer(
                    provider=self.name,
                    price=price,
                    currency=query.currency.upper(),
                    airlines=[airline],
                    segments_out=segs,
                    segments_return=ret,
                    stops_out=stops,
                    stops_return=0 if ret else None,
                    price_kind="mock",
                    notes="demo data — not a real fare",
                    bookable=False,
                )
            )
        return offers


class AIDemoProvider(FlightProvider):
    """Demo provider: Grok-invented fares when a key is present, seeded mock otherwise.

    Fares are always marked mock/non-bookable and excluded from price history
    (history.py skips price_kind=="mock").
    """

    name = "mock"

    def __init__(
        self,
        settings: object,  # yonder.config.Settings — avoid circular at module level
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client)
        self._settings = settings
        self._fallback = MockProvider(client)

    def is_configured(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        import asyncio

        from yonder.ai_usage import log_usage
        from yonder.grok import GrokClient

        # Only attempt AI generation when a key is present
        if not (self._settings.xai_api_key if hasattr(self._settings, "xai_api_key") else False):
            return await self._offline_fallback(query)

        # Internal timeout must exceed the HTTP client's read timeout (22 s) so
        # the real HTTP error propagates instead of being swallowed by wait_for.
        # asyncio.TimeoutError.__str__ returns "" in CPython, so catch it
        # explicitly to log a useful message.
        _TIMEOUT = 28.0
        try:
            async with GrokClient(self._settings, self._client) as grok:
                offers = await asyncio.wait_for(
                    grok.invent_demo_fares(query), timeout=_TIMEOUT
                )
            usage = grok.accumulated_usage
            if usage:
                await log_usage("demo_fares", usage)
            if offers:
                return offers
            # Empty list → fall back to seeded
            log.warning("AIDemoProvider: Grok returned no offers, falling back to seeded mock")
        except asyncio.TimeoutError:
            log.warning(
                "AIDemoProvider: Grok call timed out after %.0fs — "
                "check XAI_API_KEY validity and model name in Settings, then falling back to seeded mock",
                _TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "AIDemoProvider: Grok call failed (%s: %s) — falling back to seeded mock",
                type(exc).__name__,
                exc,
            )

        return await self._offline_fallback(query)

    async def _offline_fallback(self, query: SearchQuery) -> list[FlightOffer]:
        """Seeded mock with a notice note so the UI can flag offline mode."""
        offers = await self._fallback.search(query)
        return [
            o.model_copy(update={"notes": "AI planner unavailable — showing offline picks"})
            for o in offers
        ]
