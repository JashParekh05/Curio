"""Drop-off analytics — where viewers actually stop within a topic.

Most viewers never reach the end of a topic's arc, so optimizing the finale is
optimizing for a minority. This module measures the real retention curve by
beat (and narrative position) from clip_events, so ordering/segmentation
decisions can be driven by data instead of guesses.

The aggregation is a pure function (unit-tested); the DB fetch is a thin
wrapper around it.
"""
import logging

from app.services.telemetry import watch_ratio as _watch_ratio

logger = logging.getLogger(__name__)

# `_watch_ratio` is kept as the module-local name `compute_dropoff` already uses;
# it now delegates to the single shared definition in `telemetry.watch_ratio` so
# there is one watch-ratio formula. `compute_dropoff` results are unchanged
# (Req 6.5).


def compute_dropoff(clip_meta: dict[str, dict], events: list[dict]) -> list[dict]:
    """Aggregate engagement per beat (section_index).

    clip_meta: clip_id -> {"section_index": int|None, "duration_seconds": int|None}
    events:    list of {"clip_id", "watch_ms", "completed"}

    Returns one row per beat (section None sorted last), each with:
      section_index, views, completions, completion_rate,
      avg_watch_ratio, retention_vs_first (views / first-beat views).
    Beats are ordered by section_index so the list reads as a funnel.
    """
    buckets: dict[object, dict] = {}
    for e in events:
        meta = clip_meta.get(e.get("clip_id"))
        if meta is None:
            continue  # event for a clip we don't have metadata for
        sec = meta.get("section_index")
        b = buckets.setdefault(sec, {"views": 0, "completions": 0, "ratios": []})
        b["views"] += 1
        if e.get("completed"):
            b["completions"] += 1
        r = _watch_ratio(e.get("watch_ms"), meta.get("duration_seconds"))
        if r is not None:
            b["ratios"].append(r)

    if not buckets:
        return []

    # Order beats: real sections ascending, None bucket last.
    def _key(sec):
        return (1, 0) if sec is None else (0, sec)
    ordered_secs = sorted(buckets.keys(), key=_key)

    # Funnel baseline = the earliest (first) beat's view count.
    first_views = buckets[ordered_secs[0]]["views"] or 1

    rows = []
    for sec in ordered_secs:
        b = buckets[sec]
        views = b["views"]
        ratios = b["ratios"]
        rows.append({
            "section_index": sec,
            "views": views,
            "completions": b["completions"],
            "completion_rate": round(b["completions"] / views, 4) if views else 0.0,
            "avg_watch_ratio": round(sum(ratios) / len(ratios), 4) if ratios else None,
            "retention_vs_first": round(views / first_views, 4),
        })
    return rows


def topic_dropoff(topic_slug: str) -> list[dict]:
    """Fetch clip metadata + events for a topic and compute its drop-off funnel.
    Best-effort: returns [] on any DB error."""
    from app.db.supabase import get_client
    db = get_client()
    try:
        clips = (
            db.table("clips")
            .select("id,section_index,duration_seconds")
            .eq("topic_slug", topic_slug)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[analytics] failed to load clips for '{topic_slug}': {exc}")
        return []

    clip_meta = {
        r["id"]: {"section_index": r.get("section_index"), "duration_seconds": r.get("duration_seconds")}
        for r in (clips.data or [])
    }
    if not clip_meta:
        return []

    try:
        events = (
            db.table("clip_events")
            .select("clip_id,watch_ms,completed")
            .in_("clip_id", list(clip_meta.keys()))
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[analytics] failed to load events for '{topic_slug}': {exc}")
        return []

    return compute_dropoff(clip_meta, events.data or [])
