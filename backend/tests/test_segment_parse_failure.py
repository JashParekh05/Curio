"""Example tests for the Atomic_Segmenter parse-failure path.

Covers Task 5.2 / Requirements 7.1 and 8.3: when the model returns output that
cannot be parsed into Learning_Atoms, or the model call raises, segmentation
yields zero atoms and an observable warning naming the topic, raising nothing
and leaving any previously recorded feed data untouched.
"""
import logging

from app.models.schemas import ArcRole, PlannedArc
from app.services import pipeline


# --- fixtures / fakes ------------------------------------------------------

def _fake_transcript() -> list[dict]:
    """A small transcript: list of dicts with text/start/duration."""
    return [
        {"text": "Binary search needs a sorted array.", "start": 0.0, "duration": 5.0},
        {"text": "We repeatedly halve the search range.", "start": 5.0, "duration": 5.0},
        {"text": "That gives logarithmic time complexity.", "start": 10.0, "duration": 5.0},
    ]


def _planned_arc() -> PlannedArc:
    return PlannedArc(
        topic_slug="binary-search",
        concept_type="conceptual",
        roles=[
            ArcRole(role="definition", ordinal=1),
            ArcRole(role="mechanism", ordinal=2),
        ],
    )


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _GarbageClient:
    """chat.completions.create returns non-JSON garbage content."""
    def __init__(self, content):
        self._content = content

        class _Completions:
            def create(_self, *a, **k):
                return _FakeResponse(self._content)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _RaisingClient:
    """chat.completions.create raises an exception."""
    def __init__(self, exc):
        self._exc = exc

        class _Completions:
            def create(_self, *a, **k):
                raise self._exc

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --- tests -----------------------------------------------------------------

def test_unparseable_content_yields_zero_atoms_and_warning(monkeypatch, caplog):
    """Non-JSON garbage from the model -> [] + warning naming the topic, no raise."""
    client = _GarbageClient("this is not JSON at all {{{ <<< oops")
    monkeypatch.setattr(pipeline, "_get_client", lambda: client)
    # Defend against accidental client caching/recreation.
    monkeypatch.setattr(pipeline, "_openai_client", client)

    with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
        result = pipeline.segment_into_atoms(
            _fake_transcript(), "binary-search", _planned_arc()
        )

    assert result == []
    assert any(
        "binary-search" in rec.getMessage() and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )


def test_model_call_exception_yields_zero_atoms_and_warning(monkeypatch, caplog):
    """create() raising -> [] + warning naming the topic, no raise."""
    client = _RaisingClient(RuntimeError("upstream model timeout"))
    monkeypatch.setattr(pipeline, "_get_client", lambda: client)
    monkeypatch.setattr(pipeline, "_openai_client", client)

    with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
        result = pipeline.segment_into_atoms(
            _fake_transcript(), "binary-search", _planned_arc()
        )

    assert result == []
    assert any(
        "binary-search" in rec.getMessage() and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )


def test_empty_json_object_is_not_a_list_yields_zero_atoms(monkeypatch, caplog):
    """Parseable JSON that is not a list of atoms -> [] + warning, no raise."""
    client = _GarbageClient('{"unexpected": "object"}')
    monkeypatch.setattr(pipeline, "_get_client", lambda: client)
    monkeypatch.setattr(pipeline, "_openai_client", client)

    with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
        result = pipeline.segment_into_atoms(
            _fake_transcript(), "binary-search", _planned_arc()
        )

    assert result == []
    assert any(
        "binary-search" in rec.getMessage() and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )
