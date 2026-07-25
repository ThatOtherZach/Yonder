"""Lean daily ground costs — decent hotel + food/drink + basic transit + 1–2 culture.

Estimates prefer local-currency day bags, then convert to the user's currency
via mid-market FX (Frankfurter). Cached 60 days under lean_v1 keys.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import httpx

from yonder.config import ROOT, Settings
from yonder.countries import COUNTRY_NAME, country_for_iata
from yonder.currency import convert_amount
from yonder.money import format_approx

DB_PATH = ROOT / "daily_costs_cache.db"
CACHE_TTL_SEC = 60 * 24 * 3600  # 60 days
CACHE_VERSION = "lean_v1"

STYLE = "lean day bag"
INCLUDES = (
    "1 night decent hotel (3★/midscale), food & drink for the day, "
    "basic local transit if any, budget for 1–2 simple cultural things"
)

# Typical local currency per ISO2 (for Grok + FX). EUR for eurozone-ish.
_LOCAL_CCY: dict[str, str] = {
    "AF": "AFN",
    "AL": "ALL",
    "DZ": "DZD",
    "AR": "ARS",
    "AU": "AUD",
    "AT": "EUR",
    "BE": "EUR",
    "BR": "BRL",
    "BG": "BGN",
    "KH": "KHR",
    "CA": "CAD",
    "CL": "CLP",
    "CN": "CNY",
    "CO": "COP",
    "HR": "EUR",
    "CU": "CUP",
    "CY": "EUR",
    "CZ": "CZK",
    "DK": "DKK",
    "DO": "DOP",
    "EC": "USD",
    "EG": "EGP",
    "EE": "EUR",
    "ET": "ETB",
    "FI": "EUR",
    "FR": "EUR",
    "DE": "EUR",
    "GH": "GHS",
    "GR": "EUR",
    "HK": "HKD",
    "HU": "HUF",
    "IS": "ISK",
    "IN": "INR",
    "ID": "IDR",
    "IR": "IRR",
    "IQ": "IQD",
    "IE": "EUR",
    "IL": "ILS",
    "IT": "EUR",
    "JM": "JMD",
    "JP": "JPY",
    "JO": "JOD",
    "KE": "KES",
    "KR": "KRW",
    "KW": "KWD",
    "LV": "EUR",
    "LB": "LBP",
    "LT": "EUR",
    "LU": "EUR",
    "MY": "MYR",
    "MV": "MVR",
    "MX": "MXN",
    "MA": "MAD",
    "NL": "EUR",
    "NZ": "NZD",
    "NG": "NGN",
    "NO": "NOK",
    "OM": "OMR",
    "PK": "PKR",
    "PA": "USD",
    "PE": "PEN",
    "PH": "PHP",
    "PL": "PLN",
    "PT": "EUR",
    "QA": "QAR",
    "RO": "RON",
    "RU": "RUB",
    "SA": "SAR",
    "RS": "RSD",
    "SG": "SGD",
    "SK": "EUR",
    "SI": "EUR",
    "ZA": "ZAR",
    "ES": "EUR",
    "SE": "SEK",
    "CH": "CHF",
    "TW": "TWD",
    "TH": "THB",
    "TR": "TRY",
    "AE": "AED",
    "GB": "GBP",
    "US": "USD",
    "UY": "UYU",
    "VN": "VND",
}

# Fallback lean day bag in USD (hotel+food+transit+1–2 culture) if Grok offline
_FALLBACK_USD: dict[str, float] = {
    "CA": 110,
    "US": 125,
    "CH": 175,
    "IS": 150,
    "NO": 145,
    "DK": 135,
    "SE": 125,
    "GB": 120,
    "IE": 115,
    "FR": 115,
    "DE": 110,
    "NL": 120,
    "BE": 110,
    "AT": 115,
    "IT": 100,
    "ES": 90,
    "PT": 80,
    "GR": 85,
    "TR": 55,
    "PL": 65,
    "CZ": 70,
    "HU": 65,
    "JP": 105,
    "KR": 95,
    "SG": 125,
    "HK": 120,
    "CN": 65,
    "TH": 45,
    "VN": 35,
    "IN": 30,
    "AE": 130,
    "QA": 125,
    "AU": 120,
    "NZ": 110,
    "MX": 55,
    "BR": 55,
    "AR": 50,
    "EG": 40,
    "ZA": 55,
    "IL": 115,
    "SA": 110,
}
_DEFAULT_USD = 85.0

# Crude mid-market mults when network FX unavailable (USD → X)
_STATIC_FROM_USD: dict[str, float] = {
    "USD": 1.0,
    "CAD": 1.38,
    "EUR": 0.92,
    "GBP": 0.79,
    "AUD": 1.52,
    "CHF": 0.88,
    "JPY": 150.0,
    "MXN": 17.0,
    "TRY": 34.0,
    "THB": 35.0,
    "INR": 83.0,
    "CNY": 7.2,
    "KRW": 1350.0,
    "SGD": 1.34,
    "HKD": 7.8,
    "NZD": 1.65,
    "SEK": 10.5,
    "NOK": 10.8,
    "DKK": 6.9,
    "PLN": 4.0,
    "CZK": 23.0,
    "HUF": 360.0,
    "BRL": 5.1,
    "ZAR": 18.0,
    "AED": 3.67,
    "SAR": 3.75,
    "ILS": 3.7,
    "PHP": 56.0,
    "MYR": 4.5,
    "IDR": 15800.0,
    "VND": 25000.0,
    "EGP": 48.0,
    "RON": 4.6,
    "BGN": 1.8,
    "RSD": 108.0,
    "ISK": 138.0,
    "QAR": 3.64,
    "KWD": 0.31,
    "JOD": 0.71,
    "OMR": 0.38,
}


def local_currency(cc: str) -> str:
    return _LOCAL_CCY.get((cc or "").upper(), "USD")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_cost_cache (
            cache_key TEXT PRIMARY KEY,
            origin_cc TEXT NOT NULL,
            stop_cc TEXT NOT NULL,
            currency TEXT NOT NULL,
            daily_origin REAL NOT NULL,
            daily_stop REAL NOT NULL,
            style TEXT,
            includes TEXT,
            blurb TEXT,
            source TEXT,
            fetched_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _key(origin_cc: str, stop_cc: str, currency: str) -> str:
    return (
        f"{origin_cc.upper()}:{stop_cc.upper()}:"
        f"{(currency or 'CAD').upper()}:{CACHE_VERSION}"
    )


@dataclass
class DailyCostCompare:
    origin_cc: str
    stop_cc: str
    origin_name: str
    stop_name: str
    daily_origin: float
    daily_stop: float
    currency: str
    stay_days: int
    ground_total: float
    delta_per_day: float
    source: str  # grok | cache | fallback
    blurb: str
    note_lines: list[str]
    display_daily_origin: str
    display_daily_stop: str
    display_ground: str
    display_delta: str
    budget_daily: float | None = None
    budget_tolerance_pct: float | None = None
    budget_status: str | None = None
    vs_budget_pct: float | None = None
    rank_delta: float = 0.0
    ground_compare_line: str = ""
    budget_line: str = ""


def _static_usd_to(amount_usd: float, currency: str) -> float:
    mult = _STATIC_FROM_USD.get(currency.upper(), 1.0)
    return round(float(amount_usd) * mult)


def _fallback_pair(origin_cc: str, stop_cc: str, currency: str) -> dict[str, Any]:
    cur = currency.upper()
    o = _static_usd_to(_FALLBACK_USD.get(origin_cc.upper(), _DEFAULT_USD), cur)
    s = _static_usd_to(_FALLBACK_USD.get(stop_cc.upper(), _DEFAULT_USD), cur)
    return {
        "daily_origin": o,
        "daily_stop": s,
        "currency": cur,
        "style": STYLE,
        "includes": INCLUDES,
        "blurb": "Rough lean day-bag seed (Grok unavailable).",
        "source": "fallback",
    }


def anchor_examples_for_currency(currency: str) -> list[dict[str, Any]]:
    """Lean anchors in the user's display currency for the Grok prompt."""
    cur = (currency or "CAD").upper()
    # USD lean seeds → user currency (static mid-market)
    seeds = [
        ("CA", "Canada mid-size city", 110),
        ("TR", "Turkey / Istanbul", 55),
        ("CH", "Switzerland / Zurich", 175),
        ("TH", "Thailand / Bangkok", 45),
        ("US", "US mid-size city", 125),
        ("ES", "Spain / Barcelona", 90),
    ]
    out = []
    for code, label, usd in seeds:
        out.append(
            {
                "country": code,
                "label": label,
                "approx_daily": _static_usd_to(usd, cur),
                "currency": cur,
            }
        )
    return out


