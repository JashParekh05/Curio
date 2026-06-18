"""Engagement telemetry — pure decision cores.

Curio records what learners *watch* (`clip_events`) but, until this feature,
never recorded what was *served*. The correctness-critical logic for the
telemetry feature — skip classification, watch-ratio math, journey ordering,
and rollup aggregation — lives here as pure functions: deterministic in their
inputs, with no DB, clock, or global reads. This mirrors how `compute_dropoff`
in `analytics.py` is a pure, unit-testable function wrapped by a thin I/O shell.

`watch_ratio` generalizes the watch-ratio definition previously private to
`analytics._watch_ratio`, so both modules now share one definition (Req 6.5).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.models.schemas import Clip, Impression

# The Feed_Surfaces a Clip may be served on (Req 1.7). Extensible later
# without changing the recording contract.
FEED_SURFACES: frozenset[str] = frozenset({"discover", "learn_path"})

# The Slice_Dimensions an Engagement_Rollup may group by (Req 4.4).
SLICE_DIMENSIONS: frozenset[str] = frozenset(
    {"user_id", "content_level", "pedagogical_role", "source_platform", "feed_surface"}
)

# Watch_Ratio at or below which a watched Impression is classified as a Skip
# (Req: Skip_Threshold default 0.1, valid inclusive range 0.0 to 1.0).
DEFAULT_SKIP_THRESHOLD: float = 0.1


def watch_ratio(watch_ms: int | None, duration_seconds: int | None) -> float | None:
    """Fraction of a Clip watched, clamped to the inclusive range [0.0, 1.0].

    Generalizes the watch-ratio definition shared with `analytics.compute_dropoff`
    (Req 6.5), preserving its exact behavior:

    - Returns None (undefined, distinguishable from every value in [0.0, 1.0])
      when the duration is unknown (None) or non-positive (<= 0). (Req 7.2)
    - Returns 1.0 when watched time exceeds the full Clip duration. (Req 7.3)
    - Returns 0.0 when `watch_ms` is at or below zero. (Req 7.4)
    - Otherwise returns `watch_ms / 1000 / duration_seconds`, clamped to
      [0.0, 1.0]. (Req 7.5)

    `watch_ms` is None (no measurement available) yields None, matching the
    original `analytics._watch_ratio` so `compute_dropoff` is unchanged.
    """
    if duration_seconds is None or duration_seconds <= 0:
        return None
    if watch_ms is None:
        return None
    if watch_ms <= 0:
        return 0.0
    return min(max(watch_ms / 1000.0 / duration_seconds, 0.0), 1.0)


def classify_skip(
    ratio: float | None,
    has_watch_event: bool,
    skip_threshold: float = DEFAULT_SKIP_THRESHOLD,
) -> bool:
    """Classify a Journey_Entry as a Skip.

    Returns True (skip) when there is no Watch_Event, or when the Watch_Ratio is
    defined and at or below `skip_threshold`. Returns False only when the
    Watch_Ratio is defined and strictly above the threshold. Pure. (Req 3.5,
    3.6, 3.7)
    """
    if not has_watch_event:
        return True
    if ratio is None:
        return True
    return ratio <= skip_threshold


def _clip_field(clip: Clip | dict, name: str) -> Any:
    """Read a served-context field from a Clip object or a dict, defensively.

    Clips reaching the serve hook may be `Clip` pydantic models or raw row dicts;
    a field absent on either shape reads as None so the Impression records null
    for it (Req 1.5).
    """
    if isinstance(clip, dict):
        return clip.get(name)
    return getattr(clip, name, None)


def _default_topic_slug_of(clip: Clip | dict) -> str | None:
    """Default Served_Context topic_slug accessor: read `topic_slug` defensively."""
    return _clip_field(clip, "topic_slug")


def _to_utc_iso(served_at: datetime) -> str:
    """Render a serve-time datetime as a UTC ISO-8601 string (Req 1.6).

    A timezone-aware datetime is converted to UTC; a naive datetime is treated as
    already being UTC. The result is the canonical ISO-8601 representation stored
    on the Impression's `served_at`.
    """
    if served_at.tzinfo is None:
        served_at = served_at.replace(tzinfo=timezone.utc)
    return served_at.astimezone(timezone.utc).isoformat()


def assign_positions(clip_ids: list[str]) -> list[int]:
    """Assign Feed_Position values to a served, ordered set of Clips (Req 1.4).

    Returns ``[0, 1, ..., n-1]`` matching serve order: consecutive integers
    starting at 0, with no duplicate and no gap, one per Clip. Pure.
    """
    return list(range(len(clip_ids)))


def build_impressions(
    clips: list[Clip | dict],
    *,
    feed_surface: str,
    session_id: str | None,
    user_id: str | None,
    served_at: datetime,
    topic_slug_of: Callable[[Clip | dict], str | None] = _default_topic_slug_of,
) -> list[Impression]:
    """Build one Impression per served Clip, in serve order. Pure.

    Produces exactly one Impression per Clip in the order served, assigning
    Feed_Position via `assign_positions` (consecutive from 0, no duplicate, no
    gap, order-preserving — Req 1.4) and snapshotting the Served_Context
    (`pedagogical_role`, `content_level`, `source_platform`, `topic_slug`) from
    each Clip, recording null for any field absent on the Clip (Req 1.3, 1.5).
    The Clip identifier, Session identifier, resolved User identifier, and
    Feed_Surface are copied from the inputs (Req 1.2); `user_id` of None is passed
    through unchanged (Req 1.9). The serve time is the injected `served_at`,
    stored as a UTC ISO-8601 string (Req 1.6) — the clock is injected so this
    function stays pure.

    Raises ValueError when `feed_surface` is not an accepted Feed_Surface, so the
    caller records nothing for that serve (Req 1.7, 1.8).
    """
    if feed_surface not in FEED_SURFACES:
        raise ValueError(f"Unsupported feed_surface: {feed_surface!r}")

    served_at_iso = _to_utc_iso(served_at)
    positions = assign_positions([_clip_field(clip, "id") for clip in clips])

    impressions: list[Impression] = []
    for clip, position in zip(clips, positions):
        impressions.append(
            Impression(
                clip_id=_clip_field(clip, "id"),
                session_id=session_id,
                user_id=user_id,
                feed_surface=feed_surface,
                feed_position=position,
                pedagogical_role=_clip_field(clip, "pedagogical_role"),
                content_level=_clip_field(clip, "content_level"),
                source_platform=_clip_field(clip, "source_platform"),
                topic_slug=topic_slug_of(clip),
                served_at=served_at_iso,
            )
        )
    return impressions


def _parse_ts(value: Any) -> datetime:
    """Parse a recorded timestamp to a timezone-aware UTC datetime for ordering.

    Accepts a `datetime` or an ISO-8601 string (with a trailing ``Z`` or an
    explicit offset). A naive datetime is treated as already UTC. ``None`` or an
    unparseable value sorts earliest (UTC `datetime.min`) so it never wins a
    latest-timestamp comparison and never disturbs deterministic journey order.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    else:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _watch_event_recency_key(event: dict) -> tuple:
    """Deterministic 'latest wins' key for joining Watch_Events (Req 3.4).

    Primary key is the recorded timestamp (`created_at`). When several Watch_Events
    for the same Clip and Session share an identical timestamp, the remaining
    fields break the tie deterministically so the selected event is independent of
    input order (Req 7.6).
    """
    return (
        _parse_ts(event.get("created_at")),
        event.get("watch_ms") or 0,
        event.get("replay_count") or 0,
        bool(event.get("completed")),
        event.get("feedback") or "",
    )


