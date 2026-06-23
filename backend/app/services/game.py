"""Game_Service: adaptive learning game logic.

This module is the self-contained Game_Service for the Adaptive Learning Game.
It holds (a) the deterministic banding/grading/selection helpers, (b) the five
verbatim-prompt LLM functions, and (c) thin stateless orchestration the router
calls.

It imports ONLY leaf services (`llm`, `quiz`, `youtube`) and never the LangGraph
pipeline, coherence machinery, arc-assembler, or quota-pool internals
(Req 13.7, 22.8).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from app.db import supabase  # leaf DB accessor (Req 16.2, 22) — best-effort only
from app.services import llm, quiz, youtube  # leaf services only (Req 22)

logger = logging.getLogger(__name__)

# Game_Model: gpt-4o for ALL game LLM calls. Resolves via the centralized
# llm.resolve_model() whose default is gpt-4o; never gpt-4o-mini (Req 22.1).
MODEL = llm.resolve_model()

# Score_Band: the three deterministic outcomes computed in code (Req 3).
Band = Literal["DESCEND", "REINFORCE", "CLIMB"]


def band(score_pct: float) -> Band:
    """Compute the deterministic Score_Band for a score fraction.

    Total over the inclusive range ``[0.0, 1.0]`` and deterministic: identical
    inputs always yield the identical band.

    - ``DESCEND``   when ``score_pct < 0.40``                  (Req 3.2)
    - ``REINFORCE`` when ``0.40 <= score_pct <= 0.70``         (Req 3.3)
    - ``CLIMB``     when ``score_pct > 0.70``                  (Req 3.4)

    The band is computed here in code and passed into ``decide_next``; the LLM
    never chooses the band (Req 3.5).
    """
    if score_pct < 0.40:
        return "DESCEND"
    if score_pct <= 0.70:
        return "REINFORCE"
    return "CLIMB"


@dataclass(frozen=True)
class GradeResult:
    """Deterministic outcome of grading a quiz, per concept (Req 4).

    - ``score_pct``: ``correct / total`` rounded to 4 decimal places, in the
      inclusive range ``[0.0, 1.0]`` (Req 3.1, 4.6).
    - ``missed_concepts`` / ``correct_concepts``: a partition of every
      Concept_Tag in the quiz — disjoint and jointly covering — where a tag is
      *missed* if any question carrying it was answered incorrectly and
      *correct* only when every question carrying it was answered correctly
      (Req 4.1, 4.2, 4.3). Both are ordered by first appearance for determinism.
    - ``correct`` / ``total``: the raw counts the score fraction derives from.
    """

    score_pct: float
    missed_concepts: list[str]
    correct_concepts: list[str]
    correct: int
    total: int


def grade_quiz(questions: list[dict], answers: list[int]) -> GradeResult:
    """Grade a quiz deterministically and localize gaps per Concept_Tag.

    Each entry in ``questions`` is an MCQ dict carrying a ``concept_tag`` and a
    ``correct_index``; ``answers`` holds the chosen option index per question
    (positional). Grading reuses :func:`quiz.grade`, so the same selected option
    for the same question always yields the identical correct/incorrect result
    (Req 4.5). A missing answer for a question counts as incorrect.

    The Concept_Tags are partitioned into ``missed_concepts`` and
    ``correct_concepts`` such that the two sets are disjoint and jointly cover
    every tag in the quiz (Req 4.1): a tag is classified as missed when at least
    one question carrying it is wrong (Req 4.2) and as correct only when every
    question carrying it is right (Req 4.3). ``score_pct`` is
    ``correct / total`` rounded to 4 decimal places, in ``[0, 1]`` (Req 4.6).

    A quiz with zero questions cannot be banded, so it is rejected with a
    ``ValueError`` rather than producing a partial result (Req 3.7, 4.7).
    """
    total = len(questions)
    if total == 0:
        raise ValueError("cannot grade a zero-question quiz: no questions provided")

    correct_count = 0
    # Ordered by first appearance: True while every question for the tag has
    # been correct so far, flipped to False the moment any one is wrong.
    tag_all_correct: dict[str, bool] = {}

    for i, question in enumerate(questions):
        tag = str((question or {}).get("concept_tag") or "")
        chosen = answers[i] if i < len(answers) else None
        is_correct, _ = quiz.grade(question, chosen)
        if is_correct:
            correct_count += 1
        if tag not in tag_all_correct:
            tag_all_correct[tag] = True
        if not is_correct:
            tag_all_correct[tag] = False

    missed_concepts = [tag for tag, ok in tag_all_correct.items() if not ok]
    correct_concepts = [tag for tag, ok in tag_all_correct.items() if ok]
    score_pct = round(correct_count / total, 4)

    return GradeResult(
        score_pct=score_pct,
        missed_concepts=missed_concepts,
        correct_concepts=correct_concepts,
        correct=correct_count,
        total=total,
    )


# Short-explainer duration ranges for clip selection (Req 10.5).
IDEAL_MIN_SECONDS = 60
IDEAL_MAX_SECONDS = 180
ACCEPTABLE_MAX_SECONDS = 600
# Target duration used when no clip falls in either range (Req 10.7).
TARGET_SECONDS = 180


def to_embed_url(video_id: str) -> str:
    """Return the canonical YouTube embed URL for a video id (Req 10.3).

    Always ``https://www.youtube.com/embed/{video_id}`` — the form
    ``ReelPlayer`` recognizes as a YouTube embed.
    """
    return f"https://www.youtube.com/embed/{video_id}"


def _duration(clip: dict) -> int:
    """Best-effort ``duration_seconds`` for a clip dict (defaults to 0)."""
    try:
        return int((clip or {}).get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return 0


def _view_count(clip: dict) -> int:
    """Best-effort ``view_count`` for a clip dict (defaults to 0)."""
    try:
        return int((clip or {}).get("view_count") or 0)
    except (TypeError, ValueError):
        return 0


def _has_caption(clip: dict) -> bool:
    """Best-effort ``has_caption`` flag for a clip dict (defaults to False)."""
    return bool((clip or {}).get("has_caption"))


# Lead-in / filler words that carry no topical signal, stripped before scoring a
# clip's relevance to a node so the match keys on the real subject terms.
_CLIP_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with",
        "how", "what", "why", "is", "are", "do", "does", "your", "you", "about",
        "learn", "learning", "teach", "teaching", "me", "into", "intro",
        "introduction", "basics", "basic", "understand", "understanding",
        "explained", "explainer", "guide", "tutorial", "overview", "concept",
        "concepts", "this", "that", "from", "as", "it", "its",
    }
)


def _topic_tokens(text: str) -> set[str]:
    """Lowercased subject tokens of ``text`` (>=3 chars, stopwords removed).

    Used to score how on-topic a clip is for a node/goal; filler like "learn",
    "intro", "explained" is dropped so only the real subject words remain.
    """
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _CLIP_STOPWORDS}


def relevant_clips(clips: list[dict], node: str, goal: str) -> list[dict]:
    """Keep only clips whose title/description are on-topic for the node (Req 10).

    ``select_clip`` ranks purely by duration / captions / views, so an off-topic
    but popular clip could win — e.g. an "imperialism" node landing on an
    unrelated history video. This pre-filter drops candidates whose title +
    description share no subject term with the node (the goal's terms count as a
    weaker secondary signal), keeping the result ON-TOPIC before duration
    ranking. Order is preserved so ``select_clip`` stays deterministic.

    Safe by construction: if the filter would remove every clip (e.g. sparse
    metadata), it returns the original list unchanged so a clip is never lost —
    a possibly-loose clip still beats no clip (the checkpoint stays soft).
    """
    node_tokens = _topic_tokens(node)
    goal_tokens = _topic_tokens(goal)
    if not node_tokens and not goal_tokens:
        return clips

    kept: list[dict] = []
    for clip in clips or []:
        c = clip or {}
        hay = _topic_tokens(f"{c.get('title', '')} {c.get('description', '')}")
        node_hits = len(node_tokens & hay)
        goal_hits = len(goal_tokens & hay)
        # On-topic when the clip shares a node term, or (when the node yields no
        # usable terms) a goal term.
        if node_hits > 0 or (not node_tokens and goal_hits > 0):
            kept.append(clip)
    # Never strand the node with no clip when nothing matched.
    return kept or clips


def select_clip(clips: list[dict]) -> dict | None:
    """Pick the single best clip from candidates, deterministically (Req 10.5, 10.7).

    Ordered rules:

    1. Prefer clips whose ``duration_seconds`` falls in the ideal range
       60–180s inclusive (Req 10.5a).
    2. Otherwise, consider clips whose ``duration_seconds`` falls in the
       acceptable range >180–600s inclusive (Req 10.5b).
    3. Among the candidates surviving the duration filter, prefer clips where
       ``has_caption`` is true over false (Req 10.5c).
    4. If a tie remains, take the highest ``view_count`` (Req 10.5d).

    When no clip falls in either range, pick the clip whose ``duration_seconds``
    is closest to 180s, breaking any remaining tie by highest ``view_count``
    (Req 10.7).

    Returns ``None`` for an empty input. Selection is deterministic for a given
    list: equal-ranked clips fall back to their original order via a stable sort.
    """
    if not clips:
        return None

    ideal = [c for c in clips if IDEAL_MIN_SECONDS <= _duration(c) <= IDEAL_MAX_SECONDS]
    acceptable = [
        c for c in clips if IDEAL_MAX_SECONDS < _duration(c) <= ACCEPTABLE_MAX_SECONDS
    ]

    candidates = ideal or acceptable
    if candidates:
        # Prefer captioned clips (rule c), then highest view_count (rule d).
        # min() is stable, so equal keys keep original list order (deterministic).
        return min(candidates, key=lambda c: (not _has_caption(c), -_view_count(c)))

    # No clip in either range: closest to the target duration, tie-break by
    # highest view_count (Req 10.7).
    return min(
        clips,
        key=lambda c: (abs(_duration(c) - TARGET_SECONDS), -_view_count(c)),
    )


def _filter_valid_questions(candidates: list[dict]) -> list[dict]:
    """Keep only candidate question dicts that pass ``quiz._validate_question``.

    Each candidate is run through the reused Quiz_Generator validation
    (:func:`quiz._validate_question`), the hard MCQ gate: 2-4 options, exactly
    one in-range ``correct_index``, and a non-empty prompt and explanation.
    Candidates that fail validation are discarded; candidates that pass are
    kept (Req 11.4). Used later by ``generate_quiz`` to enforce that only valid
    MCQs survive checkpoint generation (Property 8).

    The validation normalizes each kept question (trimmed prompt/options/
    explanation, coerced ``correct_index``); this helper preserves the
    ``concept_tag`` and ``level`` from the original candidate when present, since
    downstream node quizzes carry a Concept_Tag per question (Req 11.3). Order is
    preserved.
    """
    kept: list[dict] = []
    for candidate in candidates or []:
        validated = quiz._validate_question(candidate)
        if validated is None:
            continue
        # Preserve the Concept_Tag (and level) the validation drops, since
        # downstream node quizzes tag each question by concept (Req 11.3).
        if isinstance(candidate, dict):
            concept_tag = candidate.get("concept_tag")
            if concept_tag is not None:
                validated["concept_tag"] = concept_tag
            level = candidate.get("level")
            if level is not None:
                validated["level"] = level
        kept.append(validated)
    return kept


# ---------------------------------------------------------------------------
# The five LLM functions (prompt strings = source of truth)
#
# Each function builds its prompt from the verbatim contracts authored by the
# user in the requirements' "LLM Functions (Source of Truth)" section, calls
# ``llm.get_client().chat.completions.create(model=MODEL, temperature=...)``,
# and parses the strict-JSON response with the Fence_Stripping_Parse pattern
# reused from ``quiz._strip_json``. The prompt strings are the contract and are
# preserved exactly as authored.
# ---------------------------------------------------------------------------

# Exactly six placement-diagnostic questions (Req 2.1).
PROBE_QUESTION_COUNT = 6


def _extract_json_substring(text: str):
    """Best-effort recovery: parse the first balanced JSON array/object in ``text``.

    Models occasionally wrap strict JSON in prose ("Here is the quiz: [...]"),
    append a trailing note, or emit a trailing comma before a closing bracket.
    When a direct ``json.loads`` fails, this scans for the outermost ``[...]`` or
    ``{...}`` span (respecting strings/escapes so brackets inside string values
    don't confuse the matcher), strips trailing commas, and parses that. Raises
    ``ValueError`` when no balanced JSON value can be recovered.
    """
    if not text:
        raise ValueError("no JSON value found in empty response")

    # Choose the earliest opening bracket so leading prose is skipped.
    candidates = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not candidates:
        raise ValueError("no JSON array or object found in response")
    start = min(candidates)
    open_ch = text[start]
    close_ch = "]" if open_ch == "[" else "}"

    depth = 0
    in_string = False
    escaped = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError("unbalanced JSON value in response")

    snippet = text[start : end + 1]
    # Strip trailing commas before a closing bracket/brace (a common LLM slip).
    snippet = re.sub(r",(\s*[}\]])", r"\1", snippet)
    return json.loads(snippet)


def _parse_json(raw: str):
    """Fence_Stripping_Parse: strip markdown code fences, then parse strict JSON.

    Reuses the shared fence-stripping pattern :func:`quiz._strip_json` (the
    Fence_Stripping_Parse used by every Game_Service LLM function, Req 2.4) and
    then ``json.loads``. When the model wraps the JSON in prose, appends a note,
    or leaves a trailing comma, a strict parse fails; in that case this falls
    back to :func:`_extract_json_substring` to recover the first balanced JSON
    value. Raises (``ValueError``/``JSONDecodeError``) only when nothing
    parseable can be recovered, so callers can decide how to handle it (error
    response for probes/quizzes, retry-then-fallback for decide/intuition/clip).
    """
    stripped = quiz._strip_json(raw)
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        # Fall back to extracting a balanced JSON value from the raw text (the
        # un-stripped form too, in case the fence split discarded content).
        for candidate in (stripped, raw or ""):
            try:
                return _extract_json_substring(candidate)
            except (ValueError, TypeError):
                continue
        raise


# The verbatim ``generate_probe`` prompt — the source of truth from the
# requirements' "LLM Functions (Source of Truth)": a 6-question placement
# diagnostic aimed AT the topic with a ~2 prerequisite / ~2 core / ~2 stretch
# gradient, each question tagged with a Concept_Tag and a level, testing
# understanding/application rather than recall trivia, distractors that are
# plausible misconceptions, strict JSON.
_GENERATE_PROBE_PROMPT = """You are designing a 6-question PLACEMENT DIAGNOSTIC quiz aimed AT the topic "{topic}".

This is NOT a lesson-one quiz. Aim the questions AT "{topic}" itself so the result reveals where a learner actually stands relative to this topic, not whether they can recite an introduction.

Produce EXACTLY 6 multiple-choice questions with this difficulty gradient:
- ~2 PREREQUISITE questions: the foundational ideas a learner must already grasp BEFORE they can learn "{topic}".
- ~2 CORE questions: the central ideas of "{topic}" itself.
- ~2 STRETCH questions: the advanced or edge applications just beyond "{topic}".

Every question MUST:
- test real UNDERSTANDING and APPLICATION of the subject, NEVER recall trivia (never ask about wording, history, or who said what)
- have EXACTLY ONE correct option and 2-3 distractors that are PLAUSIBLE MISCONCEPTIONS a real learner would fall for, never obvious throwaways
- carry a "concept_tag": a short label naming the SPECIFIC concept that question tests, used to localize the learner's gap
- carry a "level": exactly one of "prerequisite", "core", or "stretch"
- include a one-line "explanation" of why the correct answer is right

Return ONLY a strict JSON array of exactly 6 objects in this shape:
[{{"question": "...", "options": ["...", "...", "..."], "correct_index": 0, "explanation": "...", "concept_tag": "...", "level": "prerequisite"}}]"""


def generate_probe(topic: str) -> list[dict]:
    """Generate the 6-question placement Probe for a topic (Req 2.1-2.4, 2.6).

    Calls the Game_Model (``gpt-4o``) at temperature 0.4 with the verbatim
    ``generate_probe`` prompt, parses the strict-JSON response with the
    Fence_Stripping_Parse pattern (:func:`_parse_json` → ``quiz._strip_json``,
    Req 2.4), and runs every candidate through the reused
    ``quiz._validate_question`` filter (:func:`_filter_valid_questions`),
    preserving each question's ``concept_tag`` and ``level`` (Req 2.3).

    The prompt asks for a ~2 prerequisite / 2 core / 2 stretch difficulty
    gradient aimed at the topic (Req 2.2). The call is issued with ``MODEL``,
    the Game_Model resolved to ``gpt-4o`` (Req 2.6).

    Returns the list of validated Probe questions. Enforcing that exactly 6
    valid questions were produced (Req 2.5) is the caller's responsibility
    (``start_session``); this function returns whatever passed validation so the
    orchestrator can surface an error rather than a partial probe.
    """
    prompt = _GENERATE_PROBE_PROMPT.format(topic=topic)
    resp = llm.get_client().chat.completions.create(
        model=MODEL,
        temperature=0.4,
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = _parse_json(resp.choices[0].message.content)
    candidates = parsed if isinstance(parsed, list) else []
    return _filter_valid_questions(candidates)


# The verbatim ``decide_next`` prompt — the source of truth from the
# requirements' "LLM Functions (Source of Truth)". The Score_Band is computed
# deterministically in code and passed IN as an input; the LLM never chooses
# the band. The model's job is to pick the SPECIFIC node consistent with the
# given band and write a 1-2 sentence second-person Diagnosis. DESCEND must pick
# a genuinely more fundamental prerequisite and never repeat a node already in
# the path; if the current node is already foundational the action becomes
# REINFORCE. CLIMB advances exactly one step toward the goal and never past it;
# if the current node IS the goal, reached_goal is true.
_DECIDE_NEXT_PROMPT = """You are the adaptive engine of a learning game. A learner is working toward the GOAL concept "{goal}". They just took a quiz on their CURRENT node "{current_node}".

The decision band has ALREADY been computed for you in code from their score. You MUST obey it — do NOT second-guess it or choose a different band:

  BAND = {band}

Their score on "{current_node}" was {score_pct} (0.0-1.0).
Concepts they MISSED: {missed_concepts}
Concepts they got CORRECT: {correct_concepts}
Nodes already visited this session (the PATH, never repeat any of these): {path}

Decide the single next step according to the BAND:

- If BAND is DESCEND: the learner is broken on a PREREQUISITE of "{current_node}". Pick ONE genuinely MORE FUNDAMENTAL prerequisite concept that "{current_node}" depends on — the specific thing whose absence explains the missed concepts. It MUST be more basic than "{current_node}", and it MUST NOT be any node already in the PATH. BUT if "{current_node}" is already foundational (it has no more fundamental prerequisite — it is bedrock for this goal), then you CANNOT descend further: set "action" to "REINFORCE" and set "next_node" to "{current_node}".

- If BAND is REINFORCE: the learner is close but not solid on "{current_node}". Keep them here. Set "action" to "REINFORCE" and "next_node" to "{current_node}".

- If BAND is CLIMB: the learner has mastered "{current_node}". Advance EXACTLY ONE step toward the GOAL "{goal}" — pick the next concept that builds directly on "{current_node}" on the way to the goal. NEVER pick a concept past the goal. If "{current_node}" IS the goal "{goal}", then they have finished: set "reached_goal" to true and "next_node" to null.

Always write a "gap" (the specific concept the next step targets) and a "diagnosis": a warm, 1-2 sentence, SECOND-PERSON, plain-language explanation of where the learner's gap is and what the next step does about it. Speak directly to the learner ("You ...").

Return ONLY strict JSON in this exact shape:
{{"action": "DESCEND|REINFORCE|CLIMB", "next_node": "concept name or null", "reached_goal": false, "gap": "...", "diagnosis": "..."}}"""

# decide_next enforces a hard 30-second timeout on the LLM call (Req 5.7).
DECIDE_NEXT_TIMEOUT_SECONDS = 30


def decide_next(
    goal: str,
    current_node: str,
    path: list[str],
    score_pct: float,
    band: Band,
    missed_concepts: list[str],
    correct_concepts: list[str],
) -> dict:
    """Ask the LLM to pick the next node and write the Diagnosis (Req 5.1-5.2, 5.7-5.8).

    The deterministic Score_Band is computed in code by :func:`band` and passed
    IN here; the LLM never chooses the band (Req 5.5, and the design's "where
    determinism lives"). The model's job is only to pick the SPECIFIC node
    consistent with the given band and write a 1-2 sentence second-person
    Diagnosis, using the verbatim ``decide_next`` prompt.

    Calls the Game_Model (``gpt-4o``) at temperature 0.2 with a hard 30-second
    timeout (Req 5.7) and parses the strict-JSON response with the
    Fence_Stripping_Parse pattern (:func:`_parse_json` → ``quiz._strip_json``,
    Req 5.8). Returns the raw decision dict ``{action, next_node, reached_goal,
    gap, diagnosis}`` (Req 5.2).

    This is the RAW LLM function. The structural guarantees the prompt is asked
    for but cannot be trusted to keep — path no-repeat enforcement, foundational
    coercion to REINFORCE, clamping CLIMB at the goal, band-consistency
    override, and retry-on-failure — are enforced by the ``decide`` orchestrator
    (task 3.3), not here.
    """
    prompt = _DECIDE_NEXT_PROMPT.format(
        goal=goal,
        current_node=current_node,
        band=band,
        score_pct=score_pct,
        missed_concepts=json.dumps(missed_concepts),
        correct_concepts=json.dumps(correct_concepts),
        path=json.dumps(path),
    )
    resp = llm.get_client().chat.completions.create(
        model=MODEL,
        temperature=0.2,
        max_tokens=600,
        timeout=DECIDE_NEXT_TIMEOUT_SECONDS,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.choices[0].message.content)


# Exactly three checkpoint questions per Node quiz (Req 11.3).
CHECKPOINT_QUESTION_COUNT = 3
# One initial generation plus up to this many backfill re-invocations to reach
# three valid questions (Req 11.7).
MAX_QUIZ_BACKFILL_ATTEMPTS = 3
# The per-question LLM quality judge is an extra gate on top of structural
# validation. Enabled by default; set GAME_QUIZ_JUDGE=0 to skip it and shave a
# round-trip off node delivery (structural _validate_question still applies).
_QUIZ_JUDGE_ENABLED = os.environ.get("GAME_QUIZ_JUDGE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# The verbatim ``generate_quiz`` prompt — the source of truth from the
# requirements' "LLM Functions (Source of Truth)": a 3-question checkpoint quiz
# for a single Node, based on the Clip's transcript when present and otherwise
# on the model's own knowledge (Req 11.1, 11.2). Each question tests real
# understanding/application of the node (never video trivia), carries a
# Concept_Tag naming the specific sub-concept it tests (Req 11.3), has exactly
# one correct option with plausible-misconception distractors, and an
# explanation; strict JSON. The transcript/model-knowledge instruction is the
# only part that varies with input (``{source_block}``); the rest of the
# contract is preserved exactly.
_GENERATE_QUIZ_PROMPT = """You are writing a 3-question CHECKPOINT quiz that tests whether a learner UNDERSTOOD the concept "{node}".

{source_block}

Write EXACTLY 3 MULTIPLE-CHOICE questions that:
- test real UNDERSTANDING and APPLICATION of "{node}", NEVER recall trivia (never ask about the video, the narrator, slide colors, wording, or what was "said")
- prefer scenario or "what happens if" framing over a dry "what is the definition of"
- each have EXACTLY ONE correct option and 2-3 plausible-but-wrong distractors that a real learner would actually fall for, never obvious throwaways
- carry a "concept_tag": a short label naming the SPECIFIC sub-concept of "{node}" that question tests, used to localize the learner's gap
- include a one-line "explanation" of why the correct answer is right

Every question MUST be multiple-choice. Do NOT produce true/false or open-ended questions.

Return ONLY a strict JSON array of exactly 3 objects in this shape:
[{{"question": "...", "options": ["...", "...", "..."], "correct_index": 0, "explanation": "...", "concept_tag": "..."}}]"""

# Transcript-present source instruction (Req 11.1).
_QUIZ_SOURCE_WITH_TRANSCRIPT = """Base the questions on this lesson transcript (excerpts):
{excerpt}"""

# Transcript-free source instruction: rely on model knowledge (Req 11.2).
_QUIZ_SOURCE_FROM_KNOWLEDGE = (
    'No transcript is available, so base the questions on your own expert '
    'knowledge of "{node}".'
)


def _quiz_source_block(node: str, transcript: str | None) -> str:
    """Build the transcript / model-knowledge instruction for the quiz prompt.

    When a transcript is present the questions are grounded in its excerpt
    (truncated to the shared ``quiz._TRANSCRIPT_BUDGET``, Req 11.1); when it is
    ``None`` or empty the model is told to draw on its own knowledge of the node
    instead (Req 11.2).
    """
    excerpt = (transcript or "").strip()[: quiz._TRANSCRIPT_BUDGET]
    if excerpt:
        return _QUIZ_SOURCE_WITH_TRANSCRIPT.format(excerpt=excerpt)
    return _QUIZ_SOURCE_FROM_KNOWLEDGE.format(node=node)


def _generate_quiz_attempt(node: str, transcript: str | None) -> list[dict]:
    """One generate→parse→validate→judge pass producing valid Node MCQs.

    Issues a single Game_Model (``gpt-4o``) call at temperature 0.4 (Req 11.6)
    with the verbatim ``generate_quiz`` prompt, parses the strict-JSON response
    with the Fence_Stripping_Parse pattern (:func:`_parse_json`), filters the
    candidates through the reused ``quiz._validate_question`` gate
    (:func:`_filter_valid_questions`, the task 1.8 filter, Req 11.4), then runs
    each survivor through the reused ``quiz._judge_question`` quality judge,
    keeping only questions the judge marks ``ok``. Mirroring
    ``quiz._vet_questions``, a judge call that raises drops only that question
    (best-effort), never the batch.

    Every kept question is guaranteed to carry a Concept_Tag (Req 11.3): the
    validation preserves the model-supplied ``concept_tag``, and any question
    missing one defaults to the node name so the per-concept invariant holds.
    """
    prompt = _GENERATE_QUIZ_PROMPT.format(
        node=node,
        source_block=_quiz_source_block(node, transcript),
    )
    resp = llm.get_client().chat.completions.create(
        model=MODEL,
        temperature=0.4,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = _parse_json(resp.choices[0].message.content)
    candidates = parsed if isinstance(parsed, list) else []

    valid = _filter_valid_questions(candidates)
    if not valid:
        return []

    # The reused quiz judge is one LLM round-trip PER question — an extra quality
    # gate on top of the structural _validate_question filter. It can be disabled
    # for speed via GAME_QUIZ_JUDGE=0 (the questions still pass full structural
    # validation; only the LLM quality vetting is skipped), removing those
    # round-trips entirely from node delivery.
    if not _QUIZ_JUDGE_ENABLED:
        for question in valid:
            if not question.get("concept_tag"):
                question["concept_tag"] = node
        return valid

    # Judge the candidates concurrently rather than back-to-back so the wall-time
    # is ~one round-trip instead of N. A judge call that raises drops only that
    # question (best-effort), never the batch. ``executor.map`` preserves order.
    def _judged(question: dict) -> dict | None:
        try:
            verdict = quiz._judge_question(node, question)
        except Exception as exc:  # judge failure drops the question, not the batch
            logger.warning(f"[game] quiz judge failed for a '{node}' question: {exc}")
            return None
        if not verdict.get("ok"):
            return None
        # Guarantee exactly one Concept_Tag per question (Req 11.3).
        if not question.get("concept_tag"):
            question["concept_tag"] = node
        return question

    with ThreadPoolExecutor(max_workers=min(len(valid), 5)) as executor:
        results = executor.map(_judged, valid)
    return [q for q in results if q is not None]


def generate_quiz(node: str, transcript: str | None) -> list[dict]:
    """Generate a Node's 3-question Checkpoint_Quiz (Req 11.1-11.8).

    Produces EXACTLY 3 validated multiple-choice questions for ``node``, each
    with exactly one correct answer and exactly one Concept_Tag (Req 11.3),
    grounded in ``transcript`` when one is supplied and otherwise in the model's
    own knowledge (Req 11.1, 11.2). Each generation pass is issued with the
    Game_Model (``gpt-4o``) at temperature 0.4 (Req 11.6).

    Every candidate is filtered through the reused ``quiz._validate_question``
    gate (:func:`_filter_valid_questions`, Req 11.4) and the reused
    ``quiz._judge_question`` quality judge. This function NEVER calls the
    DB-coupled ``quiz.generate_and_store_questions`` orchestrator (Req 11.5).

    If a pass yields fewer than 3 valid questions, generation is re-invoked to
    backfill the shortfall with no more than 3 additional attempts (Req 11.7).
    If 3 valid questions still cannot be produced after the maximum attempts,
    a ``ValueError`` is raised so the caller surfaces an error rather than a
    partial Checkpoint_Quiz (Req 11.8).
    """
    kept: list[dict] = []
    # One initial attempt plus up to MAX_QUIZ_BACKFILL_ATTEMPTS backfills.
    for _ in range(1 + MAX_QUIZ_BACKFILL_ATTEMPTS):
        try:
            kept.extend(_generate_quiz_attempt(node, transcript))
        except Exception as exc:  # parse/API failure on a pass; try to backfill
            logger.warning(f"[game] generate_quiz attempt failed for '{node}': {exc}")
        if len(kept) >= CHECKPOINT_QUESTION_COUNT:
            return kept[:CHECKPOINT_QUESTION_COUNT]

    raise ValueError(
        f"generate_quiz could not produce {CHECKPOINT_QUESTION_COUNT} valid "
        f"questions for '{node}' after {1 + MAX_QUIZ_BACKFILL_ATTEMPTS} attempts"
    )


def _best_effort_quiz(node: str, transcript: str | None) -> list[dict]:
    """Salvage up to 3 valid checkpoint questions without ever raising (Req 23.4).

    The strict :func:`generate_quiz` raises when it cannot assemble a full
    3-question quiz (Req 11.8). For on-the-fly node delivery, though, the
    checkpoint is SOFT and must never hard-block the loop, so :func:`deliver_node`
    falls back here: this runs the same generate→validate→judge pass(es) and
    returns whatever valid questions were produced (0-3), swallowing any
    parse/API failure. An empty result is acceptable — the Play_Surface treats a
    missing/short quiz as skippable rather than a dead end.
    """
    kept: list[dict] = []
    for _ in range(1 + MAX_QUIZ_BACKFILL_ATTEMPTS):
        try:
            kept.extend(_generate_quiz_attempt(node, transcript))
        except Exception as exc:
            logger.warning(
                f"[game] best-effort quiz attempt failed for '{node}': {exc}"
            )
        if len(kept) >= CHECKPOINT_QUESTION_COUNT:
            break
    return kept[:CHECKPOINT_QUESTION_COUNT]


# The verbatim ``intuition`` prompt — the source of truth from the requirements'
# "LLM Functions (Source of Truth)": a 2-3 sentence punchy SECOND-PERSON mental
# model (the Intuition_Card ``hook``) for a Node, in service of the learner's
# goal. Strict JSON. The contract is preserved exactly as authored.
_INTUITION_PROMPT = """You are giving a learner the core INTUITION for the concept "{node}", on their way to understanding the bigger goal "{goal}".

Write a 2-3 sentence punchy mental model that makes "{node}" suddenly CLICK — the kind of crisp, vivid analogy or reframing that a great teacher drops to make an idea obvious. NOT a textbook definition, NOT a history lesson, NOT a list.

Your hook MUST:
- be SECOND-PERSON, speaking directly to the learner ("You ...")
- be 2-3 sentences, punchy and concrete, never dry or hedging
- give the ESSENCE / mental model of "{node}", not its formal definition
- be plain language a curious beginner instantly gets

Return ONLY strict JSON in this exact shape:
{{"hook": "..."}}"""

# ``intuition`` is issued with the Game_Model at temperature 0.7 (Req 9.2),
# higher than the other functions for a punchier, more vivid hook.
INTUITION_TEMPERATURE = 0.7


def intuition(node: str, goal: str) -> dict:
    """Generate a Node's Intuition_Card ``hook`` (Req 9.1-9.3).

    Calls the Game_Model (``gpt-4o``) at temperature 0.7 with the verbatim
    ``intuition`` prompt (Req 9.2) and parses the strict-JSON response with the
    Fence_Stripping_Parse pattern (:func:`_parse_json` → ``quiz._strip_json``,
    Req 9.3). Returns the raw decision dict ``{"hook": ...}``; the ``deliver_node``
    orchestrator (task 3.8) extracts the ``hook`` text the Play_Surface shows as
    a 2-3 sentence punchy second-person mental model (Req 9.1).
    """
    prompt = _INTUITION_PROMPT.format(node=node, goal=goal)
    resp = llm.get_client().chat.completions.create(
        model=MODEL,
        temperature=INTUITION_TEMPERATURE,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.choices[0].message.content)


# The verbatim ``clip_query`` prompt — the source of truth from the requirements'
# "LLM Functions (Source of Truth)": an optimized YouTube search string for a
# SHORT (3-10 min) focused explainer of the Node, with NO channel names and NO
# "full course" phrasing. Strict JSON. The contract is preserved exactly as
# authored.
_CLIP_QUERY_PROMPT = """You are writing the single best YouTube SEARCH QUERY to find ONE short, focused explainer video for the concept "{node}" (a learner is working toward the bigger goal "{goal}").

You want a SHORT, punchy explainer — roughly a 3 to 10 minute focused video that explains "{node}" clearly — NOT a long lecture and NOT a full course.

Your query MUST:
- target a SHORT focused explainer of "{node}" (the kind of 3-10 minute video that explains one idea well)
- stay tightly ON-TOPIC for "{node}" as it relates to "{goal}" — include the most specific, disambiguating terms so results are about THIS concept, not a same-named topic in another field or a loosely related tangent
- NOT name any specific channel, creator, or person
- NOT contain the phrase "full course" or ask for a course, playlist, or full lecture series
- be a concise search string, not a sentence

Return ONLY strict JSON in this exact shape:
{{"query": "..."}}"""

# ``clip_query`` is issued with the Game_Model at temperature 0.3 (Req 10.2),
# low for a tight, consistent search string.
CLIP_QUERY_TEMPERATURE = 0.3


def clip_query(node: str, goal: str) -> dict:
    """Generate an optimized YouTube search query for a Node (Req 10.1-10.2).

    Calls the Game_Model (``gpt-4o``) at temperature 0.3 with the verbatim
    ``clip_query`` prompt (Req 10.2) and parses the strict-JSON response with the
    Fence_Stripping_Parse pattern (:func:`_parse_json` → ``quiz._strip_json``).
    The prompt asks for a SHORT (3-10 min) focused-explainer query carrying no
    channel names and no "full course" phrasing. Returns the raw decision dict
    ``{"query": ...}``; the ``deliver_node`` orchestrator (task 3.8) feeds the
    ``query`` into ``youtube.youtube_search`` (Clip_Search, Req 10.1).
    """
    prompt = _CLIP_QUERY_PROMPT.format(node=node, goal=goal)
    resp = llm.get_client().chat.completions.create(
        model=MODEL,
        temperature=CLIP_QUERY_TEMPERATURE,
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.choices[0].message.content)


# ---------------------------------------------------------------------------
# Stateless orchestration the Game_Router calls
#
# The backend is stateless in Phase 1: each orchestration function takes the
# full context it needs and returns the next artifact. The client (the
# Game_State_Machine) owns the loop and holds session state in localStorage.
# ---------------------------------------------------------------------------


class ProbeGenerationError(Exception):
    """The placement Probe could not be produced as 6 valid MCQs (Req 2.5).

    Raised by :func:`start_session` when ``generate_probe`` cannot be parsed
    into exactly ``PROBE_QUESTION_COUNT`` valid multiple-choice questions — a
    probe is all-or-nothing because the difficulty gradient is what makes
    placement meaningful. The Game_Router maps this to an HTTP 502
    ``probe_generation_failed`` response so the Play_Surface can show a retry
    affordance rather than starting a session with a partial probe.
    """


@dataclass(frozen=True)
class SessionStart:
    """The artifact returned when a session is started (Req 1.1, 1.2, 13.3).

    - ``session_id``: a fresh UUID identifying this session.
    - ``goal``: the Goal_Node — the topic the learner entered (Req 1.1).
    - ``current_node``: the node the learner is on; equal to ``goal`` at the
      start of the session, since placement begins aimed AT the goal.
    - ``probe``: the 6-question placement Probe for the topic (Req 2.1).
    """

    session_id: str
    goal: str
    current_node: str
    probe: list[dict]


def start_session(topic: str) -> SessionStart:
    """Start a session for a topic and return its placement Probe (Req 1, 2.5).

    The non-empty ``topic`` is treated as the Goal_Node for the session, and the
    ``current_node`` is set equal to the goal — placement begins aimed AT the
    goal, not at lesson one (Req 1.1). Topics may come from any subject domain;
    no predefined list is consulted (Req 1.4). A fresh UUID ``session_id`` is
    generated to identify the session.

    The Probe is produced by calling ``generate_probe(topic)`` (Req 1.2). Because
    a probe is all-or-nothing — the ~2 prerequisite / 2 core / 2 stretch gradient
    is what makes placement meaningful — this returns an error rather than a
    partial probe: if ``generate_probe`` fails to parse, or yields fewer than
    ``PROBE_QUESTION_COUNT`` valid MCQs, a :class:`ProbeGenerationError` is
    raised for the router to map to ``probe_generation_failed`` (Req 2.5). When
    more than 6 valid questions are produced, the first 6 are kept so the
    returned probe always has exactly ``PROBE_QUESTION_COUNT`` questions.
    """
    goal = topic.strip() if isinstance(topic, str) else ""

    try:
        probe = generate_probe(goal)
    except Exception as exc:
        # A parse/API failure means the probe could not be produced as 6 valid
        # MCQs — surface a clear error rather than a partial probe (Req 2.5).
        logger.warning(f"[game] generate_probe failed for topic '{goal}': {exc}")
        raise ProbeGenerationError(
            f"could not generate a placement probe for '{goal}'"
        ) from exc

    if len(probe) < PROBE_QUESTION_COUNT:
        # Fewer than 6 valid questions survived validation: no partial probe.
        raise ProbeGenerationError(
            f"generate_probe produced only {len(probe)} valid question(s) for "
            f"'{goal}'; {PROBE_QUESTION_COUNT} are required"
        )

    return SessionStart(
        session_id=str(uuid.uuid4()),
        goal=goal,
        current_node=goal,
        probe=probe[:PROBE_QUESTION_COUNT],
    )


# Maximum descent depth before the recursion is forced to bottom out at the
# current node (Req 6.1, 6.6).
MAX_DESCENT_DEPTH = 10
# decide_next is retried up to this many times after the initial attempt; if it
# still fails/times out/returns an unparseable response, ``decide`` raises and
# leaves Path unchanged (Req 5.11).
DECIDE_NEXT_MAX_RETRIES = 2


class DecideError(Exception):
    """``decide_next`` could not produce a usable decision (Req 5.11).

    Raised by :func:`decide` when the ``decide_next`` LLM call fails, times out,
    or returns an unparseable/non-object response after the initial attempt plus
    :data:`DECIDE_NEXT_MAX_RETRIES` retries. When this is raised the caller's
    ``Path`` is left unchanged — no node is appended and no decision is applied
    — so the Game_Router can surface an error and the learner can retry without
    corrupting the session (Req 5.11).
    """


@dataclass(frozen=True)
class DecideRequest:
    """The context ``decide`` needs to grade, band, and pick the next step.

    The backend is stateless in Phase 1, so the client passes the full context:

    - ``goal``: the Goal_Node (the topic the learner entered).
    - ``current_node``: the node the just-graded quiz was on.
    - ``path``: the ordered list of nodes already visited this session; a
      candidate ``next_node`` already in here is never re-selected (Req 6.3).
    - ``questions`` / ``answers``: the quiz just taken and the chosen option
      index per question, graded deterministically by :func:`grade_quiz`.
    - ``floor_node``: the Floor_Node established so far this session, or ``None``
      if the learner has not yet bottomed out; carried through so the floor is
      marked exactly once (Req 6.2).
    - ``descent_depth``: how many descents have happened so far this session,
      used to bound the recursion at :data:`MAX_DESCENT_DEPTH` (Req 6.1, 6.6).
    """

    goal: str
    current_node: str
    path: list[str]
    questions: list[dict]
    answers: list[int]
    floor_node: str | None = None
    descent_depth: int = 0


@dataclass(frozen=True)
class DecideResponse:
    """The banded, enforced next-step decision returned to the caller (Req 5.2).

    Carries everything the Play_Surface needs to render the outcome card and
    advance the loop: the resolved ``action`` (after the deterministic
    guarantees are enforced), the code-computed ``band``, the ``next_node`` (or
    ``None`` when the goal is reached or the learner reinforces in place),
    ``reached_goal``, the LLM ``gap`` / ``diagnosis`` text, and the graded
    ``score_pct`` / ``missed_concepts`` / ``correct_concepts``. ``floor_node``
    and ``descent_depth`` are returned so the stateless client can persist the
    updated session state.
    """

    action: Band
    band: Band
    next_node: str | None
    reached_goal: bool
    gap: str
    diagnosis: str
    score_pct: float
    missed_concepts: list[str]
    correct_concepts: list[str]
    floor_node: str | None
    descent_depth: int


def _norm_node(value) -> str:
    """Normalize a node name for comparison (trimmed, case-insensitive)."""
    return str(value or "").strip().lower()


def _normalize_next_node(value) -> str | None:
    """Coerce a raw ``next_node`` from the LLM into a clean name or ``None``.

    Treats ``None``, empty/whitespace, and the literal string ``"null"`` (which
    the strict-JSON prompt may emit as text) as "no node".
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _in_path(node: str | None, path: list[str]) -> bool:
    """True when ``node`` already appears in ``path`` (normalized compare)."""
    if node is None:
        return False
    target = _norm_node(node)
    return any(_norm_node(p) == target for p in (path or []))


def _call_decide_next(
    goal: str,
    current_node: str,
    path: list[str],
    score_pct: float,
    band_value: Band,
    missed_concepts: list[str],
    correct_concepts: list[str],
) -> dict:
    """Call ``decide_next`` with retries, returning the raw decision dict.

    Issues the initial attempt plus up to :data:`DECIDE_NEXT_MAX_RETRIES`
    retries (Req 5.11). A call that raises (API error / timeout / unparseable
    response) or returns a non-object is treated as a failed attempt and retried.
    If every attempt fails, raises :class:`DecideError` so :func:`decide` can
    surface an error and leave Path unchanged.
    """
    last_exc: Exception | None = None
    for attempt in range(1 + DECIDE_NEXT_MAX_RETRIES):
        try:
            raw = decide_next(
                goal,
                current_node,
                path,
                score_pct,
                band_value,
                missed_concepts,
                correct_concepts,
            )
            if not isinstance(raw, dict):
                raise ValueError("decide_next returned a non-object response")
            return raw
        except Exception as exc:
            last_exc = exc
            logger.warning(
                f"[game] decide_next attempt {attempt + 1} failed for "
                f"'{current_node}' (goal '{goal}'): {exc}"
            )
    raise DecideError(
        f"decide_next failed for '{current_node}' after "
        f"{1 + DECIDE_NEXT_MAX_RETRIES} attempts"
    ) from last_exc


def decide(req: DecideRequest) -> DecideResponse:
    """Grade, band, and resolve the enforced next-step decision (Req 3-6, 8).

    This is the core orchestration that wraps the raw, untrusted ``decide_next``
    LLM function with the deterministic guarantees the "it found where I'm
    broken" magic depends on. The sequence is:

    1. **Grade** the quiz deterministically per Concept_Tag via
       :func:`grade_quiz` (Req 4). A zero-question quiz cannot be banded, so
       :func:`grade_quiz` raises ``ValueError`` and ``decide_next`` is never
       called (Req 3.7, 4.7).
    2. **Band** the score fraction in code via :func:`band` (Req 3.5) and pass
       the band INTO ``decide_next`` — the LLM never chooses the band (Req 3.6).
    3. **Decide** by calling ``decide_next`` with retries; on failure after the
       initial attempt plus :data:`DECIDE_NEXT_MAX_RETRIES` retries, raise
       :class:`DecideError` and leave Path unchanged (Req 5.11).

    The structural guarantees the prompt is asked for but cannot be trusted to
    keep are then enforced in code:

    - **Band-consistency override** (Req 5.10): the resolved ``action`` is forced
      to match the passed band, except where a stronger rule coerces ``REINFORCE``.
    - **DESCEND no-repeat / floor** (Req 5.3, 5.4, 5.9, 6.3, 6.5, 6.6): a
      proposed prerequisite already in ``Path`` (or equal to the current node, or
      missing) is rejected and coerced to ``REINFORCE``; a foundational node is
      coerced to ``REINFORCE`` and marked the Floor_Node; and the descent is
      bounded at :data:`MAX_DESCENT_DEPTH`, bottoming out at ``REINFORCE`` on the
      current node marked as the floor.
    - **CLIMB bound** (Req 5.5, 5.6, 8.1-8.3): ``reached_goal`` is true exactly
      when the current node is the goal (then ``next_node`` is ``None``);
      otherwise the climb advances exactly one node. A climbed ``next_node``
      already in ``Path`` (or missing) is coerced to ``REINFORCE``.
    - **Floor marked once** (Req 6.2): the first ``CLIMB`` out of a descent chain
      sets the Floor_Node to the climbed node; it is never moved afterward.

    Returns a :class:`DecideResponse` carrying the resolved action, the band, the
    next node, ``reached_goal``, the LLM gap/diagnosis, the graded score and
    concept sets, and the updated ``floor_node`` / ``descent_depth`` for the
    stateless client to persist.
    """
    # 1. Deterministic per-concept grading. Raises ValueError on a zero-question
    #    quiz, so decide_next is never reached for an unbandable quiz (Req 3.7).
    result = grade_quiz(req.questions, req.answers)
    score_pct = result.score_pct
    missed = result.missed_concepts
    correct = result.correct_concepts

    # 2. Deterministic banding in code; the band is passed INTO decide_next.
    band_value: Band = band(score_pct)

    # 3. Raw LLM decision with retries; failure leaves Path unchanged (Req 5.11).
    raw = _call_decide_next(
        req.goal,
        req.current_node,
        req.path,
        score_pct,
        band_value,
        missed,
        correct,
    )

    llm_action = _norm_node(raw.get("action")).upper()
    llm_next = _normalize_next_node(raw.get("next_node"))
    gap = str(raw.get("gap") or "")
    diagnosis = str(raw.get("diagnosis") or "")

    is_goal = _norm_node(req.current_node) == _norm_node(req.goal)
    floor_node = req.floor_node
    descent_depth = req.descent_depth

    def mark_floor() -> str | None:
        # The floor is set exactly once; once established it never moves (Req 6.2).
        return floor_node if floor_node is not None else req.current_node

    if band_value == "CLIMB":
        # Req 5.6 / 8.2: reached_goal is true exactly when the current node is
        # the goal; the climb stops there with no next node.
        if is_goal:
            return DecideResponse(
                action="CLIMB",
                band="CLIMB",
                next_node=None,
                reached_goal=True,
                gap=gap,
                diagnosis=diagnosis,
                score_pct=score_pct,
                missed_concepts=missed,
                correct_concepts=correct,
                floor_node=floor_node,
                descent_depth=descent_depth,
            )

        # Req 8.1 / 8.3: advance exactly one node toward the goal. A proposed
        # next node that repeats the path, equals the current node, or is missing
        # has no valid band-consistent candidate, so coerce REINFORCE (Req 5.9).
        if llm_next is None or _in_path(llm_next, req.path) or (
            _norm_node(llm_next) == _norm_node(req.current_node)
        ):
            return DecideResponse(
                action="REINFORCE",
                band="CLIMB",
                next_node=req.current_node,
                reached_goal=False,
                gap=gap,
                diagnosis=diagnosis,
                score_pct=score_pct,
                missed_concepts=missed,
                correct_concepts=correct,
                floor_node=floor_node,
                descent_depth=descent_depth,
            )

        # A genuine climb out of a descent chain marks the floor once (Req 6.2):
        # the node the learner just cleared (current_node) is the Floor_Node.
        if descent_depth > 0:
            floor_node = mark_floor()

        return DecideResponse(
            action="CLIMB",
            band="CLIMB",
            next_node=llm_next,
            reached_goal=False,
            gap=gap,
            diagnosis=diagnosis,
            score_pct=score_pct,
            missed_concepts=missed,
            correct_concepts=correct,
            floor_node=floor_node,
            descent_depth=descent_depth,
        )

    if band_value == "REINFORCE":
        # Req 7.1: keep the learner on the current node.
        return DecideResponse(
            action="REINFORCE",
            band="REINFORCE",
            next_node=req.current_node,
            reached_goal=False,
            gap=gap,
            diagnosis=diagnosis,
            score_pct=score_pct,
            missed_concepts=missed,
            correct_concepts=correct,
            floor_node=floor_node,
            descent_depth=descent_depth,
        )

    # band_value == "DESCEND".
    # The LLM signals a foundational node (no more fundamental prerequisite) by
    # returning action REINFORCE and/or next_node == current_node, per the
    # decide_next prompt. In that case, or when no usable prerequisite is
    # proposed, coerce REINFORCE and mark the foundational node as the floor
    # (Req 5.4, 6.5).
    foundational = (
        llm_action == "REINFORCE"
        or llm_next is None
        or _norm_node(llm_next) == _norm_node(req.current_node)
    )
    # Bound the recursion: once descent depth has reached the max, stop
    # descending, bottom out at REINFORCE, and mark the floor (Req 6.6).
    depth_exhausted = descent_depth >= MAX_DESCENT_DEPTH
    # A proposed prerequisite already in the path is rejected; with a single LLM
    # candidate and no valid substitute, coerce REINFORCE (Req 5.3, 5.9, 6.3).
    repeats_path = _in_path(llm_next, req.path)

    if foundational or depth_exhausted or repeats_path:
        floor_node = mark_floor()
        return DecideResponse(
            action="REINFORCE",
            band="DESCEND",
            next_node=req.current_node,
            reached_goal=False,
            gap=gap,
            diagnosis=diagnosis,
            score_pct=score_pct,
            missed_concepts=missed,
            correct_concepts=correct,
            floor_node=floor_node,
            descent_depth=descent_depth,
        )

    # A genuine descent to a more fundamental prerequisite not already visited.
    return DecideResponse(
        action="DESCEND",
        band="DESCEND",
        next_node=llm_next,
        reached_goal=False,
        gap=gap,
        diagnosis=diagnosis,
        score_pct=score_pct,
        missed_concepts=missed,
        correct_concepts=correct,
        floor_node=floor_node,
        descent_depth=descent_depth + 1,
    )


@dataclass(frozen=True)
class NodePayload:
    """A delivered Node: its Intuition_Card, Clip, and Checkpoint_Quiz (Req 7, 9-11).

    The artifact ``deliver_node`` returns for the Play_Surface to render a node
    beat — the intuition hook, then the short clip (when one is affordable), then
    the 3-question checkpoint quiz:

    - ``node``: the node being delivered.
    - ``hook``: the Intuition_Card text — a 2-3 sentence punchy second-person
      mental model from ``intuition`` (Req 9.1).
    - ``clip``: the selected Clip enriched with a canonical ``video_url`` embed
      form (Req 10.3), or ``None`` when no clip is affordable or available so the
      node flow continues with the intuition + a transcript-free quiz and no
      ``video_url`` is exposed (Req 10.4, 10.6).
    - ``quiz``: exactly 3 validated MCQs for the node (Req 11.3), grounded in the
      clip's transcript when one is available and otherwise in model knowledge
      (Req 11.1, 11.2).
    """

    node: str
    hook: str
    clip: dict | None
    quiz: list[dict]


# intuition / clip_query are retried once on failure, then fall back to a safe
# default so a single failed generation never breaks the node-delivery loop
# (Error Handling table, Req 2.4 / 9.3). The clip/transcript fallbacks below are
# the heart of this task (Req 10.4, 10.6, 11.2).
INTUITION_MAX_RETRIES = 1
CLIP_QUERY_MAX_RETRIES = 1

# Safe fallback hook when ``intuition`` cannot be parsed after its retry.
_FALLBACK_HOOK = (
    "You're about to build your intuition for {node}. Focus on the core idea "
    "and how it connects to {goal} — the rest follows from there."
)


def _safe_hook(node: str, goal: str) -> str:
    """Return the Intuition_Card ``hook`` for a node, degrading gracefully.

    Calls ``intuition(node, goal)`` and extracts the ``hook`` text (Req 9.1). On
    a parse/API failure or an empty hook the call is retried once; if it still
    fails a generic second-person fallback hook is returned so the node delivery
    never breaks the loop (Error Handling table, Req 9.3).
    """
    for attempt in range(1 + INTUITION_MAX_RETRIES):
        try:
            raw = intuition(node, goal)
            hook = str((raw or {}).get("hook") or "").strip()
            if hook:
                return hook
            raise ValueError("intuition returned an empty hook")
        except Exception as exc:
            logger.warning(
                f"[game] intuition attempt {attempt + 1} failed for '{node}': {exc}"
            )
    logger.warning(f"[game] intuition fell back to a generic hook for '{node}'")
    return _FALLBACK_HOOK.format(node=node, goal=goal)


def _safe_clip_query(node: str, goal: str) -> str:
    """Return a YouTube search query for a node, degrading gracefully.

    Calls ``clip_query(node, goal)`` and extracts the ``query`` text (Req 10.1).
    On a parse/API failure or an empty query the call is retried once; if it
    still fails it falls back to a short-explainer search for the node itself so
    the clip flow stays alive (Error Handling table).
    """
    for attempt in range(1 + CLIP_QUERY_MAX_RETRIES):
        try:
            raw = clip_query(node, goal)
            query = str((raw or {}).get("query") or "").strip()
            if query:
                return query
            raise ValueError("clip_query returned an empty query")
        except Exception as exc:
            logger.warning(
                f"[game] clip_query attempt {attempt + 1} failed for '{node}': {exc}"
            )
    logger.warning(f"[game] clip_query fell back to a node-name query for '{node}'")
    return f"{node} explained"


def _transcript_text(video_id: str) -> str | None:
    """Fetch a clip's transcript and join its segments into one string (Req 11.1).

    Calls the reused ``youtube._fetch_transcript`` (Transcript_Fetch), which
    returns ``[{start, duration, text}]`` or ``None``. The segment texts are
    joined into a single transcript string for ``generate_quiz``. Returns
    ``None`` when no transcript is available — or when the fetch raises — so the
    caller falls back to a model-knowledge quiz (Req 11.2). Never raises.
    """
    try:
        segments = youtube._fetch_transcript(video_id)
    except Exception as exc:
        logger.warning(f"[game] transcript fetch failed for {video_id}: {exc}")
        return None
    if not segments:
        return None
    text = " ".join(str((seg or {}).get("text") or "").strip() for seg in segments)
    text = text.strip()
    return text or None


def _build_clip(selected: dict) -> dict | None:
    """Enrich a selected clip with a canonical embed ``video_url`` (Req 10.3).

    Maps the raw youtube clip dict into the Clip shape the Play_Surface expects,
    adding ``video_url`` in the ``youtube.com/embed/...`` form (Req 10.3).
    Returns ``None`` when the clip has no usable ``video_id`` so the caller
    treats it as "no clip" and continues transcript-free (Req 10.6).
    """
    video_id = str((selected or {}).get("video_id") or "").strip()
    if not video_id:
        return None
    return {
        "video_id": video_id,
        "video_url": to_embed_url(video_id),
        "title": (selected or {}).get("title"),
        "channel_title": (selected or {}).get("channel_title"),
        "duration_seconds": _duration(selected),
        "has_caption": _has_caption(selected),
        "view_count": _view_count(selected),
        "thumbnail_url": (selected or {}).get("thumbnail_url"),
        "description": (selected or {}).get("description"),
    }


def _resolve_clip(node: str, goal: str) -> tuple[dict | None, str | None]:
    """Resolve a node's clip and its transcript text (Req 10, 11.1, 11.2).

    Runs the full clip pipeline — ``clip_query`` → ``youtube.youtube_search``
    (Clip_Search) → :func:`select_clip` → :func:`_build_clip` →
    ``_fetch_transcript`` — and returns ``(clip, transcript_text)``. Extracted so
    :func:`deliver_node` can run it concurrently with the Intuition_Card hook.

    ``youtube_search`` returns ``None`` when no project can afford a search
    (Req 10.4) and an empty list on a successful-but-empty search (Req 10.6);
    both paths return ``(None, None)`` so the node flow continues with no clip
    and a transcript-free quiz, exposing no ``video_url``. Never raises.
    """
    query = _safe_clip_query(node, goal)
    try:
        clips = youtube.youtube_search(query)
    except Exception as exc:
        # Treat an unexpected search error like an unaffordable search: continue
        # with no clip and a transcript-free quiz rather than break the loop.
        logger.warning(f"[game] youtube_search failed for query '{query}': {exc}")
        clips = None

    if not clips:
        return None, None

    # Drop off-topic candidates before duration ranking so the chosen clip is
    # actually about this node (Req 10), not just well-sized/popular.
    clip_out = _build_clip(select_clip(relevant_clips(clips, node, goal)))
    if clip_out is None:
        return None, None

    transcript_text = _transcript_text(clip_out["video_id"])
    return clip_out, transcript_text


def deliver_node(node: str, goal: str) -> NodePayload:
    """Deliver a Node: Intuition_Card + Clip + Checkpoint_Quiz (Req 7, 9, 10, 11).

    Builds one node beat for the Play_Surface, stateless and band-agnostic — the
    same flow serves a CLIMB step, a DESCEND step, and a REINFORCE re-quiz on the
    same node, always producing a *fresh* checkpoint quiz (Req 7.2, 7.3):

    1. **Intuition** — call ``intuition(node, goal)`` for the ``hook`` (Req 9.1),
       via :func:`_safe_hook` so a generation failure degrades to a generic hook
       rather than breaking the loop.
    2. **Clip** — call ``clip_query(node, goal)`` for a search query (Req 10.1)
       and pass it to ``youtube.youtube_search`` (Clip_Search). When the search
       returns one or more clips, :func:`select_clip` picks the best one and
       :func:`to_embed_url` exposes it as a canonical embed ``video_url``
       (Req 10.3, 10.5). When the search returns ``None`` (no project can afford
       it) or an empty list, the flow continues with ``clip = None`` and no
       ``video_url`` — no error is raised (Req 10.4, 10.6).
    3. **Checkpoint quiz** — when a clip was selected, fetch its transcript via
       ``youtube._fetch_transcript`` and ground the quiz in it (Req 11.1); when
       the transcript is ``None`` (or no clip was selected), generate the quiz
       from model knowledge with ``transcript=None`` (Req 11.2). The quiz is
       always exactly 3 validated MCQs from :func:`generate_quiz` (Req 11.3);
       this never calls the DB-coupled ``quiz.generate_and_store_questions``
       (Req 11.5). If a valid 3-question quiz cannot be produced,
       :func:`generate_quiz` raises so the caller surfaces an error rather than a
       partial checkpoint (Req 11.8).

    Returns a :class:`NodePayload` carrying the node, the hook, the clip (or
    ``None``), and the 3-question checkpoint quiz.
    """
    # The Intuition_Card hook and the clip pipeline (clip_query → search →
    # transcript) are independent, so run them concurrently to cut latency —
    # otherwise the hook LLM call and the clip-query LLM call happen
    # back-to-back. The quiz still runs afterward because it depends on the
    # clip's transcript.
    with ThreadPoolExecutor(max_workers=2) as executor:
        hook_future = executor.submit(_safe_hook, node, goal)
        clip_future = executor.submit(_resolve_clip, node, goal)
        hook = hook_future.result()
        clip_out, transcript_text = clip_future.result()

    # Transcript-grounded when one is available, else model-knowledge (Req 11.2).
    # Checkpoints are SOFT and must never hard-block the loop (Req 23.4): if
    # generate_quiz cannot assemble a full 3-question quiz (e.g. repeated LLM
    # parse failures), salvage whatever valid questions a best-effort pass
    # produced rather than letting the node delivery 500. The frontend renders
    # however many questions arrive (and treats an empty quiz as skippable).
    try:
        quiz_questions = generate_quiz(node, transcript_text)
    except Exception as exc:
        logger.warning(
            f"[game] generate_quiz could not produce a full quiz for '{node}'; "
            f"delivering a best-effort (possibly short) checkpoint: {exc}"
        )
        quiz_questions = _best_effort_quiz(node, transcript_text)

    return NodePayload(node=node, hook=hook, clip=clip_out, quiz=quiz_questions)


# ---------------------------------------------------------------------------
# Phase 2: Spaced decay and Defend scheduling (Req 14)
#
# A mastered Node decays on a spaced schedule and can be defended with a
# re-quiz. Everything here is pure and deterministic: ``now`` is always passed
# in as a parameter so the transitions are trivially testable and never read the
# wall clock implicitly. The Defend re-quiz reuses the SAME quiz engine as a
# normal Node quiz (``generate_quiz`` via ``deliver_node``, Req 14.4) — there is
# no separate generation path.
# ---------------------------------------------------------------------------

# Node_State: the display/scheduling state of a Node on the Skill_Tree_Map
# (Req 19.3). In Phase 1 these are display-only; in Phase 2 the DECAYING and
# MASTERED states are driven by the Decay_Schedule transitions below.
NodeState = Literal["LOCKED", "NEXT", "CLEARED", "DECAYING", "MASTERED"]

# Decay_Schedule: the spaced intervals, in days, between Defend re-quizzes for a
# mastered Node — 1 day → 3 days → 7 days → 16 days (Req 14.1). After the final
# 16-day interval the cadence holds at 16 days (the step clamps at the last
# interval), so a long-mastered Node keeps decaying on the widest spacing rather
# than ever being scheduled past the table.
DECAY_SCHEDULE_DAYS: tuple[int, ...] = (1, 3, 7, 16)
_DECAY_LAST_STEP = len(DECAY_SCHEDULE_DAYS) - 1


@dataclass(frozen=True)
class DecaySchedule:
    """The next scheduled decay for a Node (Req 14.1, 14.3).

    - ``decay_due_at``: the timestamp at which the Node next becomes due for a
      Defend re-quiz.
    - ``decay_step``: the Decay_Schedule index to use the *next* time the Node is
      (re-)mastered, advanced one interval and clamped at the final 16-day step.
    """

    decay_due_at: datetime
    decay_step: int


@dataclass(frozen=True)
class DecayState:
    """A Node's resolved decay state after a transition (Req 14.2, 14.3).

    Carries the Node_State plus the schedule fields a caller persists
    (mirroring the ``game_progress`` columns ``state`` / ``decay_due_at`` /
    ``decay_step`` from the design): ``decay_due_at`` is ``None`` only when the
    Node has no live schedule.
    """

    state: NodeState
    decay_due_at: datetime | None
    decay_step: int


def _clamp_decay_step(decay_step: int) -> int:
    """Clamp a Decay_Schedule step into the valid ``[0, last]`` index range."""
    if decay_step < 0:
        return 0
    if decay_step > _DECAY_LAST_STEP:
        return _DECAY_LAST_STEP
    return decay_step


def schedule_next_due(decay_step: int, now: datetime) -> DecaySchedule:
    """Schedule a mastered Node's next decay due time (Req 14.1, 14.3).

    Uses the Decay_Schedule interval at ``decay_step`` (clamped into range) to
    set ``decay_due_at = now + interval`` and advances the step by one — clamped
    at the final 16-day interval — for the next mastery. Pure and deterministic:
    the same ``decay_step`` and ``now`` always yield the same schedule.

    Progression of due offsets across successive masteries starting at step 0:
    ``+1d`` (step→1), ``+3d`` (step→2), ``+7d`` (step→3), ``+16d`` (step→3),
    then ``+16d`` thereafter.
    """
    idx = _clamp_decay_step(decay_step)
    interval_days = DECAY_SCHEDULE_DAYS[idx]
    due_at = now + timedelta(days=interval_days)
    next_step = min(idx + 1, _DECAY_LAST_STEP)
    return DecaySchedule(decay_due_at=due_at, decay_step=next_step)


def is_decaying(decay_due_at: datetime | None, now: datetime) -> bool:
    """True when a mastered Node is past its decay due time (Req 14.2).

    A Node with no scheduled due time (``decay_due_at is None``) is never
    decaying. Otherwise the Node is decaying once ``now`` reaches or passes the
    due time. Pure: depends only on its two arguments.
    """
    if decay_due_at is None:
        return False
    return now >= decay_due_at


def mark_mastered(decay_step: int, now: datetime) -> DecayState:
    """Master a Node and schedule its first/next decay (Req 14.1).

    Sets the Node_State to ``MASTERED`` and schedules the next decay due time via
    :func:`schedule_next_due`, returning the resolved :class:`DecayState` the
    caller persists. ``decay_step`` is ``0`` on the very first mastery and the
    previously advanced step on a re-mastery.
    """
    sched = schedule_next_due(decay_step, now)
    return DecayState(
        state="MASTERED",
        decay_due_at=sched.decay_due_at,
        decay_step=sched.decay_step,
    )


def refresh_decay_state(
    state: NodeState,
    decay_due_at: datetime | None,
    decay_step: int,
    now: datetime,
) -> DecayState:
    """Flag a past-due mastered Node as DECAYING (Req 14.2).

    While a ``MASTERED`` Node is past its decay due time it is flagged due for a
    Defend re-quiz and its Node_State becomes ``DECAYING``; the schedule fields
    are preserved so the Defend can advance from the correct step. Any other
    state — or a mastered Node not yet past due — is returned unchanged. Pure and
    idempotent: a Node already ``DECAYING`` stays ``DECAYING``.
    """
    if state == "MASTERED" and is_decaying(decay_due_at, now):
        return DecayState(
            state="DECAYING",
            decay_due_at=decay_due_at,
            decay_step=decay_step,
        )
    return DecayState(state=state, decay_due_at=decay_due_at, decay_step=decay_step)


def defend_passed(score_pct: float) -> bool:
    """A Defend re-quiz is passed when the score lands in the CLIMB band (Req 14.3).

    The Defend re-quiz is graded by the same deterministic banding as a normal
    Node quiz (:func:`band`): a score that climbs (``> 0.70``) restores mastery.
    """
    return band(score_pct) == "CLIMB"


def apply_defend(score_pct: float, decay_step: int, now: datetime) -> DecayState:
    """Resolve a Defend re-quiz outcome for a decaying Node (Req 14.3).

    When the re-quiz is passed (the score lands in the CLIMB band, see
    :func:`defend_passed`), the Node is restored to ``MASTERED`` and advanced to
    the next Decay_Schedule interval via :func:`schedule_next_due` (Req 14.3).
    When it is not passed, the Node stays ``DECAYING`` — still due ``now`` so the
    learner can retry — and its ``decay_step`` is left unchanged so the next pass
    resumes from the correct interval. Pure and deterministic.

    The Defend re-quiz itself runs through the same quiz engine as a normal Node
    quiz; see :func:`deliver_defend` (Req 14.4).
    """
    if defend_passed(score_pct):
        return mark_mastered(decay_step, now)
    return DecayState(
        state="DECAYING",
        decay_due_at=now,
        decay_step=_clamp_decay_step(decay_step),
    )


def deliver_defend(node: str, goal: str) -> NodePayload:
    """Deliver a Defend re-quiz for a decaying Node (Req 14.4).

    A Defend re-quiz runs through the SAME quiz engine as a normal Node quiz:
    this delegates straight to :func:`deliver_node`, producing the same
    Intuition_Card + Clip + fresh 3-question Checkpoint_Quiz beat
    (``generate_quiz``). There is no separate Defend generation path — the only
    difference is how the graded result is applied afterward (:func:`apply_defend`).
    """
    return deliver_node(node, goal)


# ---------------------------------------------------------------------------
# Phase 2: Streaks and the Freeze_Token (Req 15)
#
# The Streak counts days on which the learner ADVANCED a Node or DEFENDED a Node
# — never raw app opens (Req 15.4). Like the decay helpers above, everything
# here is pure and deterministic: the calendar ``day`` is always passed in as a
# parameter (never read from the wall clock), so the transitions are trivially
# testable. The streak state mirrors what a caller persists: the last day the
# streak was accounted for, the current consecutive-day count, and the number of
# Freeze_Tokens held.
#
# Usage contract: ``update_streak`` is called once per calendar day, in
# non-decreasing day order, with whether a qualifying advance-or-defend activity
# happened that day. It folds in any inactive days that elapsed since the last
# accounted day, consuming one Freeze_Token per inactive day to preserve the
# Streak (Req 15.2) and resetting the Streak to zero once no token remains
# (Req 15.3).
# ---------------------------------------------------------------------------

# StreakActivity: the kinds of activity a day can carry. Only ADVANCE (a CLIMB
# that advances a Node toward the Goal_Node) and DEFEND (a passed Defend
# re-quiz on a decaying Node) count toward the Streak; OPEN (a raw app open) and
# NONE never do (Req 15.1, 15.4).
StreakActivity = Literal["ADVANCE", "DEFEND", "OPEN", "NONE"]

_QUALIFYING_ACTIVITIES: frozenset[str] = frozenset({"ADVANCE", "DEFEND"})


def is_qualifying_activity(activity: StreakActivity | str) -> bool:
    """True only for advance-or-defend activity (Req 15.1, 15.4).

    The Streak is based on advancing or defending a Node, never on raw daily app
    opens: ``ADVANCE`` and ``DEFEND`` qualify; ``OPEN`` and ``NONE`` (and any
    other value) do not.
    """
    return activity in _QUALIFYING_ACTIVITIES


@dataclass(frozen=True)
class StreakState:
    """A learner's Streak state (Req 15).

    Mirrors what a caller persists:

    - ``last_active_date``: the most recent calendar day the Streak was accounted
      for — the last qualifying-activity day, or the last inactive day already
      folded in while settling. ``None`` before any day has been processed.
    - ``count``: the current consecutive-day Streak length.
    - ``freeze_tokens``: the number of Freeze_Tokens held, each able to preserve
      the Streak across one inactive day (Req 15.2).
    """

    last_active_date: date | None
    count: int
    freeze_tokens: int


def _settle_inactive_days(count: int, tokens: int, inactive_days: int) -> tuple[int, int]:
    """Fold ``inactive_days`` of no qualifying activity into a streak (Req 15.2, 15.3).

    Each inactive day consumes one Freeze_Token to preserve the Streak if one is
    held (Req 15.2); the first inactive day with no token remaining resets the
    Streak to zero (Req 15.3), after which the remaining inactive days are no-ops.
    A Streak already at zero has nothing to preserve, so no token is spent.
    Returns the resolved ``(count, freeze_tokens)``. Pure and deterministic.
    """
    if inactive_days <= 0 or count <= 0:
        return count, tokens
    if tokens >= inactive_days:
        # A token covers every inactive day; the Streak is fully preserved.
        return count, tokens - inactive_days
    # Tokens run out before the gap closes: spend them all, then the next
    # uncovered inactive day resets the Streak to zero (Req 15.3).
    return 0, 0


def update_streak(
    state: StreakState,
    day: date,
    has_qualifying_activity: bool,
) -> StreakState:
    """Advance the Streak to ``day`` given that day's activity (Req 15.1-15.3).

    On a qualifying advance-or-defend day the Streak counts that day and
    increments (Req 15.1); on an inactive day a Freeze_Token is consumed to
    preserve the Streak if held (Req 15.2), otherwise the Streak resets to zero
    (Req 15.3). Any inactive days that elapsed since ``last_active_date`` are
    folded in first via :func:`_settle_inactive_days`. Pure and deterministic:
    the same prior state, day, and activity flag always yield the same result.

    Called once per calendar day in non-decreasing day order; a ``day`` at or
    before ``last_active_date`` is treated as already accounted for and returns
    the state unchanged, so a second qualifying activity on the same day never
    double-counts (Req 15.1).
    """
    prev = state.last_active_date
    count = state.count
    tokens = state.freeze_tokens

    # First day ever processed: a qualifying activity starts the Streak at one;
    # an inactive day has no prior Streak to preserve or reset.
    if prev is None:
        if has_qualifying_activity:
            return StreakState(last_active_date=day, count=count + 1, freeze_tokens=tokens)
        return state

    # Same-day repeat or out-of-order day: already accounted for (Req 15.1).
    if day <= prev:
        return state

    # Fold in the inactive days strictly between the last accounted day and now.
    inactive_before = (day - prev).days - 1
    count, tokens = _settle_inactive_days(count, tokens, inactive_before)

    if has_qualifying_activity:
        # The qualifying activity counts this day toward the Streak (Req 15.1).
        return StreakState(last_active_date=day, count=count + 1, freeze_tokens=tokens)

    # ``day`` itself carries no qualifying activity: preserve via a token or
    # reset to zero (Req 15.2, 15.3). Advance the accounted-for pointer so the
    # day is not folded in again on a later call.
    count, tokens = _settle_inactive_days(count, tokens, 1)
    return StreakState(last_active_date=day, count=count, freeze_tokens=tokens)


def register_activity(
    state: StreakState,
    day: date,
    activity: StreakActivity | str,
) -> StreakState:
    """Update the Streak for a day's activity, classifying it first (Req 15.1, 15.4).

    Convenience wrapper that maps the ``activity`` to whether it qualifies via
    :func:`is_qualifying_activity` — so a raw app ``OPEN`` is treated exactly like
    an inactive day (Req 15.4) — and delegates to :func:`update_streak`.
    """
    return update_streak(state, day, is_qualifying_activity(activity))


# ---------------------------------------------------------------------------
# Phase 2: Best-effort Supabase persistence (Req 16)
#
# Sessions, per-node progress, and quiz attempts are persisted to the Phase 2
# tables ``game_sessions`` / ``game_progress`` / ``attempts`` through the reused
# ``db/supabase.get_client()`` leaf (Req 16.1, 16.2). These tables are created by
# the operator-run migration ``scripts/migration_adaptive_learning_game.sql``.
#
# Persistence is a pure SIDE EFFECT, decoupled from the stateless orchestration:
# ``start_session`` / ``decide`` / ``deliver_node`` keep their pure return values
# unchanged and never write to Supabase themselves. A caller (the Game_Router)
# may invoke these helpers afterward as OPTIONAL best-effort writes. Each helper
# is wrapped in try/except, logs on failure, and returns a ``bool`` indicating
# whether the write landed — it NEVER raises and NEVER blocks the learning loop
# (Req 16.2, 16.3). Because the import surface stays leaf-only (``db.supabase``
# is a leaf like ``llm`` / ``quiz`` / ``youtube``), this touches none of the
# pipeline / coherence / arc-assembler / quota-pool internals (Req 22.8).
# ---------------------------------------------------------------------------

# Phase 2 table names (created by migration_adaptive_learning_game.sql).
_GAME_SESSIONS_TABLE = "game_sessions"
_GAME_PROGRESS_TABLE = "game_progress"
_ATTEMPTS_TABLE = "attempts"


def _utcnow_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string for ``timestamptz`` columns."""
    return datetime.now(timezone.utc).isoformat()


def _iso_or_none(value: datetime | str | None) -> str | None:
    """Serialize a datetime (or pass through a string) to ISO-8601, or ``None``.

    ``game_progress.decay_due_at`` is a nullable ``timestamptz``; the decay
    helpers carry it as a :class:`datetime` (or ``None`` when a node has no live
    schedule), which this coerces into the string Supabase expects.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def persist_session(
    session_id: str,
    user_id: str,
    goal: str,
    floor_node: str | None = None,
) -> bool:
    """Best-effort upsert of one ``game_sessions`` row (Req 16.1-16.3).

    Writes (or refreshes) the session row keyed by ``session_id`` and owned by
    ``user_id`` (from ``require_user``, Req 17.1), recording the Goal_Node and
    the Floor_Node established so far. Wrapped in try/except: a Supabase failure
    is logged and swallowed so persistence never blocks the loop (Req 16.2,
    16.3). Returns ``True`` when the write landed, ``False`` otherwise.
    """
    try:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            "goal": goal,
            "floor_node": floor_node,
            "updated_at": _utcnow_iso(),
        }
        supabase.get_client().table(_GAME_SESSIONS_TABLE).upsert(
            row, on_conflict="session_id"
        ).execute()
        return True
    except Exception as exc:
        logger.warning(
            f"[game] best-effort persist_session failed for session "
            f"'{session_id}': {exc}"
        )
        return False


def persist_progress(
    session_id: str,
    user_id: str,
    node: str,
    state: NodeState | str,
    decay_due_at: datetime | str | None = None,
    decay_step: int = 0,
) -> bool:
    """Best-effort upsert of one ``game_progress`` row (Req 16.1-16.3).

    Writes the per-node state and Decay_Schedule fields, keyed by the
    ``(session_id, node)`` unique constraint so re-delivering a node updates the
    same row rather than duplicating it. ``decay_due_at`` is serialized to
    ISO-8601 (or left ``None`` for a node with no live schedule). Wrapped in
    try/except: a Supabase failure is logged and swallowed so persistence never
    blocks the loop (Req 16.2, 16.3). Returns ``True`` when the write landed,
    ``False`` otherwise.
    """
    try:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            "node": node,
            "state": state,
            "decay_due_at": _iso_or_none(decay_due_at),
            "decay_step": decay_step,
            "updated_at": _utcnow_iso(),
        }
        supabase.get_client().table(_GAME_PROGRESS_TABLE).upsert(
            row, on_conflict="session_id,node"
        ).execute()
        return True
    except Exception as exc:
        logger.warning(
            f"[game] best-effort persist_progress failed for node '{node}' "
            f"(session '{session_id}'): {exc}"
        )
        return False


