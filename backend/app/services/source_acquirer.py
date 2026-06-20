"""The Source_Acquirer orchestrator shell for the Content_Provider abstraction.

The Source_Acquirer is the thin best-effort I/O shell that turns a
Provider_Registry snapshot plus live per-provider availability into a bounded,
de-duplicated set of ``SourceItem``s for one Topic, ready to feed the unchanged
shared ``ingest_topic`` decode stage (Req 5.1, 5.3). It owns no decision logic of
its own: every decision is delegated to a pure core --

  - ``source_selection.select_sources`` chooses which enabled, search-capable,
    in-budget, available providers to query and in what order (Req 6.1, 6.5);
  - ``content_provider.gate_invocation`` gates the search op by declared
    capability (Req 1.3, 1.4);
  - ``provider_cost.charge_before_call`` verifies and charges the search
    Cost_Unit BEFORE the call, failing closed on any accounting outage
    (Req 7.1, 7.4);
  - ``content_provider.identity_ok`` validates each returned item's
    provider-identity (Req 1.5, 1.6);
  - ``provider_dedup.dedup`` collapses same-identity items and suppresses
    cross-provider content duplicates (Req 8.2, 8.3).

Around those cores this shell adds only orchestration: it iterates the selected
providers in order, retries a failing search up to its configured maximum (default
3 attempts), records ``(provider_id, reason)`` for any provider that errors or
yields nothing after its retries and continues with the rest (Req 6.4, 9.2), and
NEVER raises and NEVER blocks the request path (Req 9.1, 9.4). A failure in one
provider leaves the others -- and any previously admitted clips -- untouched, with
no rollback (Req 9.4).

Idempotent skip (Req 9.3): when the caller reports that the Topic is already
coherently ingested -- its ingestion status is ``complete`` AND its count of
admitted clips is greater than or equal to the configured target -- acquisition
short-circuits before any provider is selected or charged, so zero Spend_Budget is
consumed from every provider. ``should_skip_acquisition`` is the pure seam the
pipeline uses for that check; ``acquire_sources`` also accepts the same inputs as
optional parameters (defaulting to "do not skip") so it can apply the check itself.

This shell does not run ``embed_reference.normalize``: a Clip's start/end
timestamps and 384-dim embedding are produced later, inside the ``ingest_topic``
decode -> break-down stages, from the transcript fetched on demand. The acquirer's
job is to produce identity-valid, de-duplicated ``SourceItem``s; it records a
normalization failure only for a candidate it must discard here (a provider-
identity mismatch, Req 1.6, or an incomplete provenance that dedup rejects,
Req 8.6).

ASCII only.

Validates: Requirements 5.1, 5.3, 6.4, 9.1, 9.2, 9.3, 9.4
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.services.content_provider import (
    ContentProvider,
    ProviderCapability,
    SourceItem,
    gate_invocation,
    identity_ok,
)
from app.services.provider_cost import charge_before_call
from app.services.provider_dedup import (
    DedupCandidate,
    Provenance,
    dedup,
)
from app.services.provider_registry import Registry
from app.services.source_selection import (
    ProviderAvailability,
    SelectionDefer,
    select_sources,
)

logger = logging.getLogger(__name__)

#: The recorded ingestion status that marks a Topic as fully ingested (Req 9.3).
STATUS_COMPLETE: str = "complete"

#: The default maximum number of attempts for a provider search operation. A
#: provider that errors or returns nothing after this many attempts is recorded
#: as failed and selection continues with the rest (Req 6.4, 9.2).
DEFAULT_RETRIES: int = 3

# Stable reason labels recorded against a provider in ``provider_failures``.
REASON_UNSUPPORTED_SEARCH: str = "unsupported_search"
REASON_NO_PROVIDER_INSTANCE: str = "no_provider_instance"
REASON_CHARGE_REFUSED: str = "charge_refused"
REASON_NO_RESULTS: str = "no_results_after_retries"

# Stable reason labels recorded against a discarded candidate in
# ``normalization_failures``.
REASON_IDENTITY_MISMATCH: str = "provider_identity_mismatch"
REASON_INCOMPLETE_PROVENANCE: str = "incomplete_provenance"


# ---------------------------------------------------------------------------
# Immutable result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionResult:
    """The outcome of one Source_Acquirer run for a Topic.

    Attributes:
        items: The normalized, de-duplicated, transcript-ready ``SourceItem``s to
            feed the unchanged ``ingest_topic`` decode stage, in selection then
            input order (Req 5.1, 5.3). Empty when acquisition was skipped or
            deferred, or when every provider failed.
        defer_reason: The ``SelectionDefer`` classification when no provider was
            eligible (Req 3.5, 6.3, 6.7), else ``None``.
        provider_failures: ``(provider_id, reason)`` for every selected provider
            that was skipped or failed -- an unsupported search, a missing
            provider instance, a refused charge, or an error / empty result after
            its retries (Req 6.4, 9.2).
        normalization_failures: ``(provider_id, reason)`` for every returned
            candidate discarded here -- a provider-identity mismatch (Req 1.6) or
            an incomplete provenance rejected by dedup (Req 8.6). The full Clip
            normalization (timestamps + 384-dim embedding) happens later in the
            shared pipeline, so this list is empty in the common path.
    """

    items: list[SourceItem] = field(default_factory=list)
    defer_reason: SelectionDefer | None = None
    provider_failures: list[tuple[str, str]] = field(default_factory=list)
    normalization_failures: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure core: idempotent-skip decision (Req 9.3)
# ---------------------------------------------------------------------------


def should_skip_acquisition(
    status: str | None,
    admitted_count: int,
    target_count: int,
) -> bool:
    """Return True iff the Topic is already coherently ingested (Req 9.3).

    A Topic is coherently ingested -- and so acquisition must be skipped with zero
    additional Spend_Budget consumed -- exactly when its recorded ingestion
    status is ``complete`` AND its count of admitted Clips is greater than or
    equal to the configured target Clip count.

    Args:
        status: The Topic's recorded ingestion status, or ``None`` when unknown.
        admitted_count: The Topic's current count of admitted Clips.
        target_count: The configured target Clip count for the Topic.

    Returns:
        True iff ``status == 'complete'`` and ``admitted_count >= target_count``.

    Pure and total: depends only on its inputs, performs no I/O.

    Validates: Requirements 9.3
    """
    return status == STATUS_COMPLETE and admitted_count >= target_count


# ---------------------------------------------------------------------------
# The Source_Acquirer shell
# ---------------------------------------------------------------------------


def _search_with_retries(
    provider: ContentProvider,
    query: str,
    max_results: int,
    retries: int,
) -> tuple[list[SourceItem], str | None]:
    """Invoke ``provider.search`` up to ``retries`` times, never raising.

    Returns ``(items, None)`` on the first attempt that yields a non-empty list.
    A provider that raises on every attempt, or returns nothing after all
    attempts, is a failure (Req 6.4): ``([], reason)`` is returned with ``reason``
    the last error text or ``'no_results_after_retries'``. Best-effort: every
    exception is caught and logged so the shell never raises (Req 9.1, 9.4).
    """
    attempts = max(1, retries)
    last_reason = REASON_NO_RESULTS
    for attempt in range(1, attempts + 1):
        try:
            items = provider.search(query, max_results=max_results)
        except Exception as exc:  # best-effort: a provider error never propagates
            last_reason = f"search_error: {exc}"
            logger.warning(
                "[source_acquirer] provider=%s search attempt %s/%s errored: %s",
                provider.provider_id,
                attempt,
                attempts,
                exc,
            )
            continue
        if items:
            return list(items), None
        # An empty result is "no result"; retry, then treat as a failure (Req 6.4).
        last_reason = REASON_NO_RESULTS
    return [], last_reason


def acquire_sources(
    registry: Registry,
    query: str,
    availability: dict[str, ProviderAvailability],
    providers: dict[str, ContentProvider],
    *,
    max_results: int,
    retries: int = DEFAULT_RETRIES,
    status: str | None = None,
    admitted_count: int = 0,
    target_count: int = 0,
    self_charging_providers: frozenset[str] = frozenset(),
    now_utc: datetime | None = None,
) -> AcquisitionResult:
    """Acquire bounded, de-duplicated Source_Items for a Topic, best-effort.

    Orchestration only -- every decision is delegated to a pure core. The steps:

    1. **Idempotent skip (Req 9.3).** If ``should_skip_acquisition(status,
       admitted_count, target_count)`` is true the Topic is already coherently
       ingested: return an empty result immediately, having selected and charged
       no provider so zero Spend_Budget is consumed. The skip inputs default to
       "do not skip" so a caller that has already applied the check can omit them.
    2. **Source_Selection (Req 6.1, 6.5).** Run ``select_sources``; if it defers
       (no enabled / no search-capable / no affordable provider) return an empty
       result carrying the ``defer_reason``.
    3. **Per-provider acquisition.** For each selected Provider_Id in order:
         a. look up its ``ContentProvider`` instance in ``providers`` -- a missing
            instance is recorded and skipped;
         b. gate the search op by declared capability via ``gate_invocation`` --
            an unsupported search is recorded and skipped (Req 1.3, 1.4);
         c. charge the search Cost_Unit BEFORE the call via
            ``charge_before_call`` (``cache_hit=False`` at this layer; the
            provider/cache layer handles reuse) -- a refused charge fails closed,
            is recorded, and the provider is skipped (Req 7.1, 7.4);
         d. invoke ``search`` through the contract with bounded retries -- an
            error or empty result after the retries is recorded and selection
            continues with the rest (Req 6.4, 9.2);
         e. validate each returned item with ``identity_ok``, discarding any
            provider-identity mismatch and recording it (Req 1.5, 1.6).
    4. **Cross-provider dedup (Req 8.2, 8.3).** Collapse same-identity items and
       suppress cross-provider content duplicates via ``dedup``; rejected
       (incomplete-provenance) candidates are recorded. The admitted items are
       returned in order to feed the unchanged ``ingest_topic`` decode stage.

    This shell NEVER raises and NEVER blocks the request path: every provider
    call is wrapped, a failure in one provider leaves the others and any
    previously admitted clips untouched with no rollback (Req 9.1, 9.4), and the
    best result already obtained is always returned (Req 9.2).

    Args:
        registry: The immutable Provider_Registry snapshot.
        query: The Topic search query passed to each provider's ``search``.
        availability: Per-provider live budget/availability inputs keyed by
            Provider_Id, consumed by ``select_sources``.
        providers: The live ``ContentProvider`` instances keyed by Provider_Id.
        max_results: The configured maximum candidate count per provider search.
        retries: The maximum search attempts per provider (default 3, Req 6.4).
        status: The Topic's recorded ingestion status for the idempotent-skip
            check (default ``None`` -> do not skip, Req 9.3).
        admitted_count: The Topic's current admitted-Clip count for the skip check.
        target_count: The configured target Clip count for the skip check.
        self_charging_providers: Provider_Ids whose billable charging is performed
            at the provider's OWN charge site inside its ``search`` (its
            Provider_Cost_Policy IS an external charge site), so the acquirer must
            NOT charge them again via ``charge_before_call`` and duplicate the
            counter. The YouTube provider is the canonical case: its search wraps
            ``youtube.youtube_search``, which charges the Key_Pool before the call,
            so re-charging here would double-spend YouTube quota (Req 7.5). Empty
            by default, so every provider is charged through ``charge_before_call``.
        now_utc: Optional clock injection forwarded to ``charge_before_call``.

    Returns:
        An ``AcquisitionResult`` with the de-duplicated ``SourceItem``s plus any
        deferral, provider failures, and discarded-candidate records.

    Validates: Requirements 5.1, 5.3, 6.4, 9.1, 9.2, 9.3, 9.4
    """
    # 1. Idempotent skip: a coherently ingested Topic charges nothing (Req 9.3).
    if should_skip_acquisition(status, admitted_count, target_count):
        logger.info(
            "[source_acquirer] skipping acquisition: topic already coherently "
            "ingested (status=%s admitted=%s target=%s)",
            status,
            admitted_count,
            target_count,
        )
        return AcquisitionResult()

    provider_failures: list[tuple[str, str]] = []
    normalization_failures: list[tuple[str, str]] = []

    # 2. Source_Selection: which providers to query, in what order (Req 6.1, 6.5).
    selection = select_sources(registry, availability)
    if selection.defer_reason is not None:
        logger.info(
            "[source_acquirer] acquisition deferred: %s", selection.defer_reason
        )
        return AcquisitionResult(defer_reason=selection.defer_reason)

    # 3. Per-provider acquisition, in selection order. Collect identity-valid items.
    candidate_items: list[SourceItem] = []
    for provider_id in selection.order:
        provider = providers.get(provider_id)
        if provider is None:
            # Selected but no live instance available: record and continue (Req 9.2).
            provider_failures.append((provider_id, REASON_NO_PROVIDER_INSTANCE))
            logger.warning(
                "[source_acquirer] no provider instance for selected id=%s",
                provider_id,
            )
            continue

        # a. Gate the search op by declared capability (Req 1.3, 1.4).
        gate_error = gate_invocation(provider.capabilities, ProviderCapability.SEARCH)
        if gate_error is not None:
            provider_failures.append((provider_id, REASON_UNSUPPORTED_SEARCH))
            continue

        # b. Charge the search Cost_Unit BEFORE the call; fail closed (Req 7.1, 7.4).
        #    cache_hit is False at the orchestration layer -- the provider/cache
        #    layer handles reuse-before-spend internally. A provider whose charging
        #    happens at its OWN charge site (``self_charging_providers``, e.g. the
        #    YouTube provider charging the Key_Pool inside ``youtube_search``) is
        #    NOT charged again here, so its quota counter is never duplicated
        #    (Req 7.5).
        if provider_id in self_charging_providers:
            allowed = True
        else:
            try:
                allowed = charge_before_call(
                    provider_id,
                    ProviderCapability.SEARCH,
                    cache_hit=False,
                    now_utc=now_utc,
                )
            except Exception as exc:  # best-effort: accounting never blocks the path
                allowed = False
                logger.warning(
                    "[source_acquirer] charge_before_call errored for provider=%s; "
                    "failing closed: %s",
                    provider_id,
                    exc,
                )
        if not allowed:
            provider_failures.append((provider_id, REASON_CHARGE_REFUSED))
            continue

        # c. Invoke search through the contract with bounded retries (Req 6.4).
        items, reason = _search_with_retries(provider, query, max_results, retries)
        if reason is not None:
            provider_failures.append((provider_id, reason))
            continue

        # d. Validate returned-item provider identity, discarding mismatches
        #    (Req 1.5, 1.6).
        for item in items:
            if identity_ok(item, provider_id):
                candidate_items.append(item)
            else:
                normalization_failures.append((provider_id, REASON_IDENTITY_MISMATCH))

    # 4. Cross-provider deduplication (Req 8.2, 8.3). Each item's Provenance is
    #    derived from its embed reference; no cross-provider content_id is known at
    #    acquisition time, so it is left None (a None content_id never matches).
    candidates = [
        DedupCandidate(
            provenance=Provenance(
                provider_id=item.provider_id,
                external_id=item.embed_ref.external_id,
            ),
            content_id=None,
        )
        for item in candidate_items
    ]
    item_by_candidate = {id(cand): item for cand, item in zip(candidates, candidate_items)}

    outcome = dedup(candidates, already_admitted=[])
    for rejected in outcome.rejected:
        normalization_failures.append(
            (rejected.provenance.provider_id, REASON_INCOMPLETE_PROVENANCE)
        )

    items = [item_by_candidate[id(cand)] for cand in outcome.admit]

    return AcquisitionResult(
        items=items,
        defer_reason=None,
        provider_failures=provider_failures,
        normalization_failures=normalization_failures,
    )