@dataclass(frozen=True)
class JourneyEntry:
    """One element of a Session_Journey describing a single served Clip.

    Read-model shape returned by `build_session_journey`: the Impression's
    Feed_Position, Feed_Surface, and Served_Context joined with the engagement
    fields of the latest matching Watch_Event, plus the Skip classification.
    `served_at` is the parsed UTC serve time; `watch_ratio` is None when undefined
    or when no Watch_Event matched.
    """

    impression_id: str
    clip_id: str
    feed_surface: str
    feed_position: int
    served_at: datetime
    served_context: dict      # pedagogical_role, content_level, source_platform, topic_slug
    watch_ms: int             # 0 when no Watch_Event
    watch_ratio: float | None  # None when undefined / no Watch_Event
    completed: bool           # False when no Watch_Event
    replay_count: int         # 0 when no Watch_Event
    feedback: str | None      # None when no Watch_Event
    skipped: bool


def build_session_journey(
    impressions: list[Impression],
    watch_events: list[dict],
    *,
    skip_threshold: float = DEFAULT_SKIP_THRESHOLD,
) -> list[JourneyEntry]:
    """Reconstruct an ordered Session_Journey from Impressions and Watch_Events. Pure.

    Produces exactly one JourneyEntry per Impression (empty input -> empty output;
    Req 3.1, 3.9, 3.10), ordered by ascending serve time, then ascending
    Feed_Position, then ascending Impression identifier as the final deterministic
    tie-break (Req 3.2). The ordering depends only on the Impression set, not on
    input order, so per-session and per-user (multi-session) journeys are
    deterministic and permutation-invariant (Req 3.8, 7.6) — a per-user journey is
    built by passing the Impressions of all the User's Sessions.

    Each entry copies Feed_Position, Feed_Surface, and Served_Context from its
    Impression (Req 3.3). Watch_Events are joined by `(clip_id, session_id)`; when
    several match, the one with the latest recorded timestamp wins (Req 3.4) and
    supplies `watch_ms`, Watch_Ratio (`watch_ratio()` over the event's
    `duration_seconds`), Completion, `replay_count`, and `feedback`. When no
    Watch_Event matches, the entry has `watch_ms` 0, undefined Watch_Ratio,
    `completed` False, `replay_count` 0, `feedback` None, and is classified as a
    Skip (Req 3.5); `classify_skip` decides the Skip flag in every case (Req 3.6,
    3.7).

    Watch_Event dicts carry `clip_id`, `session_id`, `watch_ms`, `completed`,
    `replay_count`, `feedback`, `created_at`, and the Clip's `duration_seconds`
    (joined at fetch time, mirroring how `compute_dropoff` obtains duration), the
    last being the only source of the duration the Watch_Ratio needs.
    """
    latest_by_key: dict[tuple, dict] = {}
    for event in watch_events:
        key = (event.get("clip_id"), event.get("session_id"))
        current = latest_by_key.get(key)
        if current is None or _watch_event_recency_key(event) > _watch_event_recency_key(current):
            latest_by_key[key] = event

    entries: list[JourneyEntry] = []
    for imp in impressions:
        served_context = {
            "pedagogical_role": imp.pedagogical_role,
            "content_level": imp.content_level,
            "source_platform": imp.source_platform,
            "topic_slug": imp.topic_slug,
        }
        match = latest_by_key.get((imp.clip_id, imp.session_id))
        if match is None:
            ratio = None
            entries.append(
                JourneyEntry(
                    impression_id=imp.id or "",
                    clip_id=imp.clip_id,
                    feed_surface=imp.feed_surface,
                    feed_position=imp.feed_position,
                    served_at=_parse_ts(imp.served_at),
                    served_context=served_context,
                    watch_ms=0,
                    watch_ratio=None,
                    completed=False,
                    replay_count=0,
                    feedback=None,
                    skipped=classify_skip(ratio, has_watch_event=False, skip_threshold=skip_threshold),
                )
            )
        else:
            watch_ms = match.get("watch_ms") or 0
            ratio = watch_ratio(watch_ms, match.get("duration_seconds"))
            entries.append(
                JourneyEntry(
                    impression_id=imp.id or "",
                    clip_id=imp.clip_id,
                    feed_surface=imp.feed_surface,
                    feed_position=imp.feed_position,
                    served_at=_parse_ts(imp.served_at),
                    served_context=served_context,
                    watch_ms=watch_ms,
                    watch_ratio=ratio,
                    completed=bool(match.get("completed")),
                    replay_count=match.get("replay_count") or 0,
                    feedback=match.get("feedback"),
                    skipped=classify_skip(ratio, has_watch_event=True, skip_threshold=skip_threshold),
                )
            )

    entries.sort(key=lambda j: (j.served_at, j.feed_position, j.impression_id))
    return entries


