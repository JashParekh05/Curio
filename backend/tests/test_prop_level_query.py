"""Property-based test for Level_Conditioned_Query.

# Feature: structured-learn-curriculum, Property 3: Retrieval is level-conditioned and safe (P1.7-P1.11)

*For any* base query string and level string, ``condition_beat_query`` conditions a
beat's base ``search_query`` safely: for a recognized level it appends that level's
qualifier exactly once (idempotent) while preserving the trimmed/collapsed base as a
prefix (P1.7); for an unrecognized or empty level it returns the trimmed/collapsed
base unchanged (P1.8); and it never returns empty when ``base_query.strip()`` is
non-empty (P1.9). ``target_content_level`` is in ``LEVELS`` for a recognized level
and ``None`` otherwise, total over all strings (P1.10). Both functions are
deterministic (P1.11).

Imports only the pure module under test (``level_query``) and the reused
``level_filter.LEVELS`` source of truth, so it runs offline with no external service.

Validates: Requirements 1.4, 1.6
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.level_filter import LEVELS
from app.services.level_query import (
    LEVEL_QUALIFIERS,
    condition_beat_query,
    target_content_level,
)

# Recognized levels and a mix of arbitrary/unrecognized level strings.
_recognized_level = st.sampled_from(LEVELS)
_arbitrary_level = st.text(max_size=16)
_any_level = st.one_of(_recognized_level, _arbitrary_level)

# Base query strings: arbitrary text plus whitespace-heavy variants so trimming /
# whitespace collapsing is exercised.
_base_query = st.text(max_size=48)


def _collapsed(s: str) -> str:
    """Reference for the trim + internal-whitespace-collapse the core performs."""
    return " ".join(s.split())


class TestConditionBeatQueryRecognizedLevel:
    @settings(max_examples=100)
    @given(base=_base_query, level=_recognized_level)
    def test_qualifier_present_with_base_prefix_and_idempotent(self, base, level):
        out = condition_beat_query(base, level)
        qualifier = LEVEL_QUALIFIERS[level]
        collapsed = _collapsed(base)

        # P1.7: the qualifier appears in the output and the trimmed/collapsed base
        # is preserved as a prefix.
        assert qualifier in out
        assert out.startswith(collapsed)

        # P1.7: applying twice is idempotent (no double-append).
        assert condition_beat_query(out, level) == out

        # The qualifier is appended exactly once (it never appears twice unless the
        # base text itself already contained it).
        assert out.count(qualifier) == collapsed.count(qualifier) + (
            0 if collapsed.endswith(qualifier) else 1
        )


class TestConditionBeatQueryUnrecognizedLevel:
    @settings(max_examples=100)
    @given(
        base=_base_query,
        level=_arbitrary_level.filter(lambda s: s not in LEVELS),
    )
    def test_unrecognized_or_empty_level_is_collapsed_base(self, base, level):
        # P1.8: unrecognized/empty level -> trimmed/collapsed base, unchanged.
        assert condition_beat_query(base, level) == _collapsed(base)


class TestConditionBeatQueryNeverEmpty:
    @settings(max_examples=100)
    @given(base=_base_query, level=_any_level)
    def test_never_empty_when_base_non_empty(self, base, level):
        # P1.9: never returns empty when base_query.strip() is non-empty.
        if base.strip():
            assert condition_beat_query(base, level) != ""


class TestTargetContentLevel:
    @settings(max_examples=100)
    @given(level=_any_level)
    def test_recognized_in_levels_else_none(self, level):
        # P1.10: in LEVELS for recognized level, None otherwise; total over strings.
        result = target_content_level(level)
        if level in LEVELS:
            assert result == level
            assert result in LEVELS
        else:
            assert result is None


class TestDeterminism:
    @settings(max_examples=100)
    @given(base=_base_query, level=_any_level)
    def test_deterministic(self, base, level):
        # P1.11: identical inputs -> identical outputs for both functions.
        assert condition_beat_query(base, level) == condition_beat_query(base, level)
        assert target_content_level(level) == target_content_level(level)
