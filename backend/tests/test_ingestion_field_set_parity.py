"""Integration test for field-set parity on the YouTube-only ingestion path (Task 11.3).

This test pins the behavior-preservation guarantee that wiring the
``Content_Provider`` abstraction into the shared ``ingest_topic`` pipeline added
NOTHING that changes the persisted clip shape for a YouTube-only ingestion beyond
the additive, nullable Provider_Provenance columns from
``migration_alt_streams.sql`` (Req 2.5, 4.2).

It verifies, fully offline, two complementary things:

  1. **Field-set parity via ``admission_gate.persist_admitted`` (the clean seam).**
     ``persist_admitted`` is the single ADMIT-stage shell that builds every clip
     row. Calling it with ``provider_provenance=None`` reproduces the EXACT
     pre-feature YouTube-only column set; calling it with a YouTube provenance map
     reproduces that same set PLUS exactly the three additive provenance columns
     (``provider_id``, ``external_id``, ``content_id``). Diffing the two row dicts
     proves no field was added (other than provenance), removed, or retyped, and
     that ``provider_id == 'youtube'``, ``external_id`` is the (non-empty) video
     id, and ``content_id is None`` (Req 2.5, 8.1).

  2. **End-to-end through the wired ``ingest_topic`` path.** Driving a YouTube-only
     ingestion through ``ingest_topic`` -- with the provider seam
     (``youtube.youtube_search``) and the pure pipeline stages
     (decode -> map -> judge) replaced by offline doubles -- the clip row actually
     persisted carries the same existing fields (topic_slug, the
     pedagogical_role/role_ordinal/concept/coherence metadata, the embed url) with
     ``provider_id == 'youtube'``, the non-empty external id, and ``content_id``
     None, confirming the wired path stamps provenance without disturbing the
     legacy field set.

A Clip remains an ``Embed_Reference`` + start/end + a 384-dim embedding: the embed
url keeps today's ``youtube.com/embed/{id}?start=...`` shape, and
``embed_reference.normalize`` (the pure core that enforces Req 4.2) accepts the
candidate only with an exactly-384-dim embedding, round-tripping to the original
``provider_id`` + ``external_id``.

Everything is offline: the Supabase client (``app.db.supabase.get_client``, which
``persist_admitted`` and the pipeline read-backs import lazily), the
``youtube_search`` / transcript seams, and the pure stages are all replaced with
doubles, so no external service is touched.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_ingestion_field_set_parity.py``.

Validates: Requirements 2.5, 4.2
"""
from __future__ import annotations

import re

import pytest

import app.db.supabase as supabase_mod
from app.models.schemas import LearningAtom
from app.services import (
    admission_gate,
    deep_decoder,
    ingestion_store,
    segment_judge,
    segment_mapper,
)
import app.services.ingestion_pipeline as ip
from app.services import youtube
from app.services.content_provider import EmbedReference, SourceItem
from app.services.embed_reference import EMBEDDING_DIM, normalize, render_coords
from app.services.segment_mapper import (
    AssemblyOutcome,
    MappedSegment,
    MappingResult,
)
from app.services.segment_judge import SegmentVerdict
from app.models.schemas import (
    AlignmentResult,
    ArcDiff,
    ArcRole,
    CoherenceResult,
    PlannedArc,
)

from tests.conftest import FakeDB

_TOPIC_SLUG = "binary-search"
_TOPIC_NAME = "Binary Search"
_VIDEO_ID = "vid_abc123"
_COHERENCE = 0.91

# The three additive, nullable provenance columns from migration_alt_streams.sql
# (Req 8.1) -- the ONLY fields a YouTube-only ingestion may add to the legacy
# clip field set (Req 2.5).
_PROVENANCE_COLUMNS = {"provider_id", "external_id", "content_id"}

# The embed URL shape the pre-existing YouTube-only path persists (Req 2.5, 4.2):
# youtube.com/embed/{id}?start=...
_EMBED_URL_RE = re.compile(
    r"^https://www\.youtube\.com/embed/(?P<vid>[^?]+)\?start=\d+"
)


# ---------------------------------------------------------------------------
# Builders for a single admitted Mapped_Segment
# ---------------------------------------------------------------------------


def _atom(video_id: str = _VIDEO_ID) -> LearningAtom:
    """A well-formed Learning_Atom whose clip passes ``validate_clip``."""
    return LearningAtom(
        id=f"atom-{video_id}-1",
        topic_slug=_TOPIC_SLUG,
        video_id=video_id,
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        role="definition",
        concept="What binary search is",
        prior_knowledge=[],
        start=12.0,
        end=48.0,
        transcript="Binary search halves the search interval each step.",
    )


def _segment(video_id: str = _VIDEO_ID) -> MappedSegment:
    """One Mapped_Segment for the conceptual arc's first role."""
    return MappedSegment(
        atom=_atom(video_id),
        pedagogical_role="definition",
        role_ordinal=1,
    )


