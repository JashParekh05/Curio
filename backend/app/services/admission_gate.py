"""Admission_Gate: pure, unit-testable decision functions for the ADMIT stage.

This module is the final quality bar of the
DECODE -> BREAK-DOWN -> MAP -> JUDGE -> ADMIT ingestion pipeline.  It contains
ONLY the pure decision core (Req 9.6): every function here is deterministic in
its inputs with no DB, clock, or global reads, mirroring the pure-core
convention in ``coherence_budget.py`` / ``arc_assembler.py``.

The best-effort I/O shell (``persist_admitted``) lives alongside these functions
in a later task; it is intentionally absent here.

Decisions implemented:

  - ``admission_decision``: admit a single segment iff it was mapped, its
    Segment_Verdict status is ``passing``, and the assembled sequence passed
    both coherence and alignment; otherwise reject with a specific reason
    (Req 4.1, 4.2, 4.3, 2.9).
  - ``admit_topic``: apply ``admission_decision`` across every candidate segment
    for a Topic and classify the outcome as ``fully_admitted`` /
    ``partially_admitted`` / ``skipped`` (Req 4.5).
  - ``funnel_counts``: assemble the observable four-count
    decode -> map -> judge -> admit funnel per Source_Video, non-negative and
    monotonic by construction (Req 4.6).

ASCII only.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

logger = logging.getLogger(__name__)

# ``SegmentVerdict`` and ``MappedSegment`` are produced by sibling modules
# (``segment_judge`` / ``segment_mapper``) that are authored in the same build
# wave.  Per the design, import the verdict type when available and otherwise
# duck-type on ``.status``; the pure core only reads ``verdict.status`` and
# ``segment.atom.id`` so it never hard-depends on those modules at runtime.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.segment_judge import SegmentVerdict
    from app.services.segment_mapper import MappedSegment

# ---------------------------------------------------------------------------
# Status / outcome literals
# ---------------------------------------------------------------------------

AdmissionStatus = Literal["admitted", "rejected"]
TopicOutcome = Literal["fully_admitted", "partially_admitted", "skipped"]

#: The verdict status value that clears the judging gate.
_PASSING_STATUS: str = "passing"


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissionDecision:
    """The admit/reject decision for a single segment.

    ``reason`` is one of:
      - ``'ok'``              -> admitted (Req 4.1)
      - ``'failing_verdict'`` -> rejected: missing/failing Segment_Verdict (Req 4.2)
      - ``'unmapped'``        -> rejected: Unmapped_Segment (Req 4.3)
      - ``'coherence'``       -> rejected: sequence failed coherence (Req 2.9)
      - ``'alignment'``       -> rejected: sequence failed alignment (Req 2.9)
    """

    status: AdmissionStatus
    reason: str


@dataclass(frozen=True)
class Funnel:
    """Per Source_Video decode -> map -> judge -> admit funnel (Req 4.6).

    All four counts are non-negative integers, and
    ``admitted <= passed <= mapped <= idea_units`` holds by construction.
    """

    video_id: str
    idea_units: int
    mapped: int
    passed: int
    admitted: int


# ---------------------------------------------------------------------------
# admission_decision
# ---------------------------------------------------------------------------

def admission_decision(
    is_mapped: bool,
    verdict: "SegmentVerdict | None",
    coherent: bool,
    aligned: bool,
) -> AdmissionDecision:
    """Decide whether a single segment is admitted.

    A segment is admitted IF AND ONLY IF all of the following hold:
      1. it was mapped to a Pedagogical_Role (``is_mapped`` is True),
      2. its Segment_Verdict exists and has ``status == 'passing'``,
      3. the assembled sequence passed coherence (``coherent`` is True), and
      4. the assembled sequence passed alignment (``aligned`` is True).

    Otherwise the segment is rejected with the first failing reason, evaluated
    in this priority order: ``unmapped`` -> ``failing_verdict`` -> ``coherence``
    -> ``alignment``.  Unmapped segments and failing/missing verdicts are always
    rejected regardless of coherence or alignment.

    This is a pure, deterministic function.

    Args:
        is_mapped: True if the segment is a Mapped_Segment (Req 4.3).
        verdict: The segment's Segment_Verdict, or None when no verdict was
            produced.  Any verdict whose ``status`` is not ``'passing'`` (and a
            None verdict) is treated as failing (Req 4.1, 4.2).
        coherent: True if the assembled sequence cleared the Coherence_Judge.
        aligned: True if the assembled sequence cleared the Plan_Alignment_Checker.

    Returns:
        An ``AdmissionDecision`` with ``status`` ``'admitted'`` / ``'rejected'``
        and the specific ``reason``.

    Validates: Requirements 2.9, 4.1, 4.2, 4.3
    """
    if not is_mapped:
        return AdmissionDecision(status="rejected", reason="unmapped")

    if verdict is None or getattr(verdict, "status", None) != _PASSING_STATUS:
        return AdmissionDecision(status="rejected", reason="failing_verdict")

    if not coherent:
        return AdmissionDecision(status="rejected", reason="coherence")

    if not aligned:
        return AdmissionDecision(status="rejected", reason="alignment")

    return AdmissionDecision(status="admitted", reason="ok")


# ---------------------------------------------------------------------------
# admit_topic
# ---------------------------------------------------------------------------

def admit_topic(
    mapped: "list[MappedSegment]",
    unmapped: "list[object]",
    verdicts: "dict[str, SegmentVerdict]",
    coherent: bool,
    aligned: bool,
) -> "tuple[list[MappedSegment], list[AdmissionDecision], TopicOutcome]":
    """Apply ``admission_decision`` to every candidate segment for a Topic.

    Candidates are every Mapped_Segment plus every Unmapped_Segment.  Each
    Mapped_Segment is judged against its Segment_Verdict (looked up by its
    atom id in ``verdicts``; a missing entry is treated as a failing verdict),
    the shared ``coherent`` / ``aligned`` flags for the assembled sequence, and
    ``is_mapped=True``.  Every Unmapped_Segment is unconditionally rejected with
    reason ``unmapped`` (Req 4.3).

    Only the subset of Mapped_Segments that each passed coherence, alignment,
    and judging is admitted (Req 4.5).

    The Topic outcome classifies the run over ALL candidates:
      - ``skipped``            -> no candidate was admitted.
      - ``fully_admitted``     -> every candidate was admitted.
      - ``partially_admitted`` -> some but not all candidates were admitted.

    A Topic with no candidates at all is classified ``skipped``.

    This is a pure, deterministic function.

    Args:
        mapped: The Topic's Mapped_Segments.  Each is expected to expose
            ``.atom.id`` for verdict lookup.
        unmapped: The Topic's Unmapped_Segments (set aside, never admitted).
        verdicts: Map of atom id -> Segment_Verdict for the mapped segments.
        coherent: True if the assembled sequence cleared coherence.
        aligned: True if the assembled sequence cleared alignment.

    Returns:
        A 3-tuple ``(admitted, all_decisions, outcome)`` where ``admitted`` is
        the list of admitted Mapped_Segments (in input order), ``all_decisions``
        is the per-candidate decision list (mapped segments first, then unmapped
        segments, each in input order), and ``outcome`` is the ``TopicOutcome``.

    Validates: Requirements 4.5
    """
    admitted: list["MappedSegment"] = []
    all_decisions: list[AdmissionDecision] = []

    # Mapped candidates: gated on verdict + coherence + alignment.
    for segment in mapped:
        atom_id = getattr(getattr(segment, "atom", None), "id", None)
        verdict = verdicts.get(atom_id) if atom_id is not None else None
        decision = admission_decision(True, verdict, coherent, aligned)
        all_decisions.append(decision)
        if decision.status == "admitted":
            admitted.append(segment)

    # Unmapped candidates: always rejected (Req 4.3).
    for _segment in unmapped:
        all_decisions.append(admission_decision(False, None, coherent, aligned))

    total_candidates = len(mapped) + len(unmapped)
    admitted_count = len(admitted)

    if admitted_count == 0:
        outcome: TopicOutcome = "skipped"
    elif admitted_count == total_candidates:
        outcome = "fully_admitted"
    else:
        outcome = "partially_admitted"

    return admitted, all_decisions, outcome


# ---------------------------------------------------------------------------
# funnel_counts
# ---------------------------------------------------------------------------

def _coerce_non_negative_int(value: object) -> int:
    """Coerce *value* to a non-negative int; non-numeric inputs yield 0."""
    try:
        coerced = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return coerced if coerced > 0 else 0


def funnel_counts(
    idea_units: int,
    mapped: int,
    passed: int,
    admitted: int,
    video_id: str,
) -> Funnel:
    """Assemble the four-count decode -> map -> judge -> admit funnel.

    Each count is coerced to a non-negative integer, then the chain is clamped
    downward so that ``admitted <= passed <= mapped <= idea_units`` holds for
    any inputs (the funnel narrows at every stage by construction).  The result
    is therefore always non-negative and monotonic.

    This is a pure, deterministic function.

    Args:
        idea_units: Count of Idea_Units the Deep_Decoder produced.
        mapped: Count of Mapped_Segments.
        passed: Count of segments that passed judging.
        admitted: Count of Admitted_Clips.
        video_id: The Source_Video this funnel describes.

    Returns:
        A ``Funnel`` with non-negative, monotonically non-increasing counts.

    Validates: Requirements 4.6
    """
    n_idea = _coerce_non_negative_int(idea_units)
    n_mapped = min(_coerce_non_negative_int(mapped), n_idea)
    n_passed = min(_coerce_non_negative_int(passed), n_mapped)
    n_admitted = min(_coerce_non_negative_int(admitted), n_passed)

    return Funnel(
        video_id=video_id,
        idea_units=n_idea,
        mapped=n_mapped,
        passed=n_passed,
        admitted=n_admitted,
    )


# ---------------------------------------------------------------------------
# Best-effort I/O shell: persist_admitted
# ---------------------------------------------------------------------------

def _embed_url(video_id: str, start: float) -> str:
    """Build the YouTube embed URL the on-demand pipeline uses for a clip.

    Mirrors ``pipeline.process_video`` exactly: an autoplaying embed seeked to
    the clip's start timestamp. Falls back to a bare embed when ``video_id`` is
    empty so the resulting ``video_url`` is always non-empty.
    """
    safe_start = int(start) if start and start > 0 else 0
    return (
        f"https://www.youtube.com/embed/{video_id}"
        f"?start={safe_start}&autoplay=1&rel=0&modestbranding=1"
    )


def persist_admitted(
    admitted: "list[MappedSegment]",
    topic_slug: str,
    coherence_score: float,
) -> int:
    """Persist each Admitted_Clip with the on-demand pipeline's field set.

    For every admitted Mapped_Segment this builds a ``Clip`` carrying the
    IDENTICAL column set the on-demand pipeline writes (``topic_slug``,
    ``title``, ``description``, ``video_url``, ``thumbnail_url``,
    ``duration_seconds``, ``transcript``, ``source_url``, ``source_platform``,
    ``hook_score``) PLUS the coherence/plan metadata required by Req 4.4 / 7.2,
    all NON-NULL:

      - ``pedagogical_role`` -> the single role the segment was mapped to,
      - ``role_ordinal``     -> its 1-based ordinal in the Planned_Arc,
      - ``concept_label``    -> the covered concept (the atom's concept),
      - ``coherence_score``  -> the topic-level Coherence_Score (mirrored
        per clip, exactly like the on-demand coherence pass in ``topics.py``).

    Each candidate clip is run through ``arc_assembler.validate_clip`` (the same
    normalisation/exclusion gate the on-demand path uses) before insertion; a
    clip the validator rejects is logged and skipped, never stored.

    Best-effort: this shell never raises. A failure obtaining the DB client, a
    per-row insert error, or a malformed segment is logged and swallowed so a
    transient failure degrades gracefully rather than blocking the pipeline.

    Args:
        admitted: The Admitted_Clips' Mapped_Segments (output of ``admit_topic``).
        topic_slug: The Topic these clips belong to.
        coherence_score: The topic-level Coherence_Score to mirror per clip.

    Returns:
        The count of clips actually stored.

    Requirements: 4.4, 7.2
    """
    if not admitted:
        return 0

    try:
        from app.db.supabase import get_client
        from app.models.schemas import Clip
        from app.services import arc_assembler
    except Exception as exc:  # pragma: no cover - import wiring failure
        logger.warning(
            "[admission_gate] persist_admitted: import failed for topic_slug=%s: %s",
            topic_slug,
            exc,
        )
        return 0

    try:
        db = get_client()
    except Exception as exc:
        logger.warning(
            "[admission_gate] persist_admitted: DB client unavailable for "
            "topic_slug=%s: %s",
            topic_slug,
            exc,
        )
        return 0

    stored = 0
    for segment in admitted:
        try:
            atom = getattr(segment, "atom", None)
            if atom is None:
                logger.warning(
                    "[admission_gate] persist_admitted: segment without atom "
                    "for topic_slug=%s; skipping",
                    topic_slug,
                )
                continue

            concept = (atom.concept or "").strip()
            duration = int(atom.end - atom.start) if atom.end > atom.start else 0

            # Build the candidate Clip and run it through the SAME validation
            # gate the on-demand pipeline uses (truncation + exclusion rules).
            candidate = Clip(
                id=str(uuid.uuid4()),
                topic_slug=topic_slug,
                title=concept or segment.pedagogical_role,
                description=concept or None,
                video_url=_embed_url(atom.video_id, atom.start),
                thumbnail_url=(
                    f"https://img.youtube.com/vi/{atom.video_id}/hqdefault.jpg"
                ),
                duration_seconds=duration,
                transcript=atom.transcript,
                source_url=atom.source_url,
                source_platform="youtube",
                hook_score=0.5,
                pedagogical_role=segment.pedagogical_role,
                role_ordinal=segment.role_ordinal,
                concept_label=concept or segment.pedagogical_role,
                coherence_score=coherence_score,
            )

            validated, warning = arc_assembler.validate_clip(candidate)
            if validated is None:
                logger.warning(
                    "[admission_gate] persist_admitted: %s", warning
                )
                continue

            # Insert with the IDENTICAL field set as the on-demand pipeline,
            # plus the required NON-NULL coherence/plan metadata (Req 4.4, 7.2).
            row = {
                "topic_slug": validated.topic_slug,
                "title": validated.title,
                "description": validated.description,
                "video_url": validated.video_url,
                "thumbnail_url": validated.thumbnail_url,
                "duration_seconds": validated.duration_seconds,
                "transcript": validated.transcript,
                "source_url": validated.source_url,
                "source_platform": validated.source_platform,
                "hook_score": validated.hook_score,
                "pedagogical_role": validated.pedagogical_role,
                "role_ordinal": validated.role_ordinal,
                "concept_label": validated.concept_label,
                "coherence_score": validated.coherence_score,
            }

            db.table("clips").insert(row).execute()
            stored += 1
        except Exception as exc:
            logger.warning(
                "[admission_gate] persist_admitted: insert failed for "
                "topic_slug=%s: %s",
                topic_slug,
                exc,
            )

    logger.info(
        "[admission_gate] persist_admitted: stored %d/%d admitted clips for %s",
        stored,
        len(admitted),
        topic_slug,
    )
    return stored
