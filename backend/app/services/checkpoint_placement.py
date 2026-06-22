"""Checkpoint_Placement pure decision core (Phase 1, Req 1 checkpoints).

Decides where a *soft* checkpoint card is inserted into the in-feed clip
sequence for a single Topic. A ``check`` card follows the last clip of an anchor
beat (a quick self-test on what was just watched); a ``post`` card follows the
final clip of the Topic (a topic-wide wrap-up). The shell
(``feed.get_path_feed``) renders the returned cards inline between clips using
the existing quiz component, pulling questions by ``(topic_slug, stage,
section_index)``.

Following the repository convention (see ``clip_ordering.py`` /
``placement.py``), this module holds ONLY the pure decision logic: no DB, no
clock, no global reads. ``place_checkpoints`` is deterministic in its inputs,
never mutates them, and never raises -- it returns a new list.

The "never hard-lock the scroll" decision is encoded *structurally*: this core
returns insertion positions only. It has no authority to remove, reorder, or
gate clips -- every returned card carries ``after_clip_index`` pointing within
the existing clip list and ``skippable=True`` (always). The feed always advances
to the next clip regardless of any card.

ASCII only.

Validates: Requirements 1.5, 1.6, 3.3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The stage of a soft checkpoint card. ``check`` follows a single beat;
#: ``post`` follows the whole Topic.
CheckpointStage = Literal["check", "post"]


@dataclass(frozen=True)
class CheckpointCard:
    """One soft checkpoint card inserted into a Topic's ordered clip sequence.

    Attributes:
        stage: ``check`` (follows a beat) or ``post`` (follows the Topic).
        after_clip_index: Insert the card AFTER this 0-based clip index. Always a
            valid index into the clip list (``0 <= idx < len(clips)``); the card
            never implies removing or reordering clips.
        topic_slug: The Topic this card belongs to.
        section_index: The beat a ``check`` follows; None for a ``post`` card.
        skippable: ALWAYS True. A checkpoint is soft and never blocks the scroll.
    """

    stage: CheckpointStage
    after_clip_index: int
    topic_slug: str
    section_index: int | None
    skippable: bool


def place_checkpoints(
    ordered_clip_section_indices: list[int],
    topic_slug: str,
    check_after_beat: int = 2,
) -> list[CheckpointCard]:
    """Compute soft checkpoint card insertions for one Topic's ordered clips.

    The input is the ordered list of each clip's ``section_index`` (the beat it
    belongs to), in feed order. The result is the soft checkpoint cards to weave
    into that sequence:

    - At most one ``check`` card is inserted AFTER the last clip whose
      ``section_index == check_after_beat``, and ONLY when that beat has at least
      one clip. Its ``section_index`` equals ``check_after_beat`` and its
      ``after_clip_index`` is the index of that last clip (Req 1.5).
    - At most one ``post`` card is inserted after the final clip of the Topic
      (``after_clip_index == len(clips) - 1``), with ``section_index`` None
      (Req 1.5).
    - Zero clips yields zero cards (Req 1.5).
    - Every ``after_clip_index`` is a valid index into the clip list
      (``0 <= idx < len(clips)``); cards never reorder or drop clips (Req 1.5).
    - Every card has ``skippable=True`` (soft; never hard-locks the scroll,
      Req 3.3).

    The returned cards are ordered by ``after_clip_index`` ascending; when both a
    ``check`` and a ``post`` are present and anchored at the same final clip, the
    ``check`` precedes the ``post``.

    Pure and deterministic: identical inputs always produce identical output, and
    no external service is accessed and no state is changed (Req 1.6).

    Args:
        ordered_clip_section_indices: Each clip's beat (``section_index``) in feed
            order. Length is the number of clips.
        topic_slug: The Topic these clips and cards belong to.
        check_after_beat: The beat after which the ``check`` card is anchored
            (defaults to 2, a quick CHECK after the "how" beat).

    Returns:
        A new list of :class:`CheckpointCard` in ``after_clip_index`` order.

    Validates: Requirements 1.5, 1.6, 3.3
    """
    clip_count = len(ordered_clip_section_indices)

    # Req 1.5: zero clips -> zero cards.
    if clip_count == 0:
        return []

    cards: list[CheckpointCard] = []

    # check card: anchor after the LAST clip of the anchor beat, only if the
    # beat has at least one clip.
    check_anchor_index: int | None = None
    for index, section_index in enumerate(ordered_clip_section_indices):
        if section_index == check_after_beat:
            check_anchor_index = index
    if check_anchor_index is not None:
        cards.append(
            CheckpointCard(
                stage="check",
                after_clip_index=check_anchor_index,
                topic_slug=topic_slug,
                section_index=check_after_beat,
                skippable=True,
            )
        )

    # post card: after the final clip of the Topic.
    cards.append(
        CheckpointCard(
            stage="post",
            after_clip_index=clip_count - 1,
            topic_slug=topic_slug,
            section_index=None,
            skippable=True,
        )
    )

    # Order by after_clip_index ascending; keep check before post on ties
    # (stage rank 0 = check, 1 = post).
    _stage_rank = {"check": 0, "post": 1}
    cards.sort(key=lambda card: (card.after_clip_index, _stage_rank[card.stage]))
    return cards
