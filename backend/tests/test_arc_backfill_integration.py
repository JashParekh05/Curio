"""Integration test for Arc_Backfill and prune/restore (Task 6.6).

Wires the Arc_Backfill pure decision core (``arc_backfill``) and its
``arc_backfill_runner`` shell together against an in-memory Supabase double, with
no external service touched. It asserts the four end-to-end guarantees the pure
property tests cannot express on their own:

  1. Backfill routes through the shared Ingestion_Pipeline (``ingest_topic``) and
     stores no raw segmentation -- the runner performs no clip/segmentation
     writes of its own (Req 3.2).
  2. An in-flight backfill still serves existing Clips: no duplicate job is
     enqueued and the existing Clips are returned unchanged (Req 3.3).
  3. A backfill failure leaves the Topic's Clips unchanged, keeps serving from
     existing content, and records the failure (Req 3.10).
  4. A prune -> restore round-trip returns the Topic to its routable state, with
     every stored row retained (Req 3.6).

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_arc_backfill_integration.py``.

Validates: Requirements 3.2, 3.3, 3.6, 3.10
"""
import types

import app.services.ingestion_pipeline as ingestion_pipeline
from app.services import arc_backfill_runner as runner
from app.services import ingestion_store


# ---------------------------------------------------------------------------
# Stateful in-memory Supabase double (supports select/insert/update/delete with
# eq/in_/neq/limit/order). The shared conftest FakeDB is read/upsert only.
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self.op = "select"
        self.payload = None
        self.eqs = {}
        self.neqs = {}
        self.inq = None
        self._limit = None

    def select(self, *a, **k):
        self.op = "select"; return self

    def insert(self, payload, **k):
        self.op = "insert"; self.payload = payload; return self

    def update(self, payload, **k):
        self.op = "update"; self.payload = payload; return self

    def delete(self, **k):
        self.op = "delete"; return self

    def eq(self, col, val):
        self.eqs[col] = val; return self

    def neq(self, col, val):
        self.neqs[col] = val; return self

    def in_(self, col, vals):
        self.inq = (col, list(vals)); return self

    def limit(self, n):
        self._limit = n; return self

    def order(self, *a, **k):
        return self

    def _matches(self, row):
        if not all(row.get(c) == v for c, v in self.eqs.items()):
            return False
        if any(row.get(c) == v for c, v in self.neqs.items()):
            return False
        if self.inq is not None:
            col, vals = self.inq
            if row.get(col) not in vals:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(payload)
            return _Result(list(payload))
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)
            return _Result(matched)
        if self.op == "delete":
            keep = [r for r in rows if not self._matches(r)]
            removed = [r for r in rows if self._matches(r)]
            self.store[self.table] = keep
            return _Result(removed)
        # select
        out = [r for r in rows if self._matches(r)]
        if self._limit is not None:
            out = out[: self._limit]
        return _Result(out)


class StatefulDB:
    def __init__(self, store=None):
        self.store = store or {}

    def table(self, name):
        return _Query(name, self.store)


def _summary(outcome="fully_admitted", stored=2):
    return types.SimpleNamespace(
        topic_slug="t", outcome=outcome, stored=stored, deferred_reason=None
    )


def _complete_topic_store():
    """A Topic whose Canonical_Arc (2 roles) is fully filled by Admitted_Clips,
    plus a structure model row -- the post-backfill, Arc_Complete state."""
    return {
        "canonical_arc": [
            {"topic_slug": "fractions", "role": "definition", "ordinal": 1},
            {"topic_slug": "fractions", "role": "mechanism", "ordinal": 2},
        ],
        "clips": [
            {"id": "c1", "topic_slug": "fractions", "role_ordinal": 1},
            {"id": "c2", "topic_slug": "fractions", "role_ordinal": 2},
        ],
        "topic_arc_roles": [{"topic_slug": "fractions", "role": "definition"}],
        "topic_sections": [],
        "ingestion_jobs": [],
        "clip_events": [],
        "impressions": [],
        "topics": [{"slug": "fractions", "archived": False, "arc_complete": False}],
    }


