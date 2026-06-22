"""Property-based test for the Level_Grouping pure core.

# Feature: structured-learn-curriculum, Property 1: Structure preserves prerequisites (P1.1-P1.6)

``group_levels`` partitions an already prerequisite-ordered path into 2-4
contiguous, ordered Levels (a single Level for the degenerate one-topic path,
``()`` for the empty path). It never reorders within the prerequisite ordering,
so flattening the Levels in ordinal order must reproduce the input topic order
exactly. The grouping is deterministic, every Level holds at least one topic,
ordinals are consecutive from 1, and bands never regress in Content_Level rank
when the input is already non-decreasing by level.

Validates: Requirements 1.1, 1.2, 1.6
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

# Import ONLY the pure module under test (plus level_filter constants used to
# generate content_level values / compute level ranks for the monotonic band
# assertion).
from app.services.level_filter import LEVEL_INDEX, LEVELS
from app.services.level_grouping import (
    MAX_LEVELS,
    MIN_LEVELS,
    LeveledPath,
    PathTopic,
    flatten,
    group_levels,
)

# Upper bound on generated path length; large enough to exercise the even-split
# fallback and the 2..4 banding without slowing the suite.
_MAX_TOPICS = 12


@st.composite
def _paths(draw, max_size: int = _MAX_TOPICS) -> list[PathTopic]:
    """Arbitrary prerequisite-ordered paths with distinct slugs.

    Each topic has a unique slug (``t0``, ``t1``, ...), a ``content_level`` drawn
    from ``LEVELS``, and ``position`` equal to its 0-based index in the path.
    """
    size = draw(st.integers(min_value=0, max_value=max_size))
    levels = draw(st.lists(st.sampled_from(LEVELS), min_size=size, max_size=size))
    return [
        PathTopic(topic_slug="t" + str(i), content_level=levels[i], position=i)
        for i in range(size)
    ]


@st.composite
def _nondecreasing_paths(draw, max_size: int = _MAX_TOPICS) -> list[PathTopic]:
    """Paths whose Content_Level rank is non-decreasing in path order.

    These mirror a real (already level-sorted) spine path, where the
    transition-based banding is expected to hold the monotonic-band invariant.
    """
    size = draw(st.integers(min_value=0, max_value=max_size))
    ranks = sorted(
        draw(st.lists(st.integers(min_value=0, max_value=2), min_size=size, max_size=size))
    )
    return [
        PathTopic(topic_slug="t" + str(i), content_level=LEVELS[ranks[i]], position=i)
        for i in range(size)
    ]


@settings(max_examples=100)
@given(path=_paths())
def test_partition_preserves_order_no_loss_no_dup(path):
    """P1.1: flatten(group_levels(path)) == the input slug order (a partition)."""
    expected = [t.topic_slug for t in path]
    assert flatten(group_levels(path)) == expected


@settings(max_examples=100)
@given(path=_paths())
def test_level_count_bounds(path):
    """P1.2: empty -> (); single topic -> 1 level; >=2 topics -> 2..4 levels."""
    result = group_levels(path)
    assert isinstance(result, LeveledPath)
    if not path:
        assert result.levels == ()
    elif len(path) == 1:
        # Degenerate edge: a one-topic path cannot split into >=2 non-empty levels.
        assert len(result.levels) == 1
    else:
        assert MIN_LEVELS <= len(result.levels) <= MAX_LEVELS


@settings(max_examples=100)
@given(path=_paths())
def test_contiguity_reproduces_input_order(path):
    """P1.3: concatenating levels in ordinal order reproduces the input order."""
    result = group_levels(path)
    ordered = sorted(result.levels, key=lambda lvl: lvl.ordinal)
    concatenated = [slug for lvl in ordered for slug in lvl.topic_slugs]
    assert concatenated == [t.topic_slug for t in path]


@settings(max_examples=100)
@given(path=_paths())
def test_every_level_non_empty_and_ordinals_consecutive(path):
    """P1.4: every level has >=1 topic; ordinals are consecutive from 1."""
    result = group_levels(path)
    for level in result.levels:
        assert len(level.topic_slugs) >= 1
    ordinals = [level.ordinal for level in result.levels]
    assert ordinals == list(range(1, len(result.levels) + 1))


@settings(max_examples=100)
@given(path=_nondecreasing_paths())
def test_monotonic_bands_for_nondecreasing_input(path):
    """P1.5: for non-decreasing input, no band's level rank regresses.

    A Level's max Content_Level rank is <= the next Level's min rank. Restricted
    to already-sorted input so it stays robust to the even-split fallback (which
    only triggers here when every topic shares one level, where the invariant
    holds trivially).
    """
    result = group_levels(path)
    rank_by_slug = {t.topic_slug: LEVEL_INDEX[t.content_level] for t in path}
    levels = sorted(result.levels, key=lambda lvl: lvl.ordinal)
    for current, nxt in zip(levels, levels[1:]):
        max_current = max(rank_by_slug[s] for s in current.topic_slugs)
        min_next = min(rank_by_slug[s] for s in nxt.topic_slugs)
        assert max_current <= min_next


@settings(max_examples=100)
@given(path=_paths())
def test_determinism(path):
    """P1.6: identical input -> identical LeveledPath."""
    # A second, independently constructed but equal input must group identically.
    copy = [
        PathTopic(topic_slug=t.topic_slug, content_level=t.content_level, position=t.position)
        for t in path
    ]
    assert group_levels(path) == group_levels(copy)
