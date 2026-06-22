"""Reseed_Prioritizer pure decision core (Phase 1, Req 7).

This module closes the telemetry -> reseed loop on the *decision* side: it turns
Watch_Quality signal (exactly the rows ``analytics.worst_clips`` / ``rank_clips``
already produce) into prioritized :class:`~app.services.topic_frontier.BacklogItem`
values for the Topic_Frontier.

It holds ONLY the pure decision logic -- no DB, no clock, no globals read --
mirroring the convention in ``topic_frontier.py`` (the matching I/O shell lives in
``backlog_store.py``). Every function here is deterministic in its inputs and free
of side effects: it never mutates the signals or the lists passed in, always
returning new values, so identical inputs always yield identical output (Req 7.9).

The two identification functions select exactly the signals whose Watch_Quality is
below the Recut_Threshold and that clear the Minimum_Views confidence floor
(Req 7.1, 7.2, 7.6), and assign each a Reseed_Priority that is a monotonic
non-decreasing function of the Watch_Quality deficit and the view count, bounded to
the inclusive range [0.0, 1.0] (Req 7.3). A low-Watch_Quality Clip enqueues a
Backlog_Item for its *parent* Topic (Req 7.7); the duplicate-suppression that keeps
a Topic with a non-done Backlog_Item from being re-enqueued is applied by the shell
through the existing ``topic_frontier.enqueue`` dedupe.

ASCII only.

Validates: Requirements 7.1, 7.2, 7.3, 7.6, 7.7, 7.9
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.topic_frontier import BacklogItem

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: The Watch_Quality value at or below which a Clip or Topic is identified for
#: reseeding. Default 0.3; valid inclusive range [0.0, 1.0]. (Req 7.1)
DEFAULT_RECUT_THRESHOLD: float = 0.3

#: The minimum view count a Clip or Topic must have before it is eligible for
#: reseed identification, excluding low-confidence signal. Default 3; valid
#: inclusive integer range [1, 100]. (Req 7.1, 7.6)
DEFAULT_MINIMUM_VIEWS: int = 3

#: The Content_Level a reseed Backlog_Item carries. The Watch_Quality signal has
#: no level of its own, so reseed items default to the same intermediate level the
#: backlog shell uses when a row predates the level column.
DEFAULT_RESEED_LEVEL: str = "intermediate"

#: View-count saturation constant for the Reseed_Priority view factor. Larger
#: view counts push the factor toward (but never reaching) 1.0, so a Topic with
#: more views -- more confident, more impactful signal -- is prioritized above an
#: otherwise-identical Topic with fewer views, while the result stays bounded.
_VIEW_SATURATION: float = 10.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchQualitySignal:
    """View-weighted Watch_Quality signal for one Topic.

    Attributes:
        topic_slug: The Topic the signal is measured over.
        watch_quality: The view-weighted average Watch_Ratio in [0.0, 1.0].
        views: The number of views the signal is measured over (>= 0).
    """

    topic_slug: str
    watch_quality: float
    views: int


@dataclass(frozen=True)
class ClipQualitySignal:
    """Watch_Quality signal for one Clip, carrying its parent Topic.

    Attributes:
        clip_id: The Clip the signal is measured over.
        topic_slug: The Clip's parent Topic slug; the reseed Backlog_Item is
            enqueued for this Topic, not for the Clip (Req 7.7).
        watch_quality: The average Watch_Ratio in [0.0, 1.0].
        views: The number of views the signal is measured over (>= 0).
    """

    clip_id: str
    topic_slug: str
    watch_quality: float
    views: int


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

def reseed_priority(
    watch_quality: float,
    views: int,
    recut_threshold: float = DEFAULT_RECUT_THRESHOLD,
) -> float:
    """Reseed_Priority: bounded, monotonic in the deficit and the view count.

    The priority combines two non-negative, non-decreasing factors:

    - the Watch_Quality *deficit* ``recut_threshold - watch_quality``, clamped to
      [0.0, 1.0] so a worse Watch_Quality (a larger deficit) never lowers the
      priority; and
    - a view factor ``views / (views + _VIEW_SATURATION)`` in [0.0, 1.0) that is
      non-decreasing in ``views`` so more (more-confident) signal ranks higher.

    Their product is non-decreasing in each argument with the other held fixed and
    lies in [0.0, 1.0), and is clamped to the inclusive range [0.0, 1.0] to
    guarantee the invariant (Req 7.3). Pure and deterministic (Req 7.9).

    Args:
        watch_quality: The view-weighted Watch_Quality in [0.0, 1.0].
        views: The view count the signal is measured over.
        recut_threshold: The Recut_Threshold the deficit is measured against.

    Returns:
        A Reseed_Priority in the inclusive range [0.0, 1.0].

    Validates: Requirements 7.3, 7.9
    """
    deficit = recut_threshold - watch_quality
    deficit_norm = _clamp_unit(deficit)

    v = views if views > 0 else 0
    view_factor = v / (v + _VIEW_SATURATION)

    return _clamp_unit(deficit_norm * view_factor)


def _clamp_unit(x: float) -> float:
    """Clamp a value to the inclusive range [0.0, 1.0]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------

