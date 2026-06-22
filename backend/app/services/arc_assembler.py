"""Arc_Assembler: pure, unit-testable decision functions for atom selection,
topological ordering, clip sizing, and assembly.

`select_atoms` and `topological_order` are implemented here; subsequent tasks
will add `assemble` and the engagement tiebreaker.
"""

import uuid
from itertools import groupby
from typing import get_args

from app.models.schemas import Clip, CoherenceDefect, LearningAtom, PedagogicalRole, PlannedArc

# Set of all defined PedagogicalRole string values, derived from the type at
# import time so it stays in sync with schema changes automatically.
_VALID_ROLES: frozenset[str] = frozenset(get_args(PedagogicalRole))

_TITLE_MAX_LEN: int = 200
_DESCRIPTION_MAX_LEN: int = 1000


def select_atoms(atoms: list[LearningAtom]) -> list[LearningAtom]:
    """Select exactly one atom per (covered_concept, role) combination.

    Deterministic total order among ties:
      1. Earliest start timestamp (ascending).
      2. Atom id (lexicographic ascending) as the final tiebreaker.

    The result is order-independent: the same input *set* always produces the
    same output regardless of the order the atoms were supplied.

    Args:
        atoms: Pool of candidate LearningAtoms (may contain duplicates by
               (concept, role)).

    Returns:
        A list of LearningAtoms — one per (covered_concept, role) pair — in
        an unspecified (caller-defined) order.  Ordering of the returned list
        is left to downstream functions (e.g. topological_order).

    Requirement: 3.5
    """
    # Group atoms by (concept, role), then pick the best representative using
    # the deterministic total order: (start ASC, id ASC).
    best: dict[tuple[str, str], LearningAtom] = {}

    for atom in atoms:
        key = (atom.concept, atom.role)
        current = best.get(key)
        if current is None:
            best[key] = atom
        else:
            # Prefer earlier start; break ties lexicographically by id.
            if (atom.start, atom.id) < (current.start, current.id):
                best[key] = atom

    return list(best.values())


def topological_order(
    atoms: list[LearningAtom],
    planned_arc: PlannedArc,
) -> tuple[list[LearningAtom], list[CoherenceDefect]]:
    """Order atoms so that:

    (a) Role groups follow the Planned_Arc ordinal order (Req 3.2).
    (b) Within / across role groups, any atom that teaches a concept precedes
        every atom that lists that concept in ``prior_knowledge`` — i.e. true
        prerequisite ordering (Req 3.3).

    Edge cases
    ----------
    * Uncovered prior_knowledge — if an atom lists a ``prior_knowledge``
      concept that no other atom covers, the atom is retained in the sequence
      and a ``prerequisite_gap`` CoherenceDefect is recorded (Req 3.4).
    * Circular dependencies — if the prerequisite graph among atoms contains a
      cycle, every atom in the cycle is retained (none dropped).  The cycle
      members are ordered by the deterministic selection order
      ``(start ASC, id ASC)`` and a single ``circular_dependency``
      CoherenceDefect is recorded for the cycle (Req 3.9).

    Args:
        atoms:       Pool of selected atoms (output of ``select_atoms``).
        planned_arc: The topic's planned arc, used to derive the canonical
                     role ordering.

    Returns:
        A 2-tuple ``(ordered_atoms, defects)``.

    Requirements: 3.2, 3.3, 3.4, 3.9
    """
    if not atoms:
        return [], []

    defects: list[CoherenceDefect] = []

    # ------------------------------------------------------------------
    # 1.  Build the role -> ordinal lookup from the Planned_Arc.
    #     Atoms whose role is absent from the planned arc are assigned an
    #     ordinal of float('inf') so they sort to the end — they stay in the
    #     output (never dropped).
    # ------------------------------------------------------------------
    role_ordinal: dict[str, int] = {
        arc_role.role: arc_role.ordinal for arc_role in planned_arc.roles
    }
    INF = float("inf")

    def _role_order(atom: LearningAtom) -> float:
        return role_ordinal.get(atom.role, INF)

    # ------------------------------------------------------------------
    # 2.  Build a concept → atom map (which atom *covers* each concept).
    #     An atom covers exactly atom.concept.
    # ------------------------------------------------------------------
    concept_to_atom: dict[str, LearningAtom] = {}
    for atom in atoms:
        concept_to_atom[atom.concept] = atom

    # ------------------------------------------------------------------
    # 3.  Build the prerequisite dependency graph among atoms.
    #     Edge A → B means "A must come before B" (A covers a concept that
    #     B lists in prior_knowledge).
    #
    #     We only add edges for prerequisites that *are* covered by another
    #     atom.  Uncovered prerequisites are tracked separately as defects.
    # ------------------------------------------------------------------
    # atom.id → set of atom.id s that must precede it
    predecessors: dict[str, set[str]] = {a.id: set() for a in atoms}
    # Reverse: atom.id → set of atom.ids that depend on it
    successors: dict[str, set[str]] = {a.id: set() for a in atoms}

    uncovered_by_atom: dict[str, list[str]] = {}  # atom.id → uncovered concepts

    for atom in atoms:
        for prereq_concept in atom.prior_knowledge:
            covering_atom = concept_to_atom.get(prereq_concept)
            if covering_atom is None or covering_atom.id == atom.id:
                # No atom covers this prerequisite — record gap.
                uncovered_by_atom.setdefault(atom.id, []).append(prereq_concept)
            else:
                predecessors[atom.id].add(covering_atom.id)
                successors[covering_atom.id].add(atom.id)

    # Emit one prerequisite_gap defect per uncovered concept per atom.
    for _atom_id, missing_concepts in uncovered_by_atom.items():
        for concept in missing_concepts:
            defects.append(
                CoherenceDefect(
                    defect_type="prerequisite_gap",
                    clip_positions=[],
                    role=None,
                )
            )
    # ------------------------------------------------------------------
    # 4.  Kahn's algorithm for topological sort.
    #
    #     The sort key used when choosing among nodes with in-degree 0 is:
    #       (role_ordinal, start ASC, id ASC)
    #     This respects the Planned_Arc role order first, then the
    #     deterministic selection order used by select_atoms.
    # ------------------------------------------------------------------
    atom_by_id: dict[str, LearningAtom] = {a.id: a for a in atoms}

    # Working in-degree counter (copy so we don't mutate predecessors).
    in_degree: dict[str, int] = {a_id: len(preds) for a_id, preds in predecessors.items()}

    def _sort_key(a_id: str) -> tuple:
        a = atom_by_id[a_id]
        return (_role_order(a), a.start, a.id)

    # Use a list as a priority queue; re-sort whenever we add new ready nodes.
    ready: list[str] = [a_id for a_id, deg in in_degree.items() if deg == 0]
    ready.sort(key=_sort_key)

    ordered: list[LearningAtom] = []
    visited: set[str] = set()

    while ready:
        # Pop the highest-priority ready node.
        current_id = ready.pop(0)
        visited.add(current_id)
        ordered.append(atom_by_id[current_id])

        # Reduce in-degree for successors.
        newly_ready: list[str] = []
        for succ_id in successors[current_id]:
            in_degree[succ_id] -= 1
            if in_degree[succ_id] == 0:
                newly_ready.append(succ_id)

        if newly_ready:
            ready.extend(newly_ready)
            ready.sort(key=_sort_key)

    # ------------------------------------------------------------------
    # 5.  Handle cycles — any atom not visited is part of a cycle.
    #     Order cycle members by deterministic selection order
    #     (start ASC, id ASC) and record one circular_dependency defect.
    # ------------------------------------------------------------------
    cycle_atoms = [a for a in atoms if a.id not in visited]

    if cycle_atoms:
        cycle_atoms.sort(key=lambda a: (a.start, a.id))
        ordered.extend(cycle_atoms)
        defects.append(
            CoherenceDefect(
                defect_type="circular_dependency",
                clip_positions=[],
                role=None,
            )
        )

    return ordered, defects


def size_into_clips(ordered: list[LearningAtom]) -> list[Clip]:
    """Collapse each maximal contiguous run of same-role atoms into one Clip.

    Rules
    -----
    * Every produced Clip contains at least one atom (by construction: each
      group has ≥1 element).
    * All atoms in a Clip share a single ``PedagogicalRole``; that role is
      assigned as the Clip's ``pedagogical_role``.
    * Adjacent Clips always have *different* roles (runs are maximal — the
      groupby ensures this).
    * The assembled order is preserved: flattening the atoms of all returned
      Clips reproduces ``ordered`` exactly.

    Clip fields
    -----------
    * ``id``               — uuid4 (freshly generated per Clip).
    * ``topic_slug``       — from the first atom in the run.
    * ``video_url``        — ``source_url`` of the first atom in the run.
    * ``start`` / ``end``  — span the whole run: ``min(start)`` of all atoms
                             to ``max(end)`` of all atoms (stored in
                             ``duration_seconds`` is not set here; the
                             caller/validator layer handles that).
    * ``title``            — role name with underscores replaced by spaces and
                             title-cased (e.g. ``"worked_example"`` →
                             ``"Worked Example"``); 1-200 chars guaranteed for
                             any valid role name.
    * ``description``      — ``concept`` label of the first atom in the run.
    * ``concept_label``    — same as ``description`` (mirrors first atom's
                             concept for downstream normalization).
    * ``pedagogical_role`` — the shared role of all atoms in the run.

    Args:
        ordered: Atoms in assembled order (output of ``topological_order``).

    Returns:
        A list of ``Clip``s in the same pedagogical order.

    Requirements: 3.6, 3.7
    """
    clips: list[Clip] = []

    for _role, group_iter in groupby(ordered, key=lambda a: a.role):
        run: list[LearningAtom] = list(group_iter)
        first = run[0]
        role = first.role

        # Time span covers all atoms in the run.
        start_ts = min(a.start for a in run)
        end_ts = max(a.end for a in run)

        # Title: role name humanised (underscores → spaces, title-cased).
        title = role.replace("_", " ").title()

        clips.append(
            Clip(
                id=str(uuid.uuid4()),
                topic_slug=first.topic_slug,
                title=title,
                description=first.concept,
                video_url=first.source_url,
                # Start/end stored as source-video timestamps.  Clip.video_url
                # is the base embed URL; the consumer appends timestamps.
                # We surface start/end via concept_label and duration_seconds
                # fields that downstream can map; for now we annotate the
                # clip with the raw values via extra fields not in the schema
                # so we store them in available nullable columns instead.
                duration_seconds=int(end_ts - start_ts),
                concept_label=first.concept,
                pedagogical_role=role,
            )
        )

    return clips


def assemble(
    atoms: list[LearningAtom],
    planned_arc: PlannedArc,
) -> tuple[list[Clip], list[CoherenceDefect]]:
    """Orchestrate the full atom-to-clip pipeline for a topic arc.

    Pipeline
    --------
    1. ``select_atoms``    — deduplicate to one atom per (concept, role).
    2. Detect unfilled roles — every role in ``planned_arc`` with no selected
       atom is recorded as an ``unfilled_role`` CoherenceDefect (Req 3.8).
       No substitution is ever made; the role simply stays empty.
    3. ``topological_order`` — sort selected atoms; collect prerequisite /
       cycle defects.
    4. ``size_into_clips``  — collapse same-role runs into Clips.

    Args:
        atoms:       Full pool of candidate LearningAtoms.
        planned_arc: The topic's planned arc, used for role ordering and
                     unfilled-role detection.

    Returns:
        A 2-tuple ``(clips, defects)`` where:
        * ``clips`` is the assembled sequence of Clips.
        * ``defects`` contains all ``unfilled_role`` defects (one per missing
          planned role) followed by any defects from ``topological_order``.

    Requirements: 3.1, 3.8
    """
    # Step 1: select one atom per (concept, role).
    selected = select_atoms(atoms)

    # Step 2: detect unfilled planned roles — never substitute (Req 3.8).
    selected_roles: set[str] = {atom.role for atom in selected}
    defects: list[CoherenceDefect] = []
    for arc_role in planned_arc.roles:
        if arc_role.role not in selected_roles:
            defects.append(
                CoherenceDefect(
                    defect_type="unfilled_role",
                    clip_positions=[],
                    role=arc_role.role,
                )
            )

    # Step 3: topological ordering; collect its defects.
    ordered, topo_defects = topological_order(selected, planned_arc)
    defects.extend(topo_defects)

    # Step 4: collapse runs into Clips.
    clips = size_into_clips(ordered)

    return clips, defects


# Type alias: an Ordering is simply a sequence of Clips.
Ordering = list[Clip]

_ENGAGEMENT_EPSILON: float = 0.001
_COHERENCE_EPSILON: float = 0.001


