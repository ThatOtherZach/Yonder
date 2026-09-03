---
name: Escape card renders through two macros
description: Why an "Escape card" change has to be made twice, and how to keep Detour output untouched while doing it.
---

An Escape trip does not have one card template. Explore and shared-trip pages
render it through the dedicated escape macro, but the **Saved** page renders it
through the Detour macro with a `kind == 'escape'` branch. A change to Escape
wording, ordering, or metadata that is only made in the escape macro silently
misses the Saved page.

**Why:** the two macros were written at different times; the saved renderer
reuses the Detour ticket because a saved Escape is stored as a one-leg
itinerary.

**How to apply:**
- Any "the Escape card should…" change needs both edit sites plus a Saved-page test.
- Inside the Detour macro, gate every Escape-only change behind a single
  `is_saved_escape` flag computed once at the top, so Detour and getaway output
  stays byte-identical.
- Field notes on Saved Escape cards have no server-side place book — they are
  always hydrated by the page's inline JS from the place-brief endpoint. Any
  pill or heading that must appear there has to be produced by *both* the
  server macro and that JS renderer, and the data has to be in the endpoint's
  payload.
- The field-note JS renderer is duplicated between the Explore and Saved
  templates; edits to one need mirroring in the other.
