from __future__ import annotations

from datetime import datetime

import httpx

from yonder.config import Settings
from yonder.providers.base import FlightProvider
from yonder.quota import get_registry
from yonder.types import FlightOffer, SearchQuery, Segment


class TravelpayoutsProvider(FlightProvider):
    """Travelpayouts / Aviasales *cached* price API (free personal use).

    Not always live inventory — great for deal scanning & trends.
    Token: https://www.travelpayouts.com/ → Profile → API token
    Docs: prices_for_dates / cheap / latest endpoints.
    """

    name = "travelpayouts"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.travelpayouts_token)

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        token = self.settings.travelpayouts_token
        # Primary: prices for specific date(s) — cached market data
        params = {
            "origin": query.origin.upper(),
            "destination": query.destination.upper(),
            "departure_at": query.depart_date.isoformat(),
            "currency": query.currency.lower(),
            "limit": min(query.max_results, 30),
            "sorting": "price",
            "unique": "false",
            "one_way": "true" if not query.return_date else "false",
            "token": token,
        }
        if query.return_date:
            params["return_at"] = query.return_date.isoformat()
        if query.nonstop_only:
            params["direct"] = "true"

        url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
        resp = await self.client.get(url, params=params)
        reg = get_registry()
        if resp.status_code >= 400:
            reg.record_call(
                self.name,
                ok=False,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                error=resp.text[:200],
            )
            return await self._search_cheap(query)

        body = resp.json()
        if not body.get("success", True) and not body.get("data"):
            return await self._search_cheap(query)

        reg.record_call(
            self.name,
            ok=True,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            burned_quota=True,
        )
        rows = body.get("data") or []
        offers = [self._map_row(r, query) for r in rows]
        return [o for o in offers if o.price > 0][: query.max_results]

    async def _search_cheap(self, query: SearchQuery) -> list[FlightOffer]:
        params = {
            "origin": query.origin.upper(),
            "destination": query.destination.upper(),
            "depart_date": query.depart_date.strftime("%Y-%m"),
            "currency": query.currency.lower(),
            "token": self.settings.travelpayouts_token,
        }
        if query.return_date:
            params["return_date"] = query.return_date.strftime("%Y-%m")

        url = "https://api.travelpayouts.com/v1/prices/cheap"
        resp = await self.client.get(url, params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"Travelpayouts HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        data = body.get("data") or {}
        # shape: { "BCN": { "0": {price, airline, ...}, "1": ... } }
        offers: list[FlightOffer] = []
        dest_bucket = data.get(query.destination.upper()) or {}
        if isinstance(dest_bucket, dict):
            for _, row in dest_bucket.items():
                if isinstance(row, dict):
                    offers.append(self._map_cheap_row(row, query))
        offers.sort(key=lambda o: o.price)
        return offers[: query.max_results]

    def _map_row(self, row: dict, query: SearchQuery) -> FlightOffer:
        price = float(row.get("price") or row.get("value") or 0)
        airline = row.get("airline") or row.get("airline_code")
        origin = row.get("origin") or query.origin.upper()
        dest = row.get("destination") or query.destination.upper()
        link = row.get("link") or row.get("gate")
        deep = None
        if link and isinstance(link, str):
            deep = link if link.startswith("http") else f"https://www.aviasales.com{link}"

        out = [
            Segment(
                origin=origin,
                destination=dest,
                departure=_parse_dt(row.get("departure_at") or row.get("depart_date")),
                airline=airline,
                flight_number=str(row.get("flight_number")) if row.get("flight_number") else None,
            )
        ]
        transfers = int(row.get("transfers") or row.get("number_of_changes") or 0)
        return FlightOffer(
            provider=self.name,
            price=price,
            currency=(row.get("currency") or query.currency).upper(),
            airlines=[airline] if airline else [],
            segments_out=out,
            stops_out=transfers,
            deep_link=deep,
            bookable=False,
            price_kind="cached",
            notes="cached market price (Travelpayouts — may lag live Google)",
            raw_id=str(row.get("flight_number") or row.get("link") or price),
        )

    def _map_cheap_row(self, row: dict, query: SearchQuery) -> FlightOffer:
        airline = row.get("airline")
        return FlightOffer(
            provider=self.name,
            price=float(row.get("price") or 0),
            currency=query.currency.upper(),
            airlines=[airline] if airline else [],
            segments_out=[
                Segment(
                    origin=query.origin.upper(),
                    destination=query.destination.upper(),
                    departure=_parse_dt(row.get("departure_at")),
                    airline=airline,
                    flight_number=str(row.get("flight_number")) if row.get("flight_number") else None,
                )
            ],
            stops_out=int(row.get("transfers") or 0),
            bookable=False,
            price_kind="cached",
            notes="cached cheap-by-month (Travelpayouts v1)",
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
