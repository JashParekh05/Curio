"""Property-based test for the Placement_Score_Map pure core.

# Feature: structured-learn-curriculum, Property 5: Placement reuses existing level semantics (P2.1-P2.6)

``score_to_level`` maps a lightweight diagnostic-quiz result (number ``correct``
out of a ``total``, with an optional ``grade_level``) to a starting Content_Level
(one of ``level_filter`` ``LEVELS``). It defers entirely to the existing
``derive_content_level(grade_level)`` map when no diagnostic was taken
(``total <= 0``), applies the documented boundary thresholds (>= 0.75 advanced,
>= 0.4 intermediate, else beginner) on the clamped fraction otherwise, clamps
out-of-range / negative inputs without raising, is monotonic non-decreasing in
``correct`` at a fixed ``total``, and is deterministic.

This module imports ONLY the pure core under test plus the ``level_filter``
constants/helper it reuses (``LEVELS`` for the membership assertion and
``derive_content_level`` for the deferral assertion). Offline, no external
service. ASCII only.

Validates: Requirements 2.1, 2.3
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

# Import ONLY the pure module under test, plus the level_filter vocabulary and
# the grade -> level map it reuses (for the P2.2 deferral assertion).
from app.services.level_filter import LEVELS, derive_content_level
from app.services.placement_score import (
    ADVANCED_THRESHOLD,
    INTERMEDIATE_THRESHOLD,
    score_to_level,
)

# Level rank for the monotonicity assertion (beginner < intermediate < advanced).
_RANK = {level: i for i, level in enumerate(LEVELS)}

# Valid grade-level keys (from discover_seeding._GRADE_DIFFICULTY) plus aliases,
# mixed with arbitrary strings and None, to exercise the deferral path broadly.
_KNOWN_GRADES = [
    "preschool",
    "elementary",
    "elementary_school",
    "middle_school",
    "high_school",
    "college",
    "adult_learning",
    "professional",
]

# correct / total can be any integer, including negative, correct > total, and
# total <= 0. Bound the magnitude so generation stays fast but still spans the
# out-of-range cases.
_ints = st.integers(min_value=-50, max_value=50)
_grade_levels = st.one_of(
    st.none(),
    st.sampled_from(_KNOWN_GRADES),
    st.text(max_size=12),
)


# ---------------------------------------------------------------------------
# P2.1 - result is always one of LEVELS (total over all inputs)
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(correct=_ints, total=_ints, grade_level=_grade_levels)
def test_result_always_in_levels(correct: int, total: int, grade_level: str | None) -> None:
    """P2.1: For any inputs, the result is one of LEVELS and never raises."""
    result = score_to_level(correct, total, grade_level)
    assert result in LEVELS


# ---------------------------------------------------------------------------
# P2.2 - total <= 0 defers entirely to derive_content_level(grade_level)
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    correct=_ints,
    total=st.integers(min_value=-50, max_value=0),
    grade_level=_grade_levels,
)
def test_no_diagnostic_defers_to_grade_level(
    correct: int, total: int, grade_level: str | None
) -> None:
    """P2.2: total <= 0 -> result == derive_content_level(grade_level)."""
    assert score_to_level(correct, total, grade_level) == derive_content_level(grade_level)


# ---------------------------------------------------------------------------
# P2.3 - monotonic non-decreasing in correct at a fixed total > 0
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    total=st.integers(min_value=1, max_value=50),
    a=_ints,
    b=_ints,
    grade_level=_grade_levels,
)
def test_monotonic_in_correct(total: int, a: int, b: int, grade_level: str | None) -> None:
    """P2.3: at a fixed total, more correct never lowers the level rank."""
    lo, hi = (a, b) if a <= b else (b, a)
    rank_lo = _RANK[score_to_level(lo, total, grade_level)]
    rank_hi = _RANK[score_to_level(hi, total, grade_level)]
    assert rank_hi >= rank_lo


# ---------------------------------------------------------------------------
# P2.4 - boundary correctness at the exact thresholds
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(k=st.integers(min_value=1, max_value=200))
def test_advanced_threshold_boundary(k: int) -> None:
    """P2.4: fraction == ADVANCED_THRESHOLD (0.75 = 3k/4k) -> advanced."""
    total = 4 * k
    correct = 3 * k
    assert correct / total == ADVANCED_THRESHOLD
    assert score_to_level(correct, total) == "advanced"


@settings(max_examples=100)
@given(k=st.integers(min_value=1, max_value=200))
def test_intermediate_threshold_boundary(k: int) -> None:
    """P2.4: fraction == INTERMEDIATE_THRESHOLD (0.4 = 2k/5k) -> intermediate.

    Uses a multiple of 5 so the fraction lands strictly below the advanced
    threshold and exactly on the intermediate threshold.
    """
    total = 5 * k
    correct = 2 * k
    assert correct / total == INTERMEDIATE_THRESHOLD
    assert score_to_level(correct, total) == "intermediate"


# ---------------------------------------------------------------------------
# P2.5 - out-of-range (negative / correct > total) clamps and never raises
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    total=st.integers(min_value=1, max_value=50),
    correct=_ints,
    grade_level=_grade_levels,
)
def test_out_of_range_clamps_to_endpoints(
    total: int, correct: int, grade_level: str | None
) -> None:
    """P2.5: negative correct behaves as 0; correct > total behaves as total."""
    result = score_to_level(correct, total, grade_level)
    assert result in LEVELS

    if correct < 0:
        # Clamps to 0 correct -> beginner (fraction 0.0 < both thresholds).
        assert result == score_to_level(0, total, grade_level)
        assert result == "beginner"
    elif correct > total:
        # Clamps to a full score -> advanced (fraction 1.0 >= advanced).
        assert result == score_to_level(total, total, grade_level)
        assert result == "advanced"


# ---------------------------------------------------------------------------
# P2.6 - determinism: identical inputs -> identical output
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(correct=_ints, total=_ints, grade_level=_grade_levels)
def test_determinism(correct: int, total: int, grade_level: str | None) -> None:
    """P2.6: repeated calls with identical inputs yield identical results."""
    first = score_to_level(correct, total, grade_level)
    second = score_to_level(correct, total, grade_level)
    assert first == second
