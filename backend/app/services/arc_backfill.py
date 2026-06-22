"""Arc_Backfill + pruning pure decision core (Phase 2, Req 3).

Curio commits to the arc model: every Topic a learner is served should have a
real, populated teaching arc rather than a loose bag of clips. This module holds
the correctness-critical *decisions* that drive that commitment:

- whether a Topic is Arc_Complete (every Canonical_Arc role filled by at least
  one Admitted_Clip),
- whether a Topic is a Structureless_Topic (neither structure model present),
- whether a Topic is a Watched_Topic (any Impression or Watch_Event recorded),
- whether an Arc_Backfill should be enqueued for a routed-to Topic, and
- whether a Topic should be pruned (structureless and unwatched).

Following the repository convention (see ``arc_unifier.py`` /
``reseed_prioritizer.py`` / ``coverage_view.py``), this module holds ONLY the
pure decision logic: no DB, no clock, no global reads. Every function is total
and deterministic in its inputs, free of side effects, and never raises -- it
reads only the supplied :class:`TopicState` and returns a value (Req 3.11). The
thin, best-effort I/O shell that derives a ``TopicState`` from Supabase, enqueues
the backfill through the shared Ingestion_Pipeline, and performs the reversible
prune/restore lives in ``arc_backfill_runner.py``.

ASCII only.

Validates: Requirements 3.4, 3.5, 3.7, 3.8, 3.9, 3.11
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.arc_unifier import CanonicalArc

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicState:
    """The persisted state of one Topic the decision core reasons over.

    Every field is supplied by the shell from a read-only projection; the core
    never reads anything else. ``clips_per_role`` is treated as immutable input
    and is never mutated.

    Attributes:
        topic_slug: The Topic this state describes.
        arc: The Topic's Canonical_Arc, or None when the Topic has no arc yet
            (it has not been unified / backfilled into an arc).
        clips_per_role: A map from a Canonical_Arc role ordinal to the
            non-negative count of Admitted_Clips filling that role. A role
            absent from the map (or mapped to <= 0) is filled by zero clips.
        has_legacy_or_role_model: True iff the Topic has a Legacy_Section_Model
            row or a Role_Arc_Model row; when False the Topic is a
            Structureless_Topic (Req 3.11).
        has_impression_or_watch: True iff the Topic has at least one Impression
            or at least one Watch_Event recorded against its Clips; when True the
            Topic is a Watched_Topic (Req 3.11).
        backfill_in_flight: True iff an Arc_Backfill is currently enqueued or in
            progress for the Topic, so no second one should be enqueued (Req 3.1,
            3.8).
    """

    topic_slug: str
    arc: CanonicalArc | None
    clips_per_role: dict[int, int]
    has_legacy_or_role_model: bool
    has_impression_or_watch: bool
    backfill_in_flight: bool


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def is_arc_complete(state: TopicState) -> bool:
    """True iff the Topic has an arc and every role ordinal is filled.

    A Topic is Arc_Complete exactly when it has a Canonical_Arc with at least one
    role and every one of that arc's role ordinals is filled by at least one
    Admitted_Clip (Req 3.4). A Topic with no arc is never Arc_Complete, and an
    arc that leaves any role at zero clips is not Arc_Complete, so such a Topic
    stays eligible for a future Arc_Backfill (Req 3.9). Pure and total: identical
    states always yield identical results, and it never raises (Req 3.11).

    Args:
        state: The Topic's persisted state.

    Returns:
        True iff the Topic is Arc_Complete.

    Validates: Requirements 3.4, 3.9, 3.11
    """
    arc = state.arc
    if arc is None or not arc.roles:
        return False
    return all(
        state.clips_per_role.get(role.ordinal, 0) >= 1 for role in arc.roles
    )


def is_structureless(state: TopicState) -> bool:
    """True iff the Topic has neither a Legacy_Section_Model nor a Role_Arc_Model.

    A Structureless_Topic is one with no structure model of either kind; it is
    the loose bag of clips the feature sets out to either backfill (when watched)
    or prune (when unwatched). Pure and total (Req 3.11).

    Args:
        state: The Topic's persisted state.

    Returns:
        True iff the Topic is a Structureless_Topic.

    Validates: Requirements 3.11
    """
    return not state.has_legacy_or_role_model


def is_watched(state: TopicState) -> bool:
    """True iff the Topic has any Impression or any Watch_Event.

    A Watched_Topic has at least one Impression or at least one Watch_Event
    recorded against its Clips; an Unwatched_Topic has zero of both. Pure and
    total (Req 3.11).

    Args:
        state: The Topic's persisted state.

    Returns:
        True iff the Topic is a Watched_Topic.

    Validates: Requirements 3.11
    """
    return state.has_impression_or_watch


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def should_enqueue_backfill(state: TopicState) -> bool:
    """True when a routed-to Topic needs an Arc_Backfill and none is in flight.

    An Arc_Backfill is enqueued exactly when the Topic is not Arc_Complete and no
    Arc_Backfill is currently enqueued or in progress for it (Req 3.1). This
    covers both an incomplete-arc Topic (arc present with a Coverage_Gap) and a
    Structureless_Topic that is also a Watched_Topic, which is retained and
    backfilled on its next route rather than pruned (Req 3.8). Pure and total:
    identical states always yield identical results (Req 3.11).

    The decision intentionally depends only on Arc_Complete-ness and the
    in-flight flag; the shell is responsible for choosing to prune an
    unwatched Structureless_Topic instead of backfilling it (see
    :func:`should_prune`).

    Args:
        state: The Topic's persisted state at route time.

    Returns:
        True iff an Arc_Backfill should be enqueued for the Topic.

    Validates: Requirements 3.1, 3.8, 3.11
    """
    return (not is_arc_complete(state)) and (not state.backfill_in_flight)


def should_prune(state: TopicState) -> bool:
    """True iff the Topic is both a Structureless_Topic and an Unwatched_Topic.

    A Topic is pruned exactly when it has no structure model and has never been
    watched (Req 3.5). A Structureless_Topic that has been watched is always
    retained, never pruned, so a watched Topic can never be pruned regardless of
    its structure (Req 3.7). Pure and total (Req 3.11).

    Args:
        state: The Topic's persisted state.

    Returns:
        True iff the Topic should be pruned.

    Validates: Requirements 3.5, 3.7, 3.11
    """
    return is_structureless(state) and not is_watched(state)