# ---------------------------------------------------------------------------
# 1. Backfill routes through ingest_topic and stores no raw segmentation
# ---------------------------------------------------------------------------

class TestBackfillRoutesThroughPipeline:
    def test_backfill_calls_ingest_topic_and_marks_complete(self, monkeypatch):
        db = StatefulDB(store=_complete_topic_store())
        calls = []

        def _spy_ingest(slug, name):
            calls.append((slug, name))
            return _summary(outcome="fully_admitted", stored=2)

        monkeypatch.setattr(ingestion_pipeline, "ingest_topic", _spy_ingest)

        clips_before = list(db.store["clips"])
        result = runner.run_backfill("fractions", "Fractions", db=db)

        # Routed through the shared Ingestion_Pipeline exactly once.
        assert calls == [("fractions", "Fractions")]
        # The runner stored NO raw segmentation: it added/removed no clip rows and
        # touched no segmentation tables.
        assert db.store["clips"] == clips_before
        assert "learning_atoms" not in db.store
        assert "segment_verdicts" not in db.store
        # Re-derived state is Arc_Complete -> topics.arc_complete set true (Req 3.4).
        assert result["status"] == "complete"
        assert result["arc_complete"] is True
        assert db.store["topics"][0]["arc_complete"] is True

    def test_backfill_leaving_a_gap_does_not_mark_complete(self, monkeypatch):
        store = _complete_topic_store()
        # Drop the clip filling role ordinal 2 -> a Coverage_Gap remains.
        store["clips"] = [{"id": "c1", "topic_slug": "fractions", "role_ordinal": 1}]
        db = StatefulDB(store=store)
        monkeypatch.setattr(
            ingestion_pipeline, "ingest_topic", lambda s, n: _summary(stored=1)
        )

        result = runner.run_backfill("fractions", "Fractions", db=db)

        # Not Arc_Complete; left eligible for a future backfill (Req 3.9).
        assert result["status"] == "incomplete"
        assert result["arc_complete"] is False
        assert db.store["topics"][0]["arc_complete"] is False


# ---------------------------------------------------------------------------
# 2. An in-flight backfill still serves existing clips (no duplicate enqueue)
# ---------------------------------------------------------------------------

class TestInFlightBackfillServesExistingClips:
    def test_in_flight_topic_enqueues_nothing_and_keeps_clips(self, monkeypatch):
        store = _complete_topic_store()
        # A gap so the Topic is not Arc_Complete, but a backfill is already queued.
        store["clips"] = [{"id": "c1", "topic_slug": "fractions", "role_ordinal": 1}]
        store["ingestion_jobs"] = [{"topic_slug": "fractions", "status": "queued"}]
        db = StatefulDB(store=store)

        enqueue_calls = []
        monkeypatch.setattr(
            ingestion_store, "enqueue_job",
            lambda slug: enqueue_calls.append(slug) or None,
        )

        clips_before = list(db.store["clips"])
        enqueued = runner.maybe_enqueue_backfill("fractions", db=db)

        # No second backfill enqueued while one is in flight (Req 3.1, 3.3)...
        assert enqueued is False
        assert enqueue_calls == []
        # ...and the existing Clips are untouched, still servable.
        assert db.store["clips"] == clips_before

    def test_needed_backfill_enqueues_exactly_one(self, monkeypatch):
        store = _complete_topic_store()
        store["clips"] = [{"id": "c1", "topic_slug": "fractions", "role_ordinal": 1}]
        store["ingestion_jobs"] = []   # nothing in flight
        db = StatefulDB(store=store)

        enqueue_calls = []
        monkeypatch.setattr(
            ingestion_store, "enqueue_job",
            lambda slug: enqueue_calls.append(slug)
            or types.SimpleNamespace(topic_slug=slug),
        )

        enqueued = runner.maybe_enqueue_backfill("fractions", db=db)

        assert enqueued is True
        assert enqueue_calls == ["fractions"]   # exactly one (Req 3.1)


