"""Integration test for Takeaway_Artifact persistence and idempotency (Task 13.4).

Wires the Takeaway pure derivation core (``takeaway``) and its ``takeaway_store``
shell together against an in-memory Supabase double, with no external service
touched. It asserts the end-to-end guarantees the pure property test cannot
express on its own:

  1. A produced Takeaway_Artifact survives a simulated process restart and stays
     retrievable by the (learner, Topic) pair (Req 10.3, 10.4).
  2. Re-triggering production for a (learner, Topic) that already has an artifact
     returns the existing artifact and produces no duplicate (Req 10.6).

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_takeaway_integration.py``.

Validates: Requirements 10.3, 10.4, 10.6
"""
from app.services import takeaway_store as store


# ---------------------------------------------------------------------------
# Stateful in-memory Supabase double (select/insert/upsert with eq/limit, and
# upsert dedupe by on_conflict key columns).
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store_):
        self.table = table
        self.store = store_
        self.op = "select"
        self.payload = None
        self.on_conflict = None
        self.eqs = {}
        self._limit = None

    def select(self, *a, **k):
        self.op = "select"; return self

    def insert(self, payload, **k):
        self.op = "insert"; self.payload = payload; return self

    def upsert(self, payload, **k):
        self.op = "upsert"; self.payload = payload
        self.on_conflict = k.get("on_conflict"); return self

    def eq(self, col, val):
        self.eqs[col] = val; return self

    def limit(self, n):
        self._limit = n; return self

    def _matches(self, row):
        return all(row.get(c) == v for c, v in self.eqs.items())

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(payload)
            return _Result(list(payload))
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            for new_row in payload:
                if keys:
                    existing = next(
                        (
                            r
                            for r in rows
                            if all(r.get(k) == new_row.get(k) for k in keys)
                        ),
                        None,
                    )
                    if existing is not None:
                        existing.update(new_row)
                        continue
                rows.append(dict(new_row))
            return _Result(list(payload))
        # select
        out = [r for r in rows if self._matches(r)]
        if self._limit is not None:
            out = out[: self._limit]
        return _Result(out)


class StatefulDB:
    def __init__(self, store_=None):
        self.store = store_ or {}

    def table(self, name):
        return _Query(name, self.store)


def _clip(clip_id, ordinal, score, title, description=None):
    return {
        "id": clip_id,
        "topic_slug": "photosynthesis",
        "role_ordinal": ordinal,
        "final_score": score,
        "title": title,
        "description": description,
    }


def _seed_clips():
    # Three admitted clips spanning arc ordinals 1..3; the last in arc order is
    # the ordinal-3 clip "c3".
    return {
        "clips": [
            _clip("c1", 1, 0.9, "What it is"),
            _clip("c2", 2, 0.5, "How it works"),
            _clip("c3", 3, 0.8, "Why it matters", "the payoff"),
        ]
    }


# ---------------------------------------------------------------------------
# 1. Produced artifact survives restart and stays retrievable (Req 10.3, 10.4)
# ---------------------------------------------------------------------------

class TestPersistenceSurvivesRestart:
    def test_artifact_retrievable_after_restart(self):
        store_dict = _seed_clips()
        db = StatefulDB(store_dict)

        # Impression on the last arc clip (c3) triggers production.
        produced = store.on_impression("learner-1", "photosynthesis", "c3", db=db)
        assert produced is not None
        assert produced.learner_id == "learner-1"
        assert produced.topic_slug == "photosynthesis"
        # Points are arc-ordered: ordinal 1, 2, then 3 (with its description).
        assert produced.points == (
            "What it is",
            "How it works",
            "Why it matters: the payoff",
        )

        # Simulate a process restart: a brand-new client over the same store.
        restarted = StatefulDB(store_dict)
        reloaded = store.get_takeaway("learner-1", "photosynthesis", db=restarted)
        assert reloaded == produced

    def test_impression_on_earlier_clip_does_not_trigger(self):
        db = StatefulDB(_seed_clips())
        # c1 is not the last clip in arc order: no artifact produced.
        assert store.on_impression("learner-1", "photosynthesis", "c1", db=db) is None
        assert store.get_takeaway("learner-1", "photosynthesis", db=db) is None


# ---------------------------------------------------------------------------
# 2. Re-triggering returns the existing artifact, no duplicate (Req 10.6)
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_retrigger_returns_existing_without_duplicate(self):
        store_dict = _seed_clips()
        db = StatefulDB(store_dict)

        first = store.on_impression("learner-1", "photosynthesis", "c3", db=db)
        assert first is not None
        assert len(store_dict[store._TAKEAWAY_TABLE]) == 1

        # Re-trigger: same pair already has an artifact -> existing returned.
        second = store.on_impression("learner-1", "photosynthesis", "c3", db=db)
        assert second == first
        # No duplicate row was written.
        assert len(store_dict[store._TAKEAWAY_TABLE]) == 1

        # produce_takeaway directly is likewise idempotent.
        third = store.produce_takeaway("learner-1", "photosynthesis", db=db)
        assert third == first
        assert len(store_dict[store._TAKEAWAY_TABLE]) == 1

    def test_zero_clips_produces_no_artifact(self):
        db = StatefulDB({"clips": []})
        assert store.produce_takeaway("learner-1", "empty-topic", db=db) is None
        # No table row created for an empty topic (Req 10.5).
        assert db.store.get(store._TAKEAWAY_TABLE, []) == []

    def test_distinct_learners_keep_separate_artifacts(self):
        store_dict = _seed_clips()
        db = StatefulDB(store_dict)
        a = store.on_impression("learner-a", "photosynthesis", "c3", db=db)
        b = store.on_impression("learner-b", "photosynthesis", "c3", db=db)
        assert a is not None and b is not None
        assert a.learner_id == "learner-a"
        assert b.learner_id == "learner-b"
        assert len(store_dict[store._TAKEAWAY_TABLE]) == 2
