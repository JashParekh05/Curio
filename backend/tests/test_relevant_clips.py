"""Unit tests for the on-topic clip relevance filter (game.relevant_clips).

In production an "imperialism" node landed on an off-topic history video because
``select_clip`` ranks only by duration / captions / views — never by topical
relevance. ``relevant_clips`` is a deterministic pre-filter that drops
candidates whose title/description share no subject term with the node (the goal
is a weaker secondary signal), with a safe fallback so a clip is never lost.
"""
from app.services import game


def _clip(video_id: str, title: str, description: str = "") -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "description": description,
        "duration_seconds": 120,
        "has_caption": True,
        "view_count": 1000,
    }


class TestRelevantClips:
    def test_drops_off_topic_clip_keeps_on_topic(self):
        clips = [
            _clip("on", "Imperialism explained: empires and colonies"),
            _clip("off", "Top 10 funniest cat videos of all time"),
        ]
        kept = game.relevant_clips(clips, "imperialism", "the age of empires")
        ids = [c["video_id"] for c in kept]
        assert "on" in ids
        assert "off" not in ids

    def test_matches_on_description_when_title_is_generic(self):
        clips = [
            _clip("ok", "Explained", "A clear look at recursion and base cases"),
            _clip("no", "Explained", "A history of the Roman aqueducts"),
        ]
        kept = game.relevant_clips(clips, "recursion", "backtracking")
        ids = [c["video_id"] for c in kept]
        assert ids == ["ok"]

    def test_returns_empty_when_nothing_matches(self):
        # Sparse/odd metadata with no subject overlap: prefer NO clip over an
        # off-topic one — deliver_node then shows the node with no video.
        clips = [_clip("a", "Untitled"), _clip("b", "Clip 2")]
        kept = game.relevant_clips(clips, "photosynthesis", "biology")
        assert kept == []

    def test_preserves_order_of_relevant_clips(self):
        clips = [
            _clip("c1", "Photosynthesis basics"),
            _clip("junk", "Unrelated cooking show"),
            _clip("c2", "How photosynthesis works in plants"),
        ]
        kept = game.relevant_clips(clips, "photosynthesis", "biology")
        assert [c["video_id"] for c in kept] == ["c1", "c2"]

    def test_empty_input_returns_empty(self):
        assert game.relevant_clips([], "anything", "goal") == []

    def test_goal_terms_used_when_node_has_no_subject_terms(self):
        # A node made only of filler words ("learn about") yields no node tokens;
        # the goal's subject terms then drive relevance.
        clips = [
            _clip("g", "A tour of the French Revolution"),
            _clip("x", "Knitting for beginners"),
        ]
        kept = game.relevant_clips(clips, "learn about it", "the French Revolution")
        ids = [c["video_id"] for c in kept]
        assert "g" in ids
        assert "x" not in ids

    def test_ignores_filler_words_in_matching(self):
        # "explained"/"guide" are filler and must not create a false match.
        clips = [_clip("filler", "A complete guide, explained: tutorial overview")]
        kept = game.relevant_clips(clips, "mitosis", "cell biology")
        # No real subject overlap -> no clip (better than an off-topic one).
        assert kept == []