# The virtual Slice_Dimension that groups by inferred Seeding_Origin rather than
# by a stored Impression field (Req 4.10). It is accepted by `compute_rollup`
# alongside the five stored SLICE_DIMENSIONS.
SEEDING_ORIGIN_DIMENSION: str = "seeding_origin"


def classify_seeding_origin(clip_metadata: dict) -> str:
    """Infer a Clip's Seeding_Origin from existing Clip metadata only (Req 4.10).

    Returns exactly one of ``"cold_start_seeded"``, ``"on_demand"``, or
    ``"unknown"`` — total over those three labels for any input. Pure.

    Heuristic. Seeding_Origin must be inferred from metadata that already exists
    on a Clip; the feature adds no new field to mark how a Clip entered the
    library (see the requirements' explicit assumption). Cold-start-seeded Clips
    and on-demand Clips both flow through the identical content pipeline and
    produce identical Clip rows, so there is no definitive stored signal that
    distinguishes them today. Accordingly this classifier:

    - honors an explicit canonical marker ``clip_metadata["seeding_origin"]`` when
      it is already one of the two determinate labels (forward-compatible: if a
      seeding marker is added later, no caller change is needed);
    - honors boolean markers (``cold_start_seeded`` / ``is_cold_start_seeded`` and
      ``on_demand`` / ``is_on_demand``) when present and True;
    - otherwise returns ``"unknown"`` — the documented default whenever the origin
      is not determinable from the metadata present (including a non-dict input).
    """
    if not isinstance(clip_metadata, dict):
        return "unknown"

    marker = clip_metadata.get("seeding_origin")
    if marker in ("cold_start_seeded", "on_demand"):
        return marker

    if clip_metadata.get("cold_start_seeded") is True or clip_metadata.get("is_cold_start_seeded") is True:
        return "cold_start_seeded"
    if clip_metadata.get("on_demand") is True or clip_metadata.get("is_on_demand") is True:
        return "on_demand"

    return "unknown"


