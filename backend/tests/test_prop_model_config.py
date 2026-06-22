"""Property-based test for the Model_Config pure core.

# Feature: structured-learn-curriculum, Property 9: LLM config is centralized and reversible (P0.1-P0.5)

The model defaults to ``gpt-4o``, is honored verbatim from ``LLM_MODEL``
(including the ``gpt-4o-mini`` rollback), and ``STRONG_MODEL`` tiering is opt-in
with a safe fallback to the standard tier. ``select_model`` is total over
arbitrary tier strings and resolution is deterministic.

Validates: Requirements 0.1, 0.2, 0.3, 0.5, 0.7
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.llm import (
    DEFAULT_LLM_MODEL,
    LEGACY_LLM_MODEL,
    ModelConfig,
    resolve_model_config,
    select_model,
)

# Values that resolve_model_config must treat as "unset" (None or blank/whitespace).
_BLANK = st.sampled_from([None, "", " ", "   ", "\t", "\n", "  \t \n "])

# Arbitrary non-empty model identifiers (must survive .strip() as non-empty).
_NON_EMPTY = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")

# Any string at all, used to exercise totality of select_model over tier inputs.
_ANY_TIER = st.text(max_size=20)


@settings(max_examples=100)
@given(blank_standard=_BLANK, blank_strong=_BLANK)
def test_default_resolves_to_gpt_4o_with_tiering_off(blank_standard, blank_strong):
    """P0.1: default (None/None or blank) -> standard == strong == gpt-4o."""
    cfg = resolve_model_config(blank_standard, blank_strong)
    assert cfg.standard_model == DEFAULT_LLM_MODEL == "gpt-4o"
    assert cfg.strong_model == DEFAULT_LLM_MODEL == "gpt-4o"
    # Tiering off: strong and standard selections coincide.
    assert select_model(cfg, "strong") == select_model(cfg, "standard")


@settings(max_examples=100)
@given(llm_model=_NON_EMPTY, blank_strong=_BLANK)
def test_non_empty_llm_model_honored_verbatim(llm_model, blank_strong):
    """P0.2: a non-empty LLM_MODEL value is honored verbatim (stripped)."""
    cfg = resolve_model_config(llm_model, blank_strong)
    assert cfg.standard_model == llm_model.strip()
    # P0.3: with strong unset, strong falls back to standard.
    assert cfg.strong_model == llm_model.strip()
    assert select_model(cfg, "standard") == llm_model.strip()


@settings(max_examples=100)
@given(blank_strong=_BLANK)
def test_gpt_4o_mini_rollback_is_honored(blank_strong):
    """P0.2: the explicit gpt-4o-mini rollback value is honored verbatim."""
    cfg = resolve_model_config(LEGACY_LLM_MODEL, blank_strong)
    assert cfg.standard_model == "gpt-4o-mini"
    assert select_model(cfg, "standard") == "gpt-4o-mini"
    # strong unset -> falls back to the rolled-back standard model.
    assert select_model(cfg, "strong") == "gpt-4o-mini"


@settings(max_examples=100)
@given(llm_model=_NON_EMPTY, blank_strong=_BLANK)
def test_strong_falls_back_to_standard_when_unset(llm_model, blank_strong):
    """P0.3: strong unset -> select_model(cfg, 'strong') == select_model(cfg, 'standard')."""
    cfg = resolve_model_config(llm_model, blank_strong)
    assert select_model(cfg, "strong") == select_model(cfg, "standard")


@settings(max_examples=100)
@given(standard=_NON_EMPTY, strong=_NON_EMPTY)
def test_strong_honored_when_set(standard, strong):
    """P0.3 (inverse): a set strong model is used for the strong tier."""
    cfg = resolve_model_config(standard, strong)
    assert cfg.strong_model == strong.strip()
    assert select_model(cfg, "strong") == strong.strip()
    assert select_model(cfg, "standard") == standard.strip()


@settings(max_examples=100)
@given(standard=_BLANK | _NON_EMPTY, strong=_BLANK | _NON_EMPTY, tier=_ANY_TIER)
def test_select_model_is_total_and_never_raises(standard, strong, tier):
    """P0.4: select_model is total over arbitrary tier strings; never raises.

    Always returns either the standard or strong model.
    """
    cfg = resolve_model_config(standard, strong)
    result = select_model(cfg, tier)  # type: ignore[arg-type]
    assert result in (cfg.standard_model, cfg.strong_model)
    # Only "strong" selects the strong model; anything else degrades to standard.
    if tier == "strong":
        assert result == cfg.strong_model
    else:
        assert result == cfg.standard_model


@settings(max_examples=100)
@given(standard=_BLANK | _NON_EMPTY, strong=_BLANK | _NON_EMPTY, tier=_ANY_TIER)
def test_determinism(standard, strong, tier):
    """P0.5: identical inputs yield identical ModelConfig and select_model output."""
    cfg_a = resolve_model_config(standard, strong)
    cfg_b = resolve_model_config(standard, strong)
    assert cfg_a == cfg_b
    assert isinstance(cfg_a, ModelConfig)
    assert select_model(cfg_a, tier) == select_model(cfg_b, tier)  # type: ignore[arg-type]
