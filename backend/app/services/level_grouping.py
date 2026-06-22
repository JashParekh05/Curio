"""Level_Grouping pure decision core (Phase 1, Req 1 structure).

Groups an **already prerequisite-ordered** Spine_Path (the topo order produced by
``spine_router.route`` / ``curriculum_spine.topo_order``) into 2-4 ordered
**Levels** (Foundations -> Core -> Advanced). This core never reorders within the
prerequisite ordering; it only partitions the existing order into contiguous
bands, so prerequisite consistency is inherited from the spine.

Following the repository convention (see ``placement.py`` /
``curriculum_spine.py`` / ``coverage_view.py``), this module holds ONLY the pure
decision logic: no DB, no clock, no globals read. Every function is deterministic
in its inputs, total over its declared inputs, and never raises. The thin,
best-effort I/O shell that maps spine nodes to ``PathTopic`` and persists the
serialized ``LeveledPath`` lives in the ``topics``/feed shell.

Banding strategy (Req 1.1, 1.2):

- Bands are formed primarily by **non-decreasing Content_Level**: walk the
  prerequisite-ordered path and start a new band at each *upward* level
  transition (a topic whose Content_Level rank is strictly higher than the
  previous topic's). For a real (non-decreasing) spine path this yields one band
  per distinct level run, so no band's level rank regresses relative to the next
  (P1.5).
- If the transition banding yields fewer than ``MIN_LEVELS`` or more than
  ``MAX_LEVELS`` bands, fall back to an **even contiguous split** into
  ``clamp(distinct_bands, MIN_LEVELS, MAX_LEVELS)`` parts.
- The chosen level count is finally clamped to the number of topics so every
  Level holds at least one topic: a path with a single topic cannot be split into
  two non-empty Levels, so it degrades to a single Level (the documented
  degenerate edge); any path with at least two topics yields 2-4 Levels.

Every Level holds >= 1 topic, every input topic appears in exactly one Level, and
concatenating the Levels in ordinal order reproduces the input order exactly (a
partition). Level ordinals are consecutive from 1. Names map ordinal ->
Foundations / Core / Advanced for <= 3 Levels, else ``"Level N"``. An empty path
yields ``LeveledPath(())``.

ASCII only.

Validates: Requirements 1.1, 1.2, 1.6
"""
from __future__ import annotations

from dataclasses import dataclass

# Reuse the single source of truth for level ordering
# (beginner < intermediate < advanced).
from app.services.level_filter import LEVEL_INDEX, LEVELS  # noqa: F401  (LEVELS re-exported)

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Minimum number of Levels a non-empty, multi-topic path is grouped into.
MIN_LEVELS: int = 2

#: Maximum number of Levels any path is grouped into.
MAX_LEVELS: int = 4

#: Level names for a grouping of at most three Levels, indexed by (ordinal - 1).
#: A grouping of four Levels uses generic ``"Level N"`` names instead.
_LEVEL_NAMES: tuple[str, str, str] = ("Foundations", "Core", "Advanced")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PathTopic:
    """One topic in the prerequisite-ordered path.

    Mirrors the ``SpineNode`` fields used for grouping.

    Attributes:
        topic_slug: The Topic's stable slug; node identity within the path.
        content_level: The Topic's Content_Level, expected to be one of
            :data:`LEVELS`. An unrecognized value is treated as the lowest
            ("beginner") band for transition detection.
        position: The 0-based index of the topic in the topo (prerequisite)
            order. Retained for traceability; grouping uses list order directly.
    """

    topic_slug: str
    content_level: str
    position: int


@dataclass(frozen=True)
class Level:
    """One ordered band of topics in the grouped curriculum.

    Attributes:
        ordinal: The 1-based level number; consecutive from 1 across a
            ``LeveledPath``.
        name: ``"Foundations" | "Core" | "Advanced"`` for a grouping of at most
            three Levels, else ``"Level N"``.
        topic_slugs: The Topic slugs in this Level, in path order; always at
            least one.
    """

    ordinal: int
    name: str
    topic_slugs: tuple[str, ...]


