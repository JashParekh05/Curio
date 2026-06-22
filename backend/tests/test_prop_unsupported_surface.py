"""Property-based test for unsupported feed surface.

# Feature: engagement-telemetry, Property 4: Unsupported feed surface records no impression

For any ``feed_surface`` string that is not one of ``{discover, learn_path}`` and
any clip list, ``telemetry.build_impressions`` rejects the serve (raises
``ValueError``) and yields no Impression.

Validates: Requirements 1.8
"""
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services import telemetry

# Any text that is NOT one of the accepted Feed_Surfaces.
_bad_surface = st.text(max_size=20).filter(lambda s: s not in telemetry.FEED_SURFACES)

_served_at = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2035, 1, 1),
    timezones=st.just(timezone.utc),
)


def _make_clip(i: int) -> Clip:
    return Clip(
        id=f"clip-{i}",
        topic_slug="binary-search",
        title=f"Clip {i}",
        video_url="https://example.com/v",
    )


class TestUnsupportedFeedSurface:
    @settings(max_examples=100)
    @given(
        feed_surface=_bad_surface,
        n_clips=st.integers(min_value=0, max_value=8),
        session_id=st.one_of(st.none(), st.text(min_size=1, max_size=12)),
        user_id=st.one_of(st.none(), st.text(min_size=1, max_size=12)),
        served_at=_served_at,
    )
    def test_unsupported_surface_raises_and_records_nothing(
        self, feed_surface, n_clips, session_id, user_id, served_at
    ):
        clips = [_make_clip(i) for i in range(n_clips)]

        # The serve is rejected: build_impressions raises ValueError so the caller
        # records nothing for that serve (Req 1.8). No Impression is produced —
        # the exception means there is no return value at all.
        with pytest.raises(ValueError):
            telemetry.build_impressions(
                clips,
                feed_surface=feed_surface,
                session_id=session_id,
                user_id=user_id,
                served_at=served_at,
            )
