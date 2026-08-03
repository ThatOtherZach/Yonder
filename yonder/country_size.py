"""Static country size data and a normalized "country scale" score.

Used to scale the domestic-seed boost by how big the traveler's home
country is: a Canadian gets a stronger domestic nudge than a Luxembourger
because "domestic" covers vastly more ground.

The score blends land area and population on log scales, calibrated so a
medium-sized country (France) sits exactly at 0.5 — the midpoint where the
domestic boost behaves like the historical flat boost.
"""
from __future__ import annotations

import math

# ISO2 -> (land area km², population).  Figures are approximate (rounded,
# early-2020s vintage); only the order of magnitude matters for the blend.
COUNTRY_SIZE: dict[str, tuple[int, int]] = {
    "AD": (468, 80000),
    "AE": (83600, 9900000),
    "AF": (652230, 41000000),
    "AG": (443, 94000),
    "AL": (28748, 2800000),
    "AM": (29743, 2800000),
    "AO": (1246700, 35000000),
    "AR": (2780400, 46000000),
    "AT": (83871, 9000000),
    "AU": (7692024, 26000000),
    "AZ": (86600, 10200000),
    "BA": (51197, 3200000),
    "BB": (430, 282000),
    "BD": (147570, 171000000),
    "BE": (30528, 11700000),
    "BF": (272967, 22700000),
    "BG": (110879, 6800000),
    "BH": (778, 1500000),
    "BI": (27834, 12900000),
    "BJ": (114763, 13400000),
    "BN": (5765, 450000),
    "BO": (1098581, 12200000),
    "BR": (8515767, 215000000),
    "BS": (13943, 410000),
    "BT": (38394, 780000),
    "BW": (581730, 2600000),
    "BY": (207600, 9200000),
    "BZ": (22966, 410000),
    "CA": (9984670, 39000000),
    "CD": (2344858, 99000000),
    "CF": (622984, 5600000),
    "CG": (342000, 5800000),
    "CH": (41284, 8800000),
    "CI": (322463, 28200000),
    "CL": (756102, 19600000),
    "CM": (475442, 27900000),
    "CN": (9596961, 1412000000),
    "CO": (1141748, 52000000),
    "CR": (51100, 5200000),
    "CU": (109884, 11200000),
    "CV": (4033, 590000),
    "CY": (9251, 1250000),
    "CZ": (78865, 10500000),
    "DE": (357114, 84000000),
    "DJ": (23200, 1100000),
    "DK": (43094, 5900000),
    "DM": (751, 73000),
    "DO": (48671, 11200000),
    "DZ": (2381741, 44900000),
    "EC": (276841, 18000000),
    "EE": (45227, 1330000),
    "EG": (1002450, 110000000),
    "ER": (117600, 3700000),
    "ES": (505992, 47800000),
    "ET": (1104300, 123000000),
    "FI": (338424, 5600000),
    "FJ": (18272, 930000),
    "FM": (702, 114000),
    "FR": (551695, 68000000),
    "GA": (267668, 2400000),
    "GB": (242495, 67000000),
    "GD": (344, 125000),
    "GE": (69700, 3700000),
    "GH": (238533, 33500000),
    "GM": (11295, 2700000),
    "GN": (245857, 13900000),
    "GQ": (28051, 1700000),
    "GR": (131957, 10400000),
    "GT": (108889, 17600000),
    "GW": (36125, 2100000),
    "GY": (214969, 810000),
    "HK": (1104, 7300000),
    "HN": (112492, 10400000),
    "HR": (56594, 3900000),
    "HT": (27750, 11600000),
    "HU": (93028, 9600000),
    "ID": (1904569, 275000000),
    "IE": (70273, 5100000),
    "IL": (20770, 9600000),
    "IN": (3287263, 1417000000),
    "IQ": (438317, 44500000),
    "IR": (1648195, 89000000),
    "IS": (103000, 380000),
    "IT": (301336, 59000000),
    "JM": (10991, 2800000),
    "JO": (89342, 11300000),
    "JP": (377930, 125000000),
    "KE": (580367, 54000000),
    "KG": (199951, 6800000),
    "KH": (181035, 16800000),
    "KI": (811, 130000),
    "KM": (2235, 840000),
    "KN": (261, 48000),
    "KP": (120538, 26000000),
    "KR": (100210, 51700000),
    "KW": (17818, 4300000),
    "KZ": (2724900, 19600000),
    "LA": (236800, 7500000),
    "LB": (10452, 5500000),
    "LC": (539, 180000),
    "LI": (160, 39000),
    "LK": (65610, 22200000),
    "LR": (111369, 5300000),
    "LS": (30355, 2300000),
    "LT": (65300, 2800000),
    "LU": (2586, 660000),
    "LV": (64559, 1880000),
    "LY": (1759540, 6800000),
    "MA": (446550, 37500000),
    "MC": (2, 36000),
    "MD": (33846, 2600000),
    "ME": (13812, 620000),
    "MG": (587041, 29600000),
    "MH": (181, 42000),
    "MK": (25713, 2100000),
    "ML": (1240192, 22600000),
    "MM": (676578, 54000000),
    "MN": (1564110, 3400000),
    "MO": (33, 700000),
    "MR": (1030700, 4700000),
    "MT": (316, 530000),
    "MU": (2040, 1260000),
    "MV": (300, 520000),
    "MW": (118484, 20400000),
    "MX": (1964375, 128000000),
    "MY": (330803, 34000000),
    "MZ": (801590, 33000000),
    "NA": (825615, 2570000),
    "NE": (1267000, 26200000),
    "NG": (923768, 219000000),
    "NI": (130373, 6900000),
    "NL": (41850, 17700000),
    "NO": (323802, 5500000),
    "NP": (147181, 30500000),
    "NR": (21, 12000),
    "NZ": (270467, 5100000),
    "OM": (309500, 4600000),
    "PA": (75417, 4400000),
    "PE": (1285216, 34000000),
    "PG": (462840, 10100000),
    "PH": (300000, 115000000),
    "PK": (881913, 236000000),
    "PL": (312679, 37000000),
    "PS": (6020, 5300000),
    "PT": (92090, 10300000),
    "PW": (459, 18000),
    "PY": (406752, 6800000),
    "QA": (11586, 2700000),
    "RO": (238391, 19000000),
    "RS": (88361, 6600000),
    "RU": (17098242, 144000000),
    "RW": (26338, 13800000),
    "SA": (2149690, 36400000),
    "SB": (28896, 720000),
    "SC": (452, 100000),
    "SD": (1861484, 46900000),
    "SE": (450295, 10500000),
    "SG": (728, 5600000),
    "SI": (20273, 2100000),
    "SK": (49037, 5400000),
    "SL": (71740, 8600000),
    "SM": (61, 34000),
    "SN": (196722, 17300000),
    "SO": (637657, 17600000),
    "SR": (163820, 620000),
    "SS": (644329, 10900000),
    "ST": (964, 230000),
    "SV": (21041, 6300000),
    "SY": (185180, 22100000),
    "SZ": (17364, 1200000),
    "TD": (1284000, 17700000),
    "TG": (56785, 8800000),
    "TH": (513120, 71700000),
    "TJ": (143100, 10000000),
    "TL": (14874, 1340000),
    "TM": (488100, 6400000),
    "TN": (163610, 12400000),
    "TO": (747, 107000),
    "TR": (783562, 85000000),
    "TT": (5130, 1530000),
    "TV": (26, 11000),
    "TW": (36193, 23900000),
    "TZ": (947303, 65500000),
    "UA": (603500, 38000000),
    "UG": (241550, 47200000),
    "US": (9833517, 333000000),
    "UY": (181034, 3400000),
    "UZ": (447400, 35600000),
    "VA": (0, 800),
    "VC": (389, 104000),
    "VE": (916445, 28300000),
    "VN": (331212, 98200000),
    "VU": (12189, 330000),
    "WS": (2842, 220000),
    "YE": (527968, 33700000),
    "ZA": (1221037, 60000000),
    "ZM": (752618, 20000000),
    "ZW": (390757, 16300000),
}

