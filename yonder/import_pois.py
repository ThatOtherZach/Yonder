"""CLI entry-point: idempotently import the curated POI CSV into Postgres.

Usage:
    python -m yonder.import_pois
    python -m yonder.import_pois path/to/alternate.csv
"""
from __future__ import annotations

import sys


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else None
    from yonder.poi import import_pois

    n = import_pois(csv_path)
    print(f"Imported / updated {n} POIs.")


if __name__ == "__main__":
    main()
