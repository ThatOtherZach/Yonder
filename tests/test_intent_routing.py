"""Regression tests for A→B detection with arrival phrasing (vs getaways)."""

from yonder.grok import detect_route_iatas, looks_like_open_getaway
from yonder.intent import (
    decide_shape,
    extract_route_cities,
    looks_like_a_to_b,
    looks_like_stop_off_route,
    looks_like_stopover_intent,
)

REPORTED = "I'll leave Vancouver and would like to be in London after a three day stop"


class TestArrivalPhrasingIsAtoB:
    def test_reported_prompt_is_a_to_b(self):
        assert looks_like_a_to_b(REPORTED)
        assert extract_route_cities(REPORTED) == ("vancouver", "london")

    def test_reported_prompt_maps_to_distinct_iatas(self):
        assert detect_route_iatas(REPORTED) == ("YVR", "LHR")

    def test_reported_prompt_shape_not_pure_escape_getaway(self):
        d = decide_shape(REPORTED)
        assert d.shape in ("mix", "detour")
        assert looks_like_a_to_b(REPORTED)

    def test_arrival_variants(self):
        variants = [
            "depart Toronto and end in Paris",
            "leaving Montreal, want to arrive in London by June",
            "fly out of Calgary and land in New York",
            "leave Seattle then get to Paris eventually",
            "depart from Vancouver and be in Toronto for the weekend",
        ]
        for p in variants:
            assert looks_like_a_to_b(p), p
            route = detect_route_iatas(p)
            assert route is not None, p
            assert route[0] != route[1], p

    def test_classic_from_to_still_works(self):
        assert extract_route_cities("from Vancouver to London") == ("vancouver", "london")
        assert detect_route_iatas("Vancouver to London with a stopover") == ("YVR", "LHR")
        assert extract_route_cities("YVR to LHR") == ("yvr", "lhr")
        assert detect_route_iatas("YVR -> LHR") == ("YVR", "LHR")


