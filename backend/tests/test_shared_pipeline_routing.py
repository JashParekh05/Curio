"""Integration test for shared-pipeline routing (Task 11.2).

# Feature: alternative-content-streams

This is the wiring-level integration test that pins the SHARED-PIPELINE routing
guarantees of the Content_Provider abstraction: every ``SourceItem`` acquired
through the abstraction is admitted ONLY by flowing through the unchanged shared
``ingestion_pipeline.ingest_topic`` stages, in order, and BOTH whole-topic
entrypoints (the on-demand ``run_pipeline`` and the cold-start ``Seeding_Worker``)
route through that same single pipeline with no bypass.

What it asserts (Req 5.1, 5.2, 5.3, 5.4):

  1. A ``SourceItem`` acquired via the ``Content_Provider`` abstraction flows
     through ``ingest_topic``'s DECODE -> (break-down) -> MAP -> JUDGE -> ADMIT
     stages IN ORDER, with the provider's Transcript as the DECODE input
     (Req 5.1, 5.3). The provider is exercised through the real abstraction:
     ``_acquire_source_items`` builds the default single-provider Registry +
     ``YouTubeProvider`` and runs ``source_acquirer.acquire_sources``
     (Source_Selection -> capability gate -> identity check -> dedup); only
     ``YouTubeProvider.search`` is stubbed to yield a known transcript-bearing
     ``SourceItem``. The five pipeline stages are spied so their call order and
     inputs are observable offline.

  2. No admission path bypasses the shared pipeline: ``persist_admitted`` (the
     single clip-writing seam) is reached ONLY after DECODE, MAP, JUDGE, and
     ADMIT have each run, and never before (Req 5.2). The persisted clips carry
     the provider's Provider_Provenance.

  3. ``run_pipeline`` with ``section_index is None`` (a whole-topic call)
     delegates to ``ingest_topic`` and returns its Admitted_Clip count, and the
     cold-start ``Seeding_Worker._generate`` routes its Backlog_Item through the
     same ``ingest_topic`` seam (Req 5.4) -- both select via the abstraction and
     run the one shared pipeline.

The provider Transcript <-> DECODE seam: ``ingest_topic`` feeds DECODE the
Source_Item's external id, and ``deep_decoder.decode_video`` resolves the
Transcript through ``youtube._fetch_transcript`` -- which is EXACTLY the seam the
``YouTubeProvider.fetch_transcript`` capability delegates to. The test patches
that single transcript seam to return the provider's Transcript and spies DECODE
to record the Transcript it resolved, proving the provider Transcript is the
DECODE input regardless of the originating Provider_Id (Req 5.3).

Everything is offline: no DB, no YouTube, no model, no Key_Pool I/O is touched.

Validates: Requirements 5.1, 5.2, 5.3, 5.4
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.ingestion_pipeline as ip
import app.services.youtube as youtube_mod
import app.agents.section_planner as section_planner
from app.agents import pipeline_agent
import scripts.seeding_worker as sw
from app.providers.youtube_provider import YouTubeProvider
from app.services.content_provider import EmbedReference, SourceItem
from app.services.ingestion_pipeline import IngestionSummary
from app.services.topic_frontier import BacklogItem


# The known provider Transcript that must become the DECODE input (Req 5.3).
_PROVIDER_TRANSCRIPT = [
    {"text": "intro to binary search", "start": 0.0, "duration": 5.0},
    {"text": "the midpoint comparison", "start": 5.0, "duration": 5.0},
]
_VIDEO_ID = "vid-known-1"


def _known_source_item() -> SourceItem:
    """A transcript-bearing ``SourceItem`` tagged with Provider_Id ``youtube``.

    Its ``embed_ref`` provider id matches the provider id and its external id is
    non-empty, so it passes ``identity_ok`` and flows through the abstraction.
    """
    return SourceItem(
        provider_id="youtube",
        embed_ref=EmbedReference(
            provider_id="youtube",
            external_id=_VIDEO_ID,
            playback_params={"start": "0"},
        ),
        title="Binary Search Explained",
        duration_seconds=120.0,
        transcript=_PROVIDER_TRANSCRIPT,
        metadata={
            "title": "Binary Search Explained",
            "description": "A clear walkthrough",
            "video_url": (
                f"https://www.youtube.com/embed/{_VIDEO_ID}"
                "?start=0&autoplay=1&rel=0&modestbranding=1"
            ),
            "source_platform": "youtube",
            "duration_seconds": 120.0,
        },
    )


class _PipelineSpies:
    """Spies for the five ``ingest_topic`` stages, recording call order + inputs."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.decode_video_id: str | None = None
        self.decode_transcript: object = None
        self.map_atoms: object = None
        self.judged_segments: list[object] = []
        self.admit_args: tuple | None = None
        self.persist_provenance: dict | None = None
        self.persist_admitted_count: int | None = None

    # ---- DECODE (transcript -> idea units -> atoms; break-down lives here) ----
    def decode_video(self, video_id, topic_slug, planned_arc, now_utc=None):
        self.order.append("decode")
        self.decode_video_id = video_id
        # The provider Transcript reaches DECODE through the SAME transcript seam
        # the YouTubeProvider.fetch_transcript capability delegates to.
        self.decode_transcript = youtube_mod._fetch_transcript(video_id)
        atom = SimpleNamespace(id="atom-1")
        return SimpleNamespace(
            video_id=video_id,
            idea_units=[SimpleNamespace(id="iu-1")],
            atoms=[atom],
            skipped=False,
            reason=None,
        )

    # ---- MAP (atoms -> mapped/unmapped + coherence/alignment) ----
    def map_and_assemble(self, atoms, planned_arc):
        self.order.append("map")
        self.map_atoms = atoms
        segment = SimpleNamespace(atom=SimpleNamespace(id="atom-1"))
        return SimpleNamespace(
            mapping=SimpleNamespace(mapped=[segment], unmapped=[]),
            clips=[],
            coherence=SimpleNamespace(defects=[], coherence_score=0.9),
            alignment=SimpleNamespace(aligned=True, unresolved=[]),
            arc_missing=False,
        )

    # ---- JUDGE (per Mapped_Segment verdict) ----
    def judge_segment(self, segment, threshold):
        self.order.append("judge")
        self.judged_segments.append(segment)
        return SimpleNamespace(
            quality_score=0.9, fit=True, status="passing", reason="ok"
        )

    # ---- ADMIT (gate mapped + passing + coherent + aligned) ----
    def admit_topic(self, mapped, unmapped, verdicts, coherent, aligned):
        self.order.append("admit")
        self.admit_args = (mapped, unmapped, verdicts, coherent, aligned)
        admitted = list(mapped)  # all passing/coherent/aligned in this scenario
        return admitted, [], "fully_admitted"

    # ---- PERSIST (the single clip-writing seam) ----
    def persist_admitted(self, admitted, topic_slug, coherence_score, provenance):
        self.order.append("persist")
        self.persist_provenance = provenance
        self.persist_admitted_count = len(admitted)
        return len(admitted)


