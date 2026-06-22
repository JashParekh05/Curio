"""Property-based test for delivery order under the single arc-ordered path.

# Feature: content-retrieval-revamp, Property 9 (delivery): Delivery order is non-decreasing in Canonical_Arc role ordinal

Validates: Requirements 2.1, 2.4, 2.5

The single arc-ordered retrieval path (Req 2.3) supersedes the legacy
``role_ordinal`` / ``section_index`` ordering. ``_order_by_arc`` now delivers a
Topic's Clips ordered by their Canonical_Arc role ordinal ascending (looked up
from the arc by the Clip's ``pedagogical_role``), so the subsequence of
role-bearing Clips is non-decreasing in ordinal (Req 2.1, 2.4). Clips whose
role is absent from the arc are role-less and are delivered after every
role-bearing Clip (Req 2.5). No Clip is lost or duplicated.

This replaces the prior content-hook-engagement Property 34, which ordered by
the now-collapsed ``role_ordinal`` arc path.
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.arc_unifier import CanonicalArc, CanonicalArcRole
from app.services.feed_retrieval import _order_by_arc

# A fixed Canonical_Arc; clip roles are drawn from these plus None / off-arc so
# both role-bearing and role-less clips are exercised.
_ARC_ROLES: list[PedagogicalRole] = ["definition", "motivation", "mechanism", "example", "common_misconception"]
_ARC = CanonicalArc(
    topic_slug="topic-slug",
    roles=tuple(CanonicalArcRole(role=r, ordinal=i) for i, r in enumerate(_ARC_ROLES, start=1)),
)
_ROLE_CHOICES: list[PedagogicalRole | None] = list(_ARC_ROLES) + [None, "approach"]


def _clip(
    clip_id: str,
    role: PedagogicalRole | None,
    final_score: float,
    hook_score: float,
) -> Clip:
    return Clip(
        id=clip_id,
        topic_slug="topic-slug",
        title="A Valid Title",
        description="A valid description.",
        video_url=f"https://example.com/{clip_id}",
        # Distinct source per clip so source-spread is an identity and does not
        # reorder within a role group.
        source_url=f"https://example.com/src/{clip_id}",
        hook_score=hook_score,
        final_score=final_score,
        pedagogical_role=role,
    )


_clip_strategy = st.builds(
    _clip,
    clip_id=st.uuids().map(str),
    role=st.sampled_from(_ROLE_CHOICES),
    final_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    hook_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

_ROLE_ORDINAL = {ar.role: ar.ordinal for ar in _ARC.roles}


class TestDeliveryOrder:
    @settings(max_examples=100)
    @given(clips=st.lists(_clip_strategy, min_size=0, max_size=20))
    def test_delivery_order_non_decreasing_in_ordinal(self, clips):
        ordered = _order_by_arc(clips, _ARC)

        # No clips are lost or duplicated.
        assert sorted(c.id for c in ordered) == sorted(c.id for c in clips)

        # Role-bearing clips (role present in the arc) lead the delivery; once a
        # role-less clip appears, no role-bearing clip may follow (Req 2.5).
        seen_role_less = False
        arc_ordinals: list[int] = []
        for c in ordered:
            ordinal = _ROLE_ORDINAL.get(c.pedagogical_role) if c.pedagogical_role is not None else None
            if ordinal is None:
                seen_role_less = True
            else:
                assert not seen_role_less, (
                    "role-bearing clip delivered after a role-less clip"
                )
                arc_ordinals.append(ordinal)

        # Within the role-bearing group, the ordinal sequence is non-decreasing.
        assert arc_ordinals == sorted(arc_ordinals), (
            f"role-bearing ordinals not non-decreasing: {arc_ordinals}"
        )
