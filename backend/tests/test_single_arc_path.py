"""Example test: a Topic with a Canonical_Arc is ordered by the single arc path.

Validates: Requirements 2.3

A Topic that has a Canonical_Arc is ordered exclusively through the one
``order_clips_by_arc`` core; the legacy ``section_index`` / ``narrative_rank``
ordering branch is never taken. These examples construct cases where the
removed legacy ordering would disagree with the arc ordering and assert the arc
ordering wins.
"""
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.clip_ordering import order_clips_by_arc
from app.services.feed_retrieval import _order_by_arc
from tests.conftest import make_clip


def _arc() -> CanonicalArc:
    # definition (ordinal 1) precedes example (ordinal 2) in the arc.
    return CanonicalArc(
        topic_slug="binary-search",
        roles=(
            CanonicalArcRole(role="definition", ordinal=1),
            CanonicalArcRole(role="example", ordinal=2),
        ),
    )


class TestSingleArcPath:
    def test_orders_by_arc_not_section_index(self):
        """Legacy section_index ordering is ignored; arc ordinal decides."""
        arc = _arc()
        # The "definition" clip carries a HIGH section_index (3) and the
        # "example" clip a LOW section_index (0). The removed legacy branch
        # would order by section_index -> [example, definition]; the arc path
        # orders by role ordinal -> [definition, example].
        definition = make_clip(id="d", section_index=3, pedagogical_role="definition")
        example = make_clip(id="e", section_index=0, pedagogical_role="example")
        definition.final_score = 0.1
        example.final_score = 0.9

        out = _order_by_arc([example, definition], arc)
        assert [c.id for c in out] == ["d", "e"]

    def test_orders_by_arc_not_narrative_rank(self):
        """Legacy narrative_rank ordering is ignored; arc ordinal decides."""
        arc = _arc()
        # narrative_rank would put the example first; the arc path does not.
        definition = make_clip(id="d", narrative_rank=5, pedagogical_role="definition")
        example = make_clip(id="e", narrative_rank=0, pedagogical_role="example")

        out = _order_by_arc([example, definition], arc)
        assert [c.id for c in out] == ["d", "e"]

    def test_feed_path_delegates_to_single_core(self):
        """_order_by_arc output matches the pure core exactly for one source."""
        arc = _arc()
        # Distinct sources so source-spread does not reorder within a role,
        # letting us assert equality with the pure core's output directly.
        c1 = make_clip(id="a", pedagogical_role="definition", source_url="s1")
        c2 = make_clip(id="b", pedagogical_role="definition", source_url="s2")
        c3 = make_clip(id="c", pedagogical_role="example", source_url="s3")
        for c, s in ((c1, 0.5), (c2, 0.9), (c3, 0.7)):
            c.final_score = s
        clips = [c3, c1, c2]

        out = _order_by_arc(clips, arc)
        core = order_clips_by_arc(clips, arc)
        assert [c.id for c in out] == [c.id for c in core]
        # definition (ord 1) before example (ord 2); within definition, score
        # desc -> b (0.9) before a (0.5).
        assert [c.id for c in out] == ["b", "a", "c"]

    def test_role_less_clip_sinks_after_arc_clips(self):
        """A clip whose role is absent from the arc sorts after arc clips."""
        arc = _arc()
        arc_clip = make_clip(id="d", pedagogical_role="definition")
        # "mechanism" is not in this arc -> role-less, sinks to the end even
        # with a higher score.
        off_arc = make_clip(id="m", pedagogical_role="mechanism")
        arc_clip.final_score = 0.1
        off_arc.final_score = 0.99

        out = _order_by_arc([off_arc, arc_clip], arc)
        assert [c.id for c in out] == ["d", "m"]
