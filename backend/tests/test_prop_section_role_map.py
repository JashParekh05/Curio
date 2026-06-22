"""Property-based test for Section_Role_Map totality.

# Feature: content-retrieval-revamp, Property 4: Section_Role_Map is total and deterministic over 0..3

Validates: Requirements 1.4

``section_role`` is total over the inclusive range 0..3: every index maps to a
valid Pedagogical_Role, and the same index always returns the same role.
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import PedagogicalRole
from app.services.arc_unifier import section_role

_VALID_ROLES = set(PedagogicalRole.__args__)

_in_range = st.integers(min_value=0, max_value=3)


class TestSectionRoleMap:
    @settings(max_examples=100)
    @given(section_index=_in_range)
    def test_total_and_deterministic_over_range(self, section_index):
        role = section_role(section_index)
        assert role is not None
        assert role in _VALID_ROLES
        # Deterministic: repeated lookups return the same role.
        assert section_role(section_index) == role

    def test_every_index_in_range_is_mapped(self):
        # Exhaustive totality check over 0..3.
        for i in range(0, 4):
            assert section_role(i) in _VALID_ROLES