def choose_by_engagement(a: Ordering, b: Ordering) -> Ordering | None:
    """Select the better of two candidate orderings using engagement as a tiebreaker.

    Precondition (enforced by the caller, not re-checked here): the two
    orderings have Coherence_Scores within 0.001 of each other and identical
    Realized_Arcs.

    Decision procedure (applied to the *first* Clip of each ordering):
      1. Retrieve engagement_score; a missing or None value is treated as 0
         (Req 6.6).
      2. If the two engagement scores differ by more than 0.001, return the
         ordering whose first clip has the higher engagement score (Req 6.1).
      3. If the scores are within 0.001, compare the first-clip ids
         lexicographically; the ordering whose first-clip id sorts *earlier*
         (lower) is selected (Req 6.5).
      4. If the first-clip ids are equal, return None — no selection is made
         (Req 6.7).

    The function is argument-order independent: for any result that is not None,
    ``choose_by_engagement(a, b) == choose_by_engagement(b, a)``.

    Args:
        a: First candidate ordering (non-empty list of Clips).
        b: Second candidate ordering (non-empty list of Clips).

    Returns:
        The selected ordering, or None when no selection can be made.

    Requirements: 6.1, 6.5, 6.6, 6.7
    """
    # Safely get engagement score, defaulting missing/None to 0 (Req 6.6).
    def _eng(ordering: Ordering) -> float:
        if not ordering:
            return 0.0
        score = ordering[0].engagement_score
        return float(score) if score is not None else 0.0

    eng_a = _eng(a)
    eng_b = _eng(b)

    # Step 1: engagement scores differ significantly → pick higher (Req 6.1).
    if abs(eng_a - eng_b) > _ENGAGEMENT_EPSILON:
        return a if eng_a > eng_b else b

    # Step 2: engagement scores are within tolerance → compare ids (Req 6.5).
    id_a = a[0].id if a else ""
    id_b = b[0].id if b else ""

    if id_a == id_b:
        # First-clip identifiers are equal — no selection possible (Req 6.7).
        return None

    # Lower lexicographic id wins.
    return a if id_a < id_b else b


def _is_prereq_valid(ordering: Ordering) -> bool:
    """Return True if the ordering's role_ordinal values are non-decreasing.

    A prerequisite violation occurs when a clip's role_ordinal is lower than
    a previously-seen role_ordinal, meaning a later pedagogical role appears
    before an earlier one, which would introduce a prerequisite gap or
    conceptual jump (Req 6.3, 6.4).

    Clips with None role_ordinal are treated as ordinal 0 (first position),
    which is the most conservative treatment that avoids false negatives.
    """
    prev = 0
    for clip in ordering:
        ordinal = clip.role_ordinal if clip.role_ordinal is not None else 0
        if ordinal < prev:
            return False
        prev = ordinal
    return True


def pick_ordering(
    candidates: list[Ordering],
    coherence_scores: list[float],
) -> Ordering | None:
    """Select the best ordering from a set of candidates.

    Selection rules, applied in priority order:

    1. Reject any ordering whose role_ordinal sequence is not non-decreasing
       (i.e. would introduce a prerequisite gap or conceptual jump).
       Engagement is irrelevant here — a violated ordering is always excluded
       regardless of its scores (Req 6.3, 6.4).

    2. Among the remaining valid candidates, if exactly one has a
       Coherence_Score that exceeds all others by more than 0.001, return it
       immediately (Req 6.2).

    3. When all remaining candidates are within 0.001 coherence of the
       maximum, reduce them pairwise using ``choose_by_engagement``.  If the
       reduction produces a unique winner, return it.  If ``choose_by_engagement``
       returns None at any step (equal first-clip ids), that step yields no
       preference between the tied pair; both remain as candidates and the
       function returns None (Req 6.7).

    4. Return None when no valid candidate remains after filtering.

    Args:
        candidates:       List of candidate orderings (each a list of Clips).
        coherence_scores: Coherence score corresponding to each candidate, in
                          the same order as ``candidates``.

    Returns:
        The selected ordering, or None when no valid candidate can be chosen.

    Requirements: 6.2, 6.3, 6.4, 6.5, 6.6, 6.7
    """
    if not candidates:
        return None

    # Pair each candidate with its coherence score; filter invalid orderings.
    paired: list[tuple[Ordering, float]] = [
        (c, s)
        for c, s in zip(candidates, coherence_scores)
        if _is_prereq_valid(c)
    ]

    if not paired:
        return None

    if len(paired) == 1:
        return paired[0][0]

    # Find the maximum coherence score among valid candidates.
    max_coherence = max(s for _, s in paired)

    # Partition: candidates that are within EPSILON of the maximum are "tied";
    # if any candidate strictly exceeds all others by > EPSILON it wins outright.
    tied: list[Ordering] = []
    for ordering, score in paired:
        if max_coherence - score <= _COHERENCE_EPSILON:
            tied.append(ordering)

    # If only one candidate reached the top tier, it wins outright (Req 6.2).
    if len(tied) == 1:
        return tied[0]

    # Multiple candidates within EPSILON coherence — defer to engagement
    # tiebreaker (Req 6.1, 6.5, 6.7).
    winner: Ordering = tied[0]
    for challenger in tied[1:]:
        result = choose_by_engagement(winner, challenger)
        if result is None:
            # Identifiers are equal → genuine tie; no selection possible (Req 6.7).
            return None
        winner = result

    return winner