@dataclass(frozen=True)
class LeveledPath:
    """The full curriculum expressed as ordered Levels.

    Attributes:
        levels: The ordered Levels. ``MIN_LEVELS``..``MAX_LEVELS`` Levels for a
            path of two or more topics, a single Level for a one-topic path, and
            ``()`` exactly when the path is empty.
    """

    levels: tuple[Level, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rank(content_level: str) -> int:
    """Return the Content_Level rank, treating an unknown value as the lowest band.

    Uses :data:`LEVEL_INDEX` (beginner < intermediate < advanced); an unrecognized
    or missing level maps to ``0`` (the "beginner" band) so it never triggers a
    spurious upward transition over a recognized level.
    """
    return LEVEL_INDEX.get(content_level, 0)


def _transition_bands(path: list[PathTopic]) -> list[list[PathTopic]]:
    """Partition the path into contiguous bands, splitting at each upward transition.

    Walks the prerequisite-ordered path and starts a new band whenever a topic's
    Content_Level rank is strictly greater than the previous topic's. Every band
    is a contiguous, non-empty run of the input, preserving order.
    """
    bands: list[list[PathTopic]] = []
    current: list[PathTopic] = [path[0]]
    prev_rank = _rank(path[0].content_level)
    for topic in path[1:]:
        rank = _rank(topic.content_level)
        if rank > prev_rank:
            bands.append(current)
            current = [topic]
        else:
            current.append(topic)
        prev_rank = rank
    bands.append(current)
    return bands


def _even_split(path: list[PathTopic], parts: int) -> list[list[PathTopic]]:
    """Split the path into ``parts`` contiguous, balanced, non-empty bands.

    The first ``len(path) % parts`` bands receive one extra topic so the band
    sizes differ by at most one. Order is preserved. Assumes
    ``1 <= parts <= len(path)``.
    """
    n = len(path)
    base, remainder = divmod(n, parts)
    bands: list[list[PathTopic]] = []
    start = 0
    for index in range(parts):
        size = base + 1 if index < remainder else base
        bands.append(path[start:start + size])
        start += size
    return bands


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into the inclusive range ``[low, high]``."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _level_name(ordinal: int, total_levels: int) -> str:
    """Map a 1-based ordinal to a Level name for a grouping of ``total_levels``.

    For groupings of at most three Levels the names are Foundations / Core /
    Advanced (indexed by ordinal); for larger groupings the generic ``"Level N"``
    form is used.
    """
    if total_levels <= len(_LEVEL_NAMES) and 1 <= ordinal <= len(_LEVEL_NAMES):
        return _LEVEL_NAMES[ordinal - 1]
    return "Level " + str(ordinal)


def _build_levels(bands: list[list[PathTopic]]) -> tuple[Level, ...]:
    """Build the ordinal-numbered, named ``Level`` tuple from contiguous bands."""
    total = len(bands)
    levels: list[Level] = []
    for index, band in enumerate(bands):
        ordinal = index + 1
        levels.append(
            Level(
                ordinal=ordinal,
                name=_level_name(ordinal, total),
                topic_slugs=tuple(topic.topic_slug for topic in band),
            )
        )
    return tuple(levels)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_levels(path: list[PathTopic]) -> LeveledPath:
    """Partition a prerequisite-ordered path into 2-4 contiguous, ordered Levels.

    Each Level is a contiguous run of the input path (never reordered), so every
    prerequisite still precedes its dependents across the whole ``LeveledPath``.
    Bands are formed primarily by non-decreasing Content_Level, splitting at each
    upward level transition; when that yields fewer than ``MIN_LEVELS`` or more
    than ``MAX_LEVELS`` bands, the path is split evenly into
    ``clamp(distinct_bands, MIN_LEVELS, MAX_LEVELS)`` contiguous parts instead.

    Every Level holds at least one topic, every input topic appears in exactly one
    Level, and flattening the Levels in ordinal order reproduces the input topic
    order exactly. Level ordinals are consecutive from 1; names map ordinal ->
    Foundations / Core / Advanced for at most three Levels, else ``"Level N"``.

    Degenerate edge: a level count can never exceed the number of topics, so a
    one-topic path yields a single Level (it cannot be split into two non-empty
    Levels). Any path with two or more topics yields between ``MIN_LEVELS`` and
    ``MAX_LEVELS`` Levels. An empty path yields ``LeveledPath(())``.

    Pure and deterministic: identical input always produces identical output, and
    no external state is read or changed.

    Args:
        path: The prerequisite-ordered topics to group.

    Returns:
        The :class:`LeveledPath` partition of the input.

    Validates: Requirements 1.1, 1.2, 1.6
    """
    if not path:
        return LeveledPath(levels=())

    bands = _transition_bands(path)
    distinct_bands = len(bands)

    if MIN_LEVELS <= distinct_bands <= MAX_LEVELS:
        # Transition banding is already a valid 2-4 grouping; use it directly.
        return LeveledPath(levels=_build_levels(bands))

    # Fall back to an even contiguous split. The desired part count is the
    # band count clamped to [MIN_LEVELS, MAX_LEVELS], finally capped by the
    # number of topics so every Level holds at least one topic (degenerate
    # single-topic paths collapse to a single Level).
    desired = _clamp(distinct_bands, MIN_LEVELS, MAX_LEVELS)
    level_count = max(1, min(desired, len(path)))
    return LeveledPath(levels=_build_levels(_even_split(path, level_count)))


def flatten(leveled: LeveledPath) -> list[str]:
    """Return all topic slugs in level-then-path order (the serving order).

    Concatenates each Level's ``topic_slugs`` in ordinal order, reproducing the
    original prerequisite-ordered topic sequence. Pure and total.

    Args:
        leveled: The grouped curriculum.

    Returns:
        The flattened list of topic slugs.

    Validates: Requirements 1.2
    """
    slugs: list[str] = []
    for level in leveled.levels:
        slugs.extend(level.topic_slugs)
    return slugs
