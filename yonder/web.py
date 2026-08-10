from __future__ import annotations

import asyncio
import hashlib
import httpx
import os
import json
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Canonical production URL — used for og:image and share links so crawlers
# always receive an https:// URL even when the dev proxy rewrites base_url.
PRODUCTION_URL = "https://yonder.city"

from yonder.adventure import (
    AdventureItinerary,
    AdventureRequest,
    QuestIdea,
    corridor_candidates,
    detect_trip_gaps,
    plan_adventure,
    plan_quest,
    reprice_itinerary,
    seed_ideas,
)
from yonder.config import get_settings, reload_settings
from yonder.countries import (
    COUNTRIES,
    COUNTRY_PRIMARY_IATA,
    country_for_currency,
    country_label,
    format_place,
    format_route,
    normalize_avoid_list,
    normalize_country_list,
    primary_iata_for_country,
)
from yonder.ai_usage import fmt_usage, log_usage as _log_ai_usage, merge_usage
from yonder.engine import search_flights
from yonder.grok import GrokClient, detect_route_iatas
from yonder.history import count_samples, recent_samples, route_stats
from yonder.last_search import (
    hydrate_detour,
    hydrate_escape,
    load_last,
    save_last,
)
from yonder.links import airline_display_name, airline_site_label
from yonder.quota import budgets_snapshot, choose_providers, get_registry
from yonder.saved import (
    SAVE_LIMIT,
    clear_all_saves,
    count_quests,
    count_saved,
    delete as delete_saved,
    get as get_saved,
    list_quests,
    list_saved,
    save_itinerary,
    top_quest_routes,
    update_from_itinerary,
)
from yonder.settings_store import MANAGED_KEYS, settings_view, write_env
from yonder.themes import theme_css_vars, theme_for_iata
from yonder.types import CabinClass, SearchQuery
from yonder.share import create_share, dump_obj, get_share, qr_png_data_uri, qr_svg_for_url
from yonder.trains import train_options, airport_train_for, ground_transfer_for
from yonder.vibe_theme import VIBE_EMOJI, resolve_vibe, vibe_theme

_VIBES_PATH = Path(__file__).parent / "vibes.json"
_vibes_json: str | None = None
_vibes_v: str | None = None

# ── Intent AI-override cache (prompt_hash → (timestamp, shape)) ───────────────
# Keeps repeated identical prompts from re-firing the secondary AI call.
_INTENT_OVERRIDE_CACHE: dict[str, tuple[float, str]] = {}
_INTENT_OVERRIDE_TTL = 3600.0  # 1 hour


async def _ai_shape_override(
    prompt: str,
    settings,
    *,
    demo: bool = False,
) -> str | None:
    """Reclassify an ambiguous prompt via a cheap secondary AI call.

    Returns 'detour', 'escape', or None when the AI is unavailable / fails.
    Result is cached for 1 hour keyed on prompt SHA-256 hash so repeated
    identical prompts skip the network round-trip.
    """
    import time as _time

    from yonder.vibe_signals import prompt_hash as _ph

    ph = _ph(prompt)
    if ph:
        cached = _INTENT_OVERRIDE_CACHE.get(ph)
        if cached and (_time.time() - cached[0]) < _INTENT_OVERRIDE_TTL:
            return cached[1]

    if demo or not settings.grok_ready():
        return None

    try:
        async with GrokClient(settings) as _grok:
            raw = await _grok._chat(
                "You are a travel intent classifier. Reply with exactly one word and nothing else.",
                (
                    "Is this travel prompt asking for "
                    "(a) a stopover/detour trip, "
                    "(b) a direct flight to a named destination, or "
                    "(c) an open getaway with no specific destination? "
                    f'Prompt: "{prompt[:240]}"\n'
                    "Reply with exactly one word: detour, escape, or getaway."
                ),
                temperature=0.0,
            )
        word = (raw or "").strip().lower().split()[0] if (raw or "").strip() else ""
        # "getaway" → detour (open getaway is routed as a detour round-trip)
        shape: str | None = {"detour": "detour", "escape": "escape", "getaway": "detour"}.get(word)
        if shape and ph:
            _INTENT_OVERRIDE_CACHE[ph] = (_time.time(), shape)
        return shape
    except Exception:
        return None


def _vibes_data() -> tuple[str, str]:
    """Return (vibes_json_str, content_hash) — loaded once and cached."""
    global _vibes_json, _vibes_v
    if _vibes_json is None:
        raw = _VIBES_PATH.read_text(encoding="utf-8")
        _vibes_json = json.dumps(json.loads(raw), separators=(",", ":"))
        # Include the slider script in the hash so JS edits bust browser caches too
        slider_src = (Path(__file__).parent / "static" / "vibe_slider.js").read_bytes()
        _vibes_v = (
            hashlib.sha1(_vibes_json.encode()).hexdigest()[:8]
            + "-"
            + hashlib.sha1(slider_src).hexdigest()[:8]
        )
    return _vibes_json, _vibes_v


def _share_pack(request: Request, *, kind: str, title: str, payload: dict) -> dict:
    """Stable share URL + scannable PNG QR for a boarding-pass stub.

    Link and QR always use the same absolute pretty URL:
    /t/escape/YVR-NRT-2026-08-20-…/id
    """
    packed = dump_obj(payload)
    # Never carry the AI model label into shared payloads.
    if isinstance(packed, dict):
        for part in packed.values():
            if isinstance(part, dict):
                part.pop("model_source", None)
        packed.pop("model_source", None)
    trip = create_share(kind=kind, title=title, payload=packed)
    base = PRODUCTION_URL
    url = f"{base}{trip.path}"
    return {
        "id": trip.id,
        "url": url,
        "path": trip.path,
        # Slightly larger modules — pretty paths are longer than /t/{id}
        "qr_src": qr_png_data_uri(url, scale=5, border=2),
        "qr_svg": qr_svg_for_url(url, scale=4),
    }


def _share_escape(request: Request, result, offer, vibe: str | None = None) -> dict | None:
    try:
        q = result.query
        title = f"{q.origin} → {q.destination}"
        payload = {"query": dump_obj(q), "offer": dump_obj(offer)}
        if vibe:
            payload["vibe"] = str(vibe).strip().lower()
        return _share_pack(
            request,
            kind="escape",
            title=title,
            payload=payload,
        )
    except Exception:
        return None


def _share_detour(request: Request, itinerary, trip_meta: dict | None = None) -> dict | None:
    try:
        title = getattr(itinerary, "title", None) or "Detour"
        if isinstance(itinerary, dict):
            title = itinerary.get("title") or title
        return _share_pack(
            request,
            kind="detour",
            title=str(title),
            payload={
                "itinerary": dump_obj(itinerary),
                "trip_meta": trip_meta or {},
            },
        )
    except Exception:
        return None


def _share_quest(
    request: Request,
    idea,
    home_iata: str,
    trip_meta: dict | None = None,
) -> dict | None:
    """Create a shareable link for a Quest open-jaw idea."""
    try:
        idea_dict = dump_obj(idea)
        if isinstance(idea_dict, dict):
            idea_dict.pop("model_source", None)
        entry_city = str((idea_dict or {}).get("entry_city") or (idea_dict or {}).get("entry_iata") or "?")
        exit_city = str((idea_dict or {}).get("exit_city") or (idea_dict or {}).get("exit_iata") or "?")
        title = f"{entry_city} → overland → {exit_city}"
        meta = dict(trip_meta or {})
        meta.pop("model_source", None)
        return _share_pack(
            request,
            kind="quest",
            title=title,
            payload={
                "idea": idea_dict,
                "home_iata": str(home_iata or ""),
                "trip_meta": meta,
            },
        )
    except Exception:
        return None


def _dest_theme(destination: str) -> dict:
    t = theme_for_iata(destination, kind="stopover")
    t["theme_style"] = theme_css_vars(t)
    t["place"] = format_place(destination)
    # ensure flag_img present (theme_for_country already sets it)
    if not t.get("flag_img") and t.get("country"):
        from yonder.themes import flag_img_url

        t["flag_img"] = flag_img_url(t["country"], width=80) or ""
    return t

def _mark_missing_fares_result(result, *, forced: bool = True):
    """Flag ALL mock offers as fare-missing — demo prices are never shown.

    The route skeleton stays, but the UI shows a per-leg "Check Fares" button
    plus a cached real-price range pill instead of an invented price.
    """
    if result is None:
        return result
    try:
        offers = [
            o.model_copy(update={"fare_missing": True})
            if (o.price_kind or "") == "mock"
            else o
            for o in (result.offers or [])
        ]
        return result.model_copy(update={"offers": offers})
    except Exception:
        return result


def _mark_missing_fares_adventure(result, *, forced: bool = True):
    """Same as _mark_missing_fares_result but for Detour itineraries' legs."""
    if result is None:
        return result
    try:
        its = []
        for it in result.itineraries or []:
            legs = []
            changed = False
            for leg in it.legs or []:
                if leg.offer is not None and (leg.offer.price_kind or "") == "mock":
                    legs.append(
                        leg.model_copy(
                            update={
                                "offer": leg.offer.model_copy(
                                    update={"fare_missing": True}
                                )
                            }
                        )
                    )
                    changed = True
                else:
                    legs.append(leg)
            its.append(it.model_copy(update={"legs": legs}) if changed else it)
        return result.model_copy(update={"itineraries": its})
    except Exception:
        return result


app = FastAPI(title="Yonder", description="Personal travel planner — flights, adventures, itineraries")
_PKG = Path(__file__).parent

# Startup check: every country code referenced by the airport lookup and
# seed stopovers must have a COUNTRY_SIZE entry, or size scaling silently
# degrades to the flat midpoint boost. Logs a one-time warning on misses.
try:
    from yonder.country_size import check_size_table_coverage as _check_size_cov

    _check_size_cov()
except Exception:
    pass

# One-time migration: move any COL/country values stored in .env into user_prefs.db
try:
    from yonder.settings_store import read_env as _read_env
    from yonder.user_prefs import migrate_from_env as _migrate_prefs

    _migrate_prefs(_read_env())
except Exception:
    pass

# Startup backfill: recompute city_slug for any POI rows that have an empty,
# corrupted (stray-dash), or un-aliased slug (e.g. "warszawa" → "warsaw").
# Safe to run on every start — no-ops when all rows are already clean.
try:
    from yonder.poi import backfill_city_slugs as _backfill_city_slugs

    _backfill_city_slugs()
except Exception:
    pass
templates = Jinja2Templates(directory=str(_PKG / "templates"))
from yonder.products import DEPARTMENTS as _PACKING_DEPTS, PACKING_PRODUCTS as _PACKING_PRODUCTS
templates.env.globals["packing_products"] = _PACKING_PRODUCTS
templates.env.globals["departments"] = _PACKING_DEPTS
def _fmt_date(value: object) -> str:
    """Format a date or ISO string as 'Month DD, YYYY' (e.g. September 5, 2026)."""
    from datetime import date as _date
    if value is None:
        return "—"
    if isinstance(value, _date):
        return value.strftime("%-d %B %Y").replace(
            value.strftime("%B"), value.strftime("%B")
        )
    s = str(value).strip()
    if not s:
        return "—"
    try:
        from datetime import date as _date2
        d = _date2.fromisoformat(s[:10])
        return d.strftime("%B %-d, %Y")
    except Exception:
        return s


templates.env.filters["fmt_date"] = _fmt_date
templates.env.filters["vibe_emoji"] = lambda vibe_id: VIBE_EMOJI.get((vibe_id or "").strip().lower(), "")
templates.env.filters["flag_emoji"] = lambda code: "".join(chr(ord(c) + 127397) for c in (code or "").upper()[:2]) if len(code or "") >= 2 else (code or "")
templates.env.globals["place"] = format_place
templates.env.globals["route"] = format_route
templates.env.globals["route_short"] = lambda o, d, **kw: format_route(o, d, with_country=False, **kw)
templates.env.globals["airline_site_label"] = airline_site_label
templates.env.globals["airline_name"] = airline_display_name
templates.env.globals["train_options"] = train_options
templates.env.globals["airport_train_for"] = airport_train_for
templates.env.globals["ground_transfer_for"] = ground_transfer_for
def _promo_offers() -> dict | None:
    """CODE_PROMO / LINK_PROMO from the environment (or .env) — None when unset."""
    import os

    code = (os.environ.get("CODE_PROMO") or "").strip()
    link = (os.environ.get("LINK_PROMO") or "").strip()
    if not code or not link:
        try:
            from yonder.settings_store import read_env as _read_env_promo

            env = _read_env_promo()
            code = code or (env.get("CODE_PROMO") or "").strip()
            link = link or (env.get("LINK_PROMO") or "").strip()
        except Exception:
            pass
    if not code and not link:
        return None
    return {"code": code or None, "link": link or None}


templates.env.globals["promo_offers"] = _promo_offers
templates.env.globals["share_escape"] = _share_escape
templates.env.globals["share_detour"] = _share_detour
templates.env.globals["share_quest"] = _share_quest
_vj_boot, _vv_boot = _vibes_data()
templates.env.globals["vibes_json"] = _vj_boot
templates.env.globals["vibes_v"] = _vv_boot
app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")


def _error_ctx() -> dict:
    """Minimal template context for error pages (no AI or DB calls)."""
    try:
        saved = count_saved()
    except Exception:
        saved = 0
    return {
        "nav": "",
        "vibe_theme": None,
        "saved_count": saved,
    }


from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", _error_ctx(), status_code=404)
    return templates.TemplateResponse(request, "500.html", _error_ctx(), status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return templates.TemplateResponse(request, "500.html", _error_ctx(), status_code=500)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> PlainTextResponse:
    return PlainTextResponse(
        "User-agent: *\nAllow: /\n\nSitemap: https://yonder.city/sitemap.xml\n"
    )


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt() -> PlainTextResponse:
    return PlainTextResponse(
        """# Yonder.City

> Find spontaneous last-minute flights based on your travel vibe.

Yonder.City is a flight-finder tool for spontaneous travelers. Users describe a trip in plain English, choose a mood or "vibe" (chaos, romance, city, etc.), and receive AI-curated flight suggestions across multiple providers.

## Features
- Escape mode: point-to-point flights
- Detour mode: multi-city open-jaw itineraries
- Save and share trip itineraries via link or QR code
- Vibe-based AI suggestions powered by xAI/Grok

## Public pages
- / - Main flight search (Explore)
- /saved - Saved itineraries
- /packing - AI packing list
- /t/{id} - Shared trip itinerary
"""
    )


@app.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    urls = [
        "https://yonder.city/",
        "https://yonder.city/quests",
        "https://yonder.city/packing",
    ]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append(f"  <url><loc>{u}</loc></url>")
    lines.append("</urlset>")
    return Response("\n".join(lines), media_type="application/xml")


def _compute_return_days() -> int:
    """Effective days-ahead for the Find Return date picker.

    Reads ``return_days`` from user_prefs.db.  When it is 0 / blank the
    value auto-computes as detour_min_stop_days + detour_max_stop_days
    (the user's own stopover range), giving a sensible per-user default
    without any extra configuration.
    """
    try:
        from yonder.user_prefs import get_all_prefs as _gup

        prefs = _gup()
        rd = int(prefs.get("return_days") or "0")
        if rd > 0:
            return max(1, min(365, rd))
        lo = max(1, int(prefs.get("detour_min_stop_days") or "4"))
        hi = max(1, int(prefs.get("detour_max_stop_days") or "5"))
        return lo + hi
    except Exception:
        return 9  # absolute fallback (4+5)


def _base_ctx(settings=None, *, vibe: str | None = None) -> dict:
    settings = settings or get_settings()
    avoid_codes = settings.avoid_country_list()
    visited_codes = settings.visited_country_list()
    vt = vibe_theme(vibe) if vibe else None
    vibes_json, vibes_v = _vibes_data()
    from yonder.xp import compute_xp as _compute_xp
    _xp = _compute_xp(settings.visited_tile_list(), avoid_codes)
    return {
        "xp_profile": _xp,
        "vibes_json": vibes_json,
        "vibes_v": vibes_v,
        "providers": settings.configured_providers(),
        "grok_ready": settings.grok_ready(),
        "testing": bool(settings.testing),
        "countries": COUNTRIES,
        "avoid_defaults": avoid_codes,
        "avoid_tiles_defaults": settings.avoid_tile_list(),
        "visited_defaults": visited_codes,
        "visited_tiles_defaults": settings.visited_tile_list(),
        "budgets": budgets_snapshot(settings),
        "history_count": count_samples(),
        "saved_count": count_saved(),
        "vibe_theme": vt,
        # For progress.js fun lines + CRT maps (names only — no secrets)
        "travel_ctx": {
            "avoid": avoid_codes,
            "avoid_tiles": settings.avoid_tile_list(),
            # visited is stamp order: first country = home when HOME_IATA is blank
            "visited": visited_codes,
            "avoid_names": [country_label(c) for c in avoid_codes],
            "visited_names": [country_label(c) for c in visited_codes],
            "country_names": {code: name for code, name in COUNTRIES},
            "home_iata": settings.resolve_home_iata(),
            "home_iata_setting": (settings.home_iata or "").strip().upper(),
            "home_iata_fallback": (
                primary_iata_for_country(
                    country_for_currency(settings.default_currency)
                )
                or "JFK"
            ),
            "primary_iata": dict(COUNTRY_PRIMARY_IATA),
            "search_aim_seconds": settings.search_timing()[0],
            "search_max_seconds": settings.search_timing()[1],
            "detour_min_stop_days": settings.detour_stop_defaults()[0],
            "detour_max_stop_days": settings.detour_stop_defaults()[1],
        },
        "stop_min_days": settings.detour_stop_defaults()[0],
        "stop_max_days": settings.detour_stop_defaults()[1],
        "home_resolved": settings.resolve_home_iata(),
        "return_days": _compute_return_days(),
        # Always an https:// URL so crawlers (Twitter, Slack, iMessage) load it.
        "og_image": f"{PRODUCTION_URL}/static/share_bg.jpg",
    }


def _home_mode(raw: str | None) -> str:
    """UI modes: escape (point-to-point hunt) | detour (multi-leg / getaway).

    Accepts legacy query values: flights, search, adventures, adventure, adv.
    """
    m = (raw or "escape").strip().lower()
    if m in ("detour", "detours", "adventures", "adventure", "adv"):
        return "detour"
    # escape, flights, search, go, …
    return "escape"


def _empty_escape_form(settings) -> dict:
    return {
        "origin": "YVR",
        "destination": "NRT",
        "depart": "",
        "return_date": "",
        "adults": 1,
        "currency": settings.default_currency,
        "nonstop": False,
        "vibe": "",
    }


def _req_sess(request: Request) -> str:
    """Server-trusted session id for last-search scoping (yv_sess cookie)."""
    return (request.cookies.get("yv_sess") or "").strip()[:64]


def _escape_panel(settings, session_id: str, override: dict | None = None) -> dict:
    """Last Escape search (or live override) for the unified toggle UI."""
    base = {
        "ask": "",
        "form": _empty_escape_form(settings),
        "result": None,
        "parsed": None,
        "analysis": None,
        "dest_theme": None,
        "place_book": None,
        "error": None,
    }
    snap = load_last("escape", session_id=session_id)
    if snap:
        try:
            base.update(hydrate_escape(snap))
        except Exception:
            pass
    if override is not None:
        # Live search / error form wins over disk snapshot
        base.update(override)
    if not base.get("form"):
        base["form"] = _empty_escape_form(settings)
    return base


def _detour_panel(settings, session_id: str, override: dict | None = None) -> dict:
    """Last Detour search (or live override) for the unified toggle UI."""
    base = {
        "form": _adventure_form_defaults(settings),
        "result": None,
        "trip_meta": None,
        "place_books": {},
        "error": None,
    }
    snap = load_last("detour", session_id=session_id)
    if snap:
        try:
            base.update(hydrate_detour(snap))
        except Exception:
            pass
    if override is not None:
        base.update(override)
    if not base.get("form"):
        base["form"] = _adventure_form_defaults(settings)
    if base.get("place_books") is None:
        base["place_books"] = {}
    return base


def _saved_shuffle_pool(limit: int = 100, session_id: str | None = None) -> list[dict]:
    """Distinct {prompt, vibe} pairs from saved trips for the compose shuffle."""
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()
    try:
        for s in list_saved(limit=limit, owner_sess=session_id):
            prompt = (s.trip_prompt or "").strip()
            vibe = (s.vibe or "").strip().lower()
            # Old rows may hold free text in the vibe column; ids are short slugs.
            if len(vibe) > 40 or " " in vibe:
                vibe = ""
            if not prompt and not vibe:
                continue
            key = (prompt, vibe)
            if key in seen:
                continue
            seen.add(key)
            pool.append({"prompt": prompt, "vibe": vibe})
    except Exception:
        return []
    return pool


def _compose_page_ctx(
    settings,
    *,
    mode: str,
    session_id: str,
    error: str | None = None,
    escape_override: dict | None = None,
    detour_override: dict | None = None,
    lock_vibe: bool | None = None,
) -> dict:
    """Shared context for home + POST results: one compose card, both result panels.

    lock_vibe: keep the form vibe after a live search. When False (normal refresh),
    the client picks a random vibe for the compose card.
    """
    mode = _home_mode(mode)
    esc = _escape_panel(settings, session_id, escape_override)
    det = _detour_panel(settings, session_id, detour_override)
    if lock_vibe is None:
        # Live Escape/Detour POST always passes an override panel
        lock_vibe = escape_override is not None or detour_override is not None
    if mode == "detour":
        form = det.get("form") or _adventure_form_defaults(settings)
        result = det.get("result")
        ask = ""
        parsed = None
        analysis = None
        dest_theme = None
        place_book = None
        place_books = det.get("place_books") or {}
        trip_meta = det.get("trip_meta")
        # Prefer live/panel error when detour is active
        err = error if error is not None else det.get("error")
    else:
        form = esc.get("form") or _empty_escape_form(settings)
        result = esc.get("result")
        ask = esc.get("ask") or ""
        parsed = esc.get("parsed")
        analysis = esc.get("analysis")
        dest_theme = esc.get("dest_theme")
        place_book = esc.get("place_book")
        place_books = {}
        trip_meta = None
        err = error if error is not None else esc.get("error")

    ctx: dict = {
        "nav": "home",
        "mode": mode,
        **_base_ctx(settings),
        "result": result,
        "error": err,
        "ask": ask,
        "parsed": parsed,
        "analysis": analysis,
        "dest_theme": dest_theme,
        "place_book": place_book,
        "place_books": place_books,
        "trip_meta": trip_meta,
        "form": form,
        "escape_panel": esc,
        "detour_panel": det,
        "lock_vibe": bool(lock_vibe),
        "saved_shuffle": _saved_shuffle_pool(session_id=session_id),
    }
    form_vibe = form.get("vibe") if isinstance(form, dict) else None
    # Page chrome vibe only when locked (post-search); otherwise JS picks random
    if form_vibe and lock_vibe:
        vt = vibe_theme(str(form_vibe))
        ctx.update(_base_ctx(settings, vibe=vt["id"]))
        ctx["cur_vibe"] = vt["id"]
    return ctx


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    settings = reload_settings()
    mode = _home_mode(request.query_params.get("mode"))
    sess = _req_sess(request)
    need_cookie = not sess
    if need_cookie:
        import uuid as _uuid

        sess = _uuid.uuid4().hex[:32]
    ctx = _compose_page_ctx(settings, mode=mode, session_id="" if need_cookie else sess)
    response = templates.TemplateResponse(request, "index.html", ctx)
    if need_cookie:
        response.set_cookie("yv_sess", sess, httponly=True, samesite="lax")
    return response


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    depart: str = Query(...),
    return_date: str | None = None,
    adults: int = Query(1, ge=1, le=9),
    currency: str = "USD",
    nonstop: bool = False,
    use_grok: bool = True,
) -> HTMLResponse:
    settings = get_settings()
    # Mock is internal-only: route skeletons when no fare providers configured.
    mock = not settings.configured_providers()
    form = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart": depart,
        "return_date": return_date or "",
        "adults": adults,
        "currency": currency.upper(),
        "nonstop": nonstop,
        "use_grok": use_grok,
    }
    try:
        query = SearchQuery(
            origin=origin.upper(),
            destination=destination.upper(),
            depart_date=date.fromisoformat(depart),
            return_date=date.fromisoformat(return_date) if return_date else None,
            adults=adults,
            cabin=CabinClass.ECONOMY,
            currency=currency.upper() or settings.default_currency,
            max_results=25,
            nonstop_only=nonstop,
        )
        result = await search_flights(query, settings=settings, include_mock=mock)
        result = _mark_missing_fares_result(result)

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "search",
                **_base_ctx(settings),
                "result": result,
                "error": None,
                "ask": "",
                "parsed": None,
                "analysis": None,
                "form": form,
                "dest_theme": _dest_theme(destination),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "search",
                **_base_ctx(settings),
                "result": None,
                "error": str(exc),
                "ask": "",
                "parsed": None,
                "analysis": None,
                "form": form,
                "dest_theme": None,
            },
            status_code=400,
        )


