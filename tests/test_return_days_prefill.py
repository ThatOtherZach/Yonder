"""Task 502 — Find Return date-picker pre-fill round-trip.

Verifies the full chain:
  user_prefs.db  →  _compute_return_days()  →  data-rt-days attribute

Two scenarios:
  1. RETURN_DAYS=21  →  data-rt-days="21"  (explicit setting flows through)
  2. RETURN_DAYS=0   →  data-rt-days = detour_min_stop_days + detour_max_stop_days
                        (zero falls back to the per-user stopover range)
"""

from __future__ import annotations

import re

import pytest

import yonder.user_prefs as prefs_module
import yonder.web as web_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_macro(template_src: str, **ctx) -> str:
    """Render an inline Jinja2 snippet using the app's configured env."""
    env = web_module.templates.env
    tmpl = env.from_string(template_src)
    return tmpl.render(**ctx)


def _escape_html_with_return_days(return_days: int) -> str:
    """Render the escape_card macro in explore mode, injecting return_days."""
    from datetime import date, timedelta

    from yonder.types import FlightOffer, SearchQuery

    offer = FlightOffer(
        provider="mock",
        price=450.0,
        currency="USD",
        airlines=["UA"],
        stops_out=0,
        price_kind="mock",
        display_price="~USD 450",
        display_price_base="~USD 450",
    )
    query = SearchQuery(
        origin="YVR",
        destination="NRT",
        depart_date=date.today() + timedelta(days=30),
        return_date=date.today() + timedelta(days=37),
        adults=1,
        currency="USD",
    )
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.escape_card('explore', o, query, 0, return_days=return_days) }}",
        o=offer,
        query=query,
        return_days=return_days,
    )


