"""Property-based test for Section_Planner template selection.

# Feature: content-hook-engagement, Property 1: Template selection is total and deterministic

Property 1: Template selection is total and deterministic — for any supported
ConceptType, ``select_template`` returns exactly one template, and the same
input always yields the same template.

Validates: Requirements 1.2
"""
from hypothesis import given, settings, strategies as st

from app.agents.section_planner import (
    CONCEPTUAL_TEMPLATE,
    DEFAULT_TEMPLATE,
    PROBLEM_SOLVING_TEMPLATE,
    select_template,
)

# The supported ConceptType values plus arbitrary strings to confirm totality
# (any unsupported value falls through to DEFAULT_TEMPLATE).
SUPPORTED_CONCEPT_TYPES = ["problem_solving", "conceptual", "default"]

_EXPECTED = {
    "problem_solving": PROBLEM_SOLVING_TEMPLATE,
    "conceptual": CONCEPTUAL_TEMPLATE,
    "default": DEFAULT_TEMPLATE,
}


# Generate both the supported ConceptType values and random strings so the
# property exercises the total/default behavior across the whole input space.
concept_type_strategy = st.one_of(
    st.sampled_from(SUPPORTED_CONCEPT_TYPES),
    st.text(),
)


class TestTemplateSelectionProperties:
    @settings(max_examples=200)
    @given(concept_type=concept_type_strategy)
    def test_selection_is_total_and_returns_nonempty_list(self, concept_type):
        # Total: every input (supported or arbitrary string) yields exactly one
        # template that is a non-empty list of roles.
        template = select_template(concept_type)
        assert isinstance(template, list)
        assert len(template) >= 1

    @settings(max_examples=200)
    @given(concept_type=concept_type_strategy)
    def test_selection_is_deterministic(self, concept_type):
        # Determinism: repeated calls with the same input return identical results.
        first = select_template(concept_type)
        second = select_template(concept_type)
        assert first == second
        assert first is second  # same constant object, no per-call construction

    @settings(max_examples=100)
    @given(concept_type=st.sampled_from(SUPPORTED_CONCEPT_TYPES))
    def test_supported_types_map_to_their_template(self, concept_type):
        # Each supported ConceptType selects exactly its associated template.
        assert select_template(concept_type) == _EXPECTED[concept_type]

    @settings(max_examples=200)
    @given(unsupported=st.text())
    def test_unsupported_values_fall_through_to_default(self, unsupported):
        # Totality for arbitrary strings: anything outside the supported set
        # maps to the DEFAULT_TEMPLATE (still a non-empty single template).
        if unsupported not in SUPPORTED_CONCEPT_TYPES:
            assert select_template(unsupported) == DEFAULT_TEMPLATE
