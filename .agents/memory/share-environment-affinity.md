---
name: Share environment affinity
description: Prevents share records created in Preview from producing broken production URLs.
---

A generated share URL must point to the same application environment and database where its share record was persisted. Replit's `REPLIT_DEPLOYMENT` marker is authoritative for production; otherwise prefer the managed Preview domain when it exists.

**Why:** A Preview-created record does not exist in production. Hard-coding the production origin creates a plausible public URL that always returns 404, while `REPLIT_DOMAINS` may also be present in the editor and cannot identify the active environment by itself.

**How to apply:** Resolve the trusted share origin from platform-managed environment context before creating links and QR codes, ignore arbitrary Host headers, and test both Preview and production behavior.