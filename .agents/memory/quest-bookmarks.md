---
name: Quest bookmarks
description: Quests are shared library rows — "saving" one must never insert, mutate, or delete the shared row.
---
Quests (kind='quest') are global, server-shared library rows with owner_sess NULL. A user "saving" a quest is an association (bookmark), not a copy.

**Why:** treating quest Save like the personal Escape/Detour insert duplicated quests in the public library for everyone; and because the shared rows are unowned, any owner-scoped-with-NULL-fallback endpoint (delete/refresh) silently becomes an unauthenticated write path to the shared record — quest ids are public in library markup.

**How to apply:** any endpoint that mutates saved_itineraries must branch on kind='quest' first and touch only the session's bookmark; never let a quest id fall through to owner-NULL delete/upsert logic. Bookmarks are exempt from the personal save cap and feed quest popularity ranking.

New Quest rows use a deterministic identity derived from origin plus route, dates, and creative content; fare snapshots, theme decoration, and vibe labels do not participate. Legacy no-ID shares may match only this full canonical content, never route/title alone.

**Why:** generated Quests can share origin, entry, exit, and title while carrying different narratives. Loose matching bookmarks the wrong row, while random IDs allow concurrent saves of one result to create duplicates.

**How to apply:** persist a Quest before rendering its share, carry that exact row ID through the share payload and save request, and let the primary-key upsert converge concurrent creators.
