"""Property-based test for delivery order vs realized-arc ordinal.

# Feature: content-hook-engagement, Property 34: Delivery order is non-decreasing in realized-arc ordinal

For any set of stored clips carrying ``role_ordinal`` values, the delivery
order produced by ``_order_by_arc`` is non-decreasing in ``role_ordinal``, so
the relative position of roles matches the Realized_Arc.  Arc clips (those with
a ``role_ordinal``) are delivered first, ordered by ordinal; pre-feature clips
(``role_ordinal=None``) come after.

Validates: Requirements 8.8
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.feed_retrieval import _order_by_arc


def _clip(
    clip_id: str,
    role_ordinal: int | None,
    final_score: float,
    hook_score: float,
) -> Clip:
    return Clip(
        id=clip_id,
        topic_slug="topic-slug",
        title="A Valid Title",
        description="A valid description.",
        video_url=f"https://example.com/{clip_id}",
        source_url=f"https://example.com/src/{clip_id}",
        hook_score=hook_score,
        final_score=final_score,
        role_ordinal=role_ordinal,
    )


# role_ordinal: None (pre-feature) or a small int so ordinals are frequently shared.
_role_ordinal = st.one_of(
    st.none(),
    st.integers(min_value=1, max_value=5),
)

_clip_strategy = st.builds(
    _clip,
    clip_id=st.uuids().map(str),
    role_ordinal=_role_ordinal,
    final_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    hook_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)


class TestDeliveryOrder:
    @settings(max_examples=100)
    @given(clips=st.lists(_clip_strategy, min_size=0, max_size=20))
    def test_delivery_order_non_decreasing_in_ordinal(self, clips):
        ordered = _order_by_arc(clips)

        # No clips are lost or duplicated.
        assert sorted(c.id for c in ordered) == sorted(c.id for c in clips)

        # Arc clips (role_ordinal is not None) lead the delivery; once a
        # pre-feature clip appears, no arc clip may follow.
        seen_legacy = False
        arc_ordinals: list[int] = []
        for c in ordered:
            if c.role_ordinal is None:
                seen_legacy = True
            else:
                assert not seen_legacy, (
                    "arc clip delivered after a pre-feature (role_ordinal=None) clip"
                )
                arc_ordinals.append(c.role_ordinal)

        # Within the arc group, role_ordinal is non-decreasing.
        assert arc_ordinals == sorted(arc_ordinals), (
            f"arc-group ordinals not non-decreasing: {arc_ordinals}"
        )
