"""Property-based test for clean-sequence coherence evaluation.

# Feature: content-hook-engagement, Property 17: Clean sequences produce an empty defect list

For any assembled sequence containing no injected defect, ``evaluate`` returns an
empty Coherence_Defect list together with a valid clamped Coherence_Score
(0.0 <= score <= 1.0, rounded to 2 decimal places).

``evaluate`` is the thin LLM shell, so the OpenAI client is mocked to return a
clean JSON payload (a coherence_score and ``"defects": []``). The empty-clip
fast path is also covered without the mock, since it short-circuits before any
model call.

Validates: Requirements 4.4
"""
import json

import pytest
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services import coherence


# ---------------------------------------------------------------------------
# Fake OpenAI client returning a clean (no-defect) evaluation payload.
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _CleanClient:
    """chat.completions.create returns JSON with a score and empty defects."""

    def __init__(self, coherence_score: float):
        payload = json.dumps({"coherence_score": coherence_score, "defects": []})

        class _Completions:
            def create(_self, *a, **k):
                return _FakeResponse(payload)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# ---------------------------------------------------------------------------
# Smart generators
# ---------------------------------------------------------------------------

def _clip(i: int) -> Clip:
    """Build a minimal valid Clip for position i."""
    return Clip(
        id=f"clip-{i}",
        topic_slug="binary-search",
        title=f"Clip {i}",
        video_url=f"https://example.com/{i}",
    )


# Clip lists of varied sizes (1..8 clips). The empty list is exercised
# separately in the fast-path test below.
_clip_lists = st.integers(min_value=1, max_value=8).map(
    lambda n: [_clip(i) for i in range(n)]
)

# Raw scores the model might emit, including out-of-range values that
# clamp_score must normalise into [0.0, 1.0].
_raw_scores = st.floats(
    min_value=-2.0, max_value=3.0, allow_nan=False, allow_infinity=False
)


# ---------------------------------------------------------------------------
# Property: a clean sequence yields no defects and a valid clamped score.
# ---------------------------------------------------------------------------

class TestCleanSequences:
    @settings(max_examples=100)
    @given(clips=_clip_lists, raw_score=_raw_scores)
    def test_clean_sequence_has_empty_defects_and_valid_score(
        self, clips, raw_score
    ):
        client = _CleanClient(raw_score)
        # Use a MonkeyPatch context (not the function-scoped fixture) so the
        # patch is applied and reverted for every generated input.
        with pytest.MonkeyPatch.context() as mp:
            # Patch both the factory and cached singleton so no real call occurs.
            mp.setattr(coherence, "_get_client", lambda: client)
            mp.setattr(coherence, "_openai_client", client)

            result = coherence.evaluate(clips)

        # No injected defects -> empty defect list (Req 4.4).
        assert result.defects == []

        # Score is clamped into range and rounded to 2 dp (Req 4.3, 4.4).
        assert 0.0 <= result.coherence_score <= 1.0
        assert result.coherence_score == round(result.coherence_score, 2)

        assert result.round_index == 0

    def test_empty_clip_fast_path_returns_clean_result(self):
        """evaluate([]) short-circuits to a clean result without a model call."""
        result = coherence.evaluate([])

        assert result.defects == []
        assert result.coherence_score == 1.0
        assert 0.0 <= result.coherence_score <= 1.0
        assert result.coherence_score == round(result.coherence_score, 2)
