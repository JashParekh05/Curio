"""Coverage_View pure decision core (Phase 1, Req 4).

The Coverage_View is the single source of truth for what the library actually
contains. For each non-pruned Topic it reports, in one Catalog_Entry, the
Topic's Canonical_Arc Pedagogical_Roles, the count of Admitted_Clips filling
each role, the role ordinals that are filled by zero clips (the Coverage_Gaps),
whether the Topic is Arc_Complete (zero gaps), and the Topic's Watch_Quality.

Following the repository convention (see ``topic_frontier.py`` /
``reseed_prioritizer.py``), this module holds ONLY the pure decision logic: no
DB, no clock, no global reads. Every function is deterministic in its inputs and
free of side effects -- it derives every Catalog_Entry solely from the supplied
projection and creates, updates, and deletes zero database rows (Req 4.5, 4.8).
The thin, read-only I/O shell that assembles the projection from Supabase lives
in ``coverage_view_store.py``.

Watch_Quality reuses the exact engagement-telemetry Watch_Ratio definition
(``telemetry.watch_ratio``) so the Coverage_View and the telemetry rollups share
one watch-ratio formula (Req 4.6).

ASCII only.

Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.telemetry import watch_ratio

# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

#: Non-numeric sentinel reported for a Topic's Watch_Quality when the Topic has
#: no Watch_Events from which a view-weighted average could be computed. It is
#: distinguishable from every numeric value in the inclusive range [0.0, 1.0]
#: (Req 4.7).
UNDEFINED_WATCH_QUALITY = None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicProjection:
    """The persisted projection the shell supplies, one per Topic.

    Attributes:
        topic_slug: The Topic this projection describes.
        arc: The Topic's Canonical_Arc, or None when the Topic has no arc yet
            (a Structureless_Topic that has not been backfilled).
        clips_per_role: A map from a Canonical_Arc role ordinal to the
            non-negative count of Admitted_Clips filling that role. A role
            absent from the map (or mapped to 0) is filled by zero clips.
        watch_events: The Topic's Watch_Events as ``(watch_ms, duration_seconds)``
            pairs -- exactly the inputs ``telemetry.watch_ratio`` consumes.
        is_pruned: True iff the Topic is a Pruned_Topic; a Pruned_Topic yields
            zero Catalog_Entries (Req 4.2).
    """

    topic_slug: str
    arc: CanonicalArc | None
    clips_per_role: dict[int, int]
    watch_events: list[tuple[int, int]]
    is_pruned: bool


@dataclass(frozen=True)
class CatalogEntry:
    """One Topic's row in the Coverage_View.

    Attributes:
        topic_slug: The Topic this entry reports.
        roles: The Topic's Canonical_Arc Pedagogical_Roles in arc order (Req 4.1).
        clips_per_role: A map from each Canonical_Arc role ordinal to its
            non-negative Admitted_Clip count; every arc role appears, with 0 for
            an unfilled role (Req 4.1).
        coverage_gaps: The role ordinals filled by exactly zero clips, ascending
            (Req 4.3).
        arc_complete: True iff the Topic has an arc with zero Coverage_Gaps
            (Req 4.4).
        watch_quality: The view-weighted average Watch_Ratio in [0.0, 1.0], or
            :data:`UNDEFINED_WATCH_QUALITY` when undefined (Req 4.6, 4.7).
    """

    topic_slug: str
    roles: tuple[CanonicalArcRole, ...]
    clips_per_role: dict[int, int]
    coverage_gaps: tuple[int, ...]
    arc_complete: bool
    watch_quality: float | None


# ---------------------------------------------------------------------------
# Watch_Quality
# ---------------------------------------------------------------------------

def topic_watch_quality(watch_events: list[tuple[int, int]]) -> float | None:
    """View-weighted average Watch_Ratio over a Topic's Watch_Events.

    Each Watch_Event is one view, so the simple mean of the per-event
    Watch_Ratios is the view-weighted average: a Clip with more views
    contributes proportionally more events to the mean. Each event's ratio is
    computed with the shared ``telemetry.watch_ratio`` definition (Req 4.6), and
    since every defined ratio is in [0.0, 1.0] their mean is bounded to the same
    inclusive range.

    Events whose Watch_Ratio is undefined (unknown or non-positive duration)
    contribute nothing to the average, mirroring ``analytics.compute_dropoff``.
    When no event has a defined Watch_Ratio -- in particular when there are zero
    Watch_Events -- the Watch_Quality is undefined and
    :data:`UNDEFINED_WATCH_QUALITY` is returned (Req 4.7).

    Pure and deterministic: identical inputs always yield identical output
    (Req 4.8).

    Args:
        watch_events: ``(watch_ms, duration_seconds)`` pairs for the Topic.

    Returns:
        The bounded view-weighted average Watch_Ratio, or
        :data:`UNDEFINED_WATCH_QUALITY` when undefined.

    Validates: Requirements 4.6, 4.7, 4.8
    """
    ratios: list[float] = []
    for watch_ms, duration_seconds in watch_events:
        ratio = watch_ratio(watch_ms, duration_seconds)
        if ratio is not None:
            ratios.append(ratio)
    if not ratios:
        return UNDEFINED_WATCH_QUALITY
    return sum(ratios) / len(ratios)


# ---------------------------------------------------------------------------
# Catalog_Entry derivation
# ---------------------------------------------------------------------------

def catalog_entry(projection: TopicProjection) -> CatalogEntry | None:
    """Derive one Catalog_Entry from a single Topic's projection.

    Produces exactly one Catalog_Entry for a non-pruned Topic (Req 4.1) and
    None for a Pruned_Topic so it contributes zero entries to the view
    (Req 4.2). The entry reports, for every Canonical_Arc role in arc order, a
    non-negative Admitted_Clip count (Req 4.1); the role ordinals whose count is
    exactly zero are the Coverage_Gaps (Req 4.3); the Topic is Arc_Complete iff
    it has an arc and zero Coverage_Gaps (Req 4.4); and the Watch_Quality is the
    bounded view-weighted average or the undefined sentinel (Req 4.6, 4.7).

    Derives the entry solely from the supplied projection, reading no external
    service and changing no state (Req 4.5, 4.8). Pure and deterministic.

    Args:
        projection: The persisted projection for one Topic.

    Returns:
        The Topic's :class:`CatalogEntry`, or None when the Topic is pruned.

    Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
    """
    if projection.is_pruned:
        return None

    roles: tuple[CanonicalArcRole, ...] = (
        projection.arc.roles if projection.arc is not None else ()
    )

    # One non-negative count per arc role; a role missing from the projection
    # map (or mapped to 0) is filled by zero clips.
    clips_per_role = {
        arc_role.ordinal: max(0, projection.clips_per_role.get(arc_role.ordinal, 0))
        for arc_role in roles
    }

    coverage_gaps = tuple(
        arc_role.ordinal for arc_role in roles if clips_per_role[arc_role.ordinal] == 0
    )

    # Arc_Complete requires an arc with no gaps; a Topic with no Canonical_Arc is
    # not Arc_Complete even though it trivially has zero gaps.
    arc_complete = projection.arc is not None and len(coverage_gaps) == 0

    return CatalogEntry(
        topic_slug=projection.topic_slug,
        roles=roles,
        clips_per_role=clips_per_role,
        coverage_gaps=coverage_gaps,
        arc_complete=arc_complete,
        watch_quality=topic_watch_quality(projection.watch_events),
    )


def coverage_view(projections: list[TopicProjection]) -> list[CatalogEntry]:
    """Derive the whole Coverage_View from the supplied projections.

    Returns one Catalog_Entry per non-pruned Topic projection, in input order,
    and zero entries for every Pruned_Topic (Req 4.1, 4.2). Every entry is
    derived solely from the supplied projection -- this function creates zero
    rows, updates zero rows, and deletes zero rows in any database (Req 4.5).
    Pure and deterministic: two invocations on identical projections produce
    identical Catalog_Entries, Coverage_Gaps, and Arc_Complete results
    (Req 4.8).

    Args:
        projections: The per-Topic persisted projections supplied by the shell.

    Returns:
        The list of Catalog_Entries, one per non-pruned Topic.

    Validates: Requirements 4.1, 4.2, 4.5, 4.8
    """
    entries: list[CatalogEntry] = []
    for projection in projections:
        entry = catalog_entry(projection)
        if entry is not None:
            entries.append(entry)
    return entries
