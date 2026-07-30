---
name: Reply-language matching
description: How generated text follows the prompt language and how caches are partitioned by language
---

Rule: the prompt alone decides the reply language, per request. `yonder/lang.py` detects it offline (script ranges for zh/ja/ko/ru/ar/he/th/el/hi, stopwords for es/fr/de/pt/it, default en) and `language_directive()` is appended to every system prompt that emits human-facing text. Machine fields (IATA, ISO2, dates, numbers, JSON keys) stay English/machine-readable.

**Why:** an English prompt must never surface Chinese content and vice versa — including via reuse paths: place-brief cache (lang suffix `|l:xx` in cache keys; legacy suffix-free keys = English, guarded by `_payload_lang_mismatch` prose sniffing), recycled saved trips (prompt-language match filter), and community vibe suggestions (answers store `lang`; API filters, client sends a script-sniffed `lang` param).

**How to apply:** any new model call that returns user-visible prose must append `language_directive(detect_lang(prompt))` and, if cached, include the language in its cache key. Offline/fallback text stays English by design (fallbacks never pretend to be translated); ground-cost blurbs (daily_costs) are still English-only and cached per-country without language.
