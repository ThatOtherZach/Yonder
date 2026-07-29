from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from yonder.adventure import (
    AdventureItinerary,
    AdventureRequest,
    plan_adventure,
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
from yonder.engine import search_flights
from yonder.grok import GrokClient
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
    count_saved,
    delete as delete_saved,
    get as get_saved,
    list_saved,
    save_itinerary,
    update_from_itinerary,
)
from yonder.settings_store import MANAGED_KEYS, settings_view, write_env
from yonder.themes import theme_css_vars, theme_for_iata
from yonder.types import CabinClass, SearchQuery
from yonder.share import create_share, dump_obj, get_share, qr_png_data_uri, qr_svg_for_url
from yonder.vibe_theme import resolve_vibe, vibe_theme


def _share_pack(request: Request, *, kind: str, title: str, payload: dict) -> dict:
    """Stable share URL + scannable PNG QR for a boarding-pass stub.

    Link and QR always use the same absolute pretty URL:
    /t/escape/YVR-NRT-2026-08-20-…/id
    """
    trip = create_share(kind=kind, title=title, payload=dump_obj(payload))
    base = str(request.base_url).rstrip("/")
    url = f"{base}{trip.path}"
    return {
        "id": trip.id,
        "url": url,
        "path": trip.path,
        # Slightly larger modules — pretty paths are longer than /t/{id}
        "qr_src": qr_png_data_uri(url, scale=5, border=2),
        "qr_svg": qr_svg_for_url(url, scale=4),
    }


