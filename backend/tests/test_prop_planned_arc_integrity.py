"""Property-based test for planned-arc integrity.

# Feature: content-hook-engagement, Property 2: Planned_Arc preserves the template exactly with consecutive ordinals

Validates: Requirements 1.3, 1.6, 1.8

`build_planned_arc` must instantiate a PlannedArc whose role sequence equals the
input template exactly — same roles, same order, none added, omitted, or
duplicated — with consecutive ordinals 1..n where ordinal i maps to template
position i-1. An empty template must yield ``template_empty=True`` and
``roles == []``.
"""
from hypothesis import given, settings, strategies as st

from app.agents.section_planner import build_planned_arc
from app.models.schemas import PedagogicalRole

# All supported PedagogicalRole literal values (problem-solving + conceptual arcs).
_PEDAGOGICAL_ROLES: list[PedagogicalRole] = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

# Random templates: include empty lists and lists with duplicates.
_template = st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=0, max_size=20)
_concept_type = st.sampled_from(["problem_solving", "conceptual", "default"])
_topic_slug = st.text(min_size=0, max_size=30)


class TestPlannedArcIntegrity:
    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, concept_type=_concept_type, template=_template)
    def test_arc_preserves_template_with_consecutive_ordinals(
        self, topic_slug, concept_type, template
    ):
        arc = build_planned_arc(
            topic_slug=topic_slug,
            concept_type=concept_type,
            template=template,
        )

        # Role sequence equals the template in order: no added/omitted/duplicated.
        assert [r.role for r in arc.roles] == list(template)

        # Ordinals are exactly 1..n consecutive, position i-1 -> ordinal i.
        assert [r.ordinal for r in arc.roles] == list(range(1, len(template) + 1))
        for i, arc_role in enumerate(arc.roles, start=1):
            assert arc_role.ordinal == i
            assert arc_role.role == template[i - 1]

        # Empty template -> template_empty=True and roles == [].
        if not template:
            assert arc.template_empty is True
            assert arc.roles == []
        else:
            assert arc.template_empty is False
            assert len(arc.roles) == len(template)

        # Metadata passthrough.
        assert arc.topic_slug == topic_slug
        assert arc.concept_type == concept_type

    @settings(max_examples=100)
    @given(template=_template)
    def test_empty_template_signals_template_empty(self, template):
        # Focused check on the empty-template contract (Req 1.8).
        arc = build_planned_arc(
            topic_slug="slug",
            concept_type="default",
            template=template,
        )
        assert arc.template_empty == (len(template) == 0)
        if len(template) == 0:
            assert arc.roles == []
