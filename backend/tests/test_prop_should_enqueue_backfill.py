"""Property-based test for the Arc_Backfill enqueue decision.

# Feature: content-retrieval-revamp, Property 16: Backfill is enqueued exactly when needed and not in flight

*For any* routed-to Topic state, ``should_enqueue_backfill`` returns True exactly
when the Topic is not Arc_Complete AND no Arc_Backfill is currently enqueued or
in progress for it. This covers both an incomplete-arc Topic (an arc with a
Coverage_Gap) and a Structureless_Topic that is also a Watched_Topic; in every
case an in-flight backfill suppresses a second enqueue, and an already
Arc_Complete Topic is never enqueued. The decision is pure and deterministic, so
identical states always produce identical results (Req 3.1, 3.8, 3.11).

Imports only the pure module under test (``arc_backfill``) plus the pure
Canonical_Arc value types it reasons over, so it runs offline with no external
service.

Validates: Requirements 3.1, 3.8, 3.11
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_backfill import (
    TopicState,
    is_arc_complete,
    should_enqueue_backfill,
)
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole

_ROLES = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_slug = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)


@st.composite
def _arcs(draw, min_roles=1, max_roles=5):
    n = draw(st.integers(min_value=min_roles, max_value=max_roles))
    roles = tuple(
        CanonicalArcRole(role=draw(st.sampled_from(_ROLES)), ordinal=i)
        for i in range(1, n + 1)
    )
    return CanonicalArc(topic_slug=draw(_slug), roles=roles)


@st.composite
def _topic_states(draw):
    """A routed-to TopicState spanning every relevant combination: arc present or
    absent, fully filled or gapped, structureless or structured, watched or not,
    and backfill in flight or not."""
    arc = draw(st.one_of(st.none(), _arcs()))
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


class TestShouldEnqueueBackfill:
    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_enqueue_iff_not_complete_and_not_in_flight(self, state):
        expected = (not is_arc_complete(state)) and (not state.backfill_in_flight)
        assert should_enqueue_backfill(state) == expected

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_in_flight_never_enqueues(self, state):
        in_flight = TopicState(
            topic_slug=state.topic_slug,
            arc=state.arc,
            clips_per_role=state.clips_per_role,
            has_legacy_or_role_model=state.has_legacy_or_role_model,
            has_impression_or_watch=state.has_impression_or_watch,
            backfill_in_flight=True,
        )
        assert should_enqueue_backfill(in_flight) is False

    @settings(max_examples=100)
    @given(arc=_arcs())
    def test_arc_complete_topic_is_never_enqueued(self, arc):
        complete = TopicState(
            topic_slug=arc.topic_slug,
            arc=arc,
            clips_per_role={role.ordinal: 1 for role in arc.roles},
            has_legacy_or_role_model=True,
            has_impression_or_watch=True,
            backfill_in_flight=False,
        )
        assert is_arc_complete(complete) is True
        assert should_enqueue_backfill(complete) is False

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_structureless_but_watched_enqueues_when_not_in_flight(self, state):
        # A Structureless_Topic (no arc, no model) that is watched is retained and
        # backfilled on its next route rather than pruned (Req 3.8).
        structureless_watched = TopicState(
            topic_slug=state.topic_slug,
            arc=None,
            clips_per_role={},
            has_legacy_or_role_model=False,
            has_impression_or_watch=True,
            backfill_in_flight=False,
        )
        assert should_enqueue_backfill(structureless_watched) is True

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
        assert should_enqueue_backfill(state) == should_enqueue_backfill(twin)