@app.api_route("/ask", methods=["GET", "POST"], response_class=HTMLResponse)
async def ask_grok(request: Request) -> HTMLResponse:
    """Natural language → Grok parse → multi-provider scan (no post-rank writeup).

    GET ?ask=... supported so links/bookmarks work; POST is the form path.
    """
    # Always re-read .env so a key saved mid-session is picked up
    settings = reload_settings()
    sess = _req_sess(request)

    if request.method == "GET":
        ask = str(request.query_params.get("ask") or "").strip()[:280]
        vibe = str(request.query_params.get("vibe") or "").strip().lower()
    else:
        form_data = await request.form()
        ask = str(form_data.get("ask") or "").strip()[:280]
        vibe = str(form_data.get("vibe") or "").strip().lower()

    # Display currency always from Settings (default USD)
    currency_pref = (settings.default_currency or "USD").strip().upper()[:3]
    if not currency_pref.isalpha() or len(currency_pref) != 3:
        currency_pref = "USD"
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"

    # Mock is purely an internal fallback: only when no fare providers are
    # configured.  Mock offers carry route skeletons; their prices are always
    # hidden (fare_missing) and replaced by real cached range pills in the UI.
    mock = not settings.configured_providers()

    empty_form = {
        "origin": "YVR",
        "destination": "NRT",
        "depart": "",
        "return_date": "",
        "adults": 1,
        "currency": currency_pref,
        "nonstop": False,
        "vibe": vibe,
    }
    # Vibe is mandatory — always fold into Grok prompt
    ask_for_grok = f"{ask}\n\nTrip vibe: {vibe}."

    if not ask:
        # GET /ask with no query → bounce home
        if request.method == "GET":
            return RedirectResponse(url="/", status_code=302)
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="escape",
                error="Type a trip in plain English first.",
                escape_override={"ask": "", "form": empty_form},
            ),
            status_code=400,
        )

    if not settings.grok_ready():
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="escape",
                error="No AI model configured — add XAI_API_KEY in Settings (console.x.ai) or set a BYOM endpoint, then Save.",
                escape_override={"ask": ask, "form": empty_form},
            ),
            status_code=400,
        )

    try:
        avoid = settings.effective_avoid_country_list()
        visited = settings.visited_country_list()
        visited_tiles_l = settings.visited_tile_list()
        aim, _skip = settings.search_timing()
        home_iata = settings.resolve_home_iata()

        _esc_usage: list[dict] = [{}]

        async def _escape_run():
            async with GrokClient(settings) as grok:
                trip = await grok.parse_natural_language(
                    ask_for_grok,
                    default_currency=currency_pref,
                    default_origin=home_iata,
                    avoid_countries=avoid,
                    visited_countries=visited,
                )
                # Settings currency wins; always 1 pax economy
                trip = trip.model_copy(
                    update={
                        "currency": currency_pref,
                        "adults": 1,
                        "cabin": CabinClass.ECONOMY,
                    }
                )
                query = grok.to_search_query(trip)
                query = query.model_copy(
                    update={
                        "currency": currency_pref,
                        "adults": 1,
                        "cabin": CabinClass.ECONOMY,
                    }
                )
                include_mock = mock or not settings.configured_providers()
                result = await search_flights(
                    query,
                    settings=settings,
                    include_mock=include_mock,
                    timeout=min(18.0, max(8.0, aim * 0.5)),
                    max_providers=1,
                )
                _esc_usage[0] = grok.accumulated_usage
            return trip, query, result

        # Soft aim only — no hard kill (Skip is on the unified /explore path)
        trip, query, result = await _escape_run()
        result = _mark_missing_fares_result(result)

        analysis = None
        # Place notes: cache only (no live Grok on the hot path)
        place_book = None
        try:
            from yonder.countries import country_for_iata
            from yonder.encyclopedia import get_cached, cache_key, PlaceBrief

            key = cache_key(
                query.destination,
                country_for_iata(query.destination),
                None,
            )
            hit = get_cached(key) if key else None
            if hit:
                from yonder.encyclopedia import _activity_links

                place_book = {
                    **hit,
                    "iata": query.destination,
                    "country": country_for_iata(query.destination),
                    "from_cache": True,
                    "activity_links": await _activity_links(
                        settings,
                        iata=query.destination,
                        city=None,
                        trip_vibe=vibe,
                        user_prompt=ask,
                    ),
                }
        except Exception:
            place_book = None

        form = {
            "origin": query.origin,
            "destination": query.destination,
            "depart": query.depart_date.isoformat(),
            "return_date": query.return_date.isoformat() if query.return_date else "",
            "adults": query.adults,
            "currency": currency_pref,
            "nonstop": query.nonstop_only,
            "vibe": vibe,
        }
        dest_theme = _dest_theme(query.destination)
        vt = vibe_theme(vibe)
        form["vibe"] = vt["id"]
        form["vibe_color"] = vt["color"]
        save_last(
            "escape",
            session_id=sess,
            payload={
                "ask": ask,
                "form": form,
                "result": result,
                "parsed": trip,
                "analysis": analysis,
                "dest_theme": dest_theme,
                "place_book": place_book,
            },
        )
        _esc_ctx = _compose_page_ctx(
            settings,
            session_id=sess,
            mode="escape",
            escape_override={
                "ask": ask,
                "form": form,
                "result": result,
                "parsed": trip,
                "analysis": analysis,
                "dest_theme": dest_theme,
                "place_book": place_book,
            },
        )
        _esc_u = _esc_usage[0]
        if _esc_u.get("total_tokens"):
            _esc_ctx["ai_usage_display"] = fmt_usage(_esc_u)
            asyncio.create_task(_log_ai_usage("escape", _esc_u))
        return templates.TemplateResponse(request, "index.html", _esc_ctx)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="escape",
                error=f"Grok search failed: {exc}",
                escape_override={"ask": ask, "form": empty_form},
            ),
            status_code=400,
        )


def _adventure_form_defaults(settings) -> dict:
    from datetime import timedelta

    depart = (date.today() + timedelta(days=45)).isoformat()
    min_stop, max_stop, max_cand = settings.detour_stop_defaults()
    return {
        "prompt": "",
        "origin": "",
        "destination": "",
        "depart": depart,
        "arrive_by": "",
        "min_stop_days": min_stop,
        "max_stop_days": max_stop,
        "max_candidates": max_cand,
        "currency": settings.default_currency or "USD",
        "vibe": "adventure",
        "use_grok": True,
    }


