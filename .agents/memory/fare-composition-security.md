---
name: Fare composition trust boundary
description: Security rule for rebuilding trips from fare selections posted by the browser.
---

Browser-posted fare snapshots are display data, not an authority. Any endpoint that composes or persists selected fares must accept server-signed selections, verify them before parsing route data, reject invalid numeric prices, and retain only public HTTPS outbound links.

**Why:** Selected fare payloads include prices, providers, and booking URLs that later appear in public share records; trusting editable browser JSON creates both itinerary-integrity and unsafe-link paths.

**How to apply:** Extend the signed selection payload when adding composition inputs. Keep route/date/model validation after signature verification, and use the shared DNS-aware public URL guard before rendering, saving, or sharing links.