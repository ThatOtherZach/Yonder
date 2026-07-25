from __future__ import annotations

from typing import Any

import httpx

from yonder.config import Settings
from yonder.providers.base import FlightProvider
from yonder.quota import get_registry
from yonder.types import FlightOffer, SearchQuery


class AviationStackProvider(FlightProvider):
    """AviationStack — airport/geo enrichment on free tier.

    Free plans typically allow **/v1/airports** (and similar reference data) but
    NOT ticket prices or sometimes not /v1/flights. We therefore:

    - Never invent fares (`search` returns []).
    - Use airports lookup to validate IATA codes and enrich stopovers
      (city, country, name, timezone) for Adventure mode.

    Docs: https://aviationstack.com/documentation
    """

    name = "aviationstack"
    BASE = "http://api.aviationstack.com/v1"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.settings = settings
        self._airport_cache: dict[str, dict[str, Any]] = {}

    def is_configured(self) -> bool:
        return bool(self.settings.aviationstack_key)

    async def search(self, query: SearchQuery) -> list[FlightOffer]:
        # Not a fare API — do not burn quota on doomed /flights calls.
        return []

    async def lookup_airport(self, iata: str) -> dict[str, Any] | None:
        """Return airport metadata for an IATA code (cached). Free-tier friendly."""
        code = iata.upper().strip()
        if len(code) != 3:
            return None
        if code in self._airport_cache:
            return self._airport_cache[code]

        resp = await self.client.get(
            f"{self.BASE}/airports",
            params={
                "access_key": self.settings.aviationstack_key,
                "search": code,
                "limit": 10,
            },
            timeout=20.0,
        )
        reg = get_registry()
        body: dict = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            pass
        err = body.get("error") if isinstance(body, dict) else None
        if resp.status_code >= 400 or err:
            msg = ""
            if isinstance(err, dict):
                msg = err.get("info") or err.get("message") or str(err)
            reg.record_call(
                self.name,
                ok=False,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                error=msg or resp.text[:200],
                burned_quota=True,
            )
            return None

        reg.record_call(
            self.name,
            ok=True,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            burned_quota=True,
        )
        rows = body.get("data") or []
        match = None
        for row in rows:
            if str(row.get("iata_code") or "").upper() == code:
                match = row
                break
        if match is None and rows:
            match = rows[0]

        if match:
            info = {
                "iata": str(match.get("iata_code") or code).upper(),
                "name": match.get("airport_name") or match.get("name") or code,
                "city": match.get("city_iata_code")
                or match.get("city_name")
                or match.get("city")
                or "",
                "country": str(match.get("country_iso2") or match.get("country_code") or "").upper()
                or None,
                "country_name": match.get("country_name") or "",
                "timezone": match.get("timezone") or "",
                "latitude": match.get("latitude"),
                "longitude": match.get("longitude"),
            }
            # city_name field preferred when present
            if match.get("city_name"):
                info["city"] = match["city_name"]
            self._airport_cache[code] = info
            return info
        self._airport_cache[code] = {"iata": code, "name": code, "city": "", "country": None}
        return self._airport_cache[code]

    async def enrich_stopovers(
        self, ideas: list[Any]
    ) -> list[Any]:
        """Attach airport metadata onto StopoverIdea-like objects."""
        out = []
        for idea in ideas:
            code = getattr(idea, "iata", None) or (idea.get("iata") if isinstance(idea, dict) else None)
            if not code:
                out.append(idea)
                continue
            meta = await self.lookup_airport(str(code))
            if not meta:
                out.append(idea)
                continue
            updates = {}
            if meta.get("city") and not getattr(idea, "city", None):
                updates["city"] = meta["city"]
            elif meta.get("city") and meta["city"] != getattr(idea, "city", None):
                # keep Grok city if set; still store airport name in why
                pass
            if meta.get("country"):
                updates["country"] = meta["country"]
            # Append airport name into why for UI
            ap_name = meta.get("name") or ""
            why = getattr(idea, "why", "") or ""
            if ap_name and ap_name not in why:
                updates["why"] = (why + f" · Airport: {ap_name}").strip(" ·")
            if hasattr(idea, "model_copy"):
                out.append(idea.model_copy(update=updates) if updates else idea)
            else:
                out.append(idea)
        return out
