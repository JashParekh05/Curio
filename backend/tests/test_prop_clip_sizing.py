"""Property-based test for clip sizing.

# Feature: content-hook-engagement, Property 12: Clips are maximal contiguous single-role runs in order

For any ordered list of Learning_Atoms, ``size_into_clips`` collapses each
maximal contiguous same-role run into exactly one Clip. The structural
guarantees asserted here:

  (a) the number of produced clips equals the number of maximal same-role runs
      in the input role sequence,
  (b) each clip's ``pedagogical_role`` equals the shared role of its run,
  (c) adjacent clips always have different ``pedagogical_role`` (runs are
      maximal),
  (d) the sequence of clip roles equals the run-length-encoded sequence of the
      input atom roles.

Validates: Requirements 3.6, 3.7
"""
from itertools import groupby
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import LearningAtom, PedagogicalRole
from app.services.arc_assembler import size_into_clips

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _ordered_atoms(draw):
    """Generate an ordered list of LearningAtoms with random roles in sequence.

    Order is whatever sequence is generated — ``size_into_clips`` operates on
    the list order as given (its caller is responsible for prior ordering), so
    the run-length encoding is computed over that same order.
    """
    n = draw(st.integers(min_value=0, max_value=30))
    atoms: list[LearningAtom] = []
    for i in range(n):
        role = draw(st.sampled_from(_VALID_ROLES))
        start = draw(
            st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
        )
        atoms.append(
            LearningAtom(
                id=f"atom-{i}",
                topic_slug="topic",
                video_id="vid",
                source_url="https://example.com/v",
                role=role,
                concept=f"concept-{i}",
                prior_knowledge=[],
                start=start,
                end=start + 5.0,
            )
        )
    return atoms


def _run_length_roles(atoms: list[LearningAtom]) -> list[str]:
    """Run-length-encoded sequence of atom roles (collapse adjacent repeats)."""
    return [role for role, _group in groupby(atoms, key=lambda a: a.role)]


class TestClipSizing:
    @settings(max_examples=100)
    @given(atoms=_ordered_atoms())
    def test_clips_are_maximal_contiguous_single_role_runs(self, atoms):
        clips = size_into_clips(atoms)

        expected_runs = _run_length_roles(atoms)

        # (a) one clip per maximal same-role run.
        assert len(clips) == len(expected_runs)

        # (b) each clip's role equals the role of its run, and
        # (d) the clip role sequence equals the run-length-encoded role sequence.
        clip_roles = [clip.pedagogical_role for clip in clips]
        assert clip_roles == expected_runs

        # (c) adjacent clips have different roles (runs are maximal).
        for prev, nxt in zip(clip_roles, clip_roles[1:]):
            assert prev != nxt

        # Every produced clip is backed by at least one atom (>=1 atom per run):
        # guaranteed structurally since each run is non-empty, reflected by the
        # count equality above when atoms is non-empty.
        if atoms:
            assert len(clips) >= 1
        else:
            assert clips == []
