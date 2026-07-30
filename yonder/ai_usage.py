"""AI token usage tracking — log to SQLite, format for display."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

DB_PATH = Path("data/ai_usage.db")


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


# ── SQLite persistence ────────────────────────────────────────────────────────

def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts               TEXT    NOT NULL,
            route            TEXT    NOT NULL,
            model            TEXT,
            prompt_tokens    INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens     INTEGER DEFAULT 0,
            est_cost_usd     REAL    DEFAULT 0,
            calls            INTEGER DEFAULT 1,
            model_source     TEXT
        )
    """)
    # Older DBs predate model_source — add it in place (nullable, legacy rows stay NULL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_usage)").fetchall()}
    if "model_source" not in cols:
        conn.execute("ALTER TABLE ai_usage ADD COLUMN model_source TEXT")
    conn.commit()
    return conn


def _log_sync(route: str, usage: dict) -> None:
    if not usage or not usage.get("total_tokens"):
        return
    try:
        conn = _ensure_db()
        conn.execute(
            "INSERT INTO ai_usage "
            "(ts, route, model, prompt_tokens, completion_tokens, total_tokens, est_cost_usd, calls, model_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        conn.close()
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
    if not DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(DB_PATH)
        if days is not None:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_tokens), 0),
                    COALESCE(SUM(prompt_tokens), 0),
                    COALESCE(SUM(completion_tokens), 0),
                    COALESCE(SUM(est_cost_usd), 0.0),
                    COALESCE(SUM(calls), 0)
                FROM ai_usage
                WHERE ts >= datetime('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_tokens), 0),
                    COALESCE(SUM(prompt_tokens), 0),
                    COALESCE(SUM(completion_tokens), 0),
                    COALESCE(SUM(est_cost_usd), 0.0),
                    COALESCE(SUM(calls), 0)
                FROM ai_usage
                """,
            ).fetchone()
        conn.close()
        total_tokens, prompt_tokens, completion_tokens, est_cost_usd, calls = row
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
