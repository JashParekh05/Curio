"""Property-based test for the realized arc computation.

# Feature: content-hook-engagement, Property 22: Realized_Arc is the clip role sequence

For any list of assembled clips, ``compute_realized_arc`` equals the list of
each clip's ``pedagogical_role`` taken in assembled order, with clips whose
role is ``None`` excluded.

Validates: Requirements 5.1
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.alignment import compute_realized_arc

_VALID_ROLES = list(get_args(PedagogicalRole))

# Roles drawn for each clip: every defined role value plus None (pre-feature
# rows that have not been assigned a pedagogical role yet).
_role = st.one_of(st.none(), st.sampled_from(_VALID_ROLES))


@st.composite
def _clip(draw):
    """Build a Clip with a random pedagogical_role (including None)."""
    return Clip(
        id=draw(st.text(min_size=1, max_size=12)),
        topic_slug=draw(st.text(min_size=1, max_size=12)),
        title=draw(st.text(min_size=1, max_size=20)),
        video_url=draw(st.text(min_size=1, max_size=20)),
        pedagogical_role=draw(_role),
    )


_clips = st.lists(_clip(), min_size=0, max_size=30)


class TestRealizedArc:
    @settings(max_examples=100)
    @given(clips=_clips)
    def test_realized_arc_is_clip_role_sequence(self, clips):
        result = compute_realized_arc(clips)

        # Equals each clip's role in assembled order, excluding None roles.
        expected = [c.pedagogical_role for c in clips if c.pedagogical_role is not None]
        assert result == expected

        # No None values leak into the realized arc.
        assert all(role is not None for role in result)

        # Every entry is a defined pedagogical role.
        assert all(role in _VALID_ROLES for role in result)

        # Length matches the count of role-bearing clips.
        assert len(result) == sum(1 for c in clips if c.pedagogical_role is not None)
