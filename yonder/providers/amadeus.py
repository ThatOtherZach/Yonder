from __future__ import annotations

from datetime import datetime

import httpx

from yonder.config import Settings
from yonder.providers.base import FlightProvider
from yonder.quota import get_registry
from yonder.types import CabinClass, FlightOffer, SearchQuery, Segment

_CABIN_MAP = {
    CabinClass.ECONOMY: "ECONOMY",
    CabinClass.PREMIUM_ECONOMY: "PREMIUM_ECONOMY",
    CabinClass.BUSINESS: "BUSINESS",
    CabinClass.FIRST: "FIRST",
}


class AmadeusProvider(FlightProvider):
    """Amadeus Self-Service Flight Offers Search.

    Free monthly quota after signup: https://developers.amadeus.com/
    Test env = sandbox data; switch AMADEUS_ENV=production for live.
    """

    name = "amadeus"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.settings = settings
        self._token: str | None = None

    def is_configured(self) -> bool:
        return bool(self.settings.amadeus_client_id and self.settings.amadeus_client_secret)

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        url = f"{self.settings.amadeus_base}/v1/security/oauth2/token"
        resp = await self.client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.amadeus_client_id,
                "client_secret": self.settings.amadeus_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        token = await self._get_token()
        params: dict = {
            "originLocationCode": query.origin.upper(),
            "destinationLocationCode": query.destination.upper(),
            "departureDate": query.depart_date.isoformat(),
            "adults": query.adults,
            "currencyCode": query.currency.upper(),
            "max": min(query.max_results, 50),
            "travelClass": _CABIN_MAP[query.cabin],
            "nonStop": str(query.nonstop_only).lower(),
        }
        if query.return_date:
            params["returnDate"] = query.return_date.isoformat()

        url = f"{self.settings.amadeus_base}/v2/shopping/flight-offers"
        resp = await self.client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        reg = get_registry()
        if resp.status_code >= 400:
            detail = resp.text[:400]
            reg.record_call(
                self.name,
                ok=False,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                error=detail,
            )
            raise RuntimeError(f"Amadeus HTTP {resp.status_code}: {detail}")
        reg.record_call(
            self.name,
            ok=True,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            burned_quota=True,
        )
        data = resp.json().get("data") or []
        return [self._map_offer(item, query.currency) for item in data]

    def _map_offer(self, item: dict, currency: str) -> FlightOffer:
        price = float(item.get("price", {}).get("grandTotal") or item.get("price", {}).get("total") or 0)
        cur = item.get("price", {}).get("currency") or currency
        itineraries = item.get("itineraries") or []
        out_segs = self._map_itinerary(itineraries[0]) if itineraries else []
        ret_segs = self._map_itinerary(itineraries[1]) if len(itineraries) > 1 else []
        airlines = sorted(
            {
                s.airline
                for s in out_segs + ret_segs
                if s.airline
            }
        )
        return FlightOffer(
            provider=self.name,
            price=price,
            currency=cur,
            airlines=airlines,
            segments_out=out_segs,
            segments_return=ret_segs,
            stops_out=max(0, len(out_segs) - 1),
            stops_return=max(0, len(ret_segs) - 1) if ret_segs else None,
            duration_out_minutes=_parse_iso_duration(
                (itineraries[0] or {}).get("duration") if itineraries else None
            ),
            duration_return_minutes=_parse_iso_duration(
                (itineraries[1] or {}).get("duration") if len(itineraries) > 1 else None
            ),
            cabin=(item.get("travelerPricings") or [{}])[0]
            .get("fareDetailsBySegment", [{}])[0]
            .get("cabin"),
            raw_id=item.get("id"),
            price_kind="live" if self.settings.amadeus_env == "production" else "sandbox",
            bookable=self.settings.amadeus_env == "production",
            notes=(
                "Amadeus production (live-ish)"
                if self.settings.amadeus_env == "production"
                else "Amadeus TEST env — sample/sandbox fares, not live market"
            ),
        )

    def _map_itinerary(self, itinerary: dict) -> list[Segment]:
        segs: list[Segment] = []
        for s in itinerary.get("segments") or []:
            dep = s.get("departure") or {}
            arr = s.get("arrival") or {}
            segs.append(
                Segment(
                    origin=dep.get("iataCode") or "?",
                    destination=arr.get("iataCode") or "?",
                    departure=_parse_dt(dep.get("at")),
                    arrival=_parse_dt(arr.get("at")),
                    airline=s.get("carrierCode"),
                    flight_number=f"{s.get('carrierCode', '')}{s.get('number', '')}".strip() or None,
                    duration_minutes=_parse_iso_duration(s.get("duration")),
                )
            )
        return segs


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_iso_duration(value: str | None) -> int | None:
    """Parse ISO-8601 duration like PT5H30M → minutes."""
    if not value or not value.startswith("P"):
        return None
    # strip leading P / PT
    t = value
    if "T" in t:
        t = t.split("T", 1)[1]
    else:
        t = t[1:]
    hours = minutes = 0
    num = ""
    for ch in t:
        if ch.isdigit():
            num += ch
        elif ch == "H" and num:
            hours = int(num)
            num = ""
        elif ch == "M" and num:
            minutes = int(num)
            num = ""
        else:
            num = ""
    return hours * 60 + minutes