def _persist_rows(admitted, provenance) -> list[dict]:
    """Run ``persist_admitted`` against a fresh ``FakeDB`` and return the rows.

    Patches the lazily-imported ``get_client`` so persistence is captured offline.
    Returns the list of inserted ``clips`` row dicts (the payloads).
    """
    fake = FakeDB()
    original = supabase_mod.get_client
    supabase_mod.get_client = lambda: fake
    try:
        admission_gate.persist_admitted(
            admitted, _TOPIC_SLUG, _COHERENCE, provenance
        )
    finally:
        supabase_mod.get_client = original
    return [payload for table, payload in fake.rec["inserts"] if table == "clips"]


# ---------------------------------------------------------------------------
# 1. Field-set parity via persist_admitted (legacy vs provenance)
# ---------------------------------------------------------------------------


class TestFieldSetParity:
    """The YouTube-only path adds ONLY the provenance columns (Req 2.5)."""

    def test_provenance_adds_exactly_three_columns_and_nothing_else(self):
        admitted = [_segment()]

        legacy_rows = _persist_rows(admitted, provenance=None)
        prov_rows = _persist_rows(admitted, provenance={_VIDEO_ID: "youtube"})

        assert len(legacy_rows) == 1
        assert len(prov_rows) == 1
        legacy, prov = legacy_rows[0], prov_rows[0]

        legacy_keys = set(legacy)
        prov_keys = set(prov)

        # No legacy field was removed by the wiring change.
        assert legacy_keys <= prov_keys, (
            "wiring removed a field: "
            f"{legacy_keys - prov_keys}"
        )
        # The ONLY additions are the three additive provenance columns (Req 2.5).
        assert prov_keys - legacy_keys == _PROVENANCE_COLUMNS

    def test_shared_fields_are_not_retyped_or_revalued(self):
        admitted = [_segment()]
        legacy = _persist_rows(admitted, provenance=None)[0]
        prov = _persist_rows(admitted, provenance={_VIDEO_ID: "youtube"})[0]

        # Every pre-existing field keeps the same value AND the same runtime type
        # (no field added, removed, or retyped -- Req 2.5).
        for key, legacy_value in legacy.items():
            assert key in prov
            assert prov[key] == legacy_value, f"value changed for {key!r}"
            assert type(prov[key]) is type(legacy_value), (
                f"type changed for {key!r}: "
                f"{type(legacy_value)} -> {type(prov[key])}"
            )

    def test_provenance_values_are_youtube_nonempty_external_id_null_content(self):
        prov = _persist_rows([_segment()], provenance={_VIDEO_ID: "youtube"})[0]

        # provider_id == 'youtube' (Req 2.5, 8.1).
        assert prov["provider_id"] == "youtube"
        # external_id is the (non-empty) video id (Req 8.1).
        assert prov["external_id"] == _VIDEO_ID
        assert prov["external_id"]  # non-empty
        # content_id is unresolved at this layer -> None.
        assert prov["content_id"] is None

    def test_existing_clip_fields_present_and_embed_url_shape(self):
        """Each persisted clip still carries the existing fields, embed url intact."""
        prov = _persist_rows([_segment()], provenance={_VIDEO_ID: "youtube"})[0]

        # The existing coherence/plan + provenance-adjacent metadata survive.
        for field in (
            "topic_slug",
            "pedagogical_role",
            "role_ordinal",
            "concept_label",
            "coherence_score",
            "video_url",
        ):
            assert field in prov, f"missing existing field {field!r}"

        assert prov["topic_slug"] == _TOPIC_SLUG
        assert prov["pedagogical_role"] == "definition"
        assert prov["role_ordinal"] == 1
        assert prov["concept_label"] == "What binary search is"
        assert prov["coherence_score"] == _COHERENCE

        # The clip remains an Embed_Reference: youtube.com/embed/{id}?start=...
        match = _EMBED_URL_RE.match(prov["video_url"])
        assert match is not None, f"embed url shape changed: {prov['video_url']!r}"
        assert match.group("vid") == _VIDEO_ID


# ---------------------------------------------------------------------------
# 2. A Clip remains an Embed_Reference + start/end + 384-dim embedding (Req 4.2)
# ---------------------------------------------------------------------------


class TestClipEmbedAndEmbedding:
    def test_embedding_must_be_exactly_384_dims_and_roundtrips(self):
        item = SourceItem(
            provider_id="youtube",
            embed_ref=EmbedReference(
                provider_id="youtube",
                external_id=_VIDEO_ID,
                playback_params={"start": "12"},
            ),
            title="What binary search is",
            duration_seconds=600.0,
            transcript=None,
            metadata={},
        )

        # A 384-dim embedding normalizes to a valid Embed_Reference (Req 4.2).
        good = [0.0] * EMBEDDING_DIM
        ref, err = normalize(item, start=12.0, end=48.0, embedding=good)
        assert err is None
        assert ref is not None
        assert len(good) == 384

        # The embed reference round-trips to the original identity (Req 4.4).
        coords = render_coords(ref)
        assert coords.provider_id == "youtube"
        assert coords.external_id == _VIDEO_ID

        # A wrong-dimension embedding is rejected, so a Clip cannot be admitted
        # without exactly 384 dims (Req 4.2).
        bad_ref, bad_err = normalize(
            item, start=12.0, end=48.0, embedding=[0.0] * 383
        )
        assert bad_ref is None
        assert bad_err == "bad_embedding_dim"


