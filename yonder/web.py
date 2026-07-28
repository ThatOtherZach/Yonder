from __future__ import annotations

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
    country_label,
    format_place,
    format_route,
    normalize_avoid_list,
    normalize_country_list,
)
from yonder.engine import search_flights
from yonder.grok import GrokClient
from yonder.history import count_samples, recent_samples, route_stats
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
app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")


def _base_ctx(settings=None) -> dict:
    settings = settings or get_settings()
    avoid_codes = settings.avoid_country_list()
    visited_codes = settings.visited_country_list()
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
        # For progress.js fun lines + CRT maps (names only — no secrets)
        "travel_ctx": {
            "avoid": avoid_codes,
            "visited": visited_codes,
            "avoid_names": [country_label(c) for c in avoid_codes],
            "visited_names": [country_label(c) for c in visited_codes],
            "country_names": {code: name for code, name in COUNTRIES},
        },
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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    settings = reload_settings()
    mode = _home_mode(request.query_params.get("mode"))
    adv_form = _adventure_form_defaults(settings)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "nav": "home",
            "mode": mode,
            **_base_ctx(settings),
            "result": None,
            "error": None,
            "ask": "",
            "parsed": None,
            "analysis": None,
            "dest_theme": None,
            "place_book": None,
            "place_books": {},
            "trip_meta": None,
            "form": adv_form
            if mode == "detour"
            else {
                "origin": "YVR",
                "destination": "NRT",
                "depart": "",
                "return_date": "",
                "adults": 1,
                "currency": settings.default_currency,
                "nonstop": False,
                "mock": bool(settings.testing)
                and not bool(settings.configured_providers()),
            },
        },
    )


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

        analysis = None
        if use_grok and settings.grok_ready() and result.offers:
            async with GrokClient(settings) as grok:
                analysis = await grok.analyze_results(prompt=None, query=query, result=result)

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
                "analysis": analysis,
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
    """Natural language → Grok parse → multi-provider scan → Grok analysis.

    GET ?ask=... supported so links/bookmarks work; POST is the form path.
    """
    # Always re-read .env so a key saved mid-session is picked up
    settings = reload_settings()

    if request.method == "GET":
        ask = str(request.query_params.get("ask") or "").strip()
        mock = str(request.query_params.get("mock") or "") in ("true", "on", "1")
        currency_pref = str(
            request.query_params.get("currency") or settings.default_currency or "CAD"
        ).strip().upper()[:3]
        vibe = str(request.query_params.get("vibe") or "").strip().lower()
    else:
        form_data = await request.form()
        ask = str(form_data.get("ask") or "").strip()
        mock = str(form_data.get("mock") or "") in ("true", "on", "1")
        currency_pref = str(
            form_data.get("currency") or settings.default_currency or "CAD"
        ).strip().upper()[:3]
        vibe = str(form_data.get("vibe") or "").strip().lower()

    if not currency_pref.isalpha() or len(currency_pref) != 3:
        currency_pref = (settings.default_currency or "CAD").upper()[:3]
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
            {
                "nav": "home",
                "mode": "escape",
                **_base_ctx(settings),
                "result": None,
                "error": "Type a trip in plain English first.",
                "ask": "",
                "parsed": None,
                "analysis": None,
                "form": empty_form,
                "place_book": None,
                "place_books": {},
            },
            status_code=400,
        )

    if not settings.grok_ready():
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "home",
                "mode": "escape",
                **_base_ctx(settings),
                "result": None,
                "error": "Grok needs an xAI API key. Add XAI_API_KEY in Settings → console.x.ai, then Save.",
                "ask": ask,
                "parsed": None,
                "analysis": None,
                "form": empty_form,
                "place_book": None,
                "place_books": {},
            },
            status_code=400,
        )

    try:
        async with GrokClient(settings) as grok:
            trip = await grok.parse_natural_language(
                ask_for_grok,
                default_currency=currency_pref,
            )
            # UI currency picker wins over anything Grok inferred
            trip = trip.model_copy(update={"currency": currency_pref})
            query = grok.to_search_query(trip)
            query = query.model_copy(update={"currency": currency_pref})
            include_mock = mock or not settings.configured_providers()
            result = await search_flights(
                query, settings=settings, include_mock=include_mock
            )
            # Always analyze when Grok is ready and we have offers
            analysis = None
            if result.offers:
                analysis = await grok.analyze_results(
                    prompt=ask_for_grok, query=query, result=result
                )

        place_book = None
        try:
            from yonder.countries import country_for_iata
            from yonder.encyclopedia import get_place_brief

            place_book_obj = await get_place_brief(
                settings,
                iata=query.destination,
                country=country_for_iata(query.destination),
                city=None,
                role="destination",
            )
            if place_book_obj:
                place_book = place_book_obj.to_dict()
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
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "home",
                "mode": "escape",
                **_base_ctx(settings),
                "result": result,
                "error": None,
                "ask": ask,
                "parsed": trip,
                "analysis": analysis,
                "form": form,
                "dest_theme": _dest_theme(query.destination),
                "place_book": place_book,
                "place_books": {},
            },
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "home",
                "mode": "escape",
                **_base_ctx(settings),
                "result": None,
                "error": f"Grok search failed: {exc}",
                "ask": ask,
                "parsed": None,
                "analysis": None,
                "form": empty_form,
                "dest_theme": None,
                "place_book": None,
                "place_books": {},
            },
            status_code=400,
        )


