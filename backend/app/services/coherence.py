"""Coherence_Judge: arc-level sequence evaluation and repair for the learning path.

This module implements the Coherence_Judge component from the design's pure-core /
thin-LLM-shell architecture. All correctness-critical decision functions (clamping,
round selection, continuation policy) are pure — no I/O, no external services —
so they are unit- and property-testable offline (Req 8.4).

LLM shells (evaluate, run_repair_loop) are added in later tasks; this module
currently exposes only the score-handling helpers.
"""
import json
import logging
import os
from typing import Any, get_args

from app.models.schemas import Clip, CoherenceDefect, CoherenceResult, DefectType, PedagogicalRole

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

_openai_client = None


def _get_client():
    """Lazily construct the OpenAI client (keeps the module import-light)."""
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


_VALID_DEFECT_TYPES: frozenset[str] = frozenset(get_args(DefectType))
_VALID_ROLES: frozenset[str] = frozenset(get_args(PedagogicalRole))


def clamp_score(value: Any) -> float:
    """Clamp a raw score value to [0.0, 1.0], rounded to 2 decimal places.

    Rules (Req 4.3, 7.8, 7.9):
    - A numeric value in range maps to its nearest 2-decimal-place value.
    - A numeric value below 0.0 maps to 0.0.
    - A numeric value above 1.0 maps to 1.0.
    - A missing (None) or non-numeric input maps to 0.5.

    Args:
        value: Any raw score value from a model response or downstream caller.

    Returns:
        A float in [0.0, 1.0] rounded to 2 decimal places.
    """
    if value is None:
        return 0.5
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.5
    if numeric != numeric:
        # NaN check — float("nan") != float("nan") is True
        return 0.5
    clamped = max(0.0, min(1.0, numeric))
    return round(clamped, 2)


def best_round(rounds: list[CoherenceResult]) -> CoherenceResult:
    """Return the CoherenceResult with the highest coherence_score.

    On ties (equal coherence_score), the result with the smallest round_index
    is returned (Req 4.7, 4.8).

    Args:
        rounds: A non-empty list of CoherenceResult instances.

    Returns:
        The CoherenceResult that has the highest coherence_score, with ties
        broken by the smallest round_index.

    Raises:
        ValueError: If rounds is empty.
    """
    if not rounds:
        raise ValueError("rounds must not be empty")
    return min(rounds, key=lambda r: (-r.coherence_score, r.round_index))


def should_continue(
    defects: list[CoherenceDefect],
    completed_rounds: int,
    max_rounds: int = 3,
) -> bool:
    """Return True if the repair loop should continue.

    Continuation requires BOTH conditions to hold (Req 4.6):
    - (a) defects is non-empty (there is still something to repair), AND
    - (b) completed_rounds < clamp(max_rounds, 1, 10)
          (budget has not been exhausted)

    The effective round cap is clamped to [1, 10] so callers cannot
    inadvertently disable the loop (0 or negative) or run unbounded (>10).

    Args:
        defects:         Defects surfaced in the most recent evaluation round.
        completed_rounds: Number of repair rounds already completed.
        max_rounds:      Requested maximum rounds; clamped to [1, 10].

    Returns:
        True if looping should continue, False otherwise.
    """
    effective_max = max(1, min(10, max_rounds))
    return bool(defects) and completed_rounds < effective_max


_EVALUATE_FALLBACK = CoherenceResult(coherence_score=0.5, defects=[], round_index=0)

_DEFECT_TYPES_LIST = ", ".join(sorted(_VALID_DEFECT_TYPES))
_ROLES_LIST = ", ".join(sorted(_VALID_ROLES))