@app.post("/explore", response_class=HTMLResponse)
async def explore_run(request: Request) -> HTMLResponse:
    """Unified Go — intent gate → pure Escape, pure Detour, or mixed stack.

    Durable writes only happen later via ★ Save (not here).
    """
    import time as _time

    from yonder.intent import decide_shape, mix_candidate_cap
    from yonder.grok import _guess_home_iata
    from datetime import timedelta

    settings = reload_settings()
    sess = _req_sess(request)
    form_data = await request.form()

    def _s(key: str, fallback: str = "") -> str:
        v = form_data.get(key)
        return str(v).strip() if v is not None and str(v).strip() else fallback

    # Accept ask or prompt (compose field name switches with mode)
    prompt = _s("prompt") or _s("ask")
    vibe = _s("vibe", "adventure").lower()
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"
    force = _s("force_mode") or _s("mode")  # optional soft force from UI
    if force in ("escape", "detour", "mix", "quest"):
        pass
    else:
        force = None
    # Mock is purely an internal fallback: only when no fare providers are
    # configured.  Mock offers carry route skeletons; their prices are always
    # hidden (fare_missing) and replaced by real cached range pills in the UI.
    mock = not settings.configured_providers()

    # Return-flight toggle: strip return_date when the user opts for one-way.
    # Omitting the field (legacy clients) defaults to one-way (False).
    _return_flight_raw = _s("return_flight")
    return_flight = _return_flight_raw.lower() in ("true", "1", "yes") if _return_flight_raw else False

    # Multi-city toggle: suppress the Detour panel only when the caller
    # explicitly opts out (multi_city=false) or forces escape-only mode.
    # When absent, default to True so decide_shape can still choose mix/detour.
    _multi_city_raw = _s("multi_city")
    if _multi_city_raw:
        multi_city = _multi_city_raw.lower() in ("true", "1", "yes")
    elif force == "escape":
        multi_city = False  # explicit escape force → no detour
    else:
        multi_city = True  # default: let decide_shape control whether detour runs

    # Quest duration: 7 / 10 / 14 / 21 days; default 10 when absent or invalid.
    _QUEST_DAYS_ALLOWED = {7, 10, 14, 21}
    try:
        quest_days = int(form_data.get("quest_days") or 10)
        if quest_days not in _QUEST_DAYS_ALLOWED:
            quest_days = 10
    except (ValueError, TypeError):
        quest_days = 10

    currency = (settings.default_currency or "USD").upper()
    if not currency.isalpha() or len(currency) != 3:
        currency = "USD"
    avoid = settings.effective_avoid_country_list()
    visited = settings.visited_country_list()
    visited_tiles_l = settings.visited_tile_list()
    min_stop, max_stop, max_cand_settings = settings.detour_stop_defaults()
    # Results criteria bar can override stop window + origin for re-runs
    try:
        ms = int(str(form_data.get("min_stop_days") or min_stop).strip() or min_stop)
        min_stop = max(1, min(21, ms))
    except (TypeError, ValueError):
        pass
    try:
        xs = int(str(form_data.get("max_stop_days") or max_stop).strip() or max_stop)
        max_stop = max(min_stop, min(30, xs))
    except (TypeError, ValueError):
        pass
    aim, skip_after = settings.search_timing()
    soft_deadline = _time.monotonic() + max(5.0, aim - 0.5)
    search_id = _s("search_id")[:80]
    chip_id = _s("chip_id")[:80]
    chip_source = _s("chip_source")[:32] or "prompt"
    click_id = _s("click_id")[:64]
    seed_iatas_raw = _s("seed_iatas")
    exclude_iatas_raw = _s("exclude_iatas")
    home_iata = settings.resolve_home_iata()
    origin_override = _s("origin").upper()
    if len(origin_override) == 3 and origin_override.isalpha():
        home_iata = origin_override
    defaults = _adventure_form_defaults(settings)
    depart = _s("depart", defaults["depart"])
    if not depart:
        depart = (date.today() + timedelta(days=45)).isoformat()

    from yonder.attribution import log_event, new_click_id
    from yonder.last_search import load_first
    from yonder.search_cancel import clear as clear_search_cancel
    from yonder.search_cancel import is_cancelled

    if not click_id:
        click_id = new_click_id()

    is_refresh = (chip_source or "").lower() in ("refresh",) or _s("refresh") in (
        "1",
        "true",
        "yes",
    )
    # Refresh is an explicit refine: the Depart field's origin is pinned and
    # wins over any origin stated in the prompt. Initial searches keep
    # prompt-first priority (origin field only serves as the default).
    origin_pinned = is_refresh and len(origin_override) == 3 and origin_override.isalpha()

    def _soft_remaining() -> float:
        """Seconds left in soft aim; after aim, still generous so work can finish."""
        if search_id and is_cancelled(search_id):
            return 0.0
        left = soft_deadline - _time.monotonic()
        if left > 0:
            return left
        # Past soft aim and not skipped — keep going
        return 120.0

    # Chip / form seed IATAs (product flywheel — skip cold invent when strong)
    chip_seeds: list[dict] = []
    for part in seed_iatas_raw.replace(";", ",").split(","):
        code = part.strip().upper()
        if len(code) == 3 and code.isalpha():
            chip_seeds.append(
                {
                    "iata": code,
                    "city": code,
                    "kind": "chip_seed",
                    "why": "from suggestion chip",
                }
            )

    # Cities already shown this session — Refresh asks for something new
    exclude_iatas: set[str] = set()
    save_ban: set[str] = set()
    for part in exclude_iatas_raw.replace(";", ",").split(","):
        code = part.strip().upper()
        if len(code) == 3 and code.isalpha():
            exclude_iatas.add(code)
    # On refresh, board seeds are "already seen" — never re-seed them as invent targets
    if is_refresh:
        for s in chip_seeds:
            exclude_iatas.add(s["iata"])
        chip_seeds = []

    # ★ Saves must NEVER reappear as invent/board destinations (hard ban).
    # Always init save_ban above first so notes never UnboundLocalError.
    try:
        from yonder.saved import saved_destination_iatas

        save_ban = saved_destination_iatas(limit=200, owner_sess=sess) or set()
    except Exception:  # noqa: BLE001
        save_ban = set()
    exclude_iatas |= save_ban
    chip_seeds = [
        h for h in chip_seeds if (h.get("iata") or "").upper() not in exclude_iatas
    ]
    seed_hints: list[dict] = []  # never invent-seed from Saves

    empty_esc = {
        "origin": home_iata,
        "destination": "",
        "depart": depart,
        "return_date": "",
        "adults": 1,
        "currency": currency,
        "nonstop": False,
        "vibe": vibe,
    }
    det_form = {
        **defaults,
        "prompt": prompt,
        "depart": depart,
        "currency": currency,
        "vibe": vibe,
        "origin": home_iata,
    }

    if not prompt:
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="escape",
                error="Type a trip in plain English first.",
                escape_override={"ask": "", "form": empty_esc},
            ),
            status_code=400,
        )

    decision = decide_shape(prompt, force=force, vibe=vibe)

    # Confidence-gated AI fallback: when decide_shape returns a low-confidence
    # "mix" and the prompt is non-trivial (≥ 6 words), fire a cheap secondary
    # AI call to reclassify before the pricing path is chosen.
    if (
        decision.shape == "mix"
        and decision.confidence <= 0.65
        and len(prompt.split()) >= 6
        and not decision.forced
    ):
        try:
            _ai_override = await _ai_shape_override(prompt, settings)
            if _ai_override and _ai_override != decision.shape:
                from yonder.intent import IntentDecision as _IntentDecision

                decision = _IntentDecision(
                    shape=_ai_override,  # type: ignore[arg-type]
                    confidence=0.75,
                    rationale=f"ai-fallback:{decision.rationale}",
                )
        except Exception:
            pass

    max_cand = mix_candidate_cap(decision.shape, max_cand_settings)
    notes: list[str] = [decision.shape]
    if save_ban:
        n = len(save_ban)
        notes.append(f"Skipping {n} saved {'city' if n == 1 else 'cities'}")

    escape_override: dict = {"ask": prompt, "form": empty_esc, "result": None}
    detour_override: dict = {"form": det_form, "result": None}
    quest_override: dict = {"ask": prompt, "result": None}
    # Route resolved by the Escape half's parse (origin, destination IATAs).
    # The Detour half reuses it when the AI is unavailable so non-English
    # prompts keep their route instead of collapsing to a home getaway.
    resolved_route: tuple[str, str] | None = None
    errors: list[str] = []
    _route_usage: list[dict] = []  # accumulates grok.accumulated_usage from each block
    active_mode = "detour" if decision.shape == "detour" else "escape"
    restored_first = False

    # Funnel: chip/prompt → search (affiliate path starts here)
    try:
        log_event(
            "explore_start",
            click_id=click_id,
            chip_id=chip_id or None,
            chip_source=chip_source,
            vibe=vibe,
            origin=home_iata,
            search_id=search_id or None,
            meta={"force": force, "shape": decision.shape},
        )
    except Exception:
        pass

    # Attribution bag stamped onto trip_meta for ★ Save + outbound links
    attr_meta = {
        "click_id": click_id,
        "chip_id": chip_id or None,
        "chip_source": chip_source,
        "search_id": search_id or None,
    }

    async def _do_escape() -> None:
        nonlocal escape_override, active_mode, restored_first, resolved_route
        if search_id and is_cancelled(search_id):
            errors.append("Escape skipped — user hit Skip")
            return
        if not settings.grok_ready() and not mock:
            errors.append("Escape skipped — add XAI_API_KEY or configure a BYOM endpoint in Settings")
            return
        remaining = _soft_remaining()
        if remaining <= 0 and search_id and is_cancelled(search_id):
            errors.append("Escape skipped — user hit Skip")
            return
        ask_for_grok = f"{prompt}\n\nTrip vibe: {vibe}."
        async with GrokClient(settings) as grok:
            if _uni_trip is not None:
                # Unified cold-start call already parsed this section — no extra Grok call
                trip = _uni_trip
            else:
                ask_esc = ask_for_grok
                if is_refresh and exclude_iatas:
                    ask_esc = (
                        f"{ask_for_grok}\n\n"
                        f"Do NOT use these destinations (already shown): "
                        f"{', '.join(sorted(exclude_iatas))}. Pick a different city."
                    )
                trip = await grok.parse_natural_language(
                    ask_esc,
                    default_currency=currency,
                    default_origin=home_iata,
                    avoid_countries=avoid,
                    visited_countries=visited,
                    # Refresh wants novelty — a cached repeat defeats the point
                    use_cache=not is_refresh,
                )
            if search_id and is_cancelled(search_id):
                errors.append("Escape skipped mid-parse — user hit Skip")
                return
            # Dataset/suggestion pills can stale-write "From JFK:" when client home
            # was wrong; never let that override resolved home on chip-driven runs.
            chip_driven = (
                (chip_source or "").lower() in ("dataset", "template", "save", "chip")
                or (chip_id or "").startswith("ds:")
            )
            if (
                chip_driven
                and home_iata
                and len(home_iata) == 3
                and (trip.origin or "").upper() != home_iata.upper()
            ):
                pass  # origin silently corrected to home
                trip = trip.model_copy(update={"origin": home_iata.upper()})
            # Refresh: the Depart field origin is pinned — it beats prompt text
            if origin_pinned and (trip.origin or "").upper() != origin_override:
                trip = trip.model_copy(update={"origin": origin_override})
            # Refresh: if Grok reused a seen destination, try seed pool once
            dest_u = (trip.destination or "").upper()
            if is_refresh and dest_u in exclude_iatas:
                from yonder.adventure import seed_ideas as _seed_esc
                from yonder.adventure import AdventureRequest as _AR

                try:
                    pool = _seed_esc(
                        _AR(
                            origin=home_iata,
                            destination=home_iata,
                            depart_date=date.fromisoformat(depart),
                            currency=currency,
                            max_candidates=max_cand,
                            vibe=vibe,
                            prompt=prompt,
                            avoid_countries=avoid,
                            visited_countries=visited,
                            visited_tiles=visited_tiles_l,
                            trip_kind="getaway",
                        ),
                        exclude_iatas=exclude_iatas,
                        shuffle=True,
                        owner_sess=sess,
                    )
                    if pool:
                        trip = trip.model_copy(
                            update={
                                "destination": pool[0].iata.upper(),
                                "assumptions": list(trip.assumptions or [])
                                + [f"Refresh rolled destination away from {dest_u}"],
                            }
                        )
                        pass  # rolled to fresh escape destination
                except Exception:
                    pass
            trip = trip.model_copy(
                update={"currency": currency, "adults": 1, "cabin": CabinClass.ECONOMY}
            )
            query = grok.to_search_query(trip)
            query = query.model_copy(
                update={"currency": currency, "adults": 1, "cabin": CabinClass.ECONOMY}
            )
            # Apply Return toggle: strip return_date when one-way requested;
            # add a sensible default when round-trip is requested but Grok
            # didn't parse an explicit return date from the prompt.
            from datetime import timedelta as _td_rt
            if not return_flight:
                query = query.model_copy(update={"return_date": None})
            elif query.return_date is None:
                query = query.model_copy(
                    update={"return_date": query.depart_date + _td_rt(days=7)}
                )
            # Remember the parsed route for the Detour half — the parse
            # understood the prompt (any language); text re-detection may not.
            _ro = (query.origin or "").upper()
            _rd = (query.destination or "").upper()
            if (
                len(_ro) == 3 and _ro.isalpha()
                and len(_rd) == 3 and _rd.isalpha()
                and _ro != _rd
            ):
                resolved_route = (_ro, _rd)
            fare_timeout = min(18.0, max(5.0, remaining * 0.5 if remaining < 100 else 14.0))
            result = await search_flights(
                query,
                settings=settings,
                include_mock=mock,
                timeout=fare_timeout,
                max_providers=1,
            )
            result = _mark_missing_fares_result(result)
            _route_usage.append(grok.accumulated_usage)
        # When mixing, keep up to 3 cheapest; pure escape stays 1 from engine
        if decision.shape == "mix" and result.offers:
            # engine already returns 1; widen slightly by re-search not needed
            pass
        form = {
            "origin": query.origin,
            "destination": query.destination,
            "depart": query.depart_date.isoformat(),
            "return_date": query.return_date.isoformat() if query.return_date else "",
            "adults": 1,
            "currency": currency,
            "nonstop": query.nonstop_only,
            "vibe": vibe,
            "vibe_color": vibe_theme(vibe)["color"],
        }
        dest_theme = _dest_theme(query.destination)
        place_book = None
        try:
            from yonder.countries import country_for_iata
            from yonder.encyclopedia import (
                _payload_lang_mismatch,
                cache_key,
                get_cached,
                get_place_brief,
            )
            from yonder.lang import detect_lang as _detect_lang

            _brief_lang = _detect_lang(prompt)
            dest_cc = country_for_iata(query.destination)
            key = cache_key(query.destination, dest_cc, None, lang=_brief_lang)
            hit = get_cached(key) if key else None
            if hit and _payload_lang_mismatch(hit, _brief_lang):
                hit = None  # legacy entry in another language — go live instead
            if hit:
                from yonder.encyclopedia import _activity_links

                place_book = {
                    **hit,
                    "iata": query.destination,
                    "country": dest_cc,
                    "from_cache": True,
                    "activity_links": await _activity_links(
                        settings,
                        iata=query.destination,
                        city=None,
                        trip_vibe=vibe,
                        user_prompt=prompt,
                    ),
                }
            # Phase B: live field note if user didn't Skip
            elif not (search_id and is_cancelled(search_id)) and settings.grok_ready():
                brief = await get_place_brief(
                    settings,
                    iata=query.destination,
                    country=dest_cc,
                    city=None,
                    role="destination",
                    user_prompt=prompt,
                    trip_vibe=vibe,
                )
                if brief:
                    place_book = brief.to_dict()
        except Exception:
            place_book = None
        escape_override = {
            "ask": prompt,
            "form": form,
            "result": result,
            "parsed": trip,
            "analysis": None,
            "dest_theme": dest_theme,
            "place_book": place_book,
            "attribution": attr_meta,
            "trip_meta": {
                **attr_meta,
                "prompt": prompt,
                "vibe": vibe,
                "origin": query.origin,
                "destination": query.destination,
                "model_source": settings.model_source_label(),
            },
        }
        # Escape refresh got nothing useful → first set
        if is_refresh and (not result or not result.offers):
            first_snap = load_first("escape", session_id=sess)
            if first_snap and first_snap.get("result"):
                from yonder.last_search import hydrate_escape

                restored = hydrate_escape(first_snap)
                if restored.get("result") and restored["result"].offers:
                    notes.append("Showing earlier results")
                    nonlocal restored_first
                    restored_first = True
                    escape_override = {
                        "ask": restored.get("ask") or prompt,
                        "form": restored.get("form") or form,
                        "result": restored["result"],
                        "parsed": restored.get("parsed"),
                        "analysis": restored.get("analysis"),
                        "dest_theme": restored.get("dest_theme"),
                        "place_book": restored.get("place_book"),
                        "attribution": attr_meta,
                        "trip_meta": {
                            **attr_meta,
                            "prompt": prompt,
                            "vibe": vibe,
                        },
                    }
                    active_mode = "escape"
                    return

        save_last(
            "escape",
            session_id=sess,
            payload={
                "ask": prompt,
                "form": form,
                "result": result,
                "parsed": trip,
                "analysis": None,
                "dest_theme": dest_theme,
                "place_book": place_book,
                "attribution": attr_meta,
            },
            pin_first=not is_refresh,
        )
        active_mode = "escape"

    async def _do_detour() -> None:
        nonlocal detour_override, active_mode, restored_first
        if search_id and is_cancelled(search_id):
            errors.append("Detour skipped — user hit Skip")
            return
        remaining = _soft_remaining()
        ideas: list = []
        req: AdventureRequest | None = None

        def _local_getaway_fallback(reason: str = "") -> tuple:
            home = origin_override if origin_pinned else (_guess_home_iata(prompt) or home_iata)
            # Honor a clearly-named A→B route even without Grok: the traveler
            # said where they want to end up, so plan origin → stop → dest,
            # not a round trip back home. Prefer the route the Escape half
            # already resolved (works for non-English prompts), then the
            # English text detector, then a home getaway as last resort.
            _route = resolved_route or detect_route_iatas(prompt)
            _dest = _route[1] if _route else home
            if _route and not origin_pinned:
                home = _route[0]
            from yonder.intent import has_proximity_intent as _has_proximity
            local_req = AdventureRequest(
                origin=home,
                destination=_dest,
                depart_date=date.fromisoformat(depart),
                arrive_by=None,
                adults=1,
                currency=currency,
                cabin=CabinClass.ECONOMY,
                min_stop_days=min_stop,
                max_stop_days=max_stop,
                max_candidates=max_cand,
                vibe=vibe,
                prompt=prompt,
                avoid_countries=avoid,
                visited_countries=visited,
                visited_tiles=visited_tiles_l,
                trip_kind="detour" if _route else "getaway",
                include_direct=False,
                proximity_mode=_has_proximity(prompt),
            )
            local_ideas = seed_ideas(
                local_req, exclude_iatas=exclude_iatas, shuffle=is_refresh, owner_sess=sess,
            )
            if not local_ideas:
                msg = "No unvisited getaway cities left after passport map."
                if reason:
                    msg = f"{msg} ({reason})"
                raise ValueError(msg)
            return local_req, local_ideas[:max_cand]

        # Dataset chip seeds only (never prior Saves). Refresh always invents fresh.
        use_fast_seeds = (
            bool(chip_seeds)
            and not is_refresh
            and chip_source in (
                "chip",
                "template",
                "dataset",
            )
        )
        if use_fast_seeds and chip_seeds:
            try:
                req, ideas = _local_getaway_fallback("chip seeds")
                pass  # fast path via chip seeds
            except Exception as exc:  # noqa: BLE001
                req, ideas = None, []
                errors.append(f"Chip seed fallback: {exc}")
                use_fast_seeds = False

        if not use_fast_seeds and settings.grok_ready():
            try:
                # Soft invent cap; past aim still allow a full parse attempt
                invent_timeout = min(22.0, max(8.0, remaining * 0.5 if remaining < 100 else 18.0))
                invent_prompt = prompt
                if exclude_iatas:
                    invent_prompt = (
                        f"{prompt}\n\n"
                        f"IMPORTANT: Do NOT propose these airports "
                        f"(already saved or shown): "
                        f"{', '.join(sorted(exclude_iatas)[:24])}. Pick different cities."
                    )
                if _uni_adventure is not None:
                    # Unified cold-start call already invented cities — no extra Grok call
                    req, ideas = _uni_adventure
                else:
                    async with GrokClient(settings) as grok:
                        req, ideas = await asyncio.wait_for(
                            grok.translate_adventure(
                                prompt=invent_prompt,
                                form={
                                    "origin": home_iata,
                                    "destination": "",
                                    "depart": depart,
                                    "arrive_by": "",
                                    "min_stop_days": min_stop,
                                    "max_stop_days": max_stop,
                                    "max_candidates": max_cand,
                                    "currency": currency,
                                    "vibe": vibe,
                                    "avoid_countries": avoid,
                                    "visited_countries": visited,
                                },
                                default_currency=currency,
                                # Refresh wants novelty — learned seed
                                # candidates would re-suggest the same places
                                seed_learned=not is_refresh,
                                anchor_legs=anchor_legs,
                            ),
                            timeout=invent_timeout,
                        )
                        _route_usage.append(grok.accumulated_usage)
                if search_id and is_cancelled(search_id):
                    errors.append("Detour invent finished after Skip — packaging what we can")
                trip_kind = (req.trip_kind or "detour").lower()
                if req.origin == req.destination:
                    # Prompt clearly names two different cities → correct the
                    # parse instead of silently forcing a getaway. The route
                    # the Escape half resolved wins over raw text detection.
                    route = resolved_route or detect_route_iatas(prompt)
                    if route:
                        req = req.model_copy(
                            update={"origin": route[0], "destination": route[1]}
                        )
                        trip_kind = "detour"
                    else:
                        trip_kind = "getaway"
                req = req.model_copy(
                    update={
                        "depart_date": date.fromisoformat(depart),
                        "currency": currency,
                        "min_stop_days": min_stop,
                        "max_stop_days": max_stop,
                        "max_candidates": max_cand,
                        "avoid_countries": avoid,
                        "visited_countries": visited,
                        "visited_tiles": visited_tiles_l,
                        "vibe": vibe,
                        "prompt": prompt,
                        "trip_kind": trip_kind,
                        "include_direct": False,
                        "adults": 1,
                        "cabin": CabinClass.ECONOMY,
                    }
                )
                # Refresh: the Depart field origin is pinned — it beats prompt text
                if origin_pinned and (req.origin or "").upper() != origin_override:
                    upd: dict = {"origin": origin_override}
                    if trip_kind == "getaway" or req.origin == req.destination:
                        upd["destination"] = origin_override
                    req = req.model_copy(update=upd)
                if not ideas:
                    ideas = seed_ideas(
                        req, exclude_iatas=exclude_iatas, shuffle=is_refresh, owner_sess=sess,
                    )
            except Exception as exc:  # noqa: BLE001
                req, ideas = _local_getaway_fallback(str(exc)[:120])
        elif not use_fast_seeds:
            req, ideas = _local_getaway_fallback("Grok offline")

        # Drop excluded cities (Saves + already-shown + refresh)
        if req is not None and exclude_iatas and ideas:
            ideas = [i for i in ideas if (i.iata or "").upper() not in exclude_iatas]
            if not ideas:
                ideas = seed_ideas(
                    req, exclude_iatas=exclude_iatas, shuffle=True, owner_sess=sess,
                )

        # Optional non-Save chip seeds only (dataset pattern, not ★ Saves)
        if req is not None and chip_seeds and not is_refresh:
            from yonder.adventure import StopoverIdea

            if not ideas:
                ideas = seed_ideas(req, exclude_iatas=exclude_iatas, owner_sess=sess)
            for h in reversed(chip_seeds):
                code = h["iata"]
                if code in exclude_iatas:
                    continue
                if any(i.iata == code for i in ideas):
                    continue
                ideas.insert(
                    0,
                    StopoverIdea(
                        iata=code,
                        city=h.get("city") or code,
                        stay_days=max(min_stop, min(max_stop, 4)),
                        why=h.get("why") or "from suggestion chip",
                        vibe_tags=[vibe] if vibe else [],
                        source=h.get("kind") or "chip_seed",
                    ),
                )
        elif req is not None and not ideas:
            ideas = seed_ideas(
                req, exclude_iatas=exclude_iatas, shuffle=is_refresh, owner_sess=sess,
            )

        # Pin user-named stops as first candidates and build multi-stop chain.
        # Handles single: "stop over in Tokyo" → pin Tokyo as first idea.
        # Handles multi: "stopping in Tokyo and Hong Kong" → pin both AND pass
        # named_stop_chain so plan_adventure prices them as one chained itinerary.
        named_stop_chain: list = []
        if req is not None and ideas is not None:
            from yonder.intent import extract_named_stops as _extract_named_stops
            _pin_cities = _extract_named_stops(prompt)
            if _pin_cities:
                from yonder.airports import iata_for_city as _iata_for_city
                from yonder.airports import city_country_for_iata as _cc_for_iata
                from yonder.adventure import StopoverIdea as _StopoverIdea
                _orig = (req.origin or "").upper()
                _dest = (req.destination or "").upper()
                _insert_pos = 0  # insert pinned stops at front in order
                for _pin_city in _pin_cities:
                    _pin_raw = _pin_city.upper()
                    _pin_iata = (
                        _pin_raw
                        if (len(_pin_raw) == 3 and _pin_raw.isalpha())
                        else _iata_for_city(_pin_city)
                    )
                    if not _pin_iata or _pin_iata in (_orig, _dest):
                        continue
                    _cc = _cc_for_iata(_pin_iata)
                    _idea = _StopoverIdea(
                        iata=_pin_iata,
                        city=(_cc[0] if _cc else _pin_city.title()),
                        country=(_cc[1] if _cc else None),
                        stay_days=max(min_stop, min(max_stop, 3)),
                        why="You asked to stop here",
                        vibe_tags=[vibe] if vibe else [],
                        source="user",
                    )
                    # Add to the named_stop_chain (for multi-stop itinerary)
                    named_stop_chain.append(_idea)
                    # Also pin in ideas list so the stop is priced individually too
                    if not any((i.iata or "").upper() == _pin_iata for i in ideas):
                        ideas.insert(_insert_pos, _idea)
                        _insert_pos += 1

        assert req is not None
        if search_id and is_cancelled(search_id) and not ideas:
            errors.append("Detour cancelled before pricing")
            return
        # No hard outer timeout — plan_adventure honors Skip via cancel_id
        result = await plan_adventure(
            req,
            ideas,
            named_stop_chain=named_stop_chain if len(named_stop_chain) >= 2 else None,
            settings=settings,
            include_mock=mock,
            cancel_id=search_id or None,
            exclude_iatas=exclude_iatas,
            owner_sess=sess,
        )
        result = _mark_missing_fares_adventure(result)
        # Refresh found nothing new → restore first result set
        if is_refresh and not (result.itineraries or []):
            first_snap = load_first("detour", session_id=sess)
            if first_snap and first_snap.get("result"):
                from yonder.last_search import hydrate_detour

                restored = hydrate_detour(first_snap)
                if restored.get("result") and restored["result"].itineraries:
                    result = restored["result"]
                    place_books_restored = restored.get("place_books") or {}
                    notes.append("Showing earlier results")
                    restored_first = True
                    detour_override = {
                        "form": restored.get("form") or det_form,
                        "result": result,
                        "trip_meta": restored.get("trip_meta") or {},
                        "place_books": place_books_restored,
                        "attribution": attr_meta,
                    }
                    if decision.shape != "mix":
                        active_mode = "detour"
                    return

        # Stamp vibe on itineraries
        vt = vibe_theme(vibe)
        try:
            stamped = []
            for it in result.itineraries:
                stamped.append(
                    it.model_copy(
                        update={
                            "theme_primary": vt["color"],
                            "theme_accent": vt["deep"],
                            "theme_label": vt["label"],
                        }
                    )
                )
            result = result.model_copy(update={"itineraries": stamped})
        except Exception:
            pass
        trip_meta = {
            "prompt": prompt,
            "trip_prompt": prompt,
            "vibe": vibe,
            "vibe_color": vt["color"],
            "origin": result.request.origin,
            "destination": result.request.destination,
            "visited": visited,
            "avoid": avoid,
            "intent": decision.shape,
            "intent_rationale": decision.rationale,
            "model_source": settings.model_source_label(),
            **attr_meta,
        }
        form = {
            **det_form,
            "prompt": prompt,
            "origin": result.request.origin,
            "destination": result.request.destination,
            "depart": depart,
            "vibe": vibe,
            "vibe_color": vt["color"],
            "min_stop_days": min_stop,
            "max_stop_days": max_stop,
        }
        # Field notes: cache always; live Grok only if user didn't Skip
        place_books: dict = {}
        try:
            from yonder.encyclopedia import briefs_for_stops, stops_from_itineraries

            stops = stops_from_itineraries(result.itineraries, limit=5)
            skipped = bool(search_id and is_cancelled(search_id))
            place_books = await briefs_for_stops(
                settings,
                stops,
                max_n=0 if skipped else 3,
                cache_only=skipped,
                cancel_id=search_id or None,
                user_prompt=prompt,
                trip_vibe=vibe,
            )
            pass  # field notes loaded
        except Exception as pb_exc:  # noqa: BLE001
            place_books = {}
            errors.append(f"Field notes: {str(pb_exc)[:80]}")

        detour_override = {
            "form": form,
            "result": result,
            "trip_meta": trip_meta,
            "place_books": place_books,
            "attribution": attr_meta,
        }
        save_last(
            "detour",
            session_id=sess,
            payload={
                "form": form,
                "result": result,
                "trip_meta": trip_meta,
                "place_books": place_books,
            },
            pin_first=not is_refresh,
        )
        active_mode = "detour"

    # Quest is no longer called from the main search — it runs on demand via
    # the "Plan a Quest" button and the /api/quest/plan endpoint.

    # Detour recycling is now on-demand via /api/detour/plan.
    # Only Escape recycling runs eagerly in the main search.
    _recycled_esc: "UnifiedSearchResult | None" = None
    _recycle_off = (os.environ.get("YONDER_DISABLE_RECYCLE") or "").strip().lower() in ("1", "true", "yes")
    if not settings.testing and not _recycle_off:
        try:
            from yonder.recycle import find_recycled_escape
            if not is_refresh:
                _recycled_esc = find_recycled_escape(
                    prompt=prompt,
                    vibe=vibe,
                    origin=home_iata,
                    depart=depart,
                    currency=currency,
                    owner_sess=sess,
                )
        except Exception:  # noqa: BLE001
            _recycled_esc = None

    try:
        # ── Escape recycle: best saved escape trip → fare-missing search result ──
        if _recycled_esc is not None and not escape_override.get("result"):
            try:
                _re_q = _recycled_esc.query
                from yonder.vibe_theme import vibe_theme as _vt_re
                _vt_re_d = _vt_re(vibe)
                _re_form = {
                    "origin": _re_q.origin,
                    "destination": _re_q.destination,
                    "depart": _re_q.depart_date.isoformat(),
                    "return_date": "",
                    "adults": 1,
                    "currency": currency,
                    "nonstop": False,
                    "vibe": vibe,
                    "vibe_color": _vt_re_d["color"],
                }
                _re_dest_theme = _dest_theme(_re_q.destination)
                _re_place_book = None
                try:
                    from yonder.countries import country_for_iata as _cfi_re
                    from yonder.encyclopedia import cache_key as _ck_re, get_cached as _gc_re, _payload_lang_mismatch as _plm_re, _activity_links as _al_re
                    from yonder.lang import detect_lang as _dl_re
                    _re_bl = _dl_re(prompt)
                    _re_cc = _cfi_re(_re_q.destination)
                    _re_key = _ck_re(_re_q.destination, _re_cc, None, lang=_re_bl)
                    _re_hit = _gc_re(_re_key) if _re_key else None
                    if _re_hit and not _plm_re(_re_hit, _re_bl):
                        _re_place_book = {
                            **_re_hit,
                            "iata": _re_q.destination,
                            "country": _re_cc,
                            "from_cache": True,
                            "activity_links": await _al_re(
                                settings, iata=_re_q.destination, city=None,
                                trip_vibe=vibe, user_prompt=prompt,
                            ),
                        }
                except Exception:
                    pass
                escape_override = {
                    "ask": prompt,
                    "form": _re_form,
                    "result": _recycled_esc,
                    "parsed": None,
                    "analysis": None,
                    "dest_theme": _re_dest_theme,
                    "place_book": _re_place_book,
                    "attribution": attr_meta,
                    "trip_meta": {
                        **attr_meta,
                        "prompt": prompt,
                        "vibe": vibe,
                        "origin": _re_q.origin,
                        "destination": _re_q.destination,
                        "model_source": None,
                    },
                }
                # Let detour (if it runs fresh) use the recycled escape route
                _ro_re = _re_q.origin.upper()
                _rd_re = _re_q.destination.upper()
                if (
                    len(_ro_re) == 3 and _ro_re.isalpha()
                    and len(_rd_re) == 3 and _rd_re.isalpha()
                    and _ro_re != _rd_re
                ):
                    resolved_route = (_ro_re, _rd_re)
                save_last(
                    "escape",
                    session_id=sess,
                    payload={
                        "ask": prompt,
                        "form": _re_form,
                        "result": _recycled_esc,
                        "parsed": None,
                        "analysis": None,
                        "dest_theme": _re_dest_theme,
                        "place_book": _re_place_book,
                        "attribution": attr_meta,
                    },
                    pin_first=True,
                )
                active_mode = "escape"
            except Exception:  # noqa: BLE001
                _recycled_esc = None

        async def _safe(fn, name: str) -> None:
            try:
                await fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")

        # ── Trip-gap awareness: read saved-trip gaps before launching panels ────
        # Fast sync DB read — never blocks meaningful work.
        try:
            trip_gaps = detect_trip_gaps(last_n=5, owner_sess=sess)
        except Exception:
            trip_gaps = []

        # ── Anchored planning: upcoming saved legs the AI may connect into ────
        # Users without upcoming saved trips get [] → prompts and results are
        # byte-identical to today's behavior. Past-dated saves never qualify.
        try:
            from yonder.saved import upcoming_anchor_legs as _upcoming_anchors

            anchor_legs = _upcoming_anchors(limit=3, owner_sess=sess)
        except Exception:
            anchor_legs = []

        # ── Unified cold-start plan: 2 Grok calls → 1 ──────────────────────────
        # When NO escape panel is recycled (cold-start Find), one combined
        # structured call covers escape-parse.  Quest and Detour run on demand.
        _uni_trip = None        # ParsedTrip | None → _do_escape
        _uni_adventure = None   # kept for plan_unified compat; unused since _do_detour is on-demand
        _unified_used = False
        _chip_fast_seeds = (
            bool(chip_seeds)
            and not is_refresh
            and chip_source in ("chip", "template", "dataset")
        )
        if (
            _recycled_esc is None
            and settings.grok_ready()
            and not is_refresh
            and not _chip_fast_seeds
            and not (search_id and is_cancelled(search_id))
        ):
            try:
                import httpx as _uni_httpx

                async with _uni_httpx.AsyncClient(
                    timeout=_uni_httpx.Timeout(
                        connect=8.0, read=32.0, write=10.0, pool=8.0
                    )
                ) as _uni_http:
                    _ugrok = GrokClient(settings, client=_uni_http)
                    _uni = await asyncio.wait_for(
                        _ugrok.plan_unified(
                            prompt,
                            vibe,
                            home_iata,
                            depart_date=date.fromisoformat(depart),
                            currency=currency,
                            min_stop_days=min_stop,
                            max_stop_days=max_stop,
                            max_candidates=max_cand,
                            avoid=avoid,
                            visited=visited,
                            exclude_iatas=exclude_iatas,
                            # Refresh (novelty) must bypass the repeat-Find
                            # cache — a repeat answer is exactly what the
                            # user does NOT want. (This block only runs on
                            # non-refresh today; the flag keeps that safe.)
                            use_cache=not is_refresh,
                            anchor_legs=anchor_legs,
                            # Quest runs on demand — skip AI budget for it here
                            include_quest=False,
                        ),
                        timeout=34.0,
                    )
                    _route_usage.append(_ugrok.accumulated_usage)
                _uni_trip = _uni.get("escape")
                _uni_adventure = _uni.get("detour_cities")
                _unified_used = bool(_uni_trip)  # detour is on-demand; only escape matters
            except Exception as _uni_exc:  # noqa: BLE001 — fall back to 2 calls
                errors.append(f"Unified plan fell back: {str(_uni_exc)[:80]}")

        # ── Eager Quest: launch concurrently with Escape on EVERY search ──────
        # Quest has no correctness dependency on Escape's resolved destination:
        # plan_quest's exclude_dests is a soft, reorder-only hint. Fire the
        # background task immediately with whatever destination hints are
        # already known (unified parse, recycled route, form text, chip seed);
        # when none are known, launch with an empty exclusion list rather than
        # waiting ~40-50 s for Escape's pipeline to finish.
        quest_job_id: str | None = None
        if prompt and not (search_id and is_cancelled(search_id)):
            try:
                from yonder import quest_jobs as _qjobs

                try:
                    _q_depart_dt = date.fromisoformat(depart)
                except (ValueError, TypeError):
                    _q_depart_dt = date.today() + timedelta(days=45)
                quest_job_id = _qjobs.create_job(home_iata=home_iata, vibe=vibe)

                # Best-effort exclusion hints, in reliability order:
                # unified parse > recycled/resolved route > prompt text > chip seeds.
                _q_excl_early: list[str] = []
                if _uni_trip is not None:
                    _uni_dest = str(getattr(_uni_trip, "destination", "") or "").upper()
                    if len(_uni_dest) == 3 and _uni_dest.isalpha():
                        _q_excl_early = [_uni_dest]
                if not _q_excl_early and resolved_route:
                    _q_excl_early = [resolved_route[1]]
                if not _q_excl_early:
                    try:
                        _q_route_hint = detect_route_iatas(prompt)
                        if _q_route_hint:
                            _q_excl_early = [_q_route_hint[1]]
                    except Exception:  # noqa: BLE001 — hint only
                        pass
                if not _q_excl_early and chip_seeds:
                    _q_excl_early = [
                        (h.get("iata") or "").upper()
                        for h in chip_seeds
                        if len(h.get("iata") or "") == 3
                    ][:1]

                asyncio.create_task(
                    _run_eager_quest(
                        quest_job_id,
                        settings=settings,
                        prompt=prompt,
                        vibe=vibe,
                        home_iata=home_iata,
                        depart_dt=_q_depart_dt,
                        currency=currency,
                        quest_days=quest_days,
                        mock=mock,
                        avoid=avoid,
                        visited=visited,
                        anchor_legs=anchor_legs,
                        exclude_dests=_q_excl_early,
                    )
                )
            except Exception:  # noqa: BLE001 — eager quest must never break search
                quest_job_id = None

        # ── Run only panels not covered by the recycle pool ────────────────────
        # Each recycled panel saves one Grok call + N flight-API calls.
        # Quest and Detour are on-demand — the main search runs Escape only.
        _gather_tasks = []
        if _recycled_esc is None:
            _gather_tasks.append(_safe(_do_escape, "Escape"))
        if _gather_tasks:
            await asyncio.gather(*_gather_tasks)

        # ── Search cost accounting ───────────────────────────────────────────────
        import logging as _sc_log
        _rp = [p for p, r in [("escape", _recycled_esc)] if r is not None]
        if _unified_used:
            # 1 unified call; +1 fallback only when escape parse failed
            _grok_calls = 1 + (0 if _uni_trip else 1)
        else:
            _grok_calls = 1 - len(_rp)
        _sc_log.getLogger("yonder.cost").info(
            "search_cost grok_calls≈%d recycled_panels=[%s] unified=%s",
            _grok_calls,
            ",".join(_rp) or "none",
            "yes" if _unified_used else "no",
        )

        has_esc = bool(escape_override.get("result"))
        has_det = bool(detour_override.get("result"))
        # Quest is on-demand — the main search never populates quest_override.result
        has_quest = False
        quest_empty = False

        # ── Apply gap labels to results that fill a known saved-trip gap ──────
        # Soft constraints only — normal results still appear if nothing matches.
        if trip_gaps:
            try:
                # Escape: label if the search route matches any gap
                if has_esc and escape_override.get("result"):
                    _esc_q2 = getattr(escape_override["result"], "query", None)
                    if _esc_q2:
                        _eq_from = (getattr(_esc_q2, "origin", "") or "").upper()
                        _eq_to = (getattr(_esc_q2, "destination", "") or "").upper()
                        for _g in trip_gaps:
                            if (
                                (_g.from_iata or "").upper() == _eq_from
                                and (_g.to_iata or "").upper() == _eq_to
                            ):
                                escape_override["escape_gap_label"] = _g.context_label
                                break

                # Detour: float gap-filling itinerary to position 0
                if has_det and detour_override.get("result"):
                    _det_res2 = detour_override["result"]
                    _its2 = list(getattr(_det_res2, "itineraries", None) or [])
                    _gap_idx2: int | None = None
                    _gap_lbl2: str | None = None
                    for _i2, _it2 in enumerate(_its2):
                        _it_legs = getattr(_it2, "legs", None) or []
                        _it_from = (
                            (_it_legs[0].from_iata if _it_legs else "")
                            or getattr(_it2, "origin", "")
                            or ""
                        ).upper()
                        _it_to = (
                            (_it_legs[-1].to_iata if _it_legs else "")
                            or getattr(_it2, "destination", "")
                            or ""
                        ).upper()
                        for _g2 in trip_gaps:
                            if (
                                (_g2.from_iata or "").upper() == _it_from
                                and (_g2.to_iata or "").upper() == _it_to
                            ):
                                _gap_idx2 = _i2
                                _gap_lbl2 = _g2.context_label
                                break
                        if _gap_idx2 is not None:
                            break
                    if _gap_idx2 is not None and _gap_lbl2:
                        _labelled_it = _its2[_gap_idx2].model_copy(
                            update={"gap_label": _gap_lbl2}
                        )
                        _new_its = [_labelled_it] + [
                            x for j2, x in enumerate(_its2) if j2 != _gap_idx2
                        ]
                        detour_override["result"] = _det_res2.model_copy(
                            update={"itineraries": _new_its}
                        )

            except Exception:
                pass  # gap labelling is best-effort — never crash the search

        # ── Anchored planning: badge results that connect into a saved leg ────
        # A result "connects" when it ends at an anchor's departure city,
        # arriving before the saved leg departs (see adventure.match_anchor —
        # dead connecting routes are skipped there via route knowledge).
        if anchor_legs:
            try:
                from yonder.adventure import match_anchor as _match_anchor

                # Escape: destination feeds an anchor's departure city
                if (
                    has_esc
                    and escape_override.get("result")
                    and not escape_override.get("escape_gap_label")
                ):
                    _esc_qa = getattr(escape_override["result"], "query", None)
                    if _esc_qa:
                        _a_esc = _match_anchor(
                            dest_iata=getattr(_esc_qa, "destination", None),
                            arrive_date=getattr(_esc_qa, "depart_date", None),
                            anchors=anchor_legs,
                            from_iata=getattr(_esc_qa, "origin", None),
                        )
                        if _a_esc:
                            escape_override["escape_anchor_label"] = _a_esc["label"]

                # Detour: itinerary's final leg lands at an anchor's departure city
                if has_det and detour_override.get("result"):
                    _det_ra = detour_override["result"]
                    _its_a = list(getattr(_det_ra, "itineraries", None) or [])
                    _changed_a = False
                    for _ia, _ita in enumerate(_its_a):
                        if getattr(_ita, "gap_label", None) or getattr(
                            _ita, "anchor_label", None
                        ):
                            continue
                        _legs_a = getattr(_ita, "legs", None) or []
                        if not _legs_a:
                            continue
                        _last_a = _legs_a[-1]
                        _a_det = _match_anchor(
                            dest_iata=getattr(_last_a, "to_iata", None),
                            arrive_date=getattr(_last_a, "depart_date", None),
                            anchors=anchor_legs,
                            from_iata=getattr(_last_a, "from_iata", None),
                        )
                        if _a_det:
                            _its_a[_ia] = _ita.model_copy(
                                update={"anchor_label": _a_det["label"]}
                            )
                            _changed_a = True
                    if _changed_a:
                        detour_override["result"] = _det_ra.model_copy(
                            update={"itineraries": _its_a}
                        )

            except Exception:
                pass  # anchor badging is best-effort — never crash the search

        # ── Vibe Base: cheapest return to the escape destination ─────────────
        # Reuses direct_price from the detour result (computed as baseline there).
        # Falls back to a Check Fares slot when direct_price is None (common).
        vibe_base: dict | None = None
        escape_direct_offer = None  # most-direct offer shown on the Escape card
        if has_esc:
            _esc_res = escape_override.get("result")
            _esc_q = getattr(_esc_res, "query", None) if _esc_res else None
            if _esc_q:
                from datetime import timedelta as _vtd
                _vbt = vibe_theme(vibe)
                _vb_depart = getattr(_esc_q, "depart_date", None)
                _vb_return = (_vb_depart + _vtd(days=7)) if _vb_depart else None
                _det_direct: float | None = None
                if has_det:
                    _det_res = detour_override.get("result")
                    if _det_res:
                        _det_direct = getattr(_det_res, "direct_price", None)

                # Derive two offers from the same result set — no extra search.
                # engine.py already sorts by (price, total_stops), so offers[0]
                # is the cheapest.  Most-direct = fewest outbound stops, then
                # shortest outbound duration.
                _all_offers = list(getattr(_esc_res, "offers", None) or [])
                _cheap_offer = _all_offers[0] if _all_offers else None
                _sorted_direct = sorted(
                    _all_offers,
                    key=lambda _o: (
                        _o.stops_out,
                        _o.duration_out_minutes if _o.duration_out_minutes is not None else 9999,
                    ),
                )
                _direct_offer = _sorted_direct[0] if _sorted_direct else None
                escape_direct_offer = _direct_offer

                # Suppress vibe card when cheapest == most-direct (same provider
                # and price) so we never show two identical boarding passes.
                _vibe_distinct = (
                    _cheap_offer is not None
                    and _direct_offer is not None
                    and not (
                        _cheap_offer.provider == _direct_offer.provider
                        and round(_cheap_offer.price, 2) == round(_direct_offer.price, 2)
                    )
                )

                vibe_base = {
                    "origin": str(getattr(_esc_q, "origin", "") or ""),
                    "destination": str(getattr(_esc_q, "destination", "") or ""),
                    "depart_date": _vb_depart.isoformat() if _vb_depart else "",
                    "return_date": _vb_return.isoformat() if _vb_return else "",
                    "currency": str(getattr(_esc_q, "currency", currency) or currency),
                    "adults": int(getattr(_esc_q, "adults", 1) or 1),
                    "direct_price": _det_direct,
                    "vibe_label": _vbt["label"],
                    "vibe_emoji": VIBE_EMOJI.get(_vbt["id"], ""),
                    "vibe_color": _vbt["color"],
                    "result": _esc_res,
                    "cheap_offer": _cheap_offer,
                }
                # Suppress vibe panel when it would duplicate the escape card.
                if not _vibe_distinct:
                    vibe_base = None
                # Apply gap label to vibe_base if its return leg fills a gap
                if vibe_base and trip_gaps:
                    try:
                        _vb_dest = vibe_base["destination"].upper()
                        _vb_orig = vibe_base["origin"].upper()
                        for _gvb in trip_gaps:
                            if (
                                (_gvb.from_iata or "").upper() == _vb_dest
                                and (_gvb.to_iata or "").upper() == _vb_orig
                            ):
                                vibe_base["gap_label"] = _gvb.context_label
                                break
                    except Exception:
                        pass

        # Vibe-learning: tier-1 "searched" signal per destination that came back.
        # IDs are generated up front and returned in data-signal-* attributes so
        # follow-up engagement events can upgrade them; the DB write itself runs
        # in a thread executor and never blocks the response.
        # Skipped when mock=True (Test Data checkbox or no live providers) so
        # fake-fare destinations never nudge pill ranking.  The MOCK env-var
        # guard inside vibe_signals catches the MOCK= process-level flag; this
        # check catches the per-request form-level flag.
        try:
            if not mock:
                import uuid as _uuid

                from yonder.vibe_signals import record_search
                _loop = asyncio.get_running_loop()
                _sess = click_id or None
                _ms_label = settings.model_source_label() or None
                _ic = decision.confidence
                _ir = decision.rationale
                if has_esc:
                    esc_tm = escape_override.get("trip_meta") or {}
                    esc_dest = str(
                        esc_tm.get("destination")
                        or (escape_override.get("form") or {}).get("destination")
                        or ""
                    ).upper()
                    esc_res = escape_override.get("result")
                    esc_n = len(getattr(esc_res, "offers", None) or [])
                    # Always record — including zero-result — so low_confidence_misses
                    # can surface this prompt for the paraphrase regression suite.
                    if len(esc_dest) == 3 and esc_dest.isalpha():
                        esc_sig = _uuid.uuid4().hex
                        _loop.run_in_executor(
                            None,
                            lambda d=esc_dest, n=esc_n, s=esc_sig, ic=_ic, ir=_ir: record_search(
                                vibe=vibe,
                                origin=home_iata,
                                dest_iata=d,
                                search_type="escape",
                                result_count=n,
                                prompt=prompt,
                                session_hash=_sess,
                                signal_id=s,
                                model_source=_ms_label,
                                intent_confidence=ic,
                                intent_rationale=ir,
                            ),
                        )
                        if esc_n:
                            esc_tm["signal_id"] = esc_sig
                            escape_override["trip_meta"] = esc_tm
                if has_det:
                    det_res = detour_override.get("result")
                    det_tm = detour_override.get("trip_meta") or {}
                    its = list(getattr(det_res, "itineraries", None) or [])[:5]
                    sig_map: dict[str, str] = {}
                    _dic = decision.confidence
                    _dir = decision.rationale
                    for it in its:
                        dest = str(getattr(it, "stop_iata", "") or "").upper()
                        if len(dest) != 3 or not dest.isalpha() or dest in sig_map:
                            continue
                        sid = _uuid.uuid4().hex
                        sig_map[dest] = sid
                        _loop.run_in_executor(
                            None,
                            lambda d=dest, s=sid, ic=_dic, ir=_dir: record_search(
                                vibe=vibe,
                                origin=home_iata,
                                dest_iata=d,
                                search_type="detour",
                                result_count=len(its),
                                prompt=prompt,
                                session_hash=_sess,
                                signal_id=s,
                                model_source=_ms_label,
                                intent_confidence=ic,
                                intent_rationale=ir,
                            ),
                        )
                    if sig_map:
                        det_tm["signal_ids"] = sig_map
                        det_tm["signal_id"] = next(iter(sig_map.values()))
                        detour_override["trip_meta"] = det_tm
                    elif not has_esc:
                        # Zero detour itineraries AND no escape side: write a single
                        # tombstone signal so low_confidence_misses can surface the prompt.
                        _zero_dest = (
                            str(det_tm.get("destination") or det_tm.get("stop_iata") or home_iata or "")
                            .strip().upper()
                        )
                        if len(_zero_dest) == 3 and _zero_dest.isalpha():
                            sid0 = _uuid.uuid4().hex
                            _loop.run_in_executor(
                                None,
                                lambda s=sid0, d=_zero_dest, ic=_dic, ir=_dir: record_search(
                                    vibe=vibe,
                                    origin=home_iata,
                                    dest_iata=d,
                                    search_type="detour",
                                    result_count=0,
                                    prompt=prompt,
                                    session_hash=_sess,
                                    signal_id=s,
                                    model_source=_ms_label,
                                    intent_confidence=ic,
                                    intent_rationale=ir,
                                ),
                            )
                # Total failure: both sides failed — write one tombstone with home_iata
                # so low_confidence_misses can surface the prompt for the test suite.
                if not has_esc and not has_det and home_iata and len(home_iata) == 3 and home_iata.isalpha():
                    sid_fail = _uuid.uuid4().hex
                    _loop.run_in_executor(
                        None,
                        lambda s=sid_fail, ic=_ic, ir=_ir: record_search(
                            vibe=vibe,
                            origin=home_iata,
                            dest_iata=home_iata,
                            search_type=decision.shape,
                            result_count=0,
                            prompt=prompt,
                            session_hash=_sess,
                            signal_id=s,
                            model_source=_ms_label,
                            intent_confidence=ic,
                            intent_rationale=ir,
                        ),
                    )
        except Exception:
            pass

        if not has_esc and not has_det:
            # Detour shape with form context: render the on-demand button card even
            # when the escape pricing step produced no result (e.g. parse failure,
            # force_mode=detour, or the AI chose a detour intent but search failed).
            _detour_button_ok = (
                decision.shape in ("detour", "mix")
                and bool(detour_override.get("form"))
            )
            if not _detour_button_ok:
                raise ValueError(
                    "; ".join(errors) if errors else "Nothing priced — try again or Turbo."
                )

        # Detour is on-demand — has_det is always False in the main search.
        # Shape "detour" keeps active_mode=detour so the button card shows first.
        if decision.shape == "detour":
            active_mode = "detour"
        else:
            active_mode = "escape"

        if search_id and is_cancelled(search_id):
            notes.append("Completed early (Skip)")

        err_msg = None
        if errors:
            err_msg = " · ".join(notes + errors)
        else:
            err_msg = " · ".join(notes) if notes else None

        # Eager Quest launches in a single site above, concurrent with Escape;
        # by this point the job (if any) is already running.

        ctx = _compose_page_ctx(
            settings,
            session_id=sess,
            mode=active_mode,
            error=None if has_esc or has_det else err_msg,
            escape_override=escape_override if has_esc or escape_override.get("ask") else None,
            detour_override=detour_override if has_det or detour_override.get("form") else None,
            lock_vibe=True,
        )
        # Always attach both sides when present
        if has_esc:
            base_esc = ctx.get("escape_panel") if isinstance(ctx.get("escape_panel"), dict) else {}
            ctx["escape_panel"] = {**base_esc, **escape_override}
        if has_det:
            base_det = ctx.get("detour_panel") if isinstance(ctx.get("detour_panel"), dict) else {}
            ctx["detour_panel"] = {**base_det, **detour_override}
        # Eager Quest fills in via polling — expose the job id to the template.
        ctx["quest_job_id"] = quest_job_id
        if vibe_base:
            ctx["vibe_base"] = vibe_base
        if escape_direct_offer is not None:
            ctx["escape_direct_offer"] = escape_direct_offer
        ctx["intent_shape"] = decision.shape
        ctx["intent_rationale"] = decision.rationale
        ctx["result_filter"] = "all"
        if err_msg and (has_esc or has_det):
            ctx["intent_note"] = err_msg
        if _route_usage:
            _usage = merge_usage(*_route_usage)
            ctx["ai_usage_display"] = fmt_usage(_usage)
            if _usage.get("total_tokens"):
                asyncio.create_task(_log_ai_usage("explore", _usage))
        return templates.TemplateResponse(request, "index.html", ctx)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="escape",
                error=str(exc),
                escape_override={"ask": prompt, "form": empty_esc},
                detour_override={"form": det_form},
                lock_vibe=True,
            ),
            status_code=400,
        )
    finally:
        if search_id:
            clear_search_cancel(search_id)


