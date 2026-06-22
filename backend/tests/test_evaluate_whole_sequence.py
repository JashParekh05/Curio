"""Example test for the whole-sequence evaluation contract.

Covers Task 12.7 / Requirement 4.1: ``evaluate`` presents the WHOLE ordered clip
sequence to the model in a SINGLE call (not clip-by-clip), which is exactly what
lets the judge surface cross-clip defects — like a redundancy or contradiction
that spans two clips — that no single-clip view could ever produce.

``evaluate`` calls the LLM, so the OpenAI client in ``app/services/coherence.py``
is mocked. The fake client records the prompt(s) it receives so the test can
assert the contract:

  (a) the model is called exactly once, with the whole ordered sequence present
      in that single prompt (every clip's title and 1-based position appears);
  (b) when the mock returns a cross-clip redundancy defect spanning two clip
      positions, ``evaluate`` surfaces it with ``clip_positions`` referencing
      both affected clips.
"""
import json

from app.models.schemas import Clip
from app.services import coherence
from app.services.coherence import evaluate


# --- fake OpenAI client that records each call's prompt --------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _RecordingClient:
    """chat.completions.create returns a fixed JSON string and records every
    call's kwargs (so the test can inspect the messages/prompt and call count)."""

    def __init__(self, content):
        self._content = content
        self.calls = []  # list of kwargs dicts passed to create()

        outer = self

        class _Completions:
            def create(_self, *args, **kwargs):
                outer.calls.append(kwargs)
                return _FakeResponse(outer._content)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()

    def prompt_text(self) -> str:
        """Concatenate the content of all messages across all recorded calls."""
        parts = []
        for kwargs in self.calls:
            for message in kwargs.get("messages", []):
                parts.append(str(message.get("content", "")))
        return "\n".join(parts)


def _make_clip(i: int, title: str) -> Clip:
    return Clip(
        id=f"clip-{i}",
        topic_slug="binary-search",
        title=title,
        video_url="https://example.com/v",
        hook_score=0.5,
    )


def _install_client(monkeypatch, client):
    monkeypatch.setattr(coherence, "_get_client", lambda: client)
    monkeypatch.setattr(coherence, "_openai_client", client)


def test_evaluate_sends_whole_ordered_sequence_in_one_call(monkeypatch):
    """The whole ordered sequence is presented to the model in exactly one call."""
    titles = [
        "What binary search is",
        "How halving the range works",
        "Halving the range again (same idea)",  # redundant with clip 2
        "Why the time is logarithmic",
    ]
    clips = [_make_clip(i + 1, t) for i, t in enumerate(titles)]

    # Mock returns no defects here; this test only checks the call contract.
    client = _RecordingClient(json.dumps({"coherence_score": 0.9, "defects": []}))
    _install_client(monkeypatch, client)

    evaluate(clips)

    # (a) exactly one model call — the sequence is evaluated as a whole, not per clip.
    assert len(client.calls) == 1, (
        f"expected a single whole-sequence call, got {len(client.calls)}"
    )

    prompt = client.prompt_text()

    # Every clip's title appears in the single prompt (the whole sequence is present).
    for title in titles:
        assert title in prompt, f"clip title missing from prompt: {title!r}"

    # Every clip's 1-based position appears, establishing playback order in one view.
    for pos in range(1, len(clips) + 1):
        assert f'"position": {pos}' in prompt, f"position {pos} missing from prompt"


def test_evaluate_surfaces_cross_clip_redundancy_spanning_two_clips(monkeypatch):
    """A cross-clip redundancy defect spanning two clips is surfaced with both positions.

    This defect is only detectable when the whole sequence is viewed at once:
    clips 2 and 3 are individually fine, but together they are redundant.
    """
    titles = [
        "What binary search is",
        "How halving the range works",
        "Halving the range again (same idea)",  # redundant with clip 2
        "Why the time is logarithmic",
    ]
    clips = [_make_clip(i + 1, t) for i, t in enumerate(titles)]

    payload = {
        "coherence_score": 0.6,
        "defects": [
            {
                "defect_type": "redundancy",
                "clip_positions": [2, 3],  # spans two distinct clips
                "role": None,
            }
        ],
    }
    client = _RecordingClient(json.dumps(payload))
    _install_client(monkeypatch, client)

    result = evaluate(clips)

    # Exactly one whole-sequence call produced this cross-clip finding.
    assert len(client.calls) == 1

    assert result.coherence_score == 0.6
    assert len(result.defects) == 1

    defect = result.defects[0]
    assert defect.defect_type == "redundancy"
    # The defect references BOTH affected clips — impossible from a single-clip view.
    assert defect.clip_positions == [2, 3]
    assert len(set(defect.clip_positions)) == 2
