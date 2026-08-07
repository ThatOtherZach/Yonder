"""ChatGPT Ads pipeline — candidate pool + hourly push to OpenAI Ads API.

Two entry points called by the application:
- ``upsert_candidate_from_save(saved, *, base_url)``  — called on every trip save.
- ``poll_and_push()``                                 — called after every hourly
  vibe-signal recompute (triggered inside ``vibe_signals.recompute_scores``).

Nothing is ever pushed with ``status: "active"``; that guardrail lives in
``ads_api.AdsApiClient.create_ad`` and is unconditional.

Without ``OPENAI_ADS_API_KEY`` in the environment the push step is skipped
silently (a warning is logged).
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from yonder.db import get_conn

if TYPE_CHECKING:
    from yonder.saved import SavedItinerary

log = logging.getLogger(__name__)

# Minimum save-count before we push a candidate to the Ads API.
# Default 1; raise this config constant to be more selective.
MIN_SAVES_THRESHOLD: int = 1

# Hard rolling 24-hour push cap.
DAILY_CAP: int = 10

# How many top dest_vibe_scores rows to scan per hourly poll for new candidates.
TOP_N_SCAN: int = 200

# Base retry window in seconds for failed candidates (default 6 hours).
# Subsequent failures use exponential backoff: base * 2^(fail_count-1).
RETRY_WINDOW_SECONDS: int = 6 * 3600

# Cap on the retry backoff to avoid waiting forever (default 7 days).
MAX_RETRY_WINDOW_SECONDS: int = 7 * 24 * 3600


# ── Copy-template helpers ────────────────────────────────────────────────────


def _vibe_emoji(vibe_id: str) -> str:
    try:
        from yonder.vibe_theme import VIBE_EMOJI

        return VIBE_EMOJI.get((vibe_id or "").strip().lower(), "✈️")
    except Exception:
        return "✈️"


def _city_for_iata(iata: str) -> str:
    try:
        from yonder.countries import city_for_iata

        return city_for_iata(iata) or iata
    except Exception:
        return iata


def render_title(vibe: str, city: str, price: str | None) -> str:
    """Build the ad title (3–50 chars).

    ``"{emoji} Escape to {city} — From {price}"`` with price, else
    ``"Escape to {city}"``.
    """
    emoji = _vibe_emoji(vibe)
    if price:
        raw = f"{emoji} Escape to {city} — From {price}"
    else:
        raw = f"Escape to {city}"
    raw = raw[:50]
    if len(raw) < 3:
        raw = f"Escape to {city}"[:50]
    return raw


def render_body(
    save_count: int,
    search_count: int,
    stay_days: int | None,
) -> str:
    """Build the ad body (≤ 100 chars).

    Uses search_count when save_count < 3.  Omits the stay clause when no data.
    """
    if save_count >= 3:
        count_phrase = f"{save_count} travelers saved it this month."
    else:
        count_phrase = f"{search_count} searches this month."

    if stay_days:
        raw = f"{count_phrase} {stay_days} nights, all-in. See what's waiting."
    else:
        raw = f"{count_phrase} See what's waiting."
    return raw[:100]


# ── Share URL helper ─────────────────────────────────────────────────────────


def _app_base_url() -> str:
    """Trusted absolute base URL derived solely from platform environment variables.

    Reads ``REPLIT_DOMAINS`` (comma-separated list of public domains set by the
    Replit platform) and falls back to ``REPLIT_DEV_DOMAIN``.  These are set by
    the platform and **cannot be influenced by client request headers**.

    Returns an empty string when no trusted domain is configured — callers must
    treat an empty return as "no usable origin".

    Validation: must resolve to an https:// URL with a non-empty hostname and no
    userinfo component; anything else is rejected and the empty string returned.
    """
    from urllib.parse import urlparse

    domains = (
        os.environ.get("REPLIT_DOMAINS") or os.environ.get("REPLIT_DEV_DOMAIN") or ""
    ).strip()
    domain = domains.split(",")[0].strip()
    if not domain:
        return ""
    url = f"https://{domain}"
    try:
        p = urlparse(url)
        if p.scheme != "https" or p.username or not p.hostname:
            log.warning("ad_pipeline: untrusted REPLIT_DOMAINS value rejected: %s", domain)
            return ""
    except Exception:
        return ""
    return url


def _make_share_url(saved: "SavedItinerary") -> str:
    """Create a share entry for a saved trip and return its absolute URL.

    The base URL is always derived from ``_app_base_url()`` (trusted env vars).
    Never accepts a caller-supplied host to prevent Host-header injection.

    Falls back to the app root path when share creation fails or no trusted
    origin is configured.
    """
    base = _app_base_url()
    try:
        from yonder.share import create_share, dump_obj

        kind = (saved.kind or "detour").lower()
        title = saved.title or "Trip"
        payload: dict[str, Any] = {
            "itinerary": dump_obj(saved.itinerary),
            "trip_meta": dump_obj(saved.trip_meta),
        }
        trip = create_share(kind=kind, title=title, payload=payload)
        return f"{base}{trip.path}" if base else trip.path
    except Exception:
        log.debug("ad_pipeline: share URL generation failed", exc_info=True)
        return f"{base}/" if base else "/"


# ── DB helpers ───────────────────────────────────────────────────────────────


def _upsert_row(
    *,
    dest_iata: str,
    vibe: str,
    city_name: str,
    ad_title: str,
    ad_body: str,
    landing_url: str,
    save_count: int,
    search_count: int,
    signal_score: float,
) -> None:
    """Insert or update an ad_candidates row.

    Preserves push_state / pushed_at / ads_api_ad_id on conflict so a
    re-save of an already-pushed destination doesn't reset it to pending.
    """
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ad_candidates (
                dest_iata, vibe, city_name, ad_title, ad_body, landing_url,
                save_count, search_count, signal_score, push_state, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            ON CONFLICT (dest_iata, vibe) DO UPDATE SET
                city_name    = EXCLUDED.city_name,
                ad_title     = EXCLUDED.ad_title,
                ad_body      = EXCLUDED.ad_body,
                landing_url  = EXCLUDED.landing_url,
                save_count   = EXCLUDED.save_count,
                search_count = EXCLUDED.search_count,
                signal_score = EXCLUDED.signal_score,
                updated_at   = EXCLUDED.updated_at
            """,
            (
                dest_iata, vibe, city_name, ad_title, ad_body, landing_url,
                save_count, search_count, signal_score, now,
            ),
        )
        conn.commit()


