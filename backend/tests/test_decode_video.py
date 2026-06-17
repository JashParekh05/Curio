"""Unit tests for the ``decode_video`` best-effort I/O shell edge cases.

Covers the two non-admitting outcomes of the Deep_Decoder shell:
  * the Source_Video transcript cannot be fetched -> skipped, nothing admitted
  * the transcript yields zero Idea_Units -> reason='no_idea_units', nothing admitted

The shell imports ``youtube._fetch_transcript`` and
``pipeline.segment_into_atoms`` lazily inside ``decode_video``, so each is
patched in its source module namespace.

Validates: Requirements 1.6, 1.7
"""
import app.services.pipeline as pipeline_mod
import app.services.youtube as youtube_mod
from app.models.schemas import PlannedArc
from app.services.deep_decoder import decode_video


def _planned_arc() -> PlannedArc:
    return PlannedArc(topic_slug="binary-search", concept_type="conceptual")


class TestTranscriptUnavailable:
    def test_none_transcript_skips_and_admits_nothing(self, monkeypatch):
        # Transcript fetch returns nothing -> the Source_Video is skipped.
        monkeypatch.setattr(youtube_mod, "_fetch_transcript", lambda vid: None)

        seg_calls: list = []
        monkeypatch.setattr(
            pipeline_mod,
            "segment_into_atoms",
            lambda *a, **k: seg_calls.append(a) or [],
        )

        result = decode_video("vid-1", "binary-search", _planned_arc())

        assert result.skipped is True
        assert result.reason == "transcript_unavailable"
        # Nothing is admitted from a skipped Source_Video.
        assert result.idea_units == []
        assert result.atoms == []
        # Labeling is never attempted when there is no transcript.
        assert seg_calls == []

    def test_fetch_exception_skips_and_admits_nothing(self, monkeypatch):
        def _boom(vid):
            raise RuntimeError("transcript api down")

        monkeypatch.setattr(youtube_mod, "_fetch_transcript", _boom)
        monkeypatch.setattr(
            pipeline_mod, "segment_into_atoms", lambda *a, **k: ["should-not-be-used"]
        )

        result = decode_video("vid-1", "binary-search", _planned_arc())

        # A raising fetch is degraded to a skip rather than propagating.
        assert result.skipped is True
        assert result.reason == "transcript_unavailable"
        assert result.idea_units == []
        assert result.atoms == []


class TestNoIdeaUnits:
    def test_empty_idea_units_records_reason_and_admits_nothing(self, monkeypatch):
        # A readable transcript whose total duration is zero yields no spans,
        # so build_idea_units returns [] -> reason='no_idea_units'.
        transcript = [{"start": 0.0, "duration": 0.0, "text": "hi"}]
        monkeypatch.setattr(youtube_mod, "_fetch_transcript", lambda vid: transcript)

        seg_calls: list = []
        monkeypatch.setattr(
            pipeline_mod,
            "segment_into_atoms",
            lambda *a, **k: seg_calls.append(a) or ["should-not-be-used"],
        )

        result = decode_video("vid-1", "binary-search", _planned_arc())

        # The transcript was readable, so the video is NOT marked skipped, but
        # it admits nothing and records the no-idea-units outcome.
        assert result.skipped is False
        assert result.reason == "no_idea_units"
        assert result.idea_units == []
        assert result.atoms == []
        # Labeling is never attempted when there are no Idea_Units.
        assert seg_calls == []