def persist_attempt(
    session_id: str,
    user_id: str,
    node: str,
    band: Band | str,
    score_pct: float,
    missed: list[str] | None = None,
    correct: list[str] | None = None,
) -> bool:
    """Best-effort insert of one ``attempts`` row (Req 16.1-16.3).

    Appends a quiz-attempt record — the graded ``band`` and ``score_pct`` plus
    the Missed_Concepts / Correct_Concepts tag arrays — to the append-only
    ``attempts`` history. Wrapped in try/except: a Supabase failure is logged and
    swallowed so persistence never blocks the loop (Req 16.2, 16.3). Returns
    ``True`` when the write landed, ``False`` otherwise.
    """
    try:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            "node": node,
            "band": band,
            "score_pct": score_pct,
            "missed": list(missed or []),
            "correct": list(correct or []),
        }
        supabase.get_client().table(_ATTEMPTS_TABLE).insert(row).execute()
        return True
    except Exception as exc:
        logger.warning(
            f"[game] best-effort persist_attempt failed for node '{node}' "
            f"(session '{session_id}'): {exc}"
        )
        return False


# ---------------------------------------------------------------------------
# Phase 2: Account-linked sessions and resume precedence (Req 17)
#
# The backend is stateless in Phase 1: the client (the Game_State_Machine) owns
# the loop and holds session state in localStorage. Phase 2 layers best-effort
# server-side persistence on top (above), keyed by the caller's user id from
# ``require_user`` (Req 17.1). When an authenticated learner resumes, the
# Game_Service loads their persisted progress from Supabase (Req 16.4) and the
# resume helpers below prefer that persisted progress over local-only state
# whenever both are present (Req 17.2).
#
# Like the persist_* helpers, every read here is best-effort: wrapped in
# try/except, logged on failure, and degrades to "no persisted progress" rather
# than raising — so a Supabase outage never blocks a resume, the loop just
# falls back to the client's local-only state. The import surface stays
# leaf-only (``db.supabase``), touching none of the pipeline / coherence /
# arc-assembler / quota-pool internals (Req 22.8).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedNode:
    """One persisted per-node progress row loaded from ``game_progress``.

    Mirrors the columns the Skill_Tree_Map needs to rehydrate a node on resume:
    the ``node`` name, its ``state`` (Node_State), and the Decay_Schedule fields
    (``decay_due_at`` / ``decay_step``) so a resumed session keeps its spaced
    cadence.
    """

    node: str
    state: str
    decay_due_at: str | None
    decay_step: int