# ---------------------------------------------------------------------------
# 3. End-to-end: a YouTube-only ingestion through the wired ingest_topic path
# ---------------------------------------------------------------------------


class TestWiredYouTubeOnlyIngestion:
    """Drive ingest_topic with YouTube as the only enabled provider, offline."""

    def test_wired_path_persists_youtube_provenance_with_legacy_field_set(
        self, monkeypatch
    ):
        canned_videos = [
            {
                "video_id": _VIDEO_ID,
                "title": "Binary Search Explained",
                "description": "A clear walkthrough.",
                "thumbnail_url": "https://img/thumb.jpg",
                "duration_seconds": 600,
                "has_caption": True,
                "view_count": 1234,
            }
        ]

        # Provider seam: YouTubeProvider.search delegates to youtube.youtube_search
        # (imported lazily inside the method), so patch it on the youtube module.
        monkeypatch.setattr(youtube, "youtube_search", lambda q: list(canned_videos))

        # Not already ingested -> the run proceeds.
        monkeypatch.setattr(
            ingestion_store, "topic_already_ingested", lambda slug: False
        )
        # Avoid touching ingestion_store I/O seams.
        monkeypatch.setattr(
            ingestion_store, "record_funnel", lambda funnel, slug: None
        )
        monkeypatch.setattr(
            ingestion_store, "set_feed_eligibility", lambda slug, ids: None
        )

        # A real Planned_Arc with the single role our atom maps to.
        planned_arc = PlannedArc(
            topic_slug=_TOPIC_SLUG,
            concept_type="conceptual",
            roles=[ArcRole(role="definition", ordinal=1)],
        )
        import app.agents.section_planner as section_planner

        monkeypatch.setattr(
            section_planner, "plan_and_store_arc", lambda slug, name: planned_arc
        )

        # DECODE: transcript -> atoms (offline double).
        def fake_decode(video_id, topic_slug, arc, *, now_utc=None):
            return deep_decoder.DecodeResult(
                video_id=video_id,
                idea_units=[object()],  # non-empty so the run does not skip
                atoms=[_atom(video_id)],
                skipped=False,
                reason=None,
            )

        monkeypatch.setattr(deep_decoder, "decode_video", fake_decode)

        # MAP: one Mapped_Segment, coherent and aligned, no defects.
        def fake_map(atoms, arc):
            segment = MappedSegment(
                atom=atoms[0], pedagogical_role="definition", role_ordinal=1
            )
            return AssemblyOutcome(
                mapping=MappingResult(
                    mapped=[segment], unmapped=[], unfilled_roles=[]
                ),
                clips=[],
                coherence=CoherenceResult(coherence_score=_COHERENCE, defects=[]),
                alignment=AlignmentResult(
                    aligned=True,
                    diff=ArcDiff(aligned=True),
                    unresolved=False,
                ),
                arc_missing=False,
            )

        monkeypatch.setattr(segment_mapper, "map_and_assemble", fake_map)

        # JUDGE: a passing verdict for every segment.
        monkeypatch.setattr(
            segment_judge,
            "judge_segment",
            lambda segment, threshold: SegmentVerdict(
                quality_score=0.95, fit="belongs", status="passing", reason="ok"
            ),
        )

        # ADMIT persistence: capture inserts offline.
        fake = FakeDB()
        monkeypatch.setattr(supabase_mod, "get_client", lambda: fake)

        summary = ip.ingest_topic(_TOPIC_SLUG, _TOPIC_NAME)

        # The wired run admitted and stored exactly one clip.
        assert summary.stored == 1
        assert summary.deferred_reason is None

        clip_rows = [p for t, p in fake.rec["inserts"] if t == "clips"]
        assert len(clip_rows) == 1
        row = clip_rows[0]

        # Provider_Provenance stamped by the wired path (Req 2.5, 8.1).
        assert row["provider_id"] == "youtube"
        assert row["external_id"] == _VIDEO_ID
        assert row["external_id"]  # non-empty
        assert row["content_id"] is None

        # The legacy field set is intact, embed url shape preserved (Req 2.5, 4.2).
        assert row["topic_slug"] == _TOPIC_SLUG
        assert row["pedagogical_role"] == "definition"
        assert row["role_ordinal"] == 1
        assert row["concept_label"] == "What binary search is"
        assert row["coherence_score"] == _COHERENCE
        match = _EMBED_URL_RE.match(row["video_url"])
        assert match is not None
        assert match.group("vid") == _VIDEO_ID

        # The wired path adds ONLY the provenance columns vs the legacy field set.
        legacy_keys = set(_persist_rows([_segment()], provenance=None)[0])
        assert set(row) - legacy_keys == _PROVENANCE_COLUMNS
