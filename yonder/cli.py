from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from yonder.config import get_settings
from yonder.engine import search_flights
from yonder.history import count_samples, export_jsonl, recent_samples, route_stats
from yonder.money import format_approx
from yonder.types import CabinClass, SearchQuery

app = typer.Typer(
    name="yonder",
    help="Yonder — personal travel planner. Multi-provider fares, adventures, saved itineraries.",
    add_completion=False,
)
console = Console()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


@app.command("history")
def history_cmd(
    origin: Optional[str] = typer.Argument(None, help="IATA origin e.g. YYZ"),
    destination: Optional[str] = typer.Argument(None, help="IATA dest e.g. YVR"),
    export: bool = typer.Option(False, "--export", help="Write price_history_export.jsonl"),
) -> None:
    """Show local price journal stats (every search is saved)."""
    if export:
        path = export_jsonl()
        console.print(f"Exported {count_samples()} samples → {path}")
        return
    total = count_samples()
    console.print(f"Total samples in journal: [bold]{total}[/]")
    if origin and destination:
        st = route_stats(origin.upper(), destination.upper())
        console.print(
            f"{st.origin}→{st.destination}: n={st.n} "
            f"min={format_approx(st.min_price, st.currency)} "
            f"median={format_approx(st.median, st.currency)} "
            f"max={format_approx(st.max_price, st.currency)}"
        )
    else:
        table = Table(title="Recent samples")
        table.add_column("When")
        table.add_column("Route")
        table.add_column("Price")
        table.add_column("Source")
        for r in recent_samples(15):
            table.add_row(
                str(r.get("observed_at", ""))[:19],
                f"{r['origin']}→{r['destination']}",
                format_approx(r["price"], r["currency"]),
                f"{r['source']} ({r.get('price_kind') or '?'})",
            )
        console.print(table)


@app.command("providers")
def list_providers() -> None:
    """Show which providers are configured from .env."""
    s = get_settings()
    configured = set(s.configured_providers())
    all_names = [
        ("amadeus", "GDS-style offers (needs key)"),
        ("travelpayouts", "Cached market (needs token — skipped if empty)"),
        ("duffel", "Live or sandbox depending on token"),
        ("serpapi_google_flights", "Google Flights scrape (needs key)"),
        ("aviationstack", "Airport enrichment only"),
        ("mock", "Demo data"),
    ]
    table = Table(title="Flight providers")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Notes")
    for name, notes in all_names:
        if name == "mock":
            status = "[dim]demo[/]"
        elif name in configured:
            status = "[green]ready[/]"
        else:
            status = "[yellow]no keys[/]"
        table.add_row(name, status, notes)
    console.print(table)
    if not configured:
        console.print(
            "\n[yellow]No live providers configured.[/] Copy .env.example → .env and add keys.\n"
            "Or run: [bold]yonder search YVR NRT 2026-09-15 --mock[/]"
        )


@app.command("search")
def search(
    origin: str = typer.Argument(..., help="Origin IATA, e.g. YVR"),
    destination: str = typer.Argument(..., help="Destination IATA, e.g. NRT"),
    depart: str = typer.Argument(..., help="Depart date YYYY-MM-DD"),
    return_date: Optional[str] = typer.Option(
        None, "--return", "-r", help="Return date YYYY-MM-DD"
    ),
    adults: int = typer.Option(1, "--adults", "-a", min=1, max=9),
    cabin: CabinClass = typer.Option(CabinClass.ECONOMY, "--cabin", "-c"),
    currency: str = typer.Option("USD", "--currency"),
    max_results: int = typer.Option(15, "--max", "-n", min=1, max=100),
    nonstop: bool = typer.Option(False, "--nonstop"),
    mock: bool = typer.Option(False, "--mock", help="Include mock demo provider"),
    only: Optional[str] = typer.Option(
        None, "--only", help="Comma-separated providers, e.g. amadeus,travelpayouts"
    ),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write full result JSON"),
) -> None:
    """Search all configured providers in parallel and print cheapest first."""
    only_list = [p.strip() for p in only.split(",")] if only else None
    # If nothing configured and user didn't ask for mock, enable mock automatically
    settings = get_settings()
    include_mock = mock
    if not settings.configured_providers() and not only_list:
        include_mock = True
        console.print("[dim]No API keys found — using mock provider. Add keys to .env for live data.[/]\n")

    query = SearchQuery(
        origin=origin.upper(),
        destination=destination.upper(),
        depart_date=_parse_date(depart),
        return_date=_parse_date(return_date) if return_date else None,
        adults=adults,
        cabin=cabin,
        currency=currency.upper(),
        max_results=max_results,
        nonstop_only=nonstop,
    )

    with console.status("Scanning providers…"):
        result = asyncio.run(
            search_flights(
                query,
                settings=settings,
                include_mock=include_mock,
                only=only_list,
            )
        )

    # Provider status strip
    status_bits = []
    for r in result.results:
        if r.ok:
            status_bits.append(f"[green]{r.provider}[/] {r.latency_ms}ms ({len(r.offers)})")
        else:
            status_bits.append(f"[red]{r.provider}[/] {r.error}")
    console.print(Panel("\n".join(status_bits) or "No providers ran", title="Providers"))

    if not result.offers:
        console.print("[yellow]No offers returned.[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"{query.origin} → {query.destination}  {query.depart_date}"
                  + (f" / {query.return_date}" if query.return_date else ""))
    table.add_column("#", justify="right")
    table.add_column("Price", justify="right", style="bold")
    table.add_column("Provider")
    table.add_column("Airlines")
    table.add_column("Stops", justify="center")
    table.add_column("Duration")
    table.add_column("Notes")

    for i, o in enumerate(result.offers[:max_results], 1):
        dur = ""
        if o.duration_out_minutes is not None:
            h, m = divmod(o.duration_out_minutes, 60)
            dur = f"{h}h{m:02d}m"
        table.add_row(
            str(i),
            f"{o.currency} {o.price:,.2f}",
            o.provider,
            ",".join(o.airlines) or "—",
            str(o.stops_out),
            dur or "—",
            (o.notes or "")[:40],
        )

    console.print(table)
    cheapest = result.offers[0]
    console.print(
        f"\nCheapest: [bold green]{cheapest.currency} {cheapest.price:,.2f}[/] "
        f"via [bold]{cheapest.provider}[/] ({','.join(cheapest.airlines) or '—'})"
    )

    if json_out:
        json_out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")


@app.command("purge-field-notes")
def purge_field_notes_cmd() -> None:
    """Delete cached field notes that lack a tagline (old era_note/vibe format).

    Stale entries are already skipped at read-time, but running this command
    removes them from disk so the next request fetches fresh prose from Grok.
    """
    from yonder.encyclopedia import purge_legacy_field_notes

    with console.status("Scanning field-note cache…"):
        deleted = purge_legacy_field_notes()

    if deleted:
        console.print(
            f"[green]Purged {deleted} legacy field note(s).[/] "
            "They will be re-fetched with the new tagline format on next use."
        )
    else:
        console.print("[dim]No legacy field notes found — cache is already up to date.[/]")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """Start local web UI + JSON API (personal use, localhost only)."""
    import uvicorn

    console.print(f"Open http://{host}:{port}")
    uvicorn.run("yonder.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
