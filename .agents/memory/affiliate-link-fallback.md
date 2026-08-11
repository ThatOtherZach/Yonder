---
name: Affiliate link fallback
description: Engine generates a fallback offer with affiliate link when all providers fail; tiered fare_note shows historical price or "no history" message
---

# Affiliate link fallback behavior

## Rule
When `search_flights` returns no offers (provider failure, quota exhaustion, or genuine empty), the engine now generates a `FlightOffer` with `fare_missing=True` and a proper Aviasales affiliate URL in `google_flights_url`. This ensures result cards always render.

**Why:** Click conversion is the primary goal; even when live prices are unavailable, travelers should be able to reach the affiliate partner. Provider failures or quota exhaustion must never produce empty/dead panels.

## How to apply
- Fallback is created in `yonder/engine.py` in `_build_fallback_offer(query, currency, results=[])`.
- `fare_note` is set to "recently ~$X" when `route_stats` has history, or "No fare history for this exact route — check live prices" when unknown.
- The fallback offer has `price_kind="live"` (NOT "mock"), so `_mark_missing_fares_result` does NOT touch it. The offer already has `fare_missing=True`.
- `fare_note` field lives on `FlightOffer`; `failure_kind` field lives on `ProviderResult`.
- Templates display `fare_note` with `.fare-note` CSS (italic muted text) below the Check Fares button.
- Detour/Quest leg bp-actions now generate inline Aviasales fallback URLs when `leg.google_flights_url` is None; handles both `date` objects and ISO string dates via `is string` Jinja2 test.

## Provider warning
`settings_page` in `web.py` computes `all_providers_down` from the quota registry and passes it to `settings.html`, which shows a warning banner (internal only, invisible to travelers).
All-providers-down is also logged at WARNING level from `yonder.engine` when the condition occurs.

## Scope note
`fare_note` is only populated for Escape fallback offers (engine.py). Detour/Quest leg pricing failures do NOT get a historical price note yet (adventure.py does not call `_build_fallback_offer`). The affiliate link CTA always renders for those legs via inline template URL generation.