def _build_evaluate_prompt(clips: list[Clip]) -> str:
    """Build the coherence evaluation prompt from the ordered clip sequence."""
    clip_entries = []
    for pos, clip in enumerate(clips, start=1):
        entry: dict = {
            "position": pos,
            "title": clip.title,
        }
        if clip.pedagogical_role is not None:
            entry["pedagogical_role"] = clip.pedagogical_role
        if clip.description:
            entry["description"] = clip.description
        if clip.transcript:
            # Truncate transcript to keep context window manageable.
            entry["transcript_excerpt"] = clip.transcript[:500]
        clip_entries.append(entry)

    clips_json = json.dumps(clip_entries, indent=2)

    return f"""You are an educational coherence evaluator. You will be given an ORDERED sequence of learning clips and must evaluate whether they form a pedagogically coherent arc.

Clip sequence (in playback order):
{clips_json}

Evaluate the WHOLE sequence for the following defect types:
- "prerequisite_gap": a clip assumes knowledge not introduced earlier in the sequence
- "conceptual_jump": consecutive clips make an abrupt conceptual leap with no bridging explanation
- "contradiction": two or more clips make contradictory claims or use contradictory definitions
- "redundancy": two or more clips cover the same ground without added value
- "unfilled_role": a required pedagogical role is absent from the sequence (e.g. no example, no definition)

Valid defect_type values: {_DEFECT_TYPES_LIST}
Valid role values (for unfilled_role defects): {_ROLES_LIST}

For each defect found, report:
- defect_type: one of the valid defect_type values above
- clip_positions: list of 1-based positions of the affected clips (e.g. [2, 3])
- role: the missing or relevant PedagogicalRole for "unfilled_role" defects; null for all others

Then produce a coherence_score from 0.0 (completely incoherent) to 1.0 (perfectly coherent), rounded to 2 decimal places.

Return a JSON object only — no markdown, no extra text:
{{
  "coherence_score": 0.85,
  "defects": [
    {{
      "defect_type": "prerequisite_gap",
      "clip_positions": [3],
      "role": null
    }}
  ]
}}

If no defects are found, return an empty defects array. Be accurate and conservative — only flag clear defects."""


def evaluate(clips: list[Clip]) -> CoherenceResult:
    """LLM shell: evaluate the coherence of the WHOLE ordered clip sequence.

    Prompts gpt-4o-mini to detect: Prerequisite_Gap, Conceptual_Jump,
    Contradiction, Redundancy, and unfilled Pedagogical_Role across the clip
    sequence. Each defect carries defect_type, clip_positions (1-based), and
    role (where applicable, else None).

    Produces a coherence_score clamped to [0.0, 1.0] rounded to 2dp via
    clamp_score.

    Returns CoherenceResult(coherence_score=..., defects=[...], round_index=0).
    An empty defects list is returned when the sequence is clean.

    Best-effort (Req 4.1, 4.2, 4.4): any failure returns
    CoherenceResult(coherence_score=0.5, defects=[], round_index=0).

    Args:
        clips: Ordered list of Clip instances representing the learning sequence.

    Returns:
        CoherenceResult with coherence_score, defects, and round_index=0.
    """
    if not clips:
        logger.info("[coherence] evaluate called with empty clip list; returning clean result")
        return CoherenceResult(coherence_score=1.0, defects=[], round_index=0)

    prompt = _build_evaluate_prompt(clips)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("[coherence] evaluate: model call failed: %s", exc)
        return _EVALUATE_FALLBACK

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present (mirrors pipeline.py pattern).
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        logger.warning("[coherence] evaluate: JSON parse failed: %s | raw=%s", exc, raw[:200])
        return _EVALUATE_FALLBACK

    if not isinstance(data, dict):
        logger.warning("[coherence] evaluate: model returned non-dict JSON")
        return _EVALUATE_FALLBACK

    # Parse and validate coherence_score.
    score = clamp_score(data.get("coherence_score"))

    # Parse defects — skip any with invalid defect_type.
    defects: list[CoherenceDefect] = []
    raw_defects = data.get("defects") or []
    if not isinstance(raw_defects, list):
        raw_defects = []

    for raw_defect in raw_defects:
        if not isinstance(raw_defect, dict):
            continue

        defect_type = raw_defect.get("defect_type")
        if not isinstance(defect_type, str) or defect_type not in _VALID_DEFECT_TYPES:
            logger.info(
                "[coherence] evaluate: skipping defect with invalid defect_type=%r",
                defect_type,
            )
            continue

        # Validate clip_positions: list of ints in [1, len(clips)].
        raw_positions = raw_defect.get("clip_positions") or []
        if not isinstance(raw_positions, list):
            raw_positions = []
        clip_positions = [
            int(p)
            for p in raw_positions
            if isinstance(p, (int, float)) and 1 <= int(p) <= len(clips)
        ]

        # Validate role (only meaningful for unfilled_role; must be None otherwise).
        raw_role = raw_defect.get("role")
        role = None
        if raw_role is not None:
            if isinstance(raw_role, str) and raw_role in _VALID_ROLES:
                role = raw_role  # type: ignore[assignment]
            else:
                logger.info(
                    "[coherence] evaluate: ignoring invalid role=%r on defect",
                    raw_role,
                )

        try:
            defects.append(
                CoherenceDefect(
                    defect_type=defect_type,  # type: ignore[arg-type]
                    clip_positions=clip_positions,
                    role=role,  # type: ignore[arg-type]
                )
            )
        except Exception as exc:
            logger.info("[coherence] evaluate: skipping malformed defect: %s", exc)
            continue

    return CoherenceResult(coherence_score=score, defects=defects, round_index=0)


