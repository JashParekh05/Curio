"""Unit tests for ``ingest_topic`` Key_Pool deferral cases (Task 9.2).

When the single YouTube charge site ``youtube.youtube_search`` declines to spend
it returns ``None`` and leaves the Key_Pool quota counters unchanged. The
orchestrator then DEFERS the run, classifying *why* the call could not be
afforded:

  - the Operator-provisioned Key_Pool has keys but none can currently afford the
    call -> ``deferred_reason == 'insufficient_quota'`` (Req 8.6);
  - the Key_Pool is empty (zero registered keys) ->
    ``deferred_reason == 'empty_key_pool'`` (Req 8.7).

Either way the run yields no Source_Videos, admits nothing, and persists no
clips. These tests mock the components in the ``ingestion_pipeline`` namespace
(it does ``from app.services import ... youtube``) so they run offline with no
external service.

Validates: Requirements 8.6, 8.7
"""
from app.services import ingestion_pipeline
from app.services import quota_store
import app.agents.section_planner as section_planner


def _patch_common(monkeypatch):
    """Stub everything the deferral path touches before youtube_search.

    - the Topic is not already ingested (so we reach the search step);
    - plan_and_store_arc is a no-op (its DB/LLM call is irrelevant here);
    - set_feed_eligibility / record_funnel raise if called, since a deferred run
      must never persist anything.
    """
    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store,
        "topic_already_ingested",
        lambda topic_slug: False,
    )
    monkeypatch.setattr(
        section_planner, "plan_and_store_arc", lambda *a, **k: None
    )

    def _must_not_persist(*a, **k):
        raise AssertionError("a deferred run must not persist any clip")

    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store,
        "set_feed_eligibility",
        _must_not_persist,
    )
    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store,
        "record_funnel",
        _must_not_persist,
    )


def test_search_none_with_keys_defers_insufficient_quota(monkeypatch):
    # youtube_search returns None and the pool has configured keys -> the pool
    # has keys but none can afford the call -> 'insufficient_quota' (Req 8.6).
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        ingestion_pipeline.youtube, "youtube_search", lambda *a, **k: None
    )
    monkeypatch.setattr(
        quota_store,
        "configured_projects",
        lambda: [("projA", "AIzaKeyA"), ("projB", "AIzaKeyB")],
    )

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    assert summary.outcome == "deferred"
    assert summary.deferred_reason == "insufficient_quota"
    # Nothing was admitted / stored on a deferred run.
    assert summary.stored == 0
    assert summary.funnels == []


def test_search_none_with_empty_pool_defers_empty_key_pool(monkeypatch):
    # youtube_search returns None and the Key_Pool is empty -> 'empty_key_pool'
    # (Req 8.7).
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        ingestion_pipeline.youtube, "youtube_search", lambda *a, **k: None
    )
    monkeypatch.setattr(quota_store, "configured_projects", lambda: [])

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    assert summary.outcome == "deferred"
    assert summary.deferred_reason == "empty_key_pool"
    assert summary.stored == 0
    assert summary.funnels == []


def test_deferral_invokes_no_model_calls(monkeypatch):
    # A deferral happens before any decode/judge model call: those components
    # must never be reached, so the Key_Pool counters stay untouched.
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        ingestion_pipeline.youtube, "youtube_search", lambda *a, **k: None
    )
    monkeypatch.setattr(
        quota_store, "configured_projects", lambda: [("projA", "AIzaKeyA")]
    )

    def _must_not_call(*a, **k):
        raise AssertionError("no model call may run on a deferred topic")

    monkeypatch.setattr(
        ingestion_pipeline.deep_decoder, "decode_video", _must_not_call
    )
    monkeypatch.setattr(
        ingestion_pipeline.segment_judge, "judge_segment", _must_not_call
    )

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    assert summary.outcome == "deferred"
    assert summary.deferred_reason == "insufficient_quota"
    assert summary.stored == 0
