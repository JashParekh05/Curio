"""Integration test for structure-aware segmentation and the soft feed (Task 7.7).

Wires the now-shipped Phase 1 shells together against the in-memory Supabase
double (``FakeDB`` from ``conftest``) with no external service touched -- no real
YouTube, LLM, or DB. It asserts the structure-aware-segmentation + soft-feed
contract at the shell boundaries the design exposes (the full HTTP
``feed.get_path_feed`` round-trip is auth- and DB-heavy, so per the design's
Testing Strategy the contract is exercised at ``persist_admitted`` level tagging,
``section_planner`` query conditioning, and ``checkpoint_placement`` cards):

  1. Structure-aware segmentation: a clip admitted through the leveled ingestion
     flow carries its curriculum-slot tags -- the resolved ``clips.level``, the
     beat anchor (``role_ordinal``, from which ``section_index = role_ordinal - 1``
     is derived at read time), and ``pedagogical_role``. A clip that fails the
     single-idea-to-single-beat invariant is left untagged (no ``level``), never
     dropped (Req 1.3).
  2. Level-conditioned retrieval: ``section_planner.plan_and_store_sections(...,
     level="beginner")`` conditions each beat's ``search_query`` via
     ``level_query.condition_beat_query`` (the qualifier is applied), while an
     unrecognized or ``None`` level leaves queries unchanged (Req 1.4).
  3. Soft feed invariant: the checkpoint cards woven into a topic's clip sequence
     are always skippable and never remove or reorder clips -- the served clip
     list is identical with or without checkpoint placement (Req 4.2).

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_structured_feed_integration.py``.

Validates: Requirements 1.3, 1.4, 4.2
"""
from __future__ import annotations

from dataclasses import dataclass

import app.db.supabase as supabase_mod
from app.agents import section_planner
from app.models.schemas import CheckpointCard, Clip, FeedResponse
from app.services import admission_gate
from app.services.checkpoint_placement import place_checkpoints
from app.services.level_query import LEVEL_QUALIFIERS
from tests.conftest import FakeDB


# ---------------------------------------------------------------------------
# Lightweight stubs standing in for a MappedSegment / LearningAtom, mirroring
# the doubles used by test_persist_admitted.
# ---------------------------------------------------------------------------

@dataclass
class _StubAtom:
    video_id: str = "vid123"
    start: float = 10.0
    end: float = 40.0
    concept: str = "Binary search invariant"
    transcript: str | None = "Some clear explanatory transcript text."
    source_url: str | None = "https://www.youtube.com/watch?v=vid123"


@dataclass
class _StubSegment:
    atom: _StubAtom
    pedagogical_role: str = "definition"  # a defined PedagogicalRole value
    role_ordinal: int = 2                 # beat = role_ordinal - 1 = 1 (in 0..3)


def _use_db(monkeypatch, db):
    """Point both lazy ``get_client`` imports (persist_admitted and
    section_planner) at the in-memory fake client."""
    monkeypatch.setattr(supabase_mod, "get_client", lambda: db)


# ---------------------------------------------------------------------------
# 1. Structure-aware segmentation: admitted clips carry their slot tags
# ---------------------------------------------------------------------------