class TestJourneyPhrasingRoutesThroughVibeStop:
    """Journey/arrival phrasing must classify as detour (origin → vibe stop
    → destination), not mix (which shows a round trip first)."""

    def test_journey_phrasing_is_detour(self):
        prompts = [
            "leave Vancouver and end up in Rome",
            "depart Toronto and arrive in Paris",
            "fly out of Calgary and wind up in Lisbon",
            "leaving Montreal, arriving in London",
            "leave Seattle and finish in Tokyo",
            "depart Berlin, land in Helsinki",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "detour", (p, d)

    def test_journey_phrasing_plus_stopover_markers_is_mix(self):
        prompts = [
            "leave Vancouver and end up in Rome via Lisbon",
            "depart Toronto, arrive in Paris with a stopover",
            "fly out of Calgary and wind up in Lisbon with a few days somewhere",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "mix", (p, d)

    def test_plain_point_to_point_ask_is_mix(self):
        prompts = [
            "how do I get to Rome from Vancouver",
            "how do I get to Paris from Toronto",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "mix", (p, d)

    def test_direct_nonstop_wording_is_escape(self):
        prompts = [
            "nonstop Vancouver to Rome",
            "direct flight from Toronto to Paris",
            "cheapest direct YVR to LHR",
            "one way only from Seattle to Tokyo",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "escape", (p, d)


class TestGetawaysStayGetaways:
    def test_open_getaway_prompts(self):
        prompts = [
            "get out of Vancouver, somewhere new",
            "get me out of Toronto for a few days",
            "cheap getaway from Montreal, wherever is fun",
            "somewhere I haven't been, leaving Vancouver",
        ]
        for p in prompts:
            assert not looks_like_a_to_b(p), p
            assert extract_route_cities(p) is None, p
            assert detect_route_iatas(p) is None, p

    def test_open_getaway_shape_is_detour_roundtrip(self):
        d = decide_shape("get out of Vancouver, somewhere new")
        assert d.shape == "detour"
        assert looks_like_open_getaway("get out of Vancouver, somewhere new")

    def test_open_place_words_rejected_as_arrival(self):
        assert extract_route_cities("leave Vancouver and be in town by Friday") is None
        assert extract_route_cities("leave Vancouver and get to somewhere warm") is None
        assert extract_route_cities("depart Vancouver, end up in Vancouver again") is None


class TestSameCityNeverReturned:
    def test_route_iatas_never_same(self):
        route = detect_route_iatas(REPORTED)
        assert route and route[0] != route[1]

    def test_same_city_named_twice_gives_none(self):
        assert detect_route_iatas("leave London and arrive in London") is None


class TestBroadCityResolution:
    """Cities beyond the small _HOME_CITY_IATA hint list resolve via dataset."""

    def test_unknown_cities_resolve_to_distinct_airports(self):
        cases = {
            "leave Berlin and arrive in Tokyo": ("BER", "NRT"),
            "fly out of Nairobi and end up in Lisbon": ("NBO", "LIS"),
            "from Marrakech to Reykjavik": ("RAK", "KEF"),
            "leave Osaka, land in Helsinki": ("KIX", "HEL"),
        }
        for prompt, expected in cases.items():
            route = detect_route_iatas(prompt)
            assert route == expected, (prompt, route)
            assert route[0] != route[1]

    def test_hint_list_cities_unchanged(self):
        assert detect_route_iatas("from Vancouver to Portland") == ("YVR", "PDX")
        assert detect_route_iatas("leave New York and arrive in Paris") == ("JFK", "CDG")
        assert detect_route_iatas("London to Tokyo") == ("LHR", "NRT")

    def test_iata_pair_still_works(self):
        assert detect_route_iatas("YVR to NRT") == ("YVR", "NRT")

    def test_unknown_tokens_and_same_city_still_none(self):
        assert detect_route_iatas("leave Berlin and arrive in Berlin") is None
        assert detect_route_iatas("get out of town somewhere new") is None


class TestTruthTable:
    """Each row of the Escape vs Detour routing model (see yonder/intent.py)."""

    def test_destination_plus_direct_language_is_escape(self):
        d = decide_shape("nonstop Vancouver to Rome")
        assert d.shape == "escape"

    def test_plain_a_to_b_is_mix_directs_first(self):
        d = decide_shape("Vancouver to Rome")
        assert d.shape == "mix"

    def test_no_destination_is_detour(self):
        d = decide_shape("somewhere new, get me out of here")
        assert d.shape == "detour"

    def test_origin_only_is_detour_roundtrip(self):
        d = decide_shape("get me out of Vancouver")
        assert d.shape == "detour"

    def test_origin_dest_journey_phrasing_is_detour(self):
        d = decide_shape("leave Vancouver and end up in Rome")
        assert d.shape == "detour"

    def test_typo_city_falls_back_gracefully(self):
        # "Vancover" won't resolve to an IATA — route mapping returns None
        # but shape decision still lands on a sane shape, never crashes.
        assert detect_route_iatas("leave Vancover and end up in Romee") is None
        d = decide_shape("leave Vancover and end up in Romee")
        assert d.shape in ("escape", "detour", "mix")


class TestVibePriorTieBreaker:
    """Vibe leans the shape ONLY for ambiguous prompts; explicit signals win."""

    AMBIGUOUS = "best croissants and museums"

    def test_wander_vibe_leans_detour(self):
        for vibe in ("adventure", "chaotic", "budget", "slow-travel"):
            d = decide_shape(self.AMBIGUOUS, vibe=vibe, demo=True)
            assert d.shape == "detour", (vibe, d)

    def test_comfort_vibe_leans_escape(self):
        for vibe in ("luxury", "romantic", "relaxing"):
            d = decide_shape(self.AMBIGUOUS, vibe=vibe, demo=True)
            assert d.shape == "escape", (vibe, d)

    def test_neutral_vibe_stays_mix(self):
        d = decide_shape(self.AMBIGUOUS, vibe="city", demo=True)
        assert d.shape == "mix"

    def test_explicit_direct_language_beats_wander_prior(self):
        d = decide_shape("direct flight from Vancouver to Rome", vibe="adventure", demo=True)
        assert d.shape == "escape"

    def test_explicit_getaway_beats_comfort_prior(self):
        d = decide_shape("get me out of Vancouver, somewhere new", vibe="luxury", demo=True)
        assert d.shape == "detour"

    def test_journey_phrasing_beats_comfort_prior(self):
        d = decide_shape("leave Vancouver and end up in Rome", vibe="luxury", demo=True)
        assert d.shape == "detour"

    def test_learned_lean_can_flip_static_prior(self, monkeypatch):
        import yonder.vibe_signals as vs
        # Strong learned escape lean on a wander vibe cancels + flips nothing
        # below threshold, but a neutral vibe with learned detour lean flips.
        monkeypatch.setattr(vs, "shape_lean_for_vibe", lambda v, demo=False: 1.0)
        d = decide_shape(self.AMBIGUOUS, vibe="city")
        assert d.shape == "detour"
        monkeypatch.setattr(vs, "shape_lean_for_vibe", lambda v, demo=False: -1.0)
        d = decide_shape(self.AMBIGUOUS, vibe="city")
        assert d.shape == "escape"

    def test_forced_shape_beats_everything(self):
        d = decide_shape(self.AMBIGUOUS, vibe="adventure", force="escape", demo=True)
        assert d.shape == "escape" and d.forced


class TestStopOffRouting:
    """'Stop off in X then go to Y' must produce detour shape, not mix."""

    REPORTED = (
        "Stop off in Tokyo and then go to Hong Kong, "
        "looking for some party stuff in Tokyo - clubs"
    )

    def test_reported_prompt_is_detour(self):
        d = decide_shape(self.REPORTED)
        assert d.shape == "detour", d

    def test_reported_prompt_extracts_route_cities(self):
        route = extract_route_cities(self.REPORTED)
        assert route is not None, "expected (tokyo, hong kong)"
        assert route[0] == "tokyo"
        assert route[1] == "hong kong"

    def test_stop_off_markers_detected(self):
        """All new stop-off phrasings must trigger looks_like_stopover_intent."""
        phrasings = [
            "stop off in Tokyo",
            "stopping off in Berlin",
            "stop over in Paris",
            "stop-over in Lisbon",
            "make a stop in Dubai",
            "pass through Singapore",
            "swing through Bangkok",
            "swing by Seoul",
        ]
        for phrase in phrasings:
            assert looks_like_stopover_intent(phrase), phrase

    def test_stop_off_route_regex_variants(self):
        """Structural 'X then go to Y' patterns must be detected."""
        variants = [
            "stop off in Tokyo then go to Hong Kong",
            "stopping off in Berlin then head to Paris",
            "stop in Lisbon then fly to Madrid",
            "stop off in Dubai then travel to Mumbai",
        ]
        for v in variants:
            assert looks_like_stop_off_route(v), v

    def test_stop_off_route_shape_is_detour_not_mix(self):
        """All stop-off-route variants must route to detour."""
        prompts = [
            "stop off in Tokyo then go to Hong Kong",
            "stopping off in Berlin then head to Paris",
            "stop in Lisbon then fly to Madrid",
            "Stop off in Tokyo and then go to Hong Kong, looking for party clubs in Tokyo",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "detour", (p, d)

    def test_stop_off_route_beats_vibe_prior(self):
        """stop-off route wins even when a comfort vibe would lean escape."""
        d = decide_shape(
            "stop off in Tokyo then go to Hong Kong",
            vibe="luxury",
            demo=True,
        )
        assert d.shape == "detour", d

    def test_generic_stopover_language_still_mix(self):
        """Plain A→B with generic stop language stays mix (not detour)."""
        prompts = [
            "Vancouver to Rome via Lisbon",
            "from Toronto to Paris with a stopover",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "mix", (p, d)

    def test_swing_pass_route_regex_variants(self):
        """swing-by / pass-through structural patterns must trigger looks_like_stop_off_route."""
        variants = [
            "swing by Tokyo then go to Hong Kong",
            "swing by Tokyo on the way to Hong Kong",
            "swing through Berlin then head to Paris",
            "swing through Berlin on the way to Paris",
            "pass through Singapore then head to Bangkok",
            "pass through Singapore on the way to Bangkok",
            "passing through Seoul then fly to Tokyo",
        ]
        for v in variants:
            assert looks_like_stop_off_route(v), v

    def test_swing_pass_route_shape_is_detour(self):
        """All swing-by / pass-through route variants must produce detour shape."""
        prompts = [
            "swing by Tokyo then go to Hong Kong",
            "swing by Tokyo on the way to Hong Kong",
            "swing through Berlin then head to Paris",
            "swing through Berlin on the way to Paris",
            "pass through Singapore then head to Bangkok",
            "pass through Singapore on the way to Bangkok",
        ]
        for p in prompts:
            d = decide_shape(p)
            assert d.shape == "detour", (p, d)

    def test_swing_pass_route_beats_comfort_vibe(self):
        """swing-by route wins even when a comfort vibe would lean escape."""
        d = decide_shape(
            "swing by Tokyo on the way to Hong Kong",
            vibe="luxury",
            demo=True,
        )
        assert d.shape == "detour", d