def cache_get(origin_cc: str, stop_cc: str, currency: str) -> dict[str, Any] | None:
    k = _key(origin_cc, stop_cc, currency)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM daily_cost_cache WHERE cache_key = ?", (k,)
        ).fetchone()
    if not row:
        return None
    if time.time() - float(row["fetched_at"]) > CACHE_TTL_SEC:
        return None
    return {
        "daily_origin": float(row["daily_origin"]),
        "daily_stop": float(row["daily_stop"]),
        "currency": row["currency"],
        "style": row["style"] or STYLE,
        "includes": row["includes"] or INCLUDES,
        "blurb": row["blurb"] or "",
        "source": "cache",
    }


def cache_put(
    origin_cc: str,
    stop_cc: str,
    currency: str,
    payload: dict[str, Any],
    *,
    source: str = "grok",
) -> None:
    k = _key(origin_cc, stop_cc, currency)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_cost_cache (
                cache_key, origin_cc, stop_cc, currency,
                daily_origin, daily_stop, style, includes, blurb, source, fetched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                k,
                origin_cc.upper(),
                stop_cc.upper(),
                currency.upper(),
                float(payload["daily_origin"]),
                float(payload["daily_stop"]),
                payload.get("style") or STYLE,
                payload.get("includes") or INCLUDES,
                payload.get("blurb"),
                source,
                time.time(),
            ),
        )
        conn.commit()


