"""Health-check for the activity catalog (activities.csv).

Samples or fully scans partner URLs (GetYourGuide / Viator) and flags
dead links — 404 / 410 responses or redirects that land on a non-tour
page (partner removed the listing and bounced to their homepage/search).

Usage (from CLI):
    yonder check-activities               # random 30-URL sample
    yonder check-activities --full        # all ~462 URLs
    yonder check-activities --sample 60   # random N URLs
    yonder check-activities --threshold 5 # fail when ≥N% broken (default 5)

Concurrency is capped at 8 parallel requests to avoid rate-limiting.
A browser-like User-Agent is sent so partners don't block the probe.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Sequence

import httpx

from yonder.activities import CSV_PATH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (compatible; YonderBot/1.0; +https://yonder.travel/healthcheck)"
)
_TIMEOUT = 12.0          # seconds per request
_CONCURRENCY = 8         # max parallel requests
_DEFAULT_SAMPLE = 30     # URLs checked when --full not given
_DEFAULT_THRESHOLD = 5   # % broken before exit(1)

# Patterns that signal a redirect landed on a search/home page rather than
# the original tour listing — treat these as soft-404s.
_DEAD_REDIRECT_PATTERNS = [
    # GYG: bounces to search results or city landing page
    re.compile(r"getyourguide\.com/[a-z-]+-l\d+/?$", re.I),
    re.compile(r"getyourguide\.com/?$", re.I),
    # Viator: bounces to home or search
    re.compile(r"viator\.com/?$", re.I),
    re.compile(r"viator\.com/search", re.I),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UrlResult:
    url: str
    city: str
    title: str
    provider: str
    status: int | None        # HTTP status of final response (None = error)
    final_url: str | None     # URL after redirects
    elapsed_ms: float
    error: str | None = None  # network/timeout error message

    @property
    def is_dead(self) -> bool:
        if self.error:
            return False      # network errors are inconclusive, not "dead"
        if self.status is None:
            return False
        if self.status in (404, 410):
            return True
        # Redirect to a non-tour page = soft 404
        if self.final_url and any(p.search(self.final_url) for p in _DEAD_REDIRECT_PATTERNS):
            return True
        return False

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def label(self) -> str:
        if self.is_dead:
            return "DEAD"
        if self.is_error:
            return "ERR"
        return "ok"


@dataclass
class HealthReport:
    total: int
    results: list[UrlResult] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def dead(self) -> list[UrlResult]:
        return [r for r in self.results if r.is_dead]

    @property
    def errors(self) -> list[UrlResult]:
        return [r for r in self.results if r.is_error]

    @property
    def ok(self) -> list[UrlResult]:
        return [r for r in self.results if r.label == "ok"]

    @property
    def dead_pct(self) -> float:
        checked = len(self.results)
        if not checked:
            return 0.0
        return 100.0 * len(self.dead) / checked


# ---------------------------------------------------------------------------
# CSV loader (independent of the mtime-cache in activities.py)
# ---------------------------------------------------------------------------

class CatalogLoadError(RuntimeError):
    """Raised when activities.csv cannot be opened or parsed."""


def _load_all_rows() -> list[dict]:
    """Return every valid catalog row as a plain dict.

    Raises:
        CatalogLoadError: if the CSV file is missing, unreadable, or
            produces zero parseable rows (which would make every health
            check report a false-clean result).
    """
    import csv

    if not CSV_PATH.exists():
        raise CatalogLoadError(f"Catalog not found: {CSV_PATH}")

    rows: list[dict] = []
    try:
        with CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                url = (row.get("URL") or "").strip()
                if not url.lower().startswith("https://"):
                    continue
                rows.append(
                    {
                        "url": url,
                        "city": (row.get("CITY") or "").strip(),
                        "title": (row.get("SHORTTITLE") or "").strip(),
                        "provider": (
                            "getyourguide"
                            if "getyourguide.com" in url.lower()
                            else "viator"
                        ),
                    }
                )
    except CatalogLoadError:
        raise
    except Exception as exc:
        raise CatalogLoadError(f"Failed to read catalog: {exc}") from exc

    if not rows:
        raise CatalogLoadError(
            f"Catalog at {CSV_PATH} parsed with zero valid rows — "
            "check the file format (expected: CITY,IATA,URL,GROKVIBE,ACTIVITYEMOJI,SHORTTITLE)."
        )
    return rows


# ---------------------------------------------------------------------------
# Async probe
# ---------------------------------------------------------------------------

async def _probe(client: httpx.AsyncClient, row: dict, sem: asyncio.Semaphore) -> UrlResult:
    """HEAD-request one URL and return a UrlResult."""
    url = row["url"]
    t0 = time.monotonic()
    async with sem:
        try:
            resp = await client.head(
                url,
                follow_redirects=True,
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            elapsed = (time.monotonic() - t0) * 1000
            return UrlResult(
                url=url,
                city=row["city"],
                title=row["title"],
                provider=row["provider"],
                status=resp.status_code,
                final_url=str(resp.url),
                elapsed_ms=elapsed,
            )
        except httpx.TimeoutException:
            return UrlResult(
                url=url,
                city=row["city"],
                title=row["title"],
                provider=row["provider"],
                status=None,
                final_url=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error="timeout",
            )
        except Exception as exc:
            return UrlResult(
                url=url,
                city=row["city"],
                title=row["title"],
                provider=row["provider"],
                status=None,
                final_url=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error=str(exc)[:80],
            )


async def _run_checks(rows: list[dict]) -> list[UrlResult]:
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        tasks = [_probe(client, row, sem) for row in rows]
        return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_catalog(
    *,
    sample: int | None = _DEFAULT_SAMPLE,
    full: bool = False,
    seed: int | None = None,
) -> HealthReport:
    """Run the health check and return a HealthReport.

    Args:
        sample: number of URLs to test (ignored when *full* is True).
        full:   check every URL in the CSV.
        seed:   RNG seed for reproducible samples (None → random).
    """
    all_rows = _load_all_rows()
    total = len(all_rows)
    if full or sample is None or sample >= total:
        rows_to_check = all_rows
    else:
        rng = random.Random(seed)
        rows_to_check = rng.sample(all_rows, min(sample, total))

    t0 = time.monotonic()
    results = asyncio.run(_run_checks(rows_to_check))
    elapsed = time.monotonic() - t0

    return HealthReport(total=total, results=list(results), elapsed_s=elapsed)
