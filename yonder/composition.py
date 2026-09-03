"""Compose user-selected one-way Escape fares into existing trip models.

This module is deliberately independent of the web layer.  The browser may
send fare snapshots back to the server, but this boundary is the authority for
route shape, date ordering, and the model used to render/save the result.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
from datetime import date
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from yonder.adventure import AdventureItinerary, PricedLeg, QuestIdea
from yonder.countries import city_for_iata
from yonder.links import google_flights_multi
from yonder.money import format_approx
from yonder.types import FlightOffer, SearchQuery
from yonder.url_guard import BYOMUrlError, validate_byom_url
from yonder.vibe_theme import vibe_theme


class FareCompositionError(ValueError):
    """A selected fare cannot participate in the requested composition."""


class SelectedFare(BaseModel):
    query: SearchQuery
    offer: FlightOffer
    vibe: str = "adventure"
    prompt: str = ""
    manual_flight: bool = False
    vibe_airport: str | None = None


class SelectedQuest(BaseModel):
    """A previously rendered Quest card selected for Detour composition."""

    kind: str = "quest"
    idea: dict[str, Any]
    home_iata: str
    vibe: str = "adventure"
    prompt: str = ""


_FALLBACK_SIGNING_SECRET = secrets.token_bytes(32)


def _signing_secret() -> bytes:
    configured = os.environ.get("SESSION_SECRET", "")
    return configured.encode("utf-8") if configured else _FALLBACK_SIGNING_SECRET


def sign_selection(value: dict[str, Any]) -> str:
    """Return a tamper-evident token for a server-rendered selectable card."""
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    return (
        payload.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def verify_selection(token: str) -> dict[str, Any]:
    """Verify and decode a server-issued selection token."""
    if not isinstance(token, str) or len(token) > 131_072 or "." not in token:
        raise FareCompositionError("That fare selection has expired. Select it again.")
    payload, supplied = token.split(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(_signing_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(supplied, expected):
        raise FareCompositionError("That fare selection has expired. Select it again.")
    try:
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        value = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FareCompositionError("That fare selection has expired. Select it again.") from exc
    if not isinstance(value, dict):
        raise FareCompositionError("That fare selection has expired. Select it again.")
    return value


def _date(value: Any, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise FareCompositionError(f"{label} needs a valid departure date.") from exc


def _iata(value: Any, label: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise FareCompositionError(f"{label} needs a three-letter airport code.")
    return code


def normalize_fare(value: Any) -> SelectedFare:
    """Parse and validate one Escape snapshot, rejecting round trips."""
    try:
        fare = SelectedFare.model_validate(value)
    except ValidationError as exc:
        raise FareCompositionError("That selection is no longer a valid Escape fare.") from exc

    origin = _iata(fare.query.origin, "The fare origin")
    destination = _iata(fare.query.destination, "The fare destination")
    if origin == destination:
        raise FareCompositionError("A fare must travel between two different airports.")
    if fare.query.return_date is not None:
        raise FareCompositionError(
            "Only one-way Escape fares can be composed. Remove the return fare."
        )
    if fare.offer.segments_return:
        raise FareCompositionError(
            "Only one-way Escape fares can be composed. Remove the return fare."
        )
    depart = _date(fare.query.depart_date, "The fare")
    if not math.isfinite(float(fare.offer.price)) or float(fare.offer.price) < 0:
        raise FareCompositionError("The selected fare has an invalid price.")

    def safe_url(raw: str | None) -> str | None:
        if not raw:
            return None
        try:
            parsed = urlparse(str(raw))
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username
            or parsed.password
            or host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith(".localhost")
        ):
            return None
        try:
            return validate_byom_url(str(raw))
        except BYOMUrlError:
            return None

    offer = fare.offer.model_copy(
        update={
            "deep_link": safe_url(fare.offer.deep_link),
            "google_flights_url": safe_url(fare.offer.google_flights_url),
            "booking_url": safe_url(fare.offer.booking_url),
        }
    )
    query = fare.query.model_copy(
        update={"origin": origin, "destination": destination, "depart_date": depart}
    )
    return fare.model_copy(update={"query": query, "offer": offer})


def _leg(fare: SelectedFare) -> PricedLeg:
    query = fare.query
    offer = fare.offer
    return PricedLeg(
        from_iata=query.origin,
        to_iata=query.destination,
        depart_date=query.depart_date,
        adults=query.adults,
        cabin=query.cabin,
        offer=offer,
        google_flights_url=offer.google_flights_url or offer.booking_url,
        booking_url=offer.booking_url or offer.google_flights_url,
    )


def _usable_price(leg: PricedLeg) -> bool:
    return bool(
        leg.offer
        and not leg.offer.fare_missing
        and leg.offer.price is not None
    )


def _same_currency(legs: list[PricedLeg], currency: str) -> bool:
    wanted = currency.upper()
    return all(
        leg.offer and str(leg.offer.currency or "").upper() == wanted
        for leg in legs
    )


def _theme(vibe: str) -> dict[str, str]:
    return vibe_theme(vibe or "adventure")


def _fare_error(message: str) -> FareCompositionError:
    return FareCompositionError(message)


def compose_quest(values: list[Any]) -> tuple[QuestIdea, dict[str, Any]]:
    """Compose an ordered open-jaw Quest from two one-way fares.

    Fare 1 is home → entry and fare 2 is exit → home.  The two middle
    airports must differ and fare 2 must depart after fare 1.
    """
    if len(values) != 2:
        raise _fare_error("Select exactly two one-way fares to build a Quest.")
    fares = [normalize_fare(value) for value in values]
    first, second = fares
    home = first.query.origin
    if second.query.destination != home:
        raise _fare_error(
            f"Quest needs the second fare to return to {home}; "
            f"it returns to {second.query.destination}."
        )
    if first.query.destination == second.query.origin:
        raise _fare_error(
            "Quest needs a different entry and exit airport; choose an open-jaw pair."
        )
    if second.query.depart_date <= first.query.depart_date:
        raise _fare_error("Quest fares must depart in chronological order.")

    vibe = first.vibe or "adventure"
    theme = _theme(vibe)
    inbound = _leg(first)
    outbound = _leg(second)
    currency = (first.query.currency or first.offer.currency or "USD").upper()
    total = (
        round(float(inbound.offer.price) + float(outbound.offer.price), 2)
        if (
            _usable_price(inbound)
            and _usable_price(outbound)
            and _same_currency([inbound, outbound], currency)
        )
        else None
    )
    idea = QuestIdea(
        entry_iata=first.query.destination,
        exit_iata=second.query.origin,
        entry_city=city_for_iata(first.query.destination) or first.query.destination,
        exit_city=city_for_iata(second.query.origin) or second.query.origin,
        overland_narrative=(
            f"Your selected fares make an open-jaw journey: fly into "
            f"{first.query.destination}, travel overland, then fly home from "
            f"{second.query.origin}."
        ),
        highlights=["Built from your selected Escape fares"],
        inbound_leg=inbound,
        outbound_leg=outbound,
        currency=currency,
        depart_date=first.query.depart_date,
        outbound_date=second.query.depart_date,
        total_price=total,
        display_total=format_approx(total, currency)
        if total is not None
        else None,
        inbound_fare_missing=not _usable_price(inbound),
        outbound_fare_missing=not _usable_price(outbound),
        theme_primary=theme["color"],
        theme_accent=theme["deep"],
        theme_label=theme["label"],
        entry_vibe=first.vibe or "adventure",
        exit_vibe=second.vibe or "adventure",
    )
    destination_vibes = [
        {
            "iata": first.query.destination,
            "vibe": first.vibe or "adventure",
        },
        {
            "iata": second.query.origin,
            "vibe": second.vibe or "adventure",
        },
    ]
    meta = {
        "vibe": vibe,
        "prompt": first.prompt or "",
        "trip_prompt": first.prompt or "",
        "origin": home,
        "destination": first.query.destination,
        "currency": idea.currency,
        "composition": "selected-fares",
        "destination_vibes": destination_vibes,
        "manual_flight": any(fare.manual_flight for fare in fares),
    }
    return idea, meta


def _quest_fares(value: Any) -> tuple[SelectedFare, SelectedFare, str, str]:
    try:
        selected = SelectedQuest.model_validate(value)
        idea = QuestIdea.model_validate(selected.idea)
        inbound = idea.inbound_leg
        outbound = idea.outbound_leg
        if not inbound or not outbound or not inbound.offer or not outbound.offer:
            raise _fare_error("This Quest does not contain two usable flight legs.")
        home = _iata(selected.home_iata, "The Quest home airport")
        if inbound.from_iata.upper() != home or outbound.to_iata.upper() != home:
            raise _fare_error("This Quest no longer returns to its starting airport.")
        first = SelectedFare(
            query=SearchQuery(
                origin=inbound.from_iata,
                destination=inbound.to_iata,
                depart_date=_date(inbound.depart_date, "The Quest inbound leg"),
                adults=inbound.adults,
                cabin=inbound.cabin,
                currency=idea.currency,
            ),
            offer=inbound.offer,
            vibe=idea.entry_vibe or selected.vibe,
            prompt=selected.prompt,
        )
        second = SelectedFare(
            query=SearchQuery(
                origin=outbound.from_iata,
                destination=outbound.to_iata,
                depart_date=_date(outbound.depart_date, "The Quest outbound leg"),
                adults=outbound.adults,
                cabin=outbound.cabin,
                currency=idea.currency,
            ).model_copy(
                update={
                    "depart_date": _date(
                        idea.outbound_date or outbound.depart_date,
                        "The Quest outbound leg",
                    )
                }
            ),
            offer=outbound.offer,
            vibe=idea.exit_vibe or selected.vibe,
            prompt=selected.prompt,
        )
        first = normalize_fare(first)
        second = normalize_fare(second)
        if second.query.depart_date <= first.query.depart_date:
            raise _fare_error("Quest fares must depart in chronological order.")
        if second.query.destination != home:
            raise _fare_error("This Quest must end at its starting airport.")
        if first.query.destination == second.query.origin:
            raise _fare_error("This Quest needs distinct entry and exit airports.")
        return first, second, selected.vibe, selected.prompt
    except ValidationError as exc:
        raise _fare_error("That Quest selection is no longer available.") from exc


def compose_detour(values: list[Any]) -> tuple[AdventureItinerary, dict[str, Any]]:
    """Compose a three-flight chronological chain, or Quest + bridge fare."""
    if len(values) == 3 and all(
        not (isinstance(value, dict) and str(value.get("kind") or "").lower() == "quest")
        for value in values
    ):
        fares = [normalize_fare(value) for value in values]
        vibe = fares[1].vibe or "adventure"
        prompt = fares[1].prompt or ""
    elif len(values) == 2 and any(
        isinstance(value, dict) and str(value.get("kind") or "").lower() == "quest"
        for value in values
    ):
        quest_value = next(
            value for value in values
            if isinstance(value, dict) and str(value.get("kind") or "").lower() == "quest"
        )
        escape_value = next(
            value for value in values
            if value is not quest_value
        )
        first, third, quest_vibe, quest_prompt = _quest_fares(quest_value)
        middle = normalize_fare(escape_value)
        if middle.query.origin != first.query.destination:
            raise _fare_error(
                f"Bridge fare must depart from {first.query.destination}."
            )
        if middle.query.destination != third.query.origin:
            raise _fare_error(
                f"Bridge fare must arrive at {third.query.origin}."
            )
        fares = [first, middle, third]
        vibe = middle.vibe or quest_vibe or "adventure"
        prompt = middle.prompt or quest_prompt
    else:
        raise _fare_error(
            "Select three fares in order, or select a Quest and its connecting Escape fare."
        )

    for left, right in zip(fares, fares[1:]):
        if left.query.destination != right.query.origin:
            raise _fare_error(
                f"Route breaks between {left.query.destination} and {right.query.origin}."
            )
        if right.query.depart_date <= left.query.depart_date:
            raise _fare_error("Detour fares must depart in chronological order.")
    if fares[0].query.origin != fares[-1].query.destination:
        raise _fare_error(
            f"Detour must return to {fares[0].query.origin}; "
            f"the last fare ends at {fares[-1].query.destination}."
        )
    stops = [fare.query.destination for fare in fares[:-1]]
    if len(set(stops)) != len(stops) or fares[0].query.origin in stops:
        raise _fare_error("Detour stops must be distinct and different from home.")

    legs = [_leg(fare) for fare in fares]
    currency = (fares[1].query.currency or fares[1].offer.currency or "USD").upper()
    total = (
        round(sum(float(leg.offer.price) for leg in legs), 2)
        if all(_usable_price(leg) for leg in legs) and _same_currency(legs, currency)
        else None
    )
    theme = _theme(vibe)
    it = AdventureItinerary(
        kind="multi-stop",
        title=" → ".join([fares[0].query.origin, *stops, fares[-1].query.destination]),
        total_price=total,
        currency=currency,
        stop_city=city_for_iata(stops[0]) or stops[0],
        stop_iata=stops[0],
        why="A chronological three-leg route built from your selected Escape fares.",
        vibe_tags=[vibe],
        legs=legs,
        notes=[
            "Built from your selected Escape fares",
            "Book each leg separately — confirm on Google before buying",
        ],
        google_flights_url=google_flights_multi(
            [
                (leg.from_iata, leg.to_iata, leg.depart_date)
                for leg in legs
            ],
            currency=currency,
        ),
        booking_url=google_flights_multi(
            [
                (leg.from_iata, leg.to_iata, leg.depart_date)
                for leg in legs
            ],
            currency=currency,
        ),
        theme_label=theme["label"],
        theme_primary=theme["color"],
        theme_accent=theme["deep"],
        theme_gradient=f"linear-gradient(165deg, {theme['deep']} 0%, {theme['color']} 100%)",
        stops=[
            {
                "iata": code,
                "city": city_for_iata(code) or code,
                "stay_days": None,
                "vibe": fares[index].vibe or "adventure",
            }
            for index, code in enumerate(stops)
        ],
    )
    meta = {
        "vibe": vibe,
        "prompt": prompt,
        "trip_prompt": prompt,
        "origin": fares[0].query.origin,
        "destination": fares[-1].query.destination,
        "currency": currency,
        "composition": "selected-fares",
        "destination_vibes": [
            {
                "iata": fare.query.destination,
                "vibe": fare.vibe or "adventure",
            }
            for fare in fares[:-1]
        ],
        "manual_flight": any(fare.manual_flight for fare in fares),
    }
    return it, meta


def compose_selected(values: list[Any], kind: str) -> tuple[Any, dict[str, Any]]:
    """Single web-facing boundary for selected-fare composition."""
    requested = (kind or "").strip().lower()
    if requested == "quest":
        return compose_quest(values)
    if requested == "detour":
        return compose_detour(values)
    raise FareCompositionError("Choose Quest or Detour before building.")