"""Placement_Score_Map pure decision core (Phase 2, Req 2).

Maps a lightweight diagnostic-quiz result (number correct out of a total, with an
optional ``grade_level``) to a starting Content_Level (one of ``level_filter``
``LEVELS``). That level is then handed to the existing ``placement.place`` core to
pick the entry node in the leveled path, so no new difficulty model is introduced
here -- this core only translates a score into the established level vocabulary.

Following the repository convention (see ``placement.py`` / ``level_filter.py`` /
``reseed_prioritizer.py``), this module holds ONLY the pure decision logic: no DB,
no clock, no global reads. ``score_to_level`` is deterministic in its inputs, total
over all integer inputs (clamping negative ``correct`` and ``correct > total``
without raising), and always returns a value in ``LEVELS``.

Scoring (Decision 2):

- ``total <= 0`` (no diagnostic taken): defer entirely to ``grade_level`` via
  ``level_filter.derive_content_level(grade_level)``.
- Otherwise ``fraction = correct / total`` clamped to ``[0, 1]``:
    - ``fraction >= ADVANCED_THRESHOLD``     -> ``advanced``
    - ``fraction >= INTERMEDIATE_THRESHOLD`` -> ``intermediate``
    - else                                   -> ``beginner``

The diagnostic result is the primary signal; ``grade_level`` is the fallback only
when no diagnostic was taken. The result is monotonic non-decreasing in ``correct``
at a fixed ``total``.

ASCII only.

Validates: Requirements 2.1, 2.3
"""
from __future__ import annotations

# Reuse the single source of truth for level vocabulary and the grade -> level map.
from app.services.level_filter import LEVELS, derive_content_level

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fraction-correct at or above which a diagnostic places at least intermediate.
INTERMEDIATE_THRESHOLD: float = 0.4

#: Fraction-correct at or above which a diagnostic places at advanced.
ADVANCED_THRESHOLD: float = 0.75


# ---------------------------------------------------------------------------
# Score mapping
# ---------------------------------------------------------------------------

def score_to_level(correct: int, total: int, grade_level: str | None = None) -> str:
    """Map a placement-quiz result to a starting Content_Level (one of ``LEVELS``).

    - ``total <= 0`` (no diagnostic taken): defer entirely to ``grade_level`` via
      ``derive_content_level(grade_level)``.
    - Otherwise ``fraction = correct / total`` clamped to ``[0, 1]`` (negative
      ``correct`` clamps to 0; ``correct > total`` caps the fraction at 1.0):
        - ``fraction >= ADVANCED_THRESHOLD``     -> ``"advanced"``
        - ``fraction >= INTERMEDIATE_THRESHOLD`` -> ``"intermediate"``
        - else                                   -> ``"beginner"``

    Pure, total, deterministic; result always in ``LEVELS``; monotonic
    non-decreasing in ``correct`` at a fixed ``total``; never raises on
    out-of-range / negative input.

    Validates: Requirements 2.1, 2.3
    """
    if total <= 0:
        return derive_content_level(grade_level)

    # Clamp correct into [0, total] so the fraction is always within [0, 1].
    clamped_correct = correct
    if clamped_correct < 0:
        clamped_correct = 0
    elif clamped_correct > total:
        clamped_correct = total

    fraction = clamped_correct / total

    if fraction >= ADVANCED_THRESHOLD:
        return "advanced"
    if fraction >= INTERMEDIATE_THRESHOLD:
        return "intermediate"
    return "beginner"
