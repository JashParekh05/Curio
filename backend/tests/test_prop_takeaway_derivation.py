"""Property-based test for Takeaway_Artifact derivation.

# Feature: content-retrieval-revamp, Property 33: Takeaway derives in arc order and is idempotent

Validates: Requirements 10.1, 10.2, 10.6

For a non-empty set of Admitted_Clips, ``derive_takeaway`` produces exactly one
Takeaway keyed by ``(learner, topic)`` (Req 10.1) whose summary points are
derived in ascending Canonical_Arc role ordinal then descending ranking score
order (Req 10.2), and a re-derivation over the same inputs yields an identical
Takeaway -- no distinct duplicate (Req 10.6). The zero-clip edge case produces
no artifact (Req 10.5).
"""
from hypothesis import given, settings, strategies as st

from app.services.takeaway import TakeawayClip, derive_takeaway


@st.composite
def _clips(draw):
    """A list of TakeawayClips with colliding ordinals/scores/ids."""
    n = draw(st.integers(min_value=1, max_value=12))
    clips = []
    for i in range(n):
        ordinal = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=5)))
        score = draw(st.sampled_from([None, 0.1, 0.5, 0.5, 0.9]))
        clip_id = draw(st.sampled_from(["a", "b", "c", "d", f"id{i}"]))
        clips.append(
            TakeawayClip(
                clip_id=clip_id,
                role_ordinal=ordinal,
                final_score=score,
                title=draw(st.text(min_size=0, max_size=8)),
                description=draw(st.one_of(st.none(), st.text(min_size=0, max_size=8))),
            )
        )
    return clips


def _sort_key(clip):
    ordinal = float("inf") if clip.role_ordinal is None else float(clip.role_ordinal)
    score = clip.final_score if clip.final_score is not None else 0.0
    return (ordinal, -score, clip.clip_id)


class TestTakeawayDerivation:
    @settings(max_examples=100)
    @given(clips=_clips())
    def test_single_takeaway_keyed_by_learner_and_topic(self, clips):
        takeaway = derive_takeaway("learner-1", "topic-a", clips)
        assert takeaway is not None
        assert takeaway.learner_id == "learner-1"
        assert takeaway.topic_slug == "topic-a"
        # One summary point per Admitted_Clip.
        assert len(takeaway.points) == len(clips)

    @settings(max_examples=100)
    @given(clips=_clips())
    def test_derived_in_arc_ordinal_then_score_order(self, clips):
        takeaway = derive_takeaway("L", "T", clips)
        expected_order = sorted(clips, key=_sort_key)
        # The role-ordinal subsequence is non-decreasing.
        ordinals = [
            c.role_ordinal for c in expected_order if c.role_ordinal is not None
        ]
        assert ordinals == sorted(ordinals)
        # Points follow exactly the arc-ordinal-then-score order.
        from app.services.takeaway import _summary_point

        assert list(takeaway.points) == [_summary_point(c) for c in expected_order]

    @settings(max_examples=100)
    @given(clips=_clips())
    def test_rederivation_is_identical(self, clips):
        first = derive_takeaway("L", "T", clips)
        second = derive_takeaway("L", "T", clips)
        assert first == second

    def test_zero_clips_produces_no_artifact(self):
        assert derive_takeaway("L", "T", []) is None