@app.post("/api/quest/plan")
async def quest_plan_api(request: Request):
    """On-demand Quest planning endpoint — called by the 'Plan a Quest' button.

    Runs only the Quest planning + pricing pipeline, isolated from the main
    search lifecycle.  Applies a generous read timeout and one automatic retry
    on timeout so the user gets a clear message instead of a dangling spinner.

    Returns JSON: {ok: bool, html: str} — the caller replaces #quest-results
    innerHTML with the returned card HTML.
    """
    from datetime import timedelta

    settings = reload_settings()
    form_data = await request.form()

    def _s(key: str, fallback: str = "") -> str:
        v = form_data.get(key)
        return str(v).strip() if v is not None and str(v).strip() else fallback

    prompt = _s("prompt") or _s("ask")
    vibe = _s("vibe", "adventure").lower()
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"

    home_iata = settings.resolve_home_iata()
    origin_override = _s("origin").upper()
    if len(origin_override) == 3 and origin_override.isalpha():
        home_iata = origin_override

    _QUEST_DAYS_ALLOWED = {7, 10, 14, 21}
    try:
        quest_days = int(_s("quest_days", "10"))
        if quest_days not in _QUEST_DAYS_ALLOWED:
            quest_days = 10
    except (ValueError, TypeError):
        quest_days = 10

    defaults = _adventure_form_defaults(settings)
    depart = _s("depart", defaults["depart"])
    if not depart:
        depart = (date.today() + timedelta(days=45)).isoformat()

    avoid = settings.effective_avoid_country_list()
    visited = settings.visited_country_list()
    mock = not settings.configured_providers()

    try:
        anchor_legs: list = []
        from yonder.saved import upcoming_anchor_legs as _quest_anchors
        anchor_legs = _quest_anchors(limit=3, owner_sess=_req_sess(request))
    except Exception:
        anchor_legs = []

    def _render_quest(
        quest_panel_dict: dict,
        error_message: str | None = None,
        place_books: dict | None = None,
    ) -> str:
        """Render the quest results partial template to an HTML string."""
        tpl = templates.env.get_template("_quest_results_partial.html")
        return tpl.render(
            request=request,
            quest_panel=quest_panel_dict,
            home_iata_fallback=home_iata,
            vibe_fallback=vibe,
            error_message=error_message,
            place_books=place_books or {},
        )

    if not prompt:
        return JSONResponse({"ok": False, "error": "No prompt — type a vibe first.", "html": ""})

    if not settings.grok_ready():
        error_text = "Quest needs an AI key — add one in Settings."
        html = _render_quest(
            {"ask": prompt, "result": [], "home_iata": home_iata, "vibe": vibe,
             "error": error_text}
        )
        return JSONResponse({"ok": False, "error": error_text, "html": html})

    try:
        depart_dt = date.fromisoformat(depart)
    except (ValueError, TypeError):
        depart_dt = date.today() + timedelta(days=45)

    # Run plan_quest with an 80 s budget — grok-4.5 regularly needs 50-70 s
    # to draft 3 open-jaw itineraries, so a shorter budget makes Quest fail
    # while Detour/Escape (faster unified/cached paths) still work.
    async def _run_quest() -> list:
        return await asyncio.wait_for(
            plan_quest(
                prompt,
                vibe,
                home_iata,
                depart_dt,
                settings,
                quest_days=quest_days,
                include_mock=mock,
                avoid=avoid,
                visited=visited,
                anchor_legs=anchor_legs,
            ),
            timeout=80.0,
        )

    quest_ideas = None
    _last_err: str | None = None
    # One automatic retry on timeout — the first attempt occasionally hits a
    # slow upstream; a second try succeeds often enough to be worth 80 s more.
    for _attempt in range(2):
        _last_err = None
        try:
            quest_ideas = await _run_quest()
            break
        except (asyncio.TimeoutError, asyncio.CancelledError, httpx.TimeoutException):
            _last_err = "timeout"
            continue  # retry once on timeout only
        except httpx.HTTPError as _exc:  # network/protocol errors: str() often empty
            _last_err = repr(_exc)[:200]
            break
        except Exception as _exc:  # noqa: BLE001
            _last_err = (str(_exc) or repr(_exc))[:200]
            break

    if _last_err == "timeout":
        error_text = "The AI took too long — try again."
        html = _render_quest({}, error_message=error_text)
        return JSONResponse({"ok": False, "error": error_text, "html": html})

    if _last_err is not None:
        error_text = f"Quest couldn't reach the AI planner — {_last_err[:120]}"
        html = _render_quest({}, error_message=error_text)
        return JSONResponse({"ok": False, "error": error_text, "html": html})

    quest_panel = {
        "ask": prompt,
        "result": quest_ideas,
        "home_iata": home_iata,
        "vibe": vibe,
        "error": (
            None if quest_ideas
            else "Quest needs an AI key — add one in Settings"
        ),
    }

    # Fetch field-note briefs for all entry + exit IATAs in the ideas.
    # Matches Detour: cache-first, live Grok fetch when not cached (max_n=3),
    # result saved to DB so later requests are instant.
    place_books: dict = {}
    if quest_ideas:
        try:
            from yonder.encyclopedia import briefs_for_stops

            _q_stops: list[tuple[str | None, str | None, str | None]] = []
            _q_seen: set[str] = set()
            for _qi in quest_ideas:
                for _iata, _city in [
                    (getattr(_qi, "entry_iata", None), getattr(_qi, "entry_city", None)),
                    (getattr(_qi, "exit_iata", None), getattr(_qi, "exit_city", None)),
                ]:
                    _code = (_iata or "").upper()
                    if _code and _code not in _q_seen:
                        _q_seen.add(_code)
                        _q_stops.append((_code, None, _city))
            place_books = await briefs_for_stops(
                settings,
                _q_stops,
                max_n=3,
                cache_only=not settings.grok_ready(),
                user_prompt=prompt,
                trip_vibe=vibe,
            )
        except Exception:
            place_books = {}

    try:
        html = _render_quest(quest_panel, place_books=place_books)
    except Exception as _render_exc:
        return JSONResponse({"ok": False, "error": f"Render error: {_render_exc}", "html": ""})

    return JSONResponse({"ok": bool(quest_ideas), "html": html})


def _render_quest_partial_html(
    request: Request,
    quest_panel_dict: dict,
    *,
    home_iata: str,
    vibe: str,
    error_message: str | None = None,
    place_books: dict | None = None,
) -> str:
    """Render the quest results partial — shared by the eager-quest poll path."""
    tpl = templates.env.get_template("_quest_results_partial.html")
    return tpl.render(
        request=request,
        quest_panel=quest_panel_dict,
        home_iata_fallback=home_iata,
        vibe_fallback=vibe,
        error_message=error_message,
        place_books=place_books or {},
    )


