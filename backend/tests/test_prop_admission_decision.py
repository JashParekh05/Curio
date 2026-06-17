"""Property-based test for the Admission_Gate single-segment decision.

# Feature: deep-content-ingestion, Property 8: Admission requires mapped + passing + coherent + aligned

``admission_decision`` admits a single segment IF AND ONLY IF all four gates
clear: the segment is mapped, its Segment_Verdict status is 'passing', and the
assembled sequence passed BOTH coherence and alignment. Otherwise it rejects
with the first failing reason in the priority order
``unmapped`` -> ``failing_verdict`` -> ``coherence`` -> ``alignment``. In
particular, whenever coherence OR alignment is false, nothing is admitted.

Validates: Requirements 2.9, 4.1, 4.2, 4.3
"""
from hypothesis import given, settings, strategies as st

from app.services.admission_gate import admission_decision
from app.services.segment_judge import SegmentVerdict, judge_verdict

# Three verdict shapes the decision must distinguish: a passing verdict, a
# failing verdict, and a missing (None) verdict. Built via the segment_judge
# pure core so the verdict objects are exactly what the pipeline produces.
_PASSING: SegmentVerdict = judge_verdict(1.0, "belongs", 0.5)
_FAILING_QUALITY: SegmentVerdict = judge_verdict(0.0, "belongs", 0.5)
_FAILING_OFF_ROLE: SegmentVerdict = judge_verdict(1.0, "off_role", 0.5)


def _verdict_for(kind: str) -> SegmentVerdict | None:
    if kind == "passing":
        return _PASSING
    if kind == "failing_quality":
        return _FAILING_QUALITY
    if kind == "failing_off_role":
        return _FAILING_OFF_ROLE
    return None  # 'none'


_VERDICT_KINDS = ["passing", "failing_quality", "failing_off_role", "none"]


class TestAdmissionDecision:
    @settings(max_examples=100)
    @given(
        is_mapped=st.booleans(),
        verdict_kind=st.sampled_from(_VERDICT_KINDS),
        coherent=st.booleans(),
        aligned=st.booleans(),
    )
    def test_admit_iff_all_four_gates_clear(
        self, is_mapped, verdict_kind, coherent, aligned
    ):
        verdict = _verdict_for(verdict_kind)
        decision = admission_decision(is_mapped, verdict, coherent, aligned)

        verdict_passing = verdict is not None and verdict.status == "passing"
        should_admit = is_mapped and verdict_passing and coherent and aligned

        if should_admit:
            # All four gates cleared -> admitted/ok (Req 4.1).
            assert decision.status == "admitted"
            assert decision.reason == "ok"
        else:
            # Any unmet gate -> rejected with the priority-ordered reason.
            assert decision.status == "rejected"
            if not is_mapped:
                # Unmapped is always rejected first (Req 4.3).
                assert decision.reason == "unmapped"
            elif not verdict_passing:
                # Missing/failing verdict outranks coherence/alignment (Req 4.2).
                assert decision.reason == "failing_verdict"
            elif not coherent:
                assert decision.reason == "coherence"
            else:
                # Only remaining failing gate is alignment (Req 2.9).
                assert not aligned
                assert decision.reason == "alignment"

    @settings(max_examples=100)
    @given(
        is_mapped=st.booleans(),
        verdict_kind=st.sampled_from(_VERDICT_KINDS),
        coherent=st.booleans(),
        aligned=st.booleans(),
    )
    def test_incoherent_or_misaligned_admits_nothing(
        self, is_mapped, verdict_kind, coherent, aligned
    ):
        # A sequence that failed coherence or alignment can never admit a
        # segment, regardless of mapping or verdict (Req 2.9).
        if coherent and aligned:
            return
        decision = admission_decision(
            is_mapped, _verdict_for(verdict_kind), coherent, aligned
        )
        assert decision.status == "rejected"

    @settings(max_examples=100)
    @given(coherent=st.booleans(), aligned=st.booleans())
    def test_unmapped_always_rejected_unmapped(self, coherent, aligned):
        # An unmapped segment is rejected with 'unmapped' even with a passing
        # verdict and a fully coherent/aligned sequence (Req 4.3).
        decision = admission_decision(False, _PASSING, coherent, aligned)
        assert decision.status == "rejected"
        assert decision.reason == "unmapped"
