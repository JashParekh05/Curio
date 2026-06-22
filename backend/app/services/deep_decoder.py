"""Deep_Decoder pure core for the deep-content-ingestion pipeline.

This module holds the *pure* decode break-down decision (Req 9.6): given a
Source_Video transcript it breaks the WHOLE transcript into fine-grained
``IdeaUnit``s that collectively span the entire transcript, in ascending,
non-overlapping start order, with every unit's timestamps in bounds.

Everything in the pure core is deterministic in its inputs: no database, no
clock, no global reads or mutation, matching the pure-core convention
established by ``coherence_budget.py`` and ``self_heal_state.py``. The
best-effort I/O shell (``decode_video``) that fetches the complete transcript
and labels Idea_Units into Learning_Atoms is appended below the pure core: it
performs I/O (transcript fetch, model-backed labeling) but never raises, so a
fetch/decode failure simply yields a skipped ``DecodeResult`` that admits
nothing.

ASCII only.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 9.1, 9.6
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from app.models.schemas import LearningAtom, PlannedArc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdeaUnit:
    """A contiguous transcript span produced by the Deep_Decoder before role
    labeling.

    Invariants (enforced by ``build_idea_units`` and checked by
    ``idea_units_well_formed``): ``0 <= start < end <= transcript duration``.

    Attributes:
        video_id: The Source_Video this span was decoded from.
        start: Span start in seconds; non-negative.
        end: Span end in seconds; strictly greater than ``start``.
        text: The transcript text the span covers.
    """

    video_id: str
    start: float
    end: float
    text: str


def transcript_duration(transcript: list[dict]) -> float:
    """Return the total duration of a transcript in seconds.

    Defined as the last segment's start plus its duration. An empty transcript
    has duration ``0.0``. A malformed last segment (missing/non-numeric
    ``start`` or ``duration``) falls back to ``0.0`` rather than raising.

    Pure: deterministic in its input, no I/O.

    Args:
        transcript: List of transcript segment dicts, each carrying numeric
            ``start`` and ``duration`` fields (the existing
            ``youtube._fetch_transcript`` shape).

    Returns:
        Total transcript duration in seconds; ``0.0`` for an empty transcript.

    Validates: Requirements 1.3
    """
    if not transcript:
        return 0.0
    last = transcript[-1]
    try:
        return float(last["start"]) + float(last["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def build_idea_units(transcript: list[dict], video_id: str) -> list[IdeaUnit]:
    """Break the WHOLE transcript into contiguous, non-overlapping Idea_Units.

    The produced units are in ascending start order and collectively span the
    entire transcript (from the first segment start through the transcript
    duration) rather than a hand-picked subset of spans. Each unit's boundaries
    are derived from consecutive transcript segment start times: a unit runs
    from one segment's start to the next segment's start, and the final unit
    runs to the transcript duration. Segments sharing a start time are merged
    into one unit (their texts are joined).

    Every produced unit satisfies ``0 <= start < end <= duration``, units are
    ordered by ascending start, and no two units overlap in time (adjacent
    units touch only at a shared boundary).

    Pure and deterministic: no I/O, no clock, no global reads. An empty
    transcript (or one that yields no positive-length span) returns ``[]``.

    Args:
        transcript: List of transcript segment dicts with numeric ``start`` /
            ``duration`` and a ``text`` field.
        video_id: The Source_Video id stamped onto every produced unit.

    Returns:
        Idea_Units spanning the transcript, ascending and non-overlapping.

    Validates: Requirements 1.2, 1.3, 1.4
    """
    duration = transcript_duration(transcript)
    if not transcript or duration <= 0.0:
        return []

    # Group transcript text by (normalized) start time. Only starts that can
    # begin a strictly-positive-length span before the transcript end are
    # eligible, which keeps every produced unit in bounds even when the input
    # is unsorted or malformed.
    texts_by_start: dict[float, list[str]] = defaultdict(list)
    for segment in transcript:
        try:
            start = float(segment["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0.0 or start >= duration:
            continue
        text = str(segment.get("text", ""))
        texts_by_start[start].append(text)

    unique_starts = sorted(texts_by_start)
    if not unique_starts:
        return []

    units: list[IdeaUnit] = []
    for index, start in enumerate(unique_starts):
        # A unit ends where the next unit begins; the last unit ends at the
        # transcript duration so the units collectively span the whole video.
        if index + 1 < len(unique_starts):
            end = unique_starts[index + 1]
        else:
            end = duration
        if end <= start:
            continue
        text = " ".join(t for t in texts_by_start[start] if t).strip()
        units.append(IdeaUnit(video_id=video_id, start=start, end=end, text=text))

    return units


def idea_units_well_formed(units: list[IdeaUnit], duration: float) -> bool:
    """Return True iff the Idea_Unit sequence respects every decode invariant.

    Checks that every unit has ``0 <= start < end <= duration`` and that the
    units are in ascending start order with no time overlap (each unit's start
    is at or after the previous unit's end). An empty sequence is trivially
    well-formed.

    Pure: deterministic in its inputs, no I/O.

    Args:
        units: The Idea_Units to validate.
        duration: The transcript duration the units were decoded from.

    Returns:
        True if every invariant holds, False otherwise.

    Validates: Requirements 1.3, 1.4
    """
    previous_end: float | None = None
    for unit in units:
        if not (0.0 <= unit.start < unit.end <= duration):
            return False
        if previous_end is not None and unit.start < previous_end:
            return False
        previous_end = unit.end
    return True


# ---------------------------------------------------------------------------
# Best-effort I/O shell
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodeResult:
    """Outcome of decoding a single Source_Video end-to-end.

    Produced by the best-effort ``decode_video`` shell. ``skipped`` is True when
    the transcript could not be fetched/decoded (Req 1.6); in that case ``atoms``
    and ``idea_units`` are empty and ``reason`` names the cause. A successfully
    fetched transcript that breaks into zero Idea_Units yields
    ``reason='no_idea_units'`` (Req 1.7) with ``skipped`` left False.

    Attributes:
        video_id: The Source_Video that was decoded.
        idea_units: Idea_Units built over the WHOLE transcript (ascending,
            non-overlapping); empty when skipped or when no units were produced.
        atoms: Learning_Atoms labeled and ordered from the transcript via
            ``pipeline.segment_into_atoms``; empty unless decode succeeded and
            produced at least one valid atom.
        skipped: True iff the transcript fetch/decode failed (admit nothing).
        reason: ``'transcript_unavailable'`` | ``'no_idea_units'`` | ``None``.
    """

    video_id: str
    idea_units: list[IdeaUnit]
    atoms: list["LearningAtom"]
    skipped: bool
    reason: str | None


def decode_video(
    video_id: str,
    topic_slug: str,
    planned_arc: "PlannedArc",
    *,
    now_utc: datetime | None = None,
) -> DecodeResult:
    """Decode a Source_Video end-to-end: transcript -> Idea_Units -> Learning_Atoms.

    Best-effort I/O shell around the pure decode core (Req 1.1, 1.5):

    1. Fetch the COMPLETE transcript via ``youtube._fetch_transcript`` (which is
       backed by ``transcript_cache`` so repeated runs over the same video never
       re-pay TranscriptAPI).
    2. Build Idea_Units over the WHOLE transcript with ``build_idea_units``.
    3. Label and order the transcript into ``Learning_Atom``s by reusing
       ``pipeline.segment_into_atoms`` (which calls ``pipeline.order_atoms``
       internally), so per-atom invariants and ordering are enforced once, in
       one place.

    This never raises (Req 9.1). Failure modes:

    - Transcript fetch/decode failure (``None`` or empty) ->
      ``DecodeResult(skipped=True, reason='transcript_unavailable', atoms=[])``
      so the Source_Video is skipped and nothing is admitted (Req 1.6).
    - A transcript that breaks into zero Idea_Units ->
      ``reason='no_idea_units'`` with ``atoms=[]`` (Req 1.7); the video is not
      marked skipped (the transcript was readable) but admits nothing.
    - Any unexpected error during labeling is caught and degraded to an empty
      atom list rather than propagating.

    Args:
        video_id: The Source_Video id to decode.
        topic_slug: The Topic this video is being ingested for (passed through to
            the segmenter for prompt context and atom stamping).
        planned_arc: The Topic's ``Planned_Arc`` (passed through to the
            segmenter so labeling targets the planned roles).
        now_utc: Optional injected clock for callers that thread a deterministic
            time; unused by the decode itself (transcript fetch is time
            independent) but accepted for interface parity with the other shells.

    Returns:
        A ``DecodeResult`` describing the decode outcome. Never raises.

    Validates: Requirements 1.1, 1.5, 1.6, 1.7, 9.1
    """
    # Imported lazily so the pure core stays importable without the heavier
    # I/O / model dependencies these modules pull in (mirrors the lazy import
    # convention in pipeline.process_video).
    try:
        from app.services.youtube import _fetch_transcript
    except Exception as exc:  # pragma: no cover - defensive, import should succeed
        logger.warning(
            "[deep_decoder] could not import transcript fetcher for video=%s: %s",
            video_id,
            exc,
        )
        return DecodeResult(
            video_id=video_id,
            idea_units=[],
            atoms=[],
            skipped=True,
            reason="transcript_unavailable",
        )

    # 1. Fetch the COMPLETE transcript (cache-first). Best-effort: any failure
    #    or an empty transcript means we skip this Source_Video (Req 1.6).
    try:
        transcript = _fetch_transcript(video_id)
    except Exception as exc:
        logger.warning(
            "[deep_decoder] transcript fetch raised for video=%s topic=%s: %s",
            video_id,
            topic_slug,
            exc,
        )
        transcript = None

    if not transcript:
        logger.info(
            "[deep_decoder] transcript unavailable for video=%s topic=%s; skipping",
            video_id,
            topic_slug,
        )
        return DecodeResult(
            video_id=video_id,
            idea_units=[],
            atoms=[],
            skipped=True,
            reason="transcript_unavailable",
        )

    # 2. Break the WHOLE transcript into Idea_Units (pure). An empty result means
    #    the transcript was readable but yielded no spans (Req 1.7): admit
    #    nothing, but do NOT mark the video skipped.
    idea_units = build_idea_units(transcript, video_id)
    if not idea_units:
        logger.info(
            "[deep_decoder] no idea units for video=%s topic=%s; admitting nothing",
            video_id,
            topic_slug,
        )
        return DecodeResult(
            video_id=video_id,
            idea_units=[],
            atoms=[],
            skipped=False,
            reason="no_idea_units",
        )

    # 3. Label + order Idea_Units into Learning_Atoms by reusing the existing
    #    Atomic_Segmenter, which orders via order_atoms internally (Req 1.5, 1.4).
    #    segment_into_atoms is itself best-effort (returns [] on model/parse
    #    failure); we still guard against unexpected errors so decode never
    #    raises (Req 9.1).
    try:
        from app.services.pipeline import segment_into_atoms

        atoms = segment_into_atoms(transcript, topic_slug, planned_arc)
    except Exception as exc:
        logger.warning(
            "[deep_decoder] atom labeling raised for video=%s topic=%s: %s",
            video_id,
            topic_slug,
            exc,
        )
        atoms = []

    return DecodeResult(
        video_id=video_id,
        idea_units=idea_units,
        atoms=atoms,
        skipped=False,
        reason=None,
    )
