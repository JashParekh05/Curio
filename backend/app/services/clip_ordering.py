"""Single arc-ordered clip-ordering pure decision core.

Curio used to order a Topic's Clips down two competing paths: an "arc path"
(``role_ordinal``) and a "legacy section path" (``section_index`` /
``narrative_rank``), both living inside ``feed_retrieval._order_by_arc``. This
module collapses ordering onto ONE arc-ordered path driven by the Topic's
Canonical_Arc (see ``arc_unifier.py``), so retrieval reasons about a single
shape (Req 2.3).

Following the repository convention (see ``arc_unifier.py`` /
``topic_frontier.py``), this module holds ONLY the pure decision logic: no DB,
no clock, no global reads. ``order_clips_by_arc`` is deterministic in its
inputs, never mutates them, and never raises -- it returns a new list. The
scoring step (``feed_scoring._compute_scores``) runs first to populate
``final_score``; this core only orders.

ASCII only.

Validates: Requirements 2.1, 2.2, 2.4, 2.5, 2.6, 2.7
"""
from __future__ import annotations

from app.models.schemas import Clip
from app.services.arc_unifier import CanonicalArc


def _clip_score(clip: Clip) -> float:
    """The existing ranking score used as the within-role tiebreaker.

    Prefers ``final_score`` (populated by ``feed_scoring._compute_scores``),
    falling back to ``hook_score`` and finally 0.0 so the ordering is a total
    order even before scoring has run.
    """
    if clip.final_score is not None:
        return clip.final_score
    if clip.hook_score is not None:
        return clip.hook_score
    return 0.0


def order_clips_by_arc(
    clips: list[Clip],
    arc: CanonicalArc | None,
) -> list[Clip]:
    """Order a Topic's Clips through the single arc-ordered path.

    Ordering keys, in precedence:

    1. Primary -- Canonical_Arc role ordinal, ascending. A Clip's ordinal is
       looked up from ``arc`` by the Clip's recorded ``pedagogical_role``
       (Req 2.1). The output's sequence of role ordinals is therefore
       non-decreasing (Req 2.4).
    2. Within one role -- ``final_score`` descending, then ascending unique
       Clip id as the final stable tiebreaker, so the result is a total order
       with no remaining ties (Req 2.2).

    Role-less Clips -- those whose ``pedagogical_role`` is absent from ``arc``
    (or when ``arc`` is None) -- sort after every role-bearing Clip, ordered
    among themselves by ``final_score`` descending then ascending Clip id
    (Req 2.5).

    Empty input returns an empty list and never raises (Req 2.6). Pure:
    identical inputs always produce identical output (Req 2.7).

    Args:
        clips: The Topic's Clips to order (not mutated).
        arc: The Topic's Canonical_Arc, or None when the Topic has no arc yet.

    Returns:
        A new list of the same Clips in single arc order.

    Validates: Requirements 2.1, 2.2, 2.4, 2.5, 2.6, 2.7
    """
    # role -> ordinal lookup from the Canonical_Arc. When two arc positions
    # carry the same role (rare), the lowest ordinal wins, matching
    # arc_unifier.associate_clip_role's single-valued association.
    role_ordinal: dict[str, int] = {}
    if arc is not None:
        for arc_role in arc.roles:
            if arc_role.role not in role_ordinal:
                role_ordinal[arc_role.role] = arc_role.ordinal

    def _sort_key(clip: Clip) -> tuple[int, int, float, str]:
        ordinal = (
            role_ordinal.get(clip.pedagogical_role)
            if clip.pedagogical_role is not None
            else None
        )
        # group 0 = role-bearing (ordered by ordinal first); group 1 = role-less
        # (sorts after every role-bearing clip, Req 2.5). Within each group the
        # tiebreak is final_score descending then ascending clip id (Req 2.2).
        if ordinal is None:
            return (1, 0, -_clip_score(clip), clip.id)
        return (0, ordinal, -_clip_score(clip), clip.id)

    return sorted(clips, key=_sort_key)
