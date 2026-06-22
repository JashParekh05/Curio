"""Property-based test for bounded, clip-preserving alignment repair.

# Feature: content-hook-engagement, Property 25: Alignment repair is bounded and preserves clips when unresolved

For any unresolvable diff, ``check_and_repair`` performs at most 3 repair
attempts, then returns ``aligned=False`` with ``unresolved=True``, records the
unresolved difference, and returns the original clips unchanged.

We construct an UNRESOLVABLE case: a planned arc that requires a role for which
there is NO atom in ``atom_pool`` AND that role is not present among the clips.
The missing role can therefore never be filled, so ``check_and_repair`` must
exhaust its attempts and report the diff as unresolved while leaving the input
clip list untouched.

Validates: Requirements 5.7
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
def _unresolvable_case(draw):
    """A planned arc with >=1 unfillable missing role.

    At least one planned role is BOTH absent from the clips AND has no atom in
    the pool, so it can never be filled. To exercise the repair loop (rather
    than the empty-clip fast path) at least one planned role is present as a
    clip. Atoms are only ever provided for roles that are already present, so
    they can never resolve the missing roles.
    """
    template = draw(st.sampled_from([_CONCEPTUAL, _PROBLEM]))
    n = draw(st.integers(min_value=2, max_value=len(template)))
    roles = template[:n]
    ordinal_of = {r: i + 1 for i, r in enumerate(roles)}

    # Partition roles: at least one present, at least one unfillable-missing.
    missing_flags = draw(
        st.lists(st.booleans(), min_size=n, max_size=n).filter(
            lambda fs: any(fs) and not all(fs)
        )
    )
    present_roles = [r for r, miss in zip(roles, missing_flags) if not miss]
    unfillable_roles = [r for r, miss in zip(roles, missing_flags) if miss]

    planned = _planned_arc(roles)
    # Present clips in planned order.
    clips = [_clip_for(r, ordinal_of[r]) for r in sorted(present_roles, key=ordinal_of.get)]

    # The pool contains atoms ONLY for already-present roles (never for the
    # unfillable missing roles), so the missing roles can never be filled.
    atom_pool = [_atom_for(r) for r in present_roles]

    return planned, clips, atom_pool, unfillable_roles


class TestBoundedRepairPreservesClips:
    @settings(max_examples=100)
    @given(case=_unresolvable_case())
    def test_unresolvable_diff_is_bounded_and_preserves_clips(self, case):
        planned, clips, atom_pool, unfillable_roles = case

        # Snapshot the input list (identity of the list object and a content
        # copy) so we can verify it is returned unchanged.
        original_list_id = id(clips)
        original_contents = list(clips)

        result = check_and_repair(clips, planned, atom_pool)

        # Repair exhausted without resolving the diff.
        assert result.aligned is False
        assert result.unresolved is True

        # The unresolved difference is recorded: every unfillable role still
        # appears in the recorded missing roles.
        for role in unfillable_roles:
            assert role in result.diff.missing_roles, (
                f"unfillable role {role!r} should remain recorded as missing: "
                f"{result.diff.missing_roles}"
            )

        # The original clips list is unchanged in identity and content.
        assert id(clips) == original_list_id
        assert clips == original_contents
