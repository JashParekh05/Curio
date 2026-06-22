"""Property-based test for the Checkpoint_Placement pure decision core.

# Feature: structured-learn-curriculum, Property 4: Checkpoints are soft (never block the scroll) (P1.17-P1.22)

*For any* ordered list of clip ``section_index`` values (the beat each clip
belongs to, in feed order), a ``topic_slug``, and a ``check_after_beat`` anchor,
``place_checkpoints`` returns only always-skippable cards that insert without
removing or reordering clips: zero clips yield zero cards (P1.18); every
``after_clip_index`` is a valid in-range index (P1.19); at most one ``check`` and
at most one ``post`` card are emitted, with the ``post`` anchored at the final
clip (P1.20); a ``check`` card is emitted iff its anchor beat has at least one
clip, anchored at the LAST such clip (P1.21); and the function is deterministic
and total (P1.22).

Imports only the pure module under test, so it runs offline with no external
service.

Validates: Requirements 1.5, 1.6
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.checkpoint_placement import CheckpointCard, place_checkpoints

# Section indices cover the canonical beats 0..3 plus some out-of-range values so
# the generator exercises beats that may or may not contain any clip.
_SECTION_INDICES = st.integers(min_value=0, max_value=6)
_TOPIC_SLUGS = st.sampled_from(["algorithms", "dynamic-programming", "graphs", "x"])
_CHECK_AFTER_BEAT = st.integers(min_value=0, max_value=3)


@given(
    clips=st.lists(_SECTION_INDICES, max_size=20),
    topic_slug=_TOPIC_SLUGS,
    check_after_beat=_CHECK_AFTER_BEAT,
)
@settings(max_examples=100)
def test_checkpoint_placement_soft_invariants(
    clips: list[int], topic_slug: str, check_after_beat: int
) -> None:
    cards = place_checkpoints(clips, topic_slug, check_after_beat)
    n = len(clips)

    # P1.18: zero clips -> zero cards.
    if n == 0:
        assert cards == []
        return

    check_cards = [c for c in cards if c.stage == "check"]
    post_cards = [c for c in cards if c.stage == "post"]

    for card in cards:
        assert isinstance(card, CheckpointCard)
        # P1.17: every emitted card is soft.
        assert card.skippable is True
        assert card.topic_slug == topic_slug
        # P1.19: after_clip_index is a valid index into the clip list.
        assert 0 <= card.after_clip_index < n

    # P1.20: at most one check and at most one post card.
    assert len(check_cards) <= 1
    assert len(post_cards) <= 1
    # A post card is always emitted for a non-empty topic, anchored at the last clip.
    assert len(post_cards) == 1
    assert post_cards[0].after_clip_index == n - 1
    assert post_cards[0].section_index is None

    # P1.21: a check card is emitted iff the anchor beat has at least one clip;
    # when emitted its section_index == check_after_beat and after_clip_index is
    # the index of the LAST such clip.
    anchor_indices = [i for i, s in enumerate(clips) if s == check_after_beat]
    if anchor_indices:
        assert len(check_cards) == 1
        assert check_cards[0].section_index == check_after_beat
        assert check_cards[0].after_clip_index == anchor_indices[-1]
    else:
        assert check_cards == []


@given(
    clips=st.lists(_SECTION_INDICES, max_size=20),
    topic_slug=_TOPIC_SLUGS,
    check_after_beat=_CHECK_AFTER_BEAT,
)
@settings(max_examples=100)
def test_checkpoint_placement_is_deterministic_and_total(
    clips: list[int], topic_slug: str, check_after_beat: int
) -> None:
    # P1.22: determinism and totality -- never raises and identical inputs yield
    # identical output. Also confirm the input list is not mutated.
    snapshot = list(clips)
    first = place_checkpoints(clips, topic_slug, check_after_beat)
    second = place_checkpoints(clips, topic_slug, check_after_beat)
    assert first == second
    assert clips == snapshot
