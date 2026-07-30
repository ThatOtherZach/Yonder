"""Snapshot tests for the compact ground-cost display strings.

Covers both paths that produce ground_* fields for a trip result:
- Settings-bag path: settings_ground_fields() → ground_display
- Grok city-COL path: compare_for_stop()/build_compare() → ground_compare_line
"""
from __future__ import annotations

from yonder.config import Settings
from yonder.daily_costs import compare_for_stop, settings_ground_fields


def _settings(daily: float) -> Settings:
    return Settings(
        col_expected_daily=daily,
        col_tolerance_pct=25.0,
        _env_file=None,
    )


class TestSettingsBagGroundDisplay:
    def test_ground_display_compact_format(self):
        fields, notes = settings_ground_fields(
            _settings(120.0),
            stay_days=6,
            currency="USD",
            stop_label="Lisbon (LIS)",
        )
        assert fields["ground_display"] == "+~$720 (~$120 per Day for 6 Days)"
        assert fields["ground_total"] == 720.0
        assert fields["ground_budget_status"] == "within"
        # compare line still references the Settings bag, not city COL
        assert "Your Settings day bag for Lisbon (LIS)" in fields["ground_compare_line"]
        assert notes  # note lines present for the itinerary

    def test_ground_display_cad_symbol(self):
        fields, _ = settings_ground_fields(
            _settings(100.0),
            stay_days=3,
            currency="CAD",
        )
        assert fields["ground_display"] == "+~C$300 (~C$100 per Day for 3 Days)"

    def test_unset_bag_returns_empty_fields(self):
        fields, notes = settings_ground_fields(
            _settings(0.0), stay_days=4, currency="USD"
        )
        assert fields == {}
        assert any("Ground COL off" in n for n in notes)


class TestGrokCityColCompareLine:
    def _batch(self, *, daily_origin: float, daily_stop: float, **extra):
        batch = {
            "origin_cc": "CA",
            "iata_to_cc": {"AUS": "US"},
            "payloads_by_cc": {
                "US": {
                    "daily_origin": daily_origin,
                    "daily_stop": daily_stop,
                    "currency": "USD",
                    "source": "grok",
                    "blurb": "",
                }
            },
        }
        batch.update(extra)
        return batch

    def test_ground_compare_line_compact_format(self):
        gcmp = compare_for_stop(
            self._batch(daily_origin=110.0, daily_stop=125.0),
            stop_iata="AUS",
            stay_days=6,
        )
        assert gcmp is not None
        assert gcmp.ground_compare_line == "~$125/Day vs. ~$110/Day (Canada)"
        # itinerary ground_display is composed from these display fields
        assert gcmp.display_ground == "~$750"
        assert gcmp.display_daily_stop == "~$125"
        assert gcmp.display_daily_origin == "~$110"

    def test_ground_compare_line_same_with_budget(self):
        # With a budget the compare line keeps the same compact format
        gcmp = compare_for_stop(
            self._batch(daily_origin=110.0, daily_stop=125.0),
            stop_iata="AUS",
            stay_days=6,
            budget_daily=130.0,
            budget_tolerance_pct=25.0,
        )
        assert gcmp is not None
        assert gcmp.ground_compare_line == "~$125/Day vs. ~$110/Day (Canada)"
        assert gcmp.budget_status == "under"
        assert gcmp.budget_line.startswith("Budget: ~$125/day under")

    def test_itinerary_ground_display_composition(self):
        # adventure.py builds: f"+{display_ground} ({display_daily_stop} per Day for {stay} Days)"
        gcmp = compare_for_stop(
            self._batch(daily_origin=110.0, daily_stop=120.0),
            stop_iata="AUS",
            stay_days=6,
        )
        assert gcmp is not None
        line = (
            f"+{gcmp.display_ground} ({gcmp.display_daily_stop} per Day "
            f"for {gcmp.stay_days} Days)"
        )
        assert line == "+~$720 (~$120 per Day for 6 Days)"
