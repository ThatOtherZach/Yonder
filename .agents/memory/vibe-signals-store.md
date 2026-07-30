---
name: Vibe signal store semantics
description: Rules for the vibe-learning signal database (tiered strengths, MOCK guard, lazy recompute)
---

- Signal tiers are upgrade-only: 1 searched → 2 reviewed → 3 engaged → 4 saved. `upsert_signal` never downgrades; a signal_id upgrade only applies when the destination matches the row (mismatched dest inserts a fresh row instead).
- **Why:** engagement clicks can arrive for any boarding pass on a multi-destination detour board; upgrading the panel's primary signal for a different city would corrupt per-destination scores.
- All writes no-op when the `MOCK` env var is set — the guard lives inside `vibe_signals.py` so call sites never need to remember it. The form-level "Test Data" checkbox also suppresses writes (guarded per-request in the web layer).
- Reads are bypassed too in demo mode: the score/top helpers return empty when `MOCK` is set or the caller passes `demo=True` (Test Data switch + TESTING=true). Stats/ranking payloads flag this via `signals_bypassed` so debug surfaces stay honest.
- `dest_vibe_scores` is rebuilt lazily at most once per hour (timestamp in `signals_meta`); fresh events won't show in `/api/vibe-stats` or pill ranking until the next recompute window. Use `recompute_scores(force=True)` when testing.
- Tier-4 (saved) can only be written server-side from ★ Save; `/api/signal-event` clamps client strengths to ≤3.
