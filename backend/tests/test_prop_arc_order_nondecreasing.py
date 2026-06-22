"""Property-based test for non-decreasing role ordinals under single arc order.

# Feature: content-retrieval-revamp, Property 9: Output role ordinals are non-decreasing (single arc order)

Validates: Requirements 2.1, 2.4, 2.7

When a Topic has a Canonical_Arc and its Clips are ordered through
``order_clips_by_arc``, the subsequence of role-bearing Clips has a
non-decreasing sequence of Canonical_Arc role ordinals (Req 2.1, 2.4), and
identical inputs produce identical output (Req 2.7).
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.clip_ordering import order_clips_by_arc

_PEDAGOGICAL_ROLES: list[PedagogicalRole] = [
    "definition", "motivation", "mechanism", "example", "common_misconception",
]


@st.composite
def _arc_and_clips(draw):
    """A Canonical_Arc plus clips whose roles may be in/out of the arc."""
    n_roles = draw(st.integers(min_value=1, max_value=5))
    arc_roles = _PEDAGOGICAL_ROLES[:n_roles]
    arc = CanonicalArc(
        topic_slug=draw(st.text(min_size=0, max_size=10)),
        roles=tuple(
            CanonicalArcRole(role=r, ordinal=i)
            for i, r in enumerate(arc_roles, start=1)
        ),
    )

    n_clips = draw(st.integers(min_value=0, max_value=12))
    # Roles drawn from the arc roles plus None (role-less) and an off-arc role.
    role_choices: list[PedagogicalRole | None] = list(arc_roles) + [None, "approach"]
    clips = []
    for i in range(n_clips):
        role = draw(st.sampled_from(role_choices))
        # Force colliding scores/ids to exercise the id tiebreaker.
        score = draw(st.sampled_from([0.1, 0.5, 0.5, 0.9]))
        clip_id = draw(st.sampled_from(["a", "b", "c", "d", f"id{i}"]))
        clips.append(
            Clip(
                id=clip_id,
                topic_slug="t",
                title="x",
                video_url="http://v",
                final_score=score,
                pedagogical_role=role,
            )
        )
    return arc, clips


class TestArcOrderNonDecreasing:
    @settings(max_examples=100)
    @given(data=_arc_and_clips())
    def test_role_ordinals_non_decreasing(self, data):
        arc, clips = data
        ordered = order_clips_by_arc(clips, arc)

        role_ordinal = {ar.role: ar.ordinal for ar in arc.roles}
        ordinals = [
            role_ordinal[c.pedagogical_role]
            for c in ordered
            if c.pedagogical_role in role_ordinal
        ]
        assert ordinals == sorted(ordinals)

    @settings(max_examples=100)
    @given(data=_arc_and_clips())
    def test_deterministic(self, data):
        arc, clips = data
        first = order_clips_by_arc(clips, arc)
        second = order_clips_by_arc(clips, arc)
        assert [c.id for c in first] == [c.id for c in second]