# ---------------------------------------------------------------------------
# Repair planner helpers
# ---------------------------------------------------------------------------

def _build_clip_from_atom(atom: "LearningAtom", planned_ordinal: dict) -> "Clip":
    """Build a synthetic Clip from a LearningAtom (mirrors check_and_repair)."""
    from app.models.schemas import LearningAtom  # local import to avoid circulars
    return Clip(
        id=atom.id,
        topic_slug=atom.topic_slug,
        title=f"{atom.role}: {atom.concept}"[:200],
        description=(atom.transcript or "")[:1000] or None,
        video_url=atom.source_url,
        source_url=atom.source_url,
        pedagogical_role=atom.role,
        role_ordinal=planned_ordinal.get(atom.role),
        concept_label=atom.concept,
    )


def _apply_one_repair(
    defect: "CoherenceDefect",
    clips: list["Clip"],
    planned_arc: "PlannedArc",
    atom_by_role: dict,
    planned_ordinal: dict,
    re_search,
) -> tuple[list["Clip"], list["CoherenceDefect"]]:
    """Apply exactly one repair for a single defect.

    Returns the (possibly updated) clip list and any extra defects generated
    by this repair (e.g. a missing_piece defect when re-search returns None).

    Repair routing (Req 4.5):
    - unfilled_role  → fill from atom_pool  (atom_by_role lookup)
    - ordering defects (prerequisite_gap, conceptual_jump, contradiction,
                        redundancy, circular_dependency)
                     → reorder by planned_arc ordinal
    - missing_piece  → re-search (calls re_search(role) if provided; else
                        retains prior sequence and records missing_piece defect)
    """
    extra_defects: list[CoherenceDefect] = []

    if defect.defect_type == "unfilled_role":
        # Fill: find a matching atom and insert it at the planned ordinal position.
        role = defect.role
        if role is None:
            # Defect has no associated role — nothing we can fill; skip.
            return clips, extra_defects

        atom = atom_by_role.get(role)
        if atom is not None:
            new_clip = _build_clip_from_atom(atom, planned_ordinal)
            target_ordinal = planned_ordinal.get(role, 0)
            insert_pos = len(clips)
            for i, existing in enumerate(clips):
                existing_ordinal = planned_ordinal.get(
                    existing.pedagogical_role, 0
                )
                if existing_ordinal > target_ordinal:
                    insert_pos = i
                    break
            clips = list(clips)
            clips.insert(insert_pos, new_clip)
        # If no atom is available we cannot fill; leave sequence unchanged.
        return clips, extra_defects

    elif defect.defect_type == "missing_piece":
        # Re-search repair (Req 4.9).
        role = defect.role
        if re_search is not None and role is not None:
            candidate = re_search(role)
            if candidate is not None:
                # candidate should be a Clip; append/insert it.
                new_clip = candidate if isinstance(candidate, Clip) else None
                if new_clip is not None:
                    target_ordinal = planned_ordinal.get(role, 0)
                    insert_pos = len(clips)
                    for i, existing in enumerate(clips):
                        existing_ordinal = planned_ordinal.get(
                            existing.pedagogical_role, 0
                        )
                        if existing_ordinal > target_ordinal:
                            insert_pos = i
                            break
                    clips = list(clips)
                    clips.insert(insert_pos, new_clip)
                return clips, extra_defects
            else:
                # re-search returned None → retain prior sequence, record defect (Req 4.9).
                extra_defects.append(
                    CoherenceDefect(
                        defect_type="missing_piece",
                        clip_positions=defect.clip_positions,
                        role=defect.role,
                    )
                )
                return clips, extra_defects
        else:
            # No re_search callback provided → retain prior sequence, record defect (Req 4.9).
            extra_defects.append(
                CoherenceDefect(
                    defect_type="missing_piece",
                    clip_positions=defect.clip_positions,
                    role=defect.role,
                )
            )
            return clips, extra_defects

    else:
        # Ordering defect (prerequisite_gap, conceptual_jump, contradiction,
        # redundancy, circular_dependency) → reorder by planned_arc ordinal.
        def _sort_key(clip: "Clip") -> int:
            if (
                clip.pedagogical_role is not None
                and clip.pedagogical_role in planned_ordinal
            ):
                return planned_ordinal[clip.pedagogical_role]
            # Clips without a planned role fall to the tail.
            return len(planned_arc.roles) + 1

        clips = sorted(clips, key=_sort_key)
        return clips, extra_defects


