from __future__ import annotations

import os
import json
import uuid
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from openai import OpenAI
from app.models.schemas import Topic, LearningPath

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_curated_cache: list[dict] | None = None

CURATED_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "curated_topics.json"


# ---------------------------------------------------------------------------
# Model_Config pure core (Phase 0, Req 0)
#
# Centralizes LLM model selection into one env-overridable config that defaults
# to gpt-4o, with optional per-step "strong" tiering. These functions are pure,
# deterministic, total, and never raise: they take already-read raw values (or
# None) and return the effective configuration / model string. Reading the
# environment belongs to the thin shell (task 1.3), not to these cores.
# ---------------------------------------------------------------------------

# A call-site "tier". Cheap, high-volume / low-stakes calls use "standard"; the
# hardest reasoning steps may opt into "strong".
ModelTier = Literal["standard", "strong"]

DEFAULT_LLM_MODEL: str = "gpt-4o"          # Req 0: new default
LEGACY_LLM_MODEL: str = "gpt-4o-mini"      # rollback target / cheap tier


@dataclass(frozen=True)
class ModelConfig:
    """The effective model configuration.

    standard_model resolves from env LLM_MODEL (default gpt-4o); strong_model
    resolves from env STRONG_MODEL and falls back to standard_model when unset.
    """

    standard_model: str
    strong_model: str


def resolve_model_config(
    llm_model: str | None, strong_model: str | None
) -> ModelConfig:
    """Pure: given the raw env values (or None), produce the effective config.

    - standard_model = llm_model when non-empty after strip, else DEFAULT_LLM_MODEL.
    - strong_model   = strong_model when non-empty after strip, else standard_model
      (tiering is OFF by default: "strong" falls back to "standard").

    Total and deterministic; never raises.
    """
    standard = llm_model.strip() if llm_model and llm_model.strip() else DEFAULT_LLM_MODEL
    strong = strong_model.strip() if strong_model and strong_model.strip() else standard
    return ModelConfig(standard_model=standard, strong_model=strong)


def select_model(config: ModelConfig, tier: ModelTier = "standard") -> str:
    """Pure total function: "strong" -> config.strong_model, anything else ->
    config.standard_model. Unknown tier values degrade to standard_model.

    Deterministic; never raises.
    """
    if tier == "strong":
        return config.strong_model
    return config.standard_model


# ---------------------------------------------------------------------------
# resolve_model shell (Phase 0, Req 0.4 / 0.6)
#
# Thin, best-effort wrapper around the Model_Config core. Reads LLM_MODEL /
# STRONG_MODEL from the environment once (cached at module level), treating
# blank/whitespace values as unset, and returns the model string for a tier.
# Never raises. A small reset hook lets tests re-read the environment.
# ---------------------------------------------------------------------------

_model_config_cache: ModelConfig | None = None


def _read_model_config() -> ModelConfig:
    """Read and cache the effective ModelConfig from the environment.

    Best-effort: blank/whitespace env values are treated as unset by the core.
    """
    global _model_config_cache
    if _model_config_cache is None:
        _model_config_cache = resolve_model_config(
            os.environ.get("LLM_MODEL"), os.environ.get("STRONG_MODEL")
        )
    return _model_config_cache


def reset_model_config_cache() -> None:
    """Test hook: clear the cached config so the next resolve re-reads env."""
    global _model_config_cache
    _model_config_cache = None


def resolve_model(tier: ModelTier = "standard") -> str:
    """Resolve the model string for a call-site tier from env (cached).

    Best-effort and total; never raises. Defaults to DEFAULT_LLM_MODEL if
    anything goes wrong reading the configuration.
    """
    try:
        return select_model(_read_model_config(), tier)
    except Exception:
        return DEFAULT_LLM_MODEL


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _curated_topics() -> list[dict]:
    """Load curated topic list once. Used to bias slug naming toward existing seeded topics."""
    global _curated_cache
    if _curated_cache is None:
        try:
            data = json.loads(CURATED_PATH.read_text())
            _curated_cache = [
                {"slug": slug, "name": meta["name"], "difficulty": meta.get("difficulty", "beginner")}
                for slug, meta in data["topics"].items()
            ]
        except Exception:
            _curated_cache = []
    return _curated_cache


SYSTEM_PROMPT = """You are a curriculum designer for an educational short-form video platform.
When a user describes what they want to learn, you:
1. Extract specific topics from their query
2. Order them from foundational to advanced (prerequisites first)
3. Assign difficulty levels
4. Return a structured JSON learning path

A library of pre-built topics exists. Only reuse a slug from this library if the user's query is asking about EXACTLY that topic. If the user is asking about something different (e.g. a specific framework, tool, or concept not in the library), create a new accurate slug. Never force-fit a query into an existing slug if it's not a genuine match.

Always return valid JSON matching the schema exactly. Slugs must be lowercase with hyphens.
"""

TOPIC_SCHEMA = """
{
  "summary": "one sentence describing the learning path",
  "topics": [
    {
      "slug": "topic-slug",
      "name": "Human Readable Name",
      "difficulty": "beginner|intermediate|advanced",
      "prerequisites": ["slug-of-prereq"],
      "rationale": "why this topic is ordered here"
    }
  ]
}
"""


def parse_learning_path(query: str, session_id: str | None = None) -> LearningPath:
    client = get_client()
    sid = session_id or str(uuid.uuid4())

    curated = _curated_topics()
    # Only inject the library for CS/ML/math queries — it's all tech topics and
    # confuses the LLM into returning wrong slugs for unrelated domains.
    CS_KEYWORDS = {"computer", "programming", "machine learning", "mathematics",
                   "algorithm", "data", "software", "statistics", "neural", "code"}
    query_lower = query.lower()
    library_relevant = any(kw in query_lower for kw in CS_KEYWORDS)
    curated_block = (
        "\n\nExisting topic library (REUSE these slugs when semantically applicable — only if the concept is an EXACT match):\n"
        + json.dumps(curated, indent=2)
        if curated and library_relevant else ""
    )

    logger.info(f"[LLM] Generating learning path for query='{query[:80]}'")
    try:
        response = client.chat.completions.create(
            model=resolve_model(),
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"User wants to learn: {query}{curated_block}\n\nReturn JSON matching this schema:\n{TOPIC_SCHEMA}",
                },
            ],
        )
    except Exception as e:
        logger.error(f"[LLM] OpenAI API call failed for query='{query[:80]}': {e}")
        raise

    raw = response.choices[0].message.content
    logger.debug(f"[LLM] Raw response length={len(raw)}")

    # Strip markdown code fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.error(f"[LLM] Failed to parse JSON response: {e} | raw={raw[:200]}")
        raise

    try:
        topics = [Topic(**t) for t in data["topics"]]
    except (KeyError, TypeError) as e:
        logger.error(f"[LLM] Unexpected response shape: {e} | keys={list(data.keys())}")
        raise

    logger.info(f"[LLM] Generated {len(topics)} topics for session={sid}")
    return LearningPath(
        session_id=sid,
        user_query=query,
        topics=topics,
        summary=data.get("summary", ""),
    )
