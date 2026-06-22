"""Per_Segment_Judge: per-segment quality and fit verdict for deep ingestion.

This module implements the Per_Segment_Judge component from the design's
pure-core / thin-LLM-shell architecture. Every correctness-critical decision
(quality-score normalization, threshold validation, and the pass/fail verdict)
is a pure function — no DB, no clock, no external services — so it is unit- and
property-testable offline (Req 9.6).

The score-normalization helper mirrors ``coherence.clamp_score`` exactly: a
missing/None/non-numeric/NaN raw value defaults to 0.5, out-of-range numerics
clamp to the nearer of 0.0/1.0, and the result is rounded to 2 decimal places.

The LLM shell (``judge_segment``) asks the model for a raw quality score and
fit judgment for one ``MappedSegment``, then runs the pure core
(``normalize_quality_score`` + ``judge_verdict``). It is best-effort: a model
failure after exhausting retries defaults the score to 0.5 and the fit to
``belongs`` so a transient error neither silently rejects good content nor
blocks the request path (Req 9.1, 9.2). It never raises.
"""
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.segment_mapper import MappedSegment

logger = logging.getLogger(__name__)

FitDecision = Literal["belongs", "off_role"]
VerdictStatus = Literal["passing", "failing"]

DEFAULT_QUALITY_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class SegmentVerdict:
    """The per-segment verdict produced by the Per_Segment_Judge.

    Attributes:
        quality_score: The normalized quality score in [0.0, 1.0], 2 dp.
        fit:           Whether the segment belongs in its mapped role.
        status:        'passing' iff the segment cleared both quality and fit.
        reason:        'ok' | 'insufficient_quality' | 'off_role'.
    """

    quality_score: float
    fit: FitDecision
    status: VerdictStatus
    reason: str


def normalize_quality_score(raw: object) -> float:
    """Normalize a raw quality score to [0.0, 1.0], rounded to 2 decimal places.

    Rules (Req 3.2, 3.3, 3.4) — mirrors ``coherence.clamp_score``:
    - A missing (None) or non-numeric input maps to 0.5 (Req 3.3).
    - A NaN input maps to 0.5 (Req 3.3).
    - A numeric value below 0.0 maps to 0.0; above 1.0 maps to 1.0 (Req 3.4).
    - An in-range numeric maps to its value rounded to 2 decimal places (Req 3.2).

    Args:
        raw: Any raw score value from a model response or downstream caller.

    Returns:
        A float in [0.0, 1.0] rounded to 2 decimal places.
    """
    if raw is None:
        return 0.5
    try:
        numeric = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    if numeric != numeric:
        # NaN check — float("nan") != float("nan") is True
        return 0.5
    clamped = max(0.0, min(1.0, numeric))
    return round(clamped, 2)


def validate_quality_threshold(raw: object) -> float:
    """Validate and clamp a configured quality threshold to [0.0, 1.0].

    A missing/None/non-numeric/NaN value defaults to 0.5; an in-range value is
    used as-is; an out-of-range value clamps to the nearer of 0.0/1.0 (Req 3.6).

    Args:
        raw: Any raw threshold value from configuration or a caller.

    Returns:
        A float threshold in [0.0, 1.0].
    """
    if raw is None:
        return DEFAULT_QUALITY_THRESHOLD
    try:
        numeric = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_QUALITY_THRESHOLD
    if numeric != numeric:
        # NaN check
        return DEFAULT_QUALITY_THRESHOLD
    return max(0.0, min(1.0, numeric))


