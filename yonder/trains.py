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
