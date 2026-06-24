"""Unit tests for the pure English-only language gate (language_filter).

Covers the script heuristic, the declared-language authority, and the
transcript-level catch-all that together keep the feed English-only.
"""
from app.services.language_filter import (
    is_probably_english,
    looks_non_english_script,
    transcript_looks_non_english,
)


class TestLooksNonEnglishScript:
    def test_english_latin_is_false(self):
        assert looks_non_english_script("Power Efficiency Usage for Cell Sites") is False

    def test_devanagari_hindi_is_true(self):
        assert looks_non_english_script("सेल साइटों के लिए बिजली दक्षता") is True

    def test_cjk_is_true(self):
        assert looks_non_english_script("细胞是微小的能量专家") is True

    def test_cyrillic_is_true(self):
        assert looks_non_english_script("Русская литература девятнадцатого века") is True

    def test_empty_and_letterless_fail_open(self):
        assert looks_non_english_script("") is False
        assert looks_non_english_script(None) is False
        assert looks_non_english_script("12:45 / 15:05  —  !!!") is False

    def test_mostly_english_with_a_few_foreign_chars_stays_english(self):
        # A stray non-Latin char below the 20% threshold must not flip it.
        assert looks_non_english_script("Intro to Calculus (नमस्ते) part 1 of the series") is False


class TestIsProbablyEnglish:
    def test_declared_english_variants_pass(self):
        assert is_probably_english("en") is True
        assert is_probably_english("en-US", "किसी") is True   # declared lang wins over text
        assert is_probably_english("EN-GB") is True

    def test_declared_non_english_drops_even_with_english_title(self):
        # The exact reported bug: English title, Hindi audio.
        assert is_probably_english("hi", "Power Efficiency Usage for Cell Sites") is False
        assert is_probably_english("es", "Some English Title") is False

    def test_no_declared_language_falls_back_to_script(self):
        assert is_probably_english(None, "How the Internet Works") is True
        assert is_probably_english("", "How the Internet Works") is True
        assert is_probably_english(None, "इंटरनेट कैसे काम करता है") is False

    def test_no_language_no_text_fails_open(self):
        assert is_probably_english(None) is True
        assert is_probably_english(None, None, "") is True


class TestTranscriptLooksNonEnglish:
    def test_english_transcript_is_false(self):
        segs = [{"text": "Today we are going to talk about"}, {"text": "how cells make energy"}]
        assert transcript_looks_non_english(segs) is False

    def test_devanagari_transcript_is_true(self):
        segs = [{"text": "आज हम बात करेंगे"}, {"text": "कोशिकाएं ऊर्जा कैसे बनाती हैं"}]
        assert transcript_looks_non_english(segs) is True

    def test_empty_or_none_fails_open(self):
        assert transcript_looks_non_english([]) is False
        assert transcript_looks_non_english(None) is False

    def test_ignores_non_dict_segments(self):
        assert transcript_looks_non_english(["bad", {"text": "all good english here"}]) is False