@pytest.fixture
def wired_pipeline(monkeypatch):
    """Wire ``ingest_topic`` to spied stages + a stubbed provider, fully offline.

    - ``YouTubeProvider.search`` yields the one known transcript-bearing
      ``SourceItem`` (the rest of the abstraction -- Source_Selection, the
      capability gate, identity check, dedup -- runs for real).
    - the transcript seam returns the provider Transcript for the known id.
    - the five pipeline stages are spied.
    - all DB / planning side effects are neutralized.
    """
    spies = _PipelineSpies()

    # Provider is exercised through the real abstraction; only its network search
    # is stubbed to return the known item (Req 5.1).
    monkeypatch.setattr(
        YouTubeProvider,
        "search",
        lambda self, query, *, max_results: [_known_source_item()],
    )

    # The single transcript seam DECODE resolves; returns the provider Transcript
    # for the known id, nothing otherwise (Req 5.3).
    monkeypatch.setattr(
        youtube_mod,
        "_fetch_transcript",
        lambda video_id, *a, **k: _PROVIDER_TRANSCRIPT if video_id == _VIDEO_ID else None,
    )

    # Spy the five shared stages in the ingestion_pipeline namespace.
    monkeypatch.setattr(ip.deep_decoder, "decode_video", spies.decode_video)
    monkeypatch.setattr(ip.segment_mapper, "map_and_assemble", spies.map_and_assemble)
    monkeypatch.setattr(ip.segment_judge, "judge_segment", spies.judge_segment)
    monkeypatch.setattr(ip.admission_gate, "admit_topic", spies.admit_topic)
    monkeypatch.setattr(ip.admission_gate, "persist_admitted", spies.persist_admitted)

    # Neutralize the surrounding best-effort side effects (planning, funnel/feed
    # bookkeeping, clip-id reads) so the test stays offline and DB-free.
    monkeypatch.setattr(
        ip.ingestion_store, "topic_already_ingested", lambda topic_slug: False
    )
    monkeypatch.setattr(section_planner, "plan_and_store_arc", lambda *a, **k: object())
    monkeypatch.setattr(ip.ingestion_store, "record_funnel", lambda *a, **k: None)
    monkeypatch.setattr(ip.ingestion_store, "set_feed_eligibility", lambda *a, **k: None)
    monkeypatch.setattr(ip, "_collect_topic_clip_ids", lambda *a, **k: set())

    return spies


# ---------------------------------------------------------------------------
# 1 + 2. SourceItems flow through the shared stages in order; nothing bypasses
# ---------------------------------------------------------------------------


