"""Shared test fixtures. Run from the backend/ dir: `python -m pytest`."""
import itertools
import pytest

from app.models.schemas import Clip

_counter = itertools.count()


def make_clip(**overrides) -> Clip:
    """Build a Clip with sane defaults; override any field per test."""
    n = next(_counter)
    base = {
        "id": f"clip-{n}",
        "topic_slug": "binary-search",
        "title": f"Clip {n}",
        "video_url": "https://example.com/v",
        "hook_score": 0.5,
    }
    base.update(overrides)
    return Clip(**base)


@pytest.fixture
def clip_factory():
    return make_clip


# --- reusable fake Supabase client for DB-wrapper tests --------------------

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Chainable stub supporting select/eq/in_/limit/order + upsert, with a
    simple in-memory filter on execute(). Records upserts; can raise to
    exercise error paths."""
    def __init__(self, table, store, rec, fail):
        self.table, self.store, self.rec, self.fail = table, store, rec, fail
        self.op = "select"
        self.filters = {}
        self.inq = None
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"; return self

    def upsert(self, payload, **k):
        self.op = "upsert"; self.payload = payload; return self

    def insert(self, payload, **k):
        self.op = "insert"; self.payload = payload; return self

    def update(self, payload, **k):
        self.op = "update"; self.payload = payload; return self

    def eq(self, col, val):
        self.filters[col] = val; return self

    def in_(self, col, vals):
        self.inq = (col, list(vals)); return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self.table in self.fail:
            raise RuntimeError(f"db down: {self.table}")
        if self.op == "upsert":
            self.rec["upserts"].append((self.table, self.payload))
            return _FakeResult([])
        if self.op == "insert":
            self.rec["inserts"].append((self.table, self.payload))
            self.store.setdefault(self.table, []).append(self.payload)
            return _FakeResult([self.payload])
        if self.op == "update":
            self.rec.setdefault("updates", []).append(
                (self.table, self.payload, dict(self.filters))
            )
            updated = []
            for r in self.store.get(self.table, []):
                if all(r.get(k) == v for k, v in self.filters.items()):
                    r.update(self.payload)
                    updated.append(r)
            return _FakeResult(updated)
        rows = self.store.get(self.table, [])
        out = []
        for r in rows:
            if not all(r.get(k) == v for k, v in self.filters.items()):
                continue
            if self.inq:
                col, vals = self.inq
                if r.get(col) not in vals:
                    continue
            out.append(r)
        return _FakeResult(out)


class _FakeRPC:
    def __init__(self, name, params, rec):
        self.name, self.params, self.rec = name, params, rec

    def execute(self):
        self.rec["rpcs"].append((self.name, self.params))
        return _FakeResult([])


class FakeDB:
    """Minimal Supabase double. `store` maps table -> list[dict]; `fail` is a
    set of table names whose queries raise. Records `upserts` and `rpcs`."""
    def __init__(self, store=None, fail=None):
        self.store = store or {}
        self.fail = fail or set()
        self.rec = {"upserts": [], "rpcs": [], "inserts": []}

    def table(self, name):
        return _FakeQuery(name, self.store, self.rec, self.fail)

    def rpc(self, name, params):
        return _FakeRPC(name, params, self.rec)

    def rpc_named(self, name):
        return [params for n, params in self.rec["rpcs"] if n == name]