def _share_escape(request: Request, result, offer) -> dict | None:
    try:
        q = result.query
        title = f"{q.origin} → {q.destination}"
        return _share_pack(
            request,
            kind="escape",
            title=title,
            payload={"query": dump_obj(q), "offer": dump_obj(offer)},
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


def _dest_theme(destination: str) -> dict:
    t = theme_for_iata(destination, kind="stopover")
    t["theme_style"] = theme_css_vars(t)
    t["place"] = format_place(destination)
    # ensure flag_img present (theme_for_country already sets it)
    if not t.get("flag_img") and t.get("country"):
        from yonder.themes import flag_img_url

        t["flag_img"] = flag_img_url(t["country"], width=80) or ""
    return t

app = FastAPI(title="Yonder", description="Personal travel planner — flights, adventures, itineraries")
_PKG = Path(__file__).parent
templates = Jinja2Templates(directory=str(_PKG / "templates"))
templates.env.globals["place"] = format_place
templates.env.globals["route"] = format_route
templates.env.globals["airline_site_label"] = airline_site_label
templates.env.globals["airline_name"] = airline_display_name
templates.env.globals["share_escape"] = _share_escape
templates.env.globals["share_detour"] = _share_detour
app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")


def _base_ctx(settings=None, *, vibe: str | None = None) -> dict:
    settings = settings or get_settings()
    avoid_codes = settings.avoid_country_list()
    visited_codes = settings.visited_country_list()
    vt = vibe_theme(vibe) if vibe else None
    return {
        "providers": settings.configured_providers(),
        "grok_ready": settings.grok_ready(),
        "testing": bool(settings.testing),
        "countries": COUNTRIES,
        "avoid_defaults": avoid_codes,
        "visited_defaults": visited_codes,
        "budgets": budgets_snapshot(settings),
        "history_count": count_samples(),
        "saved_count": count_saved(),
        "vibe_theme": vt,
        # For progress.js fun lines + CRT maps (names only — no secrets)
        "travel_ctx": {
            "avoid": avoid_codes,
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
        "mock": bool(settings.testing) and not bool(settings.configured_providers()),
        "vibe": "",
    }


def _escape_panel(settings, override: dict | None = None) -> dict:
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
    snap = load_last("escape")
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


def _detour_panel(settings, override: dict | None = None) -> dict:
    """Last Detour search (or live override) for the unified toggle UI."""
    base = {
        "form": _adventure_form_defaults(settings),
        "result": None,
        "trip_meta": None,
        "place_books": {},
        "error": None,
    }
    snap = load_last("detour")
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


def _compose_page_ctx(
    settings,
    *,
    mode: str,
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
    esc = _escape_panel(settings, escape_override)
    det = _detour_panel(settings, detour_override)
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
    ctx = _compose_page_ctx(settings, mode=mode)
    return templates.TemplateResponse(request, "index.html", ctx)


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
    mock: bool = False,
    use_grok: bool = True,
) -> HTMLResponse:
    settings = get_settings()
    form = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart": depart,
        "return_date": return_date or "",
        "adults": adults,
        "currency": currency.upper(),
        "nonstop": nonstop,
        "mock": mock,
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
        include_mock = mock or not settings.configured_providers()
        result = await search_flights(query, settings=settings, include_mock=include_mock)

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

    if request.method == "GET":
        ask = str(request.query_params.get("ask") or "").strip()
        mock = str(request.query_params.get("mock") or "") in ("true", "on", "1")
        vibe = str(request.query_params.get("vibe") or "").strip().lower()
    else:
        form_data = await request.form()
        ask = str(form_data.get("ask") or "").strip()
        mock = str(form_data.get("mock") or "") in ("true", "on", "1")
        vibe = str(form_data.get("vibe") or "").strip().lower()

    # Display currency always from Settings (default USD)
    currency_pref = (settings.default_currency or "USD").strip().upper()[:3]
    if not currency_pref.isalpha() or len(currency_pref) != 3:
        currency_pref = "USD"
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"

    # Test Data (mock) only when TESTING=true; always allow mock if no live keys
    if not settings.testing:
        mock = False
    if not settings.configured_providers():
        mock = True

    empty_form = {
        "origin": "YVR",
        "destination": "NRT",
        "depart": "",
        "return_date": "",
        "adults": 1,
        "currency": currency_pref,
        "nonstop": False,
        "mock": mock,
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
                mode="escape",
                error="Grok needs an xAI API key. Add XAI_API_KEY in Settings → console.x.ai, then Save.",
                escape_override={"ask": ask, "form": empty_form},
            ),
            status_code=400,
        )

    try:
        avoid = settings.avoid_country_list()
        visited = settings.visited_country_list()
        aim, _skip = settings.search_timing()
        home_iata = settings.resolve_home_iata()

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
            return trip, query, result

        # Soft aim only — no hard kill (Skip is on the unified /explore path)
        trip, query, result = await _escape_run()

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
                place_book = {
                    **hit,
                    "iata": query.destination,
                    "country": country_for_iata(query.destination),
                    "from_cache": True,
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
            "mock": mock,
            "vibe": vibe,
        }
        dest_theme = _dest_theme(query.destination)
        vt = vibe_theme(vibe)
        form["vibe"] = vt["id"]
        form["vibe_color"] = vt["color"]
        save_last(
            "escape",
            {
                "ask": ask,
                "form": form,
                "result": result,
                "parsed": trip,
                "analysis": analysis,
                "dest_theme": dest_theme,
                "place_book": place_book,
            },
        )
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
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
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
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
        "mock": bool(settings.testing)
        and not bool(settings.configured_providers()),
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
    if force in ("escape", "detour", "mix"):
        pass
    else:
        force = None
    mock = str(form_data.get("mock") or "") in ("true", "on", "1")
    if not settings.testing:
        mock = False
    if not settings.configured_providers():
        mock = True

    currency = (settings.default_currency or "USD").upper()
    if not currency.isalpha() or len(currency) != 3:
        currency = "USD"
    avoid = settings.avoid_country_list()
    visited = settings.visited_country_list()
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

        save_ban = saved_destination_iatas(limit=200) or set()
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
        "mock": mock,
        "vibe": vibe,
    }
    det_form = {
        **defaults,
        "prompt": prompt,
        "depart": depart,
        "currency": currency,
        "vibe": vibe,
        "mock": mock,
        "origin": home_iata,
    }

    if not prompt:
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                mode="escape",
                error="Type a trip in plain English first.",
                escape_override={"ask": "", "form": empty_esc},
            ),
            status_code=400,
        )

    decision = decide_shape(prompt, force=force)
    max_cand = mix_candidate_cap(decision.shape, max_cand_settings)
    notes: list[str] = [
        f"Intent: {decision.shape} ({decision.confidence:.0%}) — {decision.rationale}"
    ]
    if save_ban:
        notes.append(
            "Excluding "
            + str(len(save_ban))
            + " ★ Saved city(ies): "
            + ", ".join(sorted(save_ban)[:12])
            + ("…" if len(save_ban) > 12 else "")
        )
    if is_refresh:
        if exclude_iatas - save_ban:
            notes.append(
                "Refresh: also skipping already-shown "
                + ", ".join(sorted(exclude_iatas - save_ban)[:10])
            )
        else:
            notes.append("Refresh: rolling new candidates")

    escape_override: dict = {"ask": prompt, "form": empty_esc, "result": None}
    detour_override: dict = {"form": det_form, "result": None}
    errors: list[str] = []
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
        nonlocal escape_override, active_mode, restored_first
        if search_id and is_cancelled(search_id):
            errors.append("Escape skipped — user hit Skip")
            return
        if not settings.grok_ready() and not mock:
            errors.append("Escape skipped — add XAI_API_KEY or enable Test Data")
            return
        remaining = _soft_remaining()
        if remaining <= 0 and search_id and is_cancelled(search_id):
            errors.append("Escape skipped — user hit Skip")
            return
        ask_for_grok = f"{prompt}\n\nTrip vibe: {vibe}."
        async with GrokClient(settings) as grok:
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
                notes.append(
                    f"Origin corrected {trip.origin}→{home_iata} (home wins over chip text)"
                )
                trip = trip.model_copy(update={"origin": home_iata.upper()})
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
                            trip_kind="getaway",
                        ),
                        exclude_iatas=exclude_iatas,
                        shuffle=True,
                    )
                    if pool:
                        trip = trip.model_copy(
                            update={
                                "destination": pool[0].iata.upper(),
                                "assumptions": list(trip.assumptions or [])
                                + [f"Refresh rolled destination away from {dest_u}"],
                            }
                        )
                        notes.append(f"Refresh: new Escape dest {pool[0].iata}")
                except Exception:
                    pass
            trip = trip.model_copy(
                update={"currency": currency, "adults": 1, "cabin": CabinClass.ECONOMY}
            )
            query = grok.to_search_query(trip)
            query = query.model_copy(
                update={"currency": currency, "adults": 1, "cabin": CabinClass.ECONOMY}
            )
            fare_timeout = min(18.0, max(5.0, remaining * 0.5 if remaining < 100 else 14.0))
            result = await search_flights(
                query,
                settings=settings,
                include_mock=mock,
                timeout=fare_timeout,
                max_providers=1,
            )
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
            "mock": mock,
            "vibe": vibe,
            "vibe_color": vibe_theme(vibe)["color"],
        }
        dest_theme = _dest_theme(query.destination)
        place_book = None
        try:
            from yonder.countries import country_for_iata
            from yonder.encyclopedia import get_cached, cache_key, get_place_brief

            dest_cc = country_for_iata(query.destination)
            key = cache_key(query.destination, dest_cc, None)
            hit = get_cached(key) if key else None
            if hit:
                place_book = {
                    **hit,
                    "iata": query.destination,
                    "country": dest_cc,
                    "from_cache": True,
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
            },
        }
        # Escape refresh got nothing useful → first set
        if is_refresh and (not result or not result.offers):
            first_snap = load_first("escape")
            if first_snap and first_snap.get("result"):
                from yonder.last_search import hydrate_escape

                restored = hydrate_escape(first_snap)
                if restored.get("result") and restored["result"].offers:
                    notes.append("No new Escape fare — back to first set")
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
            {
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
            home = _guess_home_iata(prompt) or home_iata
            local_req = AdventureRequest(
                origin=home,
                destination=home,
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
                trip_kind="getaway",
                include_direct=False,
            )
            local_ideas = seed_ideas(
                local_req, exclude_iatas=exclude_iatas, shuffle=is_refresh
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
                notes.append("Fast path: invent from chip seeds")
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
                        ),
                        timeout=invent_timeout,
                    )
                if search_id and is_cancelled(search_id):
                    errors.append("Detour invent finished after Skip — packaging what we can")
                trip_kind = (req.trip_kind or "detour").lower()
                if req.origin == req.destination:
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
                        "vibe": vibe,
                        "prompt": prompt,
                        "trip_kind": trip_kind,
                        "include_direct": False,
                        "adults": 1,
                        "cabin": CabinClass.ECONOMY,
                    }
                )
                if not ideas:
                    ideas = seed_ideas(
                        req, exclude_iatas=exclude_iatas, shuffle=is_refresh
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
                    req, exclude_iatas=exclude_iatas, shuffle=True
                )

        # Optional non-Save chip seeds only (dataset pattern, not ★ Saves)
        if req is not None and chip_seeds and not is_refresh:
            from yonder.adventure import StopoverIdea

            if not ideas:
                ideas = seed_ideas(req, exclude_iatas=exclude_iatas)
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
                req, exclude_iatas=exclude_iatas, shuffle=is_refresh
            )

        assert req is not None
        if search_id and is_cancelled(search_id) and not ideas:
            errors.append("Detour cancelled before pricing")
            return
        # No hard outer timeout — plan_adventure honors Skip via cancel_id
        result = await plan_adventure(
            req,
            ideas,
            settings=settings,
            include_mock=mock,
            cancel_id=search_id or None,
            exclude_iatas=exclude_iatas,
        )
        # Refresh found nothing new → restore first result set
        if is_refresh and not (result.itineraries or []):
            first_snap = load_first("detour")
            if first_snap and first_snap.get("result"):
                from yonder.last_search import hydrate_detour

                restored = hydrate_detour(first_snap)
                if restored.get("result") and restored["result"].itineraries:
                    result = restored["result"]
                    place_books_restored = restored.get("place_books") or {}
                    notes.append("No new cities left — back to first set")
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
            "mock": mock,
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
            if place_books and not skipped:
                notes.append(f"Field notes for {len(place_books)} stop(s)")
            elif skipped and place_books:
                notes.append("Field notes from cache (Skip — more may load in UI)")
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
            {
                "form": form,
                "result": result,
                "trip_meta": trip_meta,
                "place_books": place_books,
            },
            pin_first=not is_refresh,
        )
        if decision.shape != "mix":
            active_mode = "detour"

    try:
        if decision.shape in ("escape", "mix"):
            try:
                await _do_escape()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Escape: {exc}")
        if decision.shape in ("detour", "mix"):
            try:
                await _do_detour()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Detour: {exc}")

        has_esc = bool(escape_override.get("result"))
        has_det = bool(detour_override.get("result"))
        if not has_esc and not has_det:
            raise ValueError(
                "; ".join(errors) if errors else "Nothing priced — try again or Test Data."
            )

        # Prefer showing the side that has data; mix defaults to escape panel first
        if decision.shape == "mix":
            active_mode = "escape" if has_esc else "detour"
        elif decision.shape == "detour":
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

        ctx = _compose_page_ctx(
            settings,
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
        ctx["intent_shape"] = decision.shape
        ctx["intent_rationale"] = decision.rationale
        ctx["result_filter"] = "all"
        if err_msg and (has_esc or has_det):
            ctx["intent_note"] = err_msg
        return templates.TemplateResponse(request, "index.html", ctx)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
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


@app.get("/adventure", response_class=HTMLResponse)
async def adventure_home(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/?mode=detour", status_code=302)


@app.post("/adventure", response_class=HTMLResponse)
async def adventure_run(request: Request) -> HTMLResponse:
    settings = reload_settings()
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
    mock = str(form_data.get("mock") or "") in ("true", "on", "1")
    if not settings.testing:
        mock = False
    use_grok = True  # always invent with Grok when key is present
    avoid = settings.avoid_country_list()
    visited = settings.visited_country_list()
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
        "mock": mock,
        "use_grok": use_grok,
    }

    if not settings.configured_providers():
        mock = True
        form["mock"] = True

    try:
        if not prompt:
            raise ValueError("Describe your trip (cities + vibe).")
        if not depart:
            raise ValueError("Pick a depart date.")

        ideas: list = []
        req: AdventureRequest | None = None
        aim, _skip = settings.search_timing()

        # ONE Grok call: cities from description + detour/getaway list.
        # On failure, fall back to passport map + local home-city guess (seed ideas).
        from yonder.grok import _guess_home_iata

        def _local_getaway_fallback(reason: str = "") -> tuple:
            home = (
                _guess_home_iata(prompt)
                or settings.resolve_home_iata()
            )
            local_req = AdventureRequest(
                origin=home,
                destination=home,
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
                trip_kind="getaway",
                include_direct=False,
            )
            local_ideas = seed_ideas(local_req)
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
                            "vibe": vibe or req.vibe,
                            "prompt": prompt,
                            "trip_kind": trip_kind,
                            "include_direct": trip_kind != "getaway",
                        }
                    )
                    # Grok returned empty candidates — fill from passport-aware seeds
                    if not ideas:
                        ideas = seed_ideas(req)
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
            ideas = seed_ideas(req)
        if not ideas:
            raise ValueError(
                "No candidate cities after applying your visited/avoid map. "
                "Unstamp some visited countries or try a different vibe."
            )

        # Force include_direct off for speed (baseline is optional noise under soft aim)
        req = req.model_copy(update={"include_direct": False})
        result = await plan_adventure(
            req, ideas, settings=settings, include_mock=mock
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
            {
                "form": form,
                "result": result,
                "trip_meta": trip_meta,
                "place_books": place_books,
            },
        )
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                mode="detour",
                detour_override={
                    "form": form,
                    "result": result,
                    "trip_meta": trip_meta,
                    "place_books": place_books,
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            _compose_page_ctx(
                settings,
                mode="detour",
                error=str(exc),
                detour_override={"form": form},
            ),
            status_code=400,
        )