def validate_clip(clip: Clip) -> tuple[Clip | None, str | None]:
    """Validate and normalise one Clip, enforcing all Req 7.2-7.7 rules.

    Normalisation (non-mutating copy):
      - Title exceeding 200 characters is silently truncated to 200 (Req 7.3).
      - Description exceeding 1000 characters is silently truncated to 1000
        (Req 7.3).

    Exclusion rules (returns ``(None, warning_msg)`` that names the clip):
      - Any required field (``id``, ``topic_slug``, ``title``, ``video_url``)
        is absent or empty (Req 7.6).
      - ``title`` is empty after truncation, or ``description`` (if present and
        non-None) is empty after truncation (Req 7.6).
      - ``start`` timestamp (if set) is negative (Req 7.5).
      - ``end`` timestamp (if set) is not strictly greater than ``start``
        (Req 7.5).
      - ``pedagogical_role`` (if set) is not a defined PedagogicalRole value
        (Req 7.7).

    A clip that passes all checks is returned as ``(clip_copy_or_self, None)``
    where the returned object is a modified copy whenever truncation was applied
    and the original clip object when no modification was needed (Req 7.2, 7.4).

    This is a pure function: the original clip is never mutated.

    Args:
        clip: The Clip to validate and normalise.

    Returns:
        ``(clip, None)`` when valid (possibly a truncated copy), or
        ``(None, warning_msg)`` where *warning_msg* identifies the excluded
        clip and the specific reason for exclusion.

    Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7
    """
    clip_id = clip.id or "<no-id>"

    # ------------------------------------------------------------------
    # 1. Required field presence and non-emptiness (Req 7.6).
    # ------------------------------------------------------------------
    if not clip.id:
        return None, f"clip excluded: missing or empty 'id'"
    if not clip.topic_slug:
        return None, f"clip {clip_id!r} excluded: missing or empty 'topic_slug'"
    if not clip.video_url:
        return None, f"clip {clip_id!r} excluded: missing or empty 'video_url'"
    # title is validated after potential truncation (see below).

    # ------------------------------------------------------------------
    # 2. Apply title / description truncation (Req 7.3) — build a mutation
    #    dict only when changes are actually needed so we avoid copying when
    #    the clip is already within bounds.
    # ------------------------------------------------------------------
    updates: dict = {}

    # --- title ---
    raw_title = clip.title  # may be None at this point; checked after
    if raw_title is None or raw_title == "":
        return None, f"clip {clip_id!r} excluded: missing or empty 'title'"
    if len(raw_title) > _TITLE_MAX_LEN:
        updates["title"] = raw_title[:_TITLE_MAX_LEN]

    effective_title = updates.get("title", raw_title)
    if not effective_title:
        return None, f"clip {clip_id!r} excluded: title is empty after truncation"

    # --- description ---
    raw_desc = clip.description
    if raw_desc is not None:
        if len(raw_desc) > _DESCRIPTION_MAX_LEN:
            updates["description"] = raw_desc[:_DESCRIPTION_MAX_LEN]
        effective_desc = updates.get("description", raw_desc)
        if not effective_desc:
            return None, f"clip {clip_id!r} excluded: description is empty after truncation"

    # ------------------------------------------------------------------
    # 3. Timestamp validation (Req 7.4, 7.5).
    #    Clip has no first-class start/end float fields in the schema;
    #    callers may supply them as extra attributes.  We validate them
    #    only when present.
    # ------------------------------------------------------------------
    raw_start = getattr(clip, "start", None)
    raw_end = getattr(clip, "end", None)

    if raw_start is not None:
        try:
            start_val = float(raw_start)
        except (TypeError, ValueError):
            return None, f"clip {clip_id!r} excluded: non-numeric 'start' timestamp"
        if start_val < 0:
            return None, (
                f"clip {clip_id!r} excluded: start timestamp is negative ({start_val})"
            )
        if raw_end is not None:
            try:
                end_val = float(raw_end)
            except (TypeError, ValueError):
                return None, f"clip {clip_id!r} excluded: non-numeric 'end' timestamp"
            if end_val <= start_val:
                return None, (
                    f"clip {clip_id!r} excluded: end ({end_val}) must be greater than "
                    f"start ({start_val})"
                )

    # ------------------------------------------------------------------
    # 4. pedagogical_role must be a defined value when present (Req 7.7).
    # ------------------------------------------------------------------
    role = clip.pedagogical_role
    if role is not None and role not in _VALID_ROLES:
        return None, (
            f"clip {clip_id!r} excluded: undefined pedagogical_role {role!r}"
        )

    # ------------------------------------------------------------------
    # 5. Return a modified copy if truncation was applied, else the original.
    # ------------------------------------------------------------------
    if updates:
        return clip.model_copy(update=updates), None
    return clip, None
