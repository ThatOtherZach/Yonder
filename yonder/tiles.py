"""Tiled world map: square-kilometres-unlocked progression.

The world is carved into "tiles":

* The US and Canada are subdivided into their ISO 3166-2 first-level
  regions (states / provinces / territories); the UK is split into its
  constituent countries (GB-ENG / GB-SCT / GB-WLS / GB-NIR).  Each
  subdivision is its own tile.
* Every other country is a single tile (its plain ISO2 code).
  Mexico, Brazil, and Australia were subdivided historically and are now
  single-country tiles again; see the retired-region collapse rule below.

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

Retired-region collapse rule (documented decision):
* MX, BR, and AU used to be subdivided; their region tiles are RETIRED.
  Any stored/incoming retired region code (e.g. "MX-JAL", "BR-SP",
  "AU-NSW") collapses to its plain country tile.
* A retired region that was **visited** makes the country tile visited
  (full country km² credited).  A retired region that was **avoided**
  makes the whole country avoided.  If a country has both, **visited
  wins** and the avoid mark is dropped.
"""
from __future__ import annotations

import re

# ISO 3166-2 subdivision tiles: code -> (display name, land area km²)
SUBDIVISION_TILES: dict[str, tuple[str, int]] = {
    # CA — 2 merged regions (BC+AB, SK+MB) + 5 unchanged solo tiles
    "CA-WCA": ("Western Canada", 1602209),    # BC + AB
    "CA-PRA": ("Prairie Provinces", 1297776), # SK + MB
    "CA-ON": ("Ontario", 1073988),
    "CA-QC": ("Québec", 1499430),
    # Atlantic Canada merged tile: NB + NL + NS + PE (see MERGED_TILE_ALIASES)
    "CA-ATL": ("Atlantic Canada", 535010),
    # Northern merged tile: NT + NU (see MERGED_TILE_ALIASES)
    "CA-NTH": ("Northwest Territories & Nunavut", 3404611),
    "CA-YT": ("Yukon", 481475),
    # GB
    "GB-ENG": ("England", 130279),
    "GB-NIR": ("Northern Ireland", 14130),
    "GB-SCT": ("Scotland", 77933),
    "GB-WLS": ("Wales", 20735),
    # US — 8 continental regions + AK + HI
    "US-NEC": ("Northeast", 475826),          # ME NH VT MA RI CT NY NJ PA MD DC DE
    "US-SEC": ("Southeast", 673381),          # VA WV NC SC GA FL
    "US-SOU": ("South", 728805),              # KY TN AL MS AR LA
    "US-TEX": ("Texas & Oklahoma", 866530),   # TX OK
    "US-GLK": ("Great Lakes", 778667),        # OH IN MI IL WI
    "US-MWP": ("Midwest & Plains", 1344272),  # MN IA MO ND SD NE KS
    "US-MTN": ("Mountain West", 2232971),     # MT ID WY CO UT NV NM AZ
    "US-PAC": ("Pacific Coast", 834584),      # WA OR CA
    "US-AK": ("Alaska", 1495905),
    "US-HI": ("Hawaii", 16896),
}

# Formerly subdivided countries whose region tiles are retired: any stored
# or incoming "CC-XXX" code for these collapses to the plain country tile.
RETIRED_SUBDIVIDED_COUNTRIES: frozenset[str] = frozenset({"MX", "BR", "AU"})

