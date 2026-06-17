"""Ingestion_Queue persistence: thin, best-effort I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``ingestion_state.py`` (and the pure ``Funnel`` from ``admission_gate.py``).
Mirroring the convention of ``backlog_store.py`` and the design's "pure decision
core, thin best-effort I/O shell" principle, it holds ONLY the Supabase
reads/writes against the ``ingestion_jobs`` / ``ingestion_funnel`` tables and the
``clips`` feed-eligibility columns, and maps rows to and from the pure
:class:`~app.services.ingestion_state.IngestionJob`. Every transition decision
(dedupe via ``should_enqueue``, resume stage, etc.) lives in ``ingestion_state``
and is applied by the caller; this module never decides anything.

Every entrypoint is best-effort: failures are logged and swallowed so an
ingestion action can never block or crash the request path, exactly like
``_seed_topics_bg``. Readers degrade to an empty list / ``False``; writers
degrade to a no-op.

ASCII only.

Validates: Requirements 4.6, 5.1, 5.3, 5.6, 6.4, 6.5, 7.4, 7.5, 9.4
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.supabase import get_client
from app.services.admission_gate import Funnel
from app.services.ingestion_state import (
    IngestionJob,
    IngestionStage,
    JobStatus,
    should_enqueue,
)

logger = logging.getLogger(__name__)

# The Supabase tables backing the Ingestion_Queue / funnel (see
# migration_deep_ingestion.sql).
_JOBS_TABLE = "ingestion_jobs"
_FUNNEL_TABLE = "ingestion_funnel"
_CLIPS_TABLE = "clips"

# Defaults used when mapping rows that carry NULL/unexpected values, matching the
# table defaults so a malformed row can never produce an invalid IngestionJob.
_DEFAULT_STAGE: IngestionStage = "fast_preview"
_DEFAULT_STATUS: JobStatus = "queued"

# A job is "active" (eligible for restart resume / dedupe) when it is not yet
# completed.
_COMPLETED_STATUS: JobStatus = "completed"


# ---------------------------------------------------------------------------
# Row <-> IngestionJob mapping
# ---------------------------------------------------------------------------

def _row_to_job(row: dict) -> IngestionJob:
    """Map an ``ingestion_jobs`` row to the pure ``IngestionJob``.

    NULL/missing columns degrade to the table defaults so a malformed row still
    maps cleanly: ``stage`` -> ``fast_preview``, ``status`` -> ``queued``,
    ``failure_reason`` -> None.
    """
    stage = row.get("stage") or _DEFAULT_STAGE
    status = row.get("status") or _DEFAULT_STATUS
    return IngestionJob(
        topic_slug=row["topic_slug"],
        stage=stage,
        status=status,
        failure_reason=row.get("failure_reason"),
    )


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string for ``updated_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Queue persistence entrypoints
# ---------------------------------------------------------------------------

def enqueue_job(topic_slug: str) -> IngestionJob | None:
    """Insert a queued / fast_preview Ingestion_Job for a Topic, deduped.

    Reads the current active jobs and routes the decision through the pure
    :func:`~app.services.ingestion_state.should_enqueue`: if a non-completed
    (queued/running/failed) job already exists for ``topic_slug`` the enqueue is
    suppressed and ``None`` is returned, so no duplicate job is ever created
    (Req 5.6). Otherwise a row is inserted with stage ``fast_preview`` / status
    ``queued`` and the new ``IngestionJob`` is returned (Req 5.1).

    Best-effort: on any failure the error is logged and ``None`` is returned.

    Validates: Requirements 5.1, 5.6
    """
    try:
        existing = load_active_jobs()
        if not should_enqueue(existing, topic_slug):
            return None

        job = IngestionJob(
            topic_slug=topic_slug,
            stage=_DEFAULT_STAGE,
            status=_DEFAULT_STATUS,
            failure_reason=None,
        )
        db = get_client()
        now = _now_iso()
        db.table(_JOBS_TABLE).upsert(
            {
                "topic_slug": job.topic_slug,
                "stage": job.stage,
                "status": job.status,
                "failure_reason": None,
                "updated_at": now,
            },
            on_conflict="topic_slug",
        ).execute()
        logger.info("[ingestion] enqueued job for topic '%s'", topic_slug)
        return job
    except Exception as exc:
        logger.warning("[ingestion] enqueue_job('%s') failed: %s", topic_slug, exc)
        return None


def load_active_jobs() -> list[IngestionJob]:
    """Load all non-completed Ingestion_Jobs for restart resume.

    Completed jobs are excluded at the query level so the caller only ever sees
    jobs that still need work; each surviving job resumes from its persisted
    stage (decided by ``ingestion_state.resume_stage``). Best-effort: on any
    failure an empty list is returned.

    Validates: Requirements 5.3, 5.4
    """
    try:
        db = get_client()
        res = (
            db.table(_JOBS_TABLE)
            .select("topic_slug, stage, status, failure_reason")
            .neq("status", _COMPLETED_STATUS)
            .execute()
        )
        return [_row_to_job(row) for row in (res.data or [])]
    except Exception as exc:
        logger.warning("[ingestion] load_active_jobs failed: %s", exc)
        return []


def persist_stage(topic_slug: str, stage: IngestionStage) -> None:
    """Persist a job's stage transition (e.g. fast_preview -> full_processing).

    The pure ``ingestion_state.next_stage`` decides the target stage; this shell
    only writes it so a restart resumes from the recorded stage. Best-effort:
    failures are logged and swallowed.

    Validates: Requirements 5.3, 6.1, 6.3
    """
    try:
        db = get_client()
        db.table(_JOBS_TABLE).update(
            {"stage": stage, "updated_at": _now_iso()}
        ).eq("topic_slug", topic_slug).execute()
    except Exception as exc:
        logger.warning(
            "[ingestion] persist_stage('%s' -> %s) failed: %s", topic_slug, stage, exc
        )


def persist_status(
    topic_slug: str, status: JobStatus, failure_reason: str | None = None
) -> None:
    """Persist a job's status transition (queued/running/completed/failed).

    The pure ``ingestion_state.next_job_status`` decides the target status; this
    shell only writes it (along with an optional ``failure_reason``). Best-effort:
    failures are logged and swallowed.

    Validates: Requirements 5.3, 5.5
    """
    try:
        db = get_client()
        db.table(_JOBS_TABLE).update(
            {
                "status": status,
                "failure_reason": failure_reason,
                "updated_at": _now_iso(),
            }
        ).eq("topic_slug", topic_slug).execute()
    except Exception as exc:
        logger.warning(
            "[ingestion] persist_status('%s' -> %s) failed: %s", topic_slug, status, exc
        )


# ---------------------------------------------------------------------------
# Funnel observability
# ---------------------------------------------------------------------------

def record_funnel(funnel: Funnel, topic_slug: str) -> None:
    """Persist the four decode->map->judge->admit counts for one Source_Video.

    Upserts a row keyed by ``(topic_slug, video_id)`` into ``ingestion_funnel``
    so re-running a Source_Video overwrites its prior funnel rather than
    duplicating it. The counts are non-negative and monotonic by construction
    (enforced by the pure ``admission_gate.funnel_counts``). Best-effort:
    failures are logged and swallowed.

    Validates: Requirements 4.6
    """
    try:
        db = get_client()
        db.table(_FUNNEL_TABLE).upsert(
            {
                "topic_slug": topic_slug,
                "video_id": funnel.video_id,
                "idea_units": funnel.idea_units,
                "mapped": funnel.mapped,
                "passed": funnel.passed,
                "admitted": funnel.admitted,
            },
            on_conflict="topic_slug,video_id",
        ).execute()
    except Exception as exc:
        logger.warning(
            "[ingestion] record_funnel('%s', video=%s) failed: %s",
            topic_slug,
            funnel.video_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Idempotent-skip / feed reconciliation
# ---------------------------------------------------------------------------

def topic_already_ingested(topic_slug: str) -> bool:
    """True iff the Topic already has >= 1 feed-eligible Admitted_Clip.

    Used to skip re-ingestion of a coherently ingested Topic for zero model
    calls and zero Quota_Cost: if at least one clip for ``topic_slug`` is
    ``feed_eligible`` the Topic is treated as already ingested. Best-effort: on
    any failure ``False`` is returned so the caller errs toward re-ingesting
    rather than wrongly skipping.

    Validates: Requirements 7.4, 7.5, 9.4
    """
    try:
        db = get_client()
        res = (
            db.table(_CLIPS_TABLE)
            .select("id")
            .eq("topic_slug", topic_slug)
            .eq("feed_eligible", True)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:
        logger.warning(
            "[ingestion] topic_already_ingested('%s') failed: %s", topic_slug, exc
        )
        return False


def set_feed_eligibility(topic_slug: str, admitted_ids: set[str]) -> None:
    """Reconcile feed-eligibility so only Admitted_Clips remain feed-eligible.

    After Full_Processing completes, this:
      1. marks every admitted clip id ``feed_eligible = True`` (and clears the
         ``is_fast_preview`` flag, since it is now a fully admitted clip), and
      2. removes the un-admitted Fast_Preview candidates for the Topic -- the
         preview clips that did not survive admission (Req 6.4, 6.5).

    Best-effort: each step is wrapped so a failure in one does not abort the
    other, and any failure is logged and swallowed. If ``admitted_ids`` is empty
    the reconciliation degrades to removing all Fast_Preview candidates for the
    Topic.

    Validates: Requirements 6.4, 6.5
    """
    db = get_client()
    ids = list(admitted_ids)

    # 1. Mark the admitted clips feed-eligible.
    if ids:
        try:
            db.table(_CLIPS_TABLE).update(
                {"feed_eligible": True, "is_fast_preview": False}
            ).eq("topic_slug", topic_slug).in_("id", ids).execute()
        except Exception as exc:
            logger.warning(
                "[ingestion] set_feed_eligibility mark-admitted('%s') failed: %s",
                topic_slug,
                exc,
            )

    # 2. Remove the un-admitted Fast_Preview candidates for the Topic.
    try:
        query = (
            db.table(_CLIPS_TABLE)
            .delete()
            .eq("topic_slug", topic_slug)
            .eq("is_fast_preview", True)
        )
        if ids:
            # Keep any preview that was admitted; drop the rest.
            query = query.not_.in_("id", ids)
        query.execute()
    except Exception as exc:
        logger.warning(
            "[ingestion] set_feed_eligibility remove-previews('%s') failed: %s",
            topic_slug,
            exc,
        )
