"""Tiled world map: square-kilometres-unlocked progression.

The world is carved into "tiles":

* Continent-scale countries — US, CA, MX, BR, AU — are subdivided into
  their ISO 3166-2 first-level regions (states / provinces / territories);
  the UK is split into its constituent countries (GB-ENG / GB-SCT /
  GB-WLS / GB-NIR).  Each subdivision is its own tile.
* Every other country is a single tile (its plain ISO2 code).

Visiting a tile credits its full land area (km²) toward the traveller's
total.  Areas for subdivisions are computed from Natural Earth
public-domain boundary geometry (approximate, spherical); areas for
single-tile countries come from yonder.country_size.COUNTRY_SIZE.

Partial-coverage rule (documented decision):
* A **country-level** entry for a subdivided country (e.g. plain "CA")
  means "some coverage": it credits ONE average region's worth of area
  (country total ÷ number of regions), not the whole country.
* Legacy visited-country lists migrate as country-level entries — a
  visited "CA" stays "CA" (partial credit), it is NOT expanded to all
  13 provinces.
* A subdivided country counts as **fully visited** (for the getaway
  visited filter) only when ALL of its subdivision tiles are marked.
  A non-subdivided country is fully visited when its tile is marked.
"""
from __future__ import annotations

import re

# ISO 3166-2 subdivision tiles: code -> (display name, land area km²)
SUBDIVISION_TILES: dict[str, tuple[str, int]] = {
    # AU
    "AU-ACT": ("Australian Capital Territory", 2349),
    "AU-NSW": ("New South Wales", 802498),
    "AU-NT": ("Northern Territory", 1352471),
    "AU-QLD": ("Queensland", 1734426),
    "AU-SA": ("South Australia", 984968),
    "AU-TAS": ("Tasmania", 68013),
    "AU-VIC": ("Victoria", 227867),
    "AU-WA": ("Western Australia", 2533138),
    # BR
    "BR-AC": ("Acre", 154662),
    "BR-AL": ("Alagoas", 27733),
    "BR-AM": ("Amazonas", 1572228),
    "BR-AP": ("Amapá", 139804),
    "BR-BA": ("Bahia", 562204),
    "BR-CE": ("Ceará", 150885),
    "BR-DF": ("Distrito Federal", 5812),
    "BR-ES": ("Espírito Santo", 45944),
    "BR-GO": ("Goiás", 342427),
    "BR-MA": ("Maranhão", 327561),
    "BR-MG": ("Minas Gerais", 588210),
    "BR-MS": ("Mato Grosso do Sul", 357060),
    "BR-MT": ("Mato Grosso", 906626),
    "BR-PA": ("Pará", 1236106),
    "BR-PB": ("Paraíba", 56758),
    "BR-PE": ("Pernambuco", 98041),
    "BR-PI": ("Piauí", 253083),
    "BR-PR": ("Paraná", 199094),
    "BR-RJ": ("Rio de Janeiro", 44091),
    "BR-RN": ("Rio Grande do Norte", 53042),
    "BR-RO": ("Rondônia", 238899),
    "BR-RR": ("Roraima", 225750),
    "BR-RS": ("Rio Grande do Sul", 272601),
    "BR-SC": ("Santa Catarina", 95255),
    "BR-SE": ("Sergipe", 21694),
    "BR-SP": ("São Paulo", 249274),
    "BR-TO": ("Tocantins", 280106),
    # CA
    "CA-AB": ("Alberta", 660758),
    "CA-BC": ("British Columbia", 941451),
    "CA-MB": ("Manitoba", 649717),
    "CA-NB": ("New Brunswick", 72594),
    "CA-NL": ("Newfoundland and Labrador", 401715),
    "CA-NS": ("Nova Scotia", 54983),
    "CA-NT": ("Northwest Territories", 1339513),
    "CA-NU": ("Nunavut", 2065098),
    "CA-ON": ("Ontario", 1073988),
    "CA-PE": ("Prince Edward Island", 5718),
    "CA-QC": ("Québec", 1499430),
    "CA-SK": ("Saskatchewan", 648059),
    "CA-YT": ("Yukon", 481475),
    # GB
    "GB-ENG": ("England", 130279),
    "GB-NIR": ("Northern Ireland", 14130),
    "GB-SCT": ("Scotland", 77933),
    "GB-WLS": ("Wales", 20735),
    # MX
    "MX-AGU": ("Aguascalientes", 5562),
    "MX-BCN": ("Baja California", 72982),
    "MX-BCS": ("Baja California Sur", 72265),
    "MX-CAM": ("Campeche", 57319),
    "MX-CHH": ("Chihuahua", 248321),
    "MX-CHP": ("Chiapas", 73635),
    "MX-COA": ("Coahuila", 151228),
    "MX-COL": ("Colima", 5896),
    "MX-DIF": ("Distrito Federal", 1379),
    "MX-DUR": ("Durango", 121279),
    "MX-GRO": ("Guerrero", 64905),
    "MX-GUA": ("Guanajuato", 30504),
    "MX-HID": ("Hidalgo", 21365),
    "MX-JAL": ("Jalisco", 80226),
    "MX-MEX": ("México", 21891),
    "MX-MIC": ("Michoacán", 59060),
    "MX-MOR": ("Morelos", 5096),
    "MX-NAY": ("Nayarit", 27236),
    "MX-NLE": ("Nuevo León", 65113),
    "MX-OAX": ("Oaxaca", 92466),
    "MX-PUE": ("Puebla", 34697),
    "MX-QUE": ("Querétaro", 11733),
    "MX-ROO": ("Quintana Roo", 44006),
    "MX-SIN": ("Sinaloa", 57837),
    "MX-SLP": ("San Luis Potosí", 64362),
    "MX-SON": ("Sonora", 179392),
    "MX-TAB": ("Tabasco", 24134),
    "MX-TAM": ("Tamaulipas", 79760),
    "MX-TLA": ("Tlaxcala", 3884),
    "MX-VER": ("Veracruz", 71179),
    "MX-YUC": ("Yucatán", 38492),
    "MX-ZAC": ("Zacatecas", 74904),
    # US
    "US-AK": ("Alaska", 1495905),
    "US-AL": ("Alabama", 133909),
    "US-AR": ("Arkansas", 137553),
    "US-AZ": ("Arizona", 295138),
    "US-CA": ("California", 409565),
    "US-CO": ("Colorado", 268980),
    "US-CT": ("Connecticut", 12686),
    "US-DC": ("District of Columbia", 162),
    "US-DE": ("Delaware", 5167),
    "US-FL": ("Florida", 146923),
    "US-GA": ("Georgia", 152252),
    "US-HI": ("Hawaii", 16896),
    "US-IA": ("Iowa", 145223),
    "US-ID": ("Idaho", 215807),
    "US-IL": ("Illinois", 149990),
    "US-IN": ("Indiana", 94235),
    "US-KS": ("Kansas", 212675),
    "US-KY": ("Kentucky", 104526),
    "US-LA": ("Louisiana", 119656),
    "US-MA": ("Massachusetts", 21128),
    "US-MD": ("Maryland", 25440),
    "US-ME": ("Maine", 84063),
    "US-MI": ("Michigan", 249498),
    "US-MN": ("Minnesota", 224212),
    "US-MO": ("Missouri", 180353),
    "US-MS": ("Mississippi", 123875),
    "US-MT": ("Montana", 378471),
    "US-NC": ("North Carolina", 127655),
    "US-ND": ("North Dakota", 182236),
    "US-NE": ("Nebraska", 200686),
    "US-NH": ("New Hampshire", 24220),
    "US-NJ": ("New Jersey", 19646),
    "US-NM": ("New Mexico", 315139),
    "US-NV": ("Nevada", 286830),
    "US-NY": ("New York", 136693),
    "US-OH": ("Ohio", 115910),
    "US-OK": ("Oklahoma", 180627),
    "US-OR": ("Oregon", 251391),
    "US-PA": ("Pennsylvania", 119206),
    "US-RI": ("Rhode Island", 2828),
    "US-SC": ("South Carolina", 80188),
    "US-SD": ("South Dakota", 198887),
    "US-TN": ("Tennessee", 109286),
    "US-TX": ("Texas", 685903),
    "US-UT": ("Utah", 219565),
    "US-VA": ("Virginia", 103674),
    "US-VT": ("Vermont", 24587),
    "US-WA": ("Washington", 173628),
    "US-WI": ("Wisconsin", 169034),
    "US-WV": ("West Virginia", 62689),
    "US-WY": ("Wyoming", 253041),
}

