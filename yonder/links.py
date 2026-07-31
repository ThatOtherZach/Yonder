"""Build booking deep links: Aviasales (affiliate) + Google Flights (fare API, kept)."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus, urlencode

from yonder.types import FlightOffer, SearchQuery

# ── Aviasales affiliate tracking ────────────────────────────────────────────
AVIASALES_MARKER = "756039.Zza75700ced74b488c8090948-756039"
AVIASALES_SUB_ID = "YonderFlights"

# IATA airline → public booking homepage (best-effort)
_AIRLINE_HOME: dict[str, str] = {
    "AC": "https://www.aircanada.com/",
    "WS": "https://www.westjet.com/",
    "F8": "https://www.flyflair.com/",
    "UA": "https://www.united.com/",
    "AA": "https://www.aa.com/",
    "DL": "https://www.delta.com/",
    "BA": "https://www.britishairways.com/",
    "LH": "https://www.lufthansa.com/",
    "AF": "https://www.airfrance.com/",
    "KL": "https://www.klm.com/",
    "EK": "https://www.emirates.com/",
    "QR": "https://www.qatarairways.com/",
    "TK": "https://www.turkishairlines.com/",
    "LX": "https://www.swiss.com/",
    "OS": "https://www.austrian.com/",
    "AY": "https://www.finnair.com/",
    "IB": "https://www.iberia.com/",
    "TP": "https://www.flytap.com/",
    "SQ": "https://www.singaporeair.com/",
    "NH": "https://www.ana.co.jp/en/us/",
    "JL": "https://www.jal.co.jp/en/",
    "CX": "https://www.cathaypacific.com/",
    "QF": "https://www.qantas.com/",
    "B6": "https://www.jetblue.com/",
    "AS": "https://www.alaskaair.com/",
    "F9": "https://www.flyfrontier.com/",
    "NK": "https://www.spirit.com/",
    "WN": "https://www.southwest.com/",
    "PD": "https://www.flyporter.com/",
    "TS": "https://www.airtransat.com/",
    "EI": "https://www.aerlingus.com/",
    "DE": "https://www.condor.com/",
    "FR": "https://www.ryanair.com/",
    "U2": "https://www.easyjet.com/",
    "DY": "https://www.norwegian.com/",
    "SK": "https://www.sas.se/",
    "AZ": "https://www.itaspa.com/",
    "VS": "https://www.virginatlantic.com/",
    "HA": "https://www.hawaiianairlines.com/",
    "AM": "https://www.aeromexico.com/",
    "CM": "https://www.copaair.com/",
    "LA": "https://www.latam.com/",
    "AV": "https://www.avianca.com/",
    "EY": "https://www.etihad.com/",
    "SV": "https://www.saudia.com/",
    "MS": "https://www.egyptair.com/",
    "ET": "https://www.ethiopianairlines.com/",
    "SA": "https://www.flysaa.com/",
    "KE": "https://www.koreanair.com/",
    "OZ": "https://flyasiana.com/",
    "MU": "https://www.ceair.com/",
    "CA": "https://www.airchina.com/",
    "CZ": "https://www.csair.com/",
    "GA": "https://www.garuda-indonesia.com/",
    "MH": "https://www.malaysiaairlines.com/",
    "PR": "https://www.philippineairlines.com/",
    "VN": "https://www.vietnamairlines.com/",
    "TG": "https://www.thaiairways.com/",
    "AI": "https://www.airindia.com/",
    "NZ": "https://www.airnewzealand.com/",
    "FI": "https://www.icelandair.com/",
    "LO": "https://www.lot.com/",
    "OK": "https://www.csa.cz/",
    "RO": "https://www.tarom.ro/",
    "A3": "https://en.aegeanair.com/",
    "PC": "https://www.flypgs.com/",
    "VY": "https://www.vueling.com/",
    "W6": "https://www.wizzair.com/",
}

# SerpAPI / Google often return full names, not IATA codes
_AIRLINE_NAMES: dict[str, str] = {
    "AIR CANADA": "AC",
    "WESTJET": "WS",
    "WEST JET": "WS",
    "FLAIR": "F8",
    "FLAIR AIRLINES": "F8",
    "UNITED": "UA",
    "UNITED AIRLINES": "UA",
    "AMERICAN": "AA",
    "AMERICAN AIRLINES": "AA",
    "DELTA": "DL",
    "DELTA AIR LINES": "DL",
    "BRITISH AIRWAYS": "BA",
    "LUFTHANSA": "LH",
    "AIR FRANCE": "AF",
    "KLM": "KL",
    "KLM ROYAL DUTCH AIRLINES": "KL",
    "EMIRATES": "EK",
    "QATAR AIRWAYS": "QR",
    "QATAR": "QR",
    "TURKISH AIRLINES": "TK",
    "TURKISH": "TK",
    "SWISS": "LX",
    "SWISS INTERNATIONAL AIR LINES": "LX",
    "AUSTRIAN": "OS",
    "AUSTRIAN AIRLINES": "OS",
    "FINNAIR": "AY",
    "IBERIA": "IB",
    "TAP": "TP",
    "TAP AIR PORTUGAL": "TP",
    "SINGAPORE AIRLINES": "SQ",
    "ANA": "NH",
    "ALL NIPPON AIRWAYS": "NH",
    "JAL": "JL",
    "JAPAN AIRLINES": "JL",
    "CATHAY PACIFIC": "CX",
    "QANTAS": "QF",
    "JETBLUE": "B6",
    "ALASKA": "AS",
    "ALASKA AIRLINES": "AS",
    "FRONTIER": "F9",
    "SPIRIT": "NK",
    "SOUTHWEST": "WN",
    "PORTER": "PD",
    "PORTER AIRLINES": "PD",
    "AIR TRANSAT": "TS",
    "AER LINGUS": "EI",
    "CONDOR": "DE",
    "RYANAIR": "FR",
    "EASYJET": "U2",
    "NORWEGIAN": "DY",
    "SAS": "SK",
    "SCANDINAVIAN": "SK",
    "ITA": "AZ",
    "ITA AIRWAYS": "AZ",
    "VIRGIN ATLANTIC": "VS",
    "HAWAIIAN": "HA",
    "AEROMEXICO": "AM",
    "COPA": "CM",
    "LATAM": "LA",
    "AVIANCA": "AV",
    "ETIHAD": "EY",
    "SAUDIA": "SV",
    "EGYPTAIR": "MS",
    "ETHIOPIAN": "ET",
    "SOUTH AFRICAN": "SA",
    "KOREAN AIR": "KE",
    "ASIANA": "OZ",
    "ICELANDAIR": "FI",
    "LOT": "LO",
    "LOT POLISH": "LO",
    "AEGEAN": "A3",
    "PEGASUS": "PC",
    "VUELING": "VY",
    "WIZZ": "W6",
    "WIZZ AIR": "W6",
}

_GL_BY_CURRENCY = {
    "CAD": "ca",
    "USD": "us",
    "EUR": "de",
    "GBP": "uk",
    "AUD": "au",
    "MXN": "mx",
}


def _ddmm(d: date) -> str:
    """Two-digit day + two-digit month: Aug 18 → '1808'."""
    return f"{d.day:02d}{d.month:02d}"


def aviasales_url(
    origin: str,
    destination: str,
    depart: date,
    *,
    return_date: date | None = None,
    adults: int = 1,
) -> str:
    """Aviasales affiliate search link with tracking marker.

    One-way:    /search/{ORIG}{DDMM}{DEST}{PAX}?marker=...
    Round-trip: /search/{ORIG}{DDMM}{DEST}{DDMM}{PAX}?marker=...
    """
    o = origin.upper().strip()
    d = destination.upper().strip()
    if not o or not d:
        return aviasales_fallback_url(o or None, adults)
    path = o + _ddmm(depart) + d
    if return_date:
        path += _ddmm(return_date)
    path += str(adults)
    qs = f"marker={AVIASALES_MARKER}&sub_id={AVIASALES_SUB_ID}"
    return f"https://www.aviasales.com/search/{path}?{qs}"


def aviasales_fallback_url(origin: str | None = None, adults: int = 1) -> str:
    """Fallback Aviasales link when full search data is missing.

    With origin:    /?marker=...&params={ORIG}{PAX}
    Without origin: /?marker=...
    """
    qs = f"marker={AVIASALES_MARKER}&sub_id={AVIASALES_SUB_ID}"
    if origin:
        o = origin.upper().strip()
        return f"https://www.aviasales.com/?{qs}&params={o}{adults}"
    return f"https://www.aviasales.com/?{qs}"


def google_flights_url(
    origin: str,
    destination: str,
    depart: date,
    *,
    return_date: date | None = None,
    currency: str = "CAD",
    adults: int = 1,
    hl: str = "en",
) -> str:
    """Prefill Google Flights via natural-language `q=` (works without tfs protobuf).

    Format proven in the wild:
      Flights to DEST from ORIGIN on YYYY-MM-DD oneway
      Flights to DEST from ORIGIN on YYYY-MM-DD through YYYY-MM-DD
    """
    o, d = origin.upper().strip(), destination.upper().strip()
    dep = depart.isoformat()
    cur = (currency or "CAD").upper()
    gl = _GL_BY_CURRENCY.get(cur, "us")

    if return_date:
        q = f"Flights to {d} from {o} on {dep} through {return_date.isoformat()}"
    else:
        q = f"Flights to {d} from {o} on {dep} oneway"

    if adults > 1:
        q += f" with {adults} adults"

    params = {
        "hl": hl,
        "gl": gl,
        "curr": cur,
        "q": q,
    }
    return "https://www.google.com/travel/flights?" + urlencode(
        params, quote_via=quote_plus
    )


def google_flights_multi(
    legs: list[tuple[str, str, date]],
    *,
    currency: str = "CAD",
    hl: str = "en",
) -> str:
    """Multi-city style query string for Google Flights."""
    if not legs:
        return "https://www.google.com/travel/flights"
    if len(legs) == 1:
        a, b, day = legs[0]
        return google_flights_url(a, b, day, currency=currency, hl=hl)

    # "Flights from A to B on DATE then B to C on DATE2"
    chunks = []
    for i, (o, d, day) in enumerate(legs):
        if i == 0:
            chunks.append(f"from {o.upper()} to {d.upper()} on {day.isoformat()}")
        else:
            chunks.append(f"then {o.upper()} to {d.upper()} on {day.isoformat()}")
    q = "Flights " + " ".join(chunks) + " multicity"
    cur = (currency or "CAD").upper()
    gl = _GL_BY_CURRENCY.get(cur, "us")
    params = {"hl": hl, "gl": gl, "curr": cur, "q": q}
    return "https://www.google.com/travel/flights?" + urlencode(
        params, quote_via=quote_plus
    )


def kayak_url(
    origin: str,
    destination: str,
    depart: date,
    *,
    return_date: date | None = None,
) -> str:
    """Kayak deep link — reliably opens prefilled search (good Google fallback)."""
    path = f"{origin.upper()}-{destination.upper()}/{depart.isoformat()}"
    if return_date:
        path += f"/{return_date.isoformat()}"
    return f"https://www.kayak.com/flights/{path}"


def provider_booking_home(provider: str) -> str | None:
    homes = {
        "duffel": "https://duffel.com/",
        "serpapi_google_flights": "https://www.google.com/travel/flights",
        "travelpayouts": "https://www.aviasales.com/",
        "amadeus": "https://www.amadeus.com/",
        "aviationstack": None,
        "mock": None,
    }
    return homes.get(provider)


def _normalize_airline_token(raw: str) -> str | None:
    """Map code or full name → 2-letter IATA when known."""
    c = (raw or "").strip().upper()
    if not c:
        return None
    # "WS 3572" / "AC123"
    if len(c) >= 2 and c[:2].isalpha() and (len(c) == 2 or not c[2].isalpha()):
        if c[:2] in _AIRLINE_HOME:
            return c[:2]
    # full name exact
    if c in _AIRLINE_NAMES:
        return _AIRLINE_NAMES[c]
    # fuzzy: longer names only (avoid "ZZ" matching "WIZZ")
    if len(c) >= 3:
        for name, code in _AIRLINE_NAMES.items():
            if name in c or (len(name) >= 4 and c in name):
                return code
    return None


# IATA → short display name for UI link labels
_IATA_DISPLAY: dict[str, str] = {
    "AC": "Air Canada",
    "WS": "WestJet",
    "F8": "Flair",
    "UA": "United",
    "AA": "American",
    "DL": "Delta",
    "BA": "British Airways",
    "LH": "Lufthansa",
    "AF": "Air France",
    "KL": "KLM",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "TK": "Turkish Airlines",
    "LX": "SWISS",
    "OS": "Austrian",
    "AY": "Finnair",
    "IB": "Iberia",
    "TP": "TAP",
    "SQ": "Singapore Airlines",
    "NH": "ANA",
    "JL": "Japan Airlines",
    "CX": "Cathay Pacific",
    "QF": "Qantas",
    "B6": "JetBlue",
    "AS": "Alaska",
    "F9": "Frontier",
    "NK": "Spirit",
    "WN": "Southwest",
    "PD": "Porter",
    "TS": "Air Transat",
    "EI": "Aer Lingus",
    "DE": "Condor",
    "FR": "Ryanair",
    "U2": "easyJet",
    "DY": "Norwegian",
    "SK": "SAS",
    "AZ": "ITA",
    "VS": "Virgin Atlantic",
    "HA": "Hawaiian",
    "AM": "Aeromexico",
    "CM": "Copa",
    "LA": "LATAM",
    "AV": "Avianca",
    "EY": "Etihad",
    "SV": "Saudia",
    "MS": "EgyptAir",
    "ET": "Ethiopian",
    "SA": "South African",
    "KE": "Korean Air",
    "OZ": "Asiana",
    "MU": "China Eastern",
    "CA": "Air China",
    "CZ": "China Southern",
    "GA": "Garuda",
    "MH": "Malaysia Airlines",
    "PR": "Philippine Airlines",
    "VN": "Vietnam Airlines",
    "TG": "Thai Airways",
    "AI": "Air India",
    "NZ": "Air New Zealand",
    "FI": "Icelandair",
    "LO": "LOT",
    "OK": "Czech Airlines",
    "RO": "TAROM",
    "A3": "Aegean",
    "PC": "Pegasus",
    "VY": "Vueling",
    "W6": "Wizz Air",
}


def airline_home(airline_codes: list[str] | None) -> str | None:
    """Real airline homepage only — never Kayak."""
    if not airline_codes:
        return None
    for raw in airline_codes:
        code = _normalize_airline_token(str(raw))
        if code and code in _AIRLINE_HOME:
            return _AIRLINE_HOME[code]
    return None


def airline_display_name(airline_codes: list[str] | None) -> str | None:
    """Human label for the operating/marketing airline (first known)."""
    if not airline_codes:
        return None
    for raw in airline_codes:
        s = str(raw or "").strip()
        if not s:
            continue
        code = _normalize_airline_token(s)
        if code and code in _IATA_DISPLAY:
            return _IATA_DISPLAY[code]
        # Already a full name from SerpAPI / Google
        if len(s) > 3 and not (len(s) == 2 and s.isalpha()):
            # Drop flight-number suffix "WS 3572"
            parts = s.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                s = " ".join(parts[:-1])
            if len(s) > 2:
                return s.title() if s.isupper() else s
        if code:
            return code
    return None


def airline_site_label(airline_codes: list[str] | None) -> str:
    """Button text: 'SWISS site' / 'Turkish Airlines site' / fallback."""
    name = airline_display_name(airline_codes)
    if name:
        return f"{name} site"
    return "Airline site"


def _is_http_url(value: str | None) -> bool:
    return bool(value and (value.startswith("http://") or value.startswith("https://")))


def attach_links_to_offer(offer: FlightOffer, query: SearchQuery) -> FlightOffer:
    """Aviasales affiliate link primary. deep_link = real airline site only (not Kayak).

    Note: the dict key 'google_flights_url' is preserved (persisted in DB / types)
    but its value is now an Aviasales affiliate URL, not a Google Flights URL.
    """
    # google_flights_url key intentionally carries an Aviasales URL — see links.py header
    gurl = aviasales_url(
        query.origin,
        query.destination,
        query.depart_date,
        return_date=query.return_date,
        adults=query.adults,
    )
    # Keep existing HTTP deep_link only if it isn't a kayak placeholder
    existing = offer.deep_link if _is_http_url(offer.deep_link) else None
    if existing and "kayak.com" in existing:
        existing = None
    airline_link = existing or airline_home(offer.airlines)

    return offer.model_copy(
        update={
            "google_flights_url": gurl,
            "booking_url": gurl,
            "deep_link": airline_link,  # airline only; null if unknown
            "notes": offer.notes,
        }
    )


def attach_links_from_leg(
    offer: FlightOffer,
    *,
    origin: str,
    destination: str,
    depart: date,
    currency: str = "CAD",
    adults: int = 1,
) -> FlightOffer:
    """Per-leg links: Aviasales affiliate URL stored; deep_link = airline homepage only.

    Note: the dict key 'google_flights_url' is preserved (persisted in DB / types)
    but its value is now an Aviasales affiliate URL.
    """
    # google_flights_url key intentionally carries an Aviasales URL — see links.py header
    gurl = aviasales_url(origin, destination, depart, adults=adults)
    existing = offer.deep_link if _is_http_url(offer.deep_link) else None
    if existing and "kayak.com" in existing:
        existing = None
    airline_link = existing or airline_home(offer.airlines)
    return offer.model_copy(
        update={
            "google_flights_url": gurl,
            "booking_url": gurl,
            "deep_link": airline_link,
        }
    )
