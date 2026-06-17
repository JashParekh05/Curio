"""Integration test for ``ingest_topic`` idempotent skip (Task 9.4).

When persisted state already marks a Topic as coherently ingested
(``ingestion_store.topic_already_ingested`` is True), re-ingestion is a no-op: it
returns ``outcome == 'skipped'`` with ``stored == 0``, issues ZERO model calls
(decode / judge), and makes ZERO YouTube calls (so it consumes zero additional
Quota_Cost), leaving the stored clips unchanged.

These tests mock the components in the ``ingestion_pipeline`` namespace so any
stray model/quota call would trip an assertion.

Validates: Requirements 7.4, 7.5, 9.4
"""
from app.services import ingestion_pipeline
import app.agents.section_planner as section_planner


def test_already_ingested_topic_is_skipped_with_no_model_or_quota_calls(monkeypatch):
    calls = {"decode": 0, "judge": 0, "search": 0}

    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store,
        "topic_already_ingested",
        lambda topic_slug: True,
    )

    def _count_decode(*a, **k):
        calls["decode"] += 1
        raise AssertionError("decode_video must not run for an ingested topic")

    def _count_judge(*a, **k):
        calls["judge"] += 1
        raise AssertionError("judge_segment must not run for an ingested topic")

    def _count_search(*a, **k):
        calls["search"] += 1
        raise AssertionError("youtube_search must not run for an ingested topic")

    monkeypatch.setattr(
        ingestion_pipeline.deep_decoder, "decode_video", _count_decode
    )
    monkeypatch.setattr(
        ingestion_pipeline.segment_judge, "judge_segment", _count_judge
    )
    monkeypatch.setattr(
        ingestion_pipeline.youtube, "youtube_search", _count_search
    )
    # The arc planner would also be a (cached) call; the skip must precede it.
    monkeypatch.setattr(
        section_planner,
        "plan_and_store_arc",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("plan_and_store_arc must not run for an ingested topic")
        ),
    )

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    # Idempotent skip: no work, no spend, stored clips unchanged.
    assert summary.outcome == "skipped"
    assert summary.stored == 0
    assert summary.funnels == []
    assert summary.deferred_reason is None
    # Zero model calls, zero quota.
    assert calls == {"decode": 0, "judge": 0, "search": 0}
