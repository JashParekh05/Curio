"""Level_Conditioned_Query: pure level-driven retrieval conditioning.

Conditions a beat's base YouTube ``search_query`` and the target Content_Level on
the learner's Level so a Foundations beat retrieves intro explainers and an
Advanced beat retrieves deeper material. It augments (never replaces) the
``search_query`` that ``section_planner`` already emits per beat.

All functions here are PURE: deterministic in their inputs, total over their
declared inputs, no DB / clock / global access, and never raise. This mirrors
``level_filter.py`` and ``reseed_prioritizer.py``. ASCII only.

Validates: Requirements 1.4, 1.6
"""
from __future__ import annotations

# Reuse the single source of truth for level ordering / recognition.
from app.services.level_filter import LEVELS, LEVEL_INDEX  # noqa: F401  (LEVEL_INDEX reused for ordering parity)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Per-level qualifier phrases appended to a beat's base ``search_query``.
LEVEL_QUALIFIERS: dict[str, str] = {
    "beginner": "for beginners intro explained simply",
    "intermediate": "explained in depth",
    "advanced": "advanced deep dive rigorous",
}


# ---------------------------------------------------------------------------
# Query conditioning
# ---------------------------------------------------------------------------

def condition_beat_query(base_query: str, level: str) -> str:
    """Append the level-appropriate qualifier to a beat's base search_query.

    - Trims and collapses internal whitespace of ``base_query``.
    - For a recognized ``level`` (one of ``LEVELS``), appends the matching
      qualifier exactly once, preserving the trimmed base as a prefix. The
      append is idempotent: if the qualifier is already present at the end of
      the trimmed base, it is not appended again.
    - For an unrecognized or empty ``level``, returns the trimmed base unchanged.
    - Never returns empty when ``base_query.strip()`` is non-empty.

    Pure; total; deterministic; never raises.

    Validates: Requirements 1.4, 1.6
    """
    collapsed = " ".join(base_query.split())

    qualifier = LEVEL_QUALIFIERS.get(level) if level in LEVELS else None
    if qualifier is None:
        return collapsed

    if not collapsed:
        return qualifier
    if collapsed.endswith(qualifier):
        return collapsed
    return collapsed + " " + qualifier


def target_content_level(level: str) -> str | None:
    """The Content_Level a beat at this level should retrieve / align to.

    Returns ``level`` when recognized (one of ``LEVELS``), else ``None`` so
    callers fall back to the topic's own difficulty (mirrors
    ``level_filter.clip_level``). Total over all strings.

    Validates: Requirements 1.4, 1.6
    """
    return level if level in LEVELS else None
