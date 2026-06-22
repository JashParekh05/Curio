"""Story-quality pass: an LLM "narrative director" that scores how well a set
of clips flows as a single story and reorders them for maximum retention.

This is the invisible framework that sits on top of the section arc. The
section planner gives the macro spine (hook → what → how → outcomes); this pass
operates over the actual segmented clips and answers a different question: does
this *sequence* hook you and pull you clip-to-clip the way a good TikTok thread
does, regardless of subject (math, history, biology — doesn't matter)?

Design mirrors section_planner: lazy LLM client, strict JSON parsing, and
best-effort orchestration — any failure returns the clips in their original
order so this can never block or corrupt feed generation. No DB access here, so
it is fully unit-testable by mocking the single LLM stage.
"""
import json
import logging
import os

from app.services import llm

logger = logging.getLogger(__name__)

# Below this overall story score we log a warning so weak topics are visible for
# tuning. It does NOT block delivery — a mediocre story still beats no story.
STORY_PASS_SCORE = 0.7

# Transcript chars fed to the judge per clip. Enough to judge the hook + through
# line without blowing up the prompt when a topic has many clips.
_TRANSCRIPT_BUDGET = 400

# The domain-agnostic narrative rubric. This is the "framework" — the same
# scaffolding good short-form educational creators use, independent of topic.
NARRATIVE_RUBRIC = """A great clip sequence behaves like one irresistible story:
1. COLD OPEN — the first clip opens on tension, a surprising claim, or a
   question, not a definition or "in this video". It earns the next swipe.
2. OPEN LOOPS — each clip raises a question the next one answers, so curiosity
   pulls the viewer forward instead of letting them drop.
3. LOGICAL BUILD — ideas are introduced before they're used; nothing references
   a concept the viewer hasn't met yet.
4. ESCALATION — stakes or depth rise across the sequence; it doesn't plateau or
   repeat the same beat.
5. PAYOFF — the sequence resolves the tension it opened with and lands the
   "so what", leaving the viewer feeling they learned something whole.
6. COHESION — adjacent clips connect; there are no jarring topic jumps or
   redundant clips covering the same point."""


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _strip_json(raw: str) -> str:
    raw = (raw or "").strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _clip_text(c: dict) -> str:
    t = (c.get("transcript") or "").strip().replace("\n", " ")
    if len(t) > _TRANSCRIPT_BUDGET:
        t = t[:_TRANSCRIPT_BUDGET] + "…"
    return t


def _payload(clips: list[dict]) -> str:
    """Compact, index-stamped view of the clips for the judge prompt."""
    rows = []
    for i, c in enumerate(clips):
        rows.append({
            "index": i,
            "section": c.get("section_index"),
            "title": c.get("title", ""),
            "description": c.get("description", ""),
            "excerpt": _clip_text(c),
        })
    return json.dumps(rows, indent=2)


def _valid_permutation(order, n: int) -> list[int] | None:
    """Return order as a clean permutation of 0..n-1, or None if it isn't one.
    Guards against the LLM dropping, duplicating, or inventing indices."""
    if not isinstance(order, list) or len(order) != n:
        return None
    seen = set()
    out = []
    for x in order:
        try:
            idx = int(x)
        except (TypeError, ValueError):
            return None
        if idx < 0 or idx >= n or idx in seen:
            return None
        seen.add(idx)
        out.append(idx)
    return out if len(seen) == n else None


def _derive_overall(verdict: dict) -> dict:
    """Make overall_score consistent and explainable: the mean of the rubric
    dimensions. The model's free-form overall tended to drift from its own
    per-dimension scores, which made the STORY_PASS_SCORE gate unpredictable.
    Falls back to the model's value if dimensions are missing/unusable."""
    dims = verdict.get("dimensions")
    if isinstance(dims, dict):
        vals = [v for v in dims.values() if isinstance(v, (int, float))]
        if vals:
            verdict["overall_score"] = round(sum(vals) / len(vals), 4)
    return verdict


