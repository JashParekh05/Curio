"""Property-based tests for the pure Level_Filter ranking core.

These tests exercise the four level-aware properties from the cold-start
content-library design against ``app.services.level_filter``:

- Property 17: Level derivation is total with a default
- Property 18: Level-match-first ranking
- Property 19: Below-level exclusion when a match exists
- Property 20: Soft fallback ordering by level distance

All functions under test are pure (deterministic in their inputs, no DB / clock
/ global mutation), so the generators below build clip dicts and grade strings
directly. ASCII only.
"""
from hypothesis import given, settings, strategies as st

from app.services.discover_seeding import _GRADE_DIFFICULTY
from app.services.level_filter import (
    DEFAULT_LEVEL,
    LEVEL_INDEX,
    LEVELS,
    derive_content_level,
    exclude_below,
    fallback_order,
    is_match,
    rank_by_level,
)

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

#: A Content_Level or None (unlevelled clip).
_level_or_none = st.sampled_from([*LEVELS, None])

#: The set of grade strings the system actually maps, plus unmapped / None.
_known_grades = list(_GRADE_DIFFICULTY.keys())


@st.composite
def _clip(draw):
    """Generate a clip dict carrying an optional content_level and a stable id.

    The ``id`` lets a test assert stable ordering within a level group; the
    ``content_level`` may be a valid level, None, or an unrecognized string
    (which Level_Filter treats as unlevelled).
    """
    level = draw(
        st.one_of(
            st.sampled_from(LEVELS),
            st.none(),
            st.text(min_size=0, max_size=4),  # unrecognized -> treated as unlevelled
        )
    )
    cid = draw(st.integers(min_value=0, max_value=10_000))
    return {"id": cid, "content_level": level}


_clips = st.lists(_clip(), min_size=0, max_size=12)
_user_level = st.sampled_from(LEVELS)


def _effective_level(clip):
    """Mirror Level_Filter._read_level: only recognized levels count."""
    value = clip.get("content_level")
    return value if value in LEVEL_INDEX else None


# ---------------------------------------------------------------------------
# Property 17: Level derivation is total with a default
# ---------------------------------------------------------------------------
class TestLevelDerivationTotalWithDefault:
    # Feature: cold-start-content-library, Property 17: Level derivation is total with a default
    @settings(max_examples=100)
    @given(
        grade=st.one_of(
            st.none(),
            st.sampled_from(_known_grades),
            st.text(min_size=0, max_size=20),  # arbitrary / unmapped strings
        )
    )
    def test_derivation_is_total_with_default(self, grade):
        result = derive_content_level(grade)

        # Totality: always exactly one of the three Content_Levels.
        assert result in LEVELS

        # Mapped grades resolve to their mapping; everything else -> default.
        if grade is not None and grade in _GRADE_DIFFICULTY:
            assert result == _GRADE_DIFFICULTY[grade]
        else:
            assert result == DEFAULT_LEVEL

        # The default is itself a valid level (intermediate).
        assert DEFAULT_LEVEL in LEVELS


# ---------------------------------------------------------------------------
# Property 18: Level-match-first ranking
# ---------------------------------------------------------------------------
class TestLevelMatchFirstRanking:
    # Feature: cold-start-content-library, Property 18: Level-match-first ranking
    @settings(max_examples=100)
    @given(clips=_clips, user_level=_user_level)
    def test_matches_precede_non_matches(self, clips, user_level):
        ranked = rank_by_level(clips, user_level)

        # Ranking is a permutation: same multiset of clips, none lost or added.
        assert len(ranked) == len(clips)
        assert sorted(c["id"] for c in ranked) == sorted(c["id"] for c in clips)

        match_flags = [is_match(_effective_level(c), user_level) for c in ranked]

        # Every exact-level match precedes every non-match: once a non-match is
        # seen, no later clip may be a match (dominates any other signal).
        seen_non_match = False
        for flag in match_flags:
            if not flag:
                seen_non_match = True
            elif seen_non_match:
                raise AssertionError("a matching clip followed a non-matching clip")

        # Clips with no recognized level are treated as non-matching.
        for c in ranked:
            if _effective_level(c) is None:
                assert not is_match(_effective_level(c), user_level)

        # Stable within the matching group: incoming order preserved.
        in_match_ids = [c["id"] for c in clips if is_match(_effective_level(c), user_level)]
        out_match_ids = [c["id"] for c in ranked if is_match(_effective_level(c), user_level)]
        assert in_match_ids == out_match_ids


# ---------------------------------------------------------------------------
# Property 19: Below-level exclusion when a match exists
# ---------------------------------------------------------------------------
class TestBelowLevelExclusionWhenMatchExists:
    # Feature: cold-start-content-library, Property 19: Below-level exclusion when a match exists
    @settings(max_examples=100)
    @given(clips=_clips, user_level=_user_level)
    def test_below_level_excluded_only_when_match_exists(self, clips, user_level):
        user_index = LEVEL_INDEX[user_level]
        levels = [_effective_level(c) for c in clips]
        has_match = any(lvl == user_level for lvl in levels)

        kept = exclude_below(clips, user_level)
        kept_ids = {c["id"] for c in kept}

        if has_match:
            # No retained clip is strictly below the user's level.
            for c in kept:
                lvl = _effective_level(c)
                if lvl is not None:
                    assert LEVEL_INDEX[lvl] >= user_index

            # Clips at-or-above level, and unlevelled clips, are all retained.
            for c in clips:
                lvl = _effective_level(c)
                if lvl is None or LEVEL_INDEX[lvl] >= user_index:
                    assert c["id"] in kept_ids
        else:
            # No exact match: input returned unchanged (fallback handles it).
            assert [c["id"] for c in kept] == [c["id"] for c in clips]


# ---------------------------------------------------------------------------
# Property 20: Soft fallback ordering by level distance
# ---------------------------------------------------------------------------
class TestSoftFallbackOrderingByDistance:
    # Feature: cold-start-content-library, Property 20: Soft fallback ordering by level distance
    @settings(max_examples=100)
    @given(clips=_clips, user_level=_user_level)
    def test_fallback_orders_by_level_distance(self, clips, user_level):
        ordered = fallback_order(clips, user_level)
        user_index = LEVEL_INDEX[user_level]

        # Ordering is a permutation of the input.
        assert sorted(c["id"] for c in ordered) == sorted(c["id"] for c in clips)

        def bucket(clip):
            """Replicate fallback_order's group/distance key for verification."""
            lvl = _effective_level(clip)
            if lvl is None:
                return (3, 0)  # unlevelled -> last
            i = LEVEL_INDEX[lvl]
            if i == user_index:
                return (0, 0)  # exact (defensive) -> first
            if i > user_index:
                return (1, i - user_index)  # higher: ascending toward advanced
            return (2, user_index - i)  # lower: descending toward beginner

        keys = [bucket(c) for c in ordered]

        # The emitted order is non-decreasing in (group, distance): higher levels
        # (nearest first) come before lower levels (nearest first), unlevelled last.
        assert keys == sorted(keys)

        # Unlevelled clips, if any, occupy the tail of the list.
        levels_present = [_effective_level(c) for c in ordered]
        first_none = next(
            (idx for idx, lvl in enumerate(levels_present) if lvl is None), None
        )
        if first_none is not None:
            assert all(lvl is None for lvl in levels_present[first_none:])