async def _run_eager_quest(
    job_id: str,
    *,
    settings,
    prompt: str,
    vibe: str,
    home_iata: str,
    depart_dt: date,
    currency: str,
    quest_days: int,
    mock: bool,
    avoid: list,
    visited: list,
    anchor_legs: list,
    exclude_dests: list[str],
) -> None:
    """Background Quest planning kicked off by every main search.

    Flow: recycled saved-quest lookup first (no AI), then a fresh plan_quest
    guarded by an internal 80 s timeout. Escape's destination arrives in
    exclude_dests so Quest lands somewhere different on the same vibe.
    Results/errors are parked in the quest_jobs store; the page polls
    /api/quest/status/{job_id} and swaps in the rendered partial.
    """
    from yonder import quest_jobs as _qjobs

    try:
        quest_ideas = None

        # 1. Recycled saved quests — fast path, no AI call
        _recycle_off = (os.environ.get("YONDER_DISABLE_RECYCLE") or "").strip().lower() in ("1", "true", "yes")
        if not settings.testing and not _recycle_off:
            try:
                from yonder.recycle import find_recycled_quest
                quest_ideas = find_recycled_quest(
                    prompt=prompt,
                    vibe=vibe,
                    origin=home_iata,
                    depart=depart_dt.isoformat(),
                    currency=currency,
                )
            except Exception:  # noqa: BLE001 — recycle is best-effort
                quest_ideas = None

        # 2. Fresh AI plan when nothing recycled
        if quest_ideas is None:
            if not settings.grok_ready():
                _qjobs.set_done(
                    job_id,
                    quest_panel={
                        "ask": prompt,
                        "result": [],
                        "home_iata": home_iata,
                        "vibe": vibe,
                        "error": "Quest needs an AI key — add one in Settings",
                    },
                    ok=False,
                )
                return
            # Advance stage: AI ideation about to start
            _qjobs.set_stage(job_id, "scouting_routes")

            def _stage_cb(stage: str) -> None:
                """Forward plan_quest stage transitions to the job store."""
                _qjobs.set_stage(job_id, stage)

            quest_ideas = await asyncio.wait_for(
                plan_quest(
                    prompt,
                    vibe,
                    home_iata,
                    depart_dt,
                    settings,
                    quest_days=quest_days,
                    include_mock=mock,
                    avoid=avoid,
                    visited=visited,
                    anchor_legs=anchor_legs,
                    exclude_dests=exclude_dests,
                    stage_cb=_stage_cb,
                ),
                timeout=80.0,
            )
    except (asyncio.TimeoutError, asyncio.CancelledError, httpx.TimeoutException):
        _qjobs.set_error(job_id, "The AI took too long — try again.")
        return
    except httpx.HTTPError as _exc:  # network/protocol errors: str() often empty
        _qjobs.set_error(job_id, f"Quest couldn't reach the AI planner — {repr(_exc)[:120]}")
        return
    except Exception as _exc:  # noqa: BLE001
        _qjobs.set_error(
            job_id,
            f"Quest couldn't reach the AI planner — {(str(_exc) or repr(_exc))[:120]}",
        )
        return

    quest_panel = {
        "ask": prompt,
        "result": quest_ideas,
        "home_iata": home_iata,
        "vibe": vibe,
        "error": None if quest_ideas else "Quest found no ideas — try a different prompt",
    }

    place_books: dict = {}
    if quest_ideas:
        try:
            from yonder.encyclopedia import briefs_for_stops

            _q_stops: list[tuple[str | None, str | None, str | None]] = []
            _q_seen: set[str] = set()
            for _qi in quest_ideas:
                for _iata, _city in [
                    (getattr(_qi, "entry_iata", None), getattr(_qi, "entry_city", None)),
                    (getattr(_qi, "exit_iata", None), getattr(_qi, "exit_city", None)),
                ]:
                    _code = (_iata or "").upper()
                    if _code and _code not in _q_seen:
                        _q_seen.add(_code)
                        _q_stops.append((_code, None, _city))
            # Cache-only here: the eager job runs on EVERY search, so it must
            # not add Grok brief calls to the search budget. Missing notes are
            # filled client-side by the brief-slot poller (/api/place-brief),
            # and the on-demand retry path still fetches live.
            place_books = await briefs_for_stops(
                settings,
                _q_stops,
                max_n=3,
                cache_only=True,
                user_prompt=prompt,
                trip_vibe=vibe,
            )
        except Exception:  # noqa: BLE001 — briefs are optional garnish
            place_books = {}

    _qjobs.set_done(
        job_id,
        quest_panel=quest_panel,
        place_books=place_books,
        ok=bool(quest_ideas),
    )


@app.get("/api/quest/status/{job_id}")
async def quest_status_api(job_id: str, request: Request):
    """Poll endpoint for eager Quest jobs.

    Returns JSON: {status: pending|done|error, ok: bool, html: str}.
    HTML is rendered at read time so share links get a real request.
    An unknown/expired job returns an error card with the retry button.
    """
    from yonder import quest_jobs as _qjobs

    job = _qjobs.get_job(job_id)
    if job is None:
        _err = "Quest plan expired — try again."
        html = _render_quest_partial_html(
            request, {}, home_iata="", vibe="adventure", error_message=_err
        )
        return JSONResponse({"status": "error", "ok": False, "error": _err, "html": html})

    if job.get("status") == "pending":
        return JSONResponse({
            "status": "pending",
            "ok": False,
            "stage": job.get("stage", "reading_vibe"),
        })

    _home = job.get("home_iata") or ""
    _vibe = job.get("vibe") or "adventure"
    if job.get("status") == "error":
        _err = job.get("error_text") or "Something went wrong — try again."
        html = _render_quest_partial_html(
            request, {}, home_iata=_home, vibe=_vibe, error_message=_err
        )
        return JSONResponse({"status": "error", "ok": False, "error": _err, "html": html})

    html = _render_quest_partial_html(
        request,
        job.get("quest_panel") or {},
        home_iata=_home,
        vibe=_vibe,
        place_books=job.get("place_books") or {},
    )
    return JSONResponse({"status": "done", "ok": bool(job.get("ok")), "html": html})


@app.post("/api/detour/plan")
async def detour_plan_api(request: Request):
    """On-demand Detour planning endpoint — called by the 'Plan a Detour' button.

    Flow:
    1. Check recycled saved trips first (fast path, no AI).
    2. Build corridor candidates from vibe-scored destinations on the route.
    3. Fall back to Grok ideation when fewer than 3 corridor candidates exist.
    4. Price via plan_adventure and return rendered HTML.

    Returns JSON: {ok: bool, html: str}
    """
    from datetime import timedelta
    from yonder.types import CabinClass
    from yonder.adventure import StopoverIdea as _StopoverIdea
    from yonder.recycle import find_recycled_result as _find_recycled

    settings = reload_settings()
    form_data = await request.form()

    def _s(key: str, fallback: str = "") -> str:
        v = form_data.get(key)
        return str(v).strip() if v is not None and str(v).strip() else fallback

    prompt = _s("prompt") or _s("ask")
    vibe = _s("vibe", "adventure").lower()
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"

    home_iata = settings.resolve_home_iata()
    origin_raw = _s("origin").upper()
    origin = origin_raw if (len(origin_raw) == 3 and origin_raw.isalpha()) else home_iata

    dest_raw = _s("destination").upper()
    destination = dest_raw if (len(dest_raw) == 3 and dest_raw.isalpha()) else ""

    depart = _s("depart")
    _depart_default = date.today() + timedelta(days=30)
    if not depart:
        depart = _depart_default.isoformat()
    try:
        depart_date: date = date.fromisoformat(depart[:10])
        # Reject implausible dates (today or before — user likely fat-fingered)
        if depart_date < date.today():
            depart_date = _depart_default
    except (ValueError, AttributeError):
        depart_date = _depart_default
    depart = depart_date.isoformat()

    defaults = _adventure_form_defaults(settings)
    min_stop, max_stop, max_cand = settings.detour_stop_defaults()
    try:
        min_stop = max(1, min(21, int(_s("min_stop_days", str(min_stop)))))
    except (ValueError, TypeError):
        pass
    try:
        max_stop = max(1, min(30, int(_s("max_stop_days", str(max_stop)))))
    except (ValueError, TypeError):
        pass

    avoid = settings.effective_avoid_country_list()
    visited = settings.visited_country_list()
    currency = (settings.default_currency or "USD").upper()
    mock = not settings.configured_providers()
    sess = _req_sess(request)
    return_days = _compute_return_days()

    def _render_detour(
        detour_panel_dict: dict,
        error_message: str | None = None,
        place_books: dict | None = None,
        candidate_source: str = "",
        ai_usage_display: str = "",
    ) -> str:
        tpl = templates.env.get_template("_detour_results_partial.html")
        return tpl.render(
            request=request,
            detour_panel=detour_panel_dict,
            error_message=error_message,
            place_books=place_books or {},
            candidate_source=candidate_source,
            ai_usage_display=ai_usage_display,
            return_days=return_days,
        )

    if not prompt:
        return JSONResponse({"ok": False, "error": "No prompt — type a search first.", "html": ""})

    # ── Step 1: Recycled saved-trip fast path ──────────────────────────────────
    _recycled_det = None
    try:
        if not settings.testing:
            _recycled_det = _find_recycled(
                prompt=prompt,
                vibe=vibe,
                origin=origin,
                depart=depart,
                currency=currency,
                limit=max_cand,
                owner_sess=sess,
            )
    except Exception:  # noqa: BLE001
        _recycled_det = None

    # Route pinning: when the user supplied an explicit destination, filter
    # recycled itineraries to those whose final leg ends at that destination.
    # Reject the whole recycled result if none survive the filter.
    if _recycled_det is not None and destination:
        try:
            def _it_final_dest(it) -> str:
                try:
                    return (it.legs[-1].to_iata or "").upper() if it.legs else ""
                except Exception:  # noqa: BLE001
                    return ""
            pinned_its = [
                it for it in _recycled_det.itineraries
                if _it_final_dest(it) == destination
            ]
            if pinned_its:
                _recycled_det = _recycled_det.model_copy(update={"itineraries": pinned_its})
            else:
                _recycled_det = None  # no itinerary matches — run fresh planning
        except Exception:  # noqa: BLE001
            _recycled_det = None

    if _recycled_det is not None:
        vt = vibe_theme(vibe)
        try:
            stamped = [
                it.model_copy(update={
                    "theme_primary": vt["color"],
                    "theme_accent": vt["deep"],
                    "theme_label": vt["label"],
                })
                for it in _recycled_det.itineraries
            ]
            _recycled_det = _recycled_det.model_copy(update={"itineraries": stamped})
        except Exception:  # noqa: BLE001
            pass
        trip_meta = {
            "prompt": prompt,
            "trip_prompt": prompt,
            "vibe": vibe,
            "vibe_color": vt["color"],
            "origin": _recycled_det.request.origin,
            "destination": _recycled_det.request.destination,
        }
        panel = {
            "form": {
                **defaults,
                "prompt": prompt,
                "origin": _recycled_det.request.origin,
                "destination": _recycled_det.request.destination,
                "depart": depart,
                "vibe": vibe,
                "vibe_color": vt["color"],
            },
            "result": _recycled_det,
            "trip_meta": trip_meta,
            "place_books": {},
        }
        html = _render_detour(panel, candidate_source="recycled")
        return JSONResponse({"ok": True, "html": html})

    # ── Step 2: Corridor candidates ────────────────────────────────────────────
    ideas: list = []
    candidate_source = "no-dest"

    if destination:
        try:
            ideas = corridor_candidates(
                origin,
                destination,
                vibe,
                min_stop_days=min_stop,
                max_stop_days=max_stop,
                avoid_countries=avoid,
                exclude_iatas=set(),
                limit=max_cand + 5,
                owner_sess=sess,
            )
            candidate_source = "vibe-corridor" if ideas else "seed-fallback"
        except Exception:  # noqa: BLE001
            ideas = []
            candidate_source = "seed-fallback"

    # ── Step 3: Grok fallback when < 3 corridor candidates ────────────────────
    _route_usage: list = []
    req = AdventureRequest(
        origin=origin,
        destination=destination or origin,
        depart_date=depart_date,
        adults=1,
        currency=currency,
        cabin=CabinClass.ECONOMY,
        min_stop_days=min_stop,
        max_stop_days=max_stop,
        max_candidates=max_cand,
        vibe=vibe,
        prompt=prompt,
        avoid_countries=avoid,
        visited_countries=visited,
        trip_kind="detour" if destination else "getaway",
        include_direct=False,
    )

    if len(ideas) < 3 and settings.grok_ready():
        try:
            async with GrokClient(settings) as grok:
                grok_req, grok_ideas = await asyncio.wait_for(
                    grok.translate_adventure(
                        prompt=prompt,
                        form={
                            "origin": origin,
                            "destination": destination or "",
                            "depart": depart,
                            "arrive_by": "",
                            "min_stop_days": min_stop,
                            "max_stop_days": max_stop,
                            "max_candidates": max_cand,
                            "currency": currency,
                            "vibe": vibe,
                            "avoid_countries": avoid,
                            "visited_countries": visited,
                        },
                        default_currency=currency,
                        seed_learned=True,
                    ),
                    timeout=22.0,
                )
                _route_usage.append(grok.accumulated_usage)
            # Merge: corridor ideas first, then Grok ideas for novelty.
            # Never accept a stopover idea that IS one of the route endpoints.
            _endpoints = {origin, destination} if destination else {origin}
            seen = {(i.iata or "").upper() for i in ideas}
            for gi in grok_ideas:
                code = (gi.iata or "").upper()
                if code and code not in seen and code not in _endpoints:
                    ideas.append(gi.model_copy(update={"source": "grok-fallback"}))
                    seen.add(code)
            if not candidate_source.startswith("vibe"):
                candidate_source = "grok-fallback"
            # Pin the submitted route: the user chose origin/destination in the
            # Detour card, so Grok's returned route fields must never override
            # them.  Only route-free getaways (no destination) may take Grok's
            # suggested destination.
            req = grok_req.model_copy(update={
                "origin": origin,
                "destination": destination or (grok_req.destination or origin),
                "trip_kind": "detour" if destination else grok_req.trip_kind,
                "depart_date": depart_date,
                "currency": currency,
                "min_stop_days": min_stop,
                "max_stop_days": max_stop,
                "max_candidates": max_cand,
                "avoid_countries": avoid,
                "visited_countries": visited,
                "vibe": vibe,
                "prompt": prompt,
                "include_direct": False,
                "adults": 1,
                "cabin": CabinClass.ECONOMY,
            })
        except Exception:  # noqa: BLE001
            pass  # corridor candidates alone will be used

    if not ideas:
        if destination:
            # When the user submitted an explicit origin→destination route, the
            # seed catalog must still respect the corridor geometry — otherwise
            # arbitrary off-route stops get priced.  Retry corridor_candidates
            # with a generous 2× deviation (capped) before falling back to a
            # friendly "no detour found" error rather than serving random seeds.
            try:
                ideas = corridor_candidates(
                    origin, destination, vibe,
                    min_stop_days=min_stop,
                    max_stop_days=max_stop,
                    avoid_countries=avoid,
                    exclude_iatas=set(),
                    limit=max_cand + 5,
                    deviation=2.0,
                    owner_sess=sess,
                )
            except Exception:  # noqa: BLE001
                ideas = []
            candidate_source = "seed-corridor-wide" if ideas else "none"
        else:
            # No destination → unrestricted getaway seeds are appropriate
            ideas = seed_ideas(req, owner_sess=sess)
            if not candidate_source.startswith(("vibe", "grok")):
                candidate_source = "seed-fallback"

    # ── Step 4: Price itineraries via plan_adventure ───────────────────────────
    result = None
    error_text: str | None = None
    try:
        result = await asyncio.wait_for(
            plan_adventure(req, ideas, settings=settings, include_mock=mock, owner_sess=sess),
            timeout=60.0,
        )
        result = _mark_missing_fares_adventure(result)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        error_text = "The AI took too long — try again."
    except Exception as exc:  # noqa: BLE001
        error_text = f"Detour planning failed — {str(exc)[:120]}"

    if error_text or not result or not result.itineraries:
        # Preserve the submitted route in the error card so a retry resubmits
        # the same origin/destination/depart rather than a blank form.
        html = _render_detour(
            {
                "form": {
                    "prompt": prompt,
                    "origin": origin,
                    "destination": destination,
                    "depart": depart,
                    "vibe": vibe,
                },
                "result": None,
            },
            error_message=error_text or "No detour found — try a different destination or prompt.",
        )
        return JSONResponse({"ok": False, "error": error_text or "No detour found", "html": html})

    # ── Step 5: Stamp vibe theme ───────────────────────────────────────────────
    vt = vibe_theme(vibe)
    try:
        stamped = [
            it.model_copy(update={
                "theme_primary": vt["color"],
                "theme_accent": vt["deep"],
                "theme_label": vt["label"],
            })
            for it in result.itineraries
        ]
        result = result.model_copy(update={"itineraries": stamped})
    except Exception:  # noqa: BLE001
        pass

    trip_meta = {
        "prompt": prompt,
        "trip_prompt": prompt,
        "vibe": vibe,
        "vibe_color": vt["color"],
        "origin": result.request.origin,
        "destination": result.request.destination,
    }
    form = {
        **defaults,
        "prompt": prompt,
        "origin": result.request.origin,
        "destination": result.request.destination,
        "depart": depart,
        "vibe": vibe,
        "vibe_color": vt["color"],
        "min_stop_days": min_stop,
        "max_stop_days": max_stop,
    }

    # ── Step 5b: Anchor badges — badge itineraries that connect to saved legs ──
    try:
        from yonder.saved import upcoming_anchor_legs as _det_anchors
        from yonder.adventure import match_anchor as _match_anchor

        anchor_legs = _det_anchors(limit=3, owner_sess=sess)
        if anchor_legs:
            _its_a = list(result.itineraries)
            _changed = False
            for _ia, _ita in enumerate(_its_a):
                if getattr(_ita, "anchor_label", None):
                    continue
                _legs_a = getattr(_ita, "legs", None) or []
                if not _legs_a:
                    continue
                _last_a = _legs_a[-1]
                _a_det = _match_anchor(
                    dest_iata=getattr(_last_a, "to_iata", None),
                    arrive_date=getattr(_last_a, "depart_date", None),
                    anchors=anchor_legs,
                    from_iata=getattr(_last_a, "from_iata", None),
                )
                if _a_det:
                    _its_a[_ia] = _ita.model_copy(update={"anchor_label": _a_det["label"]})
                    _changed = True
            if _changed:
                result = result.model_copy(update={"itineraries": _its_a})
    except Exception:  # noqa: BLE001
        pass  # anchor badging is best-effort

    # ── Step 6: Field notes (cache-first) ─────────────────────────────────────
    place_books: dict = {}
    try:
        from yonder.encyclopedia import briefs_for_stops, stops_from_itineraries
        stops = stops_from_itineraries(result.itineraries, limit=5)
        place_books = await briefs_for_stops(
            settings,
            stops,
            max_n=3,
            cache_only=not settings.grok_ready(),
            user_prompt=prompt,
            trip_vibe=vibe,
        )
    except Exception:  # noqa: BLE001
        place_books = {}

    # ── Step 7: Persist + render ───────────────────────────────────────────────
    panel = {
        "form": form,
        "result": result,
        "trip_meta": trip_meta,
        "place_books": place_books,
    }
    try:
        save_last("detour", session_id=sess, payload={**panel}, pin_first=True)
    except Exception:  # noqa: BLE001
        pass

    ai_usage = ""
    if _route_usage:
        try:
            _usage = merge_usage(*_route_usage)
            ai_usage = fmt_usage(_usage)
            if _usage.get("total_tokens"):
                asyncio.create_task(_log_ai_usage("detour", _usage))
        except Exception:  # noqa: BLE001
            pass

    html = _render_detour(
        panel,
        place_books=place_books,
        candidate_source=candidate_source,
        ai_usage_display=ai_usage,
    )
    return JSONResponse({"ok": bool(result.itineraries), "html": html})


