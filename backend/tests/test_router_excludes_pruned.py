"""Example test: the Spine_Router never returns a Pruned_Topic (Task 10.6).

Asserts Req 6.3 from two angles, with no external service touched:

  1. The pure :func:`~app.services.spine_router.route` only ever returns nodes
     drawn from the scored set it was supplied -- so when the caller supplies
     only non-pruned nodes, no Pruned_Topic can appear in the Learning_Path
     (matched path and the no-match closest path alike).
  2. The ``spine_router_runner`` shell sources its scored nodes from
     ``curriculum_spine_store.load_spine``, which excludes every Pruned_Topic
     (``topics.archived``); the nodes handed to scoring -- and therefore the
     returned path -- never include the pruned Topic.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_router_excludes_pruned.py``.

Validates: Requirements 6.3
"""
from app.services import spine_router_runner as runner
from app.services.curriculum_spine import SpineEdge, SpineNode
from app.services.spine_router import ScoredNode, route


# ---------------------------------------------------------------------------
# Minimal read-only Supabase double for load_spine (reads all rows per table).
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Result(list(self._rows))


class _FakeDB:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _Query(self._store.get(name, []))


# ---------------------------------------------------------------------------
# 1. Pure route: the path is a subset of the supplied (non-pruned) nodes.
# ---------------------------------------------------------------------------

class TestPureRouteExcludesUnsupplied:
    def _scored(self, *pairs):
        return [
            ScoredNode(
                node=SpineNode(topic_slug=slug, content_level="beginner", est_minutes=10),
                match_score=score,
            )
            for slug, score in pairs
        ]

    def test_matched_path_only_contains_supplied_nodes(self):
        # "pruned-topic" is NOT supplied (the caller excluded it). Only the two
        # non-pruned nodes are scored above threshold.
        scored = self._scored(("intro", 0.9), ("deep-dive", 0.8))
        edges = [SpineEdge("intro", "deep-dive")]

        result = route("teach me", scored, edges, threshold=0.75)

        path_slugs = {n.topic_slug for n in result.path}
        assert path_slugs == {"intro", "deep-dive"}
        assert "pruned-topic" not in path_slugs

    def test_no_match_closest_path_only_contains_supplied_nodes(self):
        # No node meets threshold -> closest available node, still only a supplied
        # (non-pruned) node, never the excluded pruned topic.
        scored = self._scored(("intro", 0.2), ("deep-dive", 0.1))
        result = route("teach me", scored, [], threshold=0.75)

        assert result.enqueue_unmatched is True
        path_slugs = {n.topic_slug for n in result.path}
        assert path_slugs == {"intro"}
        assert "pruned-topic" not in path_slugs


# ---------------------------------------------------------------------------
# 2. Runner: load_spine excludes the pruned Topic, so it is never scored or
#    returned.
# ---------------------------------------------------------------------------

class TestRunnerSuppliesOnlyNonPruned:
    def test_pruned_topic_never_scored_or_returned(self, monkeypatch):
        store = {
            "curriculum_spine_nodes": [
                {"topic_slug": "keep", "content_level": "beginner", "est_minutes": 10},
                {"topic_slug": "pruned", "content_level": "beginner", "est_minutes": 10},
            ],
            "curriculum_spine_edges": [
                {"prerequisite": "keep", "dependent": "pruned"},
            ],
            # "pruned" is archived -> a Pruned_Topic excluded by load_spine.
            "topics": [
                {"slug": "keep", "archived": False},
                {"slug": "pruned", "archived": True},
            ],
        }
        db = _FakeDB(store)

        supplied_slugs: list[str] = []

        def _fake_score(query, nodes):
            # Capture exactly which nodes the router was supplied, and match them.
            supplied_slugs.extend(n.topic_slug for n in nodes)
            return [ScoredNode(node=n, match_score=0.95) for n in nodes]

        monkeypatch.setattr(runner, "_score_nodes", _fake_score)

        result = runner.resolve_path("anything", db=db, threshold=0.75)

        # The pruned Topic was never supplied to scoring (Req 6.3).
        assert "pruned" not in supplied_slugs
        assert supplied_slugs == ["keep"]
        # And it never appears in the returned Learning_Path.
        path_slugs = {n.topic_slug for n in result.path}
        assert "pruned" not in path_slugs
        assert path_slugs == {"keep"}