class TestLeveledClipSlotTagging:
    def test_admitted_clip_carries_level_beat_and_role(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        # role_ordinal=2 -> realized beat (section_index) 1, a valid single beat.
        segment = _StubSegment(atom=_StubAtom(), role_ordinal=2)
        stored = admission_gate.persist_admitted(
            [segment], "binary-search", 0.87, level="beginner"
        )

        assert stored == 1
        rows = [p for (t, p) in db.rec["inserts"] if t == "clips"]
        assert len(rows) == 1
        row = rows[0]

        # The slot tags travel with the clip: the resolved curriculum level on
        # clips.level, plus the beat anchor (role_ordinal, from which the beat
        # section_index = role_ordinal - 1 is derived at read time) and the
        # pedagogical_role.
        assert row["level"] == "beginner"
        assert row["role_ordinal"] == 2
        assert (row["role_ordinal"] - 1) == 1  # the realized beat (section_index)
        assert row["pedagogical_role"] == "definition"
        assert row["topic_slug"] == "binary-search"

    def test_none_level_leaves_clip_untagged_but_stored(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        segment = _StubSegment(atom=_StubAtom(), role_ordinal=2)
        # No level supplied: the clip is stored with its role metadata but no
        # clips.level tag (NULL defers to the topic's difficulty at read time).
        stored = admission_gate.persist_admitted(
            [segment], "binary-search", 0.5, level=None
        )

        assert stored == 1
        row = [p for (t, p) in db.rec["inserts"] if t == "clips"][0]
        assert "level" not in row
        assert row["role_ordinal"] == 2
        assert row["pedagogical_role"] == "definition"

    def test_clip_failing_single_beat_is_left_untagged_not_dropped(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        # role_ordinal=5 -> beat section_index 4, OUTSIDE the 0..3 arc, so
        # build_slot returns None and the clip fails the single-beat invariant.
        segment = _StubSegment(atom=_StubAtom(), role_ordinal=5)
        stored = admission_gate.persist_admitted(
            [segment], "binary-search", 0.87, level="beginner"
        )

        # Not dropped: the clip is still persisted...
        assert stored == 1
        row = [p for (t, p) in db.rec["inserts"] if t == "clips"][0]
        # ...but left untagged (no clips.level) -- served role-less, never gated.
        assert "level" not in row
        assert row["role_ordinal"] == 5

    def test_unrecognized_level_leaves_clip_untagged(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        segment = _StubSegment(atom=_StubAtom(), role_ordinal=2)
        # An unrecognized level resolves to no Content_Level, so clips.level is
        # left NULL (behavior-preserving for the read path).
        stored = admission_gate.persist_admitted(
            [segment], "binary-search", 0.5, level="wizard"
        )

        assert stored == 1
        row = [p for (t, p) in db.rec["inserts"] if t == "clips"][0]
        assert "level" not in row


# ---------------------------------------------------------------------------
# 2. Level-conditioned retrieval: per-beat search query conditioned on level
# ---------------------------------------------------------------------------

class TestLevelConditionedRetrieval:
    def _seed_sections(self) -> dict:
        """A topic whose 4 beats already have base (level-agnostic) queries
        persisted in topic_sections -- the cached path that skips the LLM."""
        return {
            "topic_sections": [
                {
                    "topic_slug": "dynamic-programming",
                    "section_index": i,
                    "title": f"Beat {i}",
                    "description": f"Desc {i}",
                    "search_query": f"dynamic programming beat {i}",
                }
                for i in range(4)
            ]
        }

    def test_recognized_level_conditions_every_beat_query(self, monkeypatch):
        db = FakeDB(store=self._seed_sections())
        _use_db(monkeypatch, db)

        sections = section_planner.plan_and_store_sections(
            "dynamic-programming", "Dynamic Programming", level="beginner"
        )

        qualifier = LEVEL_QUALIFIERS["beginner"]
        assert len(sections) == 4
        for i, s in enumerate(sections):
            base = f"dynamic programming beat {i}"
            # The qualifier is appended exactly once, base preserved as a prefix.
            assert s["search_query"] == f"{base} {qualifier}"
            assert s["search_query"].startswith(base)
            assert s["search_query"].count(qualifier) == 1

    def test_advanced_and_beginner_condition_differently(self, monkeypatch):
        db = FakeDB(store=self._seed_sections())
        _use_db(monkeypatch, db)

        beginner = section_planner.plan_and_store_sections(
            "dynamic-programming", "Dynamic Programming", level="beginner"
        )
        # A fresh fake DB so the cached read path is exercised again cleanly.
        db2 = FakeDB(store=self._seed_sections())
        _use_db(monkeypatch, db2)
        advanced = section_planner.plan_and_store_sections(
            "dynamic-programming", "Dynamic Programming", level="advanced"
        )

        assert beginner[0]["search_query"].endswith(LEVEL_QUALIFIERS["beginner"])
        assert advanced[0]["search_query"].endswith(LEVEL_QUALIFIERS["advanced"])
        assert beginner[0]["search_query"] != advanced[0]["search_query"]

    def test_none_level_leaves_queries_unchanged(self, monkeypatch):
        db = FakeDB(store=self._seed_sections())
        _use_db(monkeypatch, db)

        sections = section_planner.plan_and_store_sections(
            "dynamic-programming", "Dynamic Programming", level=None
        )

        for i, s in enumerate(sections):
            assert s["search_query"] == f"dynamic programming beat {i}"

    def test_unrecognized_level_leaves_queries_unchanged(self, monkeypatch):
        db = FakeDB(store=self._seed_sections())
        _use_db(monkeypatch, db)

        sections = section_planner.plan_and_store_sections(
            "dynamic-programming", "Dynamic Programming", level="wizard"
        )

        for i, s in enumerate(sections):
            assert s["search_query"] == f"dynamic programming beat {i}"


# ---------------------------------------------------------------------------
# 3. Soft feed invariant: checkpoints are skippable and never alter the clips
# ---------------------------------------------------------------------------

def _clip(idx: int, section_index: int) -> Clip:
    return Clip(
        id=f"dp-clip-{idx}",
        topic_slug="dynamic-programming",
        title=f"DP clip {idx}",
        video_url="https://example.com/v",
        section_index=section_index,
    )


def _assemble_feed(clips: list[Clip]) -> FeedResponse:
    """Mirror the feed shell: place checkpoints over the served clip list and
    attach them to the FeedResponse WITHOUT touching the clip list itself
    (the same snippet used by ``feed.get_path_feed``)."""
    cards = place_checkpoints(
        [c.section_index if c.section_index is not None else -1 for c in clips],
        "dynamic-programming",
    )
    checkpoints = [
        CheckpointCard(
            stage=card.stage,
            after_clip_index=card.after_clip_index,
            topic_slug=card.topic_slug,
            section_index=card.section_index,
            skippable=card.skippable,
        )
        for card in cards
    ]
    return FeedResponse(
        topic_slug="dynamic-programming",
        clips=clips,
        checkpoints=checkpoints,
    )


class TestSoftFeedInvariant:
    def test_checkpoints_never_remove_or_reorder_clips(self):
        # A full 4-beat topic in feed order (hook/what/how/outcomes).
        clips = [
            _clip(0, 0),
            _clip(1, 1),
            _clip(2, 2),
            _clip(3, 2),
            _clip(4, 3),
        ]
        original_ids = [c.id for c in clips]

        feed = _assemble_feed(clips)

        # The clip list is unchanged by checkpoint placement: same clips, same
        # order, none added or removed.
        assert [c.id for c in feed.clips] == original_ids
        assert feed.clips == clips

    def test_every_checkpoint_card_is_skippable(self):
        clips = [_clip(i, sidx) for i, sidx in enumerate([0, 1, 2, 2, 3])]

        feed = _assemble_feed(clips)

        assert feed.checkpoints, "expected at least one checkpoint for a full topic"
        for card in feed.checkpoints:
            assert card.skippable is True

    def test_checkpoint_indices_point_within_the_clip_list(self):
        clips = [_clip(i, sidx) for i, sidx in enumerate([0, 1, 2, 2, 3])]

        feed = _assemble_feed(clips)

        for card in feed.checkpoints:
            assert 0 <= card.after_clip_index < len(feed.clips)

    def test_empty_clip_list_yields_no_checkpoints_and_no_clip_change(self):
        feed = _assemble_feed([])

        assert feed.clips == []
        assert feed.checkpoints == []
