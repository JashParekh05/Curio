"""
Plan_Alignment_Checker — pure functions for diffing and repairing the
realized pedagogical arc against the planned arc.

``compute_realized_arc``, ``diff_arcs``, and ``check_and_repair`` are all
implemented here.
"""

from __future__ import annotations

from app.models.schemas import (
    ArcDiff,
    AlignmentResult,
    Clip,
    LearningAtom,
    PedagogicalRole,
    PlannedArc,
)


def compute_realized_arc(clips: list[Clip]) -> list[PedagogicalRole]:
    """Return the sequence of pedagogical roles from *clips* in assembled order.

    Clips whose ``pedagogical_role`` is ``None`` (pre-feature rows that have
    not been assigned a role yet) are excluded from the result.

    Args:
        clips: Assembled clip sequence in delivery order.

    Returns:
        A list of :class:`~app.models.schemas.PedagogicalRole` values, one per
        clip that carries a role, preserving the input order.

    Example::

        >>> from app.models.schemas import Clip
        >>> clips = [
        ...     Clip(id="a", topic_slug="t", title="A", video_url="u",
        ...          pedagogical_role="definition"),
        ...     Clip(id="b", topic_slug="t", title="B", video_url="u",
        ...          pedagogical_role=None),
        ...     Clip(id="c", topic_slug="t", title="C", video_url="u",
        ...          pedagogical_role="example"),
        ... ]
        >>> compute_realized_arc(clips)
        ['definition', 'example']
    """
    return [clip.pedagogical_role for clip in clips if clip.pedagogical_role is not None]


def diff_arcs(planned: PlannedArc, realized: list[PedagogicalRole]) -> ArcDiff:
    """Diff a realized arc against a planned arc.

    Compares the sequence of roles the clips actually deliver against what the
    planned arc promised and returns a structured description of the difference.

    Args:
        planned: The :class:`~app.models.schemas.PlannedArc` committed to for
            the topic, containing an ordered list of
            :class:`~app.models.schemas.ArcRole` entries.
        realized: The realized sequence of
            :class:`~app.models.schemas.PedagogicalRole` values as returned by
            :func:`compute_realized_arc`.

    Returns:
        An :class:`~app.models.schemas.ArcDiff` with:

        * ``missing_roles`` — every role that appears in *planned* but is
          absent from *realized*, in planned order.
        * ``order_mismatch_positions`` — the 0-based indices in *realized*
          where the role's relative order among planned roles diverges from the
          planned order.  A role at index ``i`` is misplaced when it appears in
          *realized* before some role that is supposed to precede it according
          to the planned arc.
        * ``aligned`` — ``True`` iff both ``missing_roles`` and
          ``order_mismatch_positions`` are empty.

    Edge case — empty realized list:
        All planned roles are reported as missing and ``aligned`` is ``False``.

    Examples::

        >>> from app.models.schemas import PlannedArc, ArcRole
        >>> planned = PlannedArc(
        ...     topic_slug="t", concept_type="conceptual",
        ...     roles=[
        ...         ArcRole(role="definition", ordinal=1),
        ...         ArcRole(role="motivation", ordinal=2),
        ...         ArcRole(role="example", ordinal=3),
        ...     ],
        ... )
        >>> diff_arcs(planned, ["definition", "motivation", "example"])
        ArcDiff(missing_roles=[], order_mismatch_positions=[], aligned=True)

        >>> diff_arcs(planned, ["motivation", "definition", "example"])
        ArcDiff(missing_roles=[], order_mismatch_positions=[0, 1], aligned=False)

        >>> diff_arcs(planned, [])
        ArcDiff(missing_roles=['definition', 'motivation', 'example'],
                order_mismatch_positions=[], aligned=False)
    """
    # Build a lookup from role name -> its 0-based position in the planned arc.
    # Only the first occurrence of each role is considered (arcs should not
    # duplicate roles, but we are defensive here).
    planned_order: dict[PedagogicalRole, int] = {}
    for arc_role in planned.roles:
        if arc_role.role not in planned_order:
            planned_order[arc_role.role] = arc_role.ordinal - 1  # convert to 0-based

    # --- missing_roles ---------------------------------------------------
    # Roles present in the planned arc but absent from realized (as a set).
    realized_set: set[PedagogicalRole] = set(realized)
    missing_roles: list[PedagogicalRole] = [
        arc_role.role
        for arc_role in planned.roles
        if arc_role.role not in realized_set
    ]

    # --- order_mismatch_positions ----------------------------------------
    # We only care about roles that exist in *both* planned and realized.
    # Extract, in realized order, those roles together with their 0-based
    # realized indices.
    realized_planned_pairs: list[tuple[int, PedagogicalRole]] = [
        (i, role)
        for i, role in enumerate(realized)
        if role in planned_order
    ]

    # A role at realized position i is misplaced when there exists a later
    # element in realized_planned_pairs whose planned order rank is *smaller*
    # (i.e., it should have come earlier in the plan but appears after).
    # Equivalently: collect the subsequence of planned-rank values in realized
    # order, then flag every index where the planned rank is not in its
    # correct non-decreasing position relative to the others.
    #
    # We use a simple O(n^2) check that is faithful to the spec semantics:
    # position i is misplaced iff any *later* element j in
    # realized_planned_pairs has a *smaller* planned rank than element i.
    order_mismatch_positions: list[int] = []
    for idx, (realized_i, role_i) in enumerate(realized_planned_pairs):
        rank_i = planned_order[role_i]
        # Check if any element appearing *after* this one in realized has a
        # smaller planned rank (meaning it should have come before role_i).
        for realized_j, role_j in realized_planned_pairs[idx + 1 :]:
            if planned_order[role_j] < rank_i:
                order_mismatch_positions.append(realized_i)
                break

    # --- aligned ---------------------------------------------------------
    aligned = not missing_roles and not order_mismatch_positions

    return ArcDiff(
        missing_roles=missing_roles,
        order_mismatch_positions=order_mismatch_positions,
        aligned=aligned,
    )


