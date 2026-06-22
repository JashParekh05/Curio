"""Ingestion_Pipeline orchestrator: the shared DECODE -> MAP -> JUDGE -> ADMIT path.

This is the thin best-effort orchestrator shell that ties together the already
implemented component modules into the single ingestion path both the on-demand
``run_pipeline`` (``pipeline_agent``) and the cold-start ``Seeding_Worker``
(``scripts/seeding_worker.py``) invoke. It REUSES the components rather than
reimplementing any of them:

  - DECODE  : ``deep_decoder.decode_video``   (transcript -> Idea_Units -> atoms)
  - MAP     : ``segment_mapper.map_and_assemble`` (atoms -> Mapped/Unmapped +
              coherence/alignment evaluation)
  - JUDGE   : ``segment_judge.judge_segment``  (per Mapped_Segment verdict)
  - ADMIT   : ``admission_gate.admit_topic`` + ``admission_gate.persist_admitted``

It also:
  - ensures the Topic's ``Planned_Arc`` via ``section_planner.plan_and_store_arc``,
  - routes ALL YouTube spend through the single charge site
    ``youtube.youtube_search`` (cache-first, charge-before-call); when it returns
    ``None`` the run is DEFERRED with the Key_Pool counters left untouched,
  - enforces the ``Model_Call_Budget`` via ``ingestion_budget.within_model_budget``,
  - skips Topics already coherently ingested (``ingestion_store.topic_already_ingested``)
    for zero model calls and zero Quota_Cost,
  - drives ``Fast_Preview`` -> ``Full_Processing`` staging
    (``ingestion_state.next_stage``), making at least one candidate feed-eligible
    quickly and then reconciling via ``ingestion_store.set_feed_eligibility``,
  - records the per Source_Video decode->map->judge->admit funnel via
    ``admission_gate.funnel_counts`` + ``ingestion_store.record_funnel``.

Best-effort, never blocks the request path: every per-video step is wrapped so a
transient failure is logged and the loop continues; ``ingest_topic`` never raises
(Req 9.1, 9.5).

This module deliberately does NOT wire ``run_pipeline`` / ``Seeding_Worker`` to
itself -- that is tasks 11.1 / 11.2.

ASCII only.

Validates: Requirements 5.1, 6.1, 6.3, 6.4, 6.5, 6.6, 7.4, 7.5, 8.1-8.8, 9.1,
9.3, 9.4, 9.5
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services import (
    admission_gate,
    deep_decoder,
    ingestion_store,
    segment_judge,
    segment_mapper,
    source_acquirer,
    youtube,
)
from app.services.admission_gate import Funnel, TopicOutcome
from app.services.ingestion_budget import (
    validate_model_budget,
    within_model_budget,
)
from app.services.ingestion_state import (
    DEFAULT_FAST_PREVIEW_LIMIT,
    DEFAULT_MODEL_CALL_BUDGET,
    next_stage,
    validate_fast_preview_limit,
)
from app.services.provider_registry import ProviderRecord, Registry
from app.services.segment_judge import (
    DEFAULT_QUALITY_THRESHOLD,
    validate_quality_threshold,
)
from app.services.source_selection import ProviderAvailability

logger = logging.getLogger(__name__)

# The outcome value used when a run is deferred for Key_Pool reasons (Req 8.6, 8.7).
_DEFERRED: Literal["deferred"] = "deferred"

# Deferral reasons surfaced when youtube_search declines to spend (Req 8.6, 8.7).
_REASON_INSUFFICIENT_QUOTA = "insufficient_quota"
_REASON_EMPTY_KEY_POOL = "empty_key_pool"

#: Maximum candidate Source_Items requested per provider search. Comfortably
#: above the count ``youtube.youtube_search`` returns (<=6) so the YouTube-only
#: default truncates nothing and processes every returned Source_Video.
DEFAULT_MAX_SOURCE_ITEMS = 10


@dataclass(frozen=True)
class IngestionSummary:
    """The result of an ``ingest_topic`` run for one Topic.

    Attributes:
        topic_slug:      The Topic that was ingested.
        outcome:         A ``TopicOutcome`` (``fully_admitted`` /
            ``partially_admitted`` / ``skipped``) classifying admission across the
            Topic's Source_Videos, or ``'deferred'`` when no YouTube call could be
            afforded (Req 8.6, 8.7).
        funnels:         The per Source_Video decode->map->judge->admit funnels
            recorded during the run (Req 4.6).
        stored:          Total count of Admitted_Clips persisted this run.
        deferred_reason: ``'insufficient_quota'`` / ``'empty_key_pool'`` when
            ``outcome == 'deferred'``; otherwise None.
    """

    topic_slug: str
    outcome: "TopicOutcome | Literal['deferred']"
    funnels: list[Funnel]
    stored: int
    deferred_reason: str | None


def _video_id_of(video: object) -> str | None:
    """Best-effort extract a ``video_id`` from a youtube_search result item."""
    if isinstance(video, dict):
        vid = video.get("video_id")
        if isinstance(vid, str) and vid:
            return vid
    return None


def _sequence_flags(outcome: "segment_mapper.AssemblyOutcome") -> tuple[bool, bool]:
    """Derive the (coherent, aligned) flags fed to the Admission_Gate.

    The assembled sequence is treated as coherent only when a Planned_Arc was
    present AND the coherence repair loop left no unresolved defect, and as
    aligned only when the Plan_Alignment_Checker reports it aligned with nothing
    unresolved. Any unresolved coherence/alignment defect therefore withholds
    admission of every Mapped_Segment in the sequence (Req 2.9).
    """
    coherent = (not outcome.arc_missing) and not outcome.coherence.defects
    aligned = bool(outcome.alignment.aligned) and not outcome.alignment.unresolved
    return coherent, aligned


def _collect_topic_clip_ids(topic_slug: str, limit: int | None = None) -> set[str]:
    """Best-effort read of the Topic's persisted clip ids (newest first).

    The ``persist_admitted`` shell returns only a count, so the orchestrator
    reads back the ids it needs to reconcile feed-eligibility via
    ``set_feed_eligibility``. Since this path persists ONLY Admitted_Clips, the
    Topic's clip ids are exactly the set that should remain feed-eligible
    (Req 6.4, 6.6). On any failure an empty set is returned.
    """
    try:
        from app.db.supabase import get_client

        query = (
            get_client()
            .table("clips")
            .select("id")
            .eq("topic_slug", topic_slug)
            .order("created_at", desc=True)
        )
        if limit is not None and limit > 0:
            query = query.limit(limit)
        res = query.execute()
        return {row["id"] for row in (res.data or []) if row.get("id")}
    except Exception as exc:
        logger.warning(
            "[ingestion_pipeline] could not read clip ids for topic '%s': %s",
            topic_slug,
            exc,
        )
        return set()


def _defer_reason() -> str:
    """Classify why source acquisition declined to spend (Req 8.6, 8.7).

    An empty Operator-provisioned Key_Pool -> ``'empty_key_pool'``; otherwise the
    pool has keys but none can currently afford the call -> ``'insufficient_quota'``.
    Either way ``youtube_search`` left the Key_Pool counters unchanged.
    """
    try:
        from app.services.quota_store import configured_projects

        if not configured_projects():
            return _REASON_EMPTY_KEY_POOL
    except Exception as exc:
        logger.warning(
            "[ingestion_pipeline] could not inspect Key_Pool config: %s", exc
        )
    return _REASON_INSUFFICIENT_QUOTA


def _acquire_source_items(
    topic_name: str,
    *,
    now_utc: datetime | None = None,
) -> source_acquirer.AcquisitionResult:
    """Acquire Source_Items for a Topic through the Content_Provider abstraction.

    Routes source acquisition through ``source_acquirer.acquire_sources`` instead
    of calling ``youtube.youtube_search`` directly (Req 5.1, 5.2). The default
    Provider_Registry contains exactly one registered, enabled provider -- the
    behavior-preserving ``YouTubeProvider`` -- so with YouTube as the only enabled
    provider the acquirer's single call path is the YouTube provider wrapping
    ``youtube.youtube_search`` and today's behavior is preserved:

      - the YouTube provider is marked available with a budget at its Key_Pool
        ``Spend_Budget``, so Source_Selection always selects it; the actual quota
        fail-closed still happens at the single charge site inside
        ``youtube_search`` (cache-first, charge-before-call against the Key_Pool);
      - ``youtube`` is listed as a ``self_charging_provider`` so the acquirer does
        NOT charge it again via ``charge_before_call`` and never duplicates the
        Key_Pool counter (Req 7.5);
      - ``retries`` is 1 because ``youtube_search`` already fails over across
        Google Cloud projects internally, so the acquirer must not re-invoke it
        and re-charge a successful-but-empty search.

    The returned ``Source_Item``s carry ``provider_id == 'youtube'`` and an
    ``Embed_Reference`` whose ``external_id`` is the video id; the unchanged
    ``ingest_topic`` decode stage consumes them by id. Best-effort:
    ``acquire_sources`` never raises.

    Args:
        topic_name: The human-readable Topic name used as the provider search query.
        now_utc: Optional injected clock threaded to the provider charge site.

    Returns:
        The ``AcquisitionResult`` with normalized, de-duplicated Source_Items.

    Validates: Requirements 5.1, 5.2, 5.3, 8.1
    """
    from app.providers.youtube_provider import YouTubeProvider

    provider = YouTubeProvider()
    record = ProviderRecord(
        provider_id=provider.provider_id,
        enabled=True,
        capabilities=provider.capabilities,
        cost_policy=provider.cost_policy,
    )
    registry = Registry(records=(record,))
    providers = {provider.provider_id: provider}
    availability = {
        provider.provider_id: ProviderAvailability(
            provider_id=provider.provider_id,
            remaining_budget=provider.cost_policy.spend_budget,
            available=True,
        )
    }

    return source_acquirer.acquire_sources(
        registry,
        topic_name,
        availability,
        providers,
        max_results=DEFAULT_MAX_SOURCE_ITEMS,
        retries=1,
        self_charging_providers=frozenset({provider.provider_id}),
        now_utc=now_utc,
    )


def ingest_topic(
    topic_slug: str,
    topic_name: str,
    *,
    quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
    fast_preview_limit: int = DEFAULT_FAST_PREVIEW_LIMIT,
    model_call_budget: int = DEFAULT_MODEL_CALL_BUDGET,
    level: str | None = None,
    now_utc: datetime | None = None,
) -> IngestionSummary:
    """Orchestrate DECODE -> MAP -> JUDGE -> ADMIT for one Topic.

    Steps:
      1. Skip Topics already coherently ingested (``topic_already_ingested``) for
         zero model calls and zero Quota_Cost (Req 7.4, 7.5, 9.4).
      2. Ensure the Topic's ``Planned_Arc`` via ``plan_and_store_arc`` (Req 2.2).
      3. Select Source_Videos via ``youtube.youtube_search`` (cache-first,
         charge-before-call). When it returns ``None``, DEFER with the Key_Pool
         counters left unchanged (Req 8.1-8.8).
      4. Per Source_Video: ``decode_video`` -> ``map_and_assemble`` ->
         ``judge_segment`` per Mapped_Segment -> ``admit_topic`` +
         ``persist_admitted``; record the funnel (Req 1.x-4.x).
      5. Enforce the ``Model_Call_Budget`` via ``within_model_budget`` -- once
         reached, stop issuing model calls and finalize with the best result
         (Req 9.3).
      6. Drive ``Fast_Preview`` -> ``Full_Processing`` staging: make at least one
         candidate feed-eligible quickly, then reconcile via
         ``set_feed_eligibility`` so only Admitted_Clips remain feed-eligible
         (Req 6.1, 6.3, 6.4, 6.5, 6.6).
      7. On a per-video failure, log and continue; never raises (Req 9.1, 9.5).

    Args:
        topic_slug:         The Topic identity.
        topic_name:         The human-readable Topic name (search query + arc).
        quality_threshold:  Per-segment ``Quality_Threshold`` (clamped to [0,1]).
        fast_preview_limit: Max candidate clips made feed-eligible during
            Fast_Preview (clamped to [1,10]).
        model_call_budget:  Max model calls per Topic per cycle (clamped to
            [1,100]).
        level:              Optional curriculum Level (one of
            ``level_filter.LEVELS``) this Topic is being ingested for. When
            supplied and recognized, each admitted clip is tagged with the
            resolved Content_Level on the nullable ``clips.level`` column via
            ``admission_gate.persist_admitted``; when ``None`` / unrecognized,
            ``clips.level`` is left NULL and the read path defers to the Topic's
            difficulty (behavior-preserving for existing callers).
        now_utc:            Optional injected clock threaded to the Key_Pool /
            transcript fetch for deterministic callers.

    Returns:
        An ``IngestionSummary`` describing the run. Never raises.
    """
    funnels: list[Funnel] = []

    try:
        # Validate configuration (pure, total). Out-of-range values fall back to
        # their documented defaults (Req 6.2, 6.8, 9.2, 9.3, 3.6).
        threshold = validate_quality_threshold(quality_threshold)
        budget = validate_model_budget(model_call_budget)
        preview_limit, _ = validate_fast_preview_limit(fast_preview_limit)

        # 1. Idempotent skip: a coherently ingested Topic costs zero model calls
        #    and zero Quota_Cost, leaving stored clips unchanged (Req 7.4, 7.5, 9.4).
        if ingestion_store.topic_already_ingested(topic_slug):
            logger.info(
                "[ingestion_pipeline] topic '%s' already ingested; skipping "
                "(0 model calls, 0 quota)",
                topic_slug,
            )
            return IngestionSummary(
                topic_slug=topic_slug,
                outcome="skipped",
                funnels=[],
                stored=0,
                deferred_reason=None,
            )

        # 2. Ensure the Planned_Arc exists before any mapping (Req 2.2). Cached
        #    after first call. Best-effort: a failure leaves planned_arc None and
        #    map_and_assemble will record a missing-arc defect (Req 2.3).
        planned_arc = None
        try:
            from app.agents.section_planner import plan_and_store_arc

            planned_arc = plan_and_store_arc(topic_slug, topic_name)
        except Exception as exc:
            logger.warning(
                "[ingestion_pipeline] plan_and_store_arc failed for '%s': %s",
                topic_slug,
                exc,
            )

        # 3. Acquire Source_Items through the Content_Provider abstraction.
        #    Source acquisition now routes through source_acquirer.acquire_sources
        #    rather than calling youtube.youtube_search directly, so NO
        #    YouTube-specific call remains in the pipeline (Req 5.1, 5.2). With
        #    YouTube as the only enabled provider the acquirer's single call path
        #    is the YouTubeProvider wrapping youtube.youtube_search, so behavior is
        #    preserved: cache-first, charge-before-call at the single Key_Pool
        #    charge site, and a deferral when nothing is affordable. The resulting
        #    transcript-ready Source_Items feed the unchanged decode stage by id;
        #    nothing is admitted by any path that bypasses this pipeline (Req 5.3).
        acquisition = _acquire_source_items(topic_name, now_utc=now_utc)
        videos = [
            {"video_id": item.embed_ref.external_id, **item.metadata}
            for item in acquisition.items
        ]

        # Provider_Provenance map (Req 8.1): external item id -> originating
        # Provider_Id, derived from the acquired Source_Items. Passed to
        # persist_admitted so every admitted Clip carries its provenance. For the
        # YouTube-only path every entry is 'youtube'.
        provenance_by_external_id = {
            item.embed_ref.external_id: item.provider_id
            for item in acquisition.items
        }

        if not videos:
            # No Source_Items: either a Source_Selection-level deferral (no
            # enabled / no search-capable / insufficient provider budget) or the
            # YouTube charge site declined to spend / returned no candidates.
            # Either way nothing is admitted and the Key_Pool counters are
            # untouched; classify the deferral reason (Req 3.5, 6.3, 6.7, 8.6, 8.7).
            reason = (
                acquisition.defer_reason
                if acquisition.defer_reason is not None
                else _defer_reason()
            )
            logger.info(
                "[ingestion_pipeline] ingestion deferred for '%s': %s",
                topic_slug,
                reason,
            )
            return IngestionSummary(
                topic_slug=topic_slug,
                outcome=_DEFERRED,
                funnels=funnels,
                stored=0,
                deferred_reason=reason,
            )

        # 4-6. Per Source_Video pipeline, budget-bounded, with Fast_Preview ->
        #      Full_Processing staging.
        calls_made = 0
        total_stored = 0
        total_candidates = 0
        total_admitted = 0
        stage = "fast_preview"

        for video in videos:
            video_id = _video_id_of(video)
            if video_id is None:
                logger.warning(
                    "[ingestion_pipeline] skipping malformed search item for '%s'",
                    topic_slug,
                )
                continue

            # Budget gate before DECODE (the decode shell issues one model call
            # to label atoms). Once the budget is reached we stop issuing model
            # calls and finalize with the best result so far (Req 9.3).
            if not within_model_budget(calls_made, budget):
                logger.info(
                    "[ingestion_pipeline] model-call budget reached for '%s' "
                    "(%d/%d); finalizing",
                    topic_slug,
                    calls_made,
                    budget,
                )
                break

            try:
                # DECODE: transcript -> Idea_Units -> Learning_Atoms.
                decode = deep_decoder.decode_video(
                    video_id, topic_slug, planned_arc, now_utc=now_utc
                )
                calls_made += 1

                if decode.skipped or not decode.atoms:
                    # Transcript unavailable (Req 1.6) or no atoms / no idea units
                    # (Req 1.7): admit nothing for this video, record the funnel.
                    funnel = admission_gate.funnel_counts(
                        len(decode.idea_units), 0, 0, 0, video_id
                    )
                    funnels.append(funnel)
                    ingestion_store.record_funnel(funnel, topic_slug)
                    continue

                # MAP: partition atoms onto the arc, assemble, and evaluate
                #      coherence + alignment before any admission (Req 2.x).
                assembly = segment_mapper.map_and_assemble(decode.atoms, planned_arc)
                mapped = assembly.mapping.mapped
                unmapped = assembly.mapping.unmapped
                coherent, aligned = _sequence_flags(assembly)
                coherence_score = assembly.coherence.coherence_score

                # JUDGE: a per-segment verdict for each Mapped_Segment, bounded
                #        by the remaining model-call budget (Req 3.x, 9.3).
                verdicts: dict[str, segment_judge.SegmentVerdict] = {}
                passed = 0
                for segment in mapped:
                    if not within_model_budget(calls_made, budget):
                        logger.info(
                            "[ingestion_pipeline] model-call budget reached "
                            "mid-judging for '%s' (%d/%d)",
                            topic_slug,
                            calls_made,
                            budget,
                        )
                        break
                    verdict = segment_judge.judge_segment(segment, threshold)
                    calls_made += 1
                    verdicts[segment.atom.id] = verdict
                    if verdict.status == "passing":
                        passed += 1

                # ADMIT: admit only mapped + passing + coherent + aligned.
                admitted, _decisions, _video_outcome = admission_gate.admit_topic(
                    mapped, unmapped, verdicts, coherent, aligned
                )
                stored = admission_gate.persist_admitted(
                    admitted,
                    topic_slug,
                    coherence_score,
                    provenance_by_external_id,
                    level=level,
                )

                total_stored += stored
                total_admitted += len(admitted)
                total_candidates += len(mapped) + len(unmapped)

                # Record the four-count decode->map->judge->admit funnel (Req 4.6).
                funnel = admission_gate.funnel_counts(
                    len(decode.idea_units),
                    len(mapped),
                    passed,
                    len(admitted),
                    video_id,
                )
                funnels.append(funnel)
                ingestion_store.record_funnel(funnel, topic_slug)

                # Fast_Preview: as soon as the first video yields admitted clips,
                # make a small number feed-eligible right away (Req 6.2, 6.6),
                # then advance to Full_Processing for the remaining videos
                # (Req 6.1, 6.3).
                if stage == "fast_preview" and stored > 0:
                    preview_ids = _collect_topic_clip_ids(topic_slug, preview_limit)
                    if preview_ids:
                        ingestion_store.set_feed_eligibility(topic_slug, preview_ids)
                    stage = next_stage(stage)

            except Exception as exc:
                # Per-video isolation: log and continue, never raise (Req 9.1, 9.5).
                logger.warning(
                    "[ingestion_pipeline] video '%s' failed for topic '%s': %s",
                    video_id,
                    topic_slug,
                    exc,
                )
                continue

        # 6 (reconcile). After Full_Processing, reconcile feed-eligibility so only
        #    Admitted_Clips remain feed-eligible and un-admitted previews are
        #    dropped (Req 6.4, 6.5). Skip reconciliation entirely when nothing
        #    was admitted so existing previews are retained for retry (Req 6.7).
        if total_stored > 0:
            admitted_ids = _collect_topic_clip_ids(topic_slug)
            ingestion_store.set_feed_eligibility(topic_slug, admitted_ids)

        # Classify the Topic-level outcome across all processed Source_Videos.
        if total_admitted == 0:
            outcome: "TopicOutcome" = "skipped"
        elif total_candidates > 0 and total_admitted == total_candidates:
            outcome = "fully_admitted"
        else:
            outcome = "partially_admitted"

        logger.info(
            "[ingestion_pipeline] topic '%s' done: outcome=%s stored=%d "
            "calls=%d/%d videos=%d",
            topic_slug,
            outcome,
            total_stored,
            calls_made,
            budget,
            len(funnels),
        )
        return IngestionSummary(
            topic_slug=topic_slug,
            outcome=outcome,
            funnels=funnels,
            stored=total_stored,
            deferred_reason=None,
        )

    except Exception as exc:
        # Top-level safety net: ingest_topic never raises (Req 9.1, 9.5).
        logger.warning(
            "[ingestion_pipeline] ingest_topic('%s') failed unexpectedly: %s",
            topic_slug,
            exc,
        )
        return IngestionSummary(
            topic_slug=topic_slug,
            outcome="skipped",
            funnels=funnels,
            stored=0,
            deferred_reason=None,
        )
