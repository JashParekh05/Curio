"""Property-based test for Seeding_Origin classification totality and rollup grouping.

# Feature: engagement-telemetry, Property 9: Seeding-origin classification is total over the three labels

For any clip metadata, ``telemetry.classify_seeding_origin`` returns exactly one of
``cold_start_seeded``, ``on_demand``, or ``unknown`` (returning ``unknown`` whenever
the origin is not determinable from the metadata present); and a Seeding_Origin
Engagement_Rollup groups Impressions into exactly the label values that occur.

Validates: Requirements 4.10
"""
from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st

from app.models.schemas import Impression
from app.services import telemetry

_LABELS = {"cold_start_seeded", "on_demand", "unknown"}

# Arbitrary metadata dicts: a mix of the marker keys the classifier honors (with
# varied value types so determinate and indeterminate cases both occur) and
# unrelated noise keys.
_marker_values = st.one_of(
    st.none(),
    st.booleans(),
    st.sampled_from(["cold_start_seeded", "on_demand", "other", ""]),
    st.integers(),
)
_metadata = st.dictionaries(
    keys=st.sampled_from(
        [
            "seeding_origin",
            "cold_start_seeded",
            "is_cold_start_seeded",
            "on_demand",
            "is_on_demand",
            "pedagogical_role",
            "content_level",
            "noise",
        ]
    ),
    values=_marker_values,
    max_size=6,
)

_served_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()


class TestSeedingOrigin:
    @settings(max_examples=100)
    @given(metadata=_metadata)
    def test_classification_is_total_over_three_labels(self, metadata):
        # Always exactly one of the three labels (Req 4.10).
        assert telemetry.classify_seeding_origin(metadata) in _LABELS

    @settings(max_examples=100)
    @given(non_dict=st.one_of(st.none(), st.text(), st.integers(), st.lists(st.integers())))
    def test_non_dict_metadata_is_unknown(self, non_dict):
        # Indeterminate (non-dict) input falls back to "unknown" (Req 4.10).
        assert telemetry.classify_seeding_origin(non_dict) == "unknown"

    @settings(max_examples=100)
    @given(origins=st.lists(st.sampled_from(sorted(_LABELS)), max_size=8))
    def test_rollup_groups_only_into_labels_that_occur(self, origins):
        # One Impression per chosen origin; a custom seeding_origin_of assigns the
        # drawn label to each Impression by id (the default classifier over today's
        # Served_Context would resolve every Impression to "unknown").
        impressions = [
            Impression(
                id=f"imp-{i}",
                clip_id=f"c{i}",
                session_id="s1",
                feed_surface="discover",
                feed_position=i,
                served_at=_served_at,
            )
            for i in range(len(origins))
        ]
        origin_by_id = {imp.id: origins[i] for i, imp in enumerate(impressions)}

        groups = telemetry.compute_rollup(
            impressions,
            [],
            telemetry.SEEDING_ORIGIN_DIMENSION,
            seeding_origin_of=lambda imp: origin_by_id[imp.id],
        )

        keys = {g.key for g in groups}
        # Every group key is one of the three labels, and the groups are exactly
        # the labels that occur -- no spurious or missing groups (Req 4.10).
        assert keys <= _LABELS
        assert keys == set(origins)
        # Count conservation across the seeding-origin groups.
        assert sum(g.count for g in groups) == len(impressions)
