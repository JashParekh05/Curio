"""Property-based test for the per-segment verdict truth table.

# Feature: deep-content-ingestion, Property 7: Per-segment verdict truth table

``judge_verdict`` is PASSING with reason 'ok' iff the quality score is at or above
the threshold AND the fit is 'belongs'. An 'off_role' fit is always failing; a
'belongs' fit below threshold fails with 'insufficient_quality'. When a segment is
both off-role and below threshold, the insufficient-quality reason takes
precedence (per the design's reason ordering), but the verdict is failing either
way.

Validates: Requirements 3.6, 3.7, 3.8
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.services.segment_judge import FitDecision, judge_verdict

_FITS = list(get_args(FitDecision))


class TestSegmentVerdictTruthTable:
    @settings(max_examples=100)
    @given(
        quality_score=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        fit=st.sampled_from(_FITS),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    def test_verdict_truth_table(self, quality_score, fit, threshold):
        verdict = judge_verdict(quality_score, fit, threshold)

        has_quality = quality_score >= threshold
        on_role = fit == "belongs"

        # The verdict echoes its inputs.
        assert verdict.quality_score == quality_score
        assert verdict.fit == fit

        if has_quality and on_role:
            # Both conditions met -> passing/ok (Req 3.8).
            assert verdict.status == "passing"
            assert verdict.reason == "ok"
        else:
            # Either condition unmet -> failing (Req 3.6, 3.7).
            assert verdict.status == "failing"
            # Insufficient quality is reported in preference to off-role.
            if not has_quality:
                assert verdict.reason == "insufficient_quality"
            else:
                # has_quality but off_role.
                assert verdict.reason == "off_role"

    @settings(max_examples=100)
    @given(
        quality_score=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    def test_off_role_is_always_failing(self, quality_score, threshold):
        # An off-role segment never passes, regardless of its quality (Req 3.7).
        verdict = judge_verdict(quality_score, "off_role", threshold)
        assert verdict.status == "failing"

    @settings(max_examples=100)
    @given(
        quality_score=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        threshold=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    def test_belongs_passes_iff_at_or_above_threshold(self, quality_score, threshold):
        # An on-role segment passes exactly when its score clears the threshold,
        # and a below-threshold on-role segment fails for insufficient quality
        # (Req 3.6, 3.8).
        verdict = judge_verdict(quality_score, "belongs", threshold)
        if quality_score >= threshold:
            assert verdict.status == "passing"
            assert verdict.reason == "ok"
        else:
            assert verdict.status == "failing"
            assert verdict.reason == "insufficient_quality"