def _detour_html_with_return_days(return_days: int) -> str:
    """Render the detour_card macro in explore mode, injecting return_days."""
    from datetime import date, timedelta

    from yonder.adventure import AdventureItinerary, PricedLeg
    from yonder.types import FlightOffer

    leg1 = PricedLeg(
        from_iata="YVR",
        to_iata="TYO",
        depart_date=date.today() + timedelta(days=30),
        offer=FlightOffer(
            provider="mock",
            price=450.0,
            currency="USD",
            airlines=["JL"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    leg2 = PricedLeg(
        from_iata="TYO",
        to_iata="YVR",
        depart_date=date.today() + timedelta(days=37),
        offer=FlightOffer(
            provider="mock",
            price=420.0,
            currency="USD",
            airlines=["JL"],
            stops_out=0,
            price_kind="mock",
        ),
    )
    it = AdventureItinerary(
        kind="stopover",
        title="Tokyo Detour",
        total_price=870.0,
        currency="USD",
        stop_iata="TYO",
        stop_city="Tokyo",
        stay_days=7,
        why="Great city for a stopover",
        vibe_tags=["adventure"],
        legs=[leg1, leg2],
        theme_primary="#e6b450",
        theme_label="Adventure",
    )
    return _render_macro(
        "{% import '_boarding_pass.html' as bp %}"
        "{{ bp.detour_card('explore', it, 0, det_vibe='adventure', det_text='test', return_days=return_days) }}",
        it=it,
        return_days=return_days,
    )


# ---------------------------------------------------------------------------
# Tests: _compute_return_days() unit behaviour
# ---------------------------------------------------------------------------


class TestComputeReturnDays:
    """_compute_return_days() must respect the stored preference and fallback."""

    def test_explicit_value_returned_unchanged(self, monkeypatch):
        """return_days=21 → _compute_return_days() returns 21."""
        monkeypatch.setattr(
            prefs_module,
            "get_all_prefs",
            lambda: {
                **prefs_module.PREF_DEFAULTS,
                "return_days": "21",
            },
        )
        # Also patch the lazy import inside _compute_return_days
        import yonder.user_prefs as up_mod
        monkeypatch.setattr(up_mod, "get_all_prefs", lambda: {
            **prefs_module.PREF_DEFAULTS,
            "return_days": "21",
        })
        result = web_module._compute_return_days()
        assert result == 21, (
            f"Expected 21 when return_days=21, got {result}"
        )

    def test_zero_falls_back_to_min_plus_max(self, monkeypatch):
        """return_days=0 → detour_min_stop_days + detour_max_stop_days."""
        fake_prefs = {
            **prefs_module.PREF_DEFAULTS,
            "return_days": "0",
            "detour_min_stop_days": "3",
            "detour_max_stop_days": "6",
        }

        import yonder.user_prefs as up_mod
        monkeypatch.setattr(up_mod, "get_all_prefs", lambda: fake_prefs)

        result = web_module._compute_return_days()
        assert result == 9, (
            f"Expected 3+6=9 when return_days=0 with min=3 max=6, got {result}"
        )

    def test_zero_with_default_stop_days(self, monkeypatch):
        """return_days=0 with factory defaults (min=4, max=5) → 9."""
        fake_prefs = {
            **prefs_module.PREF_DEFAULTS,
            "return_days": "0",
        }

        import yonder.user_prefs as up_mod
        monkeypatch.setattr(up_mod, "get_all_prefs", lambda: fake_prefs)

        result = web_module._compute_return_days()
        assert result == 9, (
            f"Expected 4+5=9 from factory defaults when return_days=0, got {result}"
        )

    def test_value_clamped_at_365(self, monkeypatch):
        """return_days beyond 365 is clamped to 365."""
        import yonder.user_prefs as up_mod
        monkeypatch.setattr(up_mod, "get_all_prefs", lambda: {
            **prefs_module.PREF_DEFAULTS,
            "return_days": "999",
        })
        result = web_module._compute_return_days()
        assert result == 365, (
            f"Expected 365 (clamped) for return_days=999, got {result}"
        )


# ---------------------------------------------------------------------------
# Tests: data-rt-days attribute in the boarding pass template
# ---------------------------------------------------------------------------


class TestBoardingPassDataRtDays:
    """The data-rt-days attribute must reflect the return_days context value."""

    def test_escape_card_emits_rt_days_21(self):
        """Escape card in explore mode must carry data-rt-days="21"."""
        html = _escape_html_with_return_days(21)
        assert 'data-rt-days="21"' in html, (
            "data-rt-days='21' missing from escape card when return_days=21"
        )

    def test_escape_card_emits_rt_days_9(self):
        """Escape card carries the fallback value (min+max=9) as data-rt-days."""
        html = _escape_html_with_return_days(9)
        assert 'data-rt-days="9"' in html, (
            "data-rt-days='9' missing from escape card when return_days=9"
        )

    def test_detour_card_emits_rt_days_21(self):
        """Detour card in explore mode must carry data-rt-days="21"."""
        html = _detour_html_with_return_days(21)
        assert 'data-rt-days="21"' in html, (
            "data-rt-days='21' missing from detour card when return_days=21"
        )

    def test_detour_card_emits_rt_days_9(self):
        """Detour card carries the fallback value (min+max=9) as data-rt-days."""
        html = _detour_html_with_return_days(9)
        assert 'data-rt-days="9"' in html, (
            "data-rt-days='9' missing from detour card when return_days=9"
        )


# ---------------------------------------------------------------------------
# Tests: full round-trip via settings POST → _compute_return_days()
# ---------------------------------------------------------------------------


class TestSettingsRoundTrip:
    """Saving RETURN_DAYS via the settings form must change what
    _compute_return_days() reports, confirming the complete data path."""

    @pytest.fixture()
    def isolated_prefs(self, tmp_path, monkeypatch):
        """Redirect user_prefs.db to a throwaway SQLite file."""
        tmp_db = tmp_path / "user_prefs.db"
        monkeypatch.setattr(prefs_module, "DB_PATH", tmp_db)
        # Invalidate the module-level read cache so the new DB is used
        prefs_module._invalidate()
        yield tmp_db
        # Ensure cache is cleared after test too
        prefs_module._invalidate()

    def test_return_days_21_flows_to_compute(self, isolated_prefs, monkeypatch):
        """Saving return_days=21 → _compute_return_days() == 21."""
        prefs_module.set_prefs({"return_days": "21"})

        # _compute_return_days imports get_all_prefs inside the function;
        # the patched DB_PATH means it reads from the tmp file.
        result = web_module._compute_return_days()
        assert result == 21, (
            f"After saving return_days=21, _compute_return_days() returned {result}"
        )

    def test_return_days_0_uses_stop_range(self, isolated_prefs, monkeypatch):
        """Saving return_days=0 + stop range → _compute_return_days() == min+max."""
        prefs_module.set_prefs({
            "return_days": "0",
            "detour_min_stop_days": "3",
            "detour_max_stop_days": "7",
        })

        result = web_module._compute_return_days()
        assert result == 10, (
            f"After saving return_days=0 with min=3/max=7, "
            f"_compute_return_days() returned {result}, expected 10"
        )

    def test_return_days_21_appears_in_escape_card(self, isolated_prefs):
        """End-to-end: saved return_days=21 produces data-rt-days="21" in HTML."""
        prefs_module.set_prefs({"return_days": "21"})
        computed = web_module._compute_return_days()

        html = _escape_html_with_return_days(computed)
        assert 'data-rt-days="21"' in html, (
            "End-to-end: data-rt-days='21' missing from escape card "
            "after saving return_days=21 in user_prefs"
        )

    def test_return_days_0_fallback_appears_in_escape_card(self, isolated_prefs):
        """End-to-end: saved return_days=0 produces data-rt-days=min+max in HTML."""
        prefs_module.set_prefs({
            "return_days": "0",
            "detour_min_stop_days": "4",
            "detour_max_stop_days": "5",
        })
        computed = web_module._compute_return_days()
        expected = 9  # 4 + 5

        assert computed == expected, (
            f"_compute_return_days() returned {computed}, expected {expected}"
        )
        html = _escape_html_with_return_days(computed)
        assert f'data-rt-days="{expected}"' in html, (
            f"data-rt-days='{expected}' missing from escape card "
            "after saving return_days=0 with min=4/max=5"
        )
