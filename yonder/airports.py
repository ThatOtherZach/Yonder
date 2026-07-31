"""City-name → IATA resolution backed by the offline `airportsdata` dataset.

Extends the tiny hardcoded hint list in yonder.grok so the same-city guard in
the web handlers can resolve *any* clearly named city (e.g. "leave Berlin and
arrive in Tokyo") to distinct airports — no extra LLM tokens, no network.
"""

from __future__ import annotations

from functools import lru_cache

# Preferred airport for famous multi-airport cities (keeps parity with the
# existing hint list and picks the "expected" hub over alphabetical order).
_PREFERRED: dict[str, str] = {
    "tokyo": "NRT",
    "london": "LHR",
    "paris": "CDG",
    "new york": "JFK",
    "moscow": "SVO",
    "milan": "MXP",
    "rome": "FCO",
    "sao paulo": "GRU",
    "são paulo": "GRU",
    "buenos aires": "EZE",
    "shanghai": "PVG",
    "beijing": "PEK",
    "seoul": "ICN",
    "osaka": "KIX",
    "bangkok": "BKK",
    "jakarta": "CGK",
    "istanbul": "IST",
    "washington": "IAD",
    "chicago": "ORD",
    "houston": "IAH",
    "dallas": "DFW",
    "montreal": "YUL",
    "toronto": "YYZ",
    "stockholm": "ARN",
    "berlin": "BER",
    "dubai": "DXB",
    "johannesburg": "JNB",
    "rio de janeiro": "GIG",
    "tehran": "IKA",
    "taipei": "TPE",
    "kuala lumpur": "KUL",
    "mexico city": "MEX",
}


@lru_cache(maxsize=1)
def _city_index() -> dict[str, list[dict]]:
    """Lowercased city name → airport records, built once from airportsdata."""
    import airportsdata

    index: dict[str, list[dict]] = {}
    for rec in airportsdata.load("IATA").values():
        city = (rec.get("city") or "").strip().lower()
        if city:
            index.setdefault(city, []).append(rec)
    return index


def _rank(rec: dict) -> tuple:
    """Prefer likely-major airports: 'International' in name, then name/code."""
    name = (rec.get("name") or "").lower()
    return (
        0 if "international" in name else 1,
        0 if "regional" not in name and "municipal" not in name else 1,
        rec.get("iata") or "",
    )


def iata_for_city(name: str) -> str | None:
    """Best-effort city name → IATA code. None when the city is unknown."""
    city = (name or "").strip().lower()
    if not city:
        return None
    preferred = _PREFERRED.get(city)
    if preferred:
        return preferred
    recs = _city_index().get(city)
    if not recs:
        return None
    return sorted(recs, key=_rank)[0].get("iata") or None


@lru_cache(maxsize=512)
def city_country_for_iata(code: str) -> tuple[str, str] | None:
    """(city, ISO country) for an IATA code — None when unknown.

    Lets multi-airport metros collapse onto one city name (LGW/LHR → London),
    with the country available to guard against namesakes (London, Ontario).
    """
    c = (code or "").strip().upper()
    if len(c) != 3 or not c.isalpha():
        return None
    import airportsdata

    rec = airportsdata.load("IATA").get(c)
    if not rec:
        return None
    city = (rec.get("city") or "").strip()
    if not city:
        return None
    return city, (rec.get("country") or "").strip().upper()


def is_known_iata(code: str) -> bool:
    """True when the 3-letter code exists in the airport dataset."""
    c = (code or "").strip().upper()
    if len(c) != 3 or not c.isalpha():
        return False
    import airportsdata

    return c in airportsdata.load("IATA")
