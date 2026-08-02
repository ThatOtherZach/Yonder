---
name: Mock fares are internal-only skeletons
description: Demo/mock fare prices are never shown; mock only supplies route skeletons when no fare providers are configured.
---

The rule: user-supplied `mock` params (query/form/JSON) are ignored everywhere. `mock = not settings.configured_providers()` is the only gate, and `_mark_missing_fares_result/_adventure` unconditionally flag `price_kind=="mock"` offers as `fare_missing`, so invented prices never render or leave any API. UI shows "Check Fares" + the cached fare-range pill (`/api/fare-estimate`).

**Why:** The user explicitly demanded all demo/test fare display be ripped out (Grok `invent_demo_fares` deleted, Test Data/Turbo checkbox removed). Fake exact prices were leaking through the Test Data path and API JSON.

**How to apply:** Never re-add a user-facing demo/test-data toggle or show a mock offer's price. New endpoints that call `search_flights` must pass `include_mock = not settings.configured_providers()` and run results through the fare-missing markers. Tests posting `force_mode=detour/mix` to /explore must also send `multi_city: "true"` or the detour branch is suppressed by the toggle.
