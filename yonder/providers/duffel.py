from __future__ import annotations

from datetime import datetime

import httpx

from yonder.config import Settings
from yonder.providers.base import FlightProvider
from yonder.quota import get_registry
from yonder.types import CabinClass, FlightOffer, SearchQuery, Segment

_CABIN = {
    CabinClass.ECONOMY: "economy",
    CabinClass.PREMIUM_ECONOMY: "premium_economy",
    CabinClass.BUSINESS: "business",
    CabinClass.FIRST: "first",
}


class DuffelProvider(FlightProvider):
    """Duffel Flights API — clean modern API, free sandbox token.

    https://duffel.com/docs/api/overview/welcome
    """

    name = "duffel"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.duffel_access_token)

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        slices = [
            {
                "origin": query.origin.upper(),
                "destination": query.destination.upper(),
                "departure_date": query.depart_date.isoformat(),
            }
        ]
        if query.return_date:
            slices.append(
                {
                    "origin": query.destination.upper(),
                    "destination": query.origin.upper(),
                    "departure_date": query.return_date.isoformat(),
                }
            )

        payload = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(query.adults)],
                "cabin_class": _CABIN[query.cabin],
            }
        }
        headers = {
            "Authorization": f"Bearer {self.settings.duffel_access_token}",
            "Duffel-Version": "v2",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self.client.post(
            "https://api.duffel.com/air/offer_requests",
            json=payload,
            headers=headers,
            params={"return_offers": "true"},
        )
        reg = get_registry()
        if resp.status_code >= 400:
            reg.record_call(
                self.name,
                ok=False,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                error=resp.text[:400],
            )
            raise RuntimeError(f"Duffel HTTP {resp.status_code}: {resp.text[:400]}")

        reg.record_call(
            self.name,
            ok=True,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            burned_quota=True,
        )
        body = resp.json().get("data") or {}
        offers_raw = body.get("offers") or []
        offers = [self._map(o, query) for o in offers_raw]
        offers.sort(key=lambda x: x.price)
        return offers[: query.max_results]

    def _map(self, item: dict, query: SearchQuery) -> FlightOffer:
        total = item.get("total_amount") or item.get("base_amount") or "0"
        currency = item.get("total_currency") or query.currency
        slices = item.get("slices") or []
        out_segs = self._map_slice(slices[0]) if slices else []
        ret_segs = self._map_slice(slices[1]) if len(slices) > 1 else []
        airlines = sorted(
            {
                s.airline
                for s in out_segs + ret_segs
                if s.airline
            }
        )
        is_test = (self.settings.duffel_access_token or "").startswith("duffel_test")
        return FlightOffer(
            provider=self.name,
            price=float(total),
            currency=currency,
            airlines=airlines,
            segments_out=out_segs,
            segments_return=ret_segs,
            stops_out=max(0, len(out_segs) - 1),
            stops_return=max(0, len(ret_segs) - 1) if ret_segs else None,
            duration_out_minutes=_minutes(slices[0].get("duration") if slices else None),
            duration_return_minutes=_minutes(slices[1].get("duration") if len(slices) > 1 else None),
            raw_id=item.get("id"),
            deep_link=None,
            price_kind="sandbox" if is_test else "live",
            bookable=not is_test,
            notes=(
                "SANDBOX test fare — not a real bookable market price (duffel_test token)"
                if is_test
                else "Duffel live offer"
            ),
        )

    def _map_slice(self, sl: dict) -> list[Segment]:
        segs: list[Segment] = []
        for s in sl.get("segments") or []:
            origin = (s.get("origin") or {}).get("iata_code") or "?"
            dest = (s.get("destination") or {}).get("iata_code") or "?"
            mkt = s.get("marketing_carrier") or {}
            segs.append(
                Segment(
                    origin=origin,
                    destination=dest,
                    departure=_parse_dt(s.get("departing_at")),
                    arrival=_parse_dt(s.get("arriving_at")),
                    airline=mkt.get("iata_code"),
                    flight_number=s.get("marketing_carrier_flight_number")
                    or (
                        f"{mkt.get('iata_code', '')}{s.get('marketing_carrier_flight_number', '')}".strip()
                        or None
                    ),
                    duration_minutes=_minutes(s.get("duration")),
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


def _minutes(iso_dur: str | None) -> int | None:
    if not iso_dur or not iso_dur.startswith("P"):
        return None
    t = iso_dur.split("T", 1)[1] if "T" in iso_dur else iso_dur[1:]
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
