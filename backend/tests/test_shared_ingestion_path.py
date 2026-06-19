"""Integration test for the shared ingestion path and per-job isolation (Task 11.3).

This is the wiring-level integration test for the deep-content-ingestion feature.
It asserts the THREE guarantees that make ``ingest_topic`` the single shared
DECODE -> MAP -> JUDGE -> ADMIT path:

  1. BOTH entrypoints route a Source_Video through ``ingest_topic`` rather than
     storing raw segmentation output (Req 7.1, 7.3):
       - the on-demand ``pipeline_agent.run_pipeline`` delegates a WHOLE-TOPIC
         call (``section_index is None``) to ``ingest_topic`` and returns the
         resulting Admitted_Clip count; a SECTION-BASED call
         (``section_index is not None``) keeps the legacy LangGraph path and
         never calls ``ingest_topic``;
       - the cold-start ``seeding_worker._generate`` drives its Backlog_Item
         through ``ingest_topic``.
  2. Ingestion never blocks the feed/Discover request path (Req 5.2): both
     ``ingest_topic`` and ``run_pipeline`` are plain synchronous functions
     invoked out-of-band (no coroutine / event-loop blocking).
  3. One failing job leaves the other jobs and existing clips intact (Req 9.5):
     within ``seeding_worker.run_once`` a topic whose ingestion raises is left
     pending while a sibling topic is still processed, and ``run_once`` never
     raises.

Patch points (where the lazily-imported name resolves at call time):
  - ``run_pipeline`` does ``from app.services.ingestion_pipeline import
    ingest_topic`` inside the function, and ``seeding_worker._generate`` does the
    same -- so patching ``app.services.ingestion_pipeline.ingest_topic`` (the
    module attribute) intercepts BOTH entrypoints.
  - The section-based ``run_pipeline`` path is isolated from any live YouTube /
    LangGraph work by stubbing the cached compiled graph.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_shared_ingestion_path.py``.

Validates: Requirements 5.2, 7.1, 7.3, 9.5
"""
import inspect

import app.services.ingestion_pipeline as ip
from app.agents import pipeline_agent
import scripts.seeding_worker as sw
from app.services.ingestion_pipeline import IngestionSummary
from app.services.quota_pool import ProjectQuota
from app.services.topic_frontier import BacklogItem


def _summary(slug: str, stored: int, outcome: str = "partially_admitted") -> IngestionSummary:
    """Build a realistic ``IngestionSummary`` for a successful ingest run."""
    return IngestionSummary(
        topic_slug=slug,
        outcome=outcome,
        funnels=[],
        stored=stored,
        deferred_reason=None,
    )