def _daily_pushed_count() -> int:
    """Count ads pushed in the last 24-hour rolling window."""
    cutoff = time.time() - 86400.0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ad_candidates WHERE pushed_at >= %s",
            (cutoff,),
        ).fetchone()
    return int(row["n"]) if row else 0


def retry_failed_candidates() -> int:
    """Reset push_state to 'pending' for failed candidates whose backoff window has elapsed.

    Each failure increments ``fail_count``; the retry window doubles per failure
    (exponential backoff capped at ``MAX_RETRY_WINDOW_SECONDS``):

        retry_window = min(RETRY_WINDOW_SECONDS * 2^(fail_count - 1), MAX_RETRY_WINDOW_SECONDS)

    Only rows whose ``updated_at`` is older than their individual retry window are
    reset — genuinely broken candidates back off progressively and never monopolise
    the daily cap.

    Returns the number of rows reset to pending.
    """
    now = time.time()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT dest_iata, vibe, fail_count, failed_at, updated_at "
                "FROM ad_candidates WHERE push_state = 'failed'"
            ).fetchall()

        to_retry: list[tuple[str, str]] = []
        for r in rows:
            fail_count = int(r["fail_count"] or 0)
            # Backoff anchored to failed_at (never updated by ordinary upserts).
            # window = base * 2^(fail_count-1), minimum base, capped at MAX.
            exponent = max(0, fail_count - 1)
            window = min(
                RETRY_WINDOW_SECONDS * (2 ** exponent),
                MAX_RETRY_WINDOW_SECONDS,
            )
            # Use failed_at as the anchor; fall back to updated_at for legacy rows
            # where failed_at was not yet recorded (pre-migration failures).
            # This ensures historical failed rows can always recover.
            anchor = r["failed_at"] or r["updated_at"]
            if anchor is None:
                # No timestamp at all — use a safe epoch so the row retries immediately.
                anchor = 0.0
            elapsed = now - float(anchor)
            if elapsed >= window:
                to_retry.append((r["dest_iata"], r["vibe"]))

        if not to_retry:
            return 0

        with get_conn() as conn:
            for dest_iata, vibe in to_retry:
                conn.execute(
                    "UPDATE ad_candidates SET push_state = 'pending', updated_at = %s "
                    "WHERE dest_iata = %s AND vibe = %s",
                    (now, dest_iata, vibe),
                )
            conn.commit()

        log.info(
            "ad_pipeline: retry_failed_candidates reset %d row(s) to pending",
            len(to_retry),
        )
        return len(to_retry)
    except Exception:
        log.exception("ad_pipeline: retry_failed_candidates failed")
        return 0


