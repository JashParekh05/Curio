"""Unit tests for the signal-driven fresh-top-up topic selection
(`select_topup_topics`) — the pure "which topics to generate fresh clips for"
algorithm that drives continuous Discover freshness off the live taste/interest
signals."""
from app.services.discover_seeding import select_topup_topics


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
