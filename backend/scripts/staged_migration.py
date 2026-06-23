"""Staged_Migration runner (Phase 4, Req 8) -- OPERATOR-RUN ONLY.

This is the thin, best-effort I/O shell that applies the database cleanup as a
sequence of reversible Staged_Migration steps. It is the runner described in the
design: it owns no decision of its own -- every safety verdict delegates to the
pure :mod:`app.services.migration_gate` core (``can_apply_step`` /
``can_drop_column``) -- and it never touches the schema unless that core permits.

Critical operational constraints (Req 8.2):

- This module NEVER runs at application startup. It has no import-time side
  effects and is invoked only through its ``__main__`` guard, which additionally
  refuses to proceed without an explicit ``--confirm`` acknowledgement from the
  Operator.
- The default SQL executor is a DRY-RUN that only logs the SQL it would run, so
  importing or invoking this module cannot, by itself, execute any DDL against
  any database. A real executor must be passed in explicitly by an Operator.

Staged_Migration shape (Req 8.1):

Each schema change is expressed as an additive step, then a cutover step, then a
cleanup step, and every step carries a RECORDED reverse step that restores the
schema to its exact pre-step state. The registry below records, per migration,
the forward SQL (or SQL file) and the reverse SQL for each step. The
content-retrieval-revamp additive step is ``migration_content_revamp.sql``; the
alternative-content-streams migration (``migration_alt_streams.sql``) is
registered as the additive step of its own Staged_Migration (Req 8.7, 8.8).

Behavioral guarantees:

- :func:`apply_step` consults ``can_apply_step`` and refuses (applying no DDL,
  leaving the schema unchanged) when a Pre_Migration_Snapshot is missing
  (Req 8.11), when a cleanup precedes a recorded cutover (Req 8.9), or when the
  transcript-cleanup is requested while a Clip-reachable transcript is still
  absent from ``transcript_cache`` (Req 8.12). ``transcript_cache`` stays the
  single home for transcript text (Req 8.6).
- :func:`drop_column` consults ``can_drop_column`` for each Dead_Column /
  superseded-column drop (Req 8.3, 8.4, 8.5).
- :func:`reverse_step` runs a step's recorded reverse SQL and then verifies the
  schema was restored to the recorded pre-step state. If the reverse fails to
  restore that state, the runner HALTS, leaves the recorded migration state
  unchanged, and returns a reversal-incomplete indication (Req 8.13).

ASCII only.

Validates: Requirements 8.1, 8.2, 8.3, 8.6, 8.9, 8.11, 8.12, 8.13
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable

from app.services.migration_gate import (
    ColumnEvidence,
    GateDecision,
    MigrationState,
    Step,
    can_apply_step,
    can_drop_column,
)

logger = logging.getLogger(__name__)

_MIGRATION_STATE_TABLE = "migration_state"

# Step outcome statuses returned to the Operator.
STATUS_APPLIED: str = "applied"
STATUS_REJECTED: str = "rejected"
STATUS_REVERSED: str = "reversed"
STATUS_REVERSAL_INCOMPLETE: str = "reversal_incomplete"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepResult:
    """The outcome of an attempted Staged_Migration step.

    Attributes:
        migration_id: The Staged_Migration the step belongs to.
        step: The step kind (additive | cutover | cleanup).
        status: One of ``applied`` | ``rejected`` | ``reversed`` |
            ``reversal_incomplete``.
        reason: The gate denial reason on rejection, or the failure detail on a
            reversal-incomplete halt; ``None`` when applied or cleanly reversed.
    """

    migration_id: str
    step: Step
    status: str
    reason: str | None


# ---------------------------------------------------------------------------
# Migration registry -- forward SQL (or file) + recorded reverse SQL per step.
# Each step's reverse SQL restores the schema to its exact pre-step state
# (Req 8.1, 8.3). Reverse for an additive step is "drop what was added"; the
# cutover/cleanup reverse SQL is recorded alongside each migration's plan.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _content_revamp_additive_reverse() -> str:
    """Recorded reverse step for the content-revamp ADDITIVE step (Req 8.1, 8.3).

    Drops exactly the objects ``migration_content_revamp.sql`` creates, restoring
    the pre-step schema. Idempotent (drop ... if exists), additive-step scope only
    -- the cutover/cleanup reverses are recorded separately on those steps.
    """
    return (
        "alter table topics drop column if exists arc_complete;\n"
        "alter table topics drop column if exists archived;\n"
        "drop table if exists migration_state;\n"
        "drop table if exists takeaway_artifacts;\n"
        "drop table if exists curriculum_spine_edges;\n"
        "drop table if exists curriculum_spine_nodes;\n"
        "drop table if exists canonical_arc;\n"
    )


def _alt_streams_additive_reverse() -> str:
    """Recorded reverse step for the alt-streams ADDITIVE step (Req 8.7, 8.8).

    Drops exactly the provider tables and Clip provenance columns
    ``migration_alt_streams.sql`` adds, leaving existing ``clips`` rows otherwise
    unchanged. Idempotent.
    """
    return (
        "drop index if exists clips_content_idx;\n"
        "drop index if exists clips_provenance_idx;\n"
        "alter table clips drop column if exists content_id;\n"
        "alter table clips drop column if exists external_id;\n"
        "alter table clips drop column if exists provider_id;\n"
        "drop table if exists provider_search_cache;\n"
        "drop table if exists provider_spend;\n"
        "drop table if exists provider_registry;\n"
    )


def _structured_curriculum_phase1_additive_reverse() -> str:
    """Recorded reverse step for the structured-curriculum Phase 1 ADDITIVE step.

    Drops exactly the objects ``migration_structured_curriculum_phase1.sql`` adds
    -- the ``clips.level`` slot column (and its supporting index) and the
    ``learning_paths.levels`` jsonb projection -- restoring the pre-step schema.
    Idempotent (drop ... if exists / drop column if exists), additive-step scope
    only, and leaves every existing clips/learning_paths row otherwise unchanged
    (Req 5.1, 5.2).
    """
    return (
        "drop index if exists clips_level_idx;\n"
        "alter table clips drop column if exists level;\n"
        "alter table learning_paths drop column if exists levels;\n"
    )


def _structured_curriculum_phase2_additive_reverse() -> str:
    """Recorded reverse step for the structured-curriculum Phase 2 ADDITIVE step.

    Drops exactly the columns ``migration_structured_curriculum_phase2.sql`` adds
    -- the ``quiz_questions.stage`` checkpoint stage and the
    ``quiz_questions.section_index`` beat anchor -- restoring the pre-step schema.
    Idempotent (drop column if exists), additive-step scope only, and leaves every
    existing quiz_questions row otherwise unchanged (Req 5.1, 5.2).
    """
    return (
        "alter table quiz_questions drop column if exists stage;\n"
        "alter table quiz_questions drop column if exists section_index;\n"
    )


def _structured_curriculum_phase3_additive_reverse() -> str:
    """Recorded reverse step for the structured-curriculum Phase 3 ADDITIVE step.

    Drops exactly the object ``migration_structured_curriculum_phase3.sql`` adds
    -- the ``learning_progress`` table -- restoring the pre-step schema.
    Idempotent (drop table if exists), additive-step scope only (Req 5.2).
    """
    return "drop table if exists learning_progress;\n"


def _self_heal_durable_additive_reverse() -> str:
    """Recorded reverse step for the self-heal durable-cap ADDITIVE step.

    Drops exactly the object ``migration_self_heal_durable.sql`` adds -- the
    ``self_heal_attempts`` table -- restoring the pre-step schema. Idempotent
    (drop table if exists), additive-step scope only.
    """
    return "drop table if exists self_heal_attempts;\n"


@dataclass(frozen=True)
class StagedStep:
    """A single registered step: its forward SQL file and recorded reverse SQL."""

    step: Step
    sql_file: str | None          # forward DDL file under scripts/, or None
    reverse_sql: str              # recorded reverse step (Req 8.1, 8.3)
    removes_transcript: bool = False   # only the clips.transcript cleanup sets this


# The registered Staged_Migrations. Only the additive steps have authored SQL in
# this feature; cutover/cleanup SQL is authored as those steps are scheduled, and
# each is added here with its recorded reverse SQL before it is applied.
MIGRATIONS: dict[str, dict[str, StagedStep]] = {
    "content_revamp": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_content_revamp.sql",
            reverse_sql=_content_revamp_additive_reverse(),
        ),
    },
    # The alternative-content-streams migration applied as the additive step of
    # its OWN Staged_Migration (Req 8.7, 8.8).
    "alt_streams": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_alt_streams.sql",
            reverse_sql=_alt_streams_additive_reverse(),
        ),
    },
    # The structured-learn-curriculum Phase 1 migration applied as the additive
    # step of its OWN Staged_Migration (Req 5.1, 5.2). The gate (can_apply_step)
    # refuses to apply this step unless a Pre_Migration_Snapshot is recorded, and
    # the recorded reverse step drops exactly the added columns/index.
    "structured_curriculum_phase1": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_structured_curriculum_phase1.sql",
            reverse_sql=_structured_curriculum_phase1_additive_reverse(),
        ),
    },
    # The structured-learn-curriculum Phase 2 migration applied as the additive
    # step of its OWN Staged_Migration (Req 5.1, 5.2). The gate (can_apply_step)
    # refuses to apply this step unless a Pre_Migration_Snapshot is recorded, and
    # the recorded reverse step drops exactly the added quiz_questions columns.
    "structured_curriculum_phase2": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_structured_curriculum_phase2.sql",
            reverse_sql=_structured_curriculum_phase2_additive_reverse(),
        ),
    },
    # The structured-learn-curriculum Phase 3 migration applied as the additive
    # step of its OWN Staged_Migration (Req 5.2). The gate (can_apply_step)
    # refuses to apply this step unless a Pre_Migration_Snapshot is recorded, and
    # the recorded reverse step drops exactly the added learning_progress table.
    "structured_curriculum_phase3": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_structured_curriculum_phase3.sql",
            reverse_sql=_structured_curriculum_phase3_additive_reverse(),
        ),
    },
    # The self-heal durable-cap migration applied as the additive step of its OWN
    # Staged_Migration. Persists the per-topic self-heal attempt count so the
    # retry budget survives restarts/deploys and is shared across workers,
    # closing the runaway transcript/quota spend door. The recorded reverse step
    # drops exactly the added self_heal_attempts table.
    "self_heal_durable": {
        "additive": StagedStep(
            step="additive",
            sql_file="migration_self_heal_durable.sql",
            reverse_sql=_self_heal_durable_additive_reverse(),
        ),
    },
}


# ---------------------------------------------------------------------------
# SQL executor -- defaults to a safe DRY-RUN that touches no database.
# ---------------------------------------------------------------------------

SqlExecutor = Callable[[str], None]


def _dry_run_executor(sql: str) -> None:
    """The default executor: logs the SQL it WOULD run and executes nothing.

    This guarantees that importing or invoking the runner cannot, by itself,
    apply any DDL. An Operator must explicitly pass a real executor to run
    against a (non-production) database.
    """
    logger.info("[staged_migration] DRY-RUN (no DDL executed):\n%s", sql)


def _read_sql_file(sql_file: str) -> str:
    """Read a forward-migration SQL file from the scripts directory."""
    path = os.path.join(_SCRIPTS_DIR, sql_file)
    with open(path, "r", encoding="ascii") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Migration-state recording (best-effort; never raises)
# ---------------------------------------------------------------------------

def _record_applied(db, migration_id: str, step: Step, snapshot_ref: str | None,
                    reverse_sql: str) -> None:
    """Record a step as applied in ``migration_state`` (best-effort).

    Stores the recorded reverse step and Pre_Migration_Snapshot reference so the
    gate and a later reverse can read them back (Req 8.1, 8.9, 8.11). Any DB error
    is logged and swallowed -- recording never blocks or crashes the runner.
    """
    if db is None:
        return
    try:
        from datetime import datetime, timezone

        db.table(_MIGRATION_STATE_TABLE).upsert(
            {
                "migration_id": migration_id,
                "step": step,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "reverse_sql": reverse_sql,
                "snapshot_ref": snapshot_ref,
            },
            on_conflict="migration_id,step",
        ).execute()
    except Exception as exc:
        logger.warning(
            "[staged_migration] record applied %s/%s failed: %s",
            migration_id, step, exc,
        )


# ---------------------------------------------------------------------------
# Apply a step (gated by can_apply_step)
# ---------------------------------------------------------------------------

def apply_step(
    migration_id: str,
    step: Step,
    state: MigrationState,
    *,
    db=None,
    execute_sql: SqlExecutor = _dry_run_executor,
    snapshot_ref: str | None = None,
) -> StepResult:
    """Apply one Staged_Migration step iff the Migration_Gate permits it.

    Looks the step up in :data:`MIGRATIONS`, consults the pure
    :func:`~app.services.migration_gate.can_apply_step` (passing the step's
    ``removes_transcript`` flag so the transcript-cleanup safety check is scoped
    correctly -- Req 8.12), and:

    - on a denial, applies NO DDL and leaves the schema unchanged, returning a
      ``rejected`` result carrying the gate's reason (Req 8.9, 8.11, 8.12);
    - on permission, runs the step's forward SQL through ``execute_sql`` and
      records the applied step (with its recorded reverse SQL and the
      Pre_Migration_Snapshot reference) in ``migration_state``.

    Best-effort: any unexpected error is logged and surfaced as a ``rejected``
    result rather than propagating.

    Validates: Requirements 8.9, 8.11, 8.12
    """
    registered = MIGRATIONS.get(migration_id, {}).get(step)
    removes_transcript = bool(registered.removes_transcript) if registered else False

    decision: GateDecision = can_apply_step(step, state, removes_transcript)
    if not decision.permitted:
        logger.info(
            "[staged_migration] step %s/%s REJECTED by gate: %s",
            migration_id, step, decision.reason,
        )
        return StepResult(migration_id, step, STATUS_REJECTED, decision.reason)

    if registered is None:
        logger.info(
            "[staged_migration] step %s/%s permitted but not yet authored",
            migration_id, step,
        )
        return StepResult(migration_id, step, STATUS_REJECTED, "step_not_registered")

    try:
        sql = _read_sql_file(registered.sql_file) if registered.sql_file else ""
        if sql:
            execute_sql(sql)
        _record_applied(db, migration_id, step, snapshot_ref, registered.reverse_sql)
        logger.info("[staged_migration] step %s/%s applied", migration_id, step)
        return StepResult(migration_id, step, STATUS_APPLIED, None)
    except Exception as exc:
        logger.warning(
            "[staged_migration] step %s/%s failed to apply: %s",
            migration_id, step, exc,
        )
        return StepResult(migration_id, step, STATUS_REJECTED, str(exc) or "apply_failed")


# ---------------------------------------------------------------------------
# Drop a column (gated by can_drop_column)
# ---------------------------------------------------------------------------

def drop_column(
    ev: ColumnEvidence,
    state: MigrationState,
    drop_sql: str,
    *,
    execute_sql: SqlExecutor = _dry_run_executor,
) -> StepResult:
    """Drop a single column iff the Migration_Gate permits it.

    Consults the pure :func:`~app.services.migration_gate.can_drop_column`. On a
    denial it applies NO DDL and leaves the schema unchanged, returning a
    ``rejected`` result with the gate reason (Req 8.3). On permission it runs the
    drop SQL through ``execute_sql``. The ``cleanup`` step kind is reported so the
    result is uniform with :func:`apply_step`.

    Validates: Requirements 8.3, 8.4, 8.5
    """
    decision = can_drop_column(ev, state)
    if not decision.permitted:
        logger.info(
            "[staged_migration] drop %s REJECTED by gate: %s",
            ev.column, decision.reason,
        )
        return StepResult("column_drop", "cleanup", STATUS_REJECTED, decision.reason)

    try:
        execute_sql(drop_sql)
        logger.info("[staged_migration] column %s dropped", ev.column)
        return StepResult("column_drop", "cleanup", STATUS_APPLIED, None)
    except Exception as exc:
        logger.warning("[staged_migration] drop %s failed: %s", ev.column, exc)
        return StepResult("column_drop", "cleanup", STATUS_REJECTED, str(exc) or "drop_failed")


# ---------------------------------------------------------------------------
# Reverse a step (Req 8.13: halt + leave state unchanged if it fails to restore)
# ---------------------------------------------------------------------------

def reverse_step(
    migration_id: str,
    step: Step,
    pre_step_schema: object,
    *,
    db=None,
    execute_sql: SqlExecutor = _dry_run_executor,
    schema_probe: Callable[[], object],
) -> StepResult:
    """Run a step's recorded reverse SQL and verify the pre-step schema is restored.

    Runs the registered reverse SQL through ``execute_sql``, then calls
    ``schema_probe`` to read the resulting schema and compares it against the
    recorded ``pre_step_schema``:

    - if the schema matches the recorded pre-step state, the reverse is recorded
      as complete (the applied marker for the step is cleared in
      ``migration_state``) and a ``reversed`` result is returned;
    - if the schema does NOT match -- the reverse failed to restore the exact
      pre-step state -- the runner HALTS: it leaves the recorded migration state
      UNCHANGED and returns a ``reversal_incomplete`` indication (Req 8.13).

    Best-effort: if running the reverse SQL itself raises, that is also treated as
    a reversal-incomplete halt with the recorded state left unchanged.

    Validates: Requirements 8.13
    """
    registered = MIGRATIONS.get(migration_id, {}).get(step)
    if registered is None:
        return StepResult(migration_id, step, STATUS_REVERSAL_INCOMPLETE, "step_not_registered")

    try:
        execute_sql(registered.reverse_sql)
    except Exception as exc:
        logger.warning(
            "[staged_migration] reverse SQL for %s/%s raised: %s; HALTING, state unchanged",
            migration_id, step, exc,
        )
        return StepResult(
            migration_id, step, STATUS_REVERSAL_INCOMPLETE, str(exc) or "reverse_raised"
        )

    actual_schema = schema_probe()
    if actual_schema != pre_step_schema:
        # The reverse did not restore the exact pre-step schema: halt and leave the
        # recorded migration state untouched (Req 8.13).
        logger.error(
            "[staged_migration] reverse of %s/%s did NOT restore the pre-step schema; "
            "HALTING, recorded migration state left unchanged",
            migration_id, step,
        )
        return StepResult(
            migration_id, step, STATUS_REVERSAL_INCOMPLETE, "schema_not_restored"
        )

    # Restored cleanly: clear the applied marker for this step (best-effort).
    _clear_applied(db, migration_id, step)
    logger.info("[staged_migration] step %s/%s reversed", migration_id, step)
    return StepResult(migration_id, step, STATUS_REVERSED, None)


def _clear_applied(db, migration_id: str, step: Step) -> None:
    """Clear a step's applied marker after a clean reverse (best-effort)."""
    if db is None:
        return
    try:
        db.table(_MIGRATION_STATE_TABLE).update({"applied_at": None}).eq(
            "migration_id", migration_id
        ).eq("step", step).execute()
    except Exception as exc:
        logger.warning(
            "[staged_migration] clear applied %s/%s failed: %s",
            migration_id, step, exc,
        )


