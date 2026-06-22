"""Property-based test for reseed identification.

# Feature: content-retrieval-revamp, Property 26: Reseed identification respects threshold and Minimum_Views

*For any* set of per-Clip and per-Topic Watch_Quality signals, the
Reseed_Prioritizer identifies *exactly* those signals whose Watch_Quality is
strictly below the Recut_Threshold AND whose view count is at least the
Minimum_Views floor -- below-Minimum_Views signal is excluded so low-confidence
signal never drives reseeding -- and the identification is deterministic, so
identical inputs always yield identical lists (Req 7.1, 7.2, 7.6, 7.9).

Imports only the pure module under test (``reseed_prioritizer``), so it runs
offline with no external service.

Validates: Requirements 7.1, 7.2, 7.6, 7.9
"""
from hypothesis import given, settings, strategies as st

from app.services.reseed_prioritizer import (
    ClipQualitySignal,
    WatchQualitySignal,
    identify_clips_to_reseed,
    identify_topics_to_reseed,
)

# Watch_Quality spans the full [0,1] band so the threshold boundary is exercised
# from both sides; views span below and above any Minimum_Views in [1, 100].
_quality = st.floats(min_value=0.0, max_value=1.0)
_views = st.integers(min_value=0, max_value=200)
_threshold = st.floats(min_value=0.0, max_value=1.0)
_min_views = st.integers(min_value=1, max_value=100)
_slug = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)


_topic_signals = st.lists(
    st.builds(WatchQualitySignal, topic_slug=_slug, watch_quality=_quality, views=_views),
    max_size=40,
)

_clip_signals = st.lists(
    st.builds(
        ClipQualitySignal,
        clip_id=_slug,
        topic_slug=_slug,
        watch_quality=_quality,
        views=_views,
    ),
    max_size=40,
)


class TestTopicIdentification:
    @settings(max_examples=100)
    @given(signals=_topic_signals, threshold=_threshold, minimum_views=_min_views)
    def test_topics_identified_are_exactly_below_threshold_and_above_min_views(
        self, signals, threshold, minimum_views
    ):
        result = identify_topics_to_reseed(signals, threshold, minimum_views)

        expected_topics = [
            s.topic_slug
            for s in signals
            if s.views >= minimum_views and s.watch_quality < threshold
        ]
        # Identified items are exactly the qualifying signals, in input order.
        assert [item.topic for item in result] == expected_topics
        # Every identified item is a pending reseed candidate.
        assert all(item.status == "pending" for item in result)
        # No excluded signal slipped in: each identified item really qualifies.
        qualifying = {
            s.topic_slug
            for s in signals
            if s.views >= minimum_views and s.watch_quality < threshold
        }
        assert all(item.topic in qualifying for item in result)

    @settings(max_examples=100)
    @given(signals=_topic_signals, threshold=_threshold, minimum_views=_min_views)
    def test_topic_identification_is_deterministic(self, signals, threshold, minimum_views):
        a = identify_topics_to_reseed(signals, threshold, minimum_views)
        b = identify_topics_to_reseed(signals, threshold, minimum_views)
        assert a == b


class TestClipIdentification:
    @settings(max_examples=100)
    @given(signals=_clip_signals, threshold=_threshold, minimum_views=_min_views)
    def test_clips_identified_are_exactly_below_threshold_and_above_min_views(
        self, signals, threshold, minimum_views
    ):
        result = identify_clips_to_reseed(signals, threshold, minimum_views)

        # One item per qualifying clip, targeting that clip's parent topic.
        expected_parents = [
            s.topic_slug
            for s in signals
            if s.views >= minimum_views and s.watch_quality < threshold
        ]
        assert [item.topic for item in result] == expected_parents
        assert all(item.status == "pending" for item in result)

    @settings(max_examples=100)
    @given(signals=_clip_signals, threshold=_threshold, minimum_views=_min_views)
    def test_clip_identification_is_deterministic(self, signals, threshold, minimum_views):
        a = identify_clips_to_reseed(signals, threshold, minimum_views)
        b = identify_clips_to_reseed(signals, threshold, minimum_views)
        assert a == b