def check_and_repair(
    clips: list[Clip],
    planned_arc: PlannedArc,
    atom_pool: list[LearningAtom],
    max_attempts: int = 3,
) -> AlignmentResult:
    """Diff the realized arc against the planned arc and attempt bounded repair.

    Runs at most ``min(max_attempts, 3)`` repair iterations.  Each iteration:

    1. Calls :func:`diff_arcs` to determine the current gap between the
       realized arc and the planned arc.
    2. If already aligned, returns immediately with ``aligned=True``.
    3. For each **missing role** in the diff: searches *atom_pool* for the
       first atom whose ``role`` matches the missing planned role, builds a
       synthetic :class:`~app.models.schemas.Clip` from that atom, and inserts
       it at the position in the working clip list that corresponds to the
       role's ordinal in *planned_arc* (so the clip lands in the right spot
       relative to existing clips).
    4. For any **ordering mismatch**: reorders the working clip list so that
       clips whose ``pedagogical_role`` appears in *planned_arc* are sorted by
       their planned ordinal.  Clips with no role (pre-feature rows) preserve
       their relative order at the tail.
    5. Re-diffs the updated clip list.  If now aligned, returns with
       ``aligned=True``.

    If the diff is still non-empty after all attempts are exhausted the
    function stops, records ``unresolved=True``, and **preserves the original**
    *clips* argument unchanged (Req 5.7).

    Empty clip set:
        All planned roles are reported as missing and the result is immediately
        returned as not-aligned (Req 5.8).  No repair is attempted.

    Args:
        clips: Assembled clip sequence in delivery order.
        planned_arc: The :class:`~app.models.schemas.PlannedArc` committed to
            for this topic.
        atom_pool: Pool of :class:`~app.models.schemas.LearningAtom`s available
            for filling missing roles.
        max_attempts: Upper bound on repair iterations.  Clamped to ``[1, 3]``
            regardless of the value supplied.

    Returns:
        An :class:`~app.models.schemas.AlignmentResult` describing the final
        aligned state, the last observed diff, and whether the repair was
        exhausted without resolving the diff.

    Requirements: 5.4, 5.5, 5.7, 5.8
    """
    # ------------------------------------------------------------------ #
    # Clamp max_attempts to [1, 3] (spec caps at 3)
    # ------------------------------------------------------------------ #
    effective_max: int = max(1, min(max_attempts, 3))

    # ------------------------------------------------------------------ #
    # Fast path: empty clip set → all planned roles missing (Req 5.8)
    # ------------------------------------------------------------------ #
    if not clips:
        all_missing = [arc_role.role for arc_role in planned_arc.roles]
        empty_diff = ArcDiff(
            missing_roles=all_missing,
            order_mismatch_positions=[],
            aligned=False,
        )
        return AlignmentResult(aligned=False, diff=empty_diff, unresolved=False)

    # ------------------------------------------------------------------ #
    # Build a lookup: role -> planned ordinal (1-based) for reordering
    # ------------------------------------------------------------------ #
    planned_ordinal: dict[PedagogicalRole, int] = {
        arc_role.role: arc_role.ordinal for arc_role in planned_arc.roles
    }

    # Build a lookup: role -> first atom in pool with that role
    # (used to fill missing roles)
    atom_by_role: dict[PedagogicalRole, LearningAtom] = {}
    for atom in atom_pool:
        if atom.role not in atom_by_role:
            atom_by_role[atom.role] = atom

    # ------------------------------------------------------------------ #
    # Working copy of clips — we mutate this across attempts
    # ------------------------------------------------------------------ #
    working_clips: list[Clip] = list(clips)

    # ------------------------------------------------------------------ #
    # Check alignment before the first attempt
    # ------------------------------------------------------------------ #
    realized = compute_realized_arc(working_clips)
    current_diff = diff_arcs(planned_arc, realized)

    if current_diff.aligned:
        return AlignmentResult(aligned=True, diff=current_diff, unresolved=False)

    # ------------------------------------------------------------------ #
    # Repair loop (bounded by effective_max)
    # ------------------------------------------------------------------ #
    for _attempt in range(effective_max):
        # ---- 1. Fill missing roles (Req 5.4) -------------------------
        for missing_role in current_diff.missing_roles:
            atom = atom_by_role.get(missing_role)
            if atom is None:
                # No atom available for this role; skip — cannot fill
                continue

            # Build a synthetic Clip from the atom
            new_clip = Clip(
                id=atom.id,
                topic_slug=atom.topic_slug,
                title=f"{atom.role}: {atom.concept}"[:200],
                description=(atom.transcript or "")[:1000] or None,
                video_url=atom.source_url,
                source_url=atom.source_url,
                pedagogical_role=atom.role,
                role_ordinal=planned_ordinal.get(atom.role),
                concept_label=atom.concept,
            )

            # Insert the new clip at the position that matches its planned
            # ordinal relative to the existing clips' planned ordinals.
            insert_pos = len(working_clips)  # default: append
            target_ordinal = planned_ordinal.get(missing_role, 0)
            for i, existing_clip in enumerate(working_clips):
                existing_ordinal = planned_ordinal.get(
                    existing_clip.pedagogical_role, 0
                )
                if existing_ordinal > target_ordinal:
                    insert_pos = i
                    break
            working_clips.insert(insert_pos, new_clip)

        # ---- 2. Reorder on ordering mismatch (Req 5.5) ---------------
        if current_diff.order_mismatch_positions:
            # Stable-sort clips that have a planned role by their planned
            # ordinal; clips with no planned role (pre-feature rows) fall
            # to the tail in their original relative order.
            def _sort_key(clip: Clip) -> int:
                if clip.pedagogical_role is not None and clip.pedagogical_role in planned_ordinal:
                    return planned_ordinal[clip.pedagogical_role]
                # Push clips without a planned role to the end
                return len(planned_arc.roles) + 1

            working_clips.sort(key=_sort_key)

        # ---- 3. Re-diff after repairs ---------------------------------
        realized = compute_realized_arc(working_clips)
        current_diff = diff_arcs(planned_arc, realized)

        if current_diff.aligned:
            return AlignmentResult(aligned=True, diff=current_diff, unresolved=False)

    # ------------------------------------------------------------------ #
    # Exhausted all attempts without reaching alignment (Req 5.7)
    # Preserve the *original* clips — reset to the caller's input.
    # ------------------------------------------------------------------ #
    realized_original = compute_realized_arc(clips)
    final_diff = diff_arcs(planned_arc, realized_original)
    return AlignmentResult(aligned=False, diff=final_diff, unresolved=True)
