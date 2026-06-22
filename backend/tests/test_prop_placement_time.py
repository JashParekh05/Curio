"""Property-based test for the Placement remaining-time estimate.

# Feature: content-retrieval-revamp, Property 32: Remaining time is the sum of durations at or after the entry

*For any* placement onto a non-empty Learning_Path, once an entry point is
selected the estimated time to complete the path equals the sum of the estimated
durations (``est_minutes``) of the Spine_Nodes ordered at or after the entry,
expressed as a whole number of minutes greater than or equal to 0 (Req 9.3).

Imports only the pure module under test (``placement``), so it runs offline with
no external service.

Validates: Requirements 9.3
"""
from hypothesis import given, settings, strategies as st

from app.services.placement import (
    VALID_LEVELS,
    SpineEdge,
    SpineNode,
    place,
)

_slug_alpha = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)
_level = st.one_of(st.none(), st.sampled_from(VALID_LEVELS))


@st.composite
def _path_and_edges(draw):
    """A non-empty path of distinct-slug nodes plus prerequisite edges among them."""
    slugs = draw(st.lists(_slug_alpha, min_size=1, max_size=6, unique=True))
    nodes = [
        SpineNode(
            topic_slug=slug,
            content_level=draw(st.sampled_from(VALID_LEVELS)),
            est_minutes=draw(st.integers(min_value=0, max_value=240)),
        )
        for slug in slugs
    ]
    edges = []
    if len(slugs) >= 2:
        edge_pairs = draw(
            st.lists(
                st.tuples(st.sampled_from(slugs), st.sampled_from(slugs)),
                min_size=0,
                max_size=6,
            )
        )
        edges = [
            SpineEdge(prerequisite=a, dependent=b) for a, b in edge_pairs if a != b
        ]
    return nodes, edges


class TestRemainingTimeEstimate:
    @settings(max_examples=200)
    @given(data=_path_and_edges(), level=_level)
    def test_estimate_is_sum_of_durations_at_or_after_entry(self, data, level):
        path, edges = data
        result = place(path, edges, level)

        assert result.entry_index is not None
        expected = sum(node.est_minutes for node in path[result.entry_index:])

        # Estimate equals the sum of durations at or after the entry (Req 9.3).
        assert result.est_minutes == expected
        # A whole number of minutes >= 0 (Req 9.3).
        assert isinstance(result.est_minutes, int)
        assert result.est_minutes >= 0
