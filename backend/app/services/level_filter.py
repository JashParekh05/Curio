"""Level_Filter: pure level-aware ranking for cold-start Discover serving.

Maps a requesting user's ``grade_level`` to a Content_Level
(``beginner | intermediate | advanced``) via the existing ``_GRADE_DIFFICULTY``
map, ranks candidate clips so the user's exact level comes first, excludes
below-level clips while a match exists, and applies a nearest-higher-then-lower
soft fallback when no exact match is available.

All functions here are PURE: deterministic in their inputs, no DB / clock /
global mutation, mirroring ``self_heal_state.py`` and ``coherence_budget.py``.
The ranking helpers use stable sorts so they preserve the incoming
(personalized) order within a level group. ASCII only.

Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 5.5, 6.9
"""
from __future__ import annotations

from typing import Any, Sequence

# Reuse the single source of truth for grade -> difficulty (Content_Level).
from app.services.discover_seeding import _GRADE_DIFFICULTY

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The three Content_Levels, ordered from lowest to highest difficulty.
LEVELS: tuple[str, str, str] = ("beginner", "intermediate", "advanced")

#: Ordinal index of each Content_Level (beginner < intermediate < advanced).
LEVEL_INDEX: dict[str, int] = {"beginner": 0, "intermediate": 1, "advanced": 2}

#: Default Content_Level for a missing or unmapped ``grade_level`` (Req 4.2).
DEFAULT_LEVEL: str = "intermediate"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_level(clip: Any) -> str | None:
    """Read a clip's Content_Level, supporting both objects and dicts.

    Returns the clip's ``content_level`` when set to one of ``LEVELS``,
    otherwise ``None``. Unknown / unset values are treated as unlevelled so a
    clip without a recognized level never matches and ranks after matches.
    """
    if isinstance(clip, dict):
        value = clip.get("content_level")
    else:
        value = getattr(clip, "content_level", None)
    if value in LEVEL_INDEX:
        return value
    return None


# ---------------------------------------------------------------------------
# Level derivation
# ---------------------------------------------------------------------------

def derive_content_level(grade_level: str | None) -> str:
    """Derive a Content_Level from a user's ``grade_level``.

    Returns ``_GRADE_DIFFICULTY[grade_level]`` when the grade is mapped, and
    ``DEFAULT_LEVEL`` (``intermediate``) when ``grade_level`` is ``None`` or
    unmapped. The result is always exactly one of ``LEVELS``.

    Validates: Requirements 4.1, 4.2
    """
    level = _GRADE_DIFFICULTY.get(grade_level, DEFAULT_LEVEL) if grade_level is not None else DEFAULT_LEVEL
    if level not in LEVEL_INDEX:
        return DEFAULT_LEVEL
    return level


# ---------------------------------------------------------------------------
# Level matching
# ---------------------------------------------------------------------------

def is_match(clip_level: str | None, user_level: str) -> bool:
    """True iff ``clip_level`` exactly equals ``user_level``.

    A clip with no level (``None``) never matches any level.

    Validates: Requirements 4.3, 4.4
    """
    return clip_level is not None and clip_level == user_level


def level_sort_key(clip_level: str | None, user_level: str) -> tuple[int]:
    """Primary ranking key: ``(0,)`` for an exact level match, ``(1,)`` otherwise.

    Clips with no level fall into the ``1`` bucket. This key is applied BEFORE
    any other ranking signal so exact-level clips always lead.

    Validates: Requirements 4.3, 4.4
    """
    return (0,) if is_match(clip_level, user_level) else (1,)


# ---------------------------------------------------------------------------
# Ranking / filtering
# ---------------------------------------------------------------------------

def rank_by_level(clips: Sequence[Any], user_level: str) -> list[Any]:
    """Stable-sort clips so every exact-level match precedes every non-match.

    Clips with no level are treated as non-matching and ranked after all
    exact-level matches. The incoming (personalized) order is preserved within
    each group because the sort is stable.

    Validates: Requirements 4.3, 4.4
    """
    return sorted(clips, key=lambda clip: level_sort_key(_read_level(clip), user_level))


def exclude_below(clips: Sequence[Any], user_level: str) -> list[Any]:
    """Drop clips strictly below ``user_level`` when at least one exact match exists.

    When no clip exactly matches ``user_level``, the input is returned unchanged
    (the soft fallback in ``fallback_order`` handles that case). Clips with no
    level are never considered "below" and are always retained.

    Validates: Requirements 4.5
    """
    user_index = LEVEL_INDEX.get(user_level)
    if user_index is None:
        return list(clips)

    levels = [_read_level(clip) for clip in clips]
    has_match = any(lvl == user_level for lvl in levels)
    if not has_match:
        return list(clips)

    kept: list[Any] = []
    for clip, lvl in zip(clips, levels):
        if lvl is not None and LEVEL_INDEX[lvl] < user_index:
            continue  # strictly below the user's level -> exclude
        kept.append(clip)
    return kept


def fallback_order(clips: Sequence[Any], user_level: str) -> list[Any]:
    """Order clips by level distance for the no-exact-match soft fallback.

    Ordering: any exact match first (defensive), then the nearest-higher level
    ascending toward ``advanced``, then the nearest-lower level descending
    toward ``beginner``, with unlevelled clips last. Stable within each group.
    This (not ``hook_score``) is what the Discover fallback uses to fill the
    feed.

    Validates: Requirements 4.6, 4.7, 5.5
    """
    user_index = LEVEL_INDEX.get(user_level, LEVEL_INDEX[DEFAULT_LEVEL])

    def key(clip: Any) -> tuple[int, int]:
        lvl = _read_level(clip)
        if lvl is None:
            return (3, 0)  # unlevelled -> last
        i = LEVEL_INDEX[lvl]
        if i == user_index:
            return (0, 0)  # exact match -> first (defensive)
        if i > user_index:
            return (1, i - user_index)  # higher: nearest-higher first, ascending
        return (2, user_index - i)  # lower: nearest-lower first, descending toward beginner

    return sorted(clips, key=key)


def clip_level(clip: Any, topic_level: str | None) -> str | None:
    """Resolve a clip's Content_Level for ranking.

    Returns the clip's own ``content_level`` when set, else the owning topic's
    level (``topics.difficulty``), else ``None``. This bridges the denormalized
    ``clips.content_level`` column and the authoritative ``topics.difficulty``
    so pre-feature clips need no backfill.

    Validates: Requirements 4.1 (data-model bridge)
    """
    own = _read_level(clip)
    if own is not None:
        return own
    if topic_level in LEVEL_INDEX:
        return topic_level
    return None
