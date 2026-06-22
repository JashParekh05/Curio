"""Property-based test that an unsupported Slice_Dimension produces no rollup.

# Feature: engagement-telemetry, Property 8: Unsupported slice dimension produces no rollup

For any dimension string that is not one of the five supported Slice_Dimensions
(``user_id``, ``content_level``, ``pedagogical_role``, ``source_platform``,
``feed_surface``) nor the virtual ``seeding_origin`` dimension, and for any set of
Impressions, ``telemetry.compute_rollup`` rejects the request by raising
``ValueError`` and produces no Engagement_Rollup.

Validates: Requirements 4.4, 4.5
"""
from datetime import datetime, timezone

import pytest
from hypothesis import assume, given, settings, strategies as st

from app.models.schemas import Impression
from app.services import telemetry

# Every dimension string compute_rollup accepts: the five stored Slice_Dimensions
# plus the virtual seeding_origin dimension. Anything else must be rejected.
_SUPPORTED = telemetry.SLICE_DIMENSIONS | {telemetry.SEEDING_ORIGIN_DIMENSION}

_served_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()


@st.composite
def _impressions(draw):
    n = draw(st.integers(min_value=0, max_value=4))
    return [
        Impression(
            id=f"imp-{i}",
            clip_id=draw(st.sampled_from(["c1", "c2"])),
            session_id=draw(st.sampled_from(["s1", None])),
            feed_surface=draw(st.sampled_from(sorted(telemetry.FEED_SURFACES))),
            feed_position=i,
            served_at=_served_at,
        )
        for i in range(n)
    ]


class TestUnsupportedDimension:
    @settings(max_examples=100)
    @given(
        dimension=st.text(min_size=0, max_size=20),
        impressions=_impressions(),
    )
    def test_unsupported_dimension_raises_valueerror(self, dimension, impressions):
        # Only exercise dimensions that are genuinely unsupported (Req 4.4).
        assume(dimension not in _SUPPORTED)
        # No Engagement_Rollup is produced -- the request is rejected (Req 4.5).
        with pytest.raises(ValueError):
            telemetry.compute_rollup(impressions, [], dimension)
