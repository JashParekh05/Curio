"""Segment_Mapper: pure, unit-testable decision functions that map
Learning_Atoms onto the Pedagogical_Roles of a topic's Planned_Arc.

This module contains the PURE decision core — the segment-to-role mapping
eligibility decision (Req 9.6) — and the best-effort ``map_and_assemble`` I/O
shell, which partitions atoms, assembles the mapped sequence with
``arc_assembler.assemble``, and evaluates that sequence with
``coherence.run_repair_loop`` and ``alignment.check_and_repair`` before any
segment is admitted.

The mapping uses the SAME deterministic total order as
``arc_assembler.select_atoms``: among atoms competing for one role, the atom
with the earliest ``start`` wins, with ``atom.id`` (lexicographic ascending)
as the final tiebreaker.
"""

import logging
from dataclasses import dataclass

from app.models.schemas import (
    AlignmentResult,
    ArcDiff,
    Clip,
    CoherenceDefect,
    CoherenceResult,
    LearningAtom,
    PedagogicalRole,
    PlannedArc,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MappedSegment:
    """One Learning_Atom mapped to exactly one Pedagogical_Role of the arc.

    Attributes:
        atom: The mapped Learning_Atom.
        pedagogical_role: The single arc role the atom was mapped to.
        role_ordinal: The 1-based ordinal of that role within the Planned_Arc.
    """

    atom: LearningAtom
    pedagogical_role: PedagogicalRole
    role_ordinal: int


@dataclass(frozen=True)
class MappingResult:
    """The complete partition of an input atom pool against a Planned_Arc.

    Attributes:
        mapped: At most one MappedSegment per Pedagogical_Role, ordered by the
            role's arc ordinal (ascending).
        unmapped: Atoms set aside as Unmapped_Segments — atoms whose role is
            absent from the arc, plus surplus atoms that lost the deterministic
            contest for an already-filled role. Their role is unchanged.
        unfilled_roles: Planned roles for which no atom was available. These are
            reported, never back-filled with a different role.
    """

    mapped: list[MappedSegment]
    unmapped: list[LearningAtom]
    unfilled_roles: list[PedagogicalRole]


def is_role_eligible(
    atom_role: PedagogicalRole,
    planned_arc: PlannedArc,
) -> bool:
    """Return True iff ``atom_role`` is one of the Planned_Arc's roles.

    An atom whose role is absent from the arc is ineligible for mapping and
    becomes an Unmapped_Segment. Pure.

    Args:
        atom_role: The role of the candidate Learning_Atom.
        planned_arc: The topic's planned arc.

    Returns:
        True if the role appears in ``planned_arc.roles``, else False.

    Requirements: 2.1, 2.5
    """
    return any(arc_role.role == atom_role for arc_role in planned_arc.roles)


def partition_segments(
    atoms: list[LearningAtom],
    planned_arc: PlannedArc,
) -> MappingResult:
    """Map each atom to AT MOST ONE planned role.

    Procedure
    ---------
    1. Partition the input atoms by eligibility (``is_role_eligible``): atoms
       whose role is absent from the arc become Unmapped_Segments immediately
       (role unchanged) — Req 2.5.
    2. For each Pedagogical_Role in the arc (processed once, in ascending
       ordinal order), select at most one atom using the SAME deterministic
       total order as ``arc_assembler.select_atoms``: earliest ``start`` first,
       then ``atom.id`` lexicographically. The winner is recorded as a
       ``MappedSegment``; every surplus atom competing for that already-filled
       role becomes an Unmapped_Segment — Req 2.1, 2.4, 2.6.
    3. Any planned role with no available atom is reported in
       ``unfilled_roles`` and is never back-filled with a different role —
       Req 2.7.

    The partition is **total** over the input: every input atom appears exactly
    once across ``mapped`` (as its ``atom``) and ``unmapped`` — no atom is lost
    or duplicated.

    Pure and deterministic: the same input always yields the same result.

    Args:
        atoms: Pool of candidate Learning_Atoms.
        planned_arc: The topic's planned arc.

    Returns:
        A ``MappingResult`` with ``mapped`` (ordered by arc ordinal),
        ``unmapped``, and ``unfilled_roles``.

    Requirements: 2.1, 2.4, 2.5, 2.6, 2.7
    """
    # Step 1: split candidates by eligibility, grouping eligible atoms by role.
    eligible_by_role: dict[str, list[LearningAtom]] = {}
    unmapped: list[LearningAtom] = []

    for atom in atoms:
        if is_role_eligible(atom.role, planned_arc):
            eligible_by_role.setdefault(atom.role, []).append(atom)
        else:
            unmapped.append(atom)

    mapped: list[MappedSegment] = []
    unfilled_roles: list[PedagogicalRole] = []

    # Step 2 & 3: walk arc roles in ascending ordinal order; each role is
    # processed at most once (guards against any duplicate role entries so the
    # partition stays total — no atom processed twice).
    seen_roles: set[str] = set()
    for arc_role in sorted(planned_arc.roles, key=lambda r: r.ordinal):
        if arc_role.role in seen_roles:
            continue
        seen_roles.add(arc_role.role)

        candidates = eligible_by_role.get(arc_role.role, [])
        if not candidates:
            # Planned role with no atom — reported, never back-filled (Req 2.7).
            unfilled_roles.append(arc_role.role)
            continue

        # Deterministic total order: earliest start, then atom id (Req 2.6).
        candidates_sorted = sorted(candidates, key=lambda a: (a.start, a.id))
        winner = candidates_sorted[0]
        mapped.append(
            MappedSegment(
                atom=winner,
                pedagogical_role=arc_role.role,
                role_ordinal=arc_role.ordinal,
            )
        )
        # Surplus atoms competing for an already-filled role -> Unmapped (Req 2.4).
        unmapped.extend(candidates_sorted[1:])

    return MappingResult(
        mapped=mapped,
        unmapped=unmapped,
        unfilled_roles=unfilled_roles,
    )


# ---------------------------------------------------------------------------
# Best-effort I/O shell: map_and_assemble
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssemblyOutcome:
    """The full result of mapping atoms onto a Planned_Arc and evaluating the
    assembled sequence before any admission.

    Attributes:
        mapping: The partition of the input atoms against the Planned_Arc
            (``MappingResult``: mapped / unmapped / unfilled_roles).
        clips: The assembled clip sequence produced by ``arc_assembler.assemble``
            from the Mapped_Segments. Empty when the Planned_Arc is absent.
        coherence: The ``CoherenceResult`` from ``coherence.run_repair_loop`` over
            the assembled sequence (Req 2.8). When the Planned_Arc is absent this
            carries a recorded coherence defect for the missing arc (Req 2.3).
        alignment: The ``AlignmentResult`` from ``alignment.check_and_repair``
            comparing the realized arc to the Planned_Arc (Req 2.8).
        arc_missing: True iff the Planned_Arc had no roles (not produced); nothing
            is mapped and a coherence defect is recorded (Req 2.3).
    """

    mapping: MappingResult
    clips: list[Clip]
    coherence: CoherenceResult
    alignment: AlignmentResult
    arc_missing: bool


# A coherence defect recording that the Planned_Arc itself is absent (Req 2.3).
# No specific role applies, so role is None and no clip positions are affected.
def _missing_arc_outcome() -> AssemblyOutcome:
    """Build the AssemblyOutcome for an absent/empty Planned_Arc (Req 2.3).

    Nothing is mapped, no sequence is assembled, and a single coherence defect
    is recorded indicating the missing Planned_Arc.
    """
    empty_mapping = MappingResult(mapped=[], unmapped=[], unfilled_roles=[])
    missing_arc_defect = CoherenceDefect(
        defect_type="missing_piece",
        clip_positions=[],
        role=None,
    )
    coherence = CoherenceResult(
        coherence_score=0.0,
        defects=[missing_arc_defect],
        round_index=0,
    )
    alignment = AlignmentResult(
        aligned=False,
        diff=ArcDiff(missing_roles=[], order_mismatch_positions=[], aligned=False),
        unresolved=True,
    )
    return AssemblyOutcome(
        mapping=empty_mapping,
        clips=[],
        coherence=coherence,
        alignment=alignment,
        arc_missing=True,
    )


def map_and_assemble(
    atoms: list[LearningAtom],
    planned_arc: PlannedArc,
    *,
    max_rounds: int = 3,
) -> AssemblyOutcome:
    """Map atoms onto the Planned_Arc, assemble, and evaluate before admission.

    Procedure
    ---------
    1. If the Planned_Arc has no roles (not produced), map nothing, flag
       ``arc_missing=True``, and record a coherence defect for the missing arc
       — no segment is mapped or admitted (Req 2.2, 2.3).
    2. Otherwise, partition the atoms with :func:`partition_segments`
       (Req 2.1, 2.4-2.7).
    3. Assemble the Mapped_Segments' atoms into a clip sequence with
       ``arc_assembler.assemble`` (Req 2.1).
    4. Evaluate the assembled sequence with ``coherence.run_repair_loop`` and
       ``alignment.check_and_repair`` BEFORE any Mapped_Segment is admitted
       (Req 2.8). Any unresolved defect surfaced here is what the Admission_Gate
       uses to withhold admission (Req 2.9); this function only produces the
       evaluation, it does not admit.

    Best-effort: this shell never raises. Each I/O step is wrapped so that a
    transient failure degrades gracefully (a neutral coherence/alignment result)
    rather than blocking the request path.

    Args:
        atoms: The pool of Learning_Atoms decoded for the Topic.
        planned_arc: The Topic's Planned_Arc.
        max_rounds: Upper bound on coherence/alignment repair rounds.

    Returns:
        An :class:`AssemblyOutcome` with the mapping, assembled clips, coherence
        and alignment evaluations, and the ``arc_missing`` flag.

    Requirements: 2.2, 2.3, 2.8, 2.9
    """
    # Step 1: absent/empty Planned_Arc -> map nothing, record missing-arc defect.
    if not planned_arc.roles:
        logger.info(
            "[segment_mapper] map_and_assemble: Planned_Arc has no roles "
            "(topic_slug=%s); mapping nothing and recording missing-arc defect",
            getattr(planned_arc, "topic_slug", "<unknown>"),
        )
        return _missing_arc_outcome()

    # Step 2: partition atoms against the arc (pure).
    mapping = partition_segments(atoms, planned_arc)

    # Step 3: assemble the mapped sequence (best-effort).
    mapped_atoms = [segment.atom for segment in mapping.mapped]
    assemble_defects: list[CoherenceDefect] = []
    clips: list[Clip] = []
    try:
        from app.services import arc_assembler

        clips, assemble_defects = arc_assembler.assemble(mapped_atoms, planned_arc)
    except Exception as exc:  # best-effort: never raise (Req 9.1)
        logger.warning(
            "[segment_mapper] map_and_assemble: assemble failed: %s", exc
        )
        clips = []
        assemble_defects = []

    # Step 4a: coherence evaluation/repair over the assembled sequence (Req 2.8).
    try:
        from app.services import coherence as coherence_mod

        coherence = coherence_mod.run_repair_loop(
            clips, planned_arc, atoms, max_rounds=max_rounds
        )
    except Exception as exc:  # best-effort fallback (Req 9.1)
        logger.warning(
            "[segment_mapper] map_and_assemble: coherence failed: %s", exc
        )
        coherence = CoherenceResult(coherence_score=0.5, defects=[], round_index=0)

    # Surface the assembly-time defects (e.g. unfilled_role) alongside the
    # coherence defects so unfilled planned roles are recorded (Req 2.7).
    if assemble_defects:
        coherence = CoherenceResult(
            coherence_score=coherence.coherence_score,
            defects=list(coherence.defects) + assemble_defects,
            round_index=coherence.round_index,
        )

    # Step 4b: plan-alignment check/repair over the assembled sequence (Req 2.8).
    try:
        from app.services import alignment as alignment_mod

        alignment = alignment_mod.check_and_repair(
            clips, planned_arc, atoms, max_attempts=max_rounds
        )
    except Exception as exc:  # best-effort fallback (Req 9.1)
        logger.warning(
            "[segment_mapper] map_and_assemble: alignment failed: %s", exc
        )
        alignment = AlignmentResult(
            aligned=False,
            diff=ArcDiff(
                missing_roles=[], order_mismatch_positions=[], aligned=False
            ),
            unresolved=True,
        )

    return AssemblyOutcome(
        mapping=mapping,
        clips=clips,
        coherence=coherence,
        alignment=alignment,
        arc_missing=False,
    )
