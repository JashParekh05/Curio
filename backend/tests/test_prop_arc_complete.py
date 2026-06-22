"""Property-based test for the Arc_Complete decision.

# Feature: content-retrieval-revamp, Property 15: Arc_Complete iff every role is filled

*For any* Topic state, ``is_arc_complete`` returns True exactly when the Topic
has a Canonical_Arc with at least one role and every one of that arc's role
ordinals is filled by at least one Admitted_Clip, and False otherwise (a Topic
with no arc, or an arc with any zero-clip role, is never Arc_Complete). The
decision is pure and deterministic, so identical states always produce identical
results (Req 3.4, 3.9, 3.11).

Imports only the pure module under test (``arc_backfill``) plus the pure
Canonical_Arc value types it reasons over, so it runs offline with no external
service.

Validates: Requirements 3.4, 3.9, 3.11
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_backfill import TopicState, is_arc_complete
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole

# The Pedagogical_Roles a Canonical_Arc position may carry.
_ROLES = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_slug = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)


@st.composite
def _arcs(draw, min_roles=1, max_roles=6):
    """A Canonical_Arc with consecutive ordinals 1..n and arbitrary roles."""
    n = draw(st.integers(min_value=min_roles, max_value=max_roles))
    roles = tuple(
        CanonicalArcRole(role=draw(st.sampled_from(_ROLES)), ordinal=i)
        for i in range(1, n + 1)
    )
    return CanonicalArc(topic_slug=draw(_slug), roles=roles)


@st.composite
def _topic_states(draw):
    """A TopicState whose arc may be present or None, with arbitrary per-role
    counts spanning zero (a Coverage_Gap) and positive (a filled role), and with
    extra ordinals outside the arc range that must never affect the decision."""
    arc = draw(st.one_of(st.none(), _arcs()))

    # Counts over ordinals 0..8 (covering the in-arc 1..n and out-of-range
    # ordinals), each 0..4 so the zero-count boundary is well exercised.
    clips_per_role = draw(
        st.dictionaries(
            keys=st.integers(min_value=0, max_value=8),
            values=st.integers(min_value=0, max_value=4),
            max_size=9,
        )
    )
    return TopicState(
        topic_slug=draw(_slug),
        arc=arc,
        clips_per_role=clips_per_role,
        has_legacy_or_role_model=draw(st.booleans()),
        has_impression_or_watch=draw(st.booleans()),
        backfill_in_flight=draw(st.booleans()),
    )


def _expected_complete(state: TopicState) -> bool:
    if state.arc is None or not state.arc.roles:
        return False
    return all(state.clips_per_role.get(r.ordinal, 0) >= 1 for r in state.arc.roles)


class TestArcComplete:
    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_complete_iff_arc_present_and_every_role_filled(self, state):
        assert is_arc_complete(state) == _expected_complete(state)

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_no_arc_is_never_complete(self, state):
        without_arc = TopicState(
            topic_slug=state.topic_slug,
            arc=None,
            clips_per_role=state.clips_per_role,
            has_legacy_or_role_model=state.has_legacy_or_role_model,
            has_impression_or_watch=state.has_impression_or_watch,
            backfill_in_flight=state.backfill_in_flight,
        )
        assert is_arc_complete(without_arc) is False

    @settings(max_examples=100)
    @given(arc=_arcs())
    def test_every_role_filled_is_complete(self, arc):
        # Fill every arc role with exactly one clip -> Arc_Complete.
        filled = {role.ordinal: 1 for role in arc.roles}
        state = TopicState(
            topic_slug=arc.topic_slug,
            arc=arc,
            clips_per_role=filled,
            has_legacy_or_role_model=True,
            has_impression_or_watch=False,
            backfill_in_flight=False,
        )
        assert is_arc_complete(state) is True

        # Drop any single role to zero -> not Arc_Complete, still eligible.
        gapped = dict(filled)
        gapped[arc.roles[0].ordinal] = 0
        gap_state = TopicState(
            topic_slug=arc.topic_slug,
            arc=arc,
            clips_per_role=gapped,
            has_legacy_or_role_model=True,
            has_impression_or_watch=False,
            backfill_in_flight=False,
        )
        assert is_arc_complete(gap_state) is False

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_decision_is_deterministic(self, state):
        twin = TopicState(
            topic_slug=state.topic_slug,
            arc=state.arc,
            clips_per_role=dict(state.clips_per_role),
            has_legacy_or_role_model=state.has_legacy_or_role_model,
            has_impression_or_watch=state.has_impression_or_watch,
            backfill_in_flight=state.backfill_in_flight,
        )
        assert is_arc_complete(state) == is_arc_complete(twin)
