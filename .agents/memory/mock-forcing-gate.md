---
name: Mock flag forced by missing providers
description: The explore search forces mock=True when no fare providers are configured; gates keyed on "not mock" silently disable.
---

The `/explore` handler forces `mock = True` whenever `settings.configured_providers()` is empty — independent of the user's Test Data toggle.

**Why:** A feature gate written as `if not settings.testing and not mock:` silently never fired in provider-less environments (found while adding the saved-trip recycle path). Signal/feedback stores also skip on `mock`, so anything that must record real user signals needs `mock` explicitly reset to False when the data is real.

**How to apply:** When gating a feature on "real search" semantics, gate on `settings.testing` / the user's explicit mock request, not the derived `mock` variable; and reset `mock = False` (or set trip_meta.mock=False) when serving real saved data so downstream signal recording still runs.