# ── Public entry points ──────────────────────────────────────────────────────


def upsert_candidate_from_save(saved: "SavedItinerary") -> None:
    """Upsert an ad candidate immediately after a trip save (sync, lightweight).

    ``saved`` — the ``SavedItinerary`` returned by ``save_itinerary()``.

    The landing URL is derived solely from the platform-managed ``REPLIT_DOMAINS``
    / ``REPLIT_DEV_DOMAIN`` environment variables via ``_app_base_url()``.
    The HTTP request's Host header is deliberately never consulted here — it could
    be forged, and we must not persist attacker-controlled URLs into ad drafts.
    """
    try:
        dest_iata = (saved.stop_iata or saved.destination or "").upper()
        vibe = (saved.vibe or "adventure").lower()
        if not dest_iata or len(dest_iata) != 3 or not dest_iata.isalpha():
            return

        city = saved.stop_city or _city_for_iata(dest_iata)
        price = saved.display_price or saved.all_in_display

        # Fetch live save/search counts from dest_vibe_scores if available.
        with get_conn() as conn:
            row = conn.execute(
                "SELECT save_count, search_count, score "
                "FROM dest_vibe_scores "
                "WHERE dest_iata = %s AND vibe = %s",
                (dest_iata, vibe),
            ).fetchone()
        save_count = int(row["save_count"]) if row else 1
        search_count = int(row["search_count"]) if row else 1
        score = float(row["score"]) if row else 1.0

        landing_url = _make_share_url(saved)
        title = render_title(vibe, city, price)
        body = render_body(save_count, search_count, saved.stay_days)

        _upsert_row(
            dest_iata=dest_iata,
            vibe=vibe,
            city_name=city,
            ad_title=title,
            ad_body=body,
            landing_url=landing_url,
            save_count=save_count,
            search_count=search_count,
            signal_score=score,
        )
    except Exception:
        log.exception("ad_pipeline: upsert_candidate_from_save failed")