# ── Saved itineraries ────────────────────────────────────────────────────────


def _saved_cards(items: list) -> list[dict]:
    """Hydrate saved rows into AdventureItinerary so the UI matches Adventure 1:1."""
    from yonder.adventure import AdventureItinerary, _apply_theme

    cards: list[dict] = []
    for s in items:
        it = None
        try:
            it = AdventureItinerary.model_validate(s.itinerary or {})
            if not it.theme_style or not it.theme_primary:
                it = _apply_theme(it)
        except Exception:
            it = None
        cards.append({"saved": s, "it": it})
    return cards


def _render_shared_trip(request: Request, share_id: str) -> HTMLResponse:
    """Standalone shareable itinerary page (QR target)."""
    settings = reload_settings()
    share = get_share(share_id)
    if not share:
        return templates.TemplateResponse(
            request,
            "trip.html",
            {
                "nav": "home",
                **_base_ctx(settings),
                "share": None,
                "error": "This shared trip is missing or expired.",
                "share_url": str(request.url),
                "qr_svg": "",
                "kind_label": "trip",
            },
            status_code=404,
        )
    base = str(request.base_url).rstrip("/")
    url = f"{base}{share.path}"
    kind_label = {
        "escape": "Escape",
        "detour": "Detour",
    }.get(share.kind, share.kind.title())
    return templates.TemplateResponse(
        request,
        "trip.html",
        {
            "nav": "home",
            **_base_ctx(settings),
            "share": share,
            "error": None,
            "share_url": url,
            "qr_src": qr_png_data_uri(url, scale=7, border=3),
            "qr_svg": qr_svg_for_url(url, scale=5),
            "kind_label": kind_label,
        },
    )