# Countries that are subdivided into tiles (the whitelist)
SUBDIVIDED_COUNTRIES: dict[str, tuple[str, ...]] = {}
for _code in SUBDIVISION_TILES:
    _cc = _code.split("-", 1)[0]
    SUBDIVIDED_COUNTRIES.setdefault(_cc, ())
    SUBDIVIDED_COUNTRIES[_cc] = SUBDIVIDED_COUNTRIES[_cc] + (_code,)

_TILE_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")


def is_subdivided(cc: str | None) -> bool:
    return bool(cc) and cc.strip().upper() in SUBDIVIDED_COUNTRIES


def country_of_tile(tile: str) -> str:
    return (tile or "").strip().upper().split("-", 1)[0]


def _country_land_area(cc: str) -> int:
    """Land area for a single-tile country (km²)."""
    from yonder.country_size import COUNTRY_SIZE

    entry = COUNTRY_SIZE.get(cc)
    return int(entry[0]) if entry else 0


def country_total_area(cc: str) -> int:
    """Full land area of a country: sum of subdivision tiles if subdivided."""
    cc = (cc or "").strip().upper()
    subs = SUBDIVIDED_COUNTRIES.get(cc)
    if subs:
        return sum(SUBDIVISION_TILES[s][1] for s in subs)
    return _country_land_area(cc)


