"""Property-based test for role-less clip placement.

# Feature: content-retrieval-revamp, Property 11: Role-less clips sort last, score then id

Validates: Requirements 2.5

A Clip is role-less when its ``pedagogical_role`` is absent from the
Canonical_Arc, or when the arc is None. Every role-less Clip sorts after every
role-bearing Clip, and role-less Clips are ordered among themselves by
``final_score`` descending then ascending unique Clip id.
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.clip_ordering import order_clips_by_arc

_ARC_ROLES: list[PedagogicalRole] = ["definition", "motivation", "mechanism"]
_OFF_ARC_ROLES: list[PedagogicalRole | None] = [None, "approach", "edge_cases"]


@st.composite
def _mixed_clips_and_arc(draw):
    """A mix of role-bearing and role-less clips, with arc possibly None."""
    arc_present = draw(st.booleans())
    if arc_present:
        arc = CanonicalArc(
            topic_slug="t",
            roles=tuple(
                CanonicalArcRole(role=r, ordinal=i)
                for i, r in enumerate(_ARC_ROLES, start=1)
            ),
        )
        role_choices: list[PedagogicalRole | None] = list(_ARC_ROLES) + _OFF_ARC_ROLES
    else:
        arc = None
        role_choices = list(_ARC_ROLES) + _OFF_ARC_ROLES

    n = draw(st.integers(min_value=0, max_value=12))
    id_pool = [f"c{i}" for i in range(1, 13)]
    ids = draw(st.permutations(id_pool).map(lambda p: p[:n]))
    clips = []
    for cid in ids:
        role = draw(st.sampled_from(role_choices))
        score = draw(st.sampled_from([0.1, 0.5, 0.5, 0.9]))
        clips.append(
            Clip(
                id=cid,
                topic_slug="t",
                title="x",
                video_url="http://v",
                final_score=score,
                pedagogical_role=role,
            )
        )
    return arc, clips


def _is_role_less(clip: Clip, arc: CanonicalArc | None) -> bool:
    if arc is None or clip.pedagogical_role is None:
        return True
    return clip.pedagogical_role not in {ar.role for ar in arc.roles}


class TestRoleLessLast:
    @settings(max_examples=100)
    @given(data=_mixed_clips_and_arc())
    def test_role_less_after_all_role_bearing(self, data):
        arc, clips = data
        ordered = order_clips_by_arc(clips, arc)
        flags = [_is_role_less(c, arc) for c in ordered]
        # Once a role-less clip appears, no role-bearing clip may follow.
        seen_role_less = False
        for is_less in flags:
            if is_less:
                seen_role_less = True
            else:
                assert not seen_role_less

    @settings(max_examples=100)
    @given(data=_mixed_clips_and_arc())
    def test_role_less_ordered_by_score_then_id(self, data):
        arc, clips = data
        ordered = order_clips_by_arc(clips, arc)
        role_less = [c for c in ordered if _is_role_less(c, arc)]
        keys = [(-(c.final_score or 0.0), c.id) for c in role_less]
        assert keys == sorted(keys)
