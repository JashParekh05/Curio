"""Property-based test for diff-to-repair mapping in the alignment planner.

# Feature: content-hook-engagement, Property 24: Diffs map to the correct recorded repairs

For any non-aligned diff, ``check_and_repair`` emits a fill repair for each
missing role (and the missing role becomes filled when an atom is available)
and a reorder repair when any ordering difference exists.

Behavioural assertions (``check_and_repair`` is bounded and best-effort):

* Case A — fillable missing role: when a planned role is absent from the
  realized arc but an atom for it exists in ``atom_pool``, the post-repair diff
  no longer lists that role as missing (it was filled).
* Case B — pure reorder: when every planned role is present as a clip but the
  clips are out of planned order, ``check_and_repair`` returns ``aligned=True``
  with the clips arranged in planned ordinal order.

Validates: Requirements 5.4, 5.5
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import (
    ArcRole,
    Clip,
    LearningAtom,
    PlannedArc,
)
from app.services.alignment import check_and_repair

# Two disjoint, fully-ordered arc templates so generated planned arcs use a
# self-consistent set of distinct roles with consecutive ordinals.
_CONCEPTUAL = ["definition", "motivation", "mechanism", "example", "common_misconception"]
_PROBLEM = [
    "problem_statement",
    "meaning",
    "visualization",
    "approach",
    "worked_example",
    "edge_cases",
]


def _planned_arc(roles):
    """Build a PlannedArc with consecutive ordinals from 1 in the given order."""
    return PlannedArc(
        topic_slug="t",
        concept_type="conceptual",
        roles=[ArcRole(role=r, ordinal=i + 1) for i, r in enumerate(roles)],
    )


def _clip_for(role, ordinal):
    return Clip(
        id=f"clip-{role}",
        topic_slug="t",
        title=f"{role} clip",
        video_url="https://example.com/v",
        pedagogical_role=role,
        role_ordinal=ordinal,
    )


def _atom_for(role):
    return LearningAtom(
        id=f"atom-{role}",
        topic_slug="t",
        video_id=f"vid-{role}",
        source_url="https://example.com/s",
        role=role,
        concept=f"{role} concept",
        start=0.0,
        end=10.0,
    )


@st.composite
def _fillable_missing_case(draw):
    """A planned arc with >=1 present clip and >=1 fillable missing role.

    Present clips are emitted in planned order so the only repair required is a
    fill; every missing role has a matching atom in the pool.
    """
    template = draw(st.sampled_from([_CONCEPTUAL, _PROBLEM]))
    n = draw(st.integers(min_value=2, max_value=len(template)))
    roles = template[:n]

    # Choose which roles are missing: at least one missing, at least one present.
    missing_flags = draw(
        st.lists(st.booleans(), min_size=n, max_size=n).filter(
            lambda fs: any(fs) and not all(fs)
        )
    )
    present_roles = [r for r, miss in zip(roles, missing_flags) if not miss]
    missing_roles = [r for r, miss in zip(roles, missing_flags) if miss]

    planned = _planned_arc(roles)
    ordinal_of = {r: i + 1 for i, r in enumerate(roles)}

    # Present clips in planned order (sorted by ordinal).
    clips = [_clip_for(r, ordinal_of[r]) for r in sorted(present_roles, key=ordinal_of.get)]
    # An atom available for every missing role -> all are fillable.
    atom_pool = [_atom_for(r) for r in missing_roles]

    return planned, clips, atom_pool, missing_roles


@st.composite
def _pure_reorder_case(draw):
    """A planned arc with every role present as a clip but out of planned order."""
    template = draw(st.sampled_from([_CONCEPTUAL, _PROBLEM]))
    n = draw(st.integers(min_value=2, max_value=len(template)))
    roles = template[:n]
    planned = _planned_arc(roles)
    ordinal_of = {r: i + 1 for i, r in enumerate(roles)}

    # A permutation of the roles for the clip order.
    permuted = draw(st.permutations(roles))
    clips = [_clip_for(r, ordinal_of[r]) for r in permuted]

    return planned, clips


class TestDiffToRepairMapping:
    @settings(max_examples=100)
    @given(case=_fillable_missing_case())
    def test_fillable_missing_role_is_filled(self, case):
        planned, clips, atom_pool, missing_roles = case

        result = check_and_repair(clips, planned, atom_pool)

        # Each fillable missing role no longer appears as missing post-repair.
        for role in missing_roles:
            assert role not in result.diff.missing_roles, (
                f"role {role!r} had an atom available but remained missing: "
                f"{result.diff.missing_roles}"
            )

    @settings(max_examples=100)
    @given(case=_pure_reorder_case())
    def test_pure_reorder_becomes_aligned(self, case):
        planned, clips = case

        # No atoms needed: every role is already present, only order differs.
        result = check_and_repair(clips, planned, atom_pool=[])

        assert result.aligned is True
        assert result.unresolved is False
        # An aligned diff carries no missing roles or order mismatches.
        assert result.diff.missing_roles == []
        assert result.diff.order_mismatch_positions == []
