"""Property-based test for Placement entry-point selection.

# Feature: content-retrieval-revamp, Property 31: Entry point is level-appropriate and prerequisite-consistent

*For any* non-empty Learning_Path and valid learner level, ``place`` selects exactly
one entry index: the earliest Spine_Node whose Content_Level is at or above the
learner's level (ordering beginner < intermediate < advanced), or the last node when
no node reaches that level, so lower-level material the learner already knows is
skipped (Req 9.1, 9.2). Because the path is prerequisite-ordered the entry is
prerequisite-consistent. The empty-path and absent/invalid-level edge cases are
covered too: an empty path is "not possible" and an absent or out-of-range level
places the learner at the first node (Req 9.4, 9.5). Placement is deterministic:
identical inputs yield identical placements (Req 9.6).

Imports only the pure module under test (``placement``), so it runs offline with
no external service.

Validates: Requirements 9.1, 9.2, 9.6
"""
from hypothesis import given, settings, strategies as st

from app.services.placement import (
    VALID_LEVELS,
    PlacementResult,
    SpineEdge,
    SpineNode,
    place,
)

_slug_alpha = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)
_valid_level = st.sampled_from(VALID_LEVELS)

_LEVEL_RANK = {level: rank for rank, level in enumerate(VALID_LEVELS)}


def _expected_entry(path, learner_level):
    """Reference: earliest index whose Content_Level rank >= the learner's rank,
    or the last index when no node reaches the learner's level."""
    target = _LEVEL_RANK[learner_level]
    for i, node in enumerate(path):
        if _LEVEL_RANK.get(node.content_level, 0) >= target:
            return i
    return len(path) - 1


@st.composite
def _path_and_edges(draw):
    """A non-empty path of distinct-slug nodes plus prerequisite edges among them.

    Node Content_Levels are drawn across all valid bands so the level-based entry
    selection is exercised at every learner level, including paths where some,
    all, or none of the nodes reach a given level.
    """
    slugs = draw(st.lists(_slug_alpha, min_size=1, max_size=6, unique=True))
    nodes = [
        SpineNode(
            topic_slug=slug,
            content_level=draw(st.sampled_from(VALID_LEVELS)),
            est_minutes=draw(st.integers(min_value=0, max_value=120)),
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


class TestEntryPointSelection:
    @settings(max_examples=200)
    @given(data=_path_and_edges(), level=_valid_level)
    def test_entry_is_earliest_node_at_or_above_level(self, data, level):
        path, edges = data
        result = place(path, edges, level)

        # Exactly one entry point for a non-empty path (Req 9.1).
        assert isinstance(result, PlacementResult)
        assert result.not_possible is False
        assert result.entry_index is not None
        assert 0 <= result.entry_index < len(path)

        # The entry is the earliest node at or above the learner's level, or the
        # last node when none reaches it (Req 9.2).
        assert result.entry_index == _expected_entry(path, level)

        # Level-appropriate: every node skipped before the entry is strictly below
        # the learner's level (material the learner already knows), unless the
        # learner already knows everything (entry forced to the last node).
        target = _LEVEL_RANK[level]
        reached = any(_LEVEL_RANK.get(n.content_level, 0) >= target for n in path)
        if reached:
            for node in path[: result.entry_index]:
                assert _LEVEL_RANK.get(node.content_level, 0) < target
            assert _LEVEL_RANK.get(path[result.entry_index].content_level, 0) >= target
        else:
            assert result.entry_index == len(path) - 1

    @settings(max_examples=100)
    @given(data=_path_and_edges(), level=_valid_level)
    def test_deterministic_for_identical_inputs(self, data, level):
        path, edges = data
        first = place(path, edges, level)
        second = place(path, edges, level)
        assert first == second

    @settings(max_examples=100)
    @given(
        data=_path_and_edges(),
        level=st.one_of(st.none(), st.text(max_size=12).filter(lambda s: s not in VALID_LEVELS)),
    )
    def test_absent_or_invalid_level_places_at_first_node(self, data, level):
        path, edges = data
        result = place(path, edges, level)
        # Absent or out-of-range level -> first node (Req 9.4).
        assert result.not_possible is False
        assert result.entry_index == 0

    @settings(max_examples=50)
    @given(level=st.one_of(st.none(), _valid_level))
    def test_empty_path_is_not_possible(self, level):
        result = place([], [], level)
        # Zero Spine_Nodes -> no entry, not-possible indication (Req 9.5).
        assert result.entry_index is None
        assert result.not_possible is True
        assert result.est_minutes == 0
