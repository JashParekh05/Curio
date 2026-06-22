"""Property-based test for Ingestion_Stage transitions and restart resume.

# Feature: deep-content-ingestion, Property 12: Stage transitions and restart resume

``next_stage`` maps ``fast_preview`` -> ``full_processing`` and is total, with
``full_processing`` as the terminal stage (it advances to itself). ``resume_stage``
returns a non-completed job's persisted stage so that, on restart, only stages not
already completed are re-executed.

Validates: Requirements 5.4, 6.1, 6.3
"""
from hypothesis import given, settings, strategies as st

from app.services.ingestion_state import IngestionJob, next_stage, resume_stage

INGESTION_STAGES = ["fast_preview", "full_processing"]
JOB_STATUSES = ["queued", "running", "completed", "failed"]


class TestStageTransitions:
    @settings(max_examples=100)
    @given(current=st.sampled_from(INGESTION_STAGES))
    def test_next_stage_invariants(self, current):
        result = next_stage(current)

        # Total: always returns a valid stage (Req 6.1, 6.3).
        assert result in INGESTION_STAGES

        if current == "fast_preview":
            # Fast_Preview advances to Full_Processing (Req 6.1, 6.3).
            assert result == "full_processing"
        else:
            # Full_Processing is terminal: it advances to itself (Req 6.3).
            assert result == "full_processing"

    @settings(max_examples=100)
    @given(current=st.sampled_from(INGESTION_STAGES))
    def test_full_processing_is_terminal(self, current):
        # Advancing twice never leaves full_processing (terminal stage, Req 6.3).
        assert next_stage(next_stage(current)) == "full_processing"

    @settings(max_examples=100)
    @given(
        topic_slug=st.text(min_size=1, max_size=40),
        stage=st.sampled_from(INGESTION_STAGES),
        status=st.sampled_from(JOB_STATUSES),
        failure_reason=st.one_of(st.none(), st.text(max_size=40)),
    )
    def test_resume_stage_returns_persisted_stage(
        self, topic_slug, stage, status, failure_reason
    ):
        job = IngestionJob(
            topic_slug=topic_slug,
            stage=stage,
            status=status,
            failure_reason=failure_reason,
        )
        # Resume re-executes from exactly the persisted stage (Req 5.4).
        assert resume_stage(job) == stage
