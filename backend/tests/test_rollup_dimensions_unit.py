"""Unit tests for seeding-origin classification examples and the five Slice_Dimensions.

Concrete examples for ``telemetry.classify_seeding_origin`` (cold_start_seeded,
on_demand, and the unknown fallback), and a check that each of the five supported
Slice_Dimensions (User, content_level, pedagogical_role, source_platform,
Feed_Surface) produces an Engagement_Rollup.

Validates: Requirements 4.4, 4.10
"""
from datetime import datetime, timezone

from app.models.schemas import Impression
from app.services import telemetry

_served_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()


class TestClassifySeedingOriginExamples:
    def test_cold_start_seeded_via_canonical_marker(self):
        assert telemetry.classify_seeding_origin({"seeding_origin": "cold_start_seeded"}) == "cold_start_seeded"

    def test_cold_start_seeded_via_boolean_markers(self):
        assert telemetry.classify_seeding_origin({"cold_start_seeded": True}) == "cold_start_seeded"
        assert telemetry.classify_seeding_origin({"is_cold_start_seeded": True}) == "cold_start_seeded"

    def test_on_demand_via_canonical_marker(self):
        assert telemetry.classify_seeding_origin({"seeding_origin": "on_demand"}) == "on_demand"

    def test_on_demand_via_boolean_markers(self):
        assert telemetry.classify_seeding_origin({"on_demand": True}) == "on_demand"
        assert telemetry.classify_seeding_origin({"is_on_demand": True}) == "on_demand"

    def test_unknown_fallback_when_not_determinable(self):
        # Empty metadata, unrelated keys, an unrecognized marker value, and a
        # non-dict input all fall back to "unknown" (Req 4.10).
        assert telemetry.classify_seeding_origin({}) == "unknown"
        assert telemetry.classify_seeding_origin({"pedagogical_role": "intro"}) == "unknown"
        assert telemetry.classify_seeding_origin({"seeding_origin": "something_else"}) == "unknown"
        assert telemetry.classify_seeding_origin({"cold_start_seeded": False}) == "unknown"
        assert telemetry.classify_seeding_origin(None) == "unknown"


def _sample_impressions():
    """Two Impressions carrying a value for each of the five Slice_Dimensions."""
    return [
        Impression(
            id="imp-0",
            clip_id="c0",
            session_id="s1",
            user_id="u1",
            feed_surface="discover",
            feed_position=0,
            pedagogical_role="intro",
            content_level="beginner",
            source_platform="youtube",
            topic_slug="algebra",
            served_at=_served_at,
        ),
        Impression(
            id="imp-1",
            clip_id="c1",
            session_id="s1",
            user_id="u2",
            feed_surface="learn_path",
            feed_position=1,
            pedagogical_role="practice",
            content_level="advanced",
            source_platform="vimeo",
            topic_slug="algebra",
            served_at=_served_at,
        ),
    ]


class TestFiveSupportedDimensionsProduceRollup:
    def test_each_supported_dimension_produces_a_rollup(self):
        impressions = _sample_impressions()
        for dimension in ["user_id", "content_level", "pedagogical_role", "source_platform", "feed_surface"]:
            groups = telemetry.compute_rollup(impressions, [], dimension)
            # A rollup is produced: the two distinct values per dimension yield two
            # groups, and the counts conserve (Req 4.4).
            assert len(groups) == 2, f"dimension {dimension!r} should produce 2 groups"
            assert sum(g.count for g in groups) == len(impressions)
            assert {g.key for g in groups} == {
                getattr(impressions[0], dimension),
                getattr(impressions[1], dimension),
            }
