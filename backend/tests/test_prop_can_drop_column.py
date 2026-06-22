"""Property-based test for the Migration_Gate column-drop decision.

# Feature: content-retrieval-revamp, Property 29: Column drop is permitted only with snapshot, reverse, and evidence

*For any* column evidence and migration state, ``can_drop_column`` permits the
drop if and only if a Pre_Migration_Snapshot is recorded, a reverse step is
recorded, and either the column is confirmed-null (zero non-null rows) or it is
Canonical_Arc-superseded and every non-Pruned_Topic's arc is built; otherwise it
denies and (by leaving the schema to the runner) leaves the schema unchanged.
The decision is pure and deterministic, so identical inputs always produce
identical decisions.

Imports only the pure module under test (``migration_gate``), so it runs offline
with no external service.

Validates: Requirements 8.3, 8.4, 8.5, 8.10
"""
from hypothesis import given, settings, strategies as st

from app.services.migration_gate import (
    REASON_NO_EVIDENCE,
    REASON_NO_REVERSE,
    REASON_NO_SNAPSHOT,
    ColumnEvidence,
    MigrationState,
    can_drop_column,
)

_columns = st.sampled_from([
    "clips.engagement_score",
    "clips.role_ordinal",
    "clips.concept_label",
    "clips.story_score",
    "clips.narrative_rank",
])


@st.composite
def _evidence(draw):
    return ColumnEvidence(
        column=draw(_columns),
        nonnull_rows=draw(st.integers(min_value=0, max_value=7000)),
        superseded_by_canonical_arc=draw(st.booleans()),
        reverse_step_recorded=draw(st.booleans()),
    )


@st.composite
def _states(draw):
    return MigrationState(
        snapshot_recorded=draw(st.booleans()),
        cutover_complete=draw(st.booleans()),
        all_nonpruned_arcs_built=draw(st.booleans()),
        transcripts_all_in_cache=draw(st.booleans()),
    )


def _expected_permitted(ev: ColumnEvidence, state: MigrationState) -> bool:
    if not state.snapshot_recorded:
        return False
    if not ev.reverse_step_recorded:
        return False
    confirmed_null = ev.nonnull_rows == 0
    superseded_and_built = (
        ev.superseded_by_canonical_arc and state.all_nonpruned_arcs_built
    )
    return confirmed_null or superseded_and_built


class TestCanDropColumn:
    @settings(max_examples=200)
    @given(ev=_evidence(), state=_states())
    def test_permitted_iff_snapshot_reverse_and_evidence(self, ev, state):
        decision = can_drop_column(ev, state)
        assert decision.permitted == _expected_permitted(ev, state)
        # A permitted decision carries no reason; a denial always carries one.
        if decision.permitted:
            assert decision.reason is None
        else:
            assert decision.reason is not None

    @settings(max_examples=200)
    @given(ev=_evidence(), state=_states())
    def test_denial_reason_matches_first_failing_condition(self, ev, state):
        decision = can_drop_column(ev, state)
        if decision.permitted:
            return
        # Reasons follow the documented precedence: snapshot, then reverse, then
        # removal evidence.
        if not state.snapshot_recorded:
            assert decision.reason == REASON_NO_SNAPSHOT
        elif not ev.reverse_step_recorded:
            assert decision.reason == REASON_NO_REVERSE
        else:
            assert decision.reason == REASON_NO_EVIDENCE

    @settings(max_examples=100)
    @given(state=_states())
    def test_confirmed_null_column_is_droppable_with_snapshot_and_reverse(self, state):
        # clips.engagement_score: confirmed Dead_Column (zero non-null rows).
        ev = ColumnEvidence(
            column="clips.engagement_score",
            nonnull_rows=0,
            superseded_by_canonical_arc=False,
            reverse_step_recorded=True,
        )
        permitted = can_drop_column(ev, state).permitted
        # Permitted exactly when a snapshot is recorded (reverse + evidence already hold).
        assert permitted == state.snapshot_recorded

    @settings(max_examples=100)
    @given(
        nonnull=st.integers(min_value=1, max_value=7000),
        arcs_built=st.booleans(),
    )
    def test_superseded_column_needs_all_arcs_built(self, nonnull, arcs_built):
        # A superseded, non-null column may only be dropped once every non-pruned
        # arc is built (Req 8.5).
        ev = ColumnEvidence(
            column="clips.role_ordinal",
            nonnull_rows=nonnull,
            superseded_by_canonical_arc=True,
            reverse_step_recorded=True,
        )
        state = MigrationState(
            snapshot_recorded=True,
            cutover_complete=True,
            all_nonpruned_arcs_built=arcs_built,
            transcripts_all_in_cache=True,
        )
        assert can_drop_column(ev, state).permitted == arcs_built

    @settings(max_examples=200)
    @given(ev=_evidence(), state=_states())
    def test_decision_is_deterministic(self, ev, state):
        assert can_drop_column(ev, state) == can_drop_column(ev, state)
