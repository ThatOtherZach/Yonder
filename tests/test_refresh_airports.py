"""Tests for scripts/refresh_airports.py — build_index filter logic.

Feeds small hand-crafted CSV fixtures through build_index and asserts that
the correct airports are kept and excluded.  No network access required.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import build_index from the scripts/ directory (not a package).
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "refresh_airports.py"


def _load_refresh_airports():
    spec = importlib.util.spec_from_file_location("refresh_airports", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_refresh_airports()
build_index = _mod.build_index


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _csv(*rows: dict) -> str:
    """Build a minimal CSV string from a list of row dicts."""
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines) + "\n"


_BASE_ROW = {
    "iata_code": "JFK",
    "type": "large_airport",
    "scheduled_service": "yes",
    "latitude_deg": "40.6398",
    "longitude_deg": "-73.7789",
    "name": "John F Kennedy International Airport",
}


def _row(**overrides):
    r = dict(_BASE_ROW)
    r.update(overrides)
    return r


# ---------------------------------------------------------------------------
# 1. Happy path — a single valid large airport is kept
# ---------------------------------------------------------------------------


def test_valid_large_airport_is_kept():
    csv_text = _csv(_row())
    index = build_index(csv_text)
    assert "JFK" in index
    assert index["JFK"] == [40.6398, -73.7789]


def test_valid_medium_airport_is_kept():
    csv_text = _csv(_row(iata_code="YYC", type="medium_airport",
                         latitude_deg="51.1131", longitude_deg="-114.0100"))
    index = build_index(csv_text)
    assert "YYC" in index


def test_valid_small_airport_is_kept():
    csv_text = _csv(_row(iata_code="BZN", type="small_airport",
                         latitude_deg="45.7775", longitude_deg="-111.1531"))
    index = build_index(csv_text)
    assert "BZN" in index


# ---------------------------------------------------------------------------
# 2. Wrong type — heliport, seaplane base, closed, etc. are excluded
# ---------------------------------------------------------------------------


def test_heliport_excluded():
    csv_text = _csv(_row(type="heliport"))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_seaplane_base_excluded():
    csv_text = _csv(_row(type="seaplane_base"))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_closed_airport_excluded():
    csv_text = _csv(_row(type="closed"))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_balloonport_excluded():
    csv_text = _csv(_row(type="balloonport"))
    index = build_index(csv_text)
    assert "JFK" not in index


# ---------------------------------------------------------------------------
# 3. Missing or invalid IATA code — row is excluded
# ---------------------------------------------------------------------------


def test_empty_iata_excluded():
    csv_text = _csv(_row(iata_code=""))
    index = build_index(csv_text)
    assert index == {}


def test_two_letter_iata_excluded():
    csv_text = _csv(_row(iata_code="JF"))
    index = build_index(csv_text)
    assert index == {}


def test_four_letter_iata_excluded():
    csv_text = _csv(_row(iata_code="KJFK"))
    index = build_index(csv_text)
    assert index == {}


def test_numeric_iata_excluded():
    csv_text = _csv(_row(iata_code="1AB"))
    index = build_index(csv_text)
    assert index == {}


def test_iata_with_digits_excluded():
    csv_text = _csv(_row(iata_code="JF1"))
    index = build_index(csv_text)
    assert index == {}


# ---------------------------------------------------------------------------
# 4. scheduled_service != "yes" — row is excluded
# ---------------------------------------------------------------------------


def test_scheduled_service_no_excluded():
    csv_text = _csv(_row(scheduled_service="no"))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_scheduled_service_empty_excluded():
    csv_text = _csv(_row(scheduled_service=""))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_scheduled_service_missing_excluded():
    row = _row()
    del row["scheduled_service"]
    csv_text = _csv(row)
    index = build_index(csv_text)
    assert "JFK" not in index


def test_scheduled_service_case_insensitive_accepted():
    """'yes' is matched case-insensitively (the script lowercases it)."""
    csv_text = _csv(_row(scheduled_service="Yes"))
    index = build_index(csv_text)
    assert "JFK" in index


# ---------------------------------------------------------------------------
# 5. Bad coordinates — row is excluded
# ---------------------------------------------------------------------------


def test_non_numeric_latitude_excluded():
    csv_text = _csv(_row(latitude_deg="unknown"))
    index = build_index(csv_text)
    assert "JFK" not in index


def test_missing_latitude_column_excluded():
    """If OurAirports renames latitude_deg the KeyError is caught and the row is skipped."""
    row = _row()
    del row["latitude_deg"]
    csv_text = _csv(row)
    index = build_index(csv_text)
    assert "JFK" not in index


def test_missing_longitude_column_excluded():
    row = _row()
    del row["longitude_deg"]
    csv_text = _csv(row)
    index = build_index(csv_text)
    assert "JFK" not in index


# ---------------------------------------------------------------------------
# 6. Multiple rows — only qualifying entries appear in the result
# ---------------------------------------------------------------------------


def test_mixed_rows_only_valid_kept():
    csv_text = _csv(
        _row(iata_code="JFK"),                          # valid → kept
        _row(iata_code="XX", type="large_airport"),     # bad IATA → excluded
        _row(iata_code="LAX", scheduled_service="no"),  # not scheduled → excluded
        _row(iata_code="SFO", type="heliport"),         # wrong type → excluded
        _row(iata_code="ORD", type="medium_airport",
             latitude_deg="41.9786", longitude_deg="-87.9048"),  # valid → kept
    )
    index = build_index(csv_text)
    assert set(index.keys()) == {"JFK", "ORD"}


def test_result_is_sorted_by_iata():
    csv_text = _csv(
        _row(iata_code="ORD", latitude_deg="41.9786", longitude_deg="-87.9048"),
        _row(iata_code="ATL", latitude_deg="33.6407", longitude_deg="-84.4277"),
        _row(iata_code="JFK"),
    )
    index = build_index(csv_text)
    assert list(index.keys()) == sorted(index.keys())


# ---------------------------------------------------------------------------
# 7. Coordinate precision — values are rounded to 4 decimal places
# ---------------------------------------------------------------------------


def test_coordinates_rounded_to_4dp():
    csv_text = _csv(_row(latitude_deg="40.639751234", longitude_deg="-73.778925678"))
    index = build_index(csv_text)
    lat, lon = index["JFK"]
    assert lat == round(40.639751234, 4)
    assert lon == round(-73.778925678, 4)


# ---------------------------------------------------------------------------
# 8. Duplicate IATA codes — first occurrence wins
# ---------------------------------------------------------------------------


def test_duplicate_iata_first_wins():
    csv_text = _csv(
        _row(iata_code="JFK", latitude_deg="10.0000", longitude_deg="20.0000"),
        _row(iata_code="JFK", latitude_deg="99.0000", longitude_deg="99.0000"),
    )
    index = build_index(csv_text)
    assert index["JFK"] == [10.0, 20.0]


# ---------------------------------------------------------------------------
# 9. Empty CSV — returns empty dict (not an exception)
# ---------------------------------------------------------------------------


def test_empty_csv_returns_empty_dict():
    csv_text = "iata_code,type,scheduled_service,latitude_deg,longitude_deg\n"
    index = build_index(csv_text)
    assert index == {}


# ---------------------------------------------------------------------------
# 10. Renamed column simulation — script survives gracefully
# ---------------------------------------------------------------------------


def test_renamed_iata_column_produces_empty():
    """If OurAirports renames 'iata_code' to 'iata', every row loses its IATA
    and the result is empty — the script won't silently write garbage data."""
    csv_text = textwrap.dedent("""\
        iata,type,scheduled_service,latitude_deg,longitude_deg
        JFK,large_airport,yes,40.6398,-73.7789
        LHR,large_airport,yes,51.4775,-0.4614
    """)
    index = build_index(csv_text)
    assert index == {}


def test_renamed_lat_column_produces_empty():
    """If 'latitude_deg' is renamed to 'lat', coordinates fail to parse."""
    csv_text = textwrap.dedent("""\
        iata_code,type,scheduled_service,lat,longitude_deg
        JFK,large_airport,yes,40.6398,-73.7789
    """)
    index = build_index(csv_text)
    assert index == {}
