"""Integration test for Curriculum_Spine eligibility and reload (Task 9.8).

Wires the Curriculum_Spine pure decision core (``curriculum_spine``) and its
``curriculum_spine_store`` shell together against an in-memory Supabase double,
with no external service touched. It asserts the end-to-end guarantees the pure
property tests cannot express on their own:

  1. Spine reload after a simulated restart returns the recorded Spine_Nodes
     (slug, Content_Level, estimated duration) and Spine_Edge endpoints (Req 5.1).
  2. A Pruned_Topic (``topics.archived``) is excluded from the reloaded spine,
     and a newly Arc_Complete, non-pruned Topic is admitted as a Spine_Node
     (Req 5.7, 5.8).
  3. A registered Spine_Edge represents the directed prerequisite relationship
     A -> B (Req 5.2).

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_spine_eligibility.py``.

Validates: Requirements 5.1, 5.2, 5.7, 5.8
"""
from app.services import curriculum_spine_store as store
from app.services.curriculum_spine import SpineEdge, SpineNode


# ---------------------------------------------------------------------------
# Stateful in-memory Supabase double (select/insert/upsert/delete with eq/in_,
# and upsert dedupe by on_conflict key columns).
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
        self.inq = None
        self._limit = None

    def select(self, *a, **k):
        self.op = "select"; return self

    def insert(self, payload, **k):
        self.op = "insert"; self.payload = payload; return self

    def upsert(self, payload, **k):
        self.op = "upsert"; self.payload = payload
        self.on_conflict = k.get("on_conflict"); return self

    def delete(self, **k):
        self.op = "delete"; return self

    def eq(self, col, val):
        self.eqs[col] = val; return self

    def in_(self, col, vals):
        self.inq = (col, list(vals)); return self

    def limit(self, n):
        self._limit = n; return self

    def order(self, *a, **k):
        return self

    def _matches(self, row):
        if not all(row.get(c) == v for c, v in self.eqs.items()):
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
    def __init__(self, store_=None):
        self.store = store_ or {}

    def table(self, name):
        return _Query(name, self.store)


def _topics(*rows):
    return [
        {
            "slug": slug,
            "archived": archived,
            "arc_complete": arc_complete,
        }
        for slug, archived, arc_complete in rows
    ]


# ---------------------------------------------------------------------------
# 1. Reload after restart returns the recorded nodes and edges (Req 5.1)
# ---------------------------------------------------------------------------

class TestReloadAfterRestart:
    def test_recorded_nodes_and_edges_reload_identically(self):
        store_dict = {
            "topics": _topics(
                ("algebra", False, True),
                ("calculus", False, True),
            ),
        }
        db = StatefulDB(store_dict)

        assert store.register_spine_node(
            SpineNode("algebra", "beginner", 30), db=db
        )
        assert store.register_spine_node(
            SpineNode("calculus", "advanced", 90), db=db
        )
        store.register_spine_edge(SpineEdge("algebra", "calculus"), db=db)

        # Simulate a process restart: a brand-new client over the same persisted
        # store. The reloaded spine reproduces every recorded field.
        restarted = StatefulDB(store_dict)
        nodes, edges = store.load_spine(db=restarted)

        by_slug = {n.topic_slug: n for n in nodes}
        assert set(by_slug) == {"algebra", "calculus"}
        assert by_slug["algebra"].content_level == "beginner"
        assert by_slug["algebra"].est_minutes == 30
        assert by_slug["calculus"].content_level == "advanced"
        assert by_slug["calculus"].est_minutes == 90

        assert edges == [SpineEdge("algebra", "calculus")]


# ---------------------------------------------------------------------------
# 2. Pruned excluded, arc-complete admitted (Req 5.7, 5.8)
# ---------------------------------------------------------------------------

class TestEligibility:
    def test_arc_complete_non_pruned_admitted(self):
        db = StatefulDB({"topics": _topics(("graphs", False, True))})
        assert store.is_spine_eligible("graphs", db=db) is True
        assert store.register_spine_node(SpineNode("graphs", "beginner", 20), db=db)

        nodes, _edges = store.load_spine(db=db)
        assert [n.topic_slug for n in nodes] == ["graphs"]

    def test_not_arc_complete_topic_refused(self):
        db = StatefulDB({"topics": _topics(("loose", False, False))})
        assert store.is_spine_eligible("loose", db=db) is False
        # Registration of an ineligible Topic is refused and persists nothing.
        assert store.register_spine_node(SpineNode("loose", "beginner", 10), db=db) is False
        nodes, _edges = store.load_spine(db=db)
        assert nodes == []

    def test_pruned_topic_excluded_from_reload(self):
        # Two arc-complete nodes registered, then one is pruned (archived).
        store_dict = {
            "topics": _topics(
                ("keep", False, True),
                ("drop", False, True),
            ),
        }
        db = StatefulDB(store_dict)
        store.register_spine_node(SpineNode("keep", "beginner", 15), db=db)
        store.register_spine_node(SpineNode("drop", "beginner", 15), db=db)
        store.register_spine_edge(SpineEdge("keep", "drop"), db=db)

        # Prune "drop": mark archived and remove it from the spine (Req 5.9).
        for row in store_dict["topics"]:
            if row["slug"] == "drop":
                row["archived"] = True
        assert store.prune_from_spine("drop", db=db) is True

        nodes, edges = store.load_spine(db=db)
        # Pruned node and its incident edge are gone (Req 5.7, 5.9).
        assert [n.topic_slug for n in nodes] == ["keep"]
        assert edges == []

    def test_pruned_topic_is_not_eligible(self):
        db = StatefulDB({"topics": _topics(("archived-topic", True, True))})
        # Arc_Complete but archived -> not eligible (Req 5.7).
        assert store.is_spine_eligible("archived-topic", db=db) is False


# ---------------------------------------------------------------------------
# 3. An edge represents the directed prerequisite relationship A -> B (Req 5.2)
# ---------------------------------------------------------------------------

class TestEdgeDirection:
    def test_edge_is_directed_prerequisite_to_dependent(self):
        store_dict = {
            "topics": _topics(
                ("intro", False, True),
                ("advanced", False, True),
            ),
        }
        db = StatefulDB(store_dict)
        store.register_spine_node(SpineNode("intro", "beginner", 10), db=db)
        store.register_spine_node(SpineNode("advanced", "advanced", 40), db=db)

        # intro is a prerequisite of advanced: the stored edge is intro -> advanced.
        result = store.register_spine_edge(SpineEdge("intro", "advanced"), db=db)
        assert result.rejected_edge is None

        _nodes, edges = store.load_spine(db=db)
        assert len(edges) == 1
        assert edges[0].prerequisite == "intro"
        assert edges[0].dependent == "advanced"

    def test_cycle_creating_edge_rejected_and_not_persisted(self):
        store_dict = {
            "topics": _topics(
                ("x", False, True),
                ("y", False, True),
            ),
        }
        db = StatefulDB(store_dict)
        store.register_spine_node(SpineNode("x", "beginner", 5), db=db)
        store.register_spine_node(SpineNode("y", "beginner", 5), db=db)
        store.register_spine_edge(SpineEdge("x", "y"), db=db)

        # y -> x would close a cycle: rejected, and the edge table is unchanged.
        result = store.register_spine_edge(SpineEdge("y", "x"), db=db)
        assert result.rejected_edge == ("y", "x")

        _nodes, edges = store.load_spine(db=db)
        assert edges == [SpineEdge("x", "y")]
