"""Property-based test for clip-to-parent-topic reseed enqueue.

# Feature: content-retrieval-revamp, Property 28: A low-quality clip enqueues its parent topic

*For any* identified low-Watch_Quality Clip, the Reseed_Prioritizer produces a
Backlog_Item that targets the Clip's **parent Topic** slug -- never the Clip id --
so the re-cut is routed through the Topic the Clip belongs to (Req 7.7).

Imports only the pure module under test (``reseed_prioritizer``), so it runs
offline with no external service.

Validates: Requirements 7.7
"""
from hypothesis import given, settings, strategies as st

from app.services.reseed_prioritizer import (
    DEFAULT_MINIMUM_VIEWS,
    DEFAULT_RECUT_THRESHOLD,
    ClipQualitySignal,
    identify_clips_to_reseed,
)

# Distinct alphabets for clip ids and topic slugs so a parent-topic target can
# never be mistaken for the clip id by accident.
_clip_id = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=109), min_size=1, max_size=8
)
_topic_slug = st.text(
    alphabet=st.characters(min_codepoint=110, max_codepoint=122), min_size=1, max_size=8
)

# Build clips that are guaranteed to be identified: Watch_Quality strictly below
# the default Recut_Threshold and views at/above the default Minimum_Views.
_low_quality = st.floats(min_value=0.0, max_value=DEFAULT_RECUT_THRESHOLD - 0.01)
_enough_views = st.integers(min_value=DEFAULT_MINIMUM_VIEWS, max_value=500)

_identified_clip = st.builds(
    ClipQualitySignal,
    clip_id=_clip_id,
    topic_slug=_topic_slug,
    watch_quality=_low_quality,
    views=_enough_views,
)


class TestClipEnqueuesParentTopic:
    @settings(max_examples=100)
    @given(signals=st.lists(_identified_clip, min_size=1, max_size=30))
    def test_each_backlog_item_targets_the_parent_topic(self, signals):
        result = identify_clips_to_reseed(signals)

        # Every identified clip yields exactly one item, in order, and each item
        # targets the clip's parent Topic slug -- not the clip id.
        assert len(result) == len(signals)
        for sig, item in zip(signals, result):
            # The item targets the parent Topic, and (by disjoint alphabets) is
            # never the clip id.
            assert item.topic == sig.topic_slug
            assert item.topic != sig.clip_id

        # The full multiset of targeted topics equals the parents of the clips.
        assert sorted(item.topic for item in result) == sorted(
            sig.topic_slug for sig in signals
        )
