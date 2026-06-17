"""Property-based test for Idea_Unit ordering and non-overlap.

# Feature: deep-content-ingestion, Property 3: Idea_Units are ordered and non-overlapping

For any Source_Video transcript, the Idea_Units produced by
``build_idea_units`` are ordered by ascending start timestamp and no two units
overlap in time (each unit's start is at or after the previous unit's end).

Validates: Requirements 1.4
"""
from hypothesis import given, settings, strategies as st

from app.services.deep_decoder import build_idea_units


_text_st = st.text(
    alphabet=st.characters(min_codepoint=1, max_codepoint=0x2FFF),
    max_size=20,
)


@st.composite
def _segment(draw):
    return {
        "start": draw(
            st.floats(min_value=0.0, max_value=120.0, allow_nan=False, allow_infinity=False)
        ),
        "duration": draw(
            st.floats(min_value=0.01, max_value=30.0, allow_nan=False, allow_infinity=False)
        ),
        "text": draw(_text_st),
    }


_transcript_st = st.one_of(
    st.just([]),                                   # empty edge case
    st.lists(_segment(), min_size=1, max_size=1),  # single-segment edge case
    st.lists(_segment(), min_size=0, max_size=15),  # general (incl. empty)
)


class TestIdeaUnitOrdering:
    @settings(max_examples=100)
    @given(transcript=_transcript_st)
    def test_units_are_ordered_and_non_overlapping(self, transcript):
        units = build_idea_units(transcript, "vid-1")

        # Ascending start order.
        starts = [u.start for u in units]
        assert starts == sorted(starts)

        # No two units overlap: each unit starts at or after the previous end.
        for current, nxt in zip(units, units[1:]):
            assert nxt.start >= current.end
