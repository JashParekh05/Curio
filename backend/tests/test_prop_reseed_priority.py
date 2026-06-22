"""Property-based test for Reseed_Priority bound and monotonicity.

# Feature: content-retrieval-revamp, Property 27: Reseed_Priority is bounded and monotonic

*For any* Watch_Quality / view-count pair, the Reseed_Priority lies in the
inclusive range [0.0, 1.0] and is a monotonic non-decreasing function of both the
Watch_Quality deficit (Recut_Threshold minus Watch_Quality) and the view count:
holding one fixed and increasing the other never lowers the priority (Req 7.3).

Imports only the pure module under test (``reseed_prioritizer``), so it runs
offline with no external service.

Validates: Requirements 7.3
"""
from hypothesis import given, settings, strategies as st

from app.services.reseed_prioritizer import reseed_priority

_quality = st.floats(min_value=0.0, max_value=1.0)
_views = st.integers(min_value=0, max_value=10_000)
_threshold = st.floats(min_value=0.0, max_value=1.0)


class TestReseedPriorityBound:
    @settings(max_examples=100)
    @given(watch_quality=_quality, views=_views, threshold=_threshold)
    def test_priority_is_bounded_to_unit_interval(self, watch_quality, views, threshold):
        p = reseed_priority(watch_quality, views, threshold)
        assert 0.0 <= p <= 1.0


class TestReseedPriorityMonotonicity:
    @settings(max_examples=100)
    @given(
        watch_quality=_quality,
        views=_views,
        extra_views=st.integers(min_value=0, max_value=10_000),
        threshold=_threshold,
    )
    def test_non_decreasing_in_views(self, watch_quality, views, extra_views, threshold):
        # More views (a more confident, more impactful signal) never lowers the
        # priority when Watch_Quality and threshold are held fixed.
        low = reseed_priority(watch_quality, views, threshold)
        high = reseed_priority(watch_quality, views + extra_views, threshold)
        assert high >= low

    @settings(max_examples=100)
    @given(
        watch_quality=_quality,
        drop=st.floats(min_value=0.0, max_value=1.0),
        views=_views,
        threshold=_threshold,
    )
    def test_non_decreasing_in_deficit(self, watch_quality, drop, views, threshold):
        # A larger deficit means a worse Watch_Quality. Lowering Watch_Quality (by
        # ``drop``) increases the deficit and must never lower the priority when
        # views and threshold are held fixed.
        worse_quality = max(0.0, watch_quality - drop)
        higher_deficit = reseed_priority(worse_quality, views, threshold)
        lower_deficit = reseed_priority(watch_quality, views, threshold)
        assert higher_deficit >= lower_deficit

    @settings(max_examples=100)
    @given(
        watch_quality=_quality,
        drop=st.floats(min_value=0.0, max_value=1.0),
        views=_views,
        extra_views=st.integers(min_value=0, max_value=10_000),
        threshold=_threshold,
    )
    def test_non_decreasing_in_both_jointly(
        self, watch_quality, drop, views, extra_views, threshold
    ):
        # Increasing both the deficit and the view count together never lowers
        # the priority.
        worse_quality = max(0.0, watch_quality - drop)
        base = reseed_priority(watch_quality, views, threshold)
        both = reseed_priority(worse_quality, views + extra_views, threshold)
        assert both >= base
