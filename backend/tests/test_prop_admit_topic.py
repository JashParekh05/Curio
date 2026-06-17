"""Property-based test for topic-level admission and outcome classification.

# Feature: deep-content-ingestion, Property 9: Topic admission admits exactly the qualifying subset and classifies the outcome

``admit_topic`` applies the single-segment ``admission_decision`` across every
candidate (Mapped_Segments plus Unmapped_Segments). It admits EXACTLY the mapped
segments whose Segment_Verdict is passing when BOTH the coherence and alignment
flags are true, and admits nothing when either flag is false. The Topic outcome
classifies the run over ALL candidates: ``skipped`` when none are admitted,
``fully_admitted`` when every candidate is admitted, and ``partially_admitted``
otherwise.

Validates: Requirements 4.5
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import LearningAtom
from app.services.admission_gate import admit_topic
from app.services.segment_judge import judge_verdict
from app.services.segment_mapper import MappedSegment

# A passing and a failing verdict from the segment_judge pure core.
_PASSING = judge_verdict(1.0, "belongs", 0.5)
_FAILING = judge_verdict(0.0, "belongs", 0.5)


@st.composite
def _topic_candidates(draw):
    """Mapped_Segments (unique atom ids) + a verdict map + Unmapped_Segments.

    Each mapped segment is tagged passing / failing / missing so the verdict map
    exercises the passing path, the explicit-failing path, and the
    missing-verdict path (which is treated as failing).
    """
    n_mapped = draw(st.integers(min_value=0, max_value=12))
    segments: list[MappedSegment] = []
    verdicts = {}
    for i in range(n_mapped):
        atom = LearningAtom(
            id=f"atom-{i}",  # unique within the topic
            topic_slug="topic",
            video_id="vid",
            source_url="https://example.com/v",
            role="definition",
            concept=f"concept-{i}",
            prior_knowledge=[],
            start=float(i),
            end=float(i) + 1.0,
        )
        segments.append(
            MappedSegment(atom=atom, pedagogical_role="definition", role_ordinal=1)
        )
        kind = draw(st.sampled_from(["passing", "failing", "missing"]))
        if kind == "passing":
            verdicts[atom.id] = _PASSING
        elif kind == "failing":
            verdicts[atom.id] = _FAILING
        # 'missing' -> intentionally left out of the verdict map.

    # Unmapped_Segments are never admitted; their contents are irrelevant.
    n_unmapped = draw(st.integers(min_value=0, max_value=5))
    unmapped = [object() for _ in range(n_unmapped)]
    return segments, verdicts, unmapped


class TestAdmitTopic:
    @settings(max_examples=100)
    @given(
        candidates=_topic_candidates(),
        coherent=st.booleans(),
        aligned=st.booleans(),
    )
    def test_admits_exact_subset_and_classifies_outcome(
        self, candidates, coherent, aligned
    ):
        segments, verdicts, unmapped = candidates
        admitted, decisions, outcome = admit_topic(
            segments, unmapped, verdicts, coherent, aligned
        )

        # --- Admitted set: exactly the passing mapped segments when both flags
        #     hold; nothing when either flag is false (Req 4.5). ---
        if coherent and aligned:
            expected_ids = [
                s.atom.id
                for s in segments
                if verdicts.get(s.atom.id) is not None
                and verdicts[s.atom.id].status == "passing"
            ]
        else:
            expected_ids = []

        assert [s.atom.id for s in admitted] == expected_ids
        # Admitted segments are a subset of the mapped input (never unmapped).
        assert all(s in segments for s in admitted)

        # --- One decision per candidate (mapped first, then unmapped). ---
        total_candidates = len(segments) + len(unmapped)
        assert len(decisions) == total_candidates
        # Every unmapped candidate is rejected as 'unmapped'.
        unmapped_decisions = decisions[len(segments):]
        assert all(d.reason == "unmapped" for d in unmapped_decisions)

        # --- Outcome classification over ALL candidates (Req 4.5). ---
        admitted_count = len(admitted)
        if admitted_count == 0:
            assert outcome == "skipped"
        elif admitted_count == total_candidates:
            assert outcome == "fully_admitted"
        else:
            assert outcome == "partially_admitted"

    @settings(max_examples=100)
    @given(candidates=_topic_candidates())
    def test_either_flag_false_skips_everything(self, candidates):
        # When coherence or alignment fails for the assembled sequence, no
        # candidate is admitted and the topic is skipped (Req 4.5).
        segments, verdicts, unmapped = candidates
        for coherent, aligned in [(False, True), (True, False), (False, False)]:
            admitted, _, outcome = admit_topic(
                segments, unmapped, verdicts, coherent, aligned
            )
            assert admitted == []
            assert outcome == "skipped"
