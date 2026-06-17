"""Property-based test for duplicate-job suppression.

# Feature: deep-content-ingestion, Property 13: Duplicate jobs are never enqueued

``should_enqueue`` returns False iff a non-completed (queued/running/failed) job
already exists for the Topic, and True otherwise (no job for the Topic, or every
existing job for it is completed). This guarantees no duplicate Ingestion_Job is
ever enqueued for a Topic that already has active work.

Validates: Requirements 5.6
"""
from hypothesis import given, settings, strategies as st

from app.services.ingestion_state import IngestionJob, should_enqueue

INGESTION_STAGES = ["fast_preview", "full_processing"]
JOB_STATUSES = ["queued", "running", "completed", "failed"]
NON_COMPLETED = {"queued", "running", "failed"}

_jobs = st.lists(
    st.builds(
        IngestionJob,
        topic_slug=st.sampled_from(["topic-a", "topic-b", "topic-c"]),
        stage=st.sampled_from(INGESTION_STAGES),
        status=st.sampled_from(JOB_STATUSES),
        failure_reason=st.one_of(st.none(), st.text(max_size=20)),
    ),
    max_size=8,
)


class TestShouldEnqueue:
    @settings(max_examples=100)
    @given(
        existing=_jobs,
        topic_slug=st.sampled_from(["topic-a", "topic-b", "topic-c", "topic-d"]),
    )
    def test_should_enqueue_matches_definition(self, existing, topic_slug):
        result = should_enqueue(existing, topic_slug)

        has_active = any(
            job.topic_slug == topic_slug and job.status in NON_COMPLETED
            for job in existing
        )

        # False iff a non-completed job exists for the topic; True otherwise (Req 5.6).
        assert result is (not has_active)

    @settings(max_examples=100)
    @given(
        existing=_jobs,
        topic_slug=st.sampled_from(["topic-a", "topic-b", "topic-c", "topic-d"]),
    )
    def test_active_job_blocks_duplicate(self, existing, topic_slug):
        if any(
            job.topic_slug == topic_slug and job.status != "completed"
            for job in existing
        ):
            # A non-completed job for the topic suppresses a duplicate enqueue (Req 5.6).
            assert should_enqueue(existing, topic_slug) is False

    @settings(max_examples=100)
    @given(topic_slug=st.text(min_size=1, max_size=40))
    def test_no_existing_jobs_always_enqueues(self, topic_slug):
        # With no jobs at all, a new job should always be enqueued (Req 5.6).
        assert should_enqueue([], topic_slug) is True

    @settings(max_examples=100)
    @given(
        topic_slug=st.text(min_size=1, max_size=40),
        count=st.integers(min_value=1, max_value=5),
    )
    def test_only_completed_jobs_allow_enqueue(self, topic_slug, count):
        existing = [
            IngestionJob(
                topic_slug=topic_slug,
                stage="full_processing",
                status="completed",
            )
            for _ in range(count)
        ]
        # When every job for the topic is completed, enqueue is allowed (Req 5.6).
        assert should_enqueue(existing, topic_slug) is True
