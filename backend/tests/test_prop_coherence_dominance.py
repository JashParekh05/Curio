"""Property-based test for coherence dominance over engagement.

# Feature: content-hook-engagement, Property 27: Coherence dominates engagement

For any two candidate orderings whose Coherence_Scores differ by more than
0.001, ``pick_ordering`` selects the higher-coherence ordering regardless of
either ordering's engagement. Even when the lower-coherence ordering has a
strictly higher first-clip engagement_score, coherence still wins.

Validates: Requirements 6.2
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.arc_assembler import pick_ordering

_COHERENCE_EPSILON = 0.001

# Distinct ids so the two orderings are never confused with each other.
_IDS = ["clip-a", "clip-b", "clip-c", "clip-d"]


def _clip(clip_id: str, engagement: float | None) -> Clip:
    # A single clip with role_ordinal=1 is a VALID ordering: the role_ordinal
    # sequence (just [1]) is trivially non-decreasing, so pick_ordering never
    # rejects it on prerequisite grounds.
    return Clip(
        id=clip_id,
        topic_slug="topic-slug",
        title="A Valid Title",
        description="A valid description.",
        video_url="https://example.com/video",
        engagement_score=engagement,
        role_ordinal=1,
        pedagogical_role="definition",
    )


class TestCoherenceDominance:
    @settings(max_examples=100)
    @given(
        # Two coherence scores guaranteed to differ by MORE than the epsilon.
        low_coherence=st.floats(
            min_value=0.0, max_value=0.8, allow_nan=False, allow_infinity=False
        ),
        gap=st.floats(
            min_value=0.0015, max_value=0.2, allow_nan=False, allow_infinity=False
        ),
        # The lower-coherence ordering gets the HIGHER engagement to prove
        # coherence still wins. Both engagement values stay in [0, 1].
        low_eng=st.floats(
            min_value=0.5, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        high_eng=st.floats(
            min_value=0.0, max_value=0.4, allow_nan=False, allow_infinity=False
        ),
        id_pair=st.lists(
            st.sampled_from(_IDS), min_size=2, max_size=2, unique=True
        ),
    )
    def test_higher_coherence_wins_regardless_of_engagement(
        self, low_coherence, gap, low_eng, high_eng, id_pair
    ):
        high_coherence = low_coherence + gap
        assert high_coherence - low_coherence > _COHERENCE_EPSILON

        # high_coherence ordering: lower engagement.
        # low_coherence ordering: higher engagement (the "tempting" opener).
        high_id, low_id = id_pair
        high_ordering = [_clip(high_id, high_eng)]
        low_ordering = [_clip(low_id, low_eng)]

        # Sanity: the lower-coherence ordering really is the more engaging one.
        assert low_eng > high_eng

        # Supply in both argument orders; coherence must dominate either way.
        result_a = pick_ordering(
            [high_ordering, low_ordering], [high_coherence, low_coherence]
        )
        result_b = pick_ordering(
            [low_ordering, high_ordering], [low_coherence, high_coherence]
        )

        assert result_a is high_ordering, (
            f"coherence {high_coherence} should beat {low_coherence} "
            f"despite engagement {high_eng} vs {low_eng}"
        )
        assert result_b is high_ordering, (
            "coherence dominance must be independent of argument order"
        )