def tile_area(tile: str) -> int:
    """Area credit for one tile entry.

    Subdivision tile → its full land area.
    Country tile of a NON-subdivided country → the country's land area.
    Country tile of a subdivided country → "some coverage" partial credit:
    one average region (total ÷ region count).
    """
    code = (tile or "").strip().upper()
    if code in SUBDIVISION_TILES:
        return SUBDIVISION_TILES[code][1]
    if "-" in code:
        return 0  # unknown subdivision code
    subs = SUBDIVIDED_COUNTRIES.get(code)
    if subs:
        return round(country_total_area(code) / len(subs))
    return _country_land_area(code)


def tile_label(tile: str) -> str:
    """Human-readable label for a tile code."""
    code = (tile or "").strip().upper()
    if code in SUBDIVISION_TILES:
        return SUBDIVISION_TILES[code][0]
    from yonder.countries import country_label

    return country_label(code)


def normalize_tile_list(raw, max_n: int = 500) -> list[str]:
    """Normalize a comma-separated string or list into valid tile codes.

    Accepts plain ISO2 country codes and ISO 3166-2 subdivision codes for
    subdivided countries.  Preserves first-seen (stamp) order, drops
    duplicates and codes that are neither a known country nor a known
    subdivision tile.
    """
    from yonder.countries import COUNTRIES

    valid_cc = {c for c, _ in COUNTRIES}
    if isinstance(raw, str):
        parts = re.split(r"[,;\s]+", raw)
    else:
        parts = list(raw or [])
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        code = str(p or "").strip().upper()
        if not code or code in seen or not _TILE_RE.match(code):
            continue
        if "-" in code:
            if code not in SUBDIVISION_TILES:
                continue
        elif code not in valid_cc and code not in SUBDIVIDED_COUNTRIES:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= max_n:
            break
    return out


def visited_countries_from_tiles(tiles: list[str]) -> list[str]:
    """Ordered unique country codes with ANY coverage (stamp order kept)."""
    out: list[str] = []
    seen: set[str] = set()
    for t in tiles or []:
        cc = country_of_tile(t)
        if cc and cc not in seen:
            seen.add(cc)
            out.append(cc)
    return out


