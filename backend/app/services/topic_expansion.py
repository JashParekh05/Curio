"""Endless within-topic expansion.

Most viewers never finish a topic's 4-beat arc, so the worst thing for an
ENGAGED viewer is hitting the end. When someone is still watching a topic and
running low on unseen clips, generate fresh "deeper/sideways" angles on the
SAME subject and append them as additional beats — so the subject feels
bottomless instead of terminating.

Mirrors path_extension, but expands one topic in place rather than adding a new
topic to the path. Best-effort throughout; failures are logged and skipped.
"""
import time
import logging

from app.db.supabase import get_client

logger = logging.getLogger(__name__)

# Trigger when an engaged viewer's unseen clips for a topic fall to/below this.
EXPAND_WHEN_UNSEEN_AT_OR_BELOW = 3
# Minimum completion rate on the topic to treat the viewer as "engaged".
ENGAGED_COMPLETION = 0.5
# How many fresh angles to add per expansion, and a ceiling so a single topic
# can't grow without bound.
ANGLES_PER_EXPANSION = 2
MAX_SECTIONS = 12

_EXPAND_COOLDOWN_S = 30
_expanding: dict[str, float] = {}


def _is_expansion_candidate(unseen_clip_count: int, completion_rate: float,
                            is_generating: bool) -> bool:
    """Pure decision: should this topic be expanded right now?

    True only when the topic is NOT already generating, the viewer is engaged
    (completion_rate at or above ENGAGED_COMPLETION), and they're running low on
    unseen clips (1..EXPAND_WHEN_UNSEEN_AT_OR_BELOW). Zero unseen means the topic
    is empty/handled by self-heal, not expansion. This is deliberately separate
    from _should_expand_topic, which has a throttling side effect.
    """
    if is_generating:
        return False
    if not (0 < unseen_clip_count <= EXPAND_WHEN_UNSEEN_AT_OR_BELOW):
        return False
    return completion_rate >= ENGAGED_COMPLETION


def _should_expand_topic(session_id: str, slug: str) -> bool:
    """Per-(session, topic) throttle so the 4s feed poll can't queue a storm of
    expansions. Best-effort in-memory; fine to reset on restart."""
    key = f"{session_id}:{slug}"
    now = time.time()
    last = _expanding.get(key, 0)
    if now - last < _EXPAND_COOLDOWN_S:
        return False
    _expanding[key] = now
    return True


def _expand_topic(slug: str, name: str) -> None:
    """Background: plan fresh angles for `slug` and append them as new beats.

    Continues section_index from the topic's current max, runs the pipeline for
    each new angle (without clearing existing clips), then re-runs the story
    pass so the new clips fold into the narrative order.
    """
    from app.agents.pipeline_agent import run_pipeline
    from app.agents.section_planner import plan_extension_sections
    from app.services.story import run_story_pass

    db = get_client()

    # Existing beats: how far the arc goes and what's already covered.
    try:
        rows = (
            db.table("topic_sections")
            .select("section_index,title")
            .eq("topic_slug", slug)
            .order("section_index")
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[expand] failed to read sections for '{slug}': {exc}")
        return

    existing = rows.data or []
    existing_titles = [r.get("title", "") for r in existing]
    max_idx = max((r.get("section_index", -1) for r in existing), default=-1)
    start_index = max(max_idx + 1, 4)  # depth beats start at 4 at the earliest
    if start_index >= MAX_SECTIONS:
        logger.info(f"[expand] '{slug}' already at max depth ({start_index}); skipping")
        return

    count = min(ANGLES_PER_EXPANSION, MAX_SECTIONS - start_index)
    new_sections = plan_extension_sections(name, existing_titles, start_index, count)
    if not new_sections:
        return

    arc_titles = existing_titles + [s["title"] for s in new_sections]
    for section in new_sections:
        # Persist the new beat so future expansions know it's covered.
        try:
            db.table("topic_sections").upsert(
                {"topic_slug": slug, **{k: section[k] for k in ("section_index", "title", "description", "search_query")}},
                on_conflict="topic_slug,section_index",
            ).execute()
        except Exception as exc:
            logger.warning(f"[expand] failed to store section {section['section_index']} for '{slug}': {exc}")
        try:
            run_pipeline(
                slug, name,
                section["search_query"], section["section_index"],
                False,  # never clear existing clips when expanding
                section.get("title"), section.get("description"), arc_titles,
            )
        except Exception as exc:
            logger.warning(f"[expand] pipeline failed for '{slug}' section {section['section_index']}: {exc}")

    try:
        run_story_pass(slug, name)
    except Exception as exc:
        logger.warning(f"[expand] story pass failed for '{slug}': {exc}")

    logger.info(f"[expand] '{slug}' grew by {len(new_sections)} depth beats from index {start_index}")