@app.get("/adventure", response_class=HTMLResponse)
async def adventure_home(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/?mode=detour", status_code=302)


@app.post("/adventure", response_class=HTMLResponse)
async def adventure_run(request: Request) -> HTMLResponse:
    settings = reload_settings()
    sess = _req_sess(request)
    form_data = await request.form()
    defaults = _adventure_form_defaults(settings)

    def _s(key: str, fallback: str = "") -> str:
        v = form_data.get(key)
        return str(v).strip() if v is not None and str(v).strip() else fallback

    prompt = _s("prompt")
    depart = _s("depart", defaults["depart"])
    # arrive_by / stop days / candidate count come from Settings defaults
    arrive_by = ""
    currency = (settings.default_currency or "USD").upper()
    if not currency.isalpha() or len(currency) != 3:
        currency = "USD"
    vibe = _s("vibe", "adventure").lower()
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"
    # Mock is internal-only: route skeletons when no fare providers configured.
    mock = not settings.configured_providers()
    use_grok = True  # always invent with Grok when key is present
    avoid = settings.effective_avoid_country_list()
    visited = settings.visited_country_list()
    visited_tiles_l = settings.visited_tile_list()
    min_stop, max_stop, max_cand = settings.detour_stop_defaults()

    form = {
        "prompt": prompt,
        "origin": "",
        "destination": "",
        "depart": depart,
        "arrive_by": arrive_by,
        "min_stop_days": min_stop,
        "max_stop_days": max_stop,
        "max_candidates": max_cand,
        "currency": currency,
        "vibe": vibe,
        "use_grok": use_grok,
    }

    try:
        if not prompt:
            raise ValueError("Describe your trip (cities + vibe).")
        if not depart:
            raise ValueError("Pick a depart date.")

        ideas: list = []
        req: AdventureRequest | None = None
        aim, _skip = settings.search_timing()
        _adv_usage: list[dict] = []

        # ONE Grok call: cities from description + detour/getaway list.
        # On failure, fall back to passport map + local home-city guess (seed ideas).
        from yonder.grok import _guess_home_iata

        # Legacy /adventure has no Escape half, so no already-resolved route to
        # reuse — kept for ordering parity with /explore's fallback (resolved
        # route → text detector → home getaway).
        resolved_route: tuple[str, str] | None = None

        def _local_getaway_fallback(reason: str = "") -> tuple:
            home = (
                _guess_home_iata(prompt)
                or settings.resolve_home_iata()
            )
            # Honor a clearly-named A→B route even without Grok
            _route = resolved_route or detect_route_iatas(prompt)
            _dest = _route[1] if _route else home
            if _route:
                home = _route[0]
            local_req = AdventureRequest(
                origin=home,
                destination=_dest,
                depart_date=date.fromisoformat(depart),
                arrive_by=None,
                adults=1,
                currency=currency,
                cabin=CabinClass.ECONOMY,
                min_stop_days=min_stop,
                max_stop_days=max_stop,
                max_candidates=max_cand,
                vibe=vibe,
                prompt=prompt,
                avoid_countries=avoid,
                visited_countries=visited,
                visited_tiles=visited_tiles_l,
                trip_kind="detour" if _route else "getaway",
                include_direct=False,
            )
            local_ideas = seed_ideas(local_req, owner_sess=sess)
            if not local_ideas:
                msg = (
                    "No unvisited getaway cities left after applying your passport map. "
                    "Clear some visited stamps or widen the vibe."
                )
                if reason:
                    msg = f"{msg} (Grok note: {reason})"
                raise ValueError(msg)
            return local_req, local_ideas

        if use_grok and settings.grok_ready():
            async with GrokClient(settings) as grok:
                try:
                    # Soft invent cap; no hard kill on the full plan
                    invent_timeout = min(22.0, max(8.0, aim * 0.55))
                    home_iata = settings.resolve_home_iata()
                    req, ideas = await asyncio.wait_for(
                        grok.translate_adventure(
                            prompt=prompt,
                            form={
                                "origin": home_iata,
                                "destination": "",
                                "depart": depart,
                                "arrive_by": arrive_by,
                                "min_stop_days": min_stop,
                                "max_stop_days": max_stop,
                                "max_candidates": max_cand,
                                "currency": currency,
                                "vibe": vibe,
                                "avoid_countries": avoid,
                                "visited_countries": visited,
                            },
                            default_currency=currency,
                        ),
                        timeout=invent_timeout,
                    )
                    # Form dates / knobs win; O/D stay from Grok description
                    trip_kind = (req.trip_kind or "detour").lower()
                    if req.origin == req.destination:
                        # Prompt clearly names two different cities → correct
                        # the parse instead of silently forcing a getaway
                        route = resolved_route or detect_route_iatas(prompt)
                        if route:
                            req = req.model_copy(
                                update={"origin": route[0], "destination": route[1]}
                            )
                            trip_kind = "detour"
                        else:
                            trip_kind = "getaway"
                    req = req.model_copy(
                        update={
                            "depart_date": date.fromisoformat(depart),
                            "arrive_by": (
                                date.fromisoformat(arrive_by) if arrive_by else req.arrive_by
                            ),
                            "currency": currency,
                            "min_stop_days": min_stop,
                            "max_stop_days": max_stop,
                            "max_candidates": max_cand,
                            "avoid_countries": avoid,
                            "visited_countries": visited,
                            "visited_tiles": visited_tiles_l,
                            "vibe": vibe or req.vibe,
                            "prompt": prompt,
                            "trip_kind": trip_kind,
                            "include_direct": trip_kind != "getaway",
                        }
                    )
                    # Grok returned empty candidates — fill from passport-aware seeds
                    if not ideas:
                        ideas = seed_ideas(req, owner_sess=sess)
                    _adv_usage.append(grok.accumulated_usage)
                except Exception as exc:  # noqa: BLE001
                    # Open getaways don't need a second city — map + home guess is enough
                    req, ideas = _local_getaway_fallback(str(exc)[:180])
        else:
            # No Grok: still allow getaways from home city + seed list + map
            try:
                req, ideas = _local_getaway_fallback("Grok offline")
            except ValueError:
                raise ValueError(
                    "Set XAI_API_KEY in Settings for full Detour invent, "
                    "or describe a getaway from a home city (e.g. Vancouver) with passport stamps set."
                )

        assert req is not None
        if len(req.origin) != 3 or len(req.destination) != 3:
            # Last chance: home-city guess from prompt
            try:
                req, ideas = _local_getaway_fallback("missing airports")
            except ValueError as exc:
                raise ValueError(
                    "Couldn’t resolve a home airport — try “get out of Vancouver for a few days” "
                    "or a full A→B route."
                ) from exc
        # Same O/D is valid getaway: home → somewhere new → home
        if req.origin == req.destination:
            req = req.model_copy(
                update={"trip_kind": "getaway", "include_direct": False}
            )

        if not ideas:
            ideas = seed_ideas(req, owner_sess=sess)
        if not ideas:
            raise ValueError(
                "No candidate cities after applying your visited/avoid map. "
                "Unstamp some visited countries or try a different vibe."
            )

        # Force include_direct off for speed (baseline is optional noise under soft aim)
        req = req.model_copy(update={"include_direct": False})
        result = await plan_adventure(
            req, ideas, settings=settings, include_mock=mock, owner_sess=sess
        )

        form.update(
            {
                "origin": result.request.origin,
                "destination": result.request.destination,
                "depart": result.request.depart_date.isoformat(),
                "arrive_by": (
                    result.request.arrive_by.isoformat()
                    if result.request.arrive_by
                    else ""
                ),
                "currency": result.request.currency,
                "vibe": result.request.vibe,
                "avoid_countries": result.request.avoid_countries,
            }
        )

        vt = vibe_theme(result.request.vibe or vibe)
        form["vibe"] = vt["id"]
        form["vibe_color"] = vt["color"]
        trip_meta = {
            "adults": result.request.adults,
            "currency": result.request.currency,
            "cabin": (
                result.request.cabin.value
                if hasattr(result.request.cabin, "value")
                else str(result.request.cabin or "economy")
            ),
            "vibe": vt["id"],
            "vibe_color": vt["color"],
            "vibe_label": vt["label"],
            "prompt": result.request.prompt,
            "origin": result.request.origin,
            "destination": result.request.destination,
        }
        # Tag AI-produced results with the backend that made them; local
        # fallback (no AI call) leaves the label off.
        if _adv_usage:
            trip_meta["model_source"] = settings.model_source_label()
        # Stamp vibe theme onto each itinerary so Save keeps the color
        try:
            stamped = []
            for it in result.itineraries:
                stamped.append(
                    it.model_copy(
                        update={
                            "theme_primary": vt["color"],
                            "theme_accent": vt["deep"],
                            "theme_label": vt["label"],
                        }
                    )
                )
            result = result.model_copy(update={"itineraries": stamped})
        except Exception:
            pass
        # No live place briefs under the 30s budget
        place_books: dict = {}

        save_last(
            "detour",
            session_id=sess,
            payload={
                "form": form,
                "result": result,
                "trip_meta": trip_meta,
                "place_books": place_books,
            },
        )
        _adv_ctx = _compose_page_ctx(
            settings,
            session_id=sess,
            mode="detour",
            detour_override={
                "form": form,
                "result": result,
                "trip_meta": trip_meta,
                "place_books": place_books,
            },
        )
        if _adv_usage:
            _adv_u = merge_usage(*_adv_usage)
            _adv_ctx["ai_usage_display"] = fmt_usage(_adv_u)
            if _adv_u.get("total_tokens"):
                asyncio.create_task(_log_ai_usage("adventure", _adv_u))
        return templates.TemplateResponse(request, "index.html", _adv_ctx)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                session_id=sess,
                mode="detour",
                error=str(exc),
                detour_override={"form": form},
            ),
            status_code=400,
        )


# ── Saved itineraries ────────────────────────────────────────────────────────


def _saved_cards(items: list) -> list[dict]:
    """Hydrate saved rows into AdventureItinerary (or QuestIdea for quest kind)."""
    from yonder.adventure import AdventureItinerary, QuestIdea, _apply_theme

    cards: list[dict] = []
    for s in items:
        it = None
        quest_idea = None
        if s.kind == "quest":
            try:
                quest_idea = QuestIdea.model_validate(s.itinerary or {})
            except Exception:
                quest_idea = None
        else:
            try:
                it = AdventureItinerary.model_validate(s.itinerary or {})
                if not it.theme_style or not it.theme_primary:
                    it = _apply_theme(it)
            except Exception:
                it = None
        cards.append({"saved": s, "it": it, "quest_idea": quest_idea})
    return cards


async def _render_shared_trip(request: Request, share_id: str) -> HTMLResponse:
    """Standalone shareable itinerary page (QR target)."""
    settings = reload_settings()
    share = get_share(share_id)
    if not share:
        return templates.TemplateResponse(
            request,
            "404.html",
            _error_ctx(),
            status_code=404,
        )
    base = PRODUCTION_URL
    url = f"{base}{share.path}"
    kind_label = {
        "escape": "Escape",
        "detour": "Detour",
        "quest": "Quest",
    }.get(share.kind, share.kind.title())

    # Gather cached field notes (never calls Grok — share page must be instant).
    from yonder.encyclopedia import get_any_cached_for_iata
    from yonder.lang import detect_lang as _detect_share_lang
    from yonder.activities import activity_links_for

    p = share.payload or {}
    # Field notes must match the language of the prompt that made the trip;
    # shares without a stored prompt default to English.
    _share_lang = _detect_share_lang(
        str((p.get("trip_meta") or {}).get("prompt") or "")
    )

    # Resolve vibe early so activity pills can be vibe-matched.
    share_vibe: dict | None = None
    raw_vibe: str | None = None
    if share.kind == "escape":
        raw_vibe = p.get("vibe") or (p.get("query") or {}).get("vibe")
    elif share.kind == "detour":
        it = p.get("itinerary") or {}
        raw_vibe = (p.get("trip_meta") or {}).get("vibe") or next(
            iter(it.get("vibe_tags") or []), None
        )
    elif share.kind == "quest":
        raw_vibe = (p.get("trip_meta") or {}).get("vibe")
    if raw_vibe:
        rv = resolve_vibe(str(raw_vibe))
        key = str(raw_vibe).strip().lower()
        # Only trust the resolution when it wasn't the "adventure" fallback
        if rv["id"] == key or rv["label"].lower() == key:
            share_vibe = rv

    _vibe_id = share_vibe["id"] if share_vibe else None

    def _share_poi_picks(iata_code: str) -> list:
        """Curator's picks for a share-page field note — resolved via IATA → city."""
        try:
            from yonder.airports import city_country_for_iata
            from yonder.poi import picks_for_city

            info = city_country_for_iata(iata_code)
            return picks_for_city(info[0] if info else None)
        except Exception:
            return []

    place_books: dict[str, dict] = {}
    if share.kind == "escape":
        dest = (p.get("query") or {}).get("destination") or ""
        if dest:
            brief = get_any_cached_for_iata(dest, lang=_share_lang)
            if brief:
                brief = dict(brief)
                brief["activity_links"] = await activity_links_for(
                    settings, iata=dest.upper(), vibe=_vibe_id
                )
                brief["poi_picks"] = _share_poi_picks(dest.upper())
                place_books[dest.upper()] = brief
    elif share.kind == "detour":
        it = p.get("itinerary") or {}
        for iata in filter(None, [it.get("stop_iata"), *[
            leg.get("to_iata") for leg in (it.get("legs") or [])
        ]]):
            code = iata.upper()
            if code not in place_books:
                brief = get_any_cached_for_iata(code, lang=_share_lang)
                if brief:
                    brief = dict(brief)
                    brief["activity_links"] = await activity_links_for(
                        settings, iata=code, vibe=_vibe_id
                    )
                    brief["poi_picks"] = _share_poi_picks(code)
                    place_books[code] = brief
    elif share.kind == "quest":
        idea = p.get("idea") or {}
        for iata in filter(None, [idea.get("entry_iata"), idea.get("exit_iata")]):
            code = iata.upper()
            if code not in place_books:
                brief = get_any_cached_for_iata(code, lang=_share_lang)
                if brief:
                    brief = dict(brief)
                    brief["activity_links"] = await activity_links_for(
                        settings, iata=code, vibe=_vibe_id
                    )
                    brief["poi_picks"] = _share_poi_picks(code)
                    place_books[code] = brief

    return templates.TemplateResponse(
        request,
        "trip.html",
        {
            "nav": "home",
            **_base_ctx(settings, vibe=_vibe_id),
            "share": share,
            "error": None,
            "share_url": url,
            "qr_src": qr_png_data_uri(url, scale=7, border=3),
            "qr_svg": qr_svg_for_url(url, scale=5),
            "kind_label": kind_label,
            "place_books": place_books,
            "share_vibe": share_vibe,
            "og_image": f"{base}/static/share_bg.jpg",
        },
    )


@app.get("/t/{kind}/{slug}/{share_id}", response_class=HTMLResponse)
async def shared_trip_pretty(
    request: Request, kind: str, slug: str, share_id: str
) -> HTMLResponse:
    """Human-readable share URL: /t/escape/YVR-NRT-2026-08-20/abc123…"""
    return await _render_shared_trip(request, share_id)


@app.get("/t/{share_id}", response_class=HTMLResponse)
async def shared_trip_page(request: Request, share_id: str) -> HTMLResponse:
    """Legacy short form /t/{id} — still works for old QRs."""
    # Avoid capturing multi-segment paths if routed here by mistake
    if "/" in share_id:
        return RedirectResponse(url="/", status_code=302)
    return await _render_shared_trip(request, share_id)


_QUESTS_PER_PAGE = 10


@app.get("/quests", response_class=HTMLResponse)
async def quests_browse_page(
    request: Request,
    page: int = 1,
    origin: str | None = None,
) -> HTMLResponse:
    """Public browse page for all saved Quest itineraries."""
    page = max(1, page)

    # Distinguish "param absent" (None) from "param present but empty" ("").
    # - absent  → pre-populate from user's home airport
    # - ""      → explicit "show all", no filter
    # - "JFK"   → filter by that origin
    origin_param_given = origin is not None
    origin_n = (origin or "").strip().upper()[:4] or None

    if not origin_param_given:
        # No ?origin in URL — fall back to user's saved home airport.
        try:
            settings = reload_settings()
            home = settings.resolve_home_iata()
            if home:
                origin_n = home.upper()
        except Exception:
            pass

    offset = (page - 1) * _QUESTS_PER_PAGE

    total = count_quests(origin=origin_n)
    total_pages = max(1, (total + _QUESTS_PER_PAGE - 1) // _QUESTS_PER_PAGE)
    # Clamp page to valid range
    if page > total_pages and total > 0:
        page = total_pages
        offset = (page - 1) * _QUESTS_PER_PAGE

    quests = list_quests(origin=origin_n, limit=_QUESTS_PER_PAGE, offset=offset)

    # Build share packs for each quest (same stable-hash approach as /saved).
    quest_cards: list[dict] = []
    for s in quests:
        share: dict | None = None
        try:
            share = _share_quest(
                request,
                s.itinerary or {},
                s.origin or "",
                {**(s.trip_meta or {}), "saved_id": s.id},
            )
        except Exception:
            share = None
        quest_cards.append({"saved": s, "share": share})

    saved_count = 0
    try:
        saved_count = count_saved(owner_sess=_req_sess(request))
    except Exception:
        pass

    board_quests: list[dict] = []
    try:
        board_quests = top_quest_routes(limit=12, origin=origin_n)
    except Exception:
        pass

    return templates.TemplateResponse(
        request,
        "quests.html",
        {
            "nav": "quests",
            "saved_count": saved_count,
            "vibe_theme": None,
            "quest_cards": quest_cards,
            "origin_filter": origin_n or "",
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "board_quests": board_quests,
        },
    )


@app.get("/saved", response_class=HTMLResponse)
async def saved_list_page(
    request: Request,
    flash: str | None = None,
    err: str | None = None,
) -> HTMLResponse:
    settings = reload_settings()

    # Resolve (or mint) the browser session id before loading saved trips so
    # list_saved can filter to this browser's rows.
    import uuid as _uuid

    sess = (request.cookies.get("yv_sess") or "").strip()[:64]
    need_cookie = not sess
    if need_cookie:
        sess = _uuid.uuid4().hex[:32]

    items = list_saved(limit=100, owner_sess=sess)
    cards = _saved_cards(items)
    for card in cards:
        s = card.get("saved")
        it = card.get("it")
        qi = card.get("quest_idea")
        if not s:
            continue
        try:
            if s.kind == "quest" and qi is not None:
                card["share"] = _share_quest(
                    request,
                    qi,
                    s.origin or "",
                    {**(s.trip_meta or {}), "saved_id": s.id},
                )
            else:
                payload = {
                    "itinerary": dump_obj(it) if it is not None else (s.itinerary or {}),
                    "trip_meta": s.trip_meta or {},
                    "saved_id": s.id,
                }
                card["share"] = _share_pack(
                    request,
                    kind="detour",
                    title=s.title or "Saved trip",
                    payload=payload,
                )
        except Exception:
            card["share"] = None

    # Fetch cached field-note briefs for Quest cards (cache-only, no live Grok).
    for card in cards:
        qi = card.get("quest_idea")
        s = card.get("saved")
        if qi is None:
            continue
        try:
            from yonder.encyclopedia import briefs_for_stops

            _sq_stops: list[tuple[str | None, str | None, str | None]] = []
            _sq_seen: set[str] = set()
            for _iata, _city in [
                (getattr(qi, "entry_iata", None), getattr(qi, "entry_city", None)),
                (getattr(qi, "exit_iata", None), getattr(qi, "exit_city", None)),
            ]:
                _code = (_iata or "").upper()
                if _code and _code not in _sq_seen:
                    _sq_seen.add(_code)
                    _sq_stops.append((_code, None, _city))
            card["place_books"] = await briefs_for_stops(
                settings,
                _sq_stops,
                max_n=0,
                cache_only=True,
                trip_vibe=(s.vibe if s else None),
            )
        except Exception:
            card["place_books"] = {}

    # Vibe-learning: visiting /saved is a tier-2 "reviewed" re-engagement signal
    # for the saved destinations — gated to once per session per destination.
    try:
        from yonder.vibe_signals import REVIEWED, upsert_signal

        loop = asyncio.get_running_loop()
        for s in items:
            dest = str(s.stop_iata or s.destination or "").upper()
            if len(dest) != 3 or not dest.isalpha():
                continue
            key = (sess, dest)
            if key in _REVIEWED_SEEN:
                continue
            _REVIEWED_SEEN.add(key)
            loop.run_in_executor(
                None,
                lambda d=dest, v=(s.vibe or None), o=(s.origin or None): upsert_signal(
                    dest_iata=d,
                    vibe=v,
                    origin=o,
                    signal_strength=REVIEWED,
                    search_type="review",
                    session_hash=sess,
                ),
            )
        if len(_REVIEWED_SEEN) > 5000:
            _REVIEWED_SEEN.clear()
    except Exception:
        pass

    response = templates.TemplateResponse(
        request,
        "saved.html",
        {
            "nav": "saved",
            **_base_ctx(settings),
            "items": items,
            "cards": cards,
            "flash": flash,
            "error": err,
            "save_limit": SAVE_LIMIT,
        },
    )
    if need_cookie:
        response.set_cookie("yv_sess", sess, httponly=True, samesite="lax")
    return response


# (session, dest) pairs already given a tier-2 review signal this process
_REVIEWED_SEEN: set[tuple[str, str]] = set()


@app.post("/api/saved")
async def api_save_itinerary(request: Request):
    """Save Escape or Detour snapshot (JSON body). Only durable write = explicit ★ Save."""
    from fastapi.responses import JSONResponse

    from yonder.saved import escape_offer_to_itinerary

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    trip_meta = body.get("trip_meta") if isinstance(body.get("trip_meta"), dict) else {}
    for k in (
        "adults",
        "currency",
        "cabin",
        "vibe",
        "prompt",
        "trip_prompt",
        "origin",
        "destination",
        "visited",
        "avoid",
        "click_id",
        "chip_id",
        "chip_source",
        "search_id",
        "model_source",
    ):
        if k in body and k not in trip_meta:
            trip_meta[k] = body[k]
    # Normalize prompt field
    if trip_meta.get("trip_prompt") and not trip_meta.get("prompt"):
        trip_meta["prompt"] = trip_meta["trip_prompt"]

    itinerary = body.get("itinerary")
    # Escape cards: { kind: "escape", query, offer, ask }
    if not itinerary and body.get("kind") == "escape" and body.get("offer") and body.get("query"):
        itinerary = escape_offer_to_itinerary(
            query=body["query"] if isinstance(body["query"], dict) else {},
            offer=body["offer"] if isinstance(body["offer"], dict) else {},
            ask=str(body.get("ask") or trip_meta.get("prompt") or ""),
            vibe=trip_meta.get("vibe"),
        )
    if not itinerary:
        itinerary = body if isinstance(body, dict) else None
    if not isinstance(itinerary, dict) or not itinerary.get("title"):
        return JSONResponse(
            {"ok": False, "error": "Missing itinerary payload"}, status_code=400
        )
    # Stamp passport context into trip_meta when provided
    settings = reload_settings()
    trip_meta.setdefault("visited", settings.visited_country_list())
    trip_meta.setdefault("avoid", settings.effective_avoid_country_list())
    # Scope the save to this browser so /saved stays private per session.
    # Mint a session id if the browser has none yet, mirroring the /saved
    # page cookie logic so the first save is immediately visible on reload.
    import uuid as _uuid_save

    owner = _req_sess(request)
    need_sess_cookie = not owner
    if need_sess_cookie:
        owner = _uuid_save.uuid4().hex[:32]
    try:
        saved = save_itinerary(itinerary, trip_meta=trip_meta, owner_sess=owner or None)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    # Funnel: Save is the strong preference label + affiliate path quality signal
    try:
        from yonder.attribution import log_event

        log_event(
            "save",
            click_id=str(trip_meta.get("click_id") or "") or None,
            chip_id=str(trip_meta.get("chip_id") or "") or None,
            chip_source=str(trip_meta.get("chip_source") or "") or None,
            vibe=str(trip_meta.get("vibe") or "") or None,
            origin=str(trip_meta.get("origin") or saved.origin or "") or None,
            search_id=str(trip_meta.get("search_id") or "") or None,
            saved_id=saved.id,
            dest=str(saved.stop_iata or saved.destination or "") or None,
        )
    except Exception:
        pass
    # Vibe-learning: ★ Save is the tier-4 signal (upgrade the search's row).
    try:
        from yonder.vibe_signals import SAVED, upsert_signal

        _dest4 = str(saved.stop_iata or saved.destination or "").upper() or None
        _sig4 = str(trip_meta.get("signal_id") or "").strip() or None
        sig_map4 = trip_meta.get("signal_ids")
        if isinstance(sig_map4, dict) and _dest4 and sig_map4.get(_dest4):
            _sig4 = str(sig_map4[_dest4])
        asyncio.get_running_loop().run_in_executor(
            None,
            lambda: upsert_signal(
                signal_id=_sig4,
                dest_iata=_dest4,
                vibe=str(trip_meta.get("vibe") or "") or None,
                origin=str(trip_meta.get("origin") or saved.origin or "") or None,
                signal_strength=SAVED,
                search_type="save",
            ),
        )
    except Exception:
        pass
    # Ad pipeline: upsert a candidate for this destination+vibe pair.
    # Landing URL is derived from REPLIT_DOMAINS (trusted env), never from
    # request.base_url which could be forged via a Host header.
    try:
        from yonder.ad_pipeline import upsert_candidate_from_save as _upsert_ad

        asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _upsert_ad(saved),
        )
    except Exception:
        pass
    resp = JSONResponse(
        {
            "ok": True,
            "id": saved.id,
            "title": saved.title,
            "display_price": saved.display_price,
            "saved_count": count_saved(owner_sess=owner or None),
            "save_limit": SAVE_LIMIT,
        }
    )
    if need_sess_cookie:
        resp.set_cookie("yv_sess", owner, httponly=True, samesite="lax")
    return resp


@app.post("/saved/{saved_id}/refresh", response_class=HTMLResponse)
async def saved_refresh(request: Request, saved_id: str) -> HTMLResponse:
    settings = reload_settings()
    owner = _req_sess(request)
    item = get_saved(saved_id)
    if not item:
        return RedirectResponse(
            url="/saved?err=" + quote("Itinerary not found"), status_code=302
        )
    # Ownership check: only the browser that saved the trip can refresh it.
    # For legacy rows (owner_sess is NULL) we skip the check so pre-migration
    # trips can still be refreshed.
    item_owner = (getattr(item, "owner_sess", None) or "").strip()[:64] or None
    if item_owner and owner != item_owner:
        return RedirectResponse(
            url="/saved?err=" + quote("Not authorised"), status_code=302
        )

    form_data = await request.form()
    # Mock is internal-only: route skeletons when no fare providers configured.
    mock = not settings.configured_providers()

    try:
        it = AdventureItinerary.model_validate(item.itinerary)
        cabin_raw = (item.cabin or "economy").lower()
        try:
            cabin = CabinClass(cabin_raw)
        except ValueError:
            cabin = CabinClass.ECONOMY
        refreshed, rmeta = await reprice_itinerary(
            it,
            adults=item.adults,
            currency=item.currency,
            cabin=cabin,
            settings=settings,
            include_mock=mock,
        )
        trip_meta = {
            **(item.trip_meta or {}),
            "last_refresh_status": rmeta.get("status"),
            "last_refresh_message": rmeta.get("message"),
            "last_refresh_delta": rmeta.get("delta"),
            "last_refresh_provider": rmeta.get("provider"),
            "prev_total_before_refresh": rmeta.get("prev_total"),
        }
        # Pass owner_sess so the UPSERT preserves ownership and the refreshed
        # row remains visible on the browser's private /saved list.
        update_from_itinerary(
            saved_id,
            refreshed.model_dump(mode="json"),
            trip_meta=trip_meta,
            owner_sess=item_owner or owner or None,
        )
        status = rmeta.get("status") or "failed"
        if status == "failed":
            msg = rmeta.get("message") or "Could not refresh fares"
            return RedirectResponse(
                url="/saved?err=" + quote(msg),
                status_code=302,
            )
        # Keep flash short; notes on the card hold detail if needed
        if status == "live":
            flash = "Fares refreshed"
        elif status == "mixed":
            flash = "Fares partially refreshed — kept last known where live failed"
        else:
            flash = "Live check failed — kept last known fares"
        return RedirectResponse(
            url="/saved?flash=" + quote(flash),
            status_code=302,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url="/saved?err=" + quote(f"Refresh failed: {exc}"),
            status_code=302,
        )