class TestSharedPipelineRouting:
    def test_source_item_flows_through_stages_in_order(self, wired_pipeline):
        """A provider ``SourceItem`` runs DECODE -> MAP -> JUDGE -> ADMIT -> persist
        in that exact order, with the provider Transcript as the DECODE input."""
        spies = wired_pipeline

        summary = ip.ingest_topic("binary-search", "Binary Search")

        # The five shared stages ran in the canonical order (Req 5.1). DECODE
        # encompasses the break-down (idea units -> atoms) per the pipeline design.
        assert spies.order == ["decode", "map", "judge", "admit", "persist"]

        # DECODE was fed the provider Source_Item (by its external id) and resolved
        # the provider Transcript as its input regardless of Provider_Id (Req 5.3).
        assert spies.decode_video_id == _VIDEO_ID
        assert spies.decode_transcript == _PROVIDER_TRANSCRIPT

        # The break-down output (atoms) flowed from DECODE into MAP, and the mapped
        # segment flowed into JUDGE -- the stages are chained, not independent.
        assert spies.map_atoms is not None and len(spies.map_atoms) == 1
        assert len(spies.judged_segments) == 1

        # A clip was admitted and persisted through the shared pipeline only.
        assert summary.outcome == "fully_admitted"
        assert summary.stored == 1

    def test_no_admission_path_bypasses_the_pipeline(self, wired_pipeline):
        """``persist_admitted`` (the clip-writing seam) is reached ONLY after all
        upstream stages ran, so no clip is admitted by a bypassing path (Req 5.2)."""
        spies = wired_pipeline

        ip.ingest_topic("binary-search", "Binary Search")

        # persist is last, and every upstream stage precedes it.
        assert spies.order[-1] == "persist"
        persist_at = spies.order.index("persist")
        for stage in ("decode", "map", "judge", "admit"):
            assert stage in spies.order
            assert spies.order.index(stage) < persist_at

        # The persisted clips carry the provider's Provider_Provenance (Req 5.2/8.1):
        # the external id maps to its originating Provider_Id 'youtube'.
        assert spies.persist_provenance == {_VIDEO_ID: "youtube"}
        assert spies.persist_admitted_count == 1

    def test_admit_received_passing_verdict_for_mapped_segment(self, wired_pipeline):
        """ADMIT was gated on the JUDGE verdict + coherence/alignment flags from the
        upstream stages -- confirming the stages feed ADMIT, not a side path."""
        spies = wired_pipeline

        ip.ingest_topic("binary-search", "Binary Search")

        mapped, unmapped, verdicts, coherent, aligned = spies.admit_args
        assert len(mapped) == 1 and unmapped == []
        assert "atom-1" in verdicts and verdicts["atom-1"].status == "passing"
        assert coherent is True and aligned is True


# ---------------------------------------------------------------------------
# 3 + 4. Both whole-topic entrypoints route through the one shared pipeline
# ---------------------------------------------------------------------------


def _summary(slug: str, stored: int) -> IngestionSummary:
    return IngestionSummary(
        topic_slug=slug,
        outcome="fully_admitted",
        funnels=[],
        stored=stored,
        deferred_reason=None,
    )


class _FakeTable:
    def select(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _FakeClient:
    def table(self, *a, **k):
        return _FakeTable()


class TestEntrypointRoutingThroughSharedPipeline:
    def test_run_pipeline_whole_topic_delegates_to_ingest_topic(self, monkeypatch):
        """``run_pipeline`` with ``section_index is None`` routes the whole-topic
        call through ``ingest_topic`` and returns its Admitted_Clip count
        (Req 5.4) -- no provider call bypasses the shared pipeline."""
        calls: list[tuple[str, str]] = []

        def fake_ingest(topic_slug, topic_name, **kwargs):
            calls.append((topic_slug, topic_name))
            return _summary(topic_slug, stored=5)

        monkeypatch.setattr(ip, "ingest_topic", fake_ingest)

        stored = pipeline_agent.run_pipeline("binary-search", "Binary Search")

        assert calls == [("binary-search", "Binary Search")]
        assert stored == 5

    def test_seeding_worker_routes_through_ingest_topic(self, monkeypatch):
        """The cold-start ``Seeding_Worker`` selects via the abstraction and routes
        its Backlog_Item through the SAME ``ingest_topic`` seam (Req 5.4)."""
        calls: list[tuple[str, str]] = []

        def fake_ingest(slug, name, **kwargs):
            calls.append((slug, name))
            return _summary(slug, stored=3)

        monkeypatch.setattr(ip, "ingest_topic", fake_ingest)
        # Not already seeded -> generation proceeds to the shared pipeline.
        monkeypatch.setattr(sw, "_topic_has_clips", lambda topic: False)
        monkeypatch.setattr(sw, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(sw, "_count_clips", lambda topic: 3)
        monkeypatch.setattr(sw, "_stamp_content_level", lambda topic, level: None)

        stored = sw._generate(
            BacklogItem(
                topic="binary-search",
                level="beginner",
                priority=0.5,
                status="pending",
            )
        )

        # The worker drove the topic through the shared ingest_topic entrypoint.
        assert calls == [("binary-search", "Binary Search")]
        assert stored == 3