def col_rank_delta(
    daily_stop: float,
    *,
    expected: float | None,
    tolerance_pct: float = 25.0,
) -> tuple[float, str | None, float | None]:
    if expected is None or expected <= 0:
        return 0.0, None, None
    d_s = float(daily_stop)
    exp = float(expected)
    tol = max(0.0, float(tolerance_pct)) / 100.0
    vs_pct = round((d_s / exp - 1.0) * 100.0, 1)
    ceiling = exp * (1.0 + tol)

    if d_s <= exp:
        room = (exp - d_s) / exp
        return min(18.0, room * 30.0), "under", vs_pct
    if d_s <= ceiling:
        if tol <= 0:
            return -4.0, "within", vs_pct
        frac = (d_s - exp) / (ceiling - exp)
        return -min(8.0, frac * 8.0), "within", vs_pct
    over = (d_s - ceiling) / exp
    return -min(35.0, 10.0 + over * 40.0), "over", vs_pct


def build_compare(
    *,
    origin_cc: str,
    stop_cc: str,
    stay_days: int,
    payload: dict[str, Any],
    budget_daily: float | None = None,
    budget_tolerance_pct: float | None = None,
    budget_components: dict[str, float] | None = None,
) -> DailyCostCompare:
    cur = str(payload.get("currency") or "CAD").upper()
    d_o = float(payload["daily_origin"])
    d_s = float(payload["daily_stop"])
    stay = max(1, int(stay_days))
    ground = round(d_s * stay, 2)
    delta = round(d_s - d_o, 2)
    o_name = COUNTRY_NAME.get(origin_cc.upper(), origin_cc.upper())
    s_name = COUNTRY_NAME.get(stop_cc.upper(), stop_cc.upper())
    src = payload.get("source") or "grok"

    if delta > 5:
        delta_txt = f"{format_approx(delta, cur)}/day more than home"
    elif delta < -5:
        delta_txt = f"{format_approx(abs(delta), cur)}/day less than home"
    else:
        delta_txt = "similar to home per day"

    compare_line = (
        f"{format_approx(d_s, cur)}/day in {s_name} vs "
        f"{format_approx(d_o, cur)}/day at home ({o_name})"
    )

    notes = [
        f"Ground: {compare_line} — {delta_txt}",
        (
            f"{format_approx(ground, cur)} estimated on-ground for {stay} night"
            f"{'s' if stay != 1 else ''} "
            f"(decent hotel + food/drink + basic transit + 1–2 culture — not flights)"
        ),
    ]

    tol = 25.0 if budget_tolerance_pct is None else float(budget_tolerance_pct)
    rank_delta, budget_status, vs_budget_pct = col_rank_delta(
        d_s, expected=budget_daily, tolerance_pct=tol
    )
    budget_line = ""
    comps = budget_components or {}
    bag_bits = []
    for key, label in (
        ("hotel", "hotel"),
        ("food", "food"),
        ("transit", "transit"),
        ("culture", "culture"),
    ):
        v = float(comps.get(key) or 0)
        if v > 0:
            bag_bits.append(f"{label} {format_approx(v, cur)}")
    bag_txt = " + ".join(bag_bits) if bag_bits else ""

    if budget_daily is not None and budget_daily > 0:
        exp_disp = format_approx(budget_daily, cur)
        ceil = budget_daily * (1 + max(0.0, tol) / 100.0)
        if bag_txt:
            notes.insert(
                1,
                f"Your day bag: {bag_txt} = {exp_disp}/day · ±{tol:.0f}% band "
                f"(ok up to {format_approx(ceil, cur)}/day)",
            )
        if budget_status == "under":
            budget_line = (
                f"Budget: {format_approx(d_s, cur)}/day under your {exp_disp}/day bag "
                f"({abs(vs_budget_pct or 0):.0f}% under)"
            )
        elif budget_status == "within":
            budget_line = (
                f"Budget: {format_approx(d_s, cur)}/day within +{tol:.0f}% of "
                f"your {exp_disp}/day bag (ok up to {format_approx(ceil, cur)}/day)"
            )
        else:
            budget_line = (
                f"Budget: {format_approx(d_s, cur)}/day over your +{tol:.0f}% band "
                f"(bag {exp_disp}/day · ceiling {format_approx(ceil, cur)}/day · "
                f"{vs_budget_pct:+.0f}% vs bag)"
            )
        notes.insert(2 if bag_txt else 1, budget_line)
        compare_line = (
            f"{format_approx(d_s, cur)}/day in {s_name} · your bag {exp_disp}/day"
            f" ({budget_status or 'n/a'})"
        )

    if payload.get("blurb") and src != "fallback":
        notes.append(str(payload["blurb"])[:200])

    return DailyCostCompare(
        origin_cc=origin_cc.upper(),
        stop_cc=stop_cc.upper(),
        origin_name=o_name,
        stop_name=s_name,
        daily_origin=d_o,
        daily_stop=d_s,
        currency=cur,
        stay_days=stay,
        ground_total=ground,
        delta_per_day=delta,
        source=src,
        blurb=str(payload.get("blurb") or ""),
        note_lines=notes,
        display_daily_origin=format_approx(d_o, cur),
        display_daily_stop=format_approx(d_s, cur),
        display_ground=format_approx(ground, cur),
        display_delta=format_approx(abs(delta), cur),
        budget_daily=float(budget_daily) if budget_daily else None,
        budget_tolerance_pct=tol if budget_daily else None,
        budget_status=budget_status,
        vs_budget_pct=vs_budget_pct,
        rank_delta=rank_delta,
        ground_compare_line=compare_line,
        budget_line=budget_line,
    )