def judge_verdict(
    quality_score: float,
    fit: FitDecision,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
) -> SegmentVerdict:
    """Produce the per-segment verdict from a quality score and fit decision.

    A segment is PASSING iff BOTH conditions hold (Req 3.8):
    - ``quality_score >= threshold`` (sufficient quality), AND
    - ``fit == 'belongs'`` (on-role).

    Otherwise the verdict is failing. Reason selection (Req 3.6, 3.7):
    - quality below threshold -> 'insufficient_quality'.
    - fit == 'off_role'       -> 'off_role'.
    - both fail               -> 'insufficient_quality' takes precedence.
    - passing                 -> 'ok'.

    Both conditions are required to pass — neither dominates the other.
    Pure, total, and deterministic.

    Args:
        quality_score: Normalized quality score in [0.0, 1.0].
        fit:           The Fit_Decision for the segment.
        threshold:     The Quality_Threshold to compare against (default 0.5).

    Returns:
        A SegmentVerdict capturing the score, fit, status, and reason.
    """
    has_quality = quality_score >= threshold
    on_role = fit == "belongs"

    if has_quality and on_role:
        return SegmentVerdict(
            quality_score=quality_score,
            fit=fit,
            status="passing",
            reason="ok",
        )

    # Failing — quality shortfall is reported in preference to off-role.
    reason = "insufficient_quality" if not has_quality else "off_role"
    return SegmentVerdict(
        quality_score=quality_score,
        fit=fit,
        status="failing",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# LLM shell
# ---------------------------------------------------------------------------

# Reuse the on-demand pipeline's model so the judge stays consistent with the
# rest of the ingestion path. Resolved through the central llm.resolve_model
# helper at the call site (strong tier for this hardest reasoning step).

_VALID_FITS: frozenset[str] = frozenset(("belongs", "off_role"))


def _build_judge_prompt(segment: "MappedSegment") -> str:
    """Build the per-segment quality+fit prompt for one Mapped_Segment.

    Describes the atom (concept, mapped pedagogical role, and a transcript
    excerpt) and asks the model for a quality_score in [0.0, 1.0] plus a fit
    judgment of whether the segment genuinely belongs in its mapped role.
    """
    atom = segment.atom
    transcript_excerpt = (atom.transcript or "")[:800]

    payload = {
        "concept": atom.concept,
        "mapped_pedagogical_role": segment.pedagogical_role,
        "role_ordinal": segment.role_ordinal,
        "transcript_excerpt": transcript_excerpt,
    }
    payload_json = json.dumps(payload, indent=2)

    return f"""You are an educational content quality judge. You will be given ONE learning segment that has been mapped to a single pedagogical role in a topic's planned learning arc. Judge the segment in isolation.

Segment:
{payload_json}

Evaluate two things:
1. quality_score — how clear, accurate, and pedagogically useful this segment is on its own, from 0.0 (useless/incoherent) to 1.0 (excellent), rounded to 2 decimal places.
2. fit — whether the segment genuinely fulfils its mapped pedagogical role ("{segment.pedagogical_role}"):
   - "belongs": the content actually serves that role.
   - "off_role": the content does not match that role (e.g. an example mapped as a definition).

Return a JSON object only — no markdown, no extra text:
{{
  "quality_score": 0.82,
  "fit": "belongs"
}}

Be accurate and conservative."""


def judge_segment(
    segment: "MappedSegment",
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
    *,
    max_retries: int = 2,
    timeout_s: int = 10,
) -> SegmentVerdict:
    """LLM shell: judge ONE Mapped_Segment for quality and role fit.

    Asks the model for a raw quality score and a fit judgment for the segment,
    then runs the pure core (``normalize_quality_score`` + ``judge_verdict``)
    to produce the final ``SegmentVerdict`` (Req 3.1, 3.2, 3.5).

    Best-effort and resilient (Req 9.1, 9.2): the model call is attempted up to
    ``1 + max_retries`` times with a per-call ``timeout_s``. A call that raises
    or exceeds the timeout counts as a failed attempt. If every attempt fails,
    the score defaults to 0.5 and the fit defaults to ``belongs`` so a transient
    error neither silently rejects good content nor blocks the request path.
    This function never raises.

    Args:
        segment:     The Mapped_Segment to judge.
        threshold:   Quality_Threshold for the verdict (default 0.5).
        max_retries: Number of retries after the first attempt (>= 0).
        timeout_s:   Per-call timeout in seconds.

    Returns:
        A ``SegmentVerdict`` from ``judge_verdict``.
    """
    # Defensive: a non-negative attempt budget regardless of caller input.
    attempts = 1 + max(0, max_retries)

    prompt = _build_judge_prompt(segment)

    raw_score: object = None
    fit: FitDecision = "belongs"

    parsed = False
    for attempt in range(attempts):
        try:
            # Lazily reuse the existing OpenAI client from the LLM module.
            from app.services.llm import get_client, resolve_model

            client = get_client()
            response = client.chat.completions.create(
                model=resolve_model("strong"),
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_s,
            )
        except Exception as exc:
            logger.warning(
                "[segment_judge] judge_segment: model call failed "
                "(attempt %d/%d): %s",
                attempt + 1,
                attempts,
                exc,
            )
            continue

        try:
            raw = response.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning(
                "[segment_judge] judge_segment: malformed model response "
                "(attempt %d/%d): %s",
                attempt + 1,
                attempts,
                exc,
            )
            continue

        # Strip markdown code fences if present (mirrors coherence/pipeline).
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            logger.warning(
                "[segment_judge] judge_segment: JSON parse failed "
                "(attempt %d/%d): %s | raw=%s",
                attempt + 1,
                attempts,
                exc,
                raw[:200],
            )
            continue

        if not isinstance(data, dict):
            logger.warning(
                "[segment_judge] judge_segment: model returned non-dict JSON "
                "(attempt %d/%d)",
                attempt + 1,
                attempts,
            )
            continue

        # Successful parse: capture raw score and validate fit.
        raw_score = data.get("quality_score")
        raw_fit = data.get("fit")
        if isinstance(raw_fit, str) and raw_fit in _VALID_FITS:
            fit = raw_fit  # type: ignore[assignment]
        else:
            # Unknown/missing fit -> best-effort default of 'belongs' (Req 9.1).
            logger.info(
                "[segment_judge] judge_segment: invalid fit=%r; defaulting to "
                "'belongs'",
                raw_fit,
            )
            fit = "belongs"
        parsed = True
        break

    if not parsed:
        # Exhausted all attempts: best-effort defaults (Req 9.1, 9.2).
        logger.warning(
            "[segment_judge] judge_segment: all %d attempt(s) failed; "
            "defaulting score=0.5, fit='belongs'",
            attempts,
        )
        raw_score = 0.5
        fit = "belongs"

    # normalize_quality_score also defaults None/non-numeric/NaN to 0.5 (Req 3.2).
    quality_score = normalize_quality_score(raw_score)
    return judge_verdict(quality_score, fit, threshold)
