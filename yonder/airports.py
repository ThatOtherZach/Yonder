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


# Common English abbreviations that happen to collide with IATA codes in the
# airportsdata dataset.  We exclude these from the bare-uppercase fallback so
# that Grok replies like "The ETA is unknown, no FAQ available" don't seed a
# wrong flight-search destination.
_COMMON_ABBR: frozenset[str] = frozenset(
    {
        # Time / scheduling
        "ETA", "ETD", "ETE",
        # Business / finance / economics
        "CEO", "CFO", "COO", "CTO", "CPO", "CSO",
        "GDP", "GNP", "CPI", "PPP",
        "VAT", "GST", "TAX",
        "MBA", "PHD",
        # Technology / internet
        "API", "SDK", "URL", "URI", "GPS", "LTE",
        "ATM", "PIN",
        # General English
        "FAQ", "TBD", "TBA", "TBC", "ETA", "FYI", "BTW",
        "USA", "UK",
        "DNA", "RNA",
        "NGO", "NPO",
        "IOU",
        "SOS",
    }
)


# Airline brand names / well-known carrier acronyms that are ALSO valid IATA
# airport codes in the airportsdata dataset.  A Grok reply like "Book with KLM
# for the best deal" must not seed KLM (Kalskag, Alaska) — or any other
# airline-name collision — as the flight-search destination.
_AIRLINE_NAME_CODES: frozenset[str] = frozenset(
    {
        "KLM",  # KLM Royal Dutch Airlines — Kalskag, AK
        "SAS",  # Scandinavian Airlines — Salton City, CA
        "TAM",  # TAM / LATAM Brasil — Tampere, Finland
        "GOL",  # GOL Linhas Aéreas — Gol, Norway
        "ANA",  # All Nippon Airways — Anaheim, CA (rail code region)
        "LOT",  # LOT Polish Airlines
        "TAP",  # TAP Air Portugal — Tapachula, Mexico
        "JAL",  # Japan Airlines
        "UPS",  # UPS Airlines (cargo)
    }
)


def is_known_iata(code: str) -> bool:
    """True when the 3-letter code exists in the airport dataset.

    Codes in *_COMMON_ABBR* are rejected even if they happen to match an
    airport code, to avoid false positives when Grok sprinkles common
    English abbreviations (FAQ, GDP, API …) through its prose.
    """
    c = (code or "").strip().upper()
    if len(c) != 3 or not c.isalpha():
        return False
    if c in _COMMON_ABBR or c in _AIRLINE_NAME_CODES:
        return False
    import airportsdata

    return c in airportsdata.load("IATA")
