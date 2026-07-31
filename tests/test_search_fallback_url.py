"""Tests for the Google Flights search-fallback URL builder.

Covers the three leg types that `buildSearchUrl` (check_fares.js) and its
Python mirror `google_flights_url` (links.py) must handle:
  1. One-way   — query ends with "oneway"
  2. Round-trip — query uses "through <return-date>" instead of "oneway"
  3. Multi-adult — appends "with N adults" after the date clause

Each assertion checks:
  - correct domain  (google.com/travel/flights)
  - correct currency parameter
  - correct query-string phrasing
"""

from datetime import date

import pytest

from yonder.links import google_flights_url


DOMAIN = "https://www.google.com/travel/flights"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_q(url: str) -> str:
    """Return the decoded value of the `q=` parameter from a Google Flights URL."""
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    return qs["q"][0]


def get_param(url: str, key: str) -> str:
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    return qs[key][0]


# ---------------------------------------------------------------------------
# 1. One-way leg
# ---------------------------------------------------------------------------

class TestOneWay:
    def test_domain(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        assert url.startswith(DOMAIN)

    def test_currency_param(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        assert get_param(url, "curr") == "CAD"

    def test_currency_usd(self):
        url = google_flights_url("JFK", "LHR", date(2026, 12, 15), currency="USD")
        assert get_param(url, "curr") == "USD"

    def test_q_contains_origin_and_dest(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        q = decode_q(url)
        assert "YVR" in q
        assert "NRT" in q

    def test_q_contains_depart_date(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        q = decode_q(url)
        assert "2026-11-01" in q

    def test_q_ends_with_oneway(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        q = decode_q(url)
        assert q.endswith("oneway"), f"Expected 'oneway' suffix, got: {q!r}"

    def test_q_does_not_contain_through(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        q = decode_q(url)
        assert "through" not in q

    def test_q_does_not_contain_adults_clause(self):
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD", adults=1)
        q = decode_q(url)
        assert "adults" not in q

    def test_q_phrase_structure(self):
        """Full phrase: 'Flights to DEST from ORIGIN on DATE oneway'."""
        url = google_flights_url("YVR", "NRT", date(2026, 11, 1), currency="CAD")
        q = decode_q(url)
        assert q == "Flights to NRT from YVR on 2026-11-01 oneway"


# ---------------------------------------------------------------------------
# 2. Round-trip leg
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_domain(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD",
        )
        assert url.startswith(DOMAIN)

    def test_currency_param(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="EUR",
        )
        assert get_param(url, "curr") == "EUR"

    def test_q_contains_through(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD",
        )
        q = decode_q(url)
        assert "through" in q, f"Expected 'through' in round-trip query, got: {q!r}"

    def test_q_contains_return_date(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD",
        )
        q = decode_q(url)
        assert "2026-09-24" in q

    def test_q_does_not_contain_oneway(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD",
        )
        q = decode_q(url)
        assert "oneway" not in q

    def test_q_phrase_structure(self):
        """Full phrase: 'Flights to DEST from ORIGIN on DEPART through RETURN'."""
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD",
        )
        q = decode_q(url)
        assert q == "Flights to CDG from YYZ on 2026-09-10 through 2026-09-24"

    def test_q_no_adults_clause_for_one_adult(self):
        url = google_flights_url(
            "YYZ", "CDG", date(2026, 9, 10),
            return_date=date(2026, 9, 24), currency="CAD", adults=1,
        )
        q = decode_q(url)
        assert "adults" not in q


# ---------------------------------------------------------------------------
# 3. Multi-adult leg
# ---------------------------------------------------------------------------

class TestMultiAdult:
    def test_one_way_with_two_adults(self):
        url = google_flights_url(
            "LAX", "SYD", date(2026, 10, 5), currency="USD", adults=2,
        )
        q = decode_q(url)
        assert "with 2 adults" in q, f"Expected 'with 2 adults', got: {q!r}"

    def test_one_way_with_two_adults_phrase_structure(self):
        url = google_flights_url(
            "LAX", "SYD", date(2026, 10, 5), currency="USD", adults=2,
        )
        q = decode_q(url)
        assert q == "Flights to SYD from LAX on 2026-10-05 oneway with 2 adults"

    def test_round_trip_with_three_adults(self):
        url = google_flights_url(
            "LHR", "DXB", date(2026, 12, 20),
            return_date=date(2027, 1, 3), currency="GBP", adults=3,
        )
        q = decode_q(url)
        assert "with 3 adults" in q, f"Expected 'with 3 adults', got: {q!r}"

    def test_round_trip_with_three_adults_phrase_structure(self):
        url = google_flights_url(
            "LHR", "DXB", date(2026, 12, 20),
            return_date=date(2027, 1, 3), currency="GBP", adults=3,
        )
        q = decode_q(url)
        assert q == "Flights to DXB from LHR on 2026-12-20 through 2027-01-03 with 3 adults"

    def test_adults_clause_appended_after_date_clause(self):
        """Adults phrase must follow the date/return clause, not precede it."""
        url = google_flights_url(
            "SFO", "HND", date(2026, 8, 1), currency="USD", adults=4,
        )
        q = decode_q(url)
        date_pos = q.index("2026-08-01")
        adults_pos = q.index("with 4 adults")
        assert adults_pos > date_pos

    def test_domain_preserved_with_adults(self):
        url = google_flights_url(
            "YVR", "NRT", date(2026, 11, 1), currency="CAD", adults=2,
        )
        assert url.startswith(DOMAIN)

    def test_currency_preserved_with_adults(self):
        url = google_flights_url(
            "YVR", "NRT", date(2026, 11, 1), currency="CAD", adults=2,
        )
        assert get_param(url, "curr") == "CAD"

    def test_single_adult_no_clause(self):
        """adults=1 must produce no 'with N adults' clause."""
        url = google_flights_url(
            "YVR", "NRT", date(2026, 11, 1), currency="CAD", adults=1,
        )
        q = decode_q(url)
        assert "adults" not in q