def _default_seeding_origin_of(imp: Impression) -> str:
    """Default `seeding_origin_of`: classify from the Impression's Served_Context.

    Builds the metadata dict from the fields an Impression actually carries and
    delegates to `classify_seeding_origin`. With today's metadata this resolves to
    ``"unknown"``; a caller that has joined richer Clip rows can pass its own
    `seeding_origin_of` to `compute_rollup` for a determinate classification.
    """
    return classify_seeding_origin(
        {
            "pedagogical_role": imp.pedagogical_role,
            "content_level": imp.content_level,
            "source_platform": imp.source_platform,
            "topic_slug": imp.topic_slug,
        }
    )


@dataclass(frozen=True)
class RollupGroup:
    """One group of an Engagement_Rollup over a single Slice_Dimension (Req 4.3).

    `key` is the Slice_Dimension value the group is keyed by — None for the single
    dedicated null-key group (Req 4.9). `count` is the number of Impressions in the
    group (>= 0). `skip_rate` and `completion_rate` are in the inclusive range
    [0.0, 1.0] (Req 4.6, 4.7, 4.11). `avg_watch_ratio` is the mean of the group's
    defined Watch_Ratios, or None when no Impression in the group has a defined
    Watch_Ratio (Req 4.8).
    """

    key: object
    count: int
    skip_rate: float
    completion_rate: float
    avg_watch_ratio: float | None


def _impression_engagement(
    imp: Impression,
    latest_by_key: dict[tuple, dict],
    skip_threshold: float,
) -> tuple[bool, bool, float | None]:
    """Derive ``(skipped, completed, watch_ratio)`` for one Impression.

    Joins the Impression to its latest matching Watch_Event exactly as
    `build_session_journey` does — by ``(clip_id, session_id)``, latest recorded
    timestamp winning — and reuses the shared `watch_ratio` / `classify_skip`
    helpers, so a rollup classifies each Impression identically to the journey.
    """
    match = latest_by_key.get((imp.clip_id, imp.session_id))
    if match is None:
        skipped = classify_skip(None, has_watch_event=False, skip_threshold=skip_threshold)
        return skipped, False, None

    watch_ms = match.get("watch_ms") or 0
    ratio = watch_ratio(watch_ms, match.get("duration_seconds"))
    completed = bool(match.get("completed"))
    skipped = classify_skip(ratio, has_watch_event=True, skip_threshold=skip_threshold)
    return skipped, completed, ratio


