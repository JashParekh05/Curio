"""Pure English-only language gate (no I/O).

Curio's Discover/Learn feed is English-only. YouTube's ``relevanceLanguage=en``
only BIASES search results, so a non-English video (classically: English title,
Hindi audio) can still slip through. This module is the pure decision core the
YouTube charge site and the ingestion pipeline use to drop non-English content.
Paired with thin I/O shells in ``youtube.py`` (search-site + cache filter) and
``pipeline_agent._node_transcribe`` (transcript-level guard).

Design: a DECLARED language (YouTube ``defaultAudioLanguage`` / ``defaultLanguage``)
is authoritative; absent that, a non-Latin-script heuristic on the available
text (title / description / transcript) catches Devanagari, CJK, Cyrillic, etc.
The gate fails OPEN on ambiguity (Latin script + no declared language -> kept),
so an English clip is never wrongly dropped.

ASCII only.
"""
from __future__ import annotations

# Unicode letter ranges for scripts that are unambiguously not English. Text
# dominated by these is non-English regardless of any (often missing) declared
# language. Accented Latin is deliberately EXCLUDED: it can be English, and we
# fail open on ambiguity.
_NON_LATIN_RANGES: tuple[tuple[int, int], ...] = (
    (0x0900, 0x097F),  # Devanagari (Hindi, Marathi, Sanskrit, ...)
    (0x0980, 0x09FF),  # Bengali / Assamese
    (0x0A00, 0x0A7F),  # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B00, 0x0B7F),  # Odia
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
    (0x0400, 0x04FF),  # Cyrillic (Russian, Ukrainian, ...)
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x0590, 0x05FF),  # Hebrew
    (0x3040, 0x309F),  # Hiragana (Japanese)
    (0x30A0, 0x30FF),  # Katakana (Japanese)
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (Chinese, Kanji)
    (0xAC00, 0xD7AF),  # Hangul (Korean)
    (0x0E00, 0x0E7F),  # Thai
)

#: Fraction of letters that must be non-Latin before text is judged non-English.
_NON_LATIN_THRESHOLD = 0.20

#: How many transcript segments to sample for the script heuristic. Cheap, and
#: a clip's opening is representative of its language.
_TRANSCRIPT_SAMPLE_SEGMENTS = 40


def _is_non_latin_letter(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _NON_LATIN_RANGES)


def looks_non_english_script(text: str | None) -> bool:
    """True when ``text`` is meaningfully written in a non-Latin script.

    Counts letters only (digits, spaces, punctuation, emoji ignored). Empty or
    letterless text returns False (no signal -> fail open). An all-Latin title
    like "Power Efficiency for Cell Sites" -> False; a Devanagari or CJK title
    clears the threshold -> True.
    """
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for c in letters if _is_non_latin_letter(c))
    return (non_latin / len(letters)) >= _NON_LATIN_THRESHOLD


def is_probably_english(language: str | None, *texts: str | None) -> bool:
    """Decide whether a video is probably English for the English-only feed.

    1. A DECLARED language (YouTube ``defaultAudioLanguage`` / ``defaultLanguage``)
       is authoritative: keep iff it starts with "en" ("en", "en-US", "en-GB"
       pass; "hi", "es", "ru" drop). This catches the Hindi-audio / English-title
       case the search-bias misses.
    2. No declared language: drop only when ANY provided text is dominated by a
       non-Latin script; otherwise fail OPEN (keep).
    """
    if language and language.strip():
        return language.strip().lower().startswith("en")
    return not any(looks_non_english_script(t) for t in texts)


def transcript_looks_non_english(
    segments: list[dict] | None,
    sample: int = _TRANSCRIPT_SAMPLE_SEGMENTS,
) -> bool:
    """True when a fetched transcript reads as a non-Latin script.

    Joins the first ``sample`` segment texts and applies
    :func:`looks_non_english_script`. This is the catch-all for a video whose
    declared language is missing and whose title is English but whose
    narration / captions are non-English (e.g. Devanagari Hindi). Empty / None
    -> False (fail open).
    """
    if not segments:
        return False
    text = " ".join(
        (seg.get("text") or "") for seg in segments[:sample] if isinstance(seg, dict)
    )
    return looks_non_english_script(text)
