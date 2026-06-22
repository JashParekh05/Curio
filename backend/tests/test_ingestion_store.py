"""Integration tests for the persisted Ingestion_Queue store (Task 8.2).

Exercises ``app.services.ingestion_store`` against a mocked Supabase client,
verifying the queue's durability / restart-resume and dedupe guarantees:

  - durability + resume: a job enqueued into the persisted ``ingestion_jobs``
    table survives a simulated process restart (a fresh ``load_active_jobs``
    read against the same durable rows) and resumes from its persisted stage
    via ``ingestion_state.resume_stage`` (Req 5.3, 5.4);
  - stage persistence: a job advanced to ``full_processing`` resumes from that
    later stage after restart, never re-running the completed ``fast_preview``
    stage (Req 5.4);
  - completed jobs are excluded from the resume set, so a restart only re-runs
    work that still needs doing (Req 5.3);
  - dedupe: a duplicate ``enqueue_job`` for a Topic that already has a
    non-completed job is suppressed and returns ``None`` (Req 5.6).

The store does ``from app.db.supabase import get_client``, so the bound name to
patch lives in the ingestion_store module namespace
(``app.services.ingestion_store.get_client``). Unlike the conftest ``FakeDB``,
this double's ``upsert`` persists to the in-memory store and survives across
``load_active_jobs`` calls so we can model a restart reading durable rows; it
also supports the ``.neq("status", "completed")`` filter the resume read uses.

Validates: Requirements 5.3, 5.4, 5.6
"""
import pytest

from app.services import ingestion_store
from app.services.ingestion_state import resume_stage

_JOBS_TABLE = "ingestion_jobs"
_COMPLETED = "completed"


# --------------------------------------------------------------------------
# A durable in-memory Supabase double for the ingestion_jobs table.
#
# Models the one behavior the conftest FakeDB intentionally omits: upserts that
# PERSIST to the backing store (keyed by the on_conflict column) so they survive
# a "restart" -- a subsequent load_active_jobs read. Also supports update().eq()
# and select().neq() exactly as ingestion_store uses them.
# --------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store, fail):
        self.table, self.store, self.fail = table, store, fail
        self.op = "select"
        self.payload = None
        self.on_conflict = None
        self.eq_filters = {}
        self.neq_filters = {}

    def select(self, *a, **k):
        self.op = "select"
        return self

    def upsert(self, payload, on_conflict=None, **k):
        self.op = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def update(self, payload, **k):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.eq_filters[col] = val
        return self

    def neq(self, col, val):
        self.neq_filters[col] = val
        return self

    def execute(self):
        if self.table in self.fail:
            raise RuntimeError(f"db down: {self.table}")
        rows = self.store.setdefault(self.table, [])
        if self.op == "upsert":
            # Dedupe on the conflict key so a re-upsert overwrites in place,
            # mirroring Postgres ON CONFLICT ... DO UPDATE.
            if self.on_conflict:
                for row in rows:
                    if row.get(self.on_conflict) == self.payload.get(self.on_conflict):
                        row.update(self.payload)
                        return _Result([dict(row)])
            rows.append(dict(self.payload))
            return _Result([dict(self.payload)])
        if self.op == "update":
            updated = []
            for row in rows:
                if all(row.get(c) == v for c, v in self.eq_filters.items()):
                    row.update(self.payload)
                    updated.append(dict(row))
            return _Result(updated)
        # select
        out = []
        for row in rows:
            if not all(row.get(c) == v for c, v in self.eq_filters.items()):
                continue
            if any(row.get(c) == v for c, v in self.neq_filters.items()):
                continue
            out.append(dict(row))
        return _Result(out)


class DurableDB:
    """Supabase double whose rows persist across queries (a durable table).

    ``store`` maps table name -> list[dict]. ``fail`` is a set of table names
    whose queries raise, to exercise best-effort degradation.
    """

    def __init__(self, store=None, fail=None):
        self.store = store if store is not None else {}
        self.fail = fail or set()

    def table(self, name):
        return _Query(name, self.store, self.fail)


@pytest.fixture
def db(monkeypatch):
    """A fresh durable DB wired into the ingestion_store module namespace."""
    fake = DurableDB(store={})
    monkeypatch.setattr(ingestion_store, "get_client", lambda: fake)
    return fake


def _stored_jobs(fake):
    return fake.store.get(_JOBS_TABLE, [])