@app.get("/t/{kind}/{slug}/{share_id}", response_class=HTMLResponse)
async def shared_trip_pretty(
    request: Request, kind: str, slug: str, share_id: str
) -> HTMLResponse:
    """Human-readable share URL: /t/escape/YVR-NRT-2026-08-20/abc123…"""
    return _render_shared_trip(request, share_id)


@app.get("/t/{share_id}", response_class=HTMLResponse)
async def shared_trip_page(request: Request, share_id: str) -> HTMLResponse:
    """Legacy short form /t/{id} — still works for old QRs."""
    # Avoid capturing multi-segment paths if routed here by mistake
    if "/" in share_id:
        return RedirectResponse(url="/", status_code=302)
    return _render_shared_trip(request, share_id)


@app.get("/saved", response_class=HTMLResponse)
async def saved_list_page(
    request: Request,
    flash: str | None = None,
    err: str | None = None,
) -> HTMLResponse:
    settings = reload_settings()
    items = list_saved(limit=100)
    cards = _saved_cards(items)
    for card in cards:
        s = card.get("saved")
        it = card.get("it")
        if not s:
            continue
        try:
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
    return templates.TemplateResponse(
        request,
        "saved.html",
        {
            "nav": "saved",
            **_base_ctx(settings),
            "items": items,
            "cards": cards,
            "flash": flash,
            "error": err,
        },
    )


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
    trip_meta.setdefault("avoid", settings.avoid_country_list())
    try:
        saved = save_itinerary(itinerary, trip_meta=trip_meta)
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
    return JSONResponse(
        {
            "ok": True,
            "id": saved.id,
            "title": saved.title,
            "display_price": saved.display_price,
            "saved_count": count_saved(),
        }
    )