@app.post("/saved/{saved_id}/delete", response_class=HTMLResponse)
async def saved_delete(request: Request, saved_id: str) -> HTMLResponse:
    owner = _req_sess(request)
    ok = delete_saved(saved_id, owner_sess=owner or None)
    if ok:
        return RedirectResponse(
            url="/saved?flash=" + quote("Removed from list"), status_code=302
        )
    return RedirectResponse(
        url="/saved?err=" + quote("Not found"), status_code=302
    )

@app.post("/api/clear-saves", response_class=HTMLResponse)
async def api_clear_saves(request: Request) -> HTMLResponse:
    """Delete all saved itineraries for the current browser — Remove All action."""
    owner = _req_sess(request)
    try:
        count = clear_all_saves(owner_sess=owner or None)
        msg = "Fresh start — all trips cleared" if count > 0 else "Nothing to clear"
        return RedirectResponse(
            url="/saved?flash=" + quote(msg), status_code=302
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url="/saved?err=" + quote(f"Could not clear saves: {exc}"),
            status_code=302,
        )
@app.post("/api/results-clear")
async def api_results_clear(request: Request) -> JSONResponse:
    """Clear last Escape + Detour result snapshots (UI Clear filter)."""
    from yonder.last_search import clear_last

    sess = _req_sess(request)
    try:
        clear_last(None, session_id=sess)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True})


@app.post("/api/search-cancel")
async def api_search_cancel(request: Request) -> JSONResponse:
    """Client Skip — mark a running /explore search so it wraps up with partials."""
    from yonder.search_cancel import request_cancel

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sid = ""
    if isinstance(body, dict):
        sid = str(body.get("search_id") or body.get("id") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing search_id"}, status_code=400)
    ok = request_cancel(sid)
    return JSONResponse({"ok": ok, "search_id": sid})


@app.post("/api/price-refresh")
async def api_price_refresh(request: Request) -> JSONResponse:
    """Live fare check for a result card before Share/Save completes.

    Body: { itinerary: {...}, adults?, currency? }. Uses the normal
    flight-search engine per leg and returns the repriced itinerary.
    """
    from yonder.recycle import strip_revealing_notes

    settings = reload_settings()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    try:
        it = AdventureItinerary.model_validate(body.get("itinerary") or {})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": f"Bad itinerary: {str(exc)[:120]}"}, status_code=400
        )
    try:
        adults = max(1, min(9, int(body.get("adults") or 1)))
    except (TypeError, ValueError):
        adults = 1
    currency = str(body.get("currency") or it.currency or settings.default_currency or "USD").upper()
    if len(currency) != 3 or not currency.isalpha():
        currency = (settings.default_currency or "USD").upper()
    include_mock = not settings.configured_providers()
    try:
        refreshed, rmeta = await reprice_itinerary(
            it,
            adults=adults,
            currency=currency,
            cabin=CabinClass.ECONOMY,
            settings=settings,
            include_mock=include_mock,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:160]}, status_code=502)
    if refreshed.total_price is None:
        return JSONResponse(
            {"ok": False, "error": rmeta.get("message") or "No live fare found"},
            status_code=502,
        )
    refreshed = strip_revealing_notes(refreshed)
    return JSONResponse(
        {
            "ok": True,
            "itinerary": refreshed.model_dump(mode="json"),
            "total_price": refreshed.total_price,
            "display_price": refreshed.display_price_base or refreshed.display_price,
            "currency": refreshed.currency,
        }
    )


@app.post("/api/leg-fare")
async def api_leg_fare(request: Request) -> JSONResponse:
    """On-demand fare check for a single leg (Check Fares button).

    Returns the cheapest real (non-mock) offer for origin→destination on the
    given date, or a clear error when no live fare can be found.
    """
    settings = reload_settings()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    origin = str(body.get("origin") or "").strip().upper()[:3]
    destination = str(body.get("destination") or "").strip().upper()[:3]
    depart = str(body.get("depart") or "").strip()[:10]
    return_raw = str(body.get("return_date") or "").strip()[:10]
    currency = str(body.get("currency") or settings.default_currency or "USD").strip().upper()[:3]
    if not currency.isalpha() or len(currency) != 3:
        currency = "USD"
    try:
        adults = max(1, min(9, int(body.get("adults") or 1)))
    except (TypeError, ValueError):
        adults = 1
    cabin_raw = str(body.get("cabin") or "economy").strip().lower()
    try:
        cabin = CabinClass(cabin_raw)
    except ValueError:
        cabin = CabinClass.ECONOMY

    if len(origin) != 3 or not origin.isalpha() or len(destination) != 3 or not destination.isalpha():
        return JSONResponse({"ok": False, "error": "invalid route"}, status_code=400)
    try:
        depart_date = date.fromisoformat(depart)
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid depart date"}, status_code=400)
    return_date = None
    if return_raw:
        try:
            return_date = date.fromisoformat(return_raw)
        except ValueError:
            return_date = None

    if not settings.configured_providers():
        return JSONResponse(
            {
                "ok": False,
                "error": "No fare providers configured — add a provider key in Settings.",
            },
            status_code=503,
        )

    query = SearchQuery(
        origin=origin,
        destination=destination,
        depart_date=depart_date,
        return_date=return_date,
        adults=adults,
        cabin=cabin,
        currency=currency,
        max_results=5,
        nonstop_only=False,
    )
    try:
        result = await search_flights(
            query,
            settings=settings,
            include_mock=False,
            timeout=20.0,
            max_providers=2,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:160]}, status_code=502)

    offers = [
        o
        for o in (result.offers or [])
        if (o.price_kind or "") != "mock" and o.price is not None
    ]
    if not offers:
        return JSONResponse(
            {"ok": False, "error": "No fares found for this leg right now — try again later."},
            status_code=502,
        )
    best = min(offers, key=lambda o: o.price)

    # Silently upsert into fare_estimates cache
    try:
        from yonder.fare_estimates import upsert_estimate
        upsert_estimate(origin, destination, best.price, best.currency or currency)
    except Exception:  # noqa: BLE001
        pass

    return JSONResponse(
        {
            "ok": True,
            "price": best.price,
            "currency": best.currency,
            "display_price": best.display_price_base
            or best.display_price
            or f"~{best.currency} {best.price:.0f}",
            "airlines": best.airlines,
            "stops_out": best.stops_out,
            "provider": best.provider,
            "offer": best.model_dump(mode="json"),
        }
    )


@app.get("/api/fare-estimate")
async def api_fare_estimate(
    origin: str = Query(default=""),
    destination: str = Query(default=""),
    currency: str = Query(default="USD"),
) -> JSONResponse:
    """Return a cached fare range for a route this month, or no_data.

    Falls back to the previous month with stale=true when current month is empty.
    """
    from yonder.fare_estimates import get_estimate

    o = origin.strip().upper()[:3]
    d = destination.strip().upper()[:3]
    c = (currency or "USD").strip().upper()[:3]
    if not c.isalpha() or len(c) != 3:
        c = "USD"

    if len(o) != 3 or not o.isalpha() or len(d) != 3 or not d.isalpha():
        return JSONResponse({"ok": False, "reason": "invalid_route"}, status_code=400)

    from datetime import datetime, timezone
    year_month = datetime.now(timezone.utc).strftime("%Y-%m")

    est = get_estimate(o, d, c, year_month=year_month)
    if not est:
        return JSONResponse({"ok": False, "reason": "no_data"})

    return JSONResponse(
        {
            "ok": True,
            "origin": o,
            "destination": d,
            "currency": c,
            "year_month": est["year_month"],
            "price_low": est["price_low"],
            "price_high": est["price_high"],
            "sample_count": est["sample_count"],
            "stale": est["stale"],
            "label": est["label"],
        }
    )


@app.post("/api/signal-event")
async def api_signal_event(request: Request) -> JSONResponse:
    """Vibe-learning engagement events (tier 2–3) — fire-and-forget like /api/funnel.

    Idempotent: only ever upgrades a signal's strength, never downgrades.
    """
    from yonder.vibe_signals import upsert_signal

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    event_type = str(body.get("event_type") or "engaged").strip()[:16] or "engaged"
    try:
        strength = int(body.get("strength") or 3)
    except (TypeError, ValueError):
        strength = 3
    # Tier 4 (saved) is only written server-side from ★ Save
    strength = max(1, min(3, strength))
    signal_id = str(body.get("signal_id") or "").strip()[:64] or None
    dest = str(body.get("dest_iata") or body.get("dest") or "").strip().upper()[:3] or None
    vibe = str(body.get("vibe") or "").strip().lower()[:40] or None
    if not signal_id and not dest:
        return JSONResponse(
            {"ok": False, "error": "need signal_id or dest_iata"}, status_code=400
        )
    try:
        sid = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: upsert_signal(
                signal_id=signal_id,
                dest_iata=dest,
                vibe=vibe,
                origin=str(body.get("origin") or "").strip().upper()[:3] or None,
                signal_strength=strength,
                search_type=event_type,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:120]}, status_code=500)
    return JSONResponse({"ok": True, "signal_id": sid})


@app.post("/api/result-feedback")
async def api_result_feedback(request: Request) -> JSONResponse:
    """Thumbs up / down on a result card.

    Up  → positive signal in vibe_signals + history log in feedback.db
    Down → negative signal + history log + enqueue AI answer for the vibe question
    """
    import asyncio

    from yonder.feedback import record_feedback, upsert_vibe_question, save_vibe_answer
    from yonder.vibe_signals import upsert_signal, record_rejection, ENGAGED

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    direction = str(body.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        return JSONResponse({"ok": False, "error": "direction must be up or down"}, status_code=400)

    vibe = str(body.get("vibe") or "").strip().lower()[:40] or None
    dest = str(body.get("dest_iata") or "").strip().upper()[:3] or None
    query = str(body.get("query") or "")[:400]

    # Server-trusted session identity for vote dedup: prefer the yv_sess
    # cookie (same session id the /saved page uses); fall back to a hash of
    # client IP + user agent so anonymous traffic still gets a stable-ish
    # per-client key. The client-supplied session_hash is only a last resort.
    sess = (request.cookies.get("yv_sess") or "").strip()[:32]
    need_cookie = not sess
    if not sess:
        ip = (request.client.host if request.client else "") or ""
        ua = request.headers.get("user-agent", "")
        if ip or ua:
            sess = hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:32]
    if not sess:
        sess = str(body.get("session_hash") or "")[:32]
    sess = sess or None

    # Write to history archive; "" means this session already cast this vote —
    # silently ignore repeats so vote-stuffing can't skew the signal store.
    row_id = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: record_feedback(
            direction=direction,
            vibe=vibe,
            dest_iata=dest,
            query=query,
            session_hash=sess,
        ),
    )
    def _resp(payload: dict) -> JSONResponse:
        resp = JSONResponse(payload)
        if need_cookie and sess:
            # Pin the derived session id so this client's future votes dedup
            # against the same key (matches the /saved yv_sess cookie).
            resp.set_cookie("yv_sess", sess, httponly=True, samesite="lax")
        return resp

    if row_id == "":
        return _resp({"ok": True, "direction": direction, "deduped": True})

    # Learning layer: shift attribute weights (dest_attributes /
    # vibe_attributes, source='user_behavior') alongside the flat vibe score.
    if dest and row_id:
        from yonder.knowledge import reinforce_from_feedback

        reinforced = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: reinforce_from_feedback(
                vibe=vibe,
                dest_iata=dest,
                direction=direction,
                feedback_id=row_id,
            ),
        )
        if not reinforced:
            # Vote is safely archived in result_feedback (row_id) and can be
            # replayed later via knowledge.backfill_feedback_reinforcement().
            import logging as _fb_log

            _fb_log.getLogger("yonder.knowledge").warning(
                "result-feedback vote archived but NOT reinforced "
                "(vibe=%s dest=%s direction=%s feedback_id=%s)",
                vibe, dest, direction, row_id,
            )

    if direction == "up" and dest:
        # Reinforce the vibe+destination match in the signal store
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: upsert_signal(
                dest_iata=dest,
                vibe=vibe,
                signal_strength=ENGAGED,
                search_type="thumb_up",
                session_hash=sess,
            ),
        )
        return _resp({"ok": True, "direction": "up"})

    if direction == "down":
        # Record rejection signal in the vibe affinity store (strength=0 dilutes score)
        if dest:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: record_rejection(
                    dest_iata=dest,
                    vibe=vibe,
                    session_hash=sess,
                ),
            )

        # Upsert the vibe question; generate AI answer only for new entries
        qid, is_new = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: upsert_vibe_question(vibe=vibe, query=query),
        )

        if is_new and qid and query:
            # Fire-and-forget: generate a suggestion answer in the background
            async def _generate_answer(question_id: str, q_vibe: str, q_text: str) -> None:
                try:
                    from yonder.grok import GrokClient
                    settings = get_settings()
                    if not settings.grok_ready():
                        return
                    from yonder.lang import detect_lang as _dl, language_directive as _ld

                    _ans_lang = _dl(q_text)
                    system = (
                        "You are a travel expert. A traveler searched with a specific vibe but felt "
                        "the results didn't quite match. Suggest 1–2 destination ideas that DO match "
                        "the vibe and query well. Keep the response to 2–3 sentences, vivid and specific. "
                        "End with the best IATA airport code in parentheses, e.g. (LIS)."
                        + _ld(_ans_lang)
                    )
                    user = f'Vibe: "{q_vibe}"\nQuery: "{q_text}"'
                    async with GrokClient(settings) as grok:
                        text = await grok._chat(system, user, temperature=0.7)
                    import re
                    # Primary: trailing parenthesised IATA, optionally followed by punctuation
                    iata_match = re.search(r"\(([A-Z]{3})\)\s*[.,!?;:]*\s*$", text.strip())
                    iata = iata_match.group(1) if iata_match else None
                    # Fallback 1: any parenthesised IATA anywhere in the reply
                    if iata is None:
                        iata_match2 = re.search(r"\(([A-Z]{3})\)", text)
                        iata = iata_match2.group(1) if iata_match2 else None
                    # Fallback 2: first bare uppercase 3-letter token that is a known IATA
                    if iata is None:
                        from yonder.airports import is_known_iata as _is_known_iata
                        for _m in re.finditer(r"\b([A-Z]{3})\b", text):
                            if _is_known_iata(_m.group(1)):
                                iata = _m.group(1)
                                break
                    answer = {"suggestion": text.strip(), "dest_iata": iata, "lang": _ans_lang}
                    save_vibe_answer(question_id, answer)
                except Exception:
                    pass

            asyncio.create_task(_generate_answer(qid, vibe or "", query))

        return _resp({"ok": True, "direction": "down", "question_id": qid or None})

    return _resp({"ok": True})


@app.get("/api/vibe-suggestions")
async def api_vibe_suggestions(
    vibe: str = Query(""),
    limit: int = Query(20, ge=1, le=100),
    lang: str = Query("", max_length=8),
    q: str = Query("", max_length=300),
) -> JSONResponse:
    """Return AI-answered vibe questions for a given vibe, in one language.

    q is the user's current prompt text: its language is detected server-side
    (full detector — Spanish/French included, not just non-Latin scripts) and
    wins over the lang hint. Defaults to English, so an English compose card
    never shows community suggestions written in another language.
    """
    import asyncio
    from yonder.feedback import get_suggestions_for_vibe
    from yonder.lang import detect_lang as _dl_sugg

    v = (vibe or "").strip().lower() or "adventure"
    if (q or "").strip():
        lang_code = _dl_sugg(q)
    else:
        lang_code = (lang or "en").strip().lower()[:8] or "en"
    suggestions = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: get_suggestions_for_vibe(v, limit=limit, lang=lang_code),
    )
    return JSONResponse({"ok": True, "vibe": v, "suggestions": suggestions})


@app.get("/api/vibe-stats")
async def api_vibe_stats(
    vibe: str = Query(""),
    limit: int = Query(10, ge=1, le=100),
    group: str = Query(""),
) -> JSONResponse:
    """Top destinations per vibe from accumulated usage signals."""
    from yonder.vibe_signals import top_for_vibe

    v = (vibe or "").strip().lower() or "adventure"
    by_country = (group or "").strip().lower() in ("country", "cc", "1", "true")
    top = top_for_vibe(v, limit=limit, group_by_country=by_country)
    return JSONResponse(
        {
            "ok": True,
            "vibe": v,
            "grouped_by_country": by_country,
            "top": top,
        }
    )


@app.post("/api/funnel")
async def api_funnel(request: Request) -> JSONResponse:
    """Lightweight engagement events (e.g. field-note expand) — not Save-level learning."""
    from yonder.attribution import log_event

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    event = str(body.get("event") or "").strip()[:64]
    if not event:
        return JSONResponse({"ok": False, "error": "missing event"}, status_code=400)
    # Allowlist so random clients can't spam arbitrary event names forever
    allowed = {
        "field_note_expand",
        "chip_click",
        "criteria_refresh",
        "outbound_intent",
    }
    if event not in allowed:
        return JSONResponse({"ok": False, "error": "unknown event"}, status_code=400)
    try:
        eid = log_event(
            event,
            click_id=str(body.get("click_id") or "") or None,
            chip_id=str(body.get("chip_id") or "") or None,
            chip_source=str(body.get("chip_source") or "") or None,
            vibe=str(body.get("vibe") or "") or None,
            origin=str(body.get("origin") or "") or None,
            search_id=str(body.get("search_id") or "") or None,
            saved_id=str(body.get("saved_id") or "") or None,
            dest=str(body.get("dest") or "")[:8] or None,
            url=str(body.get("url") or "")[:500] or None,
            meta={k: body[k] for k in ("meta",) if k in body},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:120]}, status_code=500)
    return JSONResponse({"ok": True, "id": eid})


@app.get("/api/place-brief")
async def api_place_brief(
    iata: str = Query(..., min_length=3, max_length=3),
    country: str = Query(""),
    city: str = Query(""),
    role: str = Query("stopover"),
    prompt: str = Query(""),
    vibe: str = Query(""),
) -> JSONResponse:
    """Stream-in field note for one stop (cache-first, live Grok on miss).

    Same structure always; prompt + vibe only tint the prose to match the user.
    """
    from yonder.encyclopedia import get_place_brief

    settings = reload_settings()
    code = (iata or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        return JSONResponse({"ok": False, "error": "bad iata"}, status_code=400)
    try:
        brief = await get_place_brief(
            settings,
            iata=code,
            country=(country or "").strip().upper() or None,
            city=(city or "").strip() or None,
            role=(role or "stopover")[:24],
            user_prompt=(prompt or "").strip()[:400] or None,
            trip_vibe=(vibe or "").strip().lower()[:32] or None,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:120]}, status_code=500)
    if not brief:
        return JSONResponse({"ok": False, "error": "no brief", "iata": code})
    return JSONResponse({"ok": True, "iata": code, "brief": brief.to_dict()})


@app.get("/api/nearest-airport")
async def api_nearest_airport(lat: float, lon: float) -> JSONResponse:
    """Return the IATA code of the nearest scheduled-service airport to lat/lon."""
    import json
    import math

    data_path = _PKG / "static" / "airports_ll.json"
    try:
        airports: dict[str, list[float]] = json.loads(data_path.read_text())
    except Exception:
        return JSONResponse({"ok": False, "error": "airport data unavailable"}, status_code=503)

    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    best_iata, best_dist = "", float("inf")
    for iata, (alat, alon) in airports.items():
        d = _haversine(lat, lon, alat, alon)
        if d < best_dist:
            best_dist, best_iata = d, iata

    if not best_iata:
        return JSONResponse({"ok": False, "error": "no airport found"}, status_code=404)
    return JSONResponse({"ok": True, "iata": best_iata, "distance_km": round(best_dist, 1)})


@app.get("/api/suggest")
async def api_suggest(
    request: Request,
    vibe: str = Query(""),
    origin: str | None = None,
) -> JSONResponse:
    """Dataset-completion chip ranking from ★ Saves (vibe + map context).

    Pills themselves are built client-side to fill missing prompt slots.
    Saves only re-rank which completions to surface and supply soft dest seeds.
    """
    from yonder.saved import ranking_from_saves

    settings = reload_settings()
    home = (origin or "").strip().upper() or settings.resolve_home_iata()
    rank = ranking_from_saves(
        vibe=(vibe or "").strip().lower() or None,
        origin=home,
        visited=settings.visited_country_list(),
        avoid=settings.effective_avoid_country_list(),
        owner_sess=_req_sess(request),
    )
    return JSONResponse(
        {
            "ok": True,
            "vibe": (vibe or "").strip().lower() or None,
            "home": home,
            "ranking": rank,
            "chips": [],  # client builds dataset-completion pills
        }
    )


@app.get("/out")
async def outbound_click(
    request: Request,
    u: str = Query(..., min_length=8, description="Destination booking URL"),
    click_id: str | None = None,
    chip_id: str | None = None,
    chip_source: str | None = None,
    dest: str | None = None,
) -> RedirectResponse:
    """Affiliate-friendly click-through: log funnel event, stamp URL, redirect."""
    from yonder.attribution import log_event, stamp_outbound_url

    settings = get_settings()
    target = (u or "").strip()
    if not target.startswith("http://") and not target.startswith("https://"):
        return RedirectResponse(url="/", status_code=302)
    # Basic open-redirect guard: allow known booking hosts only
    host = target.split("/")[2].lower() if "://" in target else ""
    allowed = (
        "google.com",
        "google.ca",
        "google.co.uk",
        "kayak.com",
        "kayak.ca",
        "www.google.com",
        "www.kayak.com",
        "www.kayak.ca",
    )
    if not any(host == a or host.endswith("." + a.lstrip("www.")) for a in allowed):
        # Still allow airline deep links we generated (any https)
        if not target.startswith("https://"):
            return RedirectResponse(url="/", status_code=302)
    stamped = stamp_outbound_url(
        target,
        click_id=click_id,
        chip_id=chip_id,
        chip_source=chip_source or "book",
        affiliate_tag=getattr(settings, "affiliate_tag", "") or None,
        affiliate_tag_live=bool(getattr(settings, "affiliate_tag_live", False)),
    )
    try:
        log_event(
            "outbound_click",
            click_id=click_id,
            chip_id=chip_id,
            chip_source=chip_source or "book",
            dest=(dest or "")[:8] or None,
            url=stamped or target,
        )
    except Exception:
        pass
    return RedirectResponse(url=stamped or target, status_code=302)


