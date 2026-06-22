"""Property-based test for Coverage_View entries per topic.

# Feature: content-retrieval-revamp, Property 12: One entry per non-pruned topic, none for pruned topics

*For any* list of per-Topic projections, ``coverage_view`` produces exactly one
Catalog_Entry for each non-pruned projection and zero entries for each
Pruned_Topic, derives every entry solely from the supplied projection (no DB
mutation), and is deterministic: identical projections always yield identical
entries (Req 4.1, 4.2, 4.5, 4.8).

Imports only the pure module under test (``coverage_view``) and the pure
Canonical_Arc types it operates over, so it runs offline with no external
service.

Validates: Requirements 4.1, 4.2, 4.5, 4.8
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.coverage_view import (
    TopicProjection,
    catalog_entry,
    coverage_view,
)

_PEDAGOGICAL_ROLES = [
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_slug = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)
# (watch_ms, duration_seconds) pairs; duration kept positive so ratios are defined.
_watch_event = st.tuples(
    st.integers(min_value=0, max_value=600_000),
    st.integers(min_value=1, max_value=600),
)


@st.composite
def _arc(draw, topic_slug):
    """A Canonical_Arc with consecutive-from-1 ordinals, or None."""
    if draw(st.booleans()):
        return None
    n = draw(st.integers(min_value=1, max_value=5))
    roles = draw(st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n))
    arc_roles = tuple(
        CanonicalArcRole(role=role, ordinal=i) for i, role in enumerate(roles, start=1)
    )
    return CanonicalArc(topic_slug=topic_slug, roles=arc_roles)


@st.composite
def _projection(draw):
    slug = draw(_slug)
    arc = draw(_arc(slug))
    ordinals = [r.ordinal for r in arc.roles] if arc is not None else []
    clips_per_role = {
        o: draw(st.integers(min_value=0, max_value=5)) for o in ordinals
    }
    watch_events = draw(st.lists(_watch_event, max_size=8))
    is_pruned = draw(st.booleans())
    return TopicProjection(
        topic_slug=slug,
        arc=arc,
        clips_per_role=clips_per_role,
        watch_events=watch_events,
        is_pruned=is_pruned,
    )


_projections = st.lists(_projection(), max_size=25)


class TestCoverageEntriesPerTopic:
    @settings(max_examples=100)
    @given(projections=_projections)
    def test_one_entry_per_non_pruned_none_for_pruned(self, projections):
        entries = coverage_view(projections)

        non_pruned = [p for p in projections if not p.is_pruned]
        pruned_slugs = {p.topic_slug for p in projections if p.is_pruned}

        # Exactly one entry per non-pruned projection, in input order.
        assert len(entries) == len(non_pruned)
        assert [e.topic_slug for e in entries] == [p.topic_slug for p in non_pruned]

        # A purely-pruned slug yields no entry (unless a non-pruned projection
        # shares the slug).
        non_pruned_slugs = {p.topic_slug for p in non_pruned}
        for slug in pruned_slugs:
            if slug not in non_pruned_slugs:
                assert all(e.topic_slug != slug for e in entries)

    @settings(max_examples=100)
    @given(projections=_projections)
    def test_pruned_projection_yields_no_entry(self, projections):
        for p in projections:
            if p.is_pruned:
                assert catalog_entry(p) is None
            else:
                assert catalog_entry(p) is not None

    @settings(max_examples=100)
    @given(projections=_projections)
    def test_deterministic(self, projections):
        assert coverage_view(projections) == coverage_view(projections)
