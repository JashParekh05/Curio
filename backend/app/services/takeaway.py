"""Takeaway_Artifact pure derivation core (Phase 4, Req 10).

A Takeaway_Artifact is the per-Topic summary a learner keeps after finishing a
Topic, derived from that Topic's Admitted_Clips. This module holds ONLY the pure
derivation: given a learner, a Topic, and the Topic's Admitted_Clips, it orders
the clips by ascending Canonical_Arc Pedagogical_Role ordinal and, within a role,
by descending ranking score, then derives the ordered summary points (Req 10.2).

Following the repository convention (see ``topic_frontier.py`` /
``curriculum_spine.py``), this module contains no DB, no clock, and no global
reads. ``derive_takeaway`` is deterministic in its inputs, never mutates them,
and never raises. It returns ``None`` for zero clips so no artifact is produced
(Req 10.5); for an identical learner, Topic, and clip set it returns an identical
:class:`Takeaway`, so re-derivation is safe and produces no distinct duplicate
(Req 10.1, 10.6). The thin, best-effort I/O shell that triggers on the
last-clip Impression and persists the artifact lives in ``takeaway_store.py``.

ASCII only.

Validates: Requirements 10.1, 10.2, 10.5, 10.6
"""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TakeawayClip:
    """One Admitted_Clip the Takeaway is derived from.

    Attributes:
        clip_id: The Clip's unique identifier, used as the final stable
            tiebreaker so the derivation is a total order with no remaining ties.
        role_ordinal: The Clip's Canonical_Arc Pedagogical_Role ordinal
            (1-based); the primary, ascending ordering key (Req 10.2). A Clip
            with no arc role carries a role_ordinal that sorts it last.
        final_score: The Clip's existing ranking score; within one role the
            ordering is by descending final_score (Req 10.2).
        title: The Clip's title, the basis of its summary point.
        description: The Clip's optional description, appended to the point when
            present.
    """

    clip_id: str
    role_ordinal: int | None
    final_score: float | None
    title: str
    description: str | None = None


@dataclass(frozen=True)
class Takeaway:
    """A per-Topic Takeaway_Artifact uniquely identified by (learner, Topic).

    Attributes:
        learner_id: The learner who keeps this artifact.
        topic_slug: The Topic the artifact summarizes.
        points: The derived summary points in Canonical_Arc order (Req 10.2).
    """

    learner_id: str
    topic_slug: str
    points: tuple[str, ...]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

#: Role ordinal a role-less Clip is treated as carrying so it sorts after every
#: role-bearing Clip. Larger than any real 1-based Canonical_Arc ordinal.
_ROLELESS_ORDINAL = float("inf")


def _order_key(clip: TakeawayClip) -> tuple[float, float, str]:
    """Total ordering key: arc ordinal asc, then score desc, then clip id asc.

    A Clip with no ``role_ordinal`` sorts after every role-bearing Clip; a Clip
    with no ``final_score`` is treated as score 0.0. The ascending ``clip_id``
    is the final stable tiebreaker, so identical clip sets always order
    identically (Req 10.2, idempotent derivation for Req 10.6).
    """
    ordinal = _ROLELESS_ORDINAL if clip.role_ordinal is None else float(clip.role_ordinal)
    score = clip.final_score if clip.final_score is not None else 0.0
    return (ordinal, -score, clip.clip_id)


def _summary_point(clip: TakeawayClip) -> str:
    """Derive one Clip's summary point from its title and optional description.

    Pure and deterministic: trims surrounding whitespace and, when a non-empty
    description is present, appends it after the title separated by ": ".
    """
    title = (clip.title or "").strip()
    description = (clip.description or "").strip()
    if description:
        return f"{title}: {description}" if title else description
    return title


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def derive_takeaway(
    learner_id: str, topic_slug: str, clips: list[TakeawayClip]
) -> Takeaway | None:
    """Derive the single Takeaway_Artifact for a (learner, Topic) pair.

    Orders the Topic's Admitted_Clips by ascending Canonical_Arc role ordinal and,
    within a role, by descending ``final_score`` -- with ascending ``clip_id`` as
    the final stable tiebreaker -- then derives one summary point per Clip in that
    order (Req 10.2). Returns exactly one :class:`Takeaway` keyed by
    ``(learner_id, topic_slug)`` (Req 10.1).

    Returns ``None`` when there are zero clips, so no artifact is produced for a
    Topic with no content to summarize (Req 10.5). Pure and deterministic:
    identical inputs always yield an identical :class:`Takeaway`, so a
    re-derivation produces no distinct duplicate (Req 10.6). Never mutates the
    input list and never raises.

    Args:
        learner_id: The learner the artifact belongs to.
        topic_slug: The Topic the artifact summarizes.
        clips: The Topic's Admitted_Clips.

    Returns:
        The derived :class:`Takeaway`, or ``None`` when ``clips`` is empty.

    Validates: Requirements 10.1, 10.2, 10.5, 10.6
    """
    if not clips:
        return None

    ordered = sorted(clips, key=_order_key)
    points = tuple(_summary_point(clip) for clip in ordered)
    return Takeaway(learner_id=learner_id, topic_slug=topic_slug, points=points)
