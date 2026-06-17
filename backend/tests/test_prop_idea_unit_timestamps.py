"""Property-based test for Idea_Unit timestamp bounds.

# Feature: deep-content-ingestion, Property 2: Idea_Unit timestamps are well-formed

For any Source_Video transcript, every Idea_Unit produced by
``build_idea_units`` satisfies ``0 <= start < end <= transcript_duration``.

Validates: Requirements 1.3
"""
from hypothesis import given, settings, strategies as st

from app.services.deep_decoder import (
    build_idea_units,
    idea_units_well_formed,
    transcript_duration,
)


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


class TestIdeaUnitTimestamps:
    @settings(max_examples=100)
    @given(transcript=_transcript_st)
    def test_timestamps_are_well_formed(self, transcript):
        duration = transcript_duration(transcript)
        units = build_idea_units(transcript, "vid-1")

        # Every produced unit's timestamps are in bounds and ordered within
        # the unit: 0 <= start < end <= duration.
        for unit in units:
            assert unit.start >= 0.0
            assert unit.start < unit.end
            assert unit.end <= duration

        # The pure invariant checker agrees with the produced sequence.
        assert idea_units_well_formed(units, duration)
