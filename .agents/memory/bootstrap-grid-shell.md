---
name: Bootstrap grid shell
description: Layout convention — pages use vendored bootstrap-grid (grid+utilities only), not full Bootstrap
---
Pages lay out on Bootstrap's grid-only stylesheet (vendored locally under static/vendor), loaded in the base template *before* the inline app styles so app overrides win by source order.

**Why:** mobile layouts were a mess of per-page flex hacks and one-off media queries; the custom parchment design must stay untouched, so full Bootstrap (buttons/forms/components) is deliberately excluded.

**How to apply:** new page layouts should use `container` / `row g-3` / `col-12 col-sm-*` (mobile-first stacking, Bootstrap breakpoints sm=576/md=768/lg=992) instead of bespoke CSS grids or media queries. `.container` max-width/padding are overridden to the app's `--max`/`--pad` vars in the base template — don't reintroduce per-page max-width shells. Component internals (boarding pass, compose field, map) keep their own CSS. Note: only grid utilities exist — no `gap-*`, `h-100`, color or component classes.
