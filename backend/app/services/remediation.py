"""Remediation_Select pure decision core.

On a weak checkpoint (a failed ``check``/``post``), Curio surfaces a soft
"rewatch these clips" suggestion on the end-card. This module holds ONLY the
pure decision logic that picks which already-seen clips to recommend rewatching
and in what order; the shell (``feed``/end-card + ``recommendation_agent``)
loads the learner's seen clips for the weak beat, calls ``clips_to_rewatch``,
and renders the result. Surfacing remediation never blocks feed advancement --
this core only selects and orders.

Following the repository convention (see ``clip_ordering.py`` /
``reseed_prioritizer.py``), this module has no DB, no clock, and no global
reads. ``clips_to_rewatch`` is deterministic in its inputs, never mutates them,
is total over all inputs, and never raises -- it returns a new list.

ASCII only.

Validates: Requirements 3.2, 3.4
"""
from __future__ import annotations

from dataclasses import dataclass

# Default cap on how many clips the end-card recommends rewatching.
DEFAULT_MAX_REWATCH: int = 3


@dataclass(frozen=True)
class RewatchClip:
    """A candidate clip the learner has already seen, considered for rewatch."""

    clip_id: str
    section_index: int           # the beat the learner was weak on (0..3)
    role_ordinal: int | None     # realized Canonical_Arc position (1-based) or None
    final_score: float


def clips_to_rewatch(
    weak_section_index: int,
    candidates: list[RewatchClip],
    max_clips: int = DEFAULT_MAX_REWATCH,
) -> list[RewatchClip]:
    """Pick up to ``max_clips`` clips from the weak beat to recommend rewatching.

    Selection and ordering:

    1. Keep only candidates whose ``section_index == weak_section_index`` so the
       suggestion targets exactly the beat the learner was weak on (Req 3.2,
       P3.12).
    2. Order the survivors by, in precedence:
       - ascending ``role_ordinal`` (Canonical_Arc order); a ``None`` ordinal
         sorts after every present ordinal,
       - then descending ``final_score``,
       - then ascending ``clip_id`` as the final stable tiebreaker.
       The ``clip_id`` tiebreaker makes the order total with no remaining ties
       (P3.14).
    3. Return at most ``max_clips`` results. ``max_clips <= 0`` yields an empty
       list (P3.13); empty candidates yield an empty list (P3.15).

    Pure: identical inputs always produce identical output (P3.16). Total: never
    raises on any input. The input list is not mutated.

    Args:
        weak_section_index: The beat (``section_index``) the learner was weak on.
        candidates: Already-seen clips eligible for rewatch (not mutated).
        max_clips: Maximum number of clips to return; non-positive -> empty.

    Returns:
        A new list of at most ``max_clips`` ``RewatchClip`` from the weak beat,
        in total arc order.

    Validates: Requirements 3.2, 3.4
    """
    if max_clips <= 0:
        return []

    weak_beat = [
        clip for clip in candidates if clip.section_index == weak_section_index
    ]

    def _sort_key(clip: RewatchClip) -> tuple[int, int, float, str]:
        # group 0 = has a role ordinal (ordered by ordinal asc); group 1 = no
        # ordinal (sorts after every ordinal-bearing clip). Within each group the
        # tiebreak is final_score descending then ascending clip_id.
        if clip.role_ordinal is None:
            return (1, 0, -clip.final_score, clip.clip_id)
        return (0, clip.role_ordinal, -clip.final_score, clip.clip_id)

    weak_beat.sort(key=_sort_key)
    return weak_beat[:max_clips]
