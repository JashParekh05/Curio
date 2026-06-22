"""Property-based test for Canonical_Arc ordinals.

# Feature: content-retrieval-revamp, Property 1: Canonical_Arc has consecutive ordinals from 1

Validates: Requirements 1.1, 1.8

Any arc produced by the Arc_Unifier -- from a valid Role_Arc_Model or from a
Legacy_Section_Model -- must carry ordinals that are exactly ``1..n`` with no
gap and no duplicate, contain at least one role, and be deterministic: the same
input always yields the same arc.
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
def _valid_role_model(draw):
    """A Role_Arc_Model with consecutive-from-1 ordinals in shuffled order."""
    n = draw(st.integers(min_value=1, max_value=8))
    roles = draw(
        st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n)
    )
    ordinals = list(range(1, n + 1))
    draw(st.randoms()).shuffle(ordinals)
    return [ArcRole(role=role, ordinal=o) for role, o in zip(roles, ordinals)]


# Non-empty subsets of the legacy section index space {0,1,2,3}.
_section_indices = st.lists(
    st.integers(min_value=0, max_value=3), min_size=1, max_size=4, unique=True
)


def _assert_canonical(arc):
    ordinals = [r.ordinal for r in arc.roles]
    assert len(arc.roles) >= 1
    assert ordinals == list(range(1, len(arc.roles) + 1))
    assert len(set(ordinals)) == len(ordinals)  # no duplicate


class TestCanonicalArcOrdinals:
    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, role_model=_valid_role_model())
    def test_role_model_arc_has_consecutive_ordinals(self, topic_slug, role_model):
        result = unify_arc(topic_slug, role_model, None)
        assert result.arc is not None
        _assert_canonical(result.arc)
        # Deterministic: identical input -> identical arc.
        again = unify_arc(topic_slug, role_model, None)
        assert again.arc == result.arc

    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, section_indices=_section_indices)
    def test_section_arc_has_consecutive_ordinals(self, topic_slug, section_indices):
        result = unify_arc(topic_slug, None, section_indices)
        assert result.arc is not None
        _assert_canonical(result.arc)
        again = unify_arc(topic_slug, None, section_indices)
        assert again.arc == result.arc
