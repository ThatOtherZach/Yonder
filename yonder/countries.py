"""Country helpers for avoid-list + stopover filtering."""

from __future__ import annotations

# Common travel countries (ISO 3166-1 alpha-2 → name). Cap selection at 10 in UI.
COUNTRIES: list[tuple[str, str]] = [
    ("AF", "Afghanistan"),
    ("AL", "Albania"),
    ("DZ", "Algeria"),
    ("AR", "Argentina"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("BR", "Brazil"),
    ("BG", "Bulgaria"),
    ("KH", "Cambodia"),
    ("CA", "Canada"),
    ("CL", "Chile"),
    ("CN", "China"),
    ("CO", "Colombia"),
    ("HR", "Croatia"),
    ("CU", "Cuba"),
    ("CY", "Cyprus"),
    ("CZ", "Czechia"),
    ("DK", "Denmark"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("EE", "Estonia"),
    ("ET", "Ethiopia"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("DE", "Germany"),
    ("GH", "Ghana"),
    ("GR", "Greece"),
    ("HK", "Hong Kong"),
    ("HU", "Hungary"),
    ("IS", "Iceland"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IR", "Iran"),
    ("IQ", "Iraq"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JM", "Jamaica"),
    ("JP", "Japan"),
    ("JO", "Jordan"),
    ("KE", "Kenya"),
    ("KR", "South Korea"),
    ("KW", "Kuwait"),
    ("LV", "Latvia"),
    ("LB", "Lebanon"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("MY", "Malaysia"),
    ("MV", "Maldives"),
    ("MX", "Mexico"),
    ("MA", "Morocco"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NG", "Nigeria"),
    ("NO", "Norway"),
    ("OM", "Oman"),
    ("PK", "Pakistan"),
    ("PA", "Panama"),
    ("PE", "Peru"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("QA", "Qatar"),
    ("RO", "Romania"),
    ("RU", "Russia"),
    ("SA", "Saudi Arabia"),
    ("RS", "Serbia"),
    ("SG", "Singapore"),
    ("SK", "Slovakia"),
    ("SI", "Slovenia"),
    ("ZA", "South Africa"),
    ("ES", "Spain"),
    ("LK", "Sri Lanka"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("TW", "Taiwan"),
    ("TH", "Thailand"),
    ("TR", "Turkey"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("US", "United States"),
    ("UA", "Ukraine"),
    ("VN", "Vietnam"),
]

COUNTRY_NAME = {code: name for code, name in COUNTRIES}

# Airport / metro IATA → country (for seed stopovers + light filtering)
IATA_COUNTRY: dict[str, str] = {
    "ZRH": "CH",
    "GVA": "CH",
    "IST": "TR",
    "SAW": "TR",
    "LIS": "PT",
    "OPO": "PT",
    "KEF": "IS",
    "DOH": "QA",
    "AMS": "NL",
    "CDG": "FR",
    "ORY": "FR",
    "MEX": "MX",
    "CUN": "MX",
    "YUL": "CA",
    "YYC": "CA",
    "YYZ": "CA",
    "YVR": "CA",
    "YOW": "CA",
    "YEG": "CA",
    "YHZ": "CA",
    "YWG": "CA",
    "LAX": "US",
    "JFK": "US",
    "EWR": "US",
    "ORD": "US",
    "SFO": "US",
    "MIA": "US",
    "SEA": "US",
    "BOS": "US",
    "DFW": "US",
    "ATL": "US",
    "DEN": "US",
    "IAD": "US",
    "NRT": "JP",
    "HND": "JP",
    "ICN": "KR",
    "DXB": "AE",
    "AUH": "AE",
    "BCN": "ES",
    "MAD": "ES",
    "LHR": "GB",
    "LGW": "GB",
    "STN": "GB",
    "MAN": "GB",
    "FRA": "DE",
    "MUC": "DE",
    "BER": "DE",
    "FCO": "IT",
    "MXP": "IT",
    "VCE": "IT",
    "VIE": "AT",
    "CPH": "DK",
    "ARN": "SE",
    "OSL": "NO",
    "HEL": "FI",
    "DUB": "IE",
    "SIN": "SG",
    "BKK": "TH",
    "HKG": "HK",
    "PVG": "CN",
    "PEK": "CN",
    "DEL": "IN",
    "BOM": "IN",
    "SYD": "AU",
    "MEL": "AU",
    "GRU": "BR",
    "EZE": "AR",
    "BOG": "CO",
    "LIM": "PE",
    "SCL": "CL",
    "CAI": "EG",
    "JNB": "ZA",
    "NBO": "KE",
    "RUH": "SA",
    "KWI": "KW",
    "TLV": "IL",
    "WAW": "PL",
    "PRG": "CZ",
    "BUD": "HU",
    "ATH": "GR",
    "SVO": "RU",
    "DME": "RU",
    "BRU": "BE",
    "LIS": "PT",
    "ALC": "ES",
    "PMI": "ES",
    "NCE": "FR",
    "LYS": "FR",
    "EDI": "GB",
    "YYT": "CA",
}

# City label for display: "Toronto (YYZ)"
IATA_CITY: dict[str, str] = {
    "YYZ": "Toronto",
    "YTZ": "Toronto",
    "YVR": "Vancouver",
    "YUL": "Montreal",
    "YYC": "Calgary",
    "YOW": "Ottawa",
    "YEG": "Edmonton",
    "YHZ": "Halifax",
    "YWG": "Winnipeg",
    "YYT": "St. John's",
    "LAX": "Los Angeles",
    "JFK": "New York",
    "EWR": "Newark",
    "LGA": "New York",
    "ORD": "Chicago",
    "SFO": "San Francisco",
    "SJC": "San Jose",
    "MIA": "Miami",
    "SEA": "Seattle",
    "BOS": "Boston",
    "DFW": "Dallas",
    "ATL": "Atlanta",
    "DEN": "Denver",
    "IAD": "Washington",
    "DCA": "Washington",
    "PHX": "Phoenix",
    "LAS": "Las Vegas",
    "MEX": "Mexico City",
    "CUN": "Cancún",
    "ZRH": "Zurich",
    "GVA": "Geneva",
    "IST": "Istanbul",
    "SAW": "Istanbul",
    "LIS": "Lisbon",
    "OPO": "Porto",
    "KEF": "Reykjavik",
    "DOH": "Doha",
    "AMS": "Amsterdam",
    "CDG": "Paris",
    "ORY": "Paris",
    "NCE": "Nice",
    "LYS": "Lyon",
    "NRT": "Tokyo",
    "HND": "Tokyo",
    "ICN": "Seoul",
    "DXB": "Dubai",
    "AUH": "Abu Dhabi",
    "BCN": "Barcelona",
    "MAD": "Madrid",
    "ALC": "Alicante",
    "PMI": "Palma",
    "LHR": "London",
    "LGW": "London",
    "STN": "London",
    "MAN": "Manchester",
    "EDI": "Edinburgh",
    "FRA": "Frankfurt",
    "MUC": "Munich",
    "BER": "Berlin",
    "FCO": "Rome",
    "MXP": "Milan",
    "VCE": "Venice",
    "VIE": "Vienna",
    "CPH": "Copenhagen",
    "ARN": "Stockholm",
    "OSL": "Oslo",
    "HEL": "Helsinki",
    "DUB": "Dublin",
    "SIN": "Singapore",
    "BKK": "Bangkok",
    "HKG": "Hong Kong",
    "PVG": "Shanghai",
    "PEK": "Beijing",
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "SYD": "Sydney",
    "MEL": "Melbourne",
    "GRU": "São Paulo",
    "EZE": "Buenos Aires",
    "BOG": "Bogotá",
    "LIM": "Lima",
    "SCL": "Santiago",
    "CAI": "Cairo",
    "JNB": "Johannesburg",
    "NBO": "Nairobi",
    "RUH": "Riyadh",
    "KWI": "Kuwait City",
    "TLV": "Tel Aviv",
    "WAW": "Warsaw",
    "PRG": "Prague",
    "BUD": "Budapest",
    "ATH": "Athens",
    "SVO": "Moscow",
    "DME": "Moscow",
    "BRU": "Brussels",
    "AKL": "Auckland",
    "CPT": "Cape Town",
    "MNL": "Manila",
    "KUL": "Kuala Lumpur",
    "CGK": "Jakarta",
    "SGN": "Ho Chi Minh City",
    "HAN": "Hanoi",
}


def city_for_iata(iata: str | None) -> str | None:
    if not iata:
        return None
    return IATA_CITY.get(iata.strip().upper())


def format_place(iata: str | None, city_hint: str | None = None) -> str:
    """Human place label: 'Toronto (YYZ)'. Falls back to code alone if unknown."""
    if not iata:
        return city_hint or "?"
    code = iata.strip().upper()
    if len(code) != 3:
        return city_hint or code
    city = (city_hint or "").strip() or city_for_iata(code)
    if city:
        # Avoid "Toronto (Toronto)" if someone passed a code as city
        if city.upper() == code:
            return code
        return f"{city} ({code})"
    return code


def format_route(
    origin: str | None,
    destination: str | None,
    *,
    origin_city: str | None = None,
    dest_city: str | None = None,
    sep: str = " → ",
) -> str:
    return (
        f"{format_place(origin, origin_city)}"
        f"{sep}"
        f"{format_place(destination, dest_city)}"
    )


def normalize_country_list(
    codes: list[str] | str | None, *, max_n: int = 250
) -> list[str]:
    """ISO2 codes, de-duplicated, capped. Used for visited (high cap) and avoid."""
    if codes is None:
        return []
    if isinstance(codes, str):
        parts = [p.strip().upper() for p in codes.replace(";", ",").split(",")]
    else:
        parts = [str(p).strip().upper() for p in codes]
    out: list[str] = []
    for p in parts:
        if len(p) == 2 and p.isalpha() and p not in out:
            out.append(p)
        if len(out) >= max_n:
            break
    return out


def normalize_avoid_list(codes: list[str] | str | None, *, max_n: int = 10) -> list[str]:
    return normalize_country_list(codes, max_n=max_n)


def country_for_iata(iata: str) -> str | None:
    return IATA_COUNTRY.get(iata.upper())


# Preferred main airport per ISO2 (home / default origin resolution)
COUNTRY_PRIMARY_IATA: dict[str, str] = {
    "CA": "YVR",
    "US": "JFK",
    "GB": "LHR",
    "FR": "CDG",
    "DE": "FRA",
    "NL": "AMS",
    "ES": "MAD",
    "IT": "FCO",
    "PT": "LIS",
    "IE": "DUB",
    "CH": "ZRH",
    "AT": "VIE",
    "BE": "BRU",
    "SE": "ARN",
    "NO": "OSL",
    "DK": "CPH",
    "FI": "HEL",
    "PL": "WAW",
    "CZ": "PRG",
    "HU": "BUD",
    "GR": "ATH",
    "TR": "IST",
    "JP": "NRT",
    "KR": "ICN",
    "CN": "PVG",
    "HK": "HKG",
    "TW": "TPE",
    "SG": "SIN",
    "TH": "BKK",
    "VN": "HAN",
    "MY": "KUL",
    "ID": "CGK",
    "PH": "MNL",
    "IN": "DEL",
    "AU": "SYD",
    "NZ": "AKL",
    "MX": "MEX",
    "BR": "GRU",
    "AR": "EZE",
    "CL": "SCL",
    "PE": "LIM",
    "CO": "BOG",
    "AE": "DXB",
    "QA": "DOH",
    "SA": "RUH",
    "IL": "TLV",
    "EG": "CAI",
    "ZA": "JNB",
    "MA": "CMN",
    "IS": "KEF",
    "RU": "SVO",
}


def primary_iata_for_country(cc: str | None) -> str | None:
    """Best-effort main airport for an ISO2 country code."""
    if not cc:
        return None
    code = cc.strip().upper()
    if len(code) != 2:
        return None
    hit = COUNTRY_PRIMARY_IATA.get(code)
    if hit:
        return hit
    # Fallback: first known IATA in our table for that country
    for iata, c in IATA_COUNTRY.items():
        if c == code:
            return iata
    return None


# Display currency → likely home country (for origin fallback)
CURRENCY_HOME_COUNTRY: dict[str, str] = {
    "USD": "US",
    "CAD": "CA",
    "EUR": "DE",
    "GBP": "GB",
    "AUD": "AU",
    "NZD": "NZ",
    "JPY": "JP",
    "CHF": "CH",
    "MXN": "MX",
    "SGD": "SG",
    "HKD": "HK",
    "INR": "IN",
    "BRL": "BR",
    "KRW": "KR",
    "SEK": "SE",
    "NOK": "NO",
    "DKK": "DK",
    "PLN": "PL",
    "CZK": "CZ",
    "HUF": "HU",
    "TRY": "TR",
    "THB": "TH",
    "MYR": "MY",
    "IDR": "ID",
    "PHP": "PH",
    "VND": "VN",
    "ZAR": "ZA",
    "AED": "AE",
    "QAR": "QA",
    "SAR": "SA",
    "ILS": "IL",
    "EGP": "EG",
    "CLP": "CL",
    "PEN": "PE",
    "COP": "CO",
    "ARS": "AR",
}


def country_for_currency(currency: str | None) -> str | None:
    """ISO2 country associated with a display currency, if known."""
    if not currency:
        return None
    return CURRENCY_HOME_COUNTRY.get(currency.strip().upper())


def is_avoided_iata(iata: str, avoid: list[str]) -> bool:
    if not avoid:
        return False
    cc = country_for_iata(iata)
    return bool(cc and cc.upper() in {a.upper() for a in avoid})


def country_label(code: str) -> str:
    return COUNTRY_NAME.get(code.upper(), code.upper())
