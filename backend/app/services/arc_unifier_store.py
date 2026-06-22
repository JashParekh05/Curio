"""Arc_Unifier persistence: thin, best-effort I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``arc_unifier.py``. Mirroring the convention of ``backlog_store.py`` and the
design's "pure decision core, thin I/O shell" principle, it holds ONLY the
Supabase reads/writes:

- it reads a Topic's Role_Arc_Model (``topic_arcs`` / ``topic_arc_roles``) and
  Legacy_Section_Model (``topic_sections``),
- calls the pure :func:`~app.services.arc_unifier.unify_arc`, and
- persists the resulting Canonical_Arc to the ``canonical_arc`` table.

All unification decisions live in ``arc_unifier`` and are applied here; this
module never decides anything.

The ``canonical_arc`` table is created later by the Phase 4 additive migration
(``migration_content_revamp.sql``); this shell is written to use it and degrades
gracefully (logs and returns) if it is not yet present, exactly like the other
best-effort shells -- so a unification pass can never block or crash the request
path.

On a ``rejection`` result the shell leaves any prior Canonical_Arc for the Topic
unchanged (Req 1.9). On a ``structureless`` result it likewise writes nothing.

ASCII only.

Validates: Requirements 1.5, 1.9
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.supabase import get_client
from app.models.schemas import ArcRole
from app.services.arc_unifier import (
    CanonicalArc,
    CanonicalArcRole,
    UnifyResult,
    unify_arc,
)

logger = logging.getLogger(__name__)

# The Supabase table backing the Canonical_Arc (see migration_content_revamp.sql).
_CANONICAL_ARC_TABLE = "canonical_arc"


# ---------------------------------------------------------------------------
# Reads (best-effort; degrade to "model absent")
# ---------------------------------------------------------------------------

def _read_role_model(db, topic_slug: str) -> list[ArcRole]:
    """Read the Role_Arc_Model rows for a Topic from ``topic_arc_roles``.

    Returns the recorded roles in any order (the pure core re-orders by ordinal).
    Best-effort: on any failure an empty list is returned, which the pure core
    treats as an absent Role_Arc_Model.
    """
    try:
        res = (
            db.table("topic_arc_roles")
            .select("role, ordinal")
            .eq("topic_slug", topic_slug)
            .execute()
        )
        return [
            ArcRole(role=row["role"], ordinal=row["ordinal"])
            for row in (res.data or [])
        ]
    except Exception as exc:
        logger.warning("[arc_unifier] read role model for '%s' failed: %s", topic_slug, exc)
        return []


def _read_section_indices(db, topic_slug: str) -> list[int]:
    """Read the Legacy_Section_Model section indices for a Topic.

    Best-effort: on any failure an empty list is returned, which the pure core
    treats as an absent Legacy_Section_Model.
    """
    try:
        res = (
            db.table("topic_sections")
            .select("section_index")
            .eq("topic_slug", topic_slug)
            .execute()
        )
        indices: list[int] = []
        for row in (res.data or []):
            idx = row.get("section_index")
            if idx is not None:
                indices.append(int(idx))
        return indices
    except Exception as exc:
        logger.warning("[arc_unifier] read sections for '%s' failed: %s", topic_slug, exc)
        return []


def load_canonical_arc(topic_slug: str, db=None) -> CanonicalArc | None:
    """Load a Topic's persisted Canonical_Arc from ``canonical_arc``.

    Returns the stored arc (roles in ascending ordinal order) or None when the
    Topic has no Canonical_Arc yet (or the table is not present). Best-effort:
    any failure is logged and yields None so the serving path never blocks or
    crashes -- a Topic without an arc simply falls through to role-less ordering
    in :func:`~app.services.clip_ordering.order_clips_by_arc`.

    Args:
        topic_slug: The Topic whose Canonical_Arc to load.
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.

    Returns:
        The Topic's :class:`~app.services.arc_unifier.CanonicalArc`, or None.
    """
    if db is None:
        db = get_client()
    try:
        res = (
            db.table(_CANONICAL_ARC_TABLE)
            .select("role, ordinal")
            .eq("topic_slug", topic_slug)
            .order("ordinal")
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        roles = tuple(
            CanonicalArcRole(role=row["role"], ordinal=int(row["ordinal"]))
            for row in rows
        )
        return CanonicalArc(topic_slug=topic_slug, roles=roles)
    except Exception as exc:
        logger.warning("[arc_unifier] load canonical arc for '%s' failed: %s", topic_slug, exc)
        return None


# ---------------------------------------------------------------------------
# Write (best-effort; leaves prior arc unchanged on rejection/structureless)
# ---------------------------------------------------------------------------

def _persist_canonical_arc(db, arc: CanonicalArc) -> None:
    """Persist a produced Canonical_Arc to ``canonical_arc``, replacing prior rows.

    The arc is stored as one row per role keyed by ``(topic_slug, ordinal)``.
    Prior rows for the Topic are cleared first so a shrunk arc never leaves stale
    higher-ordinal rows behind. Best-effort: any failure (including the table not
    yet existing) is logged and swallowed.
    """
    try:
        # Replace prior arc rows for this Topic so the stored arc is exactly the
        # newly produced one.
        db.table(_CANONICAL_ARC_TABLE).delete().eq("topic_slug", arc.topic_slug).execute()

        now = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "topic_slug": arc.topic_slug,
                "role": arc_role.role,
                "ordinal": arc_role.ordinal,
                "updated_at": now,
            }
            for arc_role in arc.roles
        ]
        if rows:
            db.table(_CANONICAL_ARC_TABLE).upsert(
                rows, on_conflict="topic_slug,ordinal"
            ).execute()
        logger.info(
            "[arc_unifier] canonical arc stored for '%s': %d role(s)",
            arc.topic_slug, len(rows),
        )
    except Exception as exc:
        logger.warning(
            "[arc_unifier] persist canonical arc for '%s' failed: %s",
            arc.topic_slug, exc,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def unify_and_store(topic_slug: str, db=None) -> UnifyResult:
    """Unify a Topic's two structure models and persist the Canonical_Arc.

    Reads the Topic's Role_Arc_Model and Legacy_Section_Model, calls the pure
    :func:`~app.services.arc_unifier.unify_arc`, and:

    - when an arc is produced (including the both-models case where the role
      model supersedes the legacy model -- Req 1.5), persists it to
      ``canonical_arc``;
    - when the result is a ``rejection`` (malformed structure -- Req 1.9) or a
      ``structureless`` Topic, writes nothing, leaving any prior Canonical_Arc
      for that Topic unchanged.

    Best-effort throughout: any DB error is logged and swallowed, and the pure
    :class:`~app.services.arc_unifier.UnifyResult` is still returned to the
    caller so callers can record indications.

    Args:
        topic_slug: The Topic to unify.
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.

    Returns:
        The pure :class:`~app.services.arc_unifier.UnifyResult` for the Topic.

    Validates: Requirements 1.5, 1.9
    """
    if db is None:
        db = get_client()

    role_model = _read_role_model(db, topic_slug)
    section_indices = _read_section_indices(db, topic_slug)

    result = unify_arc(topic_slug, role_model, section_indices)

    if result.arc is not None:
        _persist_canonical_arc(db, result.arc)
    else:
        # Rejection or structureless: leave any prior Canonical_Arc unchanged.
        if result.rejection is not None:
            logger.info(
                "[arc_unifier] '%s' rejected (%s); prior canonical arc left unchanged",
                topic_slug, result.rejection,
            )

    return result
