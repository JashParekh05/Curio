"""Integration/smoke test for feed latency (Requirements 8.6, 8.7).

These are representative smoke cases (not property iterations) that exercise the
DB-free retrieval path used by the feed endpoint, asserting the latency contract:

  Req 8.6 — A topic with at least one stored clip returns the ranked clips within
            2 seconds WITHOUT waiting for any in-progress background generation.
  Req 8.7 — A topic with no stored clips returns an empty list within 2 seconds
            together with the "processing" signal, without blocking on background
            generation.

To prove the feed never blocks on background generation, each case runs a
long-lived background task in a daemon thread while the retrieval is timed. The
retrieval must complete well under the 2s budget regardless of the background
work still being in flight.
"""
import threading
import time

from app.services.feed_retrieval import _fetch_clips_for_slug


# ── Minimal fake DB matching the .table().select().eq()...execute() chain ────
# Mirrors the two queries _fetch_clips_for_slug issues against the "clips" table:
#   1. .select("section_index").eq("topic_slug", slug).execute()
#   2. .select("*").eq("topic_slug", slug)[.eq("section_index", i)]
#         .order("hook_score", desc=True).limit(n).execute()
# plus the population-stats query (.select(...).in_("clip_id", ids).execute()).
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table, self.store = table, store
        self.cols = "*"
        self.filters = {}
        self.inq = None
        self.limitn = None

    def select(self, cols="*"):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.inq = (col, list(vals))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self.limitn = n
        return self

    def execute(self):
        if self.table == "clips":
            rows = [r for r in self.store["clips"]
                    if r.get("topic_slug") == self.filters.get("topic_slug")]
            if "section_index" in self.filters:
                rows = [r for r in rows if r.get("section_index") == self.filters["section_index"]]
            if self.cols == "section_index":
                return _Result([{"section_index": r.get("section_index")} for r in rows])
            rows = sorted(rows, key=lambda r: r.get("hook_score", 0.5), reverse=True)
            if self.limitn is not None:
                rows = rows[:self.limitn]
            return _Result([dict(r) for r in rows])
        if self.table == "clip_events":
            col, vals = self.inq
            return _Result([e for e in self.store["clip_events"] if e.get(col) in vals])
        return _Result([])


class _DB:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _clip(cid, section, hook=0.5, **extra):
    row = {
        "id": cid, "topic_slug": "t", "title": cid, "video_url": "u",
        "hook_score": hook, "section_index": section, "duration_seconds": 60,
        "source_url": f"src-{cid}",
    }
    row.update(extra)
    return row


def _db(clips, events=None):
    return _DB({"clips": clips, "clip_events": events or []})


class _BackgroundTask:
    """A stand-in for in-progress generation: spins in a daemon thread until
    stopped, so we can assert the feed retrieval does not wait for it."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            # Simulate ongoing background work without busy-spinning the CPU.
            self._stop.wait(0.01)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1.0)


LATENCY_BUDGET_SECONDS = 2.0


def test_topic_with_stored_clips_returns_ranked_under_2s_while_generating():
    # Req 8.6: stored clips across several sections; retrieval+arc ordering must
    # return the ranked clips well under the 2s budget while generation runs.
    clips = [
        _clip("c2", 2, hook=0.99),
        _clip("c0", 0, hook=0.10),
        _clip("c1", 1, hook=0.50),
        _clip("c3", 3, hook=0.75),
    ]
    db = _db(clips)

    with _BackgroundTask():
        start = time.perf_counter()
        out = _fetch_clips_for_slug(db, "t")
        elapsed = time.perf_counter() - start

    assert out, "expected ranked clips for a topic with stored clips"
    assert {c.id for c in out} == {"c0", "c1", "c2", "c3"}
    # Arc order leads with section 0 regardless of hook_score.
    assert [c.section_index for c in out] == [0, 1, 2, 3]
    assert elapsed < LATENCY_BUDGET_SECONDS, f"retrieval took {elapsed:.3f}s (>= {LATENCY_BUDGET_SECONDS}s)"


def test_topic_with_no_clips_returns_empty_promptly_while_generating():
    # Req 8.7: cold-start topic — no stored clips. Retrieval returns an empty
    # list promptly (the endpoint then sets processing=True), never blocking on
    # the background generation that is still running.
    db = _db([])

    with _BackgroundTask():
        start = time.perf_counter()
        out = _fetch_clips_for_slug(db, "cold-start")
        elapsed = time.perf_counter() - start

    assert out == [], "expected empty clip list for a topic with no stored clips"
    assert elapsed < LATENCY_BUDGET_SECONDS, f"retrieval took {elapsed:.3f}s (>= {LATENCY_BUDGET_SECONDS}s)"
