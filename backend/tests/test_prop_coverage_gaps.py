"""Property-based test for Coverage_Gaps and Arc_Complete.

# Feature: content-retrieval-revamp, Property 13: Coverage gaps and Arc_Complete agree with per-role counts

*For any* Canonical_Arc and any per-role Admitted_Clip counts (including zeros),
the Catalog_Entry's Coverage_Gaps are exactly the role ordinals whose count is
zero, and the Topic is reported Arc_Complete if and only if there are no gaps
(Req 4.3, 4.4).

Imports only the pure module under test (``coverage_view``) and the pure
Canonical_Arc types it operates over, so it runs offline with no external
service.

Validates: Requirements 4.3, 4.4
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.coverage_view import TopicProjection, catalog_entry

_PEDAGOGICAL_ROLES = [
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_slug = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)


@st.composite
def _arc_with_counts(draw):
    """A non-empty Canonical_Arc plus a per-ordinal clip count (some zero)."""
    slug = draw(_slug)
    n = draw(st.integers(min_value=1, max_value=6))
    roles = draw(st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n))
    arc_roles = tuple(
        CanonicalArcRole(role=role, ordinal=i) for i, role in enumerate(roles, start=1)
    )
    arc = CanonicalArc(topic_slug=slug, roles=arc_roles)
    # Counts include zeros so the gap boundary is exercised from both sides.
    counts = {
        i: draw(st.integers(min_value=0, max_value=4)) for i in range(1, n + 1)
    }
    return slug, arc, counts


class TestCoverageGapsAndArcComplete:
    @settings(max_examples=100)
    @given(data=_arc_with_counts(), watch_events=st.just([]))
    def test_gaps_are_exactly_zero_count_ordinals(self, data, watch_events):
        slug, arc, counts = data
        projection = TopicProjection(
            topic_slug=slug,
            arc=arc,
            clips_per_role=counts,
            watch_events=watch_events,
            is_pruned=False,
        )
        entry = catalog_entry(projection)
        assert entry is not None

        expected_gaps = tuple(
            sorted(o for o, c in counts.items() if c == 0)
        )
        assert tuple(sorted(entry.coverage_gaps)) == expected_gaps

    @settings(max_examples=100)
    @given(data=_arc_with_counts())
    def test_arc_complete_iff_no_gaps(self, data):
        slug, arc, counts = data
        projection = TopicProjection(
            topic_slug=slug,
            arc=arc,
            clips_per_role=counts,
            watch_events=[],
            is_pruned=False,
        )
        entry = catalog_entry(projection)
        assert entry is not None
        assert entry.arc_complete == (len(entry.coverage_gaps) == 0)

    @settings(max_examples=100)
    @given(data=_arc_with_counts())
    def test_missing_ordinal_is_a_gap(self, data):
        """A role ordinal absent from the projection map counts as zero clips."""
        slug, arc, counts = data
        # Drop one ordinal from the map entirely; it must still surface as a gap.
        dropped = arc.roles[0].ordinal
        partial = {o: c for o, c in counts.items() if o != dropped}
        projection = TopicProjection(
            topic_slug=slug,
            arc=arc,
            clips_per_role=partial,
            watch_events=[],
            is_pruned=False,
        )
        entry = catalog_entry(projection)
        assert entry is not None
        assert dropped in entry.coverage_gaps
        assert entry.clips_per_role[dropped] == 0
