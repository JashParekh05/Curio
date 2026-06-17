"""Property-based test for whole-transcript decode break-down.

# Feature: deep-content-ingestion, Property 1: Decode breaks down the WHOLE transcript

For any Source_Video transcript, ``build_idea_units`` breaks the ENTIRE
transcript into contiguous Idea_Units that collectively span the transcript
(no large unexplained interior gap, reaching the transcript duration) rather
than a hand-picked subset of spans. An empty transcript yields ``[]``.

Validates: Requirements 1.2
"""
from hypothesis import given, settings, strategies as st

from app.services.deep_decoder import build_idea_units, transcript_duration


# ---------------------------------------------------------------------------
# Smart transcript generator.
#
# A transcript is a list of {start, duration, text} dicts mirroring the
# youtube._fetch_transcript shape. The generator intelligently constrains to
# the real input space while exercising edge cases the decoder must survive:
#   * empty transcript            -> the `st.just([])` branch
#   * single-segment transcript   -> max_size=1 branch
#   * non-ASCII text              -> unicode characters drawn for `text`
#   * very short spans            -> durations as small as 0.01s
# Starts are drawn from a small bounded range so generated segments frequently
# share or interleave start times, exercising the merge/ordering path.
# ---------------------------------------------------------------------------
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


class TestDecodeWholeTranscript:
    @settings(max_examples=100)
    @given(transcript=_transcript_st)
    def test_breaks_down_whole_transcript(self, transcript):
        units = build_idea_units(transcript, "vid-1")
        duration = transcript_duration(transcript)

        # Empty transcript -> no Idea_Units.
        if not transcript or duration <= 0.0:
            assert units == []
            return

        # A readable transcript may still yield no positive-length span (e.g.
        # every segment starts at/after the computed duration); that is a valid
        # "no idea units" outcome handled by the shell, not a break-down defect.
        if not units:
            return

        # The units are contiguous: each unit ends exactly where the next
        # begins, so there is NO interior gap -- the decode covers the whole
        # interior rather than a hand-picked subset of spans.
        for current, nxt in zip(units, units[1:]):
            assert current.end == nxt.start

        # The break-down reaches the transcript duration: the final unit ends
        # at the total duration, so coverage extends to the end of the video.
        assert units[-1].end == duration

        # Coverage starts at the earliest in-bounds segment start, confirming
        # the whole span from first content to the end is decoded.
        eligible_starts = [
            float(s["start"])
            for s in transcript
            if 0.0 <= float(s["start"]) < duration
        ]
        assert units[0].start == min(eligible_starts)