# ---------------------------------------------------------------------------
# run_repair_loop
# ---------------------------------------------------------------------------

def run_repair_loop(
    clips: list["Clip"],
    planned_arc: "PlannedArc",
    atom_pool: list["LearningAtom"],
    max_rounds: int = 3,
    re_search=None,
) -> "CoherenceResult":
    """Drive the evaluate → repair → re-evaluate loop for coherence repair.

    Algorithm (Req 4.5, 4.6, 4.7, 4.8, 4.9):
    1. Evaluate the current clip sequence to obtain a CoherenceResult.
    2. Tag the result with the current round_index and record it.
    3. If should_continue (defects remain and rounds < max), apply exactly
       one repair per defect in the result, then go to 1.
    4. Return best_round across all recorded results.

    Repair actions per defect type:
    - ``unfilled_role``        → fill from atom_pool (first matching atom).
    - ``missing_piece``        → call re_search(role) if provided; if it
                                  returns None (or re_search is None), retain
                                  the prior sequence and record a
                                  ``missing_piece`` CoherenceDefect (Req 4.9).
    - all other defect types   → reorder clips by planned_arc ordinal.

    Each iteration produces one CoherenceResult tagged with round_index.
    The loop is bounded by should_continue; the function returns the
    CoherenceResult with the highest coherence_score (earliest on tie) across
    all completed rounds (Req 4.7, 4.8).

    Args:
        clips:       Ordered list of Clip instances to evaluate and repair.
        planned_arc: The Planned_Arc for this topic (used for ordinal lookups
                     when filling/reordering).
        atom_pool:   Pool of LearningAtoms available for filling missing roles.
        max_rounds:  Maximum number of repair rounds.  Effective value is
                     clamped to [1, 10] by should_continue.
        re_search:   Optional callable ``(role: PedagogicalRole) -> Clip | None``.
                     Called for ``missing_piece`` repairs.  Returning ``None``
                     retains the prior sequence and records a
                     ``missing_piece`` defect (Req 4.9).

    Returns:
        The CoherenceResult (with round_index) that has the highest
        coherence_score across all rounds, ties broken by smallest round_index.

    Requirements: 4.5, 4.6, 4.7, 4.8, 4.9
    """
    from app.models.schemas import LearningAtom, PlannedArc as _PlannedArc  # noqa

    # Build lookup tables from atom_pool and planned_arc once.
    atom_by_role: dict = {}
    for atom in atom_pool:
        if atom.role not in atom_by_role:
            atom_by_role[atom.role] = atom

    planned_ordinal: dict = {
        arc_role.role: arc_role.ordinal for arc_role in planned_arc.roles
    }

    # Working clip sequence (we never mutate the caller's list in place).
    working_clips: list[Clip] = list(clips)

    # Accumulate all round results for best_round selection.
    round_results: list[CoherenceResult] = []

    completed_rounds: int = 0

    # Initial evaluation (round 0).
    result = evaluate(working_clips)
    result = CoherenceResult(
        coherence_score=result.coherence_score,
        defects=result.defects,
        round_index=completed_rounds,
    )
    round_results.append(result)

    while should_continue(result.defects, completed_rounds, max_rounds):
        completed_rounds += 1

        # Apply exactly one repair per defect (Req 4.5).
        accumulated_extra_defects: list[CoherenceDefect] = []
        for defect in result.defects:
            working_clips, extra = _apply_one_repair(
                defect=defect,
                clips=working_clips,
                planned_arc=planned_arc,
                atom_by_role=atom_by_role,
                planned_ordinal=planned_ordinal,
                re_search=re_search,
            )
            accumulated_extra_defects.extend(extra)

        # Re-evaluate after all repairs for this round.
        result = evaluate(working_clips)
        # Merge any extra defects produced by repairs (e.g. missing_piece).
        merged_defects = result.defects + accumulated_extra_defects
        result = CoherenceResult(
            coherence_score=result.coherence_score,
            defects=merged_defects,
            round_index=completed_rounds,
        )
        round_results.append(result)

    return best_round(round_results)
