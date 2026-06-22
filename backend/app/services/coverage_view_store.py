"""Coverage_View persistence: thin, read-only I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``coverage_view.py``. Mirroring the convention of ``arc_unifier_store.py`` and
``analytics.topic_dropoff`` -- and the design's "pure decision core, thin I/O
shell" principle -- it holds ONLY the Supabase reads that assemble a
:class:`~app.services.coverage_view.TopicProjection` for each Topic, then hands
them to the pure :func:`~app.services.coverage_view.coverage_view` to derive the
Catalog_Entries. Every coverage decision lives in ``coverage_view``; this module
decides nothing.

It is strictly **read-only** (Req 4.5): it issues only ``select`` queries and
creates, updates, and deletes zero rows. It powers an operator coverage endpoint.

A projection is assembled from three reads:

- the ``canonical_arc`` table -> the Topic's :class:`CanonicalArc`;
- a ``clips`` read grouped by ``role_ordinal`` -> the per-role Admitted_Clip
  counts; and
- a ``clip_events`` read joined to ``clips.duration_seconds`` -> the Watch_Events
  as ``(watch_ms, duration_seconds)`` pairs, exactly the inputs the shared
  ``telemetry.watch_ratio`` definition consumes (the pure core applies it).

The ``canonical_arc`` table and the ``topics.archived`` Pruned_Topic flag are
both introduced by the Phase 4 additive migration (``migration_content_revamp.sql``);
this shell is written to use them and degrades gracefully (logs and returns a
safe default) when they are not yet present, exactly like the other best-effort
shells -- so a coverage pass can never block or crash the request path.

ASCII only.

Validates: Requirements 4.1, 4.5, 4.6
"""
from __future__ import annotations

import logging

from app.db.supabase import get_client
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.coverage_view import (
    CatalogEntry,
    TopicProjection,
    coverage_view,
)

logger = logging.getLogger(__name__)

# The Supabase table backing the Canonical_Arc (see migration_content_revamp.sql).
_CANONICAL_ARC_TABLE = "canonical_arc"


# ---------------------------------------------------------------------------
# Reads (best-effort; degrade to "absent" on any failure)
# ---------------------------------------------------------------------------

def _read_canonical_arcs(db, topic_slugs: list[str] | None) -> dict[str, CanonicalArc]:
    """Read ``canonical_arc`` rows and assemble one CanonicalArc per Topic.

    Rows are ``(topic_slug, role, ordinal)``; they are grouped by ``topic_slug``
    and ordered by ascending ordinal. Best-effort: on any failure (including the
    table not yet existing) an empty map is returned, so every Topic is treated
    as having no arc rather than crashing the pass.
    """
    try:
        query = db.table(_CANONICAL_ARC_TABLE).select("topic_slug, role, ordinal")
        if topic_slugs is not None:
            query = query.in_("topic_slug", topic_slugs)
        res = query.execute()
    except Exception as exc:
        logger.warning("[coverage_view] read canonical_arc failed: %s", exc)
        return {}

    by_topic: dict[str, list[CanonicalArcRole]] = {}
    for row in (res.data or []):
        slug = row.get("topic_slug")
        role = row.get("role")
        ordinal = row.get("ordinal")
        if slug is None or role is None or ordinal is None:
            continue
        by_topic.setdefault(slug, []).append(
            CanonicalArcRole(role=role, ordinal=int(ordinal))
        )

    arcs: dict[str, CanonicalArc] = {}
    for slug, roles in by_topic.items():
        ordered = tuple(sorted(roles, key=lambda r: r.ordinal))
        arcs[slug] = CanonicalArc(topic_slug=slug, roles=ordered)
    return arcs


def _read_clips(db, topic_slugs: list[str] | None) -> list[dict]:
    """Read clip rows used for per-role counts and Watch_Event duration joins.

    Returns rows of ``(id, topic_slug, role_ordinal, duration_seconds)``.
    Best-effort: on any failure an empty list is returned.
    """
    try:
        query = db.table("clips").select(
            "id, topic_slug, role_ordinal, duration_seconds"
        )
        if topic_slugs is not None:
            query = query.in_("topic_slug", topic_slugs)
        res = query.execute()
        return list(res.data or [])
    except Exception as exc:
        logger.warning("[coverage_view] read clips failed: %s", exc)
        return []


def _read_watch_events(db, clip_ids: list[str]) -> list[dict]:
    """Read Watch_Events for the given Clips.

    Returns rows of ``(clip_id, watch_ms)``; the duration is joined locally from
    the clip rows. Best-effort: on any failure (or empty clip set) an empty list
    is returned.
    """
    if not clip_ids:
        return []
    try:
        res = (
            db.table("clip_events")
            .select("clip_id, watch_ms")
            .in_("clip_id", clip_ids)
            .execute()
        )
        return list(res.data or [])
    except Exception as exc:
        logger.warning("[coverage_view] read clip_events failed: %s", exc)
        return []


