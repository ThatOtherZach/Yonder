---
name: Learning-layer knowledge graph
description: Semantics of the destination/vibe/route knowledge store (knowledge.py) and its wiring rules.
---

# Learning-layer knowledge graph

- Route negative caching: only a live provider answering "no offers" writes a failed route row. Provider errors/timeouts say nothing about route existence and must NOT be recorded as failures. Failed rows expire after 30 days (route reverts to unknown so it can recover); a verification newer than the last failure always wins.
- **Why:** poisoning the negative cache with quota/timeout errors would silently hide viable routes for a month.
- Provenance rule: attribute rows are keyed by (subject, attribute, source); readers combine them as Σ(weight × confidence × source_multiplier) with trust order editorial > user_behavior > external > ai_inference (one tunable dict, SOURCE_MULTIPLIERS). Never collapse sources into one row.
- vibe_interpretations is append-only forever (raw query verbatim + AI reasoning verbatim); aggregates are a recomputable cache, evidence links (attribute_evidence) keep every score traceable to raw rows.
- Confidence is derived from stored evidence_count / contradiction_count / last_reinforced_at — retune the formula freely, the inputs persist.
- All capture is fire-and-forget (daemon thread) and MOCK-env guarded like vibe_signals; cold start (empty tables) must equal the pre-knowledge flow.
- **How to apply:** any new fare path must upsert route_knowledge on every outcome; any new Grok proposal entry point should call capture_interpretations_async.
