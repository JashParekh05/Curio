"""Property-based test for Remediation_Select.

# Feature: structured-learn-curriculum, Property 8: Remediation targets the weak beat deterministically (P3.12-P3.16)

Validates: Requirements 3.2, 3.4

``clips_to_rewatch`` keeps only clips from the weak beat (P3.12), bounds the
result by ``max_clips`` with non-positive yielding empty (P3.13), orders the
survivors by a total key with no remaining ties (P3.14), handles empty input
without raising (P3.15), and is deterministic (P3.16).
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.remediation import RewatchClip, clips_to_rewatch


@st.composite
def _candidates(draw):
    """A list of RewatchClip with unique clip_ids and varied fields.

    section_index spans the weak beat (0..3) and neighbours; role_ordinal mixes
    present ints with None; final_score varies including duplicates.
    """
    n = draw(st.integers(min_value=0, max_value=12))
    clip_ids = draw(
        st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=6),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    clips = []
    for clip_id in clip_ids:
        section_index = draw(st.integers(min_value=0, max_value=5))
        role_ordinal = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=8)))
        final_score = draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        )
        clips.append(
            RewatchClip(
                clip_id=clip_id,
                section_index=section_index,
                role_ordinal=role_ordinal,
                final_score=final_score,
            )
        )
    return clips


def _key(clip: RewatchClip) -> tuple[int, int, float, str]:
    """Total order key: role_ordinal asc (None last), final_score desc, clip_id asc."""
    if clip.role_ordinal is None:
        return (1, 0, -clip.final_score, clip.clip_id)
    return (0, clip.role_ordinal, -clip.final_score, clip.clip_id)


class TestRemediationSelect:
    @settings(max_examples=100)
    @given(
        candidates=_candidates(),
        weak_section_index=st.integers(min_value=0, max_value=5),
        max_clips=st.integers(min_value=-2, max_value=6),
    )
    def test_remediation_properties(self, candidates, weak_section_index, max_clips):
        result = clips_to_rewatch(weak_section_index, candidates, max_clips)

        # P3.12: every returned clip belongs to the weak beat.
        for clip in result:
            assert clip.section_index == weak_section_index

        # P3.13: bounded by max_clips; non-positive max_clips -> empty.
        if max_clips <= 0:
            assert result == []
        else:
            assert len(result) <= max_clips

        # P3.14: total order with no two adjacent equal under the key (strict).
        for earlier, later in zip(result, result[1:]):
            assert _key(earlier) < _key(later)

        # P3.15: empty candidates -> empty; never raises (reaching here proves it).
        if not candidates:
            assert result == []

        # P3.16: determinism -- identical inputs produce identical output.
        again = clips_to_rewatch(weak_section_index, candidates, max_clips)
        assert result == again