def _judge_story(topic_name: str, clips: list[dict]) -> dict:
    """Score the sequence on the narrative rubric and propose an order that
    maximizes flow. Returns parsed JSON; raises on API/parse failure (callers
    handle that). overall_score is normalized to the mean of the dimensions."""
    prompt = f"""You are a narrative director for short-form educational video, in the style of
the most addictive, bingeable explainer channels. Your job is to make a set of
clips about "{topic_name}" flow as ONE story that keeps a viewer watching.

{NARRATIVE_RUBRIC}

Here are the clips (with a stable "index"), currently in delivery order:
{_payload(clips)}

Do two things:
1. Score the CURRENT sequence on each rubric dimension from 0 to 1.
2. Propose the order (a list of the indices) that tells the strongest story.
   You MAY reorder for flow, but respect logical build: never place a clip that
   uses a concept before the clip that introduces it. Keep every index exactly
   once — do not drop, duplicate, or invent indices.

Return ONLY JSON:
{{"overall_score": 0.0,
  "dimensions": {{"cold_open": 0.0, "open_loops": 0.0, "build": 0.0, "escalation": 0.0, "payoff": 0.0, "cohesion": 0.0}},
  "order": [<indices in the best storytelling order>],
  "clips": [{{"index": 0, "issue": "<one-line note, empty if strong>"}}]}}"""
    resp = _client().chat.completions.create(
        model=llm.resolve_model(), max_tokens=900, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return _derive_overall(json.loads(_strip_json(resp.choices[0].message.content)))


def _arc_clamp(clips: list[dict]) -> list[dict]:
    """Stable-sort by section so the pedagogical arc (hook → what → how →
    outcomes) is never violated, while preserving the judge's relative order
    WITHIN each section. This keeps the story pass from putting advanced
    material before the basics."""
    def _beat(c: dict) -> int:
        s = c.get("section_index")
        return s if s is not None else 1_000_000
    return sorted(clips, key=_beat)  # Python's sort is stable


def order_for_story(topic_name: str, clips: list[dict]) -> tuple[list[dict], dict | None]:
    """Reorder clips into the strongest narrative sequence, WITHIN the section
    arc. The judge proposes a flow and scores the sequence; we then clamp the
    result to the arc so sections never go backwards (the arc is the backbone,
    the story pass is the polish). Best-effort: returns (clips, verdict). On any
    failure, returns the clips unchanged and verdict None so this never blocks
    generation.

    Fewer than 2 clips can't form a sequence, so it's a no-op.
    """
    if len(clips) < 2:
        return clips, None

    try:
        verdict = _judge_story(topic_name, clips)
    except Exception as exc:
        logger.warning(f"[story] judge failed for '{topic_name}': {exc}")
        return clips, None

    order = _valid_permutation(verdict.get("order"), len(clips))
    score = verdict.get("overall_score")
    if order is None:
        logger.warning(f"[story] '{topic_name}' judge returned an invalid order; keeping original")
        return _arc_clamp(clips), verdict

    reordered = _arc_clamp([clips[i] for i in order])
    moved = [id(c) for c in reordered] != [id(c) for c in clips]
    level = logging.WARNING if (isinstance(score, (int, float)) and score < STORY_PASS_SCORE) else logging.INFO
    logger.log(level, f"[story] '{topic_name}' score={score} reordered={moved} order={order}")
    return reordered, verdict


def run_story_pass(topic_slug: str, topic_name: str) -> dict | None:
    """Generation-time entrypoint: score a topic's stored clips as a sequence
    and persist the story_score + within-beat narrative order. Best-effort —
    any failure is logged and skipped so it never blocks topic generation.

    This is the only DB-touching function in the module; the judging and
    ordering logic above stays pure and unit-testable.
    """
    from app.db.supabase import get_client
    db = get_client()

    try:
        res = (
            db.table("clips")
            .select("id,section_index,title,description,transcript")
            .eq("topic_slug", topic_slug)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[story] failed to load clips for '{topic_slug}': {exc}")
        return None

    clips = res.data or []
    if len(clips) < 2:
        return None

    ordered, verdict = order_for_story(topic_name, clips)
    if verdict is None:
        return None

    score = verdict.get("overall_score")
    persisted = 0
    for rank, clip in enumerate(ordered):
        update: dict = {"narrative_rank": rank}
        if isinstance(score, (int, float)):
            update["story_score"] = score
        try:
            db.table("clips").update(update).eq("id", clip["id"]).execute()
            persisted += 1
        except Exception as exc:
            logger.warning(f"[story] failed to persist clip {clip.get('id')} for '{topic_slug}': {exc}")

    logger.info(f"[story] '{topic_slug}' story pass: score={score} persisted={persisted}/{len(ordered)}")
    return verdict