# Calibration: France defines the midpoint (scale = 0.5) so medium-sized
# countries keep the historical flat boost exactly.  One blend unit is one
# order of magnitude in the (log-area + log-pop)/2 average; SPAN maps ±2
# blend units to the full 0..1 range.
_REF_BLEND = (math.log10(551695) + math.log10(68000000)) / 2.0  # France ≈ 6.79
_SPAN = 4.0


def _blend(area_km2: float, population: float) -> float:
    a = math.log10(max(area_km2, 1.0))
    p = math.log10(max(population, 1.0))
    return (a + p) / 2.0


# Historical flat domestic boost — still the exact strength at scale 0.5.
DOMESTIC_BOOST_BASE = 3


def domestic_boost_points(cc: str | None) -> int:
    """Domestic-seed boost points scaled by home-country size.

    Linear in the country scale score with the historical +3 as the
    midpoint: microstates bottom out at 1 point (a domestic option never
    entirely loses its nudge while the boost is active), continent-scale
    countries reach ~6.
    """
    return max(1, round(DOMESTIC_BOOST_BASE * 2 * country_scale(cc)))


def country_scale(cc: str | None) -> float:
    """Normalized 0–1 size score for an ISO2 country code.

    0 ≈ microstates (Luxembourg and smaller), 0.5 = medium (France),
    1 ≈ continent-scale (Russia/Canada/US saturate near the top).
    Unknown or missing codes return 0.5 so behavior matches the historical
    flat boost rather than silently zeroing it.
    """
    if not cc:
        return 0.5
    entry = COUNTRY_SIZE.get(cc.strip().upper())
    if entry is None:
        return 0.5
    area, pop = entry
    s = 0.5 + (_blend(area, pop) - _REF_BLEND) / _SPAN
    return max(0.0, min(1.0, s))
