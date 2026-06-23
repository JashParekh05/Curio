"""Property-based test for deterministic short-explainer clip selection.

# Feature: adaptive-learning-game, Property 7: Clip selection prefers the short-explainer range

For any non-empty list of candidate clips that contains at least one clip whose
``duration_seconds`` falls within the short-explainer range, ``select_clip``
returns a clip whose duration is within that range, and the selection is
deterministic for a given list.

The short-explainer range is the union of the ideal range (60-180s inclusive)
and the acceptable range (>180-600s inclusive), i.e. ``60 <= duration <= 600``.

Validates: Requirements 10.5
"""
from hypothesis import given, settings, strategies as st

from app.services.game import (
    ACCEPTABLE_MAX_SECONDS,
    IDEAL_MIN_SECONDS,
    select_clip,
)

# Combined short-explainer range: ideal (60-180s) ∪ acceptable (>180-600s).
_IN_RANGE_MIN = IDEAL_MIN_SECONDS          # 60
_IN_RANGE_MAX = ACCEPTABLE_MAX_SECONDS     # 600


def _in_range(duration: int) -> bool:
    return _IN_RANGE_MIN <= duration <= _IN_RANGE_MAX


# Durations span well below, within, and well above the short-explainer range so
# the in-range / out-of-range partition is exercised across examples.
_durations = st.integers(min_value=0, max_value=1200)

# An in-range duration guarantees at least one clip qualifies (60-600s).
_in_range_durations = st.integers(min_value=_IN_RANGE_MIN, max_value=_IN_RANGE_MAX)

_view_counts = st.integers(min_value=0, max_value=10_000_000)


@st.composite
def _clip(draw, duration_strategy=_durations):
    """Build a single candidate clip dict with the fields select_clip reads."""
    idx = draw(st.integers(min_value=0, max_value=1_000_000))
    return {
        "video_id": f"vid{idx}",
        "duration_seconds": draw(duration_strategy),
        "has_caption": draw(st.booleans()),
        "view_count": draw(_view_counts),
    }


@st.composite
def _clips_with_at_least_one_in_range(draw):
    """A non-empty clip list guaranteed to contain at least one in-range clip.

    Mixes freely-durationed clips with one forced in-range clip inserted at a
    random position, so the in-range guarantee holds without biasing where the
    qualifying clip sits in the list.
    """
    others = draw(st.lists(_clip(), min_size=0, max_size=8))
    guaranteed = draw(_clip(duration_strategy=_in_range_durations))
    insert_at = draw(st.integers(min_value=0, max_value=len(others)))
    clips = others[:insert_at] + [guaranteed] + others[insert_at:]
    return clips


class TestClipSelectionPrefersShortExplainerRange:
    @settings(max_examples=200)
    @given(clips=_clips_with_at_least_one_in_range())
    def test_selected_clip_is_in_range_when_one_exists(self, clips):
        chosen = select_clip(clips)
        # A non-empty list always yields a selection.
        assert chosen is not None
        # When at least one clip is in the short-explainer range, the chosen
        # clip's duration must fall within that range (Req 10.5a/b).
        assert _in_range(int(chosen["duration_seconds"]))

    @settings(max_examples=200)
    @given(clips=st.lists(_clip(), min_size=1, max_size=10))
    def test_selection_is_deterministic_for_a_given_list(self, clips):
        # Same input list always yields the same selected clip.
        first = select_clip(clips)
        second = select_clip(clips)
        assert first == second

    def test_empty_list_returns_none(self):
        assert select_clip([]) is None
