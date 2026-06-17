"""Property-based test for the decode -> map -> judge -> admit funnel counts.

# Feature: deep-content-ingestion, Property 10: Funnel counts are non-negative and monotonic

For ANY raw counts (including negatives), ``funnel_counts`` coerces each to a
non-negative integer and clamps the chain downward so the funnel narrows at
every stage. The result therefore always satisfies
``0 <= admitted <= passed <= mapped <= idea_units``.

Validates: Requirements 4.6
"""
from hypothesis import given, settings, strategies as st

from app.services.admission_gate import funnel_counts

# Wide range incl. negatives to exercise the non-negative coercion and the
# downward clamp at every stage.
_counts = st.integers(min_value=-50, max_value=200)


class TestFunnelCounts:
    @settings(max_examples=100)
    @given(
        idea_units=_counts,
        mapped=_counts,
        passed=_counts,
        admitted=_counts,
        video_id=st.text(max_size=20),
    )
    def test_counts_are_non_negative_and_monotonic(
        self, idea_units, mapped, passed, admitted, video_id
    ):
        funnel = funnel_counts(idea_units, mapped, passed, admitted, video_id)

        # All four counts are plain non-negative ints.
        for value in (
            funnel.idea_units,
            funnel.mapped,
            funnel.passed,
            funnel.admitted,
        ):
            assert isinstance(value, int)
            assert value >= 0

        # Monotonic narrowing of the funnel (Req 4.6).
        assert funnel.admitted <= funnel.passed
        assert funnel.passed <= funnel.mapped
        assert funnel.mapped <= funnel.idea_units

        # The Source_Video id is carried through verbatim.
        assert funnel.video_id == video_id

    @settings(max_examples=100)
    @given(
        idea_units=st.integers(min_value=0, max_value=200),
        mapped=st.integers(min_value=0, max_value=200),
        passed=st.integers(min_value=0, max_value=200),
        admitted=st.integers(min_value=0, max_value=200),
    )
    def test_already_monotonic_inputs_are_preserved(
        self, idea_units, mapped, passed, admitted
    ):
        # When the raw inputs are already non-negative and non-increasing, the
        # clamp is a no-op and the counts pass through unchanged.
        if not (admitted <= passed <= mapped <= idea_units):
            return
        funnel = funnel_counts(idea_units, mapped, passed, admitted, "vid")
        assert funnel.idea_units == idea_units
        assert funnel.mapped == mapped
        assert funnel.passed == passed
        assert funnel.admitted == admitted
