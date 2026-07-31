"""Static passenger-rail route lookup.

Provides a curated, hardcoded table of passenger rail routes and a
``train_options(from_iata, to_iata)`` function that does a two-pass lookup:

  1. Explicit IATA-pair match (e.g. LHR→CDG for Eurostar)
  2. Country-pair fallback (e.g. any US→US pair → Amtrak)

Returns an empty list for routes that only make sense to fly (ocean crossings,
island nations, etc.).  No schedules, no pricing — just operator + homepage.
"""
from __future__ import annotations

from yonder.countries import IATA_COUNTRY

# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------
# Each entry uses *either* from_iata/to_iata (specific airport pair) *or*
# from_cc/to_cc (ISO-2 country pair), never both.
#
# Entries with explicit IATA pairs are checked first; country-pair fallback
# only fires when no IATA-pair entry matches.
# ---------------------------------------------------------------------------
RAIL_ROUTES: list[dict] = [
    # ── Amtrak (US domestic) ─────────────────────────────────────────────
    {
        "from_cc": "US", "to_cc": "US",
        "operator": "Amtrak", "url": "https://www.amtrak.com", "emoji": "🚆",
    },
    # ── Amtrak Cascades (US ↔ Canada) ────────────────────────────────────
    {
        "from_cc": "US", "to_cc": "CA",
        "operator": "Amtrak Cascades", "url": "https://www.amtrak.com/cascades-train", "emoji": "🚆",
    },
    {
        "from_cc": "CA", "to_cc": "US",
        "operator": "Amtrak Cascades", "url": "https://www.amtrak.com/cascades-train", "emoji": "🚆",
    },
    # ── VIA Rail (Canada domestic) ───────────────────────────────────────
    {
        "from_cc": "CA", "to_cc": "CA",
        "operator": "VIA Rail", "url": "https://www.viarail.ca", "emoji": "🚆",
    },
    # ── Eurostar (London ↔ Paris) ─────────────────────────────────────────
    {
        "from_iata": "LHR", "to_iata": "CDG",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "LHR", "to_iata": "ORY",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "LGW", "to_iata": "CDG",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "LGW", "to_iata": "ORY",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "STN", "to_iata": "CDG",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "STN", "to_iata": "ORY",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "CDG", "to_iata": "LHR",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "ORY", "to_iata": "LHR",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "CDG", "to_iata": "LGW",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "ORY", "to_iata": "LGW",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "CDG", "to_iata": "STN",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "ORY", "to_iata": "STN",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    # ── Eurostar (London ↔ Brussels) ─────────────────────────────────────
    {
        "from_iata": "LHR", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "LGW", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "STN", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "BRU", "to_iata": "LHR",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "BRU", "to_iata": "LGW",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "BRU", "to_iata": "STN",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    # ── Eurostar (London ↔ Amsterdam) ────────────────────────────────────
    {
        "from_iata": "LHR", "to_iata": "AMS",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "LGW", "to_iata": "AMS",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "STN", "to_iata": "AMS",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "AMS", "to_iata": "LHR",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "AMS", "to_iata": "LGW",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "AMS", "to_iata": "STN",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    # ── Eurostar (Brussels ↔ Paris) ──────────────────────────────────────
    {
        "from_iata": "BRU", "to_iata": "CDG",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "BRU", "to_iata": "ORY",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "CDG", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "ORY", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    # ── Eurostar (Brussels ↔ Amsterdam) ─────────────────────────────────
    {
        "from_iata": "BRU", "to_iata": "AMS",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    {
        "from_iata": "AMS", "to_iata": "BRU",
        "operator": "Eurostar", "url": "https://www.eurostar.com", "emoji": "🚆",
    },
    # ── SNCF / TGV (France domestic + cross-border) ──────────────────────
    {
        "from_cc": "FR", "to_cc": "FR",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "FR", "to_cc": "ES",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "ES", "to_cc": "FR",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "FR", "to_cc": "IT",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "IT", "to_cc": "FR",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "FR", "to_cc": "CH",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "CH", "to_cc": "FR",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "FR", "to_cc": "DE",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "FR",
        "operator": "SNCF / TGV", "url": "https://www.sncf-connect.com", "emoji": "🚆",
    },
    # ── Deutsche Bahn (Germany domestic + major cross-border) ────────────
    {
        "from_cc": "DE", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "AT",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "AT", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "CH",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "CH", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "NL",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "NL", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "BE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "BE", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "PL",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "PL", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "CZ",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "CZ", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DE", "to_cc": "DK",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    {
        "from_cc": "DK", "to_cc": "DE",
        "operator": "Deutsche Bahn", "url": "https://www.bahn.de", "emoji": "🚆",
    },
    # ── RENFE / Ouigo (Spain domestic) ───────────────────────────────────
    {
        "from_cc": "ES", "to_cc": "ES",
        "operator": "RENFE / Ouigo", "url": "https://www.renfe.com", "emoji": "🚆",
    },
    # ── Trenitalia (Italy domestic) ───────────────────────────────────────
    {
        "from_cc": "IT", "to_cc": "IT",
        "operator": "Trenitalia", "url": "https://www.trenitalia.com", "emoji": "🚆",
    },
    # ── SBB (Switzerland domestic + cross-border) ────────────────────────
    {
        "from_cc": "CH", "to_cc": "CH",
        "operator": "SBB", "url": "https://www.sbb.ch", "emoji": "🚆",
    },
    {
        "from_cc": "CH", "to_cc": "AT",
        "operator": "SBB", "url": "https://www.sbb.ch", "emoji": "🚆",
    },
    {
        "from_cc": "AT", "to_cc": "CH",
        "operator": "SBB", "url": "https://www.sbb.ch", "emoji": "🚆",
    },
    {
        "from_cc": "CH", "to_cc": "IT",
        "operator": "SBB", "url": "https://www.sbb.ch", "emoji": "🚆",
    },
    {
        "from_cc": "IT", "to_cc": "CH",
        "operator": "SBB", "url": "https://www.sbb.ch", "emoji": "🚆",
    },
    # ── NS (Netherlands domestic + cross-border) ─────────────────────────
    {
        "from_cc": "NL", "to_cc": "NL",
        "operator": "NS", "url": "https://www.ns.nl", "emoji": "🚆",
    },
    {
        "from_cc": "NL", "to_cc": "BE",
        "operator": "NS", "url": "https://www.ns.nl", "emoji": "🚆",
    },
    {
        "from_cc": "BE", "to_cc": "NL",
        "operator": "NS", "url": "https://www.ns.nl", "emoji": "🚆",
    },
    # ── ÖBB (Austria domestic + cross-border) ────────────────────────────
    {
        "from_cc": "AT", "to_cc": "AT",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "AT", "to_cc": "IT",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "IT", "to_cc": "AT",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "AT", "to_cc": "HU",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "HU", "to_cc": "AT",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "AT", "to_cc": "CZ",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    {
        "from_cc": "CZ", "to_cc": "AT",
        "operator": "ÖBB", "url": "https://www.oebb.at", "emoji": "🚆",
    },
    # ── Shinkansen via JR Pass (Japan domestic) ───────────────────────────
    {
        "from_cc": "JP", "to_cc": "JP",
        "operator": "Shinkansen (JR Pass)", "url": "https://www.japanrailpass.net", "emoji": "🚄",
    },
    # ── KTX (South Korea domestic) ────────────────────────────────────────
    {
        "from_cc": "KR", "to_cc": "KR",
        "operator": "KTX", "url": "https://www.korail.com", "emoji": "🚄",
    },
    # ── THSR (Taiwan) ─────────────────────────────────────────────────────
    {
        "from_cc": "TW", "to_cc": "TW",
        "operator": "Taiwan High Speed Rail", "url": "https://www.thsrc.com.tw", "emoji": "🚄",
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def train_options(from_iata: str, to_iata: str) -> list[dict]:
    """Return rail operator dicts for the given IATA airport pair.

    Two-pass lookup:
      1. Explicit IATA-pair match (checked first; most specific)
      2. Country-pair fallback (using IATA_COUNTRY to resolve countries)

    Returns a deduplicated list sorted by operator name, or an empty list
    when no rail service is known for this corridor.
    """
    from_iata = (from_iata or "").strip().upper()
    to_iata = (to_iata or "").strip().upper()
    if not from_iata or not to_iata or from_iata == to_iata:
        return []

    from_cc = (IATA_COUNTRY.get(from_iata) or "").upper()
    to_cc = (IATA_COUNTRY.get(to_iata) or "").upper()

    # Pass 1: explicit IATA-pair matches
    iata_matches: list[dict] = []
    for entry in RAIL_ROUTES:
        if "from_iata" in entry and "to_iata" in entry:
            if (
                entry["from_iata"].upper() == from_iata
                and entry["to_iata"].upper() == to_iata
            ):
                iata_matches.append(entry)

    if iata_matches:
        return _dedup_sort(iata_matches)

    # Pass 2: country-pair fallback (only when both countries are resolvable)
    if not from_cc or not to_cc:
        return []

    cc_matches: list[dict] = []
    for entry in RAIL_ROUTES:
        if "from_cc" in entry and "to_cc" in entry:
            if (
                entry["from_cc"].upper() == from_cc
                and entry["to_cc"].upper() == to_cc
            ):
                cc_matches.append(entry)

    return _dedup_sort(cc_matches)


# ---------------------------------------------------------------------------
# Airport-to-city transit links
# ---------------------------------------------------------------------------
# Keyed by airport IATA. Each entry is a list (some airports have multiple
# useful options). Fields: name (shown in pill), url, emoji.
# Rail / metro / tram only — no pure-bus services.
# ---------------------------------------------------------------------------
AIRPORT_TRAINS: dict[str, list[dict]] = {
    # ── Europe ──────────────────────────────────────────────────────────
    "LHR": [{"name": "Heathrow Express (~15 min)", "url": "https://www.heathrowexpress.com", "emoji": "🚄"}],
    "LGW": [{"name": "Gatwick Express (~30 min)", "url": "https://www.gatwickexpress.com", "emoji": "🚄"}],
    "STN": [{"name": "Stansted Express (~47 min)", "url": "https://www.stanstedexpress.com", "emoji": "🚄"}],
    "MAN": [{"name": "Metrolink → Manchester (~20 min)", "url": "https://tfgm.com/public-transport/metrolink", "emoji": "🚇"}],
    "EDI": [{"name": "Tram → City Centre (~35 min)", "url": "https://edinburghtrams.com", "emoji": "🚃"}],
    "GLA": [{"name": "Train → Glasgow Central (~15 min)", "url": "https://www.scotrail.co.uk", "emoji": "🚆"}],
    "CDG": [{"name": "RER B → Paris Nord (~35 min)", "url": "https://www.transilien.com/en", "emoji": "🚇"}],
    "ORY": [{"name": "OrlyVal + RER B → Paris (~35 min)", "url": "https://www.ratp.fr/en/titres-et-tarifs/orlyval-access", "emoji": "🚇"}],
    "AMS": [{"name": "NS → Amsterdam Centraal (~20 min)", "url": "https://www.ns.nl/en", "emoji": "🚆"}],
    "FRA": [{"name": "S-Bahn → Frankfurt Hbf (~11 min)", "url": "https://www.rmv.de/en/homepage/", "emoji": "🚇"}],
    "MUC": [{"name": "S-Bahn S1/S8 → München Hbf (~40 min)", "url": "https://www.mvv-muenchen.de/en/", "emoji": "🚇"}],
    "DUS": [{"name": "SkyTrain + S-Bahn → Düsseldorf Hbf (~12 min)", "url": "https://www.vrs.de/en/", "emoji": "🚇"}],
    "ZRH": [{"name": "SBB → Zürich HB (~10 min)", "url": "https://www.sbb.ch/en", "emoji": "🚆"}],
    "GVA": [{"name": "Léman Express → Geneva (~8 min)", "url": "https://www.lemanexpress.ch/en/", "emoji": "🚄"}],
    "VIE": [{"name": "City Airport Train (~16 min)", "url": "https://www.cityairporttrain.com/en", "emoji": "🚄"}],
    "BRU": [{"name": "Airport Express → Brussels Midi (~20 min)", "url": "https://www.belgiantrain.be/en", "emoji": "🚆"}],
    "ARN": [{"name": "Arlanda Express (~18 min)", "url": "https://www.arlandaexpress.com", "emoji": "🚄"}],
    "CPH": [{"name": "Metro M2 → Copenhagen C (~15 min)", "url": "https://intl.m.dk", "emoji": "🚇"}],
    "HEL": [{"name": "Ring Rail → Helsinki C (~30 min)", "url": "https://www.hsl.fi/en", "emoji": "🚆"}],
    "OSL": [{"name": "Flytoget → Oslo S (~20 min)", "url": "https://flytoget.no/en/", "emoji": "🚄"}],
    "MXP": [{"name": "Malpensa Express → Milano (~40 min)", "url": "https://www.malpensaexpress.it/en/", "emoji": "🚄"}],
    "FCO": [{"name": "Leonardo Express → Roma Termini (~32 min)", "url": "https://www.trenitalia.com/en/services/leonardo_express.html", "emoji": "🚄"}],
    "BCN": [{"name": "Rodalies R2 Nord → Sants (~19 min)", "url": "https://www.rodalies.gencat.cat/en/", "emoji": "🚆"}],
    "MAD": [{"name": "Metro L8 → Nuevos Ministerios (~12 min)", "url": "https://www.metromadrid.es/en", "emoji": "🚇"}],
    "PMI": [{"name": "Metro L1 → Palma (~20 min)", "url": "https://www.tib.org/en/metro", "emoji": "🚇"}],
    "LIS": [{"name": "Metro Red Line → Oriente (~45 min)", "url": "https://www.metrolisboa.pt/en/", "emoji": "🚇"}],
    "OTP": [{"name": "Train → Gara de Nord (~15 min)", "url": "https://www.cfrcalatori.ro/en/", "emoji": "🚆"}],
    "WAW": [{"name": "SKM → Warsaw Central (~23 min)", "url": "https://www.skm.warszawa.pl/en", "emoji": "🚆"}],
    "IST": [{"name": "Metro M11 → Gayrettepe (~38 min)", "url": "https://www.istanbul-airport.com/en/passenger-rights-and-info/public-transport/metro", "emoji": "🚇"}],
    # ── Asia-Pacific ─────────────────────────────────────────────────────
    "NRT": [{"name": "Narita Express N'EX (~60 min)", "url": "https://www.jreast.co.jp/multi/en/nex/", "emoji": "🚄"}],
    "HND": [
        {"name": "Tokyo Monorail (~18 min)", "url": "https://www.tokyo-monorail.co.jp/english/", "emoji": "🚇"},
        {"name": "Keikyu → Shinagawa (~11 min)", "url": "https://www.haneda-tokyo-access.com/en/", "emoji": "🚆"},
    ],
    "KIX": [{"name": "Haruka Express → Osaka (~75 min)", "url": "https://www.westjr.co.jp/global/en/travel/shopping/access/train/haruka/", "emoji": "🚄"}],
    "CTS": [{"name": "Airport Rapid → Sapporo (~37 min)", "url": "https://www.new-chitose-airport.jp/en/access/train/", "emoji": "🚆"}],
    "ICN": [{"name": "AREX → Seoul Station (~51 min)", "url": "https://www.arex.or.kr/en/main.do", "emoji": "🚄"}],
    "GMP": [{"name": "Metro Line 5 → Gimpo (~10 min)", "url": "https://www.seoulmetro.co.kr/en/", "emoji": "🚇"}],
    "PEK": [{"name": "Airport Express → Dongzhimen (~19 min)", "url": "https://www.bairport.com/en/", "emoji": "🚄"}],
    "PKX": [{"name": "Daxing Express → Caoqiao (~45 min)", "url": "https://www.bairport.com/en/", "emoji": "🚄"}],
    "PVG": [{"name": "Maglev → Longyang Rd (~8 min)", "url": "https://www.smtdc.com/en/", "emoji": "🚄"}],
    "HKG": [{"name": "Airport Express → Hong Kong (~24 min)", "url": "https://www.mtr.com.hk/en/customer/services/airport_express_index.html", "emoji": "🚄"}],
    "TPE": [{"name": "MRT → Taipei Main Station (~35 min)", "url": "https://www.metro.taipei/en/", "emoji": "🚇"}],
    "SIN": [{"name": "MRT East-West → City Hall (~30 min)", "url": "https://www.smrt.com.sg", "emoji": "🚇"}],
    "KUL": [{"name": "KLIA Ekspres → KL Sentral (~28 min)", "url": "https://www.kliaekspres.com", "emoji": "🚄"}],
    "BKK": [{"name": "Airport Rail Link → Phaya Thai (~30 min)", "url": "https://www.srtet.co.th/en/", "emoji": "🚆"}],
    "CGK": [{"name": "Railink → Manggarai (~50 min)", "url": "https://www.railink.co.id/en/", "emoji": "🚆"}],
    "DXB": [{"name": "Dubai Metro Red Line (~40 min to Union)", "url": "https://www.rta.ae/wps/portal/rta/ae/public-transport/dubai-metro", "emoji": "🚇"}],
    "DOH": [{"name": "Doha Metro Gold Line (~15 min)", "url": "https://www.qr.com.qa/en/travel/doha-metro", "emoji": "🚇"}],
    "DEL": [{"name": "Delhi Metro Orange Line (~19 min)", "url": "https://www.delhimetrorail.com", "emoji": "🚇"}],
    "BOM": [{"name": "Mumbai Metro Line 1 → Andheri (~10 min)", "url": "https://www.mmrcl.com/en", "emoji": "🚇"}],
    "SYD": [{"name": "Airport Link → Central (~13 min)", "url": "https://www.airportlink.com.au", "emoji": "🚆"}],
    "BNE": [{"name": "Airtrain → Brisbane (~20 min)", "url": "https://www.airtrain.com.au", "emoji": "🚆"}],
    "PER": [{"name": "Transperth → Perth (~30 min)", "url": "https://www.transperth.wa.gov.au", "emoji": "🚆"}],
    "AKL": [{"name": "Airport Express → Britomart (~50 min)", "url": "https://at.govt.nz/airports/", "emoji": "🚆"}],
    # ── Americas ─────────────────────────────────────────────────────────
    "JFK": [{"name": "AirTrain + Subway (~60 min to Manhattan)", "url": "https://www.jfkairport.com/to-from-airport/air-train", "emoji": "🚇"}],
    "EWR": [{"name": "AirTrain + NJ Transit (~40 min to Penn Station)", "url": "https://www.newarkairport.com/to-from-airport/air-train", "emoji": "🚆"}],
    "ORD": [{"name": "CTA Blue Line → Loop (~45 min)", "url": "https://www.transitchicago.com", "emoji": "🚇"}],
    "MDW": [{"name": "CTA Orange Line → Loop (~30 min)", "url": "https://www.transitchicago.com", "emoji": "🚇"}],
    "DEN": [{"name": "A Line → Union Station (~37 min)", "url": "https://www.rtd-denver.com/services/airport-train", "emoji": "🚆"}],
    "ATL": [{"name": "MARTA → Five Points (~30 min)", "url": "https://www.itsmarta.com", "emoji": "🚇"}],
    "SFO": [{"name": "BART → Embarcadero (~30 min)", "url": "https://www.bart.gov/stations/sfia", "emoji": "🚇"}],
    "SEA": [{"name": "Link Light Rail → Downtown (~40 min)", "url": "https://www.soundtransit.org/ride-with-us/routes-schedules/link-light-rail", "emoji": "🚇"}],
    "YYZ": [{"name": "UP Express → Union Station (~25 min)", "url": "https://www.upexpress.com", "emoji": "🚄"}],
    "YVR": [{"name": "Canada Line SkyTrain → Waterfront (~25 min)", "url": "https://www.translink.ca", "emoji": "🚇"}],
    "GRU": [{"name": "CPTM Line 13 → Luz (~35 min)", "url": "https://www.cptm.sp.gov.br/english/", "emoji": "🚆"}],
    "SCL": [{"name": "Metro Line 3 → Baquedano (~45 min)", "url": "https://www.metro.cl/en", "emoji": "🚇"}],
    "MEX": [{"name": "Metro Line B → Buenavista (~30 min)", "url": "https://www.metro.cdmx.gob.mx/en", "emoji": "🚇"}],
    # ── Africa & Middle East ─────────────────────────────────────────────
    "TLV": [{"name": "Train → Tel Aviv (~20 min)", "url": "https://www.rail.co.il/en/", "emoji": "🚆"}],
    "JNB": [{"name": "Gautrain → Sandton (~15 min)", "url": "https://www.gautrain.co.za", "emoji": "🚆"}],
}


def airport_train_for(iata: str) -> list[dict]:
    """Return airport-to-city transit links for the given airport IATA code.

    Returns an empty list when no rail/metro/tram link is known.
    Each dict has: name (str), url (str), emoji (str).
    """
    iata = (iata or "").strip().upper()
    return AIRPORT_TRAINS.get(iata, [])


def _dedup_sort(entries: list[dict]) -> list[dict]:
    """Deduplicate by operator name, then sort alphabetically."""
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        name = e.get("operator", "")
        if name not in seen:
            seen.add(name)
            out.append(e)
    return sorted(out, key=lambda e: e.get("operator", "").lower())
