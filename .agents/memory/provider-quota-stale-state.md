---
name: Provider quota registry stale state
description: Why searches silently return zero offers after a provider API key is swapped
---

The quota registry (`yonder/quota.py`) caches per-provider `monthly_remaining` in memory
and in `.quota_cache.json`, with a 10-minute refresh TTL (`last_quota_refresh`).

**Rule:** after swapping or renaming a provider API key (SerpAPI etc.), the cached
"exhausted"/inactive state from the OLD key survives — providers return
`monthly quota exhausted` from `safe_search` while the new key is perfectly healthy.
Searches then render empty panels with HTTP 200 and no traceback.

**Why:** `refresh_serpapi` is TTL-gated; a recent refresh against the old key blocks
re-checking the new one. Provider errors are swallowed into `ProviderResult.error`
and only visible in the saved last-search snapshot (`load_last("escape")["result"]["results"]`).

**How to apply:** whenever a provider key changes, restart the app workflow AND expect
up to one TTL of stale gating; to diagnose empty search results, read the provider
errors from the last-search snapshot first — it names the exact failing provider.
Also remember secrets are only visible to processes started after the secret was added.

Related: `YONDER_DISABLE_RECYCLE=1` env var fully bypasses the recycle-first pool
(added 2026-08-02 at user request).
