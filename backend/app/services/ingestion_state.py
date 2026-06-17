"""Ingestion queue/job + Fast_Preview/Full_Processing stage state (pure core).

This module implements the persisted ``Ingestion_Queue``'s decision core from
the design's pure-core / thin-I/O-shell architecture. Every correctness-critical
transition (stage advancement, restart resume, job-state transition, Fast_Preview
limit validation, and duplicate-job suppression) is a pure function — no DB, no
clock, no global reads — so it is unit- and property-testable offline (Req 9.6).

The persisted state itself lives in the ``ingestion_jobs`` table (Topic slug,
stage, status); these functions only decide transitions. The companion
``ingestion_store`` shell does the persistence. Mirrors the pure-core convention
of ``self_heal_state.py`` and ``segment_judge.py``. ASCII only.
"""
from dataclasses import dataclass
from typing import Literal

IngestionStage = Literal["fast_preview", "full_processing"]
JobStatus = Literal["queued", "running", "completed", "failed"]
JobEvent = Literal["start", "succeed", "fail"]

# Default number of candidate clips made feed-eligible during Fast_Preview.
# Valid configured range is [1, 10]; out-of-range falls back to this. (Req 6.2)
DEFAULT_FAST_PREVIEW_LIMIT: int = 3
# Default cap on model calls per Topic per ingestion cycle. (Req 9.3)
DEFAULT_MODEL_CALL_BUDGET: int = 20


@dataclass(frozen=True)
class IngestionJob:
    """A persisted Ingestion_Queue entry for one Topic.

    Attributes:
        topic_slug:     Identity for dedupe — one active job per Topic (Req 5.6).
        stage:          'fast_preview' | 'full_processing' (Req 6.1, 6.3).
        status:         'queued' | 'running' | 'completed' | 'failed' (Req 5.3).
        failure_reason: Optional reason recorded when a job fails; None otherwise.
    """

    topic_slug: str
    stage: IngestionStage
    status: JobStatus
    failure_reason: str | None = None


def next_stage(current: IngestionStage) -> IngestionStage:
    """Advance the ingestion stage.

    'fast_preview' -> 'full_processing'; 'full_processing' -> 'full_processing'
    (the terminal stage). Pure and total over every IngestionStage. (Req 6.1, 6.3)

    Args:
        current: The job's current stage.

    Returns:
        The next stage to execute.
    """
    if current == "fast_preview":
        return "full_processing"
    return "full_processing"


def resume_stage(job: IngestionJob) -> IngestionStage:
    """The stage a non-completed job resumes from on restart.

    A non-completed job re-executes from its persisted stage — only stages not
    already completed are re-run, so resume is exactly the recorded stage. Pure
    and deterministic. (Req 5.4)

    Args:
        job: The job being resumed.

    Returns:
        The persisted stage to resume from.
    """
    return job.stage


def validate_fast_preview_limit(raw: object) -> tuple[int, str | None]:
    """Validate a configured Fast_Preview limit against the range [1, 10].

    A value within [1, 10] is returned as ``(value, None)``; anything else
    (missing, None, non-numeric, or out-of-range) falls back to the default and
    reports the error code, returning ``(3, 'fast_preview_limit_out_of_range')``.
    Pure and total. (Req 6.2, 6.8)

    Args:
        raw: Any raw limit value from configuration or a caller.

    Returns:
        A ``(limit, error)`` tuple: the effective limit and an error code, or None
        when the input was valid.
    """
    if raw is None or isinstance(raw, bool):
        return DEFAULT_FAST_PREVIEW_LIMIT, "fast_preview_limit_out_of_range"
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        # OverflowError guards non-finite floats (inf/-inf), keeping this
        # function total per its contract (Req 6.8).
        return DEFAULT_FAST_PREVIEW_LIMIT, "fast_preview_limit_out_of_range"
    if 1 <= value <= 10:
        return value, None
    return DEFAULT_FAST_PREVIEW_LIMIT, "fast_preview_limit_out_of_range"


def next_job_status(current: JobStatus, event: JobEvent) -> JobStatus:
    """Transition a job's status given a lifecycle event.

    Transition table (Req 5.4, 5.5):
    - queued  + start   -> running
    - running + succeed -> completed
    - running + fail    -> running  (UNCHANGED — left queued for retry, Req 5.5)
    - completed         -> terminal (no event changes it)
    - any other pairing -> current  (unchanged)

    Pure and total over every (status, event) pairing.

    Args:
        current: The job's current status.
        event:   The lifecycle event being applied.

    Returns:
        The resulting status.
    """
    if current == "queued" and event == "start":
        return "running"
    if current == "running" and event == "succeed":
        return "completed"
    # running + fail leaves the job running (retryable); completed is terminal;
    # every other pairing is a no-op.
    return current


def should_enqueue(existing: list[IngestionJob], topic_slug: str) -> bool:
    """Decide whether a new Ingestion_Job should be enqueued for a Topic.

    Returns False iff a non-completed (queued/running/failed) job already exists
    for ``topic_slug`` — so no duplicate is enqueued. Returns True when there is
    no job for the Topic or every existing job for it is completed. Pure and
    deterministic. (Req 5.6)

    Args:
        existing:   The known jobs to check against.
        topic_slug: The Topic identity being considered for enqueue.

    Returns:
        True if a new job should be enqueued; False otherwise.
    """
    for job in existing:
        if job.topic_slug == topic_slug and job.status != "completed":
            return False
    return True
