"""Pure cross-provider deduplication core for the Content_Provider abstraction.

Every candidate Source_Item carries a ``Provider_Provenance`` (the originating
``provider_id`` plus the provider's ``external_id``) and an optional
cross-provider ``content_id`` that identifies the same underlying content
regardless of which provider surfaced it (for example a normalized title or a
content hash). This module decides, for one Topic, which candidates to admit
given the candidates already admitted for that Topic.

The decision is a single *pure* function over immutable value inputs — no DB
reads, no clock reads, no global mutation — mirroring ``quota_pool.py`` and
``coherence_budget.py``. The thin I/O shell that persists admitted clips and
their provenance lives elsewhere; this module never touches it.

The rules it enforces:

  - A candidate lacking a non-empty ``provider_id`` OR a non-empty
    ``external_id`` is rejected and admits no Clip (Req 8.6).
  - Two candidates resolving to the same ``(provider_id, external_id)`` identity
    are collapsed to a single admit per Topic; the earliest in input order wins
    and the later ones are suppressed in favour of the retained clip (Req 8.2).
  - A candidate whose ``content_id`` matches a clip already admitted for the
    Topic from a *different* provider is suppressed; the previously admitted clip
    is retained and the suppression records BOTH provenances (Req 8.3, 8.4).

The outcome depends ONLY on the provenance values and content identifiers
supplied as inputs — no wall-clock time, no external or stored state, and no
order dependence beyond the documented earliest-wins tie-break — so identical
inputs always yield an identical ``DedupOutcome`` (Req 8.5). ASCII only.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.5
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Immutable value models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """The originating Content_Provider of a candidate or admitted Clip.

    Attributes:
        provider_id: Stable Provider_Id of the source provider. Non-empty for a
            valid, admissible candidate (Req 8.1).
        external_id: The provider's external item identifier. Non-empty for a
            valid, admissible candidate (Req 8.1).
    """

    provider_id: str
    external_id: str


@dataclass(frozen=True)
class DedupCandidate:
    """A candidate Source_Item considered for admission to a Topic.

    Attributes:
        provenance: The candidate's Provider_Provenance.
        content_id: The cross-provider content identity (for example a
            normalized title or content hash), or ``None`` when the candidate
            carries no cross-provider identity. A ``None`` ``content_id`` never
            matches another candidate, so it is never treated as a cross-provider
            duplicate (Req 8.3).
    """

    provenance: Provenance
    content_id: str | None


@dataclass(frozen=True)
class DedupOutcome:
    """The result of a deduplication decision for one Topic.

    Attributes:
        admit: Candidates to admit, in input order, at most one per
            ``(provider_id, external_id)`` identity (Req 8.2).
        suppressed: ``(suppressed_candidate, retained_provenance)`` pairs, in
            input order. A pair is recorded for every candidate dropped in favour
            of an already-retained clip, whether the drop is a same-identity
            collapse (Req 8.2) or a cross-provider content duplicate (Req 8.3),
            and it names the provenance of the retained clip (Req 8.4).
        rejected: Candidates admitting no Clip because they lack a non-empty
            ``provider_id`` or ``external_id`` (Req 8.6), in input order.
    """

    admit: list[DedupCandidate] = field(default_factory=list)
    suppressed: list[tuple[DedupCandidate, Provenance]] = field(default_factory=list)
    rejected: list[DedupCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _has_complete_provenance(candidate: DedupCandidate) -> bool:
    """Return True iff the candidate has a non-empty provider id AND external id.

    A candidate missing either identifier admits no Clip and is rejected
    (Req 8.6). Pure.
    """
    prov = candidate.provenance
    return bool(prov.provider_id) and bool(prov.external_id)


# ---------------------------------------------------------------------------
# The deduplication decision
# ---------------------------------------------------------------------------


def dedup(
    candidates: list[DedupCandidate],
    already_admitted: list[DedupCandidate],
) -> DedupOutcome:
    """Decide which candidates to admit for a Topic, given those already admitted.

    Processing each candidate in input order, the decision applies, in priority
    order:

      1. **Reject incomplete candidates.** A candidate lacking a non-empty
         ``provider_id`` OR ``external_id`` is placed in ``rejected`` and admits
         no Clip (Req 8.6).
      2. **Collapse same-identity duplicates.** A candidate whose
         ``(provider_id, external_id)`` identity has already been admitted — by an
         already-admitted clip or by an earlier candidate in this call — is placed
         in ``suppressed`` paired with the retained clip's provenance. At most one
         Clip is admitted per identity per Topic, and the earliest candidate in
         input order wins the collision (Req 8.2).
      3. **Suppress cross-provider content duplicates.** A candidate whose
         ``content_id`` matches a clip already admitted for the Topic from a
         *different* provider is placed in ``suppressed`` paired with the retained
         clip's provenance; the previously admitted clip is retained (Req 8.3,
         8.4). A ``None`` ``content_id`` never matches, and a match from the *same*
         provider is not a cross-provider duplicate.
      4. **Admit.** Any remaining candidate is placed in ``admit`` and becomes the
         retained owner of its identity and (when present and not already owned)
         its ``content_id``.

    The ``already_admitted`` candidates are the clips already retained for the
    Topic; they seed the retained state but never appear in the returned
    ``admit`` list. The outcome depends only on the provenance values and content
    identifiers provided — no wall-clock time, no external or stored state, and no
    order dependence beyond the earliest-wins tie-break — so identical inputs
    yield an identical outcome (Req 8.5). Pure: neither input list is mutated.

    Args:
        candidates: Candidate Source_Items to consider, in priority order.
        already_admitted: Clips already admitted for the Topic, used as retained
            context. Entries with incomplete provenance are ignored for seeding.

    Returns:
        A ``DedupOutcome`` partitioning the candidates into ``admit``,
        ``suppressed``, and ``rejected``.

    Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.5
    """
    admit: list[DedupCandidate] = []
    suppressed: list[tuple[DedupCandidate, Provenance]] = []
    rejected: list[DedupCandidate] = []

    # Retained state seeded from the already-admitted clips, then extended by
    # each candidate this call admits. ``identity_owner`` maps a
    # (provider_id, external_id) identity to the provenance of the retained clip
    # that owns it; ``content_owner`` maps a content_id to the provenance of the
    # first retained clip that claimed it.
    identity_owner: dict[tuple[str, str], Provenance] = {}
    content_owner: dict[str, Provenance] = {}

    for prior in already_admitted:
        if not _has_complete_provenance(prior):
            continue
        identity = (prior.provenance.provider_id, prior.provenance.external_id)
        if identity not in identity_owner:
            identity_owner[identity] = prior.provenance
        if prior.content_id is not None and prior.content_id not in content_owner:
            content_owner[prior.content_id] = prior.provenance

    for candidate in candidates:
        # 1. Reject candidates with incomplete provenance (Req 8.6).
        if not _has_complete_provenance(candidate):
            rejected.append(candidate)
            continue

        identity = (candidate.provenance.provider_id, candidate.provenance.external_id)

        # 2. Collapse same-identity duplicates to one admit per Topic (Req 8.2).
        if identity in identity_owner:
            suppressed.append((candidate, identity_owner[identity]))
            continue

        # 3. Suppress cross-provider content duplicates (Req 8.3, 8.4).
        content_id = candidate.content_id
        if content_id is not None and content_id in content_owner:
            retained = content_owner[content_id]
            if retained.provider_id != candidate.provenance.provider_id:
                suppressed.append((candidate, retained))
                continue

        # 4. Admit and become the retained owner of this identity / content_id.
        admit.append(candidate)
        identity_owner[identity] = candidate.provenance
        if content_id is not None and content_id not in content_owner:
            content_owner[content_id] = candidate.provenance

    return DedupOutcome(admit=admit, suppressed=suppressed, rejected=rejected)