@app.post("/saved/{saved_id}/refresh", response_class=HTMLResponse)
async def saved_refresh(request: Request, saved_id: str) -> HTMLResponse:
    settings = reload_settings()
    item = get_saved(saved_id)
    if not item:
        return RedirectResponse(
            url="/saved?err=" + quote("Itinerary not found"), status_code=302
        )

    form_data = await request.form()
    mock = str(form_data.get("mock") or "") in ("true", "on", "1")
    if not settings.testing:
        mock = False
    if not settings.configured_providers():
        mock = True

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
        update_from_itinerary(
            saved_id,
            refreshed.model_dump(mode="json"),
            trip_meta=trip_meta,
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
    ok = delete_saved(saved_id)
    if ok:
        return RedirectResponse(
            url="/saved?flash=" + quote("Removed from list"), status_code=302
        )
    return RedirectResponse(
        url="/saved?err=" + quote("Not found"), status_code=302
    )


@app.post("/api/results-clear")
async def api_results_clear() -> JSONResponse:
    """Clear last Escape + Detour result snapshots (UI Clear filter)."""
    from yonder.last_search import clear_last

    try:
        clear_last(None)
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


@app.get("/api/suggest")
async def api_suggest(
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
        avoid=settings.avoid_country_list(),
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

    s = get_settings()
    if "avoid" in body:
        avoid = normalize_avoid_list(body.get("avoid") or [])
    else:
        avoid = list(s.avoid_country_list())

    if "visited" in body:
        visited = normalize_country_list(body.get("visited") or [], max_n=250)
    else:
        visited = list(s.visited_country_list())

    avoid_set = set(avoid)
    visited = [c for c in visited if c not in avoid_set]

    updates = {
        "AVOID_COUNTRIES": ",".join(avoid),
        "VISITED_COUNTRIES": ",".join(visited),
    }
    # write_env treats "" as "keep existing" for secrets — force clear empty lists
    clear_keys = {k for k, v in updates.items() if not v}

    try:
        write_env(updates, clear_keys=clear_keys)
        reload_settings()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "ok": True,
            "avoid": avoid,
            "visited": visited,
            "avoid_names": [country_label(c) for c in avoid],
            "visited_names": [country_label(c) for c in visited],
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: str | None = None, err: str | None = None) -> HTMLResponse:
    flash = None
    if saved:
        flash = {"kind": "ok", "message": saved}
    elif err:
        flash = {"kind": "err", "message": err}
    settings = reload_settings()
    view = settings_view()
    view["home_resolved"] = settings.resolve_home_iata()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "nav": "settings",
            "view": view,
            "flash": flash,
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

    def _col_num(key: str, default: str = "0", lo: float = 0.0, hi: float = 5000.0) -> None:
        if key not in updates:
            return
        try:
            v = float(str(updates[key]).strip() or default)
            updates[key] = str(max(lo, min(hi, v)))
        except ValueError:
            updates[key] = default

    # Single daily $ field is authoritative; zero legacy component split on save
    _col_num("COL_EXPECTED_DAILY")
    _col_num("COL_TOLERANCE_PCT", default="25", hi=100.0)
    updates["COL_HOTEL"] = "0"
    updates["COL_FOOD"] = "0"
    updates["COL_TRANSIT"] = "0"
    updates["COL_CULTURE"] = "0"

    # Country map / multi-select → normalize ISO2 lists
    avoid_multi = form.getlist("AVOID_COUNTRIES_MULTI") if hasattr(form, "getlist") else []
    if avoid_multi:
        updates["AVOID_COUNTRIES"] = ",".join(
            normalize_avoid_list([str(x) for x in avoid_multi])
        )
    elif "AVOID_COUNTRIES" in updates:
        updates["AVOID_COUNTRIES"] = ",".join(
            normalize_avoid_list(updates.get("AVOID_COUNTRIES") or "")
        )

    if "VISITED_COUNTRIES" in updates:
        avoid_set = set(
            normalize_avoid_list(updates.get("AVOID_COUNTRIES") or "")
        )
        # Prefer form avoid; if only visited updated, still strip overlaps after avoid normalize above
        if "AVOID_COUNTRIES" not in updates:
            avoid_set = set(get_settings().avoid_country_list())
        visited = normalize_country_list(
            updates.get("VISITED_COUNTRIES") or "", max_n=250
        )
        updates["VISITED_COUNTRIES"] = ",".join(
            c for c in visited if c not in avoid_set
        )

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
    include_mock = mock or not settings.configured_providers()
    result = await search_flights(query, settings=settings, include_mock=include_mock)
    out = result.model_dump(mode="json")
    out["analysis"] = None
    return out


@app.post("/api/ask")
async def api_ask(request: Request) -> dict:
    settings = get_settings()
    body = await request.json()
    ask = str(body.get("ask") or "").strip()
    mock = bool(body.get("mock", False))
    if not ask:
        return {"ok": False, "error": "ask is required"}
    if not settings.grok_ready():
        return {"ok": False, "error": "XAI_API_KEY not configured"}
    if not settings.configured_providers():
        mock = True

    async with GrokClient(settings) as grok:
        trip = await grok.parse_natural_language(
            ask,
            default_currency=settings.default_currency,
            avoid_countries=settings.avoid_country_list(),
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
