from __future__ import annotations

# Feature: structured-learn-curriculum, Task 1.4
# Unit tests asserting the gpt-4o-mini literal has been removed from the nine
# LLM call-site modules (except the intentional LEGACY_LLM_MODEL constant in
# llm.py) and that the single resolve_model helper honors LLM_MODEL verbatim.
# Validates: Requirements 0.6, 0.2

import os
from pathlib import Path

import pytest

from app.services import llm

LEGACY_LITERAL = "gpt-4o-mini"

# The nine LLM call-site modules centralized in task 1.3, relative to
# backend/app. llm.py is special-cased: it is allowed exactly the
# LEGACY_LLM_MODEL assignment line.
_APP_DIR = Path(llm.__file__).resolve().parent.parent  # backend/app

_MODULE_RELPATHS = (
    "agents/curriculum_agent.py",
    "agents/section_planner.py",
    "agents/recommendation_agent.py",
    "services/pipeline.py",
    "services/quiz.py",
    "services/story.py",
    "services/coherence.py",
    "services/segment_judge.py",
)


def _module_path(relpath: str) -> Path:
    path = _APP_DIR / relpath
    assert path.is_file(), f"expected module to exist: {path}"
    return path


@pytest.mark.parametrize("relpath", _MODULE_RELPATHS)
def test_no_legacy_literal_in_call_site_modules(relpath: str) -> None:
    """No gpt-4o-mini literal remains in the eight non-llm call-site modules."""
    source = _module_path(relpath).read_text(encoding="utf-8")
    assert LEGACY_LITERAL not in source, (
        f"{relpath} still contains a {LEGACY_LITERAL!r} literal; "
        "it must resolve the model through llm.resolve_model"
    )


def test_llm_module_only_keeps_legacy_constant() -> None:
    """llm.py may reference gpt-4o-mini only in the LEGACY_LLM_MODEL constant."""
    source = Path(llm.__file__).resolve().read_text(encoding="utf-8")
    offending = [
        line
        for line in source.splitlines()
        if LEGACY_LITERAL in line and "LEGACY_LLM_MODEL" not in line
    ]
    assert not offending, (
        "llm.py contains an unexpected gpt-4o-mini literal outside the "
        f"LEGACY_LLM_MODEL constant: {offending!r}"
    )
    # And the constant itself is still present and correct.
    assert llm.LEGACY_LLM_MODEL == LEGACY_LITERAL


def test_resolve_model_honors_env_through_single_helper() -> None:
    """LLM_MODEL=gpt-4o and LLM_MODEL=gpt-4o-mini both resolve via resolve_model.

    Validates Requirement 0.2 (LLM_MODEL honored verbatim, incl. rollback) and
    0.6 (single centralized helper). Env and cache are restored afterward.
    """
    saved_llm_model = os.environ.get("LLM_MODEL")
    saved_strong_model = os.environ.get("STRONG_MODEL")
    try:
        # Default gpt-4o resolves to gpt-4o.
        os.environ["LLM_MODEL"] = "gpt-4o"
        os.environ.pop("STRONG_MODEL", None)
        llm.reset_model_config_cache()
        assert llm.resolve_model() == "gpt-4o"

        # Rollback value gpt-4o-mini is honored verbatim through the same helper.
        os.environ["LLM_MODEL"] = "gpt-4o-mini"
        llm.reset_model_config_cache()
        assert llm.resolve_model() == "gpt-4o-mini"
    finally:
        if saved_llm_model is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = saved_llm_model
        if saved_strong_model is None:
            os.environ.pop("STRONG_MODEL", None)
        else:
            os.environ["STRONG_MODEL"] = saved_strong_model
        llm.reset_model_config_cache()
