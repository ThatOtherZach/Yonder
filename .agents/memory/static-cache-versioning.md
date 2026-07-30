---
name: Static JS cache versioning
description: Static assets are manually versioned with ?v=N query strings in templates; bump on every edit
---
Templates reference static JS with manual cache-buster queries (e.g. `country_map.js?v=16`).

**Why:** A Reset Map button fix "didn't work" for the user because the browser kept serving the cached old file — the code was correct, the version string was stale.

**How to apply:** After editing any file under `yonder/static/`, grep templates for its filename and bump the `?v=N` number (or hash) in every reference before telling the user to retest.