def _adventure_form_defaults(settings) -> dict:
    from datetime import timedelta

    depart = (date.today() + timedelta(days=45)).isoformat()
    return {
        "prompt": "",
        "origin": "",
        "destination": "",
        "depart": depart,
        "arrive_by": "",
        "min_stop_days": 3,
        "max_stop_days": 5,
        "max_candidates": 5,
        "currency": settings.default_currency or "CAD",
        "vibe": "adventure",
        "mock": bool(settings.testing)
        and not bool(settings.configured_providers()),
        "use_grok": True,
        "grok_story": False,
    }


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
    arrive_by = _s("arrive_by")
    currency = (settings.default_currency or "CAD").upper()
    vibe = _s("vibe", "adventure").lower()
    if not vibe or len(vibe) > 32 or not vibe.replace("-", "").replace("_", "").isalnum():
        vibe = "adventure"
    mock = str(form_data.get("mock") or "") in ("true", "on", "1")
    if not settings.testing:
        mock = False
    use_grok = str(form_data.get("use_grok") or "") in ("true", "on", "1")
    grok_story = str(form_data.get("grok_story") or "") in ("true", "on", "1")
    avoid = settings.avoid_country_list()
    visited = settings.visited_country_list()
    try:
        min_stop = int(_s("min_stop_days", "3"))
        max_stop = int(_s("max_stop_days", "5"))
        max_cand = min(10, max(2, int(_s("max_candidates", "5"))))
    except ValueError:
        min_stop, max_stop, max_cand = 3, 5, 5

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
        "grok_story": grok_story,
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

        # ONE Grok call: cities from description + detour/getaway list
        if use_grok and settings.grok_ready():
            async with GrokClient(settings) as grok:
                try:
                    req, ideas = await grok.translate_adventure(
                        prompt=prompt,
                        form={
                            "origin": "",
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
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(
                        f"Could not read cities from description: {exc}"
                    ) from exc
        else:
            raise ValueError(
                "Turn on “Grok translates + invents” (and set XAI_API_KEY in Settings) "
                "so we can get origin/destination from your description."
            )

        assert req is not None
        if len(req.origin) != 3 or len(req.destination) != 3:
            raise ValueError(
                "Couldn’t resolve airports from the description — try naming a home city "
                "(e.g. “get out of Vancouver for a few days”) or a route "
                "(“Toronto to Vancouver”)."
            )
        # Same O/D is valid getaway: home → somewhere new → home
        if req.origin == req.destination:
            req = req.model_copy(
                update={"trip_kind": "getaway", "include_direct": False}
            )

        if not ideas:
            ideas = seed_ideas(req)

        result = await plan_adventure(
            req, ideas, settings=settings, include_mock=mock
        )

        # Optional second Grok call only if user wants the story (saves tokens by default)
        if grok_story and settings.grok_ready() and result.itineraries:
            async with GrokClient(settings) as grok:
                try:
                    result.narrative = await grok.narrate_adventure(result)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"Narrative skipped: {exc}")

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

        trip_meta = {
            "adults": result.request.adults,
            "currency": result.request.currency,
            "cabin": (
                result.request.cabin.value
                if hasattr(result.request.cabin, "value")
                else str(result.request.cabin or "economy")
            ),
            "vibe": result.request.vibe,
            "prompt": result.request.prompt,
            "origin": result.request.origin,
            "destination": result.request.destination,
        }
        place_books: dict = {}
        try:
            from yonder.encyclopedia import briefs_for_stops

            stops = [
                (it.stop_iata, it.theme_country, it.stop_city)
                for it in result.itineraries
                if it.stop_iata
            ]
            place_books = await briefs_for_stops(settings, stops, max_n=4)
        except Exception:
            place_books = {}

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "home",
                "mode": "detour",
                **_base_ctx(settings),
                "result": result,
                "error": None,
                "form": form,
                "trip_meta": trip_meta,
                "place_book": None,
                "place_books": place_books,
                "ask": "",
                "parsed": None,
                "analysis": None,
                "dest_theme": None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "nav": "home",
                "mode": "detour",
                **_base_ctx(settings),
                "result": None,
                "error": str(exc),
                "form": form,
                "trip_meta": None,
                "place_book": None,
                "place_books": {},
                "ask": "",
                "parsed": None,
                "analysis": None,
                "dest_theme": None,
            },
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