# ---------------------------------------------------------------------------
# Operator entrypoint -- explicit confirmation required; never at startup.
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Operator CLI entrypoint. Refuses to act without explicit confirmation.

    This is the ONLY place the runner is invoked, and it never executes during
    application startup (Req 8.2). It deliberately does NOT wire a real database
    executor: it prints the planned, registered steps and requires an explicit
    ``--confirm`` flag merely to acknowledge intent. Even with ``--confirm`` the
    default executor is the DRY-RUN, so no DDL is applied here -- a real,
    non-production executor must be wired by an Operator in a controlled session.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    if "--confirm" not in argv:
        logger.warning(
            "[staged_migration] refusing to run without explicit --confirm. "
            "This runner is operator-invoked only and never runs at startup. "
            "Planned migrations: %s",
            ", ".join(sorted(MIGRATIONS)),
        )
        return 1

    logger.info(
        "[staged_migration] --confirm acknowledged. Default executor is DRY-RUN; "
        "no DDL will be applied. Registered migrations: %s",
        ", ".join(sorted(MIGRATIONS)),
    )
    for migration_id, steps in sorted(MIGRATIONS.items()):
        for step, registered in steps.items():
            logger.info(
                "[staged_migration] plan %s/%s (forward=%s)",
                migration_id, step, registered.sql_file,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
