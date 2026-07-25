from __future__ import annotations

import httpx

from yonder.config import Settings
from yonder.providers.amadeus import AmadeusProvider
from yonder.providers.aviationstack import AviationStackProvider
from yonder.providers.base import FlightProvider
from yonder.providers.duffel import DuffelProvider
from yonder.providers.mock import MockProvider
from yonder.providers.serpapi_google import SerpApiGoogleFlightsProvider
from yonder.providers.travelpayouts import TravelpayoutsProvider


def build_providers(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    include_mock: bool = False,
    only: list[str] | None = None,
) -> list[FlightProvider]:
    all_providers: list[FlightProvider] = [
        AmadeusProvider(settings, client),
        TravelpayoutsProvider(settings, client),
        DuffelProvider(settings, client),
        SerpApiGoogleFlightsProvider(settings, client),
        AviationStackProvider(settings, client),
    ]
    if include_mock:
        all_providers.append(MockProvider(client))

    if only:
        wanted = {n.lower() for n in only}
        all_providers = [p for p in all_providers if p.name in wanted]

    return [
        p
        for p in all_providers
        if p.is_configured() or (include_mock and p.name == "mock")
    ]
