"""Property-based test for the Migration_Gate step decision.

# Feature: content-retrieval-revamp, Property 30: Steps are gated by snapshot, cutover order, and transcript safety

*For any* step and migration state, ``can_apply_step`` denies any step when no
Pre_Migration_Snapshot is recorded, denies a cleanup step before its cutover is
recorded complete, and denies the transcript-cleanup step while any Clip-reachable
transcript is not yet present in ``transcript_cache``; otherwise it permits. The
decision is pure and deterministic, so identical inputs always produce identical
decisions.

Imports only the pure module under test (``migration_gate``), so it runs offline
with no external service.

Validates: Requirements 8.9, 8.11, 8.12, 8.10
"""
from hypothesis import given, settings, strategies as st

from app.services.migration_gate import (
    REASON_CUTOVER_INCOMPLETE,
    REASON_NO_SNAPSHOT,
    REASON_TRANSCRIPTS_NOT_CACHED,
    MigrationState,
    can_apply_step,
)

_steps = st.sampled_from(["additive", "cutover", "cleanup"])


@st.composite
def _states(draw):
    return MigrationState(
        snapshot_recorded=draw(st.booleans()),
        cutover_complete=draw(st.booleans()),
        all_nonpruned_arcs_built=draw(st.booleans()),
        transcripts_all_in_cache=draw(st.booleans()),
    )


def _expected(step, state, removes_transcript):
    if not state.snapshot_recorded:
        return False, REASON_NO_SNAPSHOT
    if step == "cleanup":
        if not state.cutover_complete:
            return False, REASON_CUTOVER_INCOMPLETE
        if removes_transcript and not state.transcripts_all_in_cache:
            return False, REASON_TRANSCRIPTS_NOT_CACHED
    return True, None


class TestCanApplyStep:
    @settings(max_examples=200)
    @given(step=_steps, state=_states(), removes_transcript=st.booleans())
    def test_gate_matches_specification(self, step, state, removes_transcript):
        decision = can_apply_step(step, state, removes_transcript)
        exp_permitted, exp_reason = _expected(step, state, removes_transcript)
        assert decision.permitted == exp_permitted
        assert decision.reason == exp_reason

    @settings(max_examples=100)
    @given(step=_steps, removes_transcript=st.booleans())
    def test_no_snapshot_denies_every_step(self, step, removes_transcript):
        state = MigrationState(
            snapshot_recorded=False,
            cutover_complete=True,
            all_nonpruned_arcs_built=True,
            transcripts_all_in_cache=True,
        )
        decision = can_apply_step(step, state, removes_transcript)
        assert decision.permitted is False
        assert decision.reason == REASON_NO_SNAPSHOT

    @settings(max_examples=100)
    @given(removes_transcript=st.booleans())
    def test_cleanup_before_cutover_is_denied(self, removes_transcript):
        state = MigrationState(
            snapshot_recorded=True,
            cutover_complete=False,
            all_nonpruned_arcs_built=True,
            transcripts_all_in_cache=True,
        )
        decision = can_apply_step("cleanup", state, removes_transcript)
        assert decision.permitted is False
        assert decision.reason == REASON_CUTOVER_INCOMPLETE

    @settings(max_examples=100)
    @given(transcripts_cached=st.booleans())
    def test_transcript_cleanup_requires_transcripts_in_cache(self, transcripts_cached):
        state = MigrationState(
            snapshot_recorded=True,
            cutover_complete=True,
            all_nonpruned_arcs_built=True,
            transcripts_all_in_cache=transcripts_cached,
        )
        # The transcript-cleanup step is permitted only when every transcript is
        # already in transcript_cache (Req 8.12).
        decision = can_apply_step("cleanup", state, removes_transcript=True)
        assert decision.permitted == transcripts_cached

        # A non-transcript cleanup with the same state is unaffected by the
        # transcript flag (Req 8.12 scopes only the transcript-cleanup step).
        other = can_apply_step("cleanup", state, removes_transcript=False)
        assert other.permitted is True

    @settings(max_examples=100)
    @given(state=_states())
    def test_additive_and_cutover_need_only_a_snapshot(self, state):
        for step in ("additive", "cutover"):
            decision = can_apply_step(step, state)
            assert decision.permitted == state.snapshot_recorded

    @settings(max_examples=200)
    @given(step=_steps, state=_states(), removes_transcript=st.booleans())
    def test_decision_is_deterministic(self, step, state, removes_transcript):
        assert can_apply_step(step, state, removes_transcript) == can_apply_step(
            step, state, removes_transcript
        )
