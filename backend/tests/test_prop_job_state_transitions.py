"""Property-based test for Ingestion_Job state transitions.

# Feature: deep-content-ingestion, Property 11: Job state transitions leave failures retryable

``next_job_status`` is a pure, total transition function over every
(JobStatus, JobEvent) pairing. The correctness-critical behavior is that a
``running`` job that fails is left ``running`` (unchanged, retryable) rather than
being marked ``failed``/``completed``, that the documented happy-path transitions
hold (queued+start -> running; running+succeed -> completed), that ``completed``
is terminal, and that no ``fail`` event ever reaches ``completed``.

Validates: Requirements 5.4, 5.5
"""
from hypothesis import given, settings, strategies as st

from app.services.ingestion_state import next_job_status

JOB_STATUSES = ["queued", "running", "completed", "failed"]
JOB_EVENTS = ["start", "succeed", "fail"]


class TestJobStateTransitions:
    @settings(max_examples=100)
    @given(
        current=st.sampled_from(JOB_STATUSES),
        event=st.sampled_from(JOB_EVENTS),
    )
    def test_next_job_status_invariants(self, current, event):
        result = next_job_status(current, event)

        # Total: always returns a valid JobStatus for every pairing (Req 5.4).
        assert result in JOB_STATUSES

        # Documented happy-path transitions (Req 5.4).
        if current == "queued" and event == "start":
            assert result == "running"
        elif current == "running" and event == "succeed":
            assert result == "completed"
        elif current == "running" and event == "fail":
            # A failed running job is left running, retryable (Req 5.5).
            assert result == "running"
        elif current == "completed":
            # Completed is terminal: no event changes it (Req 5.4).
            assert result == "completed"
        else:
            # Every other pairing is a no-op (Req 5.4).
            assert result == current

        # A fail event never advances a job to completed (Req 5.5).
        if event == "fail":
            assert result != "completed" or current == "completed"

    @settings(max_examples=100)
    @given(current=st.sampled_from(JOB_STATUSES))
    def test_completed_is_terminal(self, current):
        # No event ever moves a completed job off completed (Req 5.4).
        for event in JOB_EVENTS:
            assert next_job_status("completed", event) == "completed"

    @settings(max_examples=100)
    @given(data=st.data())
    def test_fail_never_reaches_completed_from_non_completed(self, data):
        current = data.draw(st.sampled_from(["queued", "running", "failed"]))
        # A fail event from any non-completed status never yields completed (Req 5.5).
        assert next_job_status(current, "fail") != "completed"