@dataclass(frozen=True)
class ResumeState:
    """The persisted state for a session, loaded best-effort on resume (Req 16.4).

    - ``found``: ``True`` only when a persisted ``game_sessions`` row exists for
      the session id; ``False`` when nothing is persisted (Supabase empty or
      unavailable), in which case the caller keeps its local-only state.
    - ``session_id`` / ``user_id`` / ``goal`` / ``floor_node``: the session row
      fields (``user_id`` ties the session to the caller, Req 17.1).
    - ``nodes``: the per-node progress rows for the Skill_Tree_Map.
    """

    found: bool
    session_id: str
    user_id: str | None = None
    goal: str | None = None
    floor_node: str | None = None
    nodes: tuple[PersistedNode, ...] = ()


def load_session(session_id: str) -> dict | None:
    """Best-effort load of one ``game_sessions`` row by id (Req 16.4).

    Returns the raw row dict for ``session_id`` (the most recent if more than one
    somehow matches), or ``None`` when no row exists or Supabase is unavailable.
    Wrapped in try/except: a failure is logged and swallowed so a resume never
    blocks the loop (Req 16.2, 16.3) — the caller falls back to local-only state.
    """
    try:
        res = (
            supabase.get_client()
            .table(_GAME_SESSIONS_TABLE)
            .select("session_id, user_id, goal, floor_node, updated_at")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(
            f"[game] best-effort load_session failed for session "
            f"'{session_id}': {exc}"
        )
        return None


def load_progress(session_id: str) -> list[PersistedNode]:
    """Best-effort load of the per-node ``game_progress`` rows (Req 16.4).

    Returns the persisted per-node progress for ``session_id`` as
    :class:`PersistedNode` records, or an empty list when none exist or Supabase
    is unavailable. Wrapped in try/except: a failure is logged and swallowed so a
    resume never blocks the loop (Req 16.2, 16.3) — the caller falls back to
    local-only state.
    """
    try:
        res = (
            supabase.get_client()
            .table(_GAME_PROGRESS_TABLE)
            .select("node, state, decay_due_at, decay_step")
            .eq("session_id", session_id)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning(
            f"[game] best-effort load_progress failed for session "
            f"'{session_id}': {exc}"
        )
        return []

    nodes: list[PersistedNode] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = str(row.get("node") or "").strip()
        if not node:
            continue
        try:
            decay_step = int(row.get("decay_step") or 0)
        except (TypeError, ValueError):
            decay_step = 0
        nodes.append(
            PersistedNode(
                node=node,
                state=str(row.get("state") or ""),
                decay_due_at=row.get("decay_due_at"),
                decay_step=decay_step,
            )
        )
    return nodes


def load_resume_state(session_id: str) -> ResumeState:
    """Load the persisted state for a session on resume (Req 16.4, 17.1).

    Combines :func:`load_session` and :func:`load_progress` into the
    :class:`ResumeState` the Game_Router returns when an authenticated learner
    resumes. When no session row is persisted (Supabase empty or unavailable)
    ``found`` is ``False`` and the caller keeps its local-only state. Both reads
    are best-effort and never raise (Req 16.2, 16.3).
    """
    session_row = load_session(session_id)
    if session_row is None:
        return ResumeState(found=False, session_id=session_id)

    return ResumeState(
        found=True,
        session_id=session_id,
        user_id=session_row.get("user_id"),
        goal=session_row.get("goal"),
        floor_node=session_row.get("floor_node"),
        nodes=tuple(load_progress(session_id)),
    )


def has_persisted_progress(state: ResumeState | None) -> bool:
    """True when ``state`` carries usable persisted progress (Req 17.2).

    Persisted progress is considered present when a session row was found — even
    with no per-node rows yet — since the persisted session itself (its goal,
    floor, and user link) is server-truth the resume should prefer over
    local-only state.
    """
    return bool(state is not None and state.found)


def prefer_persisted_progress(
    persisted: ResumeState | None,
    local: dict | None,
) -> dict:
    """Resolve the resume state, preferring persisted progress (Req 17.2).

    WHERE persisted progress exists for a learner (a persisted session row was
    found), THE Game_Service prefers that persisted progress over local-only
    state when both are present (Req 17.2): the returned dict is built from the
    persisted :class:`ResumeState`. When no persisted progress exists, the
    caller's local-only state is returned unchanged so a brand-new or
    offline-only session is never clobbered.

    The result is a plain dict the Game_Router maps onto the wire response:
    ``{source, session_id, goal, floor_node, nodes}`` where ``source`` is
    ``"persisted"`` or ``"local"`` so the Play_Surface knows which won.
    """
    if has_persisted_progress(persisted):
        return {
            "source": "persisted",
            "session_id": persisted.session_id,
            "user_id": persisted.user_id,
            "goal": persisted.goal,
            "floor_node": persisted.floor_node,
            "nodes": [
                {
                    "node": n.node,
                    "state": n.state,
                    "decay_due_at": n.decay_due_at,
                    "decay_step": n.decay_step,
                }
                for n in persisted.nodes
            ],
        }
    return {
        "source": "local",
        "session_id": (local or {}).get("session_id", ""),
        "user_id": (local or {}).get("user_id"),
        "goal": (local or {}).get("goal"),
        "floor_node": (local or {}).get("floor_node"),
        "nodes": list((local or {}).get("nodes") or []),
    }