async def _to_user_currency(
    http: httpx.AsyncClient,
    amount: float,
    from_cur: str,
    to_cur: str,
) -> float:
    amt, cur, _ok = await convert_amount(http, float(amount), from_cur, to_cur)
    if cur.upper() != to_cur.upper():
        # FX failed — static path via USD if possible
        fr = from_cur.upper()
        to = to_cur.upper()
        if fr == "USD":
            return float(_static_usd_to(amount, to))
        # local → USD static inverse then to target
        inv = _STATIC_FROM_USD.get(fr)
        if inv and inv > 0:
            usd = float(amount) / inv
            return float(_static_usd_to(usd, to))
        return float(amount)
    return float(amt)


async def estimate_batch_for_stops(
    settings: Settings,
    *,
    origin_iata: str,
    stops: list[tuple[str, str | None, str | None]],  # (iata, country, city)
    currency: str,
    vibe: str = "adventure",
) -> dict[str, Any]:
    """Batch lean day costs; Grok prices in local currency → convert to user currency."""
    origin_cc = country_for_iata(origin_iata) or "CA"
    currency = (currency or "CAD").upper()
    origin_local = local_currency(origin_cc)

    needed: list[dict[str, str]] = []
    seen_cc: set[str] = set()
    iata_to_cc: dict[str, str] = {}
    for iata, cc, city in stops:
        code = (cc or country_for_iata(iata) or "").upper()
        if not code or len(code) != 2:
            code = "XX"
        iata_to_cc[iata.upper()] = code
        if code in seen_cc or code == "XX":
            continue
        hit = cache_get(origin_cc, code, currency)
        if hit:
            seen_cc.add(code)
            continue
        seen_cc.add(code)
        needed.append(
            {
                "country": code,
                "country_name": COUNTRY_NAME.get(code, code),
                "city": city or "",
                "local_currency": local_currency(code),
            }
        )

    payloads_by_cc: dict[str, dict[str, Any]] = {}

    for iata, cc in iata_to_cc.items():
        if cc == "XX":
            continue
        hit = cache_get(origin_cc, cc, currency)
        if hit:
            payloads_by_cc[cc] = hit

    if needed and settings.grok_ready():
        try:
            from yonder.grok import GrokClient

            async with GrokClient(settings) as grok, httpx.AsyncClient(
                timeout=20.0
            ) as http:
                b_daily, b_tol, b_comps = settings.col_budget()
                batch = await grok.estimate_daily_costs_batch(
                    origin_country=origin_cc,
                    origin_name=COUNTRY_NAME.get(origin_cc, origin_cc),
                    origin_local_currency=origin_local,
                    stops=needed,
                    currency=currency,
                    vibe=vibe,
                    anchors=anchor_examples_for_currency(currency),
                    user_budget={
                        "currency": currency,
                        "hotel": b_comps.get("hotel") or 0,
                        "food": b_comps.get("food") or 0,
                        "transit": b_comps.get("transit") or 0,
                        "culture": b_comps.get("culture") or 0,
                        "total": b_daily or 0,
                        "tolerance_pct": b_tol,
                    },
                )

                # Origin: prefer local amount
                o_local_amt = batch.get("origin_daily_local") or batch.get(
                    "origin_daily"
                )
                o_local_cur = (
                    str(batch.get("origin_currency") or origin_local).upper()
                )
                origin_daily = 0.0
                if o_local_amt:
                    origin_daily = await _to_user_currency(
                        http, float(o_local_amt), o_local_cur, currency
                    )

                if origin_daily <= 0:
                    origin_daily = float(
                        _static_usd_to(
                            _FALLBACK_USD.get(origin_cc, _DEFAULT_USD), currency
                        )
                    )

                stops_map = batch.get("stops") or batch.get("countries") or {}
                for cc, info in stops_map.items():
                    cc_u = str(cc).upper()
                    if isinstance(info, dict):
                        local_amt = float(
                            info.get("daily_local")
                            or info.get("daily_stop")
                            or info.get("daily")
                            or info.get("cost")
                            or 0
                        )
                        local_cur = str(
                            info.get("local_currency")
                            or info.get("currency")
                            or local_currency(cc_u)
                        ).upper()
                        blurb = str(info.get("blurb") or info.get("note") or "")
                    else:
                        local_amt = float(info)
                        local_cur = local_currency(cc_u)
                        blurb = ""
                    if local_amt <= 0:
                        continue
                    daily_stop = await _to_user_currency(
                        http, local_amt, local_cur, currency
                    )
                    if daily_stop <= 0:
                        continue
                    if local_cur != currency:
                        blurb = (
                            (blurb + " " if blurb else "")
                            + f"(~{int(round(local_amt)):,} {local_cur}/day → {currency})"
                        ).strip()
                    payload = {
                        "daily_origin": round(origin_daily),
                        "daily_stop": round(daily_stop),
                        "currency": currency,
                        "style": STYLE,
                        "includes": INCLUDES,
                        "blurb": blurb[:240],
                        "source": "grok",
                    }
                    cache_put(origin_cc, cc_u, currency, payload, source="grok")
                    payloads_by_cc[cc_u] = {**payload, "source": "grok"}
        except Exception:
            pass

    for iata, cc in iata_to_cc.items():
        if cc == "XX":
            continue
        if cc not in payloads_by_cc:
            fb = _fallback_pair(origin_cc, cc, currency)
            cache_put(origin_cc, cc, currency, fb, source="fallback")
            payloads_by_cc[cc] = fb

    budget_daily, budget_tol, budget_comps = settings.col_budget()

    return {
        "origin_cc": origin_cc,
        "payloads_by_cc": payloads_by_cc,
        "iata_to_cc": iata_to_cc,
        "budget_daily": budget_daily,
        "budget_tolerance_pct": budget_tol,
        "budget_components": budget_comps,
    }


def compare_for_stop(
    batch: dict[str, Any],
    *,
    stop_iata: str,
    stay_days: int,
    budget_daily: float | None = None,
    budget_tolerance_pct: float | None = None,
    budget_components: dict[str, float] | None = None,
) -> DailyCostCompare | None:
    origin_cc = batch.get("origin_cc") or "CA"
    iata_to_cc = batch.get("iata_to_cc") or {}
    payloads = batch.get("payloads_by_cc") or {}
    b_daily = budget_daily if budget_daily is not None else batch.get("budget_daily")
    b_tol = (
        budget_tolerance_pct
        if budget_tolerance_pct is not None
        else batch.get("budget_tolerance_pct")
    )
    b_comps = budget_components if budget_components is not None else batch.get(
        "budget_components"
    )
    cc = iata_to_cc.get(stop_iata.upper()) or country_for_iata(stop_iata)
    if not cc or cc not in payloads:
        return None
    return build_compare(
        origin_cc=origin_cc,
        stop_cc=cc,
        stay_days=stay_days,
        payload=payloads[cc],
        budget_daily=b_daily,
        budget_tolerance_pct=b_tol,
        budget_components=b_comps if isinstance(b_comps, dict) else None,
    )