def _is_eligible(watch_quality: float, views: int,
                 recut_threshold: float, minimum_views: int) -> bool:
    """A signal is identified iff it clears Minimum_Views AND is below threshold.

    Below-Minimum_Views signal is excluded so low-confidence signal does not drive
    reseeding (Req 7.6); among the remaining signal, only Watch_Quality strictly
    below the Recut_Threshold is identified (Req 7.1, 7.2).
    """
    return views >= minimum_views and watch_quality < recut_threshold


def identify_topics_to_reseed(
    signals: list[WatchQualitySignal],
    recut_threshold: float = DEFAULT_RECUT_THRESHOLD,
    minimum_views: int = DEFAULT_MINIMUM_VIEWS,
) -> list[BacklogItem]:
    """Identify low-Watch_Quality Topics as pending reseed Backlog_Items.

    A Topic is identified exactly when its view-weighted Watch_Quality is below
    ``recut_threshold`` and its view count is at least ``minimum_views`` (Req 7.2,
    7.6). Each identified Topic becomes a pending ``BacklogItem`` carrying its
    ``reseed_priority`` (Req 7.3). The input order is preserved and the signals are
    never mutated; identical inputs yield identical output (Req 7.9). Duplicate
    suppression for a Topic that already has a non-done Backlog_Item is applied by
    the shell via ``topic_frontier.enqueue`` (Req 7.4).

    Args:
        signals: Per-Topic Watch_Quality signals.
        recut_threshold: The Recut_Threshold (valid [0.0, 1.0]).
        minimum_views: The Minimum_Views floor (valid [1, 100]).

    Returns:
        A new list of pending reseed ``BacklogItem`` values, one per identified
        Topic, in input order.

    Validates: Requirements 7.2, 7.3, 7.6, 7.9
    """
    items: list[BacklogItem] = []
    for sig in signals:
        if _is_eligible(sig.watch_quality, sig.views, recut_threshold, minimum_views):
            items.append(BacklogItem(
                topic=sig.topic_slug,
                level=DEFAULT_RESEED_LEVEL,
                priority=reseed_priority(sig.watch_quality, sig.views, recut_threshold),
                status="pending",
            ))
    return items


def identify_clips_to_reseed(
    signals: list[ClipQualitySignal],
    recut_threshold: float = DEFAULT_RECUT_THRESHOLD,
    minimum_views: int = DEFAULT_MINIMUM_VIEWS,
) -> list[BacklogItem]:
    """Identify low-Watch_Quality Clips as pending reseed Backlog_Items for their
    parent Topic.

    A Clip is identified exactly when its Watch_Quality is below ``recut_threshold``
    and its view count is at least ``minimum_views`` (Req 7.1, 7.6). Each identified
    Clip becomes a pending ``BacklogItem`` targeting its **parent Topic** slug --
    never the Clip id -- so the re-cut is routed through the Topic the Clip belongs
    to (Req 7.7); the priority is the Clip's ``reseed_priority`` (Req 7.3). The
    input order is preserved and the signals are never mutated; identical inputs
    yield identical output (Req 7.9). When several identified Clips share a parent
    Topic the core emits one item per Clip; the shell collapses them to a single
    non-done Backlog_Item via the existing ``topic_frontier.enqueue`` dedupe, the
    same duplicate-suppression applied to a reseeded Topic (Req 7.7).

    Args:
        signals: Per-Clip Watch_Quality signals, each carrying its parent Topic.
        recut_threshold: The Recut_Threshold (valid [0.0, 1.0]).
        minimum_views: The Minimum_Views floor (valid [1, 100]).

    Returns:
        A new list of pending reseed ``BacklogItem`` values, one per identified
        Clip, each targeting the Clip's parent Topic slug, in input order.

    Validates: Requirements 7.1, 7.3, 7.6, 7.7, 7.9
    """
    items: list[BacklogItem] = []
    for sig in signals:
        if _is_eligible(sig.watch_quality, sig.views, recut_threshold, minimum_views):
            items.append(BacklogItem(
                topic=sig.topic_slug,
                level=DEFAULT_RESEED_LEVEL,
                priority=reseed_priority(sig.watch_quality, sig.views, recut_threshold),
                status="pending",
            ))
    return items
