"""Migration_Gate pure decision core (Phase 4, Req 8).

This module holds the correctness-critical decisions that gate every destructive
schema change in the database cleanup. It is a pure decision core in the codebase
convention (mirroring ``reseed_prioritizer.py`` / ``arc_backfill.py``): no DB, no
clock, no globals, deterministic in its inputs, total, and it never raises. The
operator-run runner (``scripts/staged_migration.py``) consults these functions
before any DDL and applies their verdict; this module decides, the runner acts.

Two gates are implemented:

- :func:`can_drop_column` decides whether a column may be dropped. A drop is
  permitted only with (a) a recorded Pre_Migration_Snapshot (Req 8.11), (b) a
  recorded reverse step that restores the column definition (Req 8.3), and (c)
  removal evidence: either the column is a confirmed Dead_Column (non-null in zero
  rows -- e.g. ``clips.engagement_score`` -- Req 8.4) OR it is Canonical_Arc-
  superseded AND every non-Pruned_Topic's arc has been built (Req 8.5). Otherwise
  it denies and the schema is left unchanged (Req 8.3).

- :func:`can_apply_step` decides whether a Staged_Migration step (additive ->
  cutover -> cleanup) may be applied. It denies any step without a recorded
  Pre_Migration_Snapshot (Req 8.11), denies a cleanup step before its cutover is
  recorded complete (Req 8.9), and denies the transcript-cleanup step (the cleanup
  that removes the duplicate ``clips.transcript`` storage) while any Clip-reachable
  transcript is not yet present in ``transcript_cache`` (Req 8.12).

Every decision is a pure function of the supplied evidence and recorded migration
state, so identical inputs always produce identical :class:`GateDecision` values
(Req 8.10). Each denial carries a stable machine-readable ``reason`` so the runner
can report exactly why a step was refused without re-deriving the decision.

ASCII only.

Validates: Requirements 8.3, 8.4, 8.5, 8.9, 8.10, 8.11, 8.12
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# A Staged_Migration step kind: every schema change is expressed as an additive
# step, then a cutover step, then a cleanup step, each with a recorded reverse
# step (Req 8.1).
Step = Literal["additive", "cutover", "cleanup"]


# ---------------------------------------------------------------------------
# Denial reasons (stable, machine-readable)
# ---------------------------------------------------------------------------

#: No Pre_Migration_Snapshot is recorded for the target database (Req 8.11).
REASON_NO_SNAPSHOT: str = "snapshot_required"
#: No reverse step that restores the column definition is recorded (Req 8.3).
REASON_NO_REVERSE: str = "reverse_step_required"
#: The column is neither confirmed-null nor a built-arc-superseded column (Req 8.4, 8.5).
REASON_NO_EVIDENCE: str = "removal_evidence_required"
#: A cleanup step was requested before its cutover is recorded complete (Req 8.9).
REASON_CUTOVER_INCOMPLETE: str = "cutover_incomplete"
#: Transcript-cleanup requested while a Clip-reachable transcript is absent from
#: transcript_cache (Req 8.12).
REASON_TRANSCRIPTS_NOT_CACHED: str = "transcripts_not_in_cache"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnEvidence:
    """The audit evidence and recorded reversibility for one candidate column.

    Attributes:
        column: The fully qualified column name (e.g. ``clips.engagement_score``).
        nonnull_rows: The number of rows in which the column is non-null, as
            measured by the read-only audit. ``0`` means confirmed Dead_Column
            (Req 8.3, 8.4).
        superseded_by_canonical_arc: True when the column's information is
            superseded by the Canonical_Arc (e.g. ``clips.role_ordinal``,
            ``clips.concept_label``, ``clips.story_score``,
            ``clips.narrative_rank`` -- Req 8.5).
        reverse_step_recorded: True when a reverse step that restores the column
            definition is recorded for this drop (Req 8.3).
    """

    column: str
    nonnull_rows: int
    superseded_by_canonical_arc: bool
    reverse_step_recorded: bool


@dataclass(frozen=True)
class MigrationState:
    """The recorded state the gate reasons over.

    Attributes:
        snapshot_recorded: True when a Pre_Migration_Snapshot is recorded for the
            target database (Req 8.11).
        cutover_complete: True when the cutover step is recorded complete in
            ``migration_state`` (Req 8.9).
        all_nonpruned_arcs_built: True when the Canonical_Arc for every
            non-Pruned_Topic that used a superseded column has been produced
            (Req 8.5).
        transcripts_all_in_cache: True when every Clip-reachable transcript value
            is present in ``transcript_cache`` (Req 8.12).
    """

    snapshot_recorded: bool
    cutover_complete: bool
    all_nonpruned_arcs_built: bool
    transcripts_all_in_cache: bool


@dataclass(frozen=True)
class GateDecision:
    """The verdict of a gate.

    Attributes:
        permitted: True iff the action is safe to apply.
        reason: A stable machine-readable denial reason when ``permitted`` is
            False; ``None`` when permitted.
    """

    permitted: bool
    reason: str | None


# Reusable permitted verdict (frozen dataclasses are immutable, so sharing is safe).
_PERMITTED = GateDecision(permitted=True, reason=None)


# ---------------------------------------------------------------------------
# Column-drop gate (Req 8.3, 8.4, 8.5, 8.10)
# ---------------------------------------------------------------------------

def can_drop_column(ev: ColumnEvidence, state: MigrationState) -> GateDecision:
    """Decide whether a column may be dropped.

    Permit the drop if and only if all three hold, in this precedence:

    1. a Pre_Migration_Snapshot is recorded (Req 8.11); else deny
       ``snapshot_required``;
    2. a reverse step that restores the column definition is recorded (Req 8.3);
       else deny ``reverse_step_required``;
    3. removal evidence exists -- either the column is confirmed-null
       (``nonnull_rows == 0``, e.g. ``clips.engagement_score`` -- Req 8.4) OR it is
       Canonical_Arc-superseded AND every non-Pruned_Topic's arc is built
       (Req 8.5); else deny ``removal_evidence_required``.

    On any denial the schema is left unchanged (the runner applies no DDL --
    Req 8.3). Pure and deterministic: identical inputs yield identical decisions
    (Req 8.10).

    Args:
        ev: The candidate column's audit evidence and recorded reversibility.
        state: The recorded migration state.

    Returns:
        A :class:`GateDecision` permitting the drop, or denying it with a reason.

    Validates: Requirements 8.3, 8.4, 8.5, 8.10
    """
    if not state.snapshot_recorded:
        return GateDecision(permitted=False, reason=REASON_NO_SNAPSHOT)
    if not ev.reverse_step_recorded:
        return GateDecision(permitted=False, reason=REASON_NO_REVERSE)

    confirmed_null = ev.nonnull_rows == 0
    superseded_and_built = (
        ev.superseded_by_canonical_arc and state.all_nonpruned_arcs_built
    )
    if not (confirmed_null or superseded_and_built):
        return GateDecision(permitted=False, reason=REASON_NO_EVIDENCE)

    return _PERMITTED


# ---------------------------------------------------------------------------
# Step gate (Req 8.9, 8.11, 8.12, 8.10)
# ---------------------------------------------------------------------------

def can_apply_step(
    step: Step,
    state: MigrationState,
    removes_transcript: bool = False,
) -> GateDecision:
    """Decide whether a Staged_Migration step may be applied.

    Denies, in this precedence:

    1. any step when no Pre_Migration_Snapshot is recorded (Req 8.11) -- deny
       ``snapshot_required``;
    2. a ``cleanup`` step before its cutover is recorded complete (Req 8.9) --
       deny ``cutover_incomplete``;
    3. the transcript-cleanup step (a ``cleanup`` step with
       ``removes_transcript=True``, which removes the duplicate
       ``clips.transcript`` storage) while any Clip-reachable transcript is not
       yet present in ``transcript_cache`` (Req 8.12) -- deny
       ``transcripts_not_in_cache``.

    ``additive`` and ``cutover`` steps need only a recorded snapshot. The
    ``removes_transcript`` flag scopes the transcript-safety check to exactly the
    transcript-cleanup step so other cleanup steps are not blocked by transcript
    state. Pure and deterministic (Req 8.10).

    Args:
        step: The Staged_Migration step kind being requested.
        state: The recorded migration state.
        removes_transcript: True only for the cleanup step that removes the
            duplicate ``clips.transcript`` storage.

    Returns:
        A :class:`GateDecision` permitting the step, or denying it with a reason.

    Validates: Requirements 8.9, 8.11, 8.12, 8.10
    """
    if not state.snapshot_recorded:
        return GateDecision(permitted=False, reason=REASON_NO_SNAPSHOT)

    if step == "cleanup":
        if not state.cutover_complete:
            return GateDecision(permitted=False, reason=REASON_CUTOVER_INCOMPLETE)
        if removes_transcript and not state.transcripts_all_in_cache:
            return GateDecision(
                permitted=False, reason=REASON_TRANSCRIPTS_NOT_CACHED
            )

    return _PERMITTED