class _FakeTable:
    """Minimal chainable Supabase table stub: every read returns no rows."""

    def select(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("_Res", (), {"data": []})()


class _FakeClient:
    def table(self, *a, **k):
        return _FakeTable()


def _item(topic: str, priority: float = 0.5, *, level: str = "intermediate",
          status: str = "pending") -> BacklogItem:
    return BacklogItem(topic=topic, level=level, priority=priority, status=status)


# --------------------------------------------------------------------------
# 1. Both entrypoints route through the shared ingest_topic path
# --------------------------------------------------------------------------

class TestSharedPathRouting:
    def test_run_pipeline_whole_topic_delegates_to_ingest_topic(self, monkeypatch):
        """A whole-topic call (section_index None) is handed to ingest_topic and
        returns its Admitted_Clip count -- no raw segmentation is stored."""
        calls = []

        def fake_ingest(topic_slug, topic_name, **kwargs):
            calls.append((topic_slug, topic_name))
            return _summary(topic_slug, stored=4)

        monkeypatch.setattr(ip, "ingest_topic", fake_ingest)

        stored = pipeline_agent.run_pipeline("photosynthesis", "Photosynthesis")

        # Delegated to the shared path and returned summary.stored.
        assert calls == [("photosynthesis", "Photosynthesis")]
        assert stored == 4

    def test_run_pipeline_section_call_keeps_langgraph_path(self, monkeypatch):
        """A section-based call (section_index set) must NOT touch ingest_topic;
        it keeps the legacy per-beat LangGraph pipeline."""
        called = []
        monkeypatch.setattr(
            ip, "ingest_topic",
            lambda *a, **k: called.append(a) or _summary("x", 1),
        )

        # Stub the cached compiled graph so no live YouTube / LLM work runs.
        class _FakeGraph:
            def __init__(self):
                self.invoked_with = None

            def invoke(self, state):
                self.invoked_with = state
                return {"stored_count": 7}

        fake_graph = _FakeGraph()
        monkeypatch.setattr(pipeline_agent, "_pipeline_graph", fake_graph)

        stored = pipeline_agent.run_pipeline(
            "calculus", "Calculus", section_index=2, section_title="Limits",
        )

        # The LangGraph path ran and ingest_topic was never called.
        assert stored == 7
        assert fake_graph.invoked_with is not None
        assert fake_graph.invoked_with["section_index"] == 2
        assert called == []

    def test_seeding_worker_generate_routes_through_ingest_topic(self, monkeypatch):
        """The cold-start worker drives each Backlog_Item through ingest_topic
        rather than bare segmentation."""
        calls = []

        def fake_ingest(slug, name, **kwargs):
            calls.append((slug, name))
            return _summary(slug, stored=3)

        monkeypatch.setattr(ip, "ingest_topic", fake_ingest)
        # Not already seeded -> generation proceeds.
        monkeypatch.setattr(sw, "_topic_has_clips", lambda topic: False)
        # Avoid real DB for the topic upsert + post-ingest counting / stamping.
        monkeypatch.setattr(sw, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(sw, "_count_clips", lambda topic: 3)
        monkeypatch.setattr(sw, "_stamp_content_level", lambda topic, level: None)

        stored = sw._generate(_item("neural-networks", level="beginner"))

        assert calls == [("neural-networks", "Neural Networks")]
        assert stored == 3


# --------------------------------------------------------------------------
# 2. Ingestion is invoked out-of-band and never blocks the request path
# --------------------------------------------------------------------------

class TestNonBlocking:
    def test_entrypoints_are_plain_synchronous_functions(self):
        """ingest_topic and run_pipeline are plain (non-coroutine) functions, so
        they are invoked out-of-band and can never block an event loop serving a
        feed / Discover request (Req 5.2)."""
        assert not inspect.iscoroutinefunction(ip.ingest_topic)
        assert not inspect.iscoroutinefunction(pipeline_agent.run_pipeline)
        assert not inspect.iscoroutinefunction(sw._generate)
        assert not inspect.iscoroutinefunction(sw.run_once)


# --------------------------------------------------------------------------
# 3. One failing job leaves other jobs and existing clips intact (isolation)
# --------------------------------------------------------------------------

class TestPerJobIsolation:
    def test_one_failing_ingest_does_not_halt_other_jobs(self, monkeypatch):
        """Within run_once, a topic whose ingest_topic raises is left pending
        while a sibling topic is still processed; run_once never raises and
        existing clips are untouched."""
        persisted: list[tuple[str, str]] = []
        spawned: list[str] = []
        ingested: list[str] = []

        # Ample budget so nothing is budget-limited.
        projects = [ProjectQuota(project_id="projA", used=0)]
        # "boom" is selected first (higher priority), then "ok".
        pending = [_item("boom", 0.9), _item("ok", 0.5)]

        def fake_ingest(slug, name, **kwargs):
            ingested.append(slug)
            if slug == "boom":
                raise RuntimeError("ingestion blew up")
            return _summary(slug, stored=2)

        monkeypatch.setattr(ip, "ingest_topic", fake_ingest)
        monkeypatch.setattr(sw.backlog_store, "init_from_grade_map", lambda: None)
        monkeypatch.setattr(sw.quota_store, "load_today", lambda now_utc=None: list(projects))
        monkeypatch.setattr(sw.backlog_store, "load_pending", lambda: list(pending))
        monkeypatch.setattr(
            sw.backlog_store, "persist_status",
            lambda topic, status: persisted.append((topic, status)),
        )
        monkeypatch.setattr(
            sw.backlog_store, "spawn_adjacent_for",
            lambda topic: spawned.append(topic) or [],
        )
        monkeypatch.setattr(sw, "_topic_has_clips", lambda topic: False)
        monkeypatch.setattr(sw, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(sw, "_stamp_content_level", lambda topic, level: None)
        # Library state after each ingest: the failing topic admitted nothing
        # (0 clips -> left pending), the sibling admitted clips (-> done).
        monkeypatch.setattr(
            sw, "_count_clips",
            lambda topic: 0 if topic == "boom" else 2,
        )

        summary = sw.run_once()  # must not raise

        # The run completed cleanly over the whole backlog.
        assert summary["stopped_reason"] == "backlog_empty"
        # Both topics were attempted; the failure did not halt the loop.
        assert ingested == ["boom", "ok"]
        # Only the sibling succeeded and was processed / persisted done / spawned.
        assert summary["processed"] == 1
        assert persisted == [("ok", "done")]
        assert spawned == ["ok"]
        # The failing topic is left pending (never marked done) for a future retry,
        # leaving its job and any existing clips intact.
        assert ("boom", "done") not in persisted
