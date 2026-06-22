"""Property-based test for role-model ordinal order.

# Feature: content-retrieval-revamp, Property 2: Role-model arc preserves recorded ordinal order

Validates: Requirements 1.2

When a Topic has a valid Role_Arc_Model (consecutive-from-1 ordinals), the
produced Canonical_Arc's role sequence equals the model's roles ordered by
ascending recorded ordinal -- regardless of the input row order.
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, PedagogicalRole
from app.services.arc_unifier import unify_arc

_PEDAGOGICAL_ROLES: list[PedagogicalRole] = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_topic_slug = st.text(min_size=0, max_size=30)


@st.composite
def _shuffled_valid_role_model(draw):
    """Consecutive-from-1 Role_Arc_Model rows supplied in a shuffled order."""
    n = draw(st.integers(min_value=1, max_value=8))
    roles = draw(
        st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n)
    )
    rows = [ArcRole(role=role, ordinal=o) for role, o in zip(roles, range(1, n + 1))]
    draw(st.randoms()).shuffle(rows)
    return rows


class TestRoleModelOrder:
    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, role_model=_shuffled_valid_role_model())
    def test_arc_sequence_equals_ordinal_sorted_roles(self, topic_slug, role_model):
        result = unify_arc(topic_slug, role_model, None)
        assert result.arc is not None

        expected_roles = [r.role for r in sorted(role_model, key=lambda r: r.ordinal)]
        actual_roles = [ar.role for ar in result.arc.roles]
        assert actual_roles == expected_roles
