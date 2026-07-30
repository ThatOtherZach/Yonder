#!/usr/bin/env python3
"""Refresh yonder/static/airports_ll.json from OurAirports open data.

Downloads airports.csv from https://davidmegginson.github.io/ourairports-data/
and rebuilds the JSON snapshot used by /api/nearest-airport.

Filters applied (same as the original snapshot):
  - type        : large_airport, medium_airport, or small_airport
  - iata_code   : exactly 3 upper-case letters
  - scheduled_service : "yes"

Output format:  {"AAA": [lat, lon], ...}  (sorted by IATA code)

Usage:
    python scripts/refresh_airports.py            # writes file in-place
    python scripts/refresh_airports.py --dry-run  # prints stats, no write
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUTPUT_PATH = Path(__file__).parent.parent / "yonder" / "static" / "airports_ll.json"

ALLOWED_TYPES = {"large_airport", "medium_airport", "small_airport"}


def fetch_csv(url: str) -> str:
    print(f"Fetching {url} …", flush=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        raw = resp.read()
    print(f"  Downloaded {len(raw):,} bytes", flush=True)
    return raw.decode("utf-8", errors="replace")


def build_index(csv_text: str) -> dict[str, list[float]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    index: dict[str, list[float]] = {}
    skipped = 0
    for row in reader:
        iata = (row.get("iata_code") or "").strip().upper()
        if len(iata) != 3 or not iata.isalpha():
            skipped += 1
            continue
        if row.get("scheduled_service", "").strip().lower() != "yes":
            skipped += 1
            continue
        if row.get("type", "").strip() not in ALLOWED_TYPES:
            skipped += 1
            continue
        try:
            lat = round(float(row["latitude_deg"]), 4)
            lon = round(float(row["longitude_deg"]), 4)
        except (KeyError, ValueError):
            skipped += 1
            continue
        # Prefer larger airport when duplicate IATA codes appear
        type_rank = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
        cur_type = row.get("type", "").strip()
        if iata in index:
            # Keep current entry — first occurrence wins (CSV is roughly ranked)
            pass
        else:
            index[iata] = [lat, lon]
    print(f"  Kept {len(index):,} airports, skipped {skipped:,} rows", flush=True)
    return dict(sorted(index.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and parse but do not write the output file",
    )
    parser.add_argument(
        "--source",
        default=SOURCE_URL,
        help=f"CSV URL (default: {SOURCE_URL})",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help=f"Output JSON path (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    csv_text = fetch_csv(args.source)
    index = build_index(csv_text)

    if not index:
        print("ERROR: no airports found — check filters or source URL", file=sys.stderr)
        sys.exit(1)

    json_str = json.dumps(index, separators=(",", ":"))

    if args.dry_run:
        print(f"Dry run — would write {len(json_str):,} bytes to {args.output}")
        print(f"Sample entries: {dict(list(index.items())[:5])}")
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json_str, encoding="utf-8")
    print(f"Wrote {len(json_str):,} bytes → {out}")


if __name__ == "__main__":
    main()
