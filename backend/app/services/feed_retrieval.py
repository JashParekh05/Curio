"""Clip retrieval from the DB for path and discover feeds."""
import random
import logging

from app.models.schemas import Clip
from app.services.feed_scoring import _get_clip_population_stats, _compute_scores, _spread_by_source, _diversify_by_topic, DISCOVER_WEIGHTS, ENGAGEMENT_WEIGHTS
from app.services.arc_unifier import CanonicalArc
from app.services.arc_unifier_store import load_canonical_arc
from app.services.clip_ordering import order_clips_by_arc

logger = logging.getLogger(__name__)

_DISCOVER_COLS = "id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,embedding"

# Fraction of the Discover feed reserved for "your content" — clips ranked by
# the personalized (taste + interest) score. The remaining ~70% is broad
# engaging discovery ranked by hook quality + cross-user completion (see
# ENGAGEMENT_WEIGHTS), so Discover feels like a TikTok For-You page (mostly
# fresh/varied content "from everywhere") instead of a single-taste echo
# chamber. This 70% broad pool subsumes the old explicit exploration carve-out.
_PERSONALIZED_FRACTION = 0.30


def _fetch_clips_for_slug(
    db,
    slug: str,
    seen_ids: set[str] | None = None,
    limit: int = 16,
    user_avg_watch_seconds: float | None = None,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    # Discover which sections exist so we sample evenly across the curriculum.
    # Without this, ordering by created_at puts all section-0 clips first and
    # section 3 clips never appear within the limit.
    try:
        sections_res = (
            db.table("clips")
            .select("section_index")
            .eq("topic_slug", slug)
            .execute()
        )
        section_indices = sorted({r["section_index"] for r in sections_res.data if r["section_index"] is not None})
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch section indices for slug={slug}: {e}")
        section_indices = []

    clips: list[Clip] = []

    if section_indices:
        per_section = max(2, limit // len(section_indices))
        for section_idx in section_indices:
            try:
                result = (
                    db.table("clips")
                    .select("*")
                    .eq("topic_slug", slug)
                    .eq("section_index", section_idx)
                    .order("hook_score", desc=True)
                    .limit(per_section)
                    .execute()
                )
            except Exception as e:
                logger.warning(f"[feed] Failed to fetch clips for slug={slug} section={section_idx}: {e}")
                continue
            for row in result.data:
                if seen_ids and row["id"] in seen_ids:
                    continue
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    # Fallback when no section data exists yet (pipeline still running)
    if not clips:
        try:
            result = (
                db.table("clips")
                .select("*")
                .eq("topic_slug", slug)
                .order("hook_score", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch clips for slug={slug}: {e}")
            return []
        for row in result.data:
            if seen_ids and row["id"] in seen_ids:
                continue
            row.setdefault("hook_score", 0.5)
            clips.append(Clip(**row))

    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, user_avg_watch_seconds, interest_vector, taste_vector)
    arc = load_canonical_arc(slug, db)
    return _order_by_arc(clips, arc)


def _order_by_arc(clips: list[Clip], arc: CanonicalArc | None = None) -> list[Clip]:
    """Deliver a Topic's clips in one arc-ordered sequence (Req 2.1-2.7).

    Ordering is routed through the single pure core
    :func:`~app.services.clip_ordering.order_clips_by_arc`. When the Topic has a
    Canonical_Arc, clips are ordered by Canonical_Arc role ordinal ascending,
    then within a role by ``final_score`` descending and ascending clip id; any
    role-less clip sorts after every role-bearing clip (Req 2.1, 2.2, 2.4, 2.5).
    The legacy ``section_index`` / ``narrative_rank`` branch is gone: a Topic
    with a Canonical_Arc is ordered exclusively by the one arc path (Req 2.3),
    and a Topic with no arc yet (``arc is None``) falls through to role-less,
    score-ordered, stable ordering inside the same core.

    ``_spread_by_source`` is applied per role group so clips from the same
    source video do not clump within a Canonical_Arc role while preserving the
    core's arc order across roles.
    """
    from itertools import groupby

    ordered = order_clips_by_arc(clips, arc)

    # Source-spread within each contiguous role-ordinal group so clips from the
    # same source video do not clump, without disturbing the cross-role arc
    # order produced by the core. Role-less clips (arc role absent) share the
    # trailing group and are spread together.
    role_ordinal = {ar.role: ar.ordinal for ar in arc.roles} if arc is not None else {}

    def _group_key(c: Clip):
        if c.pedagogical_role is not None and c.pedagogical_role in role_ordinal:
            return (0, role_ordinal[c.pedagogical_role])
        return (1, 0)

    spread: list[Clip] = []
    for _, group in groupby(ordered, key=_group_key):
        spread.extend(_spread_by_source(list(group)))
    return spread


def _fetch_discover_clips(
    db,
    relevant_slugs: list[str],
    all_slugs: list[str],
    seen_ids: set[str],
    limit: int,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    # --- 70 / 30 composition -------------------------------------------------
    # Build ONE broad candidate pool, rank it TWO ways, and merge:
    #   * ~70% "engaging discovery" — ranked by ENGAGEMENT (hook quality +
    #     cross-user completion + recency), NOT taste-bound, so the feed is full
    #     of high-hook, widely-watched, freshly-generated clips "from everywhere"
    #     (the TikTok For-You feel). Fresh content is biased toward trusted
    #     high-production channels at ingestion (_channel_bonus), so this pool
    #     naturally surfaces Crash Course / Veritasium / OverSimplified-style hits.
    #   * ~30% "your content" — the same pool ranked by the PERSONALIZED score
    #     (taste + interest), guaranteeing taste matches a reserved slice.
    # Candidates are drawn relevant-topics-first (2:1 over broad), so a NEW user
    # whose relevant_slugs come straight from onboarding (grade + interests ->
    # e.g. high_school history -> WW2) gets a feed anchored to those topics,
    # while an established user gets broad serendipity. The library IS the cache:
    # we serve already-stored clips here and only TOP UP new ones via the bounded
    # background task, so "fresh" never costs a per-request generation.
    pool: list[Clip] = []
    added: set[str] = set()

    def _collect(slug: str, per_slug: int) -> None:
        try:
            result = db.table("clips").select(_DISCOVER_COLS).eq("topic_slug", slug).limit(per_slug).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch discover clips for slug={slug}: {e}")
            return
        for row in result.data:
            if row["id"] in seen_ids or row["id"] in added:
                continue
            row.setdefault("hook_score", 0.5)
            pool.append(Clip(**row))
            added.add(row["id"])

    for slug in relevant_slugs[:8]:        # relevant/onboarding topics first
        _collect(slug, 6)
    other_slugs = [s for s in all_slugs if s not in relevant_slugs]
    random.shuffle(other_slugs)
    for slug in other_slugs[:8]:           # broad serendipity ("from everywhere")
        _collect(slug, 4)

    if not pool:
        return []

    pop_stats = _get_clip_population_stats(db, [c.id for c in pool])

    # Personalized ranking (taste/interest-first) — captured BEFORE the second
    # scoring pass overwrites final_score on the shared clip objects.
    _compute_scores(pool, pop_stats, None, interest_vector=interest_vector,
                    taste_vector=taste_vector, weights=DISCOVER_WEIGHTS)
    personalized_rank = sorted(pool, key=lambda c: c.final_score or 0.0, reverse=True)

    # Engagement ranking (hook + completion + recency, broad — NOT taste-bound).
    _compute_scores(pool, pop_stats, None, interest_vector=interest_vector,
                    taste_vector=taste_vector, weights=ENGAGEMENT_WEIGHTS)
    engaging_rank = sorted(pool, key=lambda c: c.final_score or 0.0, reverse=True)

    # Reserve the personalized 30% (best taste matches), fill the rest from the
    # engagement ranking (disjoint), then interleave so "your content" is
    # sprinkled through the feed instead of front- or back-loaded.
    personalized_n = min(len(pool), max(1, round(limit * _PERSONALIZED_FRACTION)))
    personalized_pick = personalized_rank[:personalized_n]
    picked = {c.id for c in personalized_pick}
    engaging_pick = [c for c in engaging_rank if c.id not in picked][: max(0, limit - len(personalized_pick))]

    merged = _ratio_interleave(engaging_pick, personalized_pick)
    # Diversify by topic so the feed never clumps same-topic clips in a row
    # (TikTok's "no two in a row", applied to topic). Preserves the 70/30 counts.
    return _diversify_by_topic(merged[:limit])


def _ratio_interleave(primary: list[Clip], secondary: list[Clip]) -> list[Clip]:
    """Evenly merge a ``primary`` (engaging, ~70%) and ``secondary``
    (personalized, ~30%) ranked list, preserving each list's internal order and
    total counts while spreading the smaller list across the larger so
    personalization is sprinkled throughout rather than blocked at one end.

    Placement is even by construction: at each output position we place a
    ``secondary`` item iff fewer have been placed than the proportional target
    ``round((pos + 1) * len(secondary) / total)``, else a ``primary`` item. The
    leading slot goes to ``primary`` (Discover is engagement-first).
    """
    if not primary:
        return list(secondary)
    if not secondary:
        return list(primary)
    total = len(primary) + len(secondary)
    out: list[Clip] = []
    pi = si = 0
    for pos in range(total):
        target_si = round((pos + 1) * len(secondary) / total)
        if si < target_si and si < len(secondary):
            out.append(secondary[si]); si += 1
        elif pi < len(primary):
            out.append(primary[pi]); pi += 1
        else:
            out.append(secondary[si]); si += 1
    return out