@app.get("/saved", response_class=HTMLResponse)
async def saved_list_page(
    request: Request,
    flash: str | None = None,
    err: str | None = None,
) -> HTMLResponse:
    settings = reload_settings()
    items = list_saved(limit=100)
    return templates.TemplateResponse(
        request,
        "saved.html",
        {
            "nav": "saved",
            **_base_ctx(settings),
            "items": items,
            "cards": _saved_cards(items),
            "flash": flash,
            "error": err,
        },
    )


@app.post("/api/saved")
async def api_save_itinerary(request: Request):
    """Save an adventure itinerary snapshot (JSON body)."""
    from fastapi.responses import JSONResponse

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    itinerary = body.get("itinerary") or body
    if not isinstance(itinerary, dict) or not itinerary.get("title"):
        return JSONResponse(
            {"ok": False, "error": "Missing itinerary payload"}, status_code=400
        )
    trip_meta = body.get("trip_meta") if isinstance(body.get("trip_meta"), dict) else {}
    # Allow trip_meta at top level fields too
    for k in ("adults", "currency", "cabin", "vibe", "prompt", "origin", "destination"):
        if k in body and k not in trip_meta:
            trip_meta[k] = body[k]
    try:
        saved = save_itinerary(itinerary, trip_meta=trip_meta)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
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
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "nav": "settings",
            "view": settings_view(),
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

    def _col_num(key: str, default: str = "0", lo: float = 0.0, hi: float = 5000.0) -> None:
        if key not in updates:
            return
        try:
            v = float(str(updates[key]).strip() or default)
            updates[key] = str(max(lo, min(hi, v)))
        except ValueError:
            updates[key] = default

    for ck in ("COL_HOTEL", "COL_FOOD", "COL_TRANSIT", "COL_CULTURE", "COL_EXPECTED_DAILY"):
        _col_num(ck)
    _col_num("COL_TOLERANCE_PCT", default="25", hi=100.0)

    # Component bag sums to daily total (authoritative when any component > 0)
    try:
        s = get_settings()

        def _comp(form_key: str, attr: str) -> float:
            if form_key in updates:
                return float(updates[form_key] or 0)
            return float(getattr(s, attr, 0) or 0)

        total = (
            _comp("COL_HOTEL", "col_hotel")
            + _comp("COL_FOOD", "col_food")
            + _comp("COL_TRANSIT", "col_transit")
            + _comp("COL_CULTURE", "col_culture")
        )
        if total > 0:
            updates["COL_EXPECTED_DAILY"] = str(round(total, 2))
    except (TypeError, ValueError):
        pass

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
    if analyze and settings.grok_ready() and result.offers:
        async with GrokClient(settings) as grok:
            analysis = await grok.analyze_results(prompt=None, query=query, result=result)
            out["analysis"] = analysis.model_dump()
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
        trip = await grok.parse_natural_language(ask, default_currency=settings.default_currency)
        query = grok.to_search_query(trip)
        result = await search_flights(
            query,
            settings=settings,
            include_mock=mock or not settings.configured_providers(),
        )
        analysis = None
        if result.offers:
            analysis = await grok.analyze_results(prompt=ask, query=query, result=result)

    return {
        "ok": True,
        "parsed": trip.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "analysis": analysis.model_dump() if analysis else None,
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