def fully_visited_countries(tiles: list[str]) -> set[str]:
    """Countries counted as completely seen for the getaway visited filter.

    Non-subdivided: visited tile == fully visited.  Subdivided: fully
    visited only when every subdivision tile is marked; a country-level
    entry alone is "some coverage" and does NOT suppress the country.
    """
    tset = {str(t or "").strip().upper() for t in tiles or []}
    full: set[str] = set()
    for t in tset:
        cc = country_of_tile(t)
        if not cc or cc in full:
            continue
        subs = SUBDIVIDED_COUNTRIES.get(cc)
        if subs is None:
            if "-" not in t:
                full.add(cc)
        elif all(s in tset for s in subs):
            full.add(cc)
    return full


# Share of a subdivided country's regions that must be marked "avoid"
# before the whole country behaves as avoided (mirrors the visited rule).
AVOID_SATURATION = 0.8


def avoid_saturated_countries(
    avoid_tiles: list[str], visited_tiles: list[str]
) -> set[str]:
    """Subdivided countries whose avoided regions hit >=80% (by tile count).

    Precedence (documented decision):
    * A tile marked visited never counts toward the avoid tally, even if it
      somehow also appears in the avoid list — visited wins per tile.
    * Because visited tiles are excluded, a country can never be both fully
      seen and avoid-saturated: every visited region lowers the avoid share.
    * The share is avoided regions / ALL regions of the country, so >=80%
      of the country's total regions must be explicitly avoided.

    Only subdivision tiles count — country-level avoid entries are handled
    by the existing avoid_countries list, and non-subdivided countries
    avoid as a whole already.
    """
    vset = {str(t or "").strip().upper() for t in visited_tiles or []}
    by_cc: dict[str, set[str]] = {}
    for t in avoid_tiles or []:
        code = str(t or "").strip().upper()
        if code in SUBDIVISION_TILES and code not in vset:
            by_cc.setdefault(country_of_tile(code), set()).add(code)
    out: set[str] = set()
    for cc, codes in by_cc.items():
        subs = SUBDIVIDED_COUNTRIES.get(cc)
        if subs and len(codes) / len(subs) >= AVOID_SATURATION - 1e-9:
            out.add(cc)
    return out


def unlocked_km2(tiles: list[str]) -> int:
    """Total square kilometres unlocked by a tile list.

    Per subdivided country: sum of visited subdivision areas, floored at
    the country-level partial credit when a plain country entry is also
    present (so "CA" + "CA-ON" never earns less than "CA-ON" alone plus
    nothing extra beyond the floor).  Non-subdivided countries simply add
    their land area once.
    """
    tset: list[str] = []
    seen: set[str] = set()
    for t in tiles or []:
        code = str(t or "").strip().upper()
        if code and code not in seen:
            seen.add(code)
            tset.append(code)
    total = 0
    by_cc: dict[str, list[str]] = {}
    for t in tset:
        by_cc.setdefault(country_of_tile(t), []).append(t)
    for cc, codes in by_cc.items():
        subs = SUBDIVIDED_COUNTRIES.get(cc)
        if not subs:
            total += _country_land_area(cc)
            continue
        sub_sum = sum(SUBDIVISION_TILES[c][1] for c in codes if c in SUBDIVISION_TILES)
        has_country_entry = any("-" not in c for c in codes)
        if has_country_entry:
            sub_sum = max(sub_sum, tile_area(cc))
        total += sub_sum
    return total


def unvisited_home_regions(home_cc: str | None, tiles: list[str]) -> list[tuple[str, str]]:
    """(code, name) subdivisions of the home country not yet visited."""
    cc = (home_cc or "").strip().upper()
    subs = SUBDIVIDED_COUNTRIES.get(cc)
    if not subs:
        return []
    tset = {str(t or "").strip().upper() for t in tiles or []}
    return [
        (s, SUBDIVISION_TILES[s][0]) for s in subs if s not in tset
    ]
