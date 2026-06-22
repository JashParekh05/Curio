from app.services.feed_scoring import (
    _parse_vector,
    _compute_scores,
    _transcript_boost,
    _interleave_topics,
    _spread_by_source,
    LEARN_WEIGHTS,
    DISCOVER_WEIGHTS,
)
from app.services.feed_retrieval import _order_by_arc
from app.models.schemas import FeedResponse
from tests.conftest import make_clip


class TestParseVector:
    def test_list_passthrough(self):
        assert _parse_vector([0.1, 0.2]) == [0.1, 0.2]

    def test_json_string(self):
        assert _parse_vector("[0.1, 0.2]") == [0.1, 0.2]

    def test_none(self):
        assert _parse_vector(None) is None

    def test_garbage_string(self):
        assert _parse_vector("not json") is None


class TestComputeScores:
    def test_neutral_baseline(self):
        # No pop stats, no avg watch, no interest/taste, no created_at:
        # 0.28*0.5 + 0.23*0.5 + 0.18*1.0 + 0.13*0.5 + 0.10*0.5 + 0.08*0.5 = 0.59
        clip = make_clip(hook_score=0.5)
        _compute_scores([clip], {}, None)
        assert clip.final_score == 0.59

    def test_unknown_population_does_not_double_count_hook(self):
        # A high-hook NEW clip with no population stats. The population term must
        # fall back to a NEUTRAL 0.5, not to hook_score. With hook=0.9:
        #   0.28*0.9 + 0.23*0.5 + 0.18*1.0 + 0.13*0.5 + 0.10*0.5 + 0.08*0.5 = 0.702
        # The old bug (pop defaulting to hook) would have scored 0.794, counting
        # hook twice. hook=0.5 in test_neutral_baseline masks this because the
        # neutral default and the hook score coincide there.
        clip = make_clip(hook_score=0.9)
        _compute_scores([clip], {}, None)
        assert clip.final_score == 0.702

    def test_liked_topic_scores_higher(self):
        neutral = make_clip(topic_slug="a", hook_score=0.5)
        liked = make_clip(topic_slug="b", hook_score=0.5)
        _compute_scores([neutral], {}, None, interest_vector={})
        _compute_scores([liked], {}, None, interest_vector={"b": 1.0})
        assert liked.final_score > neutral.final_score

    def test_higher_hook_scores_higher(self):
        low = make_clip(hook_score=0.2)
        high = make_clip(hook_score=0.9)
        _compute_scores([low, high], {}, None)
        assert high.final_score > low.final_score


class TestTranscriptBoost:
    def test_keyword_match_boosts(self):
        clip = make_clip(transcript="this explains binary search clearly", final_score=0.5)
        _transcript_boost([clip], "binary search")
        assert clip.final_score > 0.5

    def test_no_query_unchanged(self):
        clip = make_clip(transcript="anything", final_score=0.5)
        _transcript_boost([clip], "")
        assert clip.final_score == 0.5

    def test_only_stopwords_unchanged(self):
        clip = make_clip(transcript="the and for that", final_score=0.5)
        _transcript_boost([clip], "the and for")
        assert clip.final_score == 0.5


class TestSpreadBySource:
    def test_no_consecutive_same_source(self):
        clips = [
            make_clip(source_url="A"),
            make_clip(source_url="A"),
            make_clip(source_url="B"),
        ]
        out = _spread_by_source(clips)
        sources = [c.source_url for c in out]
        assert sources == ["A", "B", "A"]

    def test_single_clip_unchanged(self):
        clips = [make_clip(source_url="A")]
        assert _spread_by_source(clips) == clips


class TestInterleaveTopics:
    def test_single_feed_passthrough(self):
        feed = FeedResponse(topic_slug="a", clips=[make_clip()], processing=False)
        assert _interleave_topics([feed]) == [feed]

    def test_no_clips_lost(self):
        a = FeedResponse(topic_slug="a", clips=[make_clip(topic_slug="a") for _ in range(8)], processing=False)
        b = FeedResponse(topic_slug="b", clips=[make_clip(topic_slug="b") for _ in range(3)], processing=False)
        out = _interleave_topics([a, b])
        before = {c.id for f in [a, b] for c in f.clips}
        after = {c.id for f in out for c in f.clips}
        assert before == after


