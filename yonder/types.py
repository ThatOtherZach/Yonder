from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CabinClass(str, Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class SearchQuery(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3, description="IATA origin")
    destination: str = Field(..., min_length=3, max_length=3, description="IATA dest")
    depart_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = "USD"
    max_results: int = Field(default=20, ge=1, le=100)
    nonstop_only: bool = False


class Segment(BaseModel):
    origin: str
    destination: str
    departure: datetime | None = None
    arrival: datetime | None = None
    airline: str | None = None
    flight_number: str | None = None
    duration_minutes: int | None = None


class FlightOffer(BaseModel):
    """Normalized offer — every provider maps into this shape."""

    provider: str
    price: float
    currency: str
    airlines: list[str] = Field(default_factory=list)
    segments_out: list[Segment] = Field(default_factory=list)
    segments_return: list[Segment] = Field(default_factory=list)
    stops_out: int = 0
    stops_return: int | None = None
    duration_out_minutes: int | None = None
    duration_return_minutes: int | None = None
    cabin: str | None = None
    deep_link: str | None = None  # provider-specific (if HTTP URL)
    google_flights_url: str | None = None
    booking_url: str | None = None  # best: provider deep link, else Google Flights
    raw_id: str | None = None
    bookable: bool = True
    # live = market-ish · sandbox = test API · converted = FX applied after fetch
    price_kind: str = "live"  # live | sandbox | cached | mock
    # True when this is placeholder data because the real fare lookup failed or
    # no provider was available — UI shows a "Check Fares" button instead.
    fare_missing: bool = False
    notes: str | None = None
    # Gentle fallback note shown when fare_missing=True:
    #   "recently ~$420" (historical) or "No fare history for this exact route"
    fare_note: str | None = None
    # Set after history pass — schema: ~C$420▼ / ~C$420▲ (vs history)
    display_price: str | None = None  # e.g. ~C$420▼
    display_price_base: str | None = None  # e.g. ~C$420 (no glyph)
    price_sign: str | None = None  # up | down | null
    price_glyph: str | None = None  # ▲ | ▼ | null
    price_tone: str | None = None  # good | bad | neutral
    deal_score: int | None = None
    deal_label: str | None = None  # great | good | ok | high | new
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_stops(self) -> int:
        r = self.stops_return or 0
        return self.stops_out + r

    def summary_route(self) -> str:
        out = " → ".join(s.origin for s in self.segments_out)
        if self.segments_out:
            out += f" → {self.segments_out[-1].destination}"
        return out or "?"


class ProviderResult(BaseModel):
    provider: str
    ok: bool
    offers: list[FlightOffer] = Field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    # Failure classification: "not_configured" | "quota_exhausted" | "cooldown" |
    # "inactive" | "error" | "no_offers" | None (ok)
    failure_kind: str | None = None


class UnifiedSearchResult(BaseModel):
    query: SearchQuery
    results: list[ProviderResult]
    offers: list[FlightOffer]  # sorted cheapest-first, deduped lightly
    searched_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def providers_ok(self) -> list[str]:
        return [r.provider for r in self.results if r.ok]

    @property
    def providers_failed(self) -> list[str]:
        return [r.provider for r in self.results if not r.ok]


class TripGap(BaseModel):
    """A structural gap detected in a saved trip — the next search may fill it."""

    # "quest_inbound" | "quest_outbound" | "missing_return"
    kind: str
    from_iata: str | None = None
    to_iata: str | None = None
    on_or_after_date: date | None = None
    context_label: str = ""