# --------------------------------------------------------------------------
# Durability + restart resume (Req 5.3, 5.4)
# --------------------------------------------------------------------------

class TestQueueDurabilityAndResume:
    def test_enqueued_job_survives_restart_and_resumes_from_persisted_stage(self, db):
        # Enqueue a job for a Topic.
        job = ingestion_store.enqueue_job("binary-search")
        assert job is not None
        assert job.topic_slug == "binary-search"
        assert job.stage == "fast_preview"
        assert job.status == "queued"

        # Simulate a process restart: a brand-new load against the SAME durable
        # rows (nothing in memory carried over -- only the persisted table).
        resumed = ingestion_store.load_active_jobs()

        # The non-completed job is present after the restart...
        assert [j.topic_slug for j in resumed] == ["binary-search"]
        survivor = resumed[0]
        # ...and resumes from exactly its persisted stage.
        assert resume_stage(survivor) == "fast_preview"
        assert survivor.stage == "fast_preview"
        assert survivor.status == "queued"

    def test_resumes_from_later_persisted_stage_after_advancing(self, db):
        # A job that has advanced to full_processing must resume from there on
        # restart, never re-running the already-completed fast_preview stage.
        ingestion_store.enqueue_job("merge-sort")
        ingestion_store.persist_stage("merge-sort", "full_processing")
        ingestion_store.persist_status("merge-sort", "running")

        resumed = ingestion_store.load_active_jobs()

        assert [j.topic_slug for j in resumed] == ["merge-sort"]
        survivor = resumed[0]
        assert resume_stage(survivor) == "full_processing"
        assert survivor.status == "running"

    def test_completed_jobs_are_excluded_from_resume_set(self, db):
        # Two jobs: one still running, one completed. A restart only re-runs the
        # job that still needs work.
        ingestion_store.enqueue_job("dijkstra")
        ingestion_store.enqueue_job("kruskal")
        ingestion_store.persist_status("kruskal", _COMPLETED)

        resumed = ingestion_store.load_active_jobs()

        slugs = {j.topic_slug for j in resumed}
        assert slugs == {"dijkstra"}

    def test_multiple_active_jobs_each_resume_from_their_own_stage(self, db):
        ingestion_store.enqueue_job("a-star")          # stays fast_preview
        ingestion_store.enqueue_job("bellman-ford")    # advanced
        ingestion_store.persist_stage("bellman-ford", "full_processing")

        resumed = ingestion_store.load_active_jobs()
        by_slug = {j.topic_slug: resume_stage(j) for j in resumed}

        assert by_slug == {
            "a-star": "fast_preview",
            "bellman-ford": "full_processing",
        }


# --------------------------------------------------------------------------
# Dedupe: duplicate enqueue suppressed (Req 5.6)
# --------------------------------------------------------------------------

class TestDuplicateEnqueueSuppressed:
    def test_duplicate_enqueue_for_active_topic_returns_none(self, db):
        first = ingestion_store.enqueue_job("quicksort")
        assert first is not None

        # A second enqueue while a non-completed job exists is suppressed.
        second = ingestion_store.enqueue_job("quicksort")
        assert second is None

        # And no duplicate row was written -- still exactly one job for the Topic.
        rows = [r for r in _stored_jobs(db) if r["topic_slug"] == "quicksort"]
        assert len(rows) == 1

    def test_duplicate_suppressed_when_existing_job_is_running(self, db):
        ingestion_store.enqueue_job("heap-sort")
        ingestion_store.persist_status("heap-sort", "running")

        assert ingestion_store.enqueue_job("heap-sort") is None
        rows = [r for r in _stored_jobs(db) if r["topic_slug"] == "heap-sort"]
        assert len(rows) == 1

    def test_re_enqueue_allowed_after_topic_completes(self, db):
        ingestion_store.enqueue_job("radix-sort")
        ingestion_store.persist_status("radix-sort", _COMPLETED)

        # Once the prior job is completed, the Topic may be enqueued again.
        again = ingestion_store.enqueue_job("radix-sort")
        assert again is not None
        assert again.stage == "fast_preview"
        assert again.status == "queued"

    def test_distinct_topics_are_not_deduped(self, db):
        assert ingestion_store.enqueue_job("topic-x") is not None
        assert ingestion_store.enqueue_job("topic-y") is not None

        resumed = {j.topic_slug for j in ingestion_store.load_active_jobs()}
        assert resumed == {"topic-x", "topic-y"}
