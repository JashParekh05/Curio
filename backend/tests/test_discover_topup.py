"""Unit tests for the signal-driven fresh-top-up topic selection
(`select_topup_topics`) — the pure "which topics to generate fresh clips for"
algorithm that drives continuous Discover freshness off the live taste/interest
signals — plus the multi-angle generation fan-out (`_topup_discover_fresh`)."""
from app.services.discover_seeding import (
    select_topup_topics,
    _topup_discover_fresh,
    _FRESH_ANGLES,
)


class TestSelectTopupTopics:
    def test_picks_top_taste_ranked_within_cap(self):
        # relevant_slugs arrives already taste-ranked; we take the top N.
        assert select_topup_topics(["a", "b", "c", "d"], {}, max_topics=2) == ["a", "b"]

    def test_skips_strongly_already_known(self):
        # interest_vector <= -0.5 means "already know / not interested" -> skip,
        # so the next eligible taste-ranked topic is promoted into the cap.
        iv = {"a": -0.6, "b": 0.3}
        assert select_topup_topics(["a", "b", "c"], iv, max_topics=2) == ["b", "c"]

    def test_mild_negative_is_not_skipped(self):
        # Only a STRONG negative (<= -0.5) is skipped; a mild one still qualifies.
        assert select_topup_topics(["a", "b"], {"a": -0.2}, max_topics=2) == ["a", "b"]

    def test_empty_relevant_yields_nothing(self):
        assert select_topup_topics([], {"x": 0.9}) == []

    def test_none_interest_vector_is_safe(self):
        assert select_topup_topics(["a", "b"], None, max_topics=1) == ["a"]

    def test_default_cap_is_four(self):
        # Aggressiveness bump: default takes up to 4 taste-ranked topics per load.
        assert select_topup_topics(["a", "b", "c", "d", "e"], {}) == ["a", "b", "c", "d"]


class TestTopupDiscoverFresh:
    def _patch_pipeline(self, monkeypatch):
        calls: list[tuple[str, str]] = []

        def fake_run_pipeline(slug, name, **kwargs):
            calls.append((slug, kwargs.get("search_query", "")))
            return 3

        monkeypatch.setattr("app.agents.pipeline_agent.run_pipeline", fake_run_pipeline)
        return calls

    def test_runs_n_distinct_angles_per_topic(self, monkeypatch):
        calls = self._patch_pipeline(monkeypatch)
        _topup_discover_fresh(["binary-search", "hashmaps"], "intermediate", angles_per_topic=2)
        # 2 topics x 2 angles = 4 fresh searches.
        assert len(calls) == 4
        # Each topic gets DISTINCT angles (no duplicate query within a topic).
        bs_queries = [q for s, q in calls if s == "binary-search"]
        assert len(bs_queries) == 2 and len(set(bs_queries)) == 2
        # Query is "<Topic Name> <angle>" so it's a cache miss.
        assert all(q.startswith("Binary Search ") for q in bs_queries)
        assert all(q.split("Binary Search ", 1)[1] in _FRESH_ANGLES for q in bs_queries)

    def test_angles_clamped_to_pool_size(self, monkeypatch):
        calls = self._patch_pipeline(monkeypatch)
        # Asking for more angles than exist clamps to the pool (no duplicates).
        _topup_discover_fresh(["a"], "beginner", angles_per_topic=999)
        assert len(calls) == len(_FRESH_ANGLES)
        assert len({q for _, q in calls}) == len(_FRESH_ANGLES)

    def test_per_search_failure_is_isolated(self, monkeypatch):
        def boom(slug, name, **kwargs):
            raise RuntimeError("quota spent")

        monkeypatch.setattr("app.agents.pipeline_agent.run_pipeline", boom)
        # A failing search must not raise out of the background task.
        _topup_discover_fresh(["a", "b"], "beginner", angles_per_topic=2)
