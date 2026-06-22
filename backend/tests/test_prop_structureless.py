"""Property-based test for the structureless result.

# Feature: content-retrieval-revamp, Property 7: Neither model yields a structureless result

Validates: Requirements 1.7

A Topic with neither a Role_Arc_Model nor a Legacy_Section_Model produces no
Canonical_Arc and is recorded as a Structureless_Topic.
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_unifier import unify_arc

_topic_slug = st.text(min_size=0, max_size=30)

# Absence is represented as either None or an empty collection.
_absent_role_model = st.sampled_from([None, []])
_absent_sections = st.sampled_from([None, []])


class TestStructureless:
    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        role_model=_absent_role_model,
        section_indices=_absent_sections,
    )
    def test_neither_model_is_structureless(self, topic_slug, role_model, section_indices):
        result = unify_arc(topic_slug, role_model, section_indices)

        assert result.arc is None
        assert result.structureless is True
        assert result.rejection is None
        assert result.legacy_superseded is False
        assert result.topic_slug == topic_slug