@app.post("/api/travel-map")
async def api_travel_map(request: Request) -> JSONResponse:
    """Autosave visited / avoid country lists from the CRT maps on Search & Adventure."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    from yonder.tiles import (
        country_of_tile,
        normalize_tile_list,
        tile_label,
        visited_countries_from_tiles,
    )

    s = get_settings()
    if "avoid" in body:
        # The avoid payload may mix plain ISO2 country codes and subdivision
        # tile codes (region-level avoid on the subdivided countries).
        avoid_entries = normalize_tile_list(body.get("avoid") or [])
        avoid = normalize_avoid_list([e for e in avoid_entries if "-" not in e])
        avoid_tiles = [e for e in avoid_entries if "-" in e]
    else:
        avoid = list(s.avoid_country_list())
        avoid_tiles = list(s.avoid_tile_list())

    # "visited" now carries tile codes: plain ISO2 country tiles and/or
    # ISO 3166-2 subdivision tiles for the subdivided whitelist countries.
    # Legacy country-only payloads keep working unchanged.
    if "visited" in body:
        tiles = normalize_tile_list(body.get("visited") or [])
    else:
        tiles = list(s.visited_tile_list())

    avoid_set = set(avoid)
    tiles = [t for t in tiles if country_of_tile(t) not in avoid_set]
    # Region-level avoid: redundant under a country-level avoid, and a tile
    # marked visited wins over an avoid mark (documented precedence).
    tile_set = set(tiles)
    avoid_tiles = [
        t
        for t in avoid_tiles
        if country_of_tile(t) not in avoid_set and t not in tile_set
    ]
    # Country list stays in sync with tiles (stamp order preserved — the
    # first stamped country still resolves the traveller's home airport).
    visited = visited_countries_from_tiles(tiles)

    try:
        from yonder.user_prefs import set_prefs as _set_prefs

        _set_prefs(
            {
                "avoid_countries": ",".join(avoid),
                "avoid_tiles": ",".join(avoid_tiles),
                "visited_countries": ",".join(visited),
                "visited_tiles": ",".join(tiles),
            }
        )
        reload_settings()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    from yonder.xp import compute_xp as _compute_xp

    xp_profile = _compute_xp(tiles, avoid)
    return JSONResponse(
        {
            "ok": True,
            "avoid": avoid,
            "avoid_tiles": avoid_tiles,
            "effective_avoid": get_settings().effective_avoid_country_list(),
            "visited": visited,
            "tiles": tiles,
            "avoid_names": [country_label(c) for c in avoid],
            "avoid_tile_names": [tile_label(t) for t in avoid_tiles],
            "visited_names": [country_label(c) for c in visited],
            "tile_names": [tile_label(t) for t in tiles],
            "xp": xp_profile,
        }
    )


_BACKUP_FORMAT = "yonder-backup"
_BACKUP_VERSION = 2
_BACKUP_ACCEPTED_VERSIONS = (1, 2)  # v1 = prefs/map only; v2 adds trips + history
# Non-secret .env settings included in backups (never API keys)
_BACKUP_ENV_KEYS = ("HOME_IATA", "DEFAULT_CURRENCY")


@app.get("/api/backup/export")
async def api_backup_export() -> JSONResponse:
    """Download all user settings + passport map as a single JSON backup.

    Never includes secrets — only user_prefs.db values and the two
    non-secret env prefs (home airport, currency). XP is derived from the
    map and recomputed on import, so it is not stored.
    """
    from yonder.history import export_all as _export_history
    from yonder.saved import export_all as _export_saved
    from yonder.settings_store import read_env as _read_env
    from yonder.user_prefs import PREF_DEFAULTS, get_all_prefs

    s = get_settings()
    prefs = {k: v for k, v in get_all_prefs().items() if k in PREF_DEFAULTS}
    # visited/avoid exported as explicit lists under travel_map
    prefs.pop("visited_countries", None)
    prefs.pop("avoid_countries", None)
    prefs.pop("visited_tiles", None)
    prefs.pop("avoid_tiles", None)
    env = _read_env()
    payload = {
        "format": _BACKUP_FORMAT,
        "version": _BACKUP_VERSION,
        "exported_at": date.today().isoformat(),
        "travel_map": {
            "visited": list(s.visited_country_list()),
            "tiles": list(s.visited_tile_list()),
            "avoid": list(s.avoid_country_list()),
            "avoid_tiles": list(s.avoid_tile_list()),
        },
        "prefs": prefs,
        "settings": {k: (env.get(k) or "").strip() for k in _BACKUP_ENV_KEYS},
        "saved_trips": _export_saved(),
        "price_history": _export_history(),
    }
    fname = f"yonder-backup-{date.today().isoformat()}.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/backup/import")
async def api_backup_import(request: Request) -> JSONResponse:
    """Restore a previously exported backup. Validates everything; rejects junk."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "Not valid JSON."}, status_code=400)

    if not isinstance(body, dict) or body.get("format") != _BACKUP_FORMAT:
        return JSONResponse(
            {"ok": False, "error": "Not a Yonder backup file."}, status_code=400
        )
    try:
        version = int(body.get("version"))
    except (TypeError, ValueError):
        version = -1
    if version not in _BACKUP_ACCEPTED_VERSIONS:
        return JSONResponse(
            {"ok": False, "error": f"Unsupported backup version {body.get('version')!r}."},
            status_code=400,
        )

    tm = body.get("travel_map") or {}
    if not isinstance(tm, dict):
        return JSONResponse(
            {"ok": False, "error": "Malformed travel_map section."}, status_code=400
        )

    def _codes(raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [
            str(c).strip().upper()
            for c in raw
            if isinstance(c, str) and len(str(c).strip()) == 2 and str(c).strip().isalpha()
        ]

    from yonder.tiles import (
        country_of_tile as _tile_cc,
        normalize_tile_list as _norm_tiles,
        visited_countries_from_tiles as _tiles_to_countries,
    )

    avoid = normalize_avoid_list(_codes(tm.get("avoid")))
    avoid_set = set(avoid)
    # Tile-aware backups carry travel_map.tiles; older backups only have the
    # country list — each country becomes its country-level tile (documented
    # migration rule in yonder.tiles).
    tiles = _norm_tiles(tm.get("tiles") if isinstance(tm.get("tiles"), list) else [])
    if not tiles:
        tiles = normalize_country_list(_codes(tm.get("visited")), max_n=250)
    tiles = [t for t in tiles if _tile_cc(t) not in avoid_set]
    visited = _tiles_to_countries(tiles)
    # Region-level avoid tiles (optional section; older backups lack it).
    avoid_tiles = _norm_tiles(
        tm.get("avoid_tiles") if isinstance(tm.get("avoid_tiles"), list) else []
    )
    _tile_set = set(tiles)
    avoid_tiles = [
        t
        for t in avoid_tiles
        if "-" in t and _tile_cc(t) not in avoid_set and t not in _tile_set
    ]

    # Prefs: only known keys, numeric keys sanity-checked
    from yonder.user_prefs import PREF_DEFAULTS, set_prefs as _set_prefs

    raw_prefs = body.get("prefs") or {}
    if not isinstance(raw_prefs, dict):
        return JSONResponse(
            {"ok": False, "error": "Malformed prefs section."}, status_code=400
        )
    numeric_bounds = {
        "col_expected_daily": (0, 5000),
        "col_tolerance_pct": (0, 100),
        "col_hotel": (0, 5000),
        "col_food": (0, 5000),
        "col_transit": (0, 5000),
        "col_culture": (0, 5000),
        "detour_min_stop_days": (1, 21),
        "detour_max_stop_days": (1, 30),
    }
    pref_updates: dict[str, str] = {}
    for key, bounds in numeric_bounds.items():
        if key not in raw_prefs or key not in PREF_DEFAULTS:
            continue
        try:
            v = float(str(raw_prefs[key]).strip() or "0")
        except (TypeError, ValueError):
            continue
        v = max(bounds[0], min(bounds[1], v))
        pref_updates[key] = str(int(v)) if v == int(v) else str(round(v, 2))
    pref_updates["visited_countries"] = ",".join(visited)
    pref_updates["visited_tiles"] = ",".join(tiles)
    pref_updates["avoid_countries"] = ",".join(avoid)
    pref_updates["avoid_tiles"] = ",".join(avoid_tiles)

    # Settings: only the whitelisted non-secret keys, format-validated
    raw_settings = body.get("settings") or {}
    if not isinstance(raw_settings, dict):
        return JSONResponse(
            {"ok": False, "error": "Malformed settings section."}, status_code=400
        )
    env_updates: dict[str, str] = {}
    clear_keys: set[str] = set()
    home = str(raw_settings.get("HOME_IATA") or "").strip().upper()
    if "HOME_IATA" in raw_settings:
        if home == "":
            clear_keys.add("HOME_IATA")
        elif len(home) == 3 and home.isalpha():
            env_updates["HOME_IATA"] = home
    cur = str(raw_settings.get("DEFAULT_CURRENCY") or "").strip().upper()
    if len(cur) == 3 and cur.isalpha():
        env_updates["DEFAULT_CURRENCY"] = cur

    # Saved trips + price history (v2 sections; merged, deduped, never wiped)
    raw_trips = body.get("saved_trips") or []
    raw_history = body.get("price_history") or []
    if not isinstance(raw_trips, list):
        return JSONResponse(
            {"ok": False, "error": "Malformed saved_trips section."}, status_code=400
        )
    if not isinstance(raw_history, list):
        return JSONResponse(
            {"ok": False, "error": "Malformed price_history section."}, status_code=400
        )

    try:
        _set_prefs(pref_updates)
        if env_updates or clear_keys:
            write_env(env_updates, clear_keys=clear_keys)
        reload_settings()
        from yonder.history import import_samples as _import_history
        from yonder.saved import import_rows as _import_saved

        trips_imported, trips_skipped = _import_saved(raw_trips)
        history_imported, history_skipped = _import_history(raw_history)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    from yonder.xp import compute_xp as _compute_xp

    xp_profile = _compute_xp(tiles, avoid)
    return JSONResponse(
        {
            "ok": True,
            "visited": visited,
            "tiles": tiles,
            "avoid": avoid,
            "trips_imported": trips_imported,
            "trips_skipped": trips_skipped,
            "history_imported": history_imported,
            "history_skipped": history_skipped,
            "avoid_names": [country_label(c) for c in avoid],
            "visited_names": [country_label(c) for c in visited],
            "xp": xp_profile,
        }
    )


@app.get("/api/usage/summary")
async def usage_summary() -> JSONResponse:
    """Return AI token usage totals for last 7d, last 30d, and all-time."""
    from yonder.ai_usage import summarise as _summarise

    return JSONResponse({
        "last_7d":  _summarise(7),
        "last_30d": _summarise(30),
        "all_time": _summarise(None),
    })


@app.get("/packing", response_class=HTMLResponse)
async def packing_page(request: Request) -> HTMLResponse:
    """Full packing list — reached via the ad widget on Explore and Share pages."""
    return templates.TemplateResponse(
        request,
        "packing.html",
        {
            "nav": None,
            **_base_ctx(),
        },
    )


@app.get("/api/pois.json")
async def api_pois_json() -> JSONResponse:
    """Slim POI dataset for the world map — all geocoded entries."""
    try:
        from yonder.poi import all_pois_for_map

        data = all_pois_for_map()
    except Exception:
        data = []
    return JSONResponse(content=data)


@app.get("/pois", response_class=HTMLResponse)
async def pois_page(request: Request) -> HTMLResponse:
    """Full interactive curated POI world map, linked from the Packing List page."""
    # Auto-seed on first deployment if the table is empty.
    try:
        from yonder.db import get_conn
        from yonder.poi import import_pois as _import_pois

        with get_conn() as _conn:
            _row = _conn.execute("SELECT 1 FROM pois LIMIT 1").fetchone()
        if not _row:
            _import_pois()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "pois.html",
        {
            "nav": None,
            **_base_ctx(),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: str | None = None, err: str | None = None) -> HTMLResponse:
    flash = None
    if saved:
        flash = {"kind": "ok", "message": saved}
    elif err:
        flash = {"kind": "err", "message": err}
    import os as _os
    from yonder.xp import compute_xp
    settings = reload_settings()
    view = settings_view()
    view["home_resolved"] = settings.resolve_home_iata()
    xp_profile = compute_xp(
        settings.visited_tile_list(),
        settings.avoid_country_list(),
    )
    byom_url_warning: str | None = None
    saved_byom_url = (getattr(settings, "byom_base_url", "") or "").strip()
    if saved_byom_url:
        from yonder.url_guard import BYOMUrlError, validate_byom_url
        try:
            validate_byom_url(saved_byom_url)
        except BYOMUrlError as exc:
            byom_url_warning = str(exc)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "nav": "settings",
            "view": view,
            "flash": flash,
            "is_deployed": bool(_os.environ.get("REPLIT_DOMAINS")),
            "xp_profile": xp_profile,
            "byom_url_warning": byom_url_warning,
            **_base_ctx(settings),
        },
    )


@app.post("/settings")
async def settings_save(request: Request) -> RedirectResponse:
    form = await request.form()
    updates: dict[str, str] = {}
    clear_keys: set[str] = set()

    for key, *_ in MANAGED_KEYS:
        if form.get(f"clear_{key}") in ("1", "on", "true", True):
            clear_keys.add(key)
            updates[key] = ""
            continue
        raw = form.get(key)
        if raw is None:
            continue
        updates[key] = str(raw).strip()

    if "DEFAULT_CURRENCY" in updates and updates["DEFAULT_CURRENCY"]:
        updates["DEFAULT_CURRENCY"] = updates["DEFAULT_CURRENCY"].upper()[:3]

    if "HOME_IATA" in updates:
        hi = (updates["HOME_IATA"] or "").strip().upper()
        if hi and (len(hi) != 3 or not hi.isalpha()):
            return RedirectResponse(
                url="/settings?err=" + quote("Home airport must be a 3-letter IATA code (e.g. YVR) or blank."),
                status_code=303,
            )
        updates["HOME_IATA"] = hi

    if updates.get("BYOM_BASE_URL") and "BYOM_BASE_URL" not in clear_keys:
        from yonder.url_guard import BYOMUrlError, validate_byom_url

        try:
            validate_byom_url(updates["BYOM_BASE_URL"].rstrip("/"))
        except BYOMUrlError as exc:
            return RedirectResponse(
                url="/settings?err=" + quote(f"BYOM URL rejected: {exc}"),
                status_code=303,
            )

    def _col_num(key: str, default: str = "0", lo: float = 0.0, hi: float = 5000.0) -> None:
        if key not in updates:
            return
        try:
            v = float(str(updates[key]).strip() or default)
            updates[key] = str(max(lo, min(hi, v)))
        except ValueError:
            updates[key] = default

    # --- User preferences (stored in user_prefs.db, not .env) ---
    from yonder.user_prefs import set_prefs as _set_prefs

    user_pref_updates: dict[str, str] = {}

    _col_num("COL_EXPECTED_DAILY")
    _col_num("COL_TOLERANCE_PCT", default="25", hi=100.0)
    for pref_key in ("COL_EXPECTED_DAILY", "COL_TOLERANCE_PCT"):
        if pref_key in updates:
            user_pref_updates[pref_key.lower()] = updates.pop(pref_key)
    # Zero legacy component fields in .env (they now live in user_prefs)
    for legacy in ("COL_HOTEL", "COL_FOOD", "COL_TRANSIT", "COL_CULTURE"):
        updates.pop(legacy, None)

    # Detour stop-length preferences and return window → user_prefs.db
    # These are NOT in MANAGED_KEYS (not env vars), so read from form directly.
    for dk, default, lo, hi in (
        ("DETOUR_MIN_STOP_DAYS", "4", 1, 21),
        ("DETOUR_MAX_STOP_DAYS", "5", 1, 30),
        ("RETURN_DAYS", "0", 0, 365),
    ):
        raw_dk = form.get(dk)
        if raw_dk is not None:
            updates[dk] = str(raw_dk).strip()
        if dk in updates:
            try:
                v = max(lo, min(hi, int(float(str(updates[dk]).strip() or default))))
            except (ValueError, TypeError):
                v = int(default)
            user_pref_updates[dk.lower()] = str(v)
            updates.pop(dk, None)

    # Remove country fields from env updates — managed via /api/travel-map
    updates.pop("AVOID_COUNTRIES", None)
    updates.pop("VISITED_COUNTRIES", None)

    if user_pref_updates:
        _set_prefs(user_pref_updates)

    if updates.get("AMADEUS_ENV"):
        env_val = updates["AMADEUS_ENV"].lower()
        updates["AMADEUS_ENV"] = "production" if env_val == "production" else "test"

    if "XAI_MODEL" in updates and not updates["XAI_MODEL"]:
        # keep default via blank = keep existing; if clearing not set, fine
        pass
    if updates.get("XAI_MODEL"):
        updates["XAI_MODEL"] = updates["XAI_MODEL"].strip()

    if updates.get("PROVIDER_MODE"):
        mode = updates["PROVIDER_MODE"].lower().strip()
        updates["PROVIDER_MODE"] = "scan_all" if mode == "scan_all" else "smart"

    if "TESTING" in updates:
        val = str(updates["TESTING"]).strip().lower()
        updates["TESTING"] = (
            "true" if val in ("1", "true", "yes", "on") else "false"
        )

    # AFFILIATE_TAG_LIVE is a checkbox — unchecked = absent from form = "false"
    atl_raw = form.get("AFFILIATE_TAG_LIVE")
    updates["AFFILIATE_TAG_LIVE"] = (
        "true" if str(atl_raw or "").strip().lower() in ("1", "true", "yes", "on") else "false"
    )

    try:
        path = write_env(updates, clear_keys=clear_keys)
        reload_settings()
        ready = get_settings().configured_providers()
        grok = " + Grok" if get_settings().grok_ready() else ""
        msg = (
            f"Saved to {path.name}. Ready providers: "
            f"{', '.join(ready) if ready else 'none (mock only)'}{grok}."
        )
        return RedirectResponse(
            url=f"/settings?saved={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url=f"/settings?err={quote(str(exc))}",
            status_code=303,
        )


@app.get("/api/search")
async def api_search(
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    depart: str = Query(...),
    return_date: str | None = None,
    adults: int = Query(1, ge=1, le=9),
    currency: str = "USD",
    nonstop: bool = False,
    mock: bool = False,
    cabin: CabinClass = CabinClass.ECONOMY,
    max_results: int = Query(25, ge=1, le=100),
    analyze: bool = False,
) -> dict:
    settings = get_settings()
    query = SearchQuery(
        origin=origin.upper(),
        destination=destination.upper(),
        depart_date=date.fromisoformat(depart),
        return_date=date.fromisoformat(return_date) if return_date else None,
        adults=adults,
        cabin=cabin,
        currency=currency.upper() or settings.default_currency,
        max_results=max_results,
        nonstop_only=nonstop,
    )
    # Mock is internal-only (route skeletons when no providers); user-supplied
    # mock params are ignored and invented prices never leave the API.
    include_mock = not settings.configured_providers()
    result = await search_flights(query, settings=settings, include_mock=include_mock)
    result = _mark_missing_fares_result(result)
    out = result.model_dump(mode="json")
    out["analysis"] = None
    return out


@app.post("/api/ask")
async def api_ask(request: Request) -> dict:
    settings = get_settings()
    body = await request.json()
    ask = str(body.get("ask") or "").strip()
    if not ask:
        return {"ok": False, "error": "ask is required"}
    if not settings.grok_ready():
        return {"ok": False, "error": "No AI model configured — add XAI_API_KEY or set a BYOM endpoint in Settings."}
    # Mock is internal-only: route skeletons when no fare providers configured.
    mock = not settings.configured_providers()

    async with GrokClient(settings) as grok:
        trip = await grok.parse_natural_language(
            ask,
            default_currency=settings.default_currency,
            avoid_countries=settings.effective_avoid_country_list(),
            visited_countries=settings.visited_country_list(),
        )
        query = grok.to_search_query(trip)
        result = await search_flights(
            query,
            settings=settings,
            include_mock=mock or not settings.configured_providers(),
        )

    return {
        "ok": True,
        "parsed": trip.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "analysis": None,
    }


@app.get("/api/history")
async def api_history(
    origin: str | None = None,
    destination: str | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    out: dict = {"total": count_samples(), "recent": recent_samples(limit)}
    if origin and destination and len(origin) == 3 and len(destination) == 3:
        st = route_stats(origin.upper(), destination.upper())
        out["route"] = {
            "origin": st.origin,
            "destination": st.destination,
            "n": st.n,
            "min": st.min_price,
            "median": st.median,
            "max": st.max_price,
            "currency": st.currency,
            "last_seen": st.last_seen,
        }
    return out


@app.get("/api/providers")
async def api_providers() -> dict:
    import httpx

    s = reload_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        active = await get_registry().probe_active(s, client, force=True)
        chosen_scan = await choose_providers(
            s, client, mode="scan", need=2, include_mock=False, probe=False
        )
        chosen_adv = await choose_providers(
            s, client, mode="adventure_leg", need=1, include_mock=False, probe=False
        )
    return {
        "configured": s.configured_providers(),
        "active": active,
        "chosen_scan": chosen_scan,
        "chosen_adventure": chosen_adv,
        "budgets": budgets_snapshot(s),
        "grok_ready": s.grok_ready(),
        "available": [
            "amadeus",
            "travelpayouts",
            "duffel",
            "serpapi_google_flights",
            "aviationstack",
            "mock",
            "grok",
        ],
        "settings": settings_view(),
    }


@app.post("/api/providers/probe")
async def api_probe_providers() -> dict:
    import httpx

    s = reload_settings()
    async with httpx.AsyncClient(timeout=45.0) as client:
        active = await get_registry().probe_active(s, client, force=True)
    return {"active": active, "budgets": budgets_snapshot(s)}


@app.get("/admin/intent-misses")
async def admin_intent_misses(
    request: Request,
    threshold: float = 0.7,
    limit: int = 100,
) -> JSONResponse:
    """Return zero-result searches where intent confidence was below threshold.

    Joined against result_feedback to flag any thumbs-down on the same prompt.
    Use these as candidates to add to the paraphrase regression test suite.

    Session-gated: only accessible from localhost / when TESTING=true.
    """
    settings = get_settings()
    # Simple local-only guard: allow when TESTING mode is on, or the request
    # comes from localhost (127.0.0.1 / ::1 / forwarded-for absent).
    client_host = (getattr(request.client, "host", "") or "").split(":")[0]
    is_local = client_host in ("127.0.0.1", "::1", "localhost", "")
    if not is_local and not settings.testing:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    from yonder.vibe_signals import low_confidence_misses

    misses = low_confidence_misses(
        confidence_threshold=max(0.0, min(1.0, threshold)),
        limit=max(1, min(500, limit)),
    )
    return JSONResponse(
        {
            "threshold": threshold,
            "count": len(misses),
            "misses": misses,
        }
    )


@app.post("/api/byom/test")
async def api_byom_test() -> JSONResponse:
    """Send a minimal chat-completion ping to the saved BYOM endpoint.

    Returns {"ok": true} on success, {"ok": false, "error": "..."} on failure.
    The endpoint must be saved first (reads from current settings).
    """
    import httpx

    s = reload_settings()
    byom_base = (getattr(s, "byom_base_url", "") or "").strip().rstrip("/")
    byom_key = (getattr(s, "byom_api_key", "") or "").strip()
    byom_model = (getattr(s, "byom_model", "") or "").strip()

    if not byom_base or not byom_key:
        return JSONResponse({"ok": False, "error": "No BYOM endpoint configured — save URL and API key first."})

    from yonder.url_guard import BYOMUrlError, validate_byom_url

    try:
        validate_byom_url(byom_base)
    except BYOMUrlError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})

    model = byom_model or "gpt-4o-mini"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=8.0, pool=8.0)
        ) as client:
            resp = await client.post(
                f"{byom_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {byom_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        if resp.status_code >= 400:
            body = resp.text[:300].strip()
            return JSONResponse({"ok": False, "error": f"HTTP {resp.status_code}: {body}"})
        data = resp.json()
        # Validate minimal OpenAI-compatible shape
        choices = data.get("choices") or []
        if not choices:
            return JSONResponse({"ok": False, "error": f"Unexpected response (no choices): {str(data)[:200]}"})
        return JSONResponse({"ok": True})
    except httpx.ConnectError as exc:
        return JSONResponse({"ok": False, "error": f"Connection refused or DNS failure: {exc}"})
    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "error": "Request timed out — endpoint took too long to respond."})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:300]})
