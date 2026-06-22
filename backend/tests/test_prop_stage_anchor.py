"""Property-based test for the Stage_Anchor pure core.

# Feature: structured-learn-curriculum, Property 6: Checkpoint anchoring is well-formed (P2.7-P2.11)

*For any* raw ``stage`` / ``section_index`` anchor inputs,
``stage_anchor.normalize_anchor`` produces a well-formed
``QuestionAnchor``: the result ``stage`` is always one of ``VALID_STAGES`` and an
invalid/None stage maps to ``DEFAULT_STAGE`` (``"check"``) (P2.7); a ``check``
result always carries ``section_index in {0,1,2,3}`` while ``pre``/``post``
results always carry ``section_index is None`` (P2.8); ``transcript_scope`` is
``"beat"`` iff the stage is ``check`` and ``"topic"`` otherwise (P2.9);
normalization is idempotent on an already-normalized anchor's fields (P2.10);
and the core is total and deterministic -- it never raises on any input
combination and identical inputs always produce identical results (P2.11).

Imports only the pure module under test (``app.services.stage_anchor``), so it
runs offline with no external service.

Validates: Requirements 2.2, 2.3
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.stage_anchor import (
    DEFAULT_STAGE,
    VALID_SECTION_INDICES,
    VALID_STAGES,
    QuestionAnchor,
    normalize_anchor,
    transcript_scope,
)

# ---------------------------------------------------------------------------
# Input strategies
# ---------------------------------------------------------------------------

# stage: recognized VALID_STAGES members + arbitrary junk text + None.
_stage = st.one_of(
    st.sampled_from(VALID_STAGES),
    st.text(max_size=12),
    st.none(),
)

# section_index: ints incl. out-of-range, plus None.
_section_index = st.one_of(
    st.integers(min_value=-5, max_value=10),
    st.none(),
)


class TestStageAnchor:
    @settings(max_examples=100)
    @given(stage=_stage, section_index=_section_index)
    def test_stage_always_valid_invalid_to_default(self, stage, section_index):
        # P2.7: result stage in VALID_STAGES; invalid/None -> DEFAULT_STAGE ("check").
        anchor = normalize_anchor(stage, section_index)
        assert anchor.stage in VALID_STAGES
        if stage not in VALID_STAGES:
            assert anchor.stage == DEFAULT_STAGE

    @settings(max_examples=100)
    @given(stage=_stage, section_index=_section_index)
    def test_check_section_coupling(self, stage, section_index):
        # P2.8: check -> section_index in {0,1,2,3}; pre/post -> section_index is None.
        anchor = normalize_anchor(stage, section_index)
        if anchor.stage == "check":
            assert anchor.section_index in VALID_SECTION_INDICES
            assert anchor.section_index in {0, 1, 2, 3}
        else:
            assert anchor.section_index is None

    @settings(max_examples=100)
    @given(stage=_stage, section_index=_section_index)
    def test_transcript_scope_beat_iff_check(self, stage, section_index):
        # P2.9: transcript_scope is "beat" iff stage == "check", else "topic".
        anchor = normalize_anchor(stage, section_index)
        scope = transcript_scope(anchor)
        assert scope == ("beat" if anchor.stage == "check" else "topic")
        assert (scope == "beat") == (anchor.stage == "check")

    @settings(max_examples=100)
    @given(stage=_stage, section_index=_section_index)
    def test_idempotent(self, stage, section_index):
        # P2.10: re-normalizing an already-normalized anchor's fields yields an equal anchor.
        anchor = normalize_anchor(stage, section_index)
        again = normalize_anchor(anchor.stage, anchor.section_index)
        assert again == anchor
        assert isinstance(anchor, QuestionAnchor)

    @settings(max_examples=100)
    @given(stage=_stage, section_index=_section_index)
    def test_total_and_deterministic(self, stage, section_index):
        # P2.11: never raises on any input combination; identical inputs -> identical result.
        first = normalize_anchor(stage, section_index)
        second = normalize_anchor(stage, section_index)
        assert first == second
        # transcript_scope is likewise total and deterministic.
        assert transcript_scope(first) == transcript_scope(second)
