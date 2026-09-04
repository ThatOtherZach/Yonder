---
name: Shared Ground Spend block
description: The budget/ground-cost strip is one macro shared by all three ticket types; how its data has to be projected so it does not silently vanish.
---

All three ticket types (Escape, Detour, Quest) render their ground/budget/all-in
numbers through **one** shared Jinja macro. Do not hand-roll a second budget
block for a new card type — they drift apart immediately and the drift is
invisible until someone compares two cards side by side.

**Why:** Escape and Quest cards spent a long time rendering *nothing* where the
budget block should be. Their templates read ground fields off models that never
had those fields, so every lookup was silently Undefined. Jinja does not error on
a missing attribute, so this is a silent failure mode, not a crash.

**How to apply:**

- A card reads the block off whatever object the macro is handed. When a ticket
  is converted between shapes, the ground fields must be **projected to the level
  the receiving card reads them at**. The classic bug: a Saved Escape renders
  through the Detour macro, which reads the itinerary, while the ground data
  lives one level down on the offer — so saving an Escape wiped its block.
- Composed tickets (built from selected fares) are assembled from legs and get no
  ground data for free. Anything that builds a ticket outside the planner has to
  attach it explicitly, or that path is the one that looks broken.
- Multi-city tickets need the stretches summed, not one city quoted for the whole
  trip. When splitting N days across cities, the parts must add back to exactly N
  — a naive half-split with a `max(1, ...)` floor charges 2 days for a 1-day trip.
- Ground estimates are decoration: fetch them cache/static-only on
  latency-sensitive paths and let them degrade to absent rather than failing the
  request.
- A Detour is a closed loop (home → A → B → home) and can never be an input to
  the trip builder. Its card says so instead of offering a disabled button.
