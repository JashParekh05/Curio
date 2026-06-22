"""Lever 2: segmentation candidate bounding in pipeline_agent._node_transcribe."""
import app.services.youtube as youtube
from app.agents import pipeline_agent


def _state(section_index, n_videos=6, missing=()):
    return {
        "videos": [{"video_id": str(i)} for i in range(1, n_videos + 1)],
        "section_index": section_index,
        "topic_slug": "t",
        "topic_name": "T",
    }


def _stub_transcripts(monkeypatch, missing=()):
    def fake(vid):
        return None if vid in missing else [{"start": 0, "duration": 1, "text": "x"}]
    monkeypatch.setattr(youtube, "_fetch_transcript", fake)


def test_first_section_keeps_one_video(monkeypatch):
    _stub_transcripts(monkeypatch)
    out = pipeline_agent._node_transcribe(_state(0))
    assert [v["video_id"] for v in out["videos"]] == ["1"]


def test_other_sections_keep_two_videos(monkeypatch):
    _stub_transcripts(monkeypatch)
    for sec in (1, 2, 3):
        out = pipeline_agent._node_transcribe(_state(sec))
        assert len(out["videos"]) == 2


def test_none_section_keeps_two(monkeypatch):
    _stub_transcripts(monkeypatch)
    out = pipeline_agent._node_transcribe(_state(None))
    assert len(out["videos"]) == 2


def test_skips_videos_without_transcript(monkeypatch):
    # videos 1 and 2 lack transcripts → should keep 3 and 4 for a non-first section
    _stub_transcripts(monkeypatch, missing={"1", "2"})
    out = pipeline_agent._node_transcribe(_state(1))
    assert [v["video_id"] for v in out["videos"]] == ["3", "4"]
    assert len(out["errors"]) == 2


def test_all_missing_keeps_none(monkeypatch):
    _stub_transcripts(monkeypatch, missing={"1", "2", "3", "4", "5", "6"})
    out = pipeline_agent._node_transcribe(_state(1))
    assert out["videos"] == []


# --- candidate relevance ranking (_rank_candidates) ------------------------

import app.services.embeddings as embeddings


def _vid(i, title="", caption=False, channel_title=None):
    return {"video_id": str(i), "title": title, "description": None,
            "has_caption": caption, "channel_title": channel_title}


def _patch_embeddings(monkeypatch, q_vec, vec_by_title):
    """Stub the local embedding model: query -> q_vec, each candidate title ->
    its vector from vec_by_title. Real cosine_similarity (dot product) is used."""
    monkeypatch.setattr(embeddings, "embed_text", lambda text: q_vec)
    monkeypatch.setattr(
        embeddings, "embed_texts",
        lambda texts: [vec_by_title.get(t.strip(), [0.0, 0.0]) for t in texts],
    )


class TestRankCandidates:
    def test_orders_by_relevance(self, monkeypatch):
        videos = [_vid(1, "far"), _vid(2, "near"), _vid(3, "mid")]
        _patch_embeddings(monkeypatch, [1.0, 0.0],
                          {"far": [0.1, 0.9], "near": [0.9, 0.1], "mid": [0.5, 0.5]})
        out = pipeline_agent._rank_candidates(videos, "query")
        assert [v["video_id"] for v in out] == ["2", "3", "1"]

    def test_caption_breaks_ties(self, monkeypatch):
        # identical relevance; the captioned video should float ahead
        videos = [_vid(1, "same", caption=False), _vid(2, "same", caption=True)]
        _patch_embeddings(monkeypatch, [1.0, 0.0], {"same": [0.5, 0.5]})
        out = pipeline_agent._rank_candidates(videos, "query")
        assert [v["video_id"] for v in out] == ["2", "1"]

    def test_views_break_ties(self, monkeypatch):
        # identical relevance + caption; the more-watched video floats ahead
        a = _vid(1, "same"); a["view_count"] = 1_000
        b = _vid(2, "same"); b["view_count"] = 5_000_000
        _patch_embeddings(monkeypatch, [1.0, 0.0], {"same": [0.5, 0.5]})
        out = pipeline_agent._rank_candidates([a, b], "query")
        assert [v["video_id"] for v in out] == ["2", "1"]

    def test_relevance_beats_popularity(self, monkeypatch):
        # a far-more-relevant obscure video must still beat a viral off-topic one
        relevant = _vid(1, "near"); relevant["view_count"] = 100
        viral = _vid(2, "far"); viral["view_count"] = 50_000_000
        _patch_embeddings(monkeypatch, [1.0, 0.0],
                          {"near": [0.95, 0.05], "far": [0.2, 0.98]})
        out = pipeline_agent._rank_candidates([viral, relevant], "query")
        assert [v["video_id"] for v in out] == ["1", "2"]

    def test_trusted_channel_breaks_ties(self, monkeypatch):
        # identical relevance; the trusted-channel video should float ahead
        a = _vid(1, "same", channel_title="Random Uploader")
        b = _vid(2, "same", channel_title="NeetCode")
        _patch_embeddings(monkeypatch, [1.0, 0.0], {"same": [0.5, 0.5]})
        out = pipeline_agent._rank_candidates([a, b], "query")
        assert [v["video_id"] for v in out] == ["2", "1"]

    def test_trusted_channel_overcomes_small_relevance_gap(self, monkeypatch):
        # b is slightly more relevant, but a is from a trusted channel and the
        # gap is within CHANNEL_BONUS -> a wins.
        a = _vid(1, "near", channel_title="StatQuest with Josh Starmer")
        b = _vid(2, "verynear", channel_title="Some Rando")
        _patch_embeddings(monkeypatch, [1.0, 0.0],
                          {"near": [0.9, 0.2], "verynear": [1.0, 0.0]})
        out = pipeline_agent._rank_candidates([b, a], "query")
        assert [v["video_id"] for v in out] == ["1", "2"]

    def test_large_relevance_gap_still_beats_trusted_channel(self, monkeypatch):
        # a trusted but barely-relevant video must NOT beat a far-more-relevant one
        trusted = _vid(1, "far", channel_title="3Blue1Brown")
        relevant = _vid(2, "near", channel_title="Nobody")
        _patch_embeddings(monkeypatch, [1.0, 0.0],
                          {"far": [0.1, 0.9], "near": [0.95, 0.05]})
        out = pipeline_agent._rank_candidates([trusted, relevant], "query")
        assert [v["video_id"] for v in out] == ["2", "1"]

    def test_noop_when_embeddings_unavailable(self, monkeypatch):
        videos = [_vid(1, "a", caption=False), _vid(2, "b", caption=True)]
        monkeypatch.setattr(embeddings, "embed_text", lambda text: None)
        out = pipeline_agent._rank_candidates(videos, "query")
        # falls back to caption-first ordering, never crashes
        assert [v["video_id"] for v in out] == ["2", "1"]

    def test_noop_without_query_or_text(self, monkeypatch):
        # no query -> unchanged
        videos = [_vid(1, "a"), _vid(2, "b")]
        assert pipeline_agent._rank_candidates(videos, "") == videos
        # textless candidates -> unchanged (preserves recall order)
        textless = [{"video_id": "1"}, {"video_id": "2"}]
        assert pipeline_agent._rank_candidates(textless, "query") == textless


