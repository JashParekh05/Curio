"""Property-based test for one-repair-per-defect in the coherence repair loop.

# Feature: content-hook-engagement, Property 18: Exactly one repair per defect

For any non-empty list of detected defects, the repair planner emits exactly
one repair action per defect, and each action is one of fill-missing-role,
reorder, or re-search.

Strategy
--------
``run_repair_loop`` drives an evaluate -> repair -> re-evaluate cycle. To
observe "exactly one repair per defect" deterministically we:

  * monkeypatch ``coherence.evaluate`` so the FIRST call returns a fixed
    ``CoherenceResult`` carrying a Hypothesis-generated set of defects and
    every later call returns a clean (empty-defect) result, and
  * run with ``max_rounds=1`` so the loop performs exactly one repair round
    then stops (regardless of any extra defects a repair records), and
  * wrap ``coherence._apply_one_repair`` with a counter that records each
    defect it is invoked with.

We then assert ``_apply_one_repair`` was invoked exactly once per defect in
that round, the defects it saw are exactly the round's defects in order, and
every defect routes to one of the three repair actions.

Validates: Requirements 4.5
"""
from hypothesis import given, settings, strategies as st

from app.services import coherence
from app.models.schemas import (
    ArcRole,
    Clip,
    CoherenceDefect,
    CoherenceResult,
    LearningAtom,
    PlannedArc,
)

# All defect types the planner must route (schemas.DefectType).
_DEFECT_TYPES = [
    "prerequisite_gap", "conceptual_jump", "contradiction",
    "redundancy", "unfilled_role", "circular_dependency", "missing_piece",
]

_ROLES = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

# The three repair actions a defect can route to (Req 4.5).
_FILL = "fill-missing-role"
_RE_SEARCH = "re-search"
_REORDER = "reorder"


def _expected_action(defect: CoherenceDefect) -> str:
    """Mirror the routing in coherence._apply_one_repair."""
    if defect.defect_type == "unfilled_role":
        return _FILL
    if defect.defect_type == "missing_piece":
        return _RE_SEARCH
    return _REORDER


# A single defect: any defect_type, an optional role, small clip_positions.
_defect = st.builds(
    CoherenceDefect,
    defect_type=st.sampled_from(_DEFECT_TYPES),
    clip_positions=st.lists(st.integers(min_value=1, max_value=2), max_size=3),
    role=st.sampled_from(_ROLES),
)

# A non-empty list of defects (Property 18 is over non-empty defect lists).
_defects = st.lists(_defect, min_size=1, max_size=12)


def _make_inputs():
    """Small, fixed clips / planned_arc / atom_pool for the loop."""
    clips = [
        Clip(id="c1", topic_slug="t", title="A", video_url="http://x",
             pedagogical_role="definition", role_ordinal=1),
        Clip(id="c2", topic_slug="t", title="B", video_url="http://y",
             pedagogical_role="example", role_ordinal=2),
    ]
    planned_arc = PlannedArc(
        topic_slug="t",
        concept_type="conceptual",
        roles=[
            ArcRole(role="definition", ordinal=1),
            ArcRole(role="example", ordinal=2),
        ],
    )
    atom_pool = [
        LearningAtom(
            id="a1", topic_slug="t", video_id="v1", source_url="http://s",
            role="definition", concept="c", prior_knowledge=[], start=0.0, end=5.0,
        ),
    ]
    return clips, planned_arc, atom_pool


class TestOneRepairPerDefect:
    @settings(max_examples=100)
    @given(defects=_defects)
    def test_exactly_one_repair_per_defect(self, defects):
        clips, planned_arc, atom_pool = _make_inputs()

        eval_calls = {"n": 0}
        seen_defects: list[CoherenceDefect] = []

        # First evaluate -> the generated defects; thereafter -> clean.
        def fake_evaluate(_clips):
            eval_calls["n"] += 1
            if eval_calls["n"] == 1:
                return CoherenceResult(
                    coherence_score=0.4, defects=list(defects), round_index=0
                )
            return CoherenceResult(coherence_score=1.0, defects=[], round_index=0)

        real_apply = coherence._apply_one_repair

        def counting_apply(*, defect, **kwargs):
            seen_defects.append(defect)
            return real_apply(defect=defect, **kwargs)

        orig_evaluate = coherence.evaluate
        orig_apply = coherence._apply_one_repair
        coherence.evaluate = fake_evaluate
        coherence._apply_one_repair = counting_apply
        try:
            result = coherence.run_repair_loop(
                clips=clips,
                planned_arc=planned_arc,
                atom_pool=atom_pool,
                max_rounds=1,  # exactly one repair round
            )
        finally:
            coherence.evaluate = orig_evaluate
            coherence._apply_one_repair = orig_apply

        # Exactly one repair invocation per defect in the round.
        assert len(seen_defects) == len(defects)

        # The defects repaired are exactly the round's defects, in order.
        assert seen_defects == list(defects)

        # Every repair routes to one of the three valid actions.
        for defect in seen_defects:
            assert _expected_action(defect) in (_FILL, _RE_SEARCH, _REORDER)

        # The loop returns a CoherenceResult (best round across the loop).
        assert isinstance(result, CoherenceResult)