# Individual region codes merged into combined tiles (documented decision):
# Canada's Atlantic provinces are one area, and NT + NU are one area.  Any
# stored or incoming old code maps to its merged tile on normalization, so
# storage self-heals on the next save.
MERGED_TILE_ALIASES: dict[str, str] = {
    # CA Atlantic provinces → ATL
    "CA-NB": "CA-ATL",
    "CA-NL": "CA-ATL",
    "CA-NS": "CA-ATL",
    "CA-PE": "CA-ATL",
    # CA northern territories → NTH
    "CA-NT": "CA-NTH",
    "CA-NU": "CA-NTH",
    # CA provinces merged into regional tiles
    "CA-BC": "CA-WCA",
    "CA-AB": "CA-WCA",
    "CA-SK": "CA-PRA",
    "CA-MB": "CA-PRA",
    # US states → continental region tiles
    "US-ME": "US-NEC", "US-NH": "US-NEC", "US-VT": "US-NEC",
    "US-MA": "US-NEC", "US-RI": "US-NEC", "US-CT": "US-NEC",
    "US-NY": "US-NEC", "US-NJ": "US-NEC", "US-PA": "US-NEC",
    "US-MD": "US-NEC", "US-DC": "US-NEC", "US-DE": "US-NEC",
    "US-VA": "US-SEC", "US-WV": "US-SEC", "US-NC": "US-SEC",
    "US-SC": "US-SEC", "US-GA": "US-SEC", "US-FL": "US-SEC",
    "US-KY": "US-SOU", "US-TN": "US-SOU", "US-AL": "US-SOU",
    "US-MS": "US-SOU", "US-AR": "US-SOU", "US-LA": "US-SOU",
    "US-TX": "US-TEX", "US-OK": "US-TEX",
    "US-OH": "US-GLK", "US-IN": "US-GLK", "US-MI": "US-GLK",
    "US-IL": "US-GLK", "US-WI": "US-GLK",
    "US-MN": "US-MWP", "US-IA": "US-MWP", "US-MO": "US-MWP",
    "US-ND": "US-MWP", "US-SD": "US-MWP", "US-NE": "US-MWP",
    "US-KS": "US-MWP",
    "US-MT": "US-MTN", "US-ID": "US-MTN", "US-WY": "US-MTN",
    "US-CO": "US-MTN", "US-UT": "US-MTN", "US-NV": "US-MTN",
    "US-NM": "US-MTN", "US-AZ": "US-MTN",
    "US-WA": "US-PAC", "US-OR": "US-PAC", "US-CA": "US-PAC",
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
        if not code or not _TILE_RE.match(code):
            continue
        code = MERGED_TILE_ALIASES.get(code, code)
        if code in seen:
            continue
        if "-" in code:
            if code not in SUBDIVISION_TILES:
                # Retired region codes (MX/BR/AU) collapse to the country tile
                cc = code.split("-", 1)[0]
                if cc in RETIRED_SUBDIVIDED_COUNTRIES and cc in valid_cc:
                    if cc in seen:
                        continue
                    code = cc
                else:
                    continue
        elif code not in valid_cc and code not in SUBDIVIDED_COUNTRIES:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= max_n:
            break
    return out


def collapse_retired_region_prefs(prefs: dict[str, str]) -> dict[str, str]:
    """One-time collapse of retired MX/BR/AU region tiles in stored prefs.

    Returns only the pref keys whose values changed (empty dict = no-op).

    Rules (documented decision):
    * A visited retired region → its country tile visited (full-country
      km² credit, since the country is no longer subdivided).
    * An avoided retired region → the whole country avoided (moved into
      avoid_countries; region codes never survive in avoid_tiles).
    * Visited wins: if a country has both visited and avoided retired
      regions, it collapses to visited and the avoid mark is dropped.
    """
    def _split(raw: str) -> list[str]:
        out, seen = [], set()
        for p in re.split(r"[,;\s]+", raw or ""):
            code = p.strip().upper()
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def _retired(code: str) -> bool:
        return "-" in code and code.split("-", 1)[0] in RETIRED_SUBDIVIDED_COUNTRIES

    visited_raw = _split(prefs.get("visited_tiles", ""))
    avoid_tiles_raw = _split(prefs.get("avoid_tiles", ""))
    avoid_cc_raw = _split(prefs.get("avoid_countries", ""))
    if not any(_retired(c) for c in visited_raw + avoid_tiles_raw):
        return {}

    # Visited: retired regions collapse to the country tile (stamp order)
    visited: list[str] = []
    vseen: set[str] = set()
    for c in visited_raw:
        code = c.split("-", 1)[0] if _retired(c) else c
        if code not in vseen:
            vseen.add(code)
            visited.append(code)
    visited_cc = {country_of_tile(t) for t in visited}

    # Avoid: retired regions promote the country to avoid_countries,
    # unless that country is (now) visited — visited wins.
    avoid_tiles = [c for c in avoid_tiles_raw if not _retired(c)]
    avoid_cc = list(avoid_cc_raw)
    for c in avoid_tiles_raw:
        if _retired(c):
            cc = c.split("-", 1)[0]
            if cc not in avoid_cc and cc not in visited_cc:
                avoid_cc.append(cc)

    changed: dict[str, str] = {}
    if visited != visited_raw:
        changed["visited_tiles"] = ",".join(visited)
        changed["visited_countries"] = ",".join(visited_countries_from_tiles(visited))
    if avoid_tiles != avoid_tiles_raw:
        changed["avoid_tiles"] = ",".join(avoid_tiles)
    if avoid_cc != avoid_cc_raw:
        changed["avoid_countries"] = ",".join(avoid_cc)
    return changed


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
        code = MERGED_TILE_ALIASES.get(code, code)
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
    tset = {
        MERGED_TILE_ALIASES.get(c, c)
        for c in (str(t or "").strip().upper() for t in tiles or [])
    }
    return [
        (s, SUBDIVISION_TILES[s][0]) for s in subs if s not in tset
    ]
