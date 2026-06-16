"""Property-based test for re-search repair preservation.

# Feature: content-hook-engagement, Property 21: Re-search with no candidate preserves the sequence and flags it

For any assembled sequence, when a re-search repair returns no candidate, the
post-repair sequence is identical to the prior sequence and a ``missing_piece``
defect is recorded.

Validates: Requirements 4.9
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import (
    Clip,
    CoherenceDefect,
    PedagogicalRole,
    PlannedArc,
)
from app.services.coherence import _apply_one_repair

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _clip(draw, index: int) -> Clip:
    """Build a single varied Clip."""
    role = draw(st.sampled_from([None, *_VALID_ROLES]))
    return Clip(
        id=f"clip-{index}",
        topic_slug="topic",
        title=draw(st.text(min_size=1, max_size=30)),
        video_url=f"https://example.com/v{index}",
        pedagogical_role=role,
    )


@st.composite
def _clips_list(draw) -> list[Clip]:
    """Generate a varied list of clips (possibly empty)."""
    n = draw(st.integers(min_value=0, max_value=8))
    return [draw(_clip(i)) for i in range(n)]


# Two flavours of "no candidate" re-search: absent (None) and present-but-empty.
_NO_CANDIDATE_RE_SEARCH = st.sampled_from(
    [None, (lambda role: None)]
)


class TestResearchPreservation:
    @settings(max_examples=100)
    @given(
        clips=_clips_list(),
        defect_role=st.sampled_from([None, *_VALID_ROLES]),
        clip_positions=st.lists(st.integers(min_value=1, max_value=8), max_size=5),
        re_search=_NO_CANDIDATE_RE_SEARCH,
    )
    def test_no_candidate_preserves_sequence_and_flags_missing_piece(
        self, clips, defect_role, clip_positions, re_search
    ):
        defect = CoherenceDefect(
            defect_type="missing_piece",
            clip_positions=clip_positions,
            role=defect_role,
        )

        # missing_piece repair with no candidate uses neither planned_arc nor
        # atom_by_role; pass minimal/empty stand-ins.
        planned_arc = PlannedArc(
            topic_slug="topic", concept_type="default", roles=[]
        )

        result_clips, extra_defects = _apply_one_repair(
            defect=defect,
            clips=clips,
            planned_arc=planned_arc,
            atom_by_role={},
            planned_ordinal={},
            re_search=re_search,
        )

        # (a) The returned sequence is identical to the prior sequence.
        assert result_clips == clips, (
            "re-search with no candidate must preserve the prior sequence"
        )

        # (b) A missing_piece defect is recorded.
        missing = [d for d in extra_defects if d.defect_type == "missing_piece"]
        assert missing, (
            "expected a missing_piece defect to be recorded when re-search "
            f"returns no candidate; got extra_defects={extra_defects}"
        )