class TestSurfaceWeights:
    """Discover is personalization-first; Learn is structure/hook-first. The
    same two clips should rank in OPPOSITE order under the two profiles."""

    def _pair(self):
        # hooky: strong hook, no taste match. on_taste: weak hook, perfect taste match.
        hooky = make_clip(hook_score=0.9)
        on_taste = make_clip(hook_score=0.2, embedding=[1.0, 0.0])
        return hooky, on_taste

    def test_discover_lets_taste_beat_hook(self):
        hooky, on_taste = self._pair()
        _compute_scores([hooky, on_taste], {}, None, taste_vector=[1.0, 0.0], weights=DISCOVER_WEIGHTS)
        assert on_taste.final_score > hooky.final_score

    def test_learn_lets_hook_beat_taste(self):
        hooky, on_taste = self._pair()
        _compute_scores([hooky, on_taste], {}, None, taste_vector=[1.0, 0.0], weights=LEARN_WEIGHTS)
        assert hooky.final_score > on_taste.final_score

    def test_default_profile_is_learn(self):
        a, b = self._pair()
        _compute_scores([a, b], {}, None, taste_vector=[1.0, 0.0])  # no weights arg
        a2, b2 = self._pair()
        _compute_scores([a2, b2], {}, None, taste_vector=[1.0, 0.0], weights=LEARN_WEIGHTS)
        assert a.final_score == a2.final_score and b.final_score == b2.final_score

    def test_weight_profiles_sum_to_one(self):
        assert abs(sum(LEARN_WEIGHTS.values()) - 1.0) < 1e-9
        assert abs(sum(DISCOVER_WEIGHTS.values()) - 1.0) < 1e-9


class TestOrderByArc:
    def test_section_index_no_longer_drives_order(self):
        # Req 2.3: the legacy section_index ordering path is removed. With no
        # Canonical_Arc (and no pedagogical_role), every clip is role-less and
        # orders by final_score descending — section_index is ignored. The
        # highest-scoring clip (section 3) therefore leads.
        clips = [
            make_clip(section_index=3, hook_score=0.99, source_url="d"),
            make_clip(section_index=0, hook_score=0.10, source_url="a"),
            make_clip(section_index=1, hook_score=0.50, source_url="b"),
            make_clip(section_index=2, hook_score=0.70, source_url="c"),
        ]
        for c in clips:
            c.final_score = c.hook_score
        out = _order_by_arc(clips)
        assert [c.section_index for c in out] == [3, 2, 1, 0]

    def test_ranks_within_beat_by_score(self):
        # Two clips in the same beat (different sources so spread keeps them
        # both) — higher score comes first.
        hi = make_clip(section_index=1, hook_score=0.9, source_url="x")
        lo = make_clip(section_index=1, hook_score=0.2, source_url="y")
        hi.final_score, lo.final_score = 0.9, 0.2
        out = _order_by_arc([lo, hi])
        assert [c.id for c in out] == [hi.id, lo.id]

    def test_no_sections_degrades_to_score_order(self):
        a = make_clip(hook_score=0.3, source_url="a")
        b = make_clip(hook_score=0.8, source_url="b")
        a.final_score, b.final_score = 0.3, 0.8
        out = _order_by_arc([a, b])
        assert [c.id for c in out] == [b.id, a.id]

    def test_no_clips_lost(self):
        clips = [make_clip(section_index=i % 4, source_url=str(i)) for i in range(12)]
        for c in clips:
            c.final_score = 0.5
        out = _order_by_arc(clips)
        assert {c.id for c in out} == {c.id for c in clips}
        assert len(out) == len(clips)

    def test_narrative_rank_no_longer_orders_within_beat(self):
        # Req 2.3: the legacy narrative_rank ordering path is removed. With no
        # Canonical_Arc, role-less clips order by final_score descending and
        # narrative_rank is ignored, so the higher-scoring clip leads even
        # though narrative_rank would have wanted the other first.
        hi = make_clip(section_index=1, hook_score=0.9, source_url="x")
        lo = make_clip(section_index=1, hook_score=0.1, source_url="y")
        hi.final_score, lo.final_score = 0.9, 0.1
        hi.narrative_rank, lo.narrative_rank = 1, 0  # legacy story wanted lo first
        out = _order_by_arc([hi, lo])
        assert [c.id for c in out] == [hi.id, lo.id]  # score order, rank ignored

    def test_partial_narrative_rank_falls_back_to_score(self):
        # If any clip in the beat lacks a rank, the beat uses score ordering.
        a = make_clip(section_index=2, hook_score=0.2, source_url="a")
        b = make_clip(section_index=2, hook_score=0.8, source_url="b")
        a.final_score, b.final_score = 0.2, 0.8
        a.narrative_rank = 0  # b has none
        out = _order_by_arc([a, b])
        assert [c.id for c in out] == [b.id, a.id]  # score order
