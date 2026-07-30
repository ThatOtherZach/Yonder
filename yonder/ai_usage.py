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
    if merged and merged.get("total_tokens"):
        merged["est_cost_usd"] = estimate_cost(
            merged.get("prompt_tokens", 0),
            merged.get("completion_tokens", 0),
            merged.get("model", ""),
        )
    return merged


def fmt_usage(usage: dict) -> str:
    """Human-readable pill text: '~3.2k tok · $0.0045'"""
    total = usage.get("total_tokens", 0)
    if not total:
        return ""
    cost = usage.get("est_cost_usd", 0.0)
    tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
    return f"~{tok_str} tok · ${cost:.4f}"


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
            calls            INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def _log_sync(route: str, usage: dict) -> None:
    if not usage or not usage.get("total_tokens"):
        return
    try:
        conn = _ensure_db()
        conn.execute(
            "INSERT INTO ai_usage "
            "(ts, route, model, prompt_tokens, completion_tokens, total_tokens, est_cost_usd, calls) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                route,
                usage.get("model", ""),
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
                int(usage.get("total_tokens", 0)),
                float(usage.get("est_cost_usd", 0.0)),
                int(usage.get("calls", 1)),
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