def test_channel_bonus_matches_normalized_substring():
    cb = pipeline_agent._channel_bonus
    assert cb({"channel_title": "StatQuest with Josh Starmer"}) == pipeline_agent.CHANNEL_BONUS
    assert cb({"channel_title": "3Blue1Brown"}) == pipeline_agent.CHANNEL_BONUS
    assert cb({"channel_title": "freeCodeCamp.org"}) == pipeline_agent.CHANNEL_BONUS
    assert cb({"channel_title": "Some Random Channel"}) == 0.0
    assert cb({"channel_title": None}) == 0.0
    assert cb({}) == 0.0


def test_transcribe_keeps_most_relevant(monkeypatch):
    """End to end: the kept video is the most relevant, not YouTube's #1."""
    state = {
        "videos": [_vid(1, "far"), _vid(2, "near"), _vid(3, "mid")],
        "section_index": 0,  # keep exactly one
        "topic_slug": "t",
        "topic_name": "T",
        "search_query": "query",
    }
    _patch_embeddings(monkeypatch, [1.0, 0.0],
                      {"far": [0.1, 0.9], "near": [0.9, 0.1], "mid": [0.5, 0.5]})
    _stub_transcripts(monkeypatch)
    out = pipeline_agent._node_transcribe(state)
    assert [v["video_id"] for v in out["videos"]] == ["2"]


# --- search-node config guard (no live YouTube calls) ----------------------

class _FakeResp:
    def __init__(self, payload, ok=True, status=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_search_node_uses_medium_duration_and_captures_signals(monkeypatch):
    """Locks in retrieval-quality config so it cannot silently regress:
      - duration filter MUST be 'medium' (not 'short' — that contradicts the
        planner's 5-10 min intent)
      - videos.list MUST request the 'statistics' part (for view_count)
      - candidates MUST carry view_count and has_caption through to ranking
    """
    import requests as _requests
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    # Force a cache miss so the live path runs (search_cache_get swallows errors).
    monkeypatch.setattr(youtube, "search_cache_get", lambda q: None)
    monkeypatch.setattr(youtube, "search_cache_put", lambda q, v: None)

    captured = {}

    search_payload = {"items": [
        {"id": {"videoId": "vid1"}, "snippet": {"title": "A", "description": "", "thumbnails": {}}},
        {"id": {"videoId": "vid2"}, "snippet": {"title": "B", "description": "", "thumbnails": {}}},
    ]}
    details_payload = {"items": [
        {"id": "vid1", "contentDetails": {"duration": "PT6M30S", "caption": "true"},
         "snippet": {}, "statistics": {"viewCount": "1234567"}},
        {"id": "vid2", "contentDetails": {"duration": "PT8M", "caption": "false"},
         "snippet": {}, "statistics": {"viewCount": "42"}},
    ]}

    def fake_get(url, params=None, timeout=None):
        if "search" in url:
            captured["search_params"] = params
            return _FakeResp(search_payload)
        captured["details_params"] = params
        return _FakeResp(details_payload)

    monkeypatch.setattr(_requests, "get", fake_get)

    out = pipeline_agent._node_search({
        "topic_slug": "t", "topic_name": "T", "search_query": "q", "section_index": 1,
    })

    assert captured["search_params"]["videoDuration"] == "medium"
    assert "statistics" in captured["details_params"]["part"]
    by_id = {v["video_id"]: v for v in out["videos"]}
    assert by_id["vid1"]["view_count"] == 1234567
    assert by_id["vid1"]["has_caption"] is True
    assert by_id["vid2"]["view_count"] == 42
    assert by_id["vid2"]["has_caption"] is False
    assert by_id["vid1"]["duration_seconds"] == 390  # 6:30 parsed correctly
