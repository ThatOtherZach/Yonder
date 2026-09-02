---
name: Share environment affinity
description: Prevents share records created in Preview from producing broken production URLs.
---

A generated share URL must point to the same application environment and database where its share record was persisted. Prefer the managed Preview domain when it exists; use the canonical production domain only outside Preview.

**Why:** A Preview-created record does not exist in production. Hard-coding the production origin creates a plausible public URL that always returns 404.

**How to apply:** Resolve the trusted share origin from platform-managed environment context before creating links and QR codes, and test both Preview and production behavior.