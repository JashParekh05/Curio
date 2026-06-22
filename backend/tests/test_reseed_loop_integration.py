"""Integration test for the telemetry -> reseed loop (Task 4.6).

Wires the Reseed_Prioritizer pure core, its ``backlog_store`` reseed shell, and
the ``Seeding_Worker`` run loop together against in-memory fakes (the shared
``FakeDB`` Supabase double plus monkeypatched seams), with no external service
touched. It asserts the three end-to-end guarantees the pure property tests
cannot express on their own:

  1. Drain through the pipeline: a reseed Backlog_Item produced from Watch_Quality
     signal is selected by ``seeding_worker.run_once`` and routed through
     ``ingestion_pipeline.ingest_topic`` -- the SAME shared path learner-initiated
     topics use (Req 7.5).
  2. Duplicate suppression: a Topic that already has a non-done Backlog_Item in the
     frontier is left unchanged and never enqueued a second time (Req 7.4, 7.7).
  3. Enqueue failure preserves the frontier: when the enqueue write fails, no row
     is persisted, the items are retained for retry, and an error indication is
     recorded (Req 7.8).

``backlog_store`` binds ``get_client`` via ``from app.db.supabase import
get_client`` and resolves ``worst_clips`` lazily via ``from app.services.analytics
import worst_clips``; the worker resolves ``ingest_topic`` lazily via ``from
app.services.ingestion_pipeline import ingest_topic``. Each is patched in the
namespace that actually resolves it.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_reseed_loop_integration.py``.

Validates: Requirements 7.4, 7.5, 7.8
"""
import types

import app.services.analytics as analytics
import app.services.ingestion_pipeline as ingestion_pipeline
import scripts.seeding_worker as sw
from app.services import backlog_store as bs
from app.services.quota_pool import ProjectQuota
from app.services.topic_frontier import BacklogItem
from tests.conftest import FakeDB

_TABLE = "topic_backlog"


def _use_db(monkeypatch, db):
    """Point the reseed shell at the supplied fake Supabase client."""
    monkeypatch.setattr(bs, "get_client", lambda: db)


def _patch_worst_clips(monkeypatch, rows):
    """Make the Watch_Quality signal source deterministic and offline.

    ``reseed_from_telemetry`` resolves ``worst_clips`` lazily from the analytics
    module, so the bound name to patch lives there.
    """
    monkeypatch.setattr(analytics, "worst_clips", lambda **kwargs: list(rows))


def _row(clip_id, topic_slug, avg_watch_ratio, views):
    return {
        "clip_id": clip_id,
        "topic_slug": topic_slug,
        "avg_watch_ratio": avg_watch_ratio,
        "views": views,
    }


def _upserted_rows(db: FakeDB) -> list[dict]:
    rows: list[dict] = []
    for table, payload in db.rec["upserts"]:
        assert table == _TABLE
        rows.extend(payload)
    return rows


# A signal set with two low-Watch_Quality topics (identified), one healthy topic
# (above threshold), and one low-confidence clip (below Minimum_Views).
_SIGNAL_ROWS = [
    _row("c1", "fractions", 0.10, 5),
    _row("c2", "decimals", 0.05, 8),
    _row("c3", "good-topic", 0.90, 10),   # above Recut_Threshold -> not identified
    _row("c4", "low-views", 0.10, 1),     # below Minimum_Views -> excluded
]


# ---------------------------------------------------------------------------
# 1. A reseed Backlog_Item is drained by run_once through ingest_topic
# ---------------------------------------------------------------------------

class TestReseedDrainsThroughPipeline:
    def test_reseeded_topic_is_routed_through_ingest_topic(self, monkeypatch):
        # --- Stage A: telemetry -> reseed enqueue ---------------------------
        db = FakeDB(store={_TABLE: []})
        _use_db(monkeypatch, db)
        _patch_worst_clips(monkeypatch, _SIGNAL_ROWS)

        summary = bs.reseed_from_telemetry()

        # Exactly the two low-quality topics were identified and enqueued.
        assert summary["error"] is None
        assert summary["identified"] == 2
        assert summary["enqueued"] == 2
        enqueued_topics = {r["topic"] for r in _upserted_rows(db)}
        assert enqueued_topics == {"fractions", "decimals"}

        # Hand the freshly persisted reseed items to the worker as the frontier.
        pending = [
            BacklogItem(
                topic=r["topic"], level=r["level"],
                priority=r["priority"], status=r["status"],
            )
            for r in _upserted_rows(db)
        ]
        assert all(item.status == "pending" for item in pending)

        # --- Stage B: Seeding_Worker drains the reseed items via ingest_topic -
        ingested: list[str] = []

        def _fake_ingest_topic(slug, name):
            ingested.append(slug)
            return types.SimpleNamespace(outcome="admitted", stored=2, deferred_reason=None)

        # Route the worker's shared pipeline call to the spy; leave the pacing /
        # selection / outcome cores real (the worker is a thin shell).
        monkeypatch.setattr(ingestion_pipeline, "ingest_topic", _fake_ingest_topic)

        worker_db = FakeDB(store={"topics": [], "clips": []})
        monkeypatch.setattr(sw, "get_client", lambda: worker_db)
        monkeypatch.setattr(sw.backlog_store, "init_from_grade_map", lambda: None)
        monkeypatch.setattr(sw.backlog_store, "load_pending", lambda: list(pending))
        monkeypatch.setattr(
            sw.quota_store, "load_today",
            lambda now_utc=None: [ProjectQuota(project_id="projA", used=0)],
        )
        persisted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            sw.backlog_store, "persist_status",
            lambda topic, status: persisted.append((topic, status)),
        )
        monkeypatch.setattr(sw.backlog_store, "spawn_adjacent_for", lambda topic: [])
        # The reseed topics have no clips yet (so they are not skipped), and the
        # ingest spy "admits" clips so the run records success.
        monkeypatch.setattr(sw, "_topic_has_clips", lambda topic: False)
        monkeypatch.setattr(sw, "_count_clips", lambda topic: 2)

        result = sw.run_once()

        # Both reseed topics were routed through the shared Ingestion_Pipeline...
        assert sorted(ingested) == ["decimals", "fractions"]
        # ...processed to success and marked done so they are not reconsidered.
        assert result["processed"] == 2
        assert result["stopped_reason"] == "backlog_empty"
        assert ("fractions", "done") in persisted
        assert ("decimals", "done") in persisted


# ---------------------------------------------------------------------------
# 2. Duplicate suppression holds
# ---------------------------------------------------------------------------

class TestDuplicateSuppression:
    def test_topic_with_existing_non_done_item_is_not_re_enqueued(self, monkeypatch):
        # "fractions" already has a pending (non-done) Backlog_Item in the frontier.
        db = FakeDB(store={_TABLE: [
            {"topic": "fractions", "level": "intermediate",
             "priority": 0.5, "status": "pending"},
        ]})
        _use_db(monkeypatch, db)
        _patch_worst_clips(monkeypatch, _SIGNAL_ROWS)

        summary = bs.reseed_from_telemetry()

        # Both topics are identified, but only the genuinely new "decimals" is
        # enqueued; the existing "fractions" item is left untouched (Req 7.4).
        assert summary["identified"] == 2
        assert summary["enqueued"] == 1
        enqueued_topics = {r["topic"] for r in _upserted_rows(db)}
        assert enqueued_topics == {"decimals"}
        assert "fractions" not in enqueued_topics

    def test_a_topic_named_by_many_low_quality_clips_enqueues_once(self, monkeypatch):
        # Three failing clips all belong to the same parent Topic; the shared
        # enqueue dedupe collapses them to a single Backlog_Item (Req 7.7).
        rows = [
            _row("c1", "fractions", 0.10, 5),
            _row("c2", "fractions", 0.08, 6),
            _row("c3", "fractions", 0.12, 7),
        ]
        db = FakeDB(store={_TABLE: []})
        _use_db(monkeypatch, db)
        _patch_worst_clips(monkeypatch, rows)

        summary = bs.reseed_from_telemetry()

        assert summary["identified"] == 1
        assert summary["enqueued"] == 1
        assert [r["topic"] for r in _upserted_rows(db)] == ["fractions"]


# ---------------------------------------------------------------------------
# 3. Enqueue failure preserves the frontier
# ---------------------------------------------------------------------------

class TestEnqueueFailurePreservesFrontier:
    def test_failed_enqueue_persists_nothing_and_records_error(self, monkeypatch):
        # The backlog table fails every query: the dedupe read degrades to empty
        # and the persist upsert raises, so nothing is written (Req 7.8).
        db = FakeDB(store={_TABLE: []}, fail={_TABLE})
        _use_db(monkeypatch, db)
        _patch_worst_clips(monkeypatch, _SIGNAL_ROWS)

        summary = bs.reseed_from_telemetry()

        # An error indication is recorded and nothing was enqueued...
        assert summary["error"] == "enqueue_failed"
        assert summary["enqueued"] == 0
        # ...the frontier state is preserved: no upsert was committed.
        assert db.rec["upserts"] == []