def upsert_candidates_from_scores() -> int:
    """Scan dest_vibe_scores for trending pairs and upsert missing candidates.

    Only inserts rows that don't already exist in ad_candidates (no
    overwrite of existing candidates — those are managed by save-triggered
    upserts which have richer data).

    Returns the number of new rows inserted.
    """
    base_url = _app_base_url()
    count = 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT dvs.dest_iata, dvs.vibe,
                       dvs.score, dvs.save_count, dvs.search_count
                FROM dest_vibe_scores dvs
                LEFT JOIN ad_candidates ac
                    ON ac.dest_iata = dvs.dest_iata AND ac.vibe = dvs.vibe
                WHERE ac.dest_iata IS NULL
                ORDER BY dvs.score DESC
                LIMIT %s
                """,
                (TOP_N_SCAN,),
            ).fetchall()

        for r in rows:
            dest_iata = r["dest_iata"]
            vibe = r["vibe"] or "adventure"
            save_count = int(r["save_count"] or 0)
            search_count = int(r["search_count"] or 0)
            score = float(r["score"] or 0.0)
            city = _city_for_iata(dest_iata)
            # No saved trip yet — land on app root; updated by first save
            landing_url = (base_url.rstrip("/") + "/") if base_url else "/"
            title = render_title(vibe, city, None)
            body = render_body(save_count, search_count, None)
            _upsert_row(
                dest_iata=dest_iata,
                vibe=vibe,
                city_name=city,
                ad_title=title,
                ad_body=body,
                landing_url=landing_url,
                save_count=save_count,
                search_count=search_count,
                signal_score=score,
            )
            count += 1
    except Exception:
        log.exception("ad_pipeline: upsert_candidates_from_scores failed")
    return count


def run_push_cycle() -> int:
    """Push qualifying candidates to the ChatGPT Ads API.

    - Checks the rolling 24h cap and skips if already at limit.
    - Pushes in descending signal-score order so top destinations get priority.
    - Records the returned ad ID and pushed_at timestamp per candidate.
    - Marks failed pushes as ``push_state = 'failed'`` (they won't retry until
      a future save or poll updates them back to ``pending``).

    Returns the number of ads pushed in this cycle.
    """
    api_key = (os.environ.get("OPENAI_ADS_API_KEY") or "").strip()
    if not api_key:
        log.warning(
            "ad_pipeline: OPENAI_ADS_API_KEY not set — skipping push cycle"
        )
        return 0

    pushed_today = _daily_pushed_count()
    remaining = DAILY_CAP - pushed_today
    if remaining <= 0:
        log.info(
            "ad_pipeline: daily cap (%d) reached — skipping push cycle", DAILY_CAP
        )
        return 0

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT dest_iata, vibe, city_name, ad_title, ad_body,
                   landing_url, save_count, search_count, signal_score
            FROM ad_candidates
            WHERE push_state = 'pending'
              AND save_count >= %s
            ORDER BY signal_score DESC, save_count DESC
            LIMIT %s
            """,
            (MIN_SAVES_THRESHOLD, remaining),
        ).fetchall()

    if not rows:
        return 0

    try:
        from yonder.ads_api import AdsApiClient

        client = AdsApiClient(api_key=api_key)
        campaign_id = client.ensure_campaign()
        ad_group_id = client.ensure_ad_group(campaign_id=campaign_id)
        file_id = client.ensure_brand_image()
    except Exception:
        log.exception("ad_pipeline: Ads API setup failed")
        return 0

    pushed = 0
    for r in rows:
        try:
            ad_id = client.create_ad(
                ad_group_id=ad_group_id,
                title=r["ad_title"],
                body=r["ad_body"],
                landing_url=r["landing_url"],
                file_id=file_id,
            )
            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE ad_candidates
                    SET push_state = 'pushed',
                        pushed_at  = %s,
                        ads_api_ad_id = %s
                    WHERE dest_iata = %s AND vibe = %s
                    """,
                    (time.time(), ad_id, r["dest_iata"], r["vibe"]),
                )
                conn.commit()
            log.info(
                "ad_pipeline: pushed ad %s for %s/%s",
                ad_id, r["dest_iata"], r["vibe"],
            )
            pushed += 1
        except Exception:
            log.exception(
                "ad_pipeline: failed to push %s/%s", r["dest_iata"], r["vibe"]
            )
            try:
                now_fail = time.time()
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE ad_candidates
                        SET push_state = 'failed',
                            fail_count  = COALESCE(fail_count, 0) + 1,
                            failed_at   = %s,
                            updated_at  = %s
                        WHERE dest_iata = %s AND vibe = %s
                        """,
                        (now_fail, now_fail, r["dest_iata"], r["vibe"]),
                    )
                    conn.commit()
            except Exception:
                pass

    return pushed


def poll_and_push() -> None:
    """Full hourly pipeline: retry stale failures, upsert trending candidates, then push.

    Called automatically inside ``vibe_signals.recompute_scores()`` after each
    successful hourly recompute.  Safe to call directly in tests.

    Step order:
    1. ``retry_failed_candidates()`` — reset failed rows whose backoff window has
       elapsed back to ``pending`` so they re-enter the queue this cycle.
    2. ``upsert_candidates_from_scores()`` — add any new trending pairs.
    3. ``run_push_cycle()`` — push qualifying pending rows up to the daily cap.
    """
    retry_failed_candidates()
    upsert_candidates_from_scores()
    run_push_cycle()