def compute_rollup(
    impressions: list[Impression],
    watch_events: list[dict],
    slice_dimension: str,
    *,
    skip_threshold: float = DEFAULT_SKIP_THRESHOLD,
    seeding_origin_of: Callable[[Impression], str] | None = None,
) -> list[RollupGroup]:
    """Aggregate Impressions into per-Slice_Dimension Engagement_Rollup groups. Pure.

    Groups the input Impressions by `slice_dimension` — one of the five stored
    SLICE_DIMENSIONS (``user_id``, ``content_level``, ``pedagogical_role``,
    ``source_platform``, ``feed_surface``) or the virtual ``seeding_origin``
    dimension — producing one RollupGroup per distinct key present (Req 4.1).
    Impressions whose stored key is null collect into a single dedicated null-key
    group, separate from every non-null group (Req 4.9); for the ``seeding_origin``
    dimension the key is the label returned by `seeding_origin_of` (defaulting to
    `classify_seeding_origin` over the Impression's Served_Context), which is always
    one of the three labels, so that dimension has no null group (Req 4.10).

    For each group, per-Impression Skip and Completion are derived by joining
    Watch_Events the same way `build_session_journey` does (latest-wins on
    ``(clip_id, session_id)``), and the group reports: `count`,
    ``skip_rate = skipped / count``, ``completion_rate = completed / count`` (each
    in [0.0, 1.0], with ``skipped <= count`` and ``completed <= count``), and
    `avg_watch_ratio` = the mean of the group's defined Watch_Ratios, or None when
    none is defined (Req 4.3, 4.6, 4.7, 4.8, 4.11, 7.8). The group counts sum to
    ``len(impressions)`` (Req 7.7); empty input yields no groups (Req 4.2). Groups
    are returned in a deterministic order (non-null keys first, sorted; the null
    group last).

    Raises ValueError when `slice_dimension` is neither a supported Slice_Dimension
    nor ``seeding_origin``, so the caller produces no Engagement_Rollup (Req 4.5).
    """
    if slice_dimension not in SLICE_DIMENSIONS and slice_dimension != SEEDING_ORIGIN_DIMENSION:
        raise ValueError(f"Unsupported slice_dimension: {slice_dimension!r}")

    if not impressions:
        return []

    origin_of = seeding_origin_of or _default_seeding_origin_of

    # Latest-wins Watch_Event index, identical to build_session_journey's join.
    latest_by_key: dict[tuple, dict] = {}
    for event in watch_events:
        key = (event.get("clip_id"), event.get("session_id"))
        current = latest_by_key.get(key)
        if current is None or _watch_event_recency_key(event) > _watch_event_recency_key(current):
            latest_by_key[key] = event

    # Accumulate per group: [count, skipped, completed, [defined ratios]].
    groups: dict[object, list] = {}
    for imp in impressions:
        if slice_dimension == SEEDING_ORIGIN_DIMENSION:
            group_key: object = origin_of(imp)
        else:
            group_key = getattr(imp, slice_dimension)

        skipped, completed, ratio = _impression_engagement(imp, latest_by_key, skip_threshold)

        acc = groups.get(group_key)
        if acc is None:
            acc = [0, 0, 0, []]
            groups[group_key] = acc
        acc[0] += 1
        if skipped:
            acc[1] += 1
        if completed:
            acc[2] += 1
        if ratio is not None:
            acc[3].append(ratio)

    result: list[RollupGroup] = []
    for group_key, (count, skipped, completed, ratios) in groups.items():
        avg_ratio = sum(ratios) / len(ratios) if ratios else None
        result.append(
            RollupGroup(
                key=group_key,
                count=count,
                skip_rate=skipped / count,
                completion_rate=completed / count,
                avg_watch_ratio=avg_ratio,
            )
        )

    # Deterministic order: non-null keys sorted by their string form, null last.
    result.sort(key=lambda g: (g.key is None, str(g.key)))
    return result
