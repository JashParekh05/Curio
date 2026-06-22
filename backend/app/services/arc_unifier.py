"""Arc_Unifier and Section_Role_Map pure decision core.

Curio carries two competing "shape of a topic" models:

- the Legacy_Section_Model (``topic_sections``: section_index 0..3, titles
  hook -> what-is-it -> how-it-works -> outcomes), and
- the Role_Arc_Model (``topic_arcs`` + ``topic_arc_roles``: Pedagogical_Roles
  with consecutive ordinals).

This module collapses both into one canonical representation -- the
Canonical_Arc -- an ordered list of at least one Pedagogical_Role, each carrying
a consecutive integer ordinal starting at 1 with no gap and no duplicate. It is
the single source of truth for a Topic's teaching structure.

Following the repository convention (see ``topic_frontier.py``), this module
holds ONLY the pure decision logic: no DB, no clock, no global reads. Every
function is deterministic in its inputs and free of side effects -- it never
mutates the inputs passed in, always returning new values. The thin best-effort
I/O shell lives in ``arc_unifier_store.py``.

ASCII only.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.models.schemas import ArcRole, PedagogicalRole

# ---------------------------------------------------------------------------
# Section_Role_Map
# ---------------------------------------------------------------------------

#: Total, deterministic map from a Legacy_Section_Model section_index (0..3) to
#: exactly one Pedagogical_Role, preserving section order as ordinal order. The
#: four legacy sections (hook -> what-is-it -> how-it-works -> outcomes) map to
#: the conceptual arc roles motivation -> definition -> mechanism -> example.
#: (Req 1.4)
SECTION_ROLE_MAP: dict[int, PedagogicalRole] = {
    0: "motivation",
    1: "definition",
    2: "mechanism",
    3: "example",
}

#: Rejection indications recorded on a UnifyResult when no arc can be produced.
REJECTION_NON_CONSECUTIVE: str = "non_consecutive_ordinals"
REJECTION_SECTION_OUT_OF_RANGE: str = "section_index_out_of_range"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CanonicalArcRole:
    """One Pedagogical_Role in a Canonical_Arc with its ordinal.

    Attributes:
        role: The Pedagogical_Role this position teaches.
        ordinal: The 1-based position; consecutive from 1 across the arc with
            no gap and no duplicate (Req 1.1).
    """

    role: PedagogicalRole
    ordinal: int


@dataclass(frozen=True)
class CanonicalArc:
    """The single unified arc representation for a Topic.

    Attributes:
        topic_slug: The Topic this arc belongs to.
        roles: The ordered Pedagogical_Roles. Length >= 1 whenever an arc is
            present; ordinals are exactly ``1..len(roles)`` (Req 1.1).
    """

    topic_slug: str
    roles: tuple[CanonicalArcRole, ...]


@dataclass(frozen=True)
class UnifyResult:
    """Outcome of a single unification attempt for one Topic.

    Exactly one of the following is the meaningful outcome:

    - ``arc`` is not None -- a Canonical_Arc was produced (Req 1.2, 1.3, 1.5).
    - ``structureless`` is True -- neither model was present (Req 1.7).
    - ``rejection`` is not None -- a present model was malformed and no arc was
      produced (Req 1.9, 1.10).

    Attributes:
        topic_slug: The Topic this result is for.
        arc: The produced Canonical_Arc, or None when none was produced.
        structureless: True iff the Topic had neither model (Req 1.7).
        legacy_superseded: True iff both models were present and the arc was
            produced from the Role_Arc_Model (Req 1.5).
        rejection: A rejection indication (one of ``REJECTION_*``) or None.
    """

    topic_slug: str
    arc: CanonicalArc | None
    structureless: bool
    legacy_superseded: bool
    rejection: str | None


# ---------------------------------------------------------------------------
# Section_Role_Map lookup
# ---------------------------------------------------------------------------

def section_role(section_index: int) -> PedagogicalRole | None:
    """Map a Legacy_Section_Model section_index to its Pedagogical_Role.

    Total and deterministic over the inclusive range 0..3 (Req 1.4); returns
    None for any out-of-range index so the caller can reject it (Req 1.10).
    Pure: the same section_index always returns the same role.

    Args:
        section_index: A Legacy_Section_Model section index.

    Returns:
        The mapped Pedagogical_Role for 0..3, else None.

    Validates: Requirements 1.4, 1.10
    """
    return SECTION_ROLE_MAP.get(section_index)


# ---------------------------------------------------------------------------
# Ordinal validation
# ---------------------------------------------------------------------------

def _ordinals_consecutive_from_one(ordinals: list[int]) -> bool:
    """True iff ``ordinals`` is a non-empty permutation of ``1..len``.

    This rejects an empty set, a set that does not start at 1, any gap, and any
    duplicate -- exactly the Canonical_Arc ordinal invariant (Req 1.1, 1.9).
    """
    if not ordinals:
        return False
    return sorted(ordinals) == list(range(1, len(ordinals) + 1))


# ---------------------------------------------------------------------------
# Canonical_Arc construction
# ---------------------------------------------------------------------------

def canonical_arc_from_role_model(
    topic_slug: str, roles: list[ArcRole]
) -> UnifyResult:
    """Build a Canonical_Arc from a Role_Arc_Model.

    When the recorded ordinals are consecutive from 1 with no gap and no
    duplicate, the arc's roles are emitted in ascending recorded ordinal order
    (Req 1.2). An empty ``roles`` list means the Role_Arc_Model is absent, so
    the result is structureless rather than a rejection (Req 1.7). A present but
    malformed ordinal set (non-consecutive, gapped, or duplicate) produces no
    arc and records ``REJECTION_NON_CONSECUTIVE`` (Req 1.9). Pure (Req 1.8).

    Args:
        topic_slug: The Topic this arc is for.
        roles: The Role_Arc_Model rows (any input order).

    Returns:
        A :class:`UnifyResult` carrying the arc, a structureless flag, or a
        rejection indication.

    Validates: Requirements 1.1, 1.2, 1.7, 1.8, 1.9
    """
    if not roles:
        return UnifyResult(
            topic_slug=topic_slug,
            arc=None,
            structureless=True,
            legacy_superseded=False,
            rejection=None,
        )

    ordinals = [r.ordinal for r in roles]
    if not _ordinals_consecutive_from_one(ordinals):
        return UnifyResult(
            topic_slug=topic_slug,
            arc=None,
            structureless=False,
            legacy_superseded=False,
            rejection=REJECTION_NON_CONSECUTIVE,
        )

    ordered = sorted(roles, key=lambda r: r.ordinal)
    arc_roles = tuple(
        CanonicalArcRole(role=r.role, ordinal=position)
        for position, r in enumerate(ordered, start=1)
    )
    return UnifyResult(
        topic_slug=topic_slug,
        arc=CanonicalArc(topic_slug=topic_slug, roles=arc_roles),
        structureless=False,
        legacy_superseded=False,
        rejection=None,
    )


def canonical_arc_from_sections(
    topic_slug: str, section_indices: list[int]
) -> UnifyResult:
    """Build a Canonical_Arc from a Legacy_Section_Model.

    Each section_index is mapped via :data:`SECTION_ROLE_MAP` and the resulting
    Pedagogical_Roles are ordered by ascending section_index, with ordinals
    reassigned consecutively from 1 (Req 1.3). Duplicate indices collapse to one
    role so the arc keeps its consecutive-ordinal invariant. An empty list means
    the Legacy_Section_Model is absent, so the result is structureless rather
    than a rejection (Req 1.7). Any index outside 0..3 produces no arc and
    records ``REJECTION_SECTION_OUT_OF_RANGE`` (Req 1.10). Pure (Req 1.8).

    Args:
        topic_slug: The Topic this arc is for.
        section_indices: The Legacy_Section_Model section indices (any order).

    Returns:
        A :class:`UnifyResult` carrying the arc, a structureless flag, or a
        rejection indication.

    Validates: Requirements 1.1, 1.3, 1.7, 1.8, 1.10
    """
    if not section_indices:
        return UnifyResult(
            topic_slug=topic_slug,
            arc=None,
            structureless=True,
            legacy_superseded=False,
            rejection=None,
        )

    for idx in section_indices:
        if section_role(idx) is None:
            return UnifyResult(
                topic_slug=topic_slug,
                arc=None,
                structureless=False,
                legacy_superseded=False,
                rejection=REJECTION_SECTION_OUT_OF_RANGE,
            )

    distinct = sorted(set(section_indices))
    arc_roles = tuple(
        CanonicalArcRole(role=SECTION_ROLE_MAP[idx], ordinal=position)
        for position, idx in enumerate(distinct, start=1)
    )
    return UnifyResult(
        topic_slug=topic_slug,
        arc=CanonicalArc(topic_slug=topic_slug, roles=arc_roles),
        structureless=False,
        legacy_superseded=False,
        rejection=None,
    )


def unify_arc(
    topic_slug: str,
    role_model: list[ArcRole] | None,
    section_indices: list[int] | None,
) -> UnifyResult:
    """Collapse a Topic's two structure models into one Canonical_Arc.

    Implements the precedence in Req 1.2 / 1.3 / 1.5 / 1.7, where a present
    model is a non-empty one:

    - Role_Arc_Model present & valid           -> arc from the Role_Arc_Model.
    - both models present (role model valid)   -> arc from the Role_Arc_Model
                                                  with ``legacy_superseded=True``
                                                  (Req 1.5).
    - Role_Arc_Model present & malformed       -> rejection, no arc (Req 1.9);
                                                  the Role_Arc_Model takes
                                                  precedence so a present legacy
                                                  model is not consulted.
    - only Legacy_Section_Model present        -> arc from the sections (Req 1.3).
    - neither model present                    -> ``structureless=True``
                                                  (Req 1.7).

    Pure: the output depends only on the inputs (Req 1.8).

    Args:
        topic_slug: The Topic to unify.
        role_model: The Role_Arc_Model rows, or None/empty when absent.
        section_indices: The Legacy_Section_Model indices, or None/empty when
            absent.

    Returns:
        A single :class:`UnifyResult` for the Topic.

    Validates: Requirements 1.2, 1.3, 1.5, 1.7, 1.8, 1.9, 1.10
    """
    has_role_model = bool(role_model)
    has_sections = bool(section_indices)

    if has_role_model:
        result = canonical_arc_from_role_model(topic_slug, role_model or [])
        # Both models present and the role model won: record supersession.
        if result.arc is not None and has_sections:
            result = replace(result, legacy_superseded=True)
        return result

    if has_sections:
        return canonical_arc_from_sections(topic_slug, section_indices or [])

    return UnifyResult(
        topic_slug=topic_slug,
        arc=None,
        structureless=True,
        legacy_superseded=False,
        rejection=None,
    )


# ---------------------------------------------------------------------------
# Clip association
# ---------------------------------------------------------------------------

def associate_clip_role(
    arc: CanonicalArc, clip_role: PedagogicalRole | None
) -> CanonicalArcRole | None:
    """Associate an Admitted_Clip with its single Canonical_Arc role.

    Returns the Canonical_Arc role whose Pedagogical_Role equals the clip's
    recorded role (Req 1.6). When the clip carries no role, or its role is
    absent from the arc, returns None so the caller associates nothing and
    records an indication (Req 1.11). On the rare event that an arc carries the
    same role at more than one ordinal, the lowest-ordinal match is returned so
    the association stays single-valued. Pure (Req 1.8).

    Args:
        arc: The Topic's Canonical_Arc.
        clip_role: The clip's recorded Pedagogical_Role, or None.

    Returns:
        The matching :class:`CanonicalArcRole`, or None when there is no match.

    Validates: Requirements 1.6, 1.8, 1.11
    """
    if clip_role is None:
        return None
    for arc_role in arc.roles:
        if arc_role.role == clip_role:
            return arc_role
    return None
