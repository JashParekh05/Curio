"""Clip_Slot_Tag pure decision core (Phase 1, Req 1 segmentation).

Defines the canonical curriculum slot a clip fills -- the exact tags that place
an admitted clip in the Level -> Topic -> Beat structure -- and the validation
that each clip maps to **exactly one** beat/role (the single-idea-to-single-beat
invariant), verified for the leveled flow.

Following the repository convention (see ``coverage_view.py`` /
``clip_ordering.py``), this module holds ONLY the pure decision logic: no DB, no
clock, no global reads. Every function is deterministic in its inputs, total
over its declared inputs, and never raises on any input combination. The thin
ingestion shell that computes the content level and persists the slot fields
onto the ``clips`` row lives in the ingestion pipeline.

It reuses the existing ``Clip`` fields (``content_level``, ``topic_slug``,
``section_index``, ``pedagogical_role``, ``role_ordinal``) rather than adding new
clip identity, and reuses ``level_filter.LEVELS`` as the single source of truth
for the recognized Content_Levels (an unknown level normalizes to ``None``, which
defers to the owning topic's difficulty at read time, exactly like
``level_filter.clip_level``).

ASCII only.

Validates: Requirements 1.3, 1.6
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import PedagogicalRole
from app.services.level_filter import LEVELS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The valid beat indices of a topic's Canonical_Arc (hook / what / how /
#: outcomes). A clip fills exactly one beat.
VALID_SECTION_INDICES: frozenset[int] = frozenset({0, 1, 2, 3})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClipSlot:
    """The exact curriculum slot a clip fills (its tags).

    Attributes:
        topic_slug: The Topic the clip belongs to (non-empty).
        content_level: The level band the clip was retrieved for, one of
            ``LEVELS`` or ``None`` (None defers to the owning topic's difficulty
            at read time).
        section_index: The beat the clip fills, in 0..3 (hook/what/how/outcomes).
        pedagogical_role: The clip's Canonical_Arc role, or ``None`` when unset.
        role_ordinal: The realized arc position (1-based), or ``None`` when unset
            or out of range.
    """

    topic_slug: str
    content_level: str | None
    section_index: int
    pedagogical_role: PedagogicalRole | None
    role_ordinal: int | None


# ---------------------------------------------------------------------------
# Slot construction
# ---------------------------------------------------------------------------

def build_slot(
    topic_slug: str,
    content_level: str | None,
    section_index: int | None,
    pedagogical_role: PedagogicalRole | None,
    role_ordinal: int | None,
) -> ClipSlot | None:
    """Build a ClipSlot for an admitted clip, or None when it cannot be placed.

    A clip can be placed in a single beat iff it has a non-empty ``topic_slug``
    and a ``section_index`` in 0..3; otherwise this returns ``None`` and the
    shell leaves the clip untagged / role-less (Req 1.3, P1.12).

    Normalization for a placeable clip:

    - ``content_level`` is normalized to one of ``LEVELS`` or ``None`` -- any
      unrecognized value (including ``None``) becomes ``None`` so it defers to
      the owning topic's difficulty at read time, mirroring
      ``level_filter.clip_level`` (Req 1.3, P1.14).
    - A present ``role_ordinal`` must be ``>= 1``; a value below 1 is treated as
      absent (``None``) rather than rejecting the whole slot (Req 1.3, P1.15).

    Every returned slot therefore satisfies :func:`is_single_beat` (P1.13).

    Pure, total, deterministic; never raises on any input combination (P1.16).

    Args:
        topic_slug: The clip's Topic; must be non-empty to place the clip.
        content_level: The clip's retrieved level band (normalized).
        section_index: The clip's beat; must be in 0..3 to place the clip.
        pedagogical_role: The clip's Canonical_Arc role, carried through as-is.
        role_ordinal: The realized arc position; a present value must be >= 1.

    Returns:
        The clip's :class:`ClipSlot`, or ``None`` when it cannot be placed in a
        single beat.

    Validates: Requirements 1.3, 1.6
    """
    # Require a non-empty topic slug. ``topic_slug`` may not be a string when
    # called from a best-effort shell on malformed data; guard defensively so we
    # never raise.
    if not isinstance(topic_slug, str) or not topic_slug:
        return None

    # Require a beat index in 0..3.
    if section_index not in VALID_SECTION_INDICES:
        return None

    # Normalize the content level: keep recognized levels, drop everything else
    # (including None) to None.
    normalized_level = content_level if content_level in LEVELS else None

    # A present role_ordinal must be >= 1; otherwise treat it as absent.
    normalized_ordinal = (
        role_ordinal
        if isinstance(role_ordinal, int) and role_ordinal >= 1
        else None
    )

    return ClipSlot(
        topic_slug=topic_slug,
        content_level=normalized_level,
        section_index=section_index,
        pedagogical_role=pedagogical_role,
        role_ordinal=normalized_ordinal,
    )


# ---------------------------------------------------------------------------
# Single-beat invariant
# ---------------------------------------------------------------------------

def is_single_beat(slot: ClipSlot | None) -> bool:
    """True iff the slot places the clip in exactly one beat.

    Encodes the single-idea-to-single-beat invariant for the leveled flow: a
    slot is single-beat iff it is not ``None`` and its ``section_index`` is in
    0..3 (P1.13). Total and never raises.

    Args:
        slot: The clip's slot, or ``None`` when the clip could not be placed.

    Returns:
        ``True`` when the slot places the clip in exactly one beat, else
        ``False``.

    Validates: Requirements 1.3
    """
    return slot is not None and slot.section_index in VALID_SECTION_INDICES
