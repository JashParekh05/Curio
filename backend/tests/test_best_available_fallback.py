"""Example tests for the best-available fallback contract (Task 14.4).

Requirements 8.1 and 8.3: every LLM shell is best-effort. When its model client
raises, the shell must NOT propagate the exception — it returns its documented
best-available / deterministic-default result instead, so content generation is
never blocked.

The fallback behaviour is realised independently by each shell:
  - coherence.evaluate(clips)              -> CoherenceResult(0.5, [], 0)
  - section_planner.classify_concept_type  -> ("default", True)
  - pipeline.segment_into_atoms            -> []

Each test monkeypatches the module's client accessor so the underlying model
call RAISES, then asserts the documented fallback is returned and nothing is
raised.
"""
from app.agents import section_planner
from app.models.schemas import ArcRole, Clip, CoherenceResult, PlannedArc
from app.services import coherence, pipeline


# --- raising client fake ---------------------------------------------------

class _RaisingClient:
    """A stand-in OpenAI client whose chat.completions.create always raises."""

    def __init__(self, exc):
        self._exc = exc

        class _Completions:
            def create(_self, *a, **k):
                raise self._exc

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --- fixtures --------------------------------------------------------------

def _clips() -> list[Clip]:
    """A non-empty ordered clip sequence (empty sequences short-circuit before
    the model call, so we need real clips to exercise the failure path)."""
    return [
        Clip(id="c1", topic_slug="binary-search", title="What is binary search",
             video_url="https://example.com/1", pedagogical_role="definition"),
        Clip(id="c2", topic_slug="binary-search", title="How halving works",
             video_url="https://example.com/2", pedagogical_role="mechanism"),
    ]


def _transcript() -> list[dict]:
    return [
        {"text": "Binary search needs a sorted array.", "start": 0.0, "duration": 5.0},
        {"text": "We repeatedly halve the search range.", "start": 5.0, "duration": 5.0},
        {"text": "That gives logarithmic time complexity.", "start": 10.0, "duration": 5.0},
    ]


def _planned_arc() -> PlannedArc:
    return PlannedArc(
        topic_slug="binary-search",
        concept_type="conceptual",
        roles=[ArcRole(role="definition", ordinal=1),
               ArcRole(role="mechanism", ordinal=2)],
    )


# --- tests -----------------------------------------------------------------

def test_coherence_evaluate_returns_default_result_when_client_raises(monkeypatch):
    """coherence.evaluate -> CoherenceResult(0.5, []) when the client raises, no raise."""
    client = _RaisingClient(RuntimeError("upstream model timeout"))
    monkeypatch.setattr(coherence, "_get_client", lambda: client)
    monkeypatch.setattr(coherence, "_openai_client", client)

    result = coherence.evaluate(_clips())

    assert isinstance(result, CoherenceResult)
    assert result.coherence_score == 0.5
    assert result.defects == []
    assert result.round_index == 0


def test_section_planner_classify_returns_default_when_client_raises(monkeypatch):
    """classify_concept_type -> ('default', True) when the client raises, no raise."""
    client = _RaisingClient(RuntimeError("upstream model error"))
    monkeypatch.setattr(section_planner, "_client", lambda: client)

    concept_type, default_applied = section_planner.classify_concept_type(
        "Binary Search", "intermediate"
    )

    assert concept_type == "default"
    assert default_applied is True


def test_pipeline_segment_returns_empty_when_client_raises(monkeypatch):
    """segment_into_atoms -> [] when the client raises, no raise."""
    client = _RaisingClient(RuntimeError("upstream model unavailable"))
    monkeypatch.setattr(pipeline, "_get_client", lambda: client)
    monkeypatch.setattr(pipeline, "_openai_client", client)

    result = pipeline.segment_into_atoms(_transcript(), "binary-search", _planned_arc())

    assert result == []
