"""Stage_Anchor pure decision core (Phase 2, Req 1 checkpoints / Req 2).

Validates and normalizes the ``stage`` (pre | check | post) and
``section_index`` anchor attached to a quiz question, and encodes which
transcripts a stage's question is generated from: a ``check`` is anchored to a
single beat's clips, while a ``pre`` / ``post`` spans the whole topic.

Following the repository convention (see ``clip_slot.py`` /
``checkpoint_placement.py``), this module holds ONLY the pure decision logic:
no DB, no clock, no global reads. Every function is deterministic in its
inputs, total over its declared inputs, and never raises on any input
combination. The thin shell that selects beat transcripts and persists the
``stage`` / ``section_index`` columns lives in ``services/quiz.py``.

ASCII only.

Validates: Requirements 2.2, 2.3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The role a quiz question plays: a diagnostic ``pre``, a per-beat ``check``,
#: or a per-topic ``post``.
QuizStage = Literal["pre", "check", "post"]

#: The recognized stage values, in canonical order.
VALID_STAGES: tuple[str, ...] = ("pre", "check", "post")

#: The fallback stage for an invalid/missing value. Matches the Phase 2
#: migration default (``quiz_questions.stage`` default ``'check'``), so legacy
#: rows become topic-wide ``check`` questions with no value backfill.
DEFAULT_STAGE: str = "check"

#: The valid beat indices of a topic's Canonical_Arc (hook / what / how /
#: outcomes); a ``check`` question is anchored to exactly one of these.
VALID_SECTION_INDICES: frozenset[int] = frozenset({0, 1, 2, 3})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuestionAnchor:
    """The normalized stage + section anchor of a quiz question.

    Attributes:
        stage: One of ``VALID_STAGES`` (``pre`` | ``check`` | ``post``).
        section_index: The anchored beat in 0..3 for a ``check`` question;
            ``None`` for the topic-wide ``pre`` / ``post`` stages.
    """

    stage: QuizStage
    section_index: int | None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_anchor(stage: str | None, section_index: int | None) -> QuestionAnchor:
    """Normalize a stored/LLM-provided anchor to a valid QuestionAnchor.

    Normalization rules (Req 2.2):

    - ``stage`` not in ``VALID_STAGES`` (or ``None``) -> ``DEFAULT_STAGE``
      (``'check'``) (P2.7).
    - For ``'check'``: ``section_index`` must be in 0..3; an out-of-range or
      missing value is coerced to ``0`` (P2.8).
    - For ``'pre'`` and ``'post'``: ``section_index`` is always ``None``
      (topic-wide), even if a value was supplied (P2.8).

    Idempotent: applying this to an already-normalized anchor's fields returns
    an equal anchor (P2.10).

    Pure, total, deterministic; never raises on any input combination (P2.11).

    Args:
        stage: The raw stage value (may be ``None`` or unrecognized).
        section_index: The raw beat index (may be ``None`` or out of range).

    Returns:
        A valid :class:`QuestionAnchor`.

    Validates: Requirements 2.2, 2.3
    """
    # Coerce an invalid/missing stage to the default.
    normalized_stage: QuizStage = stage if stage in VALID_STAGES else DEFAULT_STAGE  # type: ignore[assignment]

    if normalized_stage == "check":
        # A check question is beat-anchored: require a valid beat, else 0.
        normalized_index: int | None = (
            section_index if section_index in VALID_SECTION_INDICES else 0
        )
    else:
        # pre / post are topic-wide: never carry a section index.
        normalized_index = None

    return QuestionAnchor(stage=normalized_stage, section_index=normalized_index)


# ---------------------------------------------------------------------------
# Transcript scope
# ---------------------------------------------------------------------------

def transcript_scope(anchor: QuestionAnchor) -> Literal["beat", "topic"]:
    """Return the transcript scope a stage's question is generated from.

    ``'beat'`` for a ``check`` (generate from that beat's clip transcripts
    only), ``'topic'`` for ``pre`` / ``post`` (generate from the whole topic)
    (P2.9). Total and never raises.

    Args:
        anchor: The question's anchor.

    Returns:
        ``'beat'`` iff ``anchor.stage == 'check'``, else ``'topic'``.

    Validates: Requirements 2.2
    """
    return "beat" if anchor.stage == "check" else "topic"