def _read_pruned_slugs(db, topic_slugs: list[str] | None) -> set[str]:
    """Read the set of Pruned_Topic slugs from ``topics.archived``.

    The ``archived`` column is added by the Phase 4 additive migration; until it
    exists the read fails and an empty set is returned, so every Topic is treated
    as non-pruned. Best-effort and read-only.
    """
    try:
        query = db.table("topics").select("slug, archived")
        if topic_slugs is not None:
            query = query.in_("slug", topic_slugs)
        res = query.execute()
        return {
            row["slug"]
            for row in (res.data or [])
            if row.get("slug") is not None and bool(row.get("archived"))
        }
    except Exception as exc:
        logger.warning("[coverage_view] read topics.archived failed: %s", exc)
        return set()


# ---------------------------------------------------------------------------
# Projection assembly
# ---------------------------------------------------------------------------

def build_projections(
    db=None, topic_slugs: list[str] | None = None
) -> list[TopicProjection]:
    """Assemble the per-Topic projections the Coverage_View core consumes.

    Reads the ``canonical_arc`` rows, the clip rows (for per-role counts and
    duration joins), the matching ``clip_events``, and the ``topics.archived``
    Pruned_Topic flags, then builds one :class:`TopicProjection` per Topic that
    has either a Canonical_Arc or at least one clip. Strictly read-only and
    best-effort throughout: any DB error degrades to a safe default rather than
    raising.

    Args:
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.
        topic_slugs: Optional subset of Topics to project; when None, every
            Topic with arc rows or clips is projected.

    Returns:
        The list of assembled :class:`TopicProjection` values.

    Validates: Requirements 4.1, 4.5, 4.6
    """
    if db is None:
        db = get_client()

    arcs = _read_canonical_arcs(db, topic_slugs)
    clips = _read_clips(db, topic_slugs)
    pruned = _read_pruned_slugs(db, topic_slugs)

    # Per-Topic: role-ordinal counts and the clip ids (for the Watch_Event join).
    counts_by_topic: dict[str, dict[int, int]] = {}
    clip_ids_by_topic: dict[str, list[str]] = {}
    duration_by_clip: dict[str, int | None] = {}
    for row in clips:
        slug = row.get("topic_slug")
        clip_id = row.get("id")
        if slug is None or clip_id is None:
            continue
        duration_by_clip[clip_id] = row.get("duration_seconds")
        clip_ids_by_topic.setdefault(slug, []).append(clip_id)
        ordinal = row.get("role_ordinal")
        if ordinal is not None:
            counts = counts_by_topic.setdefault(slug, {})
            counts[int(ordinal)] = counts.get(int(ordinal), 0) + 1

    # Watch_Events for every clip we know about, grouped back to their Topic and
    # joined to the clip's duration.
    all_clip_ids = list(duration_by_clip.keys())
    events = _read_watch_events(db, all_clip_ids)
    topic_of_clip: dict[str, str] = {
        cid: slug for slug, cids in clip_ids_by_topic.items() for cid in cids
    }
    watch_events_by_topic: dict[str, list[tuple[int, int]]] = {}
    for event in events:
        clip_id = event.get("clip_id")
        slug = topic_of_clip.get(clip_id)
        duration = duration_by_clip.get(clip_id)
        if slug is None or duration is None:
            continue
        watch_ms = event.get("watch_ms") or 0
        watch_events_by_topic.setdefault(slug, []).append((watch_ms, duration))

    # One projection per Topic that has an arc or any clip.
    slugs = set(arcs) | set(clip_ids_by_topic)
    if topic_slugs is not None:
        slugs &= set(topic_slugs)

    projections: list[TopicProjection] = []
    for slug in sorted(slugs):
        projections.append(
            TopicProjection(
                topic_slug=slug,
                arc=arcs.get(slug),
                clips_per_role=counts_by_topic.get(slug, {}),
                watch_events=watch_events_by_topic.get(slug, []),
                is_pruned=slug in pruned,
            )
        )
    return projections


def coverage_report(
    db=None, topic_slugs: list[str] | None = None
) -> list[CatalogEntry]:
    """Assemble projections and derive the Coverage_View Catalog_Entries.

    The single entry point an operator coverage endpoint calls: it assembles the
    projections via :func:`build_projections` and runs the pure
    :func:`~app.services.coverage_view.coverage_view` over them, yielding one
    Catalog_Entry per non-pruned Topic. Strictly read-only -- it creates,
    updates, and deletes zero rows (Req 4.5).

    Args:
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.
        topic_slugs: Optional subset of Topics to report on.

    Returns:
        The list of :class:`CatalogEntry` values for non-pruned Topics.

    Validates: Requirements 4.1, 4.5, 4.6
    """
    return coverage_view(build_projections(db, topic_slugs))
