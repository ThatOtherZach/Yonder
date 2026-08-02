"""AI token usage tracking — log to SQLite, format for display."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ── Pricing (USD per 1M tokens) ──────────────────────────────────────────────
# Update when xAI adjusts rates: https://console.x.ai/
_PRICING: dict[str, tuple[float, float]] = {
    "grok-4.5":      (3.00, 15.00),
    "grok-4":        (3.00, 15.00),
    "grok-3-mini":   (0.30,  0.50),
    "grok-3":        (3.00, 15.00),
}
_DEFAULT_PRICING = (3.00, 15.00)  # conservative fallback


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str = "") -> float:
    input_rate, output_rate = _PRICING.get(model, _DEFAULT_PRICING)
    return round(
        (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000,
        6,
    )


def merge_usage(*usages: dict) -> dict:
    """Sum token counts across multiple GrokClient instances / calls."""
    merged: dict = {}
    for u in usages:
        if not u:
            continue
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            merged[k] = merged.get(k, 0) + u.get(k, 0)
        if not merged.get("model"):
            merged["model"] = u.get("model", "")
        if not merged.get("model_source"):
            merged["model_source"] = u.get("model_source", "")
    if merged and merged.get("total_tokens"):
        merged["est_cost_usd"] = estimate_cost(
            merged.get("prompt_tokens", 0),
            merged.get("completion_tokens", 0),
            merged.get("model", ""),
        )
    return merged


def fmt_usage(usage: dict) -> str:
    """Human-readable pill text: '~3.2k tok · $0.0045 · 2 AI calls'"""
    total = usage.get("total_tokens", 0)
    if not total:
        return ""
    cost = usage.get("est_cost_usd", 0.0)
    tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
    calls = int(usage.get("calls", 0) or 0)
    call_txt = f" · {calls} AI call{'s' if calls != 1 else ''}" if calls else ""
    return f"~{tok_str} tok · ${cost:.4f}{call_txt}"


# ── PostgreSQL persistence ────────────────────────────────────────────────────

def _log_sync(route: str, usage: dict) -> None:
    if not usage or not usage.get("total_tokens"):
        return
    try:
        from yonder.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ai_usage "
                "(ts, route, model, prompt_tokens, completion_tokens, total_tokens, est_cost_usd, calls, model_source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    route,
                    usage.get("model", ""),
                    int(usage.get("prompt_tokens", 0)),
                    int(usage.get("completion_tokens", 0)),
                    int(usage.get("total_tokens", 0)),
                    float(usage.get("est_cost_usd", 0.0)),
                    int(usage.get("calls", 1)),
                    usage.get("model_source") or None,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_usage log failed: %s", exc)


async def log_usage(route: str, usage: dict) -> None:
    """Fire-and-forget async wrapper — never blocks the response."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _log_sync, route, usage)


def summarise(days: int | None = None) -> dict:
    """Return aggregated usage totals for the given window (or all-time if days is None).

    Returns a dict with keys:
        total_tokens, prompt_tokens, completion_tokens, est_cost_usd, calls
    Returns an empty dict (not an error) when the DB doesn't exist yet.
    """
    try:
        from yonder.db import get_conn

        with get_conn() as conn:
            if days is not None:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=int(days))
                ).isoformat()
                row = conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(total_tokens), 0) AS a,
                        COALESCE(SUM(prompt_tokens), 0) AS b,
                        COALESCE(SUM(completion_tokens), 0) AS c,
                        COALESCE(SUM(est_cost_usd), 0.0) AS d,
                        COALESCE(SUM(calls), 0) AS e
                    FROM ai_usage
                    WHERE ts >= %s
                    """,
                    (cutoff,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(total_tokens), 0) AS a,
                        COALESCE(SUM(prompt_tokens), 0) AS b,
                        COALESCE(SUM(completion_tokens), 0) AS c,
                        COALESCE(SUM(est_cost_usd), 0.0) AS d,
                        COALESCE(SUM(calls), 0) AS e
                    FROM ai_usage
                    """,
                ).fetchone()
        total_tokens, prompt_tokens, completion_tokens, est_cost_usd, calls = (
            row["a"], row["b"], row["c"], row["d"], row["e"]
        )
        return {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "est_cost_usd": round(float(est_cost_usd), 6),
            "calls": calls,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_usage summarise failed: %s", exc)
        return {}
