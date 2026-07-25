from __future__ import annotations

from datetime import datetime

import httpx

from yonder.config import Settings
from yonder.providers.base import FlightProvider
from yonder.quota import get_registry
from yonder.types import FlightOffer, SearchQuery, Segment


class SerpApiGoogleFlightsProvider(FlightProvider):
    """SerpAPI Google Flights — scrapes Google Flights results.

    Free tier has a monthly search quota. https://serpapi.com/
    """

    name = "serpapi_google_flights"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.serpapi_key)

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        params: dict = {
            "engine": "google_flights",
            "departure_id": query.origin.upper(),
            "arrival_id": query.destination.upper(),
            "outbound_date": query.depart_date.isoformat(),
            "currency": query.currency.upper(),
            "hl": "en",
            "api_key": self.settings.serpapi_key,
            "type": "1" if query.return_date else "2",  # 1=round trip, 2=one way
        }
        if query.return_date:
            params["return_date"] = query.return_date.isoformat()
        if query.nonstop_only:
            params["stops"] = "0"
        # cabin: 1 economy, 2 premium, 3 business, 4 first
        cabin_map = {
            "economy": "1",
            "premium_economy": "2",
            "business": "3",
            "first": "4",
        }
        params["travel_class"] = cabin_map.get(query.cabin.value, "1")
        params["adults"] = query.adults

        resp = await self.client.get("https://serpapi.com/search.json", params=params)
        reg = get_registry()
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        if resp.status_code >= 400 or body.get("error"):
            err = body.get("error") or f"SerpAPI HTTP {resp.status_code}: {resp.text[:300]}"
            reg.record_call(
                self.name,
                ok=False,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                error=str(err),
            )
            raise RuntimeError(str(err))

        reg.record_call(
            self.name,
            ok=True,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            burned_quota=True,
        )
        # Refresh remaining (doesn't burn quota)
        try:
            await reg.refresh_serpapi(self.settings, self.client)
        except Exception:
            pass

        best = body.get("best_flights") or []
        other = body.get("other_flights") or []
        rows = best + other
        offers = [self._map(r, query) for r in rows if r.get("price") is not None]
        offers.sort(key=lambda o: o.price)
        return offers[: query.max_results]

    def _map(self, row: dict, query: SearchQuery) -> FlightOffer:
        flights = row.get("flights") or []
        segs: list[Segment] = []
        airlines: list[str] = []
        for f in flights:
            dep = f.get("departure_airport") or {}
            arr = f.get("arrival_airport") or {}
            airline = f.get("airline")
            if airline and airline not in airlines:
                airlines.append(airline)
            segs.append(
                Segment(
                    origin=dep.get("id") or "?",
                    destination=arr.get("id") or "?",
                    departure=_parse_dt(dep.get("time")),
                    arrival=_parse_dt(arr.get("time")),
                    airline=airline,
                    flight_number=f.get("flight_number"),
                    duration_minutes=f.get("duration"),
                )
            )
        total_dur = row.get("total_duration")
        return FlightOffer(
            provider=self.name,
            price=float(row["price"]),
            currency=query.currency.upper(),
            airlines=airlines,
            segments_out=segs,
            stops_out=max(0, len(segs) - 1),
            duration_out_minutes=int(total_dur) if total_dur is not None else None,
            deep_link=None,  # booking_token is not a URL
            price_kind="live",
            notes="Live Google Flights snapshot via SerpAPI — closest to Google Flights links",
            bookable=False,
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None
