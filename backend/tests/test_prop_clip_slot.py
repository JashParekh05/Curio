"""Property-based test for the Clip_Slot_Tag pure core.

# Feature: structured-learn-curriculum, Property 2: Single idea maps to a single beat (P1.12-P1.16)

*For any* combination of clip tag inputs, ``clip_slot.build_slot`` places the
clip in exactly one beat iff it has a non-empty ``topic_slug`` and a
``section_index`` in {0,1,2,3} (P1.12), every returned slot satisfies
``is_single_beat`` (P1.13), the resulting ``content_level`` is always in
``LEVELS`` or ``None`` with unknown values normalized to ``None`` (P1.14), any
present ``role_ordinal`` on the result is ``>= 1`` (P1.15), and the core is
total and deterministic -- it never raises on any input combination and
identical inputs always produce identical results (P1.16).

Imports only the pure module under test (``app.services.clip_slot``) plus
``level_filter.LEVELS`` and ``schemas.PedagogicalRole`` to build inputs, so it
runs offline with no external service.

Validates: Requirements 1.3, 1.6
"""
from __future__ import annotations

from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import PedagogicalRole
from app.services.clip_slot import (
    VALID_SECTION_INDICES,
    build_slot,
    is_single_beat,
)
from app.services.level_filter import LEVELS

# ---------------------------------------------------------------------------
# Input strategies
# ---------------------------------------------------------------------------

# topic_slug: arbitrary text including the empty string.
_topic_slug = st.text(max_size=12)

# content_level: recognized LEVELS members + arbitrary text + None (unknown).
_content_level = st.one_of(
    st.sampled_from(LEVELS),
    st.text(max_size=12),
    st.none(),
)

# section_index: ints incl. out-of-range, plus None.
_section_index = st.one_of(
    st.integers(min_value=-5, max_value=10),
    st.none(),
)

# pedagogical_role: a valid PedagogicalRole literal value or None.
_pedagogical_role = st.one_of(
    st.sampled_from(list(get_args(PedagogicalRole))),
    st.none(),
)

# role_ordinal: ints incl. values < 1, plus None.
_role_ordinal = st.one_of(
    st.integers(min_value=-3, max_value=8),
    st.none(),
)


class TestClipSlotTag:
    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        content_level=_content_level,
        section_index=_section_index,
        pedagogical_role=_pedagogical_role,
        role_ordinal=_role_ordinal,
    )
    def test_non_none_iff_valid_slug_and_section(
        self, topic_slug, content_level, section_index, pedagogical_role, role_ordinal
    ):
        # P1.12: non-None iff topic_slug is a non-empty str AND section_index in {0,1,2,3}.
        slot = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        expected_placeable = (
            isinstance(topic_slug, str)
            and topic_slug != ""
            and section_index in VALID_SECTION_INDICES
        )
        assert (slot is not None) == expected_placeable

    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        content_level=_content_level,
        section_index=_section_index,
        pedagogical_role=_pedagogical_role,
        role_ordinal=_role_ordinal,
    )
    def test_returned_slot_is_single_beat(
        self, topic_slug, content_level, section_index, pedagogical_role, role_ordinal
    ):
        # P1.13: every non-None slot satisfies is_single_beat.
        slot = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        if slot is not None:
            assert is_single_beat(slot)
            assert slot.section_index in VALID_SECTION_INDICES

    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        content_level=_content_level,
        section_index=_section_index,
        pedagogical_role=_pedagogical_role,
        role_ordinal=_role_ordinal,
    )
    def test_content_level_normalized(
        self, topic_slug, content_level, section_index, pedagogical_role, role_ordinal
    ):
        # P1.14: content_level is always in LEVELS or None; unknown -> None.
        slot = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        if slot is not None:
            assert slot.content_level is None or slot.content_level in LEVELS
            if content_level not in LEVELS:
                assert slot.content_level is None
            else:
                assert slot.content_level == content_level

    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        content_level=_content_level,
        section_index=_section_index,
        pedagogical_role=_pedagogical_role,
        role_ordinal=_role_ordinal,
    )
    def test_role_ordinal_at_least_one(
        self, topic_slug, content_level, section_index, pedagogical_role, role_ordinal
    ):
        # P1.15: a returned role_ordinal is None or >= 1 (a supplied value < 1 -> None).
        slot = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        if slot is not None:
            assert slot.role_ordinal is None or slot.role_ordinal >= 1
            if not (isinstance(role_ordinal, int) and role_ordinal >= 1):
                assert slot.role_ordinal is None

    @settings(max_examples=100)
    @given(
        topic_slug=_topic_slug,
        content_level=_content_level,
        section_index=_section_index,
        pedagogical_role=_pedagogical_role,
        role_ordinal=_role_ordinal,
    )
    def test_total_and_deterministic(
        self, topic_slug, content_level, section_index, pedagogical_role, role_ordinal
    ):
        # P1.16: never raises on any input combination; identical inputs -> identical result.
        first = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        second = build_slot(
            topic_slug, content_level, section_index, pedagogical_role, role_ordinal
        )
        assert first == second
        # is_single_beat is likewise total over None and any slot.
        assert is_single_beat(first) == is_single_beat(second)
