"""Property-based test for clip association.

# Feature: content-retrieval-revamp, Property 6: Clip association is exactly the matching arc role, or nothing

Validates: Requirements 1.6, 1.11

``associate_clip_role`` returns the unique Canonical_Arc role matching a clip's
recorded Pedagogical_Role when present (Req 1.6), and returns None -- so the
caller associates nothing -- when the clip's role is absent from the arc or the
clip carries no role (Req 1.11).
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import PedagogicalRole
from app.services.arc_unifier import (
    CanonicalArc,
    CanonicalArcRole,
    associate_clip_role,
)

_PEDAGOGICAL_ROLES: list[PedagogicalRole] = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]


@st.composite
def _arc_with_distinct_roles(draw):
    """A Canonical_Arc whose roles are distinct, consecutive from 1."""
    n = draw(st.integers(min_value=1, max_value=len(_PEDAGOGICAL_ROLES)))
    chosen = draw(
        st.lists(
            st.sampled_from(_PEDAGOGICAL_ROLES),
            min_size=n, max_size=n, unique=True,
        )
    )
    roles = tuple(
        CanonicalArcRole(role=role, ordinal=o)
        for o, role in enumerate(chosen, start=1)
    )
    return CanonicalArc(topic_slug="t", roles=roles)


class TestClipAssociation:
    @settings(max_examples=100)
    @given(arc=_arc_with_distinct_roles(), data=st.data())
    def test_present_role_associates_to_its_unique_arc_role(self, arc, data):
        present = data.draw(st.sampled_from([ar.role for ar in arc.roles]))
        matched = associate_clip_role(arc, present)

        assert matched is not None
        assert matched.role == present
        # It is the exact arc role for that Pedagogical_Role.
        expected = next(ar for ar in arc.roles if ar.role == present)
        assert matched == expected

    @settings(max_examples=100)
    @given(arc=_arc_with_distinct_roles(), data=st.data())
    def test_absent_role_associates_to_nothing(self, arc, data):
        arc_roles = {ar.role for ar in arc.roles}
        absent_candidates = [r for r in _PEDAGOGICAL_ROLES if r not in arc_roles]
        # Skip when the arc happens to cover every role.
        if not absent_candidates:
            return
        absent = data.draw(st.sampled_from(absent_candidates))
        assert associate_clip_role(arc, absent) is None

    @settings(max_examples=100)
    @given(arc=_arc_with_distinct_roles())
    def test_roleless_clip_associates_to_nothing(self, arc):
        assert associate_clip_role(arc, None) is None