# ---------------------------------------------------------------------------
# 3. A backfill failure leaves clips unchanged and logs
# ---------------------------------------------------------------------------

class TestBackfillFailurePreservesClips:
    def test_pipeline_exception_leaves_clips_unchanged_and_records(
        self, monkeypatch, caplog
    ):
        db = StatefulDB(store=_complete_topic_store())

        def _boom(slug, name):
            raise RuntimeError("decode exploded")

        monkeypatch.setattr(ingestion_pipeline, "ingest_topic", _boom)

        clips_before = list(db.store["clips"])
        with caplog.at_level("WARNING"):
            result = runner.run_backfill("fractions", "Fractions", db=db)

        # Failure recorded with the (slug, reason); existing Clips untouched and
        # the Topic is NOT marked Arc_Complete (Req 3.10).
        assert result["status"] == "failed"
        assert result["arc_complete"] is False
        assert "decode exploded" in result["reason"]
        assert db.store["clips"] == clips_before
        assert db.store["topics"][0]["arc_complete"] is False
        assert any("backfill failed" in r.message.lower() for r in caplog.records)

    def test_deferred_pipeline_with_no_clips_is_a_failure(self, monkeypatch):
        store = _complete_topic_store()
        store["clips"] = [{"id": "c1", "topic_slug": "fractions", "role_ordinal": 1}]
        db = StatefulDB(store=store)
        monkeypatch.setattr(
            ingestion_pipeline, "ingest_topic",
            lambda s, n: types.SimpleNamespace(
                topic_slug=s, outcome="deferred", stored=0, deferred_reason="no_quota"
            ),
        )

        clips_before = list(db.store["clips"])
        result = runner.run_backfill("fractions", "Fractions", db=db)

        assert result["status"] == "failed"
        assert result["reason"] == "no_quota"
        assert db.store["clips"] == clips_before


# ---------------------------------------------------------------------------
# 4. A prune -> restore round-trip returns the Topic to its routable state
# ---------------------------------------------------------------------------

class TestPruneRestoreRoundTrip:
    def test_structureless_unwatched_topic_prunes_then_restores(self):
        # Structureless (no arc/role/section model) and unwatched (no events).
        store = {
            "canonical_arc": [],
            "clips": [{"id": "c1", "topic_slug": "ghost", "role_ordinal": None}],
            "topic_arc_roles": [],
            "topic_sections": [],
            "ingestion_jobs": [],
            "clip_events": [],
            "impressions": [],
            "topics": [{"slug": "ghost", "archived": False, "arc_complete": False}],
        }
        db = StatefulDB(store=store)

        assert runner.is_pruned("ghost", db=db) is False

        # Prune: structureless + unwatched -> archived=true (reversible).
        assert runner.maybe_prune("ghost", db=db) is True
        assert runner.is_pruned("ghost", db=db) is True
        # Rows retained: the Topic's Clips are NOT deleted by pruning (Req 3.6).
        assert db.store["clips"] == [
            {"id": "c1", "topic_slug": "ghost", "role_ordinal": None}
        ]

        # Restore: clears archived -> back to the pre-prune routable state.
        assert runner.restore_topic("ghost", db=db) is True
        assert runner.is_pruned("ghost", db=db) is False
        assert db.store["topics"][0]["archived"] is False

    def test_watched_topic_is_never_pruned(self):
        store = {
            "canonical_arc": [],
            "clips": [{"id": "c1", "topic_slug": "loved", "role_ordinal": None}],
            "topic_arc_roles": [],
            "topic_sections": [],
            "ingestion_jobs": [],
            # A Watch_Event against the Topic's Clip makes it a Watched_Topic.
            "clip_events": [{"clip_id": "c1", "watch_ms": 1000}],
            "impressions": [],
            "topics": [{"slug": "loved", "archived": False, "arc_complete": False}],
        }
        db = StatefulDB(store=store)

        assert runner.maybe_prune("loved", db=db) is False
        assert runner.is_pruned("loved", db=db) is False
