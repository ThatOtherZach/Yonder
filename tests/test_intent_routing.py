"""Regression tests for A→B detection with arrival phrasing (vs getaways)."""

from yonder.grok import detect_route_iatas, looks_like_open_getaway
from yonder.intent import decide_shape, extract_route_cities, looks_like_a_to_b

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
