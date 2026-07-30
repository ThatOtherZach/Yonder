"""Prompt-language detection + model directives (reply language matching).

The prompt alone decides the reply language, per request — no picker, no
persisted preference. Detection is offline and cheap:

- Script-based for non-Latin languages (zh/ja/ko/ru/ar/he/th/el/hi).
- Stopword-based for the big Latin languages (es/fr/de/pt/it).
- Everything else defaults to "en".

Structured fields (IATA codes, ISO2, dates, numbers, JSON keys) are never
translated — the directive tells the model to keep them machine-readable.
Offline/fallback-generated text stays English by design (consistent behavior:
fallbacks never pretend to be translated).
"""

from __future__ import annotations

import re

LANG_NAMES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "he": "Hebrew",
    "th": "Thai",
    "el": "Greek",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
}

# Non-Latin scripts → language (checked by codepoint ranges)
_SCRIPT_RANGES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    ("ja", ((0x3040, 0x309F), (0x30A0, 0x30FF))),  # hiragana/katakana beat han
    ("zh", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))),
    ("ko", ((0xAC00, 0xD7AF), (0x1100, 0x11FF))),
    ("ru", ((0x0400, 0x04FF),)),
    ("ar", ((0x0600, 0x06FF), (0x0750, 0x077F))),
    ("he", ((0x0590, 0x05FF),)),
    ("th", ((0x0E00, 0x0E7F),)),
    ("el", ((0x0370, 0x03FF),)),
    ("hi", ((0x0900, 0x097F),)),
]

# Distinctive stopwords for Latin-script languages (accents help precision)
_LATIN_HINTS: dict[str, tuple[str, ...]] = {
    "es": ("el", "la", "los", "las", "una", "desde", "hasta", "vuelo", "quiero",
           "para", "con", "días", "semana", "playa", "barato", "viaje", "ciudad"),
    "fr": ("le", "la", "les", "une", "des", "je", "veux", "depuis", "vers",
           "vol", "pas", "avec", "semaine", "voyage", "plage", "ville"),
    "de": ("der", "die", "das", "ein", "eine", "ich", "nach", "von", "flug",
           "mit", "und", "woche", "reise", "billig", "stadt"),
    "pt": ("o", "a", "os", "as", "uma", "eu", "quero", "voo", "para", "com",
           "praia", "barato", "viagem", "semana", "cidade"),
    "it": ("il", "lo", "la", "gli", "una", "io", "voglio", "volo", "da", "per",
           "con", "settimana", "viaggio", "spiaggia", "città"),
}


def detect_lang(text: str | None) -> str:
    """Best-effort language code for a user prompt. Defaults to 'en'."""
    t = (text or "").strip()
    if not t:
        return "en"
    # Script pass: count chars per script; first language with >= 2 hits wins
    counts: dict[str, int] = {}
    for ch in t:
        cp = ord(ch)
        if cp < 0x0370:
            continue
        for lang, ranges in _SCRIPT_RANGES:
            if any(lo <= cp <= hi for lo, hi in ranges):
                counts[lang] = counts.get(lang, 0) + 1
                break
    if counts:
        # Japanese kana implies ja even with many han chars
        if counts.get("ja"):
            return "ja"
        best = max(counts, key=lambda k: counts[k])
        if counts[best] >= 2:
            return best
    # Latin pass: distinctive stopword scoring
    words = re.findall(r"[a-zà-ÿœç]+", t.lower())
    if not words:
        return "en"
    wordset = set(words)
    best_lang, best_score = "en", 1  # need >= 2 hits to beat English default
    for lang, hints in _LATIN_HINTS.items():
        score = sum(1 for h in hints if h in wordset)
        if score > best_score:
            best_lang, best_score = lang, score
    return best_lang


def lang_name(code: str | None) -> str:
    return LANG_NAMES.get((code or "en").lower(), "English")


def language_directive(lang: str | None) -> str:
    """Model-agnostic system-prompt line enforcing the reply language.

    Human-facing text fields follow the prompt language; structured fields
    stay machine-readable (JSON keys, IATA, ISO2, dates, numbers, currency).
    """
    name = lang_name(lang)
    return (
        f"\nLANGUAGE: The user's prompt is in {name}. Write ALL human-facing "
        f"text fields (titles, why, intent_summary, assumptions, culture, food, "
        f"caution, tagline, facts, blurbs, suggestions) in {name}. "
        "NEVER use any other language for those fields. Structured fields stay "
        "machine-readable and unchanged: JSON keys in English, IATA codes as 3 "
        "uppercase Latin letters, ISO2 codes, dates as YYYY-MM-DD, numbers and "
        "currency codes as-is."
    )
