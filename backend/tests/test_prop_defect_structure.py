"""Property-based test for defect structure well-formedness.

# Feature: content-hook-engagement, Property 15: Detected defects have a well-formed structure

For any evaluated sequence of n clips, every recorded ``CoherenceDefect`` has a
defect_type drawn from the defined set, clip_positions that are 1-based ordinals
within ``1..n``, and a role populated whenever the defect type pertains to a role.

``evaluate`` calls the LLM, so the OpenAI client is mocked to return controlled
JSON containing a mix of defects — including some with out-of-range
clip_positions and invalid defect_type/role values — to verify that evaluate's
own validation/normalization keeps only well-formed defects.

Validates: Requirements 4.2
"""
import json

from hypothesis import given, settings, strategies as st

from app.models.schemas import DefectType, PedagogicalRole
from app.services import coherence
from app.services.coherence import evaluate

from typing import get_args

from app.models.schemas import Clip


def _make_clip(i: int) -> Clip:
    return Clip(
        id=f"clip-{i}",
        topic_slug="binary-search",
        title=f"Clip {i}",
        video_url="https://example.com/v",
        hook_score=0.5,
    )


_VALID_DEFECT_TYPES = list(get_args(DefectType))
_VALID_ROLES = list(get_args(PedagogicalRole))


# --- fake OpenAI client returning controlled JSON -------------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _JSONClient:
    """chat.completions.create returns a fixed JSON string."""
    def __init__(self, content):
        self._content = content

        class _Completions:
            def create(_self, *a, **k):
                return _FakeResponse(self._content)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --- smart generators ------------------------------------------------------
# Generate raw defect dicts mixing well-formed and malformed values so that
# evaluate's normalization is exercised across the full input space.

def _defect_type_strategy():
    # Mix valid defect types with invalid/garbage values.
    return st.one_of(
        st.sampled_from(_VALID_DEFECT_TYPES),
        st.sampled_from(["bogus", "PREREQUISITE_GAP", "", "jump", "redundant"]),
        st.integers(),  # non-string types
        st.none(),
    )


def _position_strategy(n):
    # Mix in-range, out-of-range (low/high), and float positions.
    return st.one_of(
        st.integers(min_value=1, max_value=n),          # in range
        st.integers(min_value=-5, max_value=0),          # too low
        st.integers(min_value=n + 1, max_value=n + 20),  # too high
        st.floats(min_value=1.0, max_value=float(n)).map(float),
    )


def _role_strategy():
    return st.one_of(
        st.sampled_from(_VALID_ROLES),
        st.sampled_from(["wizardry", "DEFINITION", "", "intro"]),
        st.none(),
    )


@st.composite
def _raw_defect(draw, n):
    return {
        "defect_type": draw(_defect_type_strategy()),
        "clip_positions": draw(
            st.lists(_position_strategy(n), min_size=0, max_size=6)
        ),
        "role": draw(_role_strategy()),
    }


@st.composite
def _scenario(draw):
    n = draw(st.integers(min_value=1, max_value=8))
    raw_defects = draw(st.lists(_raw_defect(n), min_size=0, max_size=8))
    score = draw(
        st.one_of(
            st.floats(min_value=-2.0, max_value=3.0, allow_nan=False, allow_infinity=False),
            st.none(),
        )
    )
    return n, raw_defects, score


class TestDefectStructure:
    @settings(max_examples=100)
    @given(scenario=_scenario())
    def test_returned_defects_are_well_formed(self, scenario):
        n, raw_defects, score = scenario

        payload = {"coherence_score": score, "defects": raw_defects}
        client = _JSONClient(json.dumps(payload))

        saved_get = coherence._get_client
        saved_client = coherence._openai_client
        coherence._get_client = lambda: client
        coherence._openai_client = client
        try:
            clips = [_make_clip(i) for i in range(n)]
            result = evaluate(clips)
        finally:
            coherence._get_client = saved_get
            coherence._openai_client = saved_client

        # Score is always a clamped float in [0, 1] regardless of input.
        assert 0.0 <= result.coherence_score <= 1.0

        for defect in result.defects:
            # defect_type drawn from the defined set.
            assert defect.defect_type in _VALID_DEFECT_TYPES

            # clip_positions are 1-based ordinals within 1..n, all ints.
            for pos in defect.clip_positions:
                assert isinstance(pos, int)
                assert 1 <= pos <= n

            # role, when present, is a valid PedagogicalRole.
            if defect.role is not None:
                assert defect.role in _VALID_ROLES
