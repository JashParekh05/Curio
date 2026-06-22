"""Property-based test for the prune decision.

# Feature: content-retrieval-revamp, Property 17: Prune iff structureless and unwatched

*For any* Topic state, ``should_prune`` returns True exactly when the Topic is
both a Structureless_Topic (no Legacy_Section_Model and no Role_Arc_Model) and an
Unwatched_Topic (zero Impressions and zero Watch_Events). A Watched_Topic is
always retained, so ``should_prune`` is always False whenever the Topic is
watched, regardless of its structure. The decision is pure and deterministic, so
identical states always produce identical results (Req 3.5, 3.7, 3.11).

Imports only the pure module under test (``arc_backfill``) plus the pure
Canonical_Arc value types it reasons over, so it runs offline with no external
service.

Validates: Requirements 3.5, 3.7, 3.11
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_backfill import (
    TopicState,
    is_structureless,
    is_watched,
    should_prune,
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


class TestShouldPrune:
    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_prune_iff_structureless_and_unwatched(self, state):
        expected = is_structureless(state) and not is_watched(state)
        assert should_prune(state) == expected

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_watched_topic_is_never_pruned(self, state):
        watched = TopicState(
            topic_slug=state.topic_slug,
            arc=state.arc,
            clips_per_role=state.clips_per_role,
            has_legacy_or_role_model=state.has_legacy_or_role_model,
            has_impression_or_watch=True,
            backfill_in_flight=state.backfill_in_flight,
        )
        assert should_prune(watched) is False

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_structured_topic_is_never_pruned(self, state):
        # A Topic with any structure model is retained even when unwatched.
        structured = TopicState(
            topic_slug=state.topic_slug,
            arc=state.arc,
            clips_per_role=state.clips_per_role,
            has_legacy_or_role_model=True,
            has_impression_or_watch=False,
            backfill_in_flight=state.backfill_in_flight,
        )
        assert should_prune(structured) is False

    @settings(max_examples=100)
    @given(state=_topic_states())
    def test_structureless_unwatched_is_pruned(self, state):
        target = TopicState(
            topic_slug=state.topic_slug,
            arc=state.arc,
            clips_per_role=state.clips_per_role,
            has_legacy_or_role_model=False,
            has_impression_or_watch=False,
            backfill_in_flight=state.backfill_in_flight,
        )
        assert should_prune(target) is True

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
        assert should_prune(state) == should_prune(twin)
