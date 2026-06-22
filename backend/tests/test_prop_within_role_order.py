"""Property-based test for within-role total order.

# Feature: content-retrieval-revamp, Property 10: Within a role, total order by score then clip id

Validates: Requirements 2.2

When two or more Clips fill the same Canonical_Arc Pedagogical_Role, they are
ordered among themselves by ``final_score`` descending and then by ascending
unique Clip id as the final stable tiebreaker, so there are no remaining ties.
The generator forces colliding scores AND colliding-prefix ids to exercise the
id tiebreaker.
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.clip_ordering import order_clips_by_arc

_SINGLE_ROLE = "definition"
_ARC = CanonicalArc(
    topic_slug="t",
    roles=(CanonicalArcRole(role=_SINGLE_ROLE, ordinal=1),),
)


@st.composite
def _same_role_clips(draw):
    """Clips all filling the same role, with colliding scores and ids."""
    n = draw(st.integers(min_value=2, max_value=10))
    # Unique ids are required for a total order; draw a distinct subset.
    id_pool = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10"]
    ids = draw(st.permutations(id_pool).map(lambda p: p[:n]))
    clips = []
    for cid in ids:
        score = draw(st.sampled_from([0.2, 0.2, 0.5, 0.5, 0.8]))
        clips.append(
            Clip(
                id=cid,
                topic_slug="t",
                title="x",
                video_url="http://v",
                final_score=score,
                pedagogical_role=_SINGLE_ROLE,
            )
        )
    return clips


class TestWithinRoleOrder:
    @settings(max_examples=100)
    @given(clips=_same_role_clips())
    def test_score_desc_then_id_asc(self, clips):
        ordered = order_clips_by_arc(clips, _ARC)
        keys = [(-(c.final_score or 0.0), c.id) for c in ordered]
        assert keys == sorted(keys)

    @settings(max_examples=100)
    @given(clips=_same_role_clips())
    def test_no_remaining_ties(self, clips):
        ordered = order_clips_by_arc(clips, _ARC)
        ids = [c.id for c in ordered]
        # A total order means every clip appears exactly once and the order is
        # fully determined (no two adjacent clips share both score and id).
        assert len(ids) == len(set(ids))
        for a, b in zip(ordered, ordered[1:]):
            assert (-(a.final_score or 0.0), a.id) <= (-(b.final_score or 0.0), b.id)
