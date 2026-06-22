"""Property-based test for both-models supersession.

# Feature: content-retrieval-revamp, Property 5: Both models present -- role model wins and legacy is marked superseded

Validates: Requirements 1.5

When a Topic has both a valid Role_Arc_Model and a Legacy_Section_Model, the
Canonical_Arc is derived from the Role_Arc_Model and the result records
``legacy_superseded = True``.
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

_section_indices = st.lists(
    st.integers(min_value=0, max_value=3), min_size=1, max_size=4, unique=True
)


@st.composite
def _valid_role_model(draw):
    n = draw(st.integers(min_value=1, max_value=8))
    roles = draw(
        st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n)
    )
    rows = [ArcRole(role=role, ordinal=o) for role, o in zip(roles, range(1, n + 1))]
    draw(st.randoms()).shuffle(rows)
    return rows


class TestBothModelsSupersede:
    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        role_model=_valid_role_model(),
        section_indices=_section_indices,
    )
    def test_role_model_wins_and_legacy_superseded(
        self, topic_slug, role_model, section_indices
    ):
        result = unify_arc(topic_slug, role_model, section_indices)

        assert result.arc is not None
        assert result.legacy_superseded is True
        assert result.rejection is None
        assert result.structureless is False

        # Arc came from the role model, not the sections.
        role_only = unify_arc(topic_slug, role_model, None)
        assert result.arc == role_only.arc
