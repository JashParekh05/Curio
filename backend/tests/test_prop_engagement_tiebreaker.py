"""Property-based test for the engagement tiebreaker.

# Feature: content-hook-engagement, Property 28: Engagement tiebreaker is well-defined and deterministic

For any two candidate orderings tied on coherence (within 0.001) with identical
Realized_Arc, ``choose_by_engagement`` selects the ordering whose first clip has
the higher ``engagement_score`` (a missing/None score is treated as 0). If the
first-clip engagement scores are within 0.001 of each other, it selects the
ordering whose first-clip identifier sorts first lexicographically; and if those
identifiers are equal it returns ``None``. The result does not depend on the
order in which the two orderings are supplied.

Validates: Requirements 6.1, 6.5, 6.6, 6.7
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.arc_assembler import choose_by_engagement

_ENGAGEMENT_EPSILON = 0.001

# Small id pool so equal-id collisions (the None-returning case) occur often.
_IDS = ["clip-a", "clip-b", "clip-c", "clip-d"]

# Engagement scores: None (missing) or a value in [0, 1]. Values are chosen so
# that pairs can land both inside and outside the 0.001 tolerance band.
_engagement = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)


def _clip(clip_id: str, engagement: float | None) -> Clip:
    return Clip(
        id=clip_id,
        topic_slug="topic-slug",
        title="A Valid Title",
        description="A valid description.",
        video_url="https://example.com/video",
        engagement_score=engagement,
        pedagogical_role="definition",
    )


@st.composite
def _ordering(draw) -> list[Clip]:
    """Build a non-empty ordering whose *first* clip carries a random id and
    engagement score. Only the first clip matters for the tiebreaker, but we
    sometimes append a trailing clip to exercise multi-clip orderings."""
    first = _clip(draw(st.sampled_from(_IDS)), draw(_engagement))
    ordering = [first]
    if draw(st.booleans()):
        ordering.append(_clip("clip-tail", draw(_engagement)))
    return ordering


def _eng(ordering: list[Clip]) -> float:
    score = ordering[0].engagement_score
    return float(score) if score is not None else 0.0


class TestEngagementTiebreaker:
    @settings(max_examples=100)
    @given(a=_ordering(), b=_ordering())
    def test_tiebreaker_is_well_defined_and_deterministic(self, a, b):
        result = choose_by_engagement(a, b)

        eng_a = _eng(a)
        eng_b = _eng(b)
        id_a = a[0].id
        id_b = b[0].id

        if abs(eng_a - eng_b) > _ENGAGEMENT_EPSILON:
            # Higher first-clip engagement wins (Req 6.1, 6.6).
            expected = a if eng_a > eng_b else b
            assert result is expected, (
                f"engagement {eng_a} vs {eng_b}: expected higher-engagement "
                f"ordering to win"
            )
        elif id_a == id_b:
            # Engagement tie and identical first-clip ids -> no selection (Req 6.7).
            assert result is None, (
                f"equal first-clip ids {id_a!r} on engagement tie should yield None"
            )
        else:
            # Engagement tie -> lexicographically smaller first-clip id wins (Req 6.5).
            expected = a if id_a < id_b else b
            assert result is expected, (
                f"engagement tie ({eng_a} vs {eng_b}): expected lower id "
                f"({id_a!r} vs {id_b!r}) to win"
            )

        # Argument-order independence: both call orders agree on the winner's
        # identity (or both return None).
        reversed_result = choose_by_engagement(b, a)
        if result is None:
            assert reversed_result is None
        else:
            assert reversed_result is result, (
                "choose_by_engagement is not argument-order independent"
            )
