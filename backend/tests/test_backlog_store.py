"""Tests for the Topic_Frontier persistence shell (``app.services.backlog_store``).

These exercise the thin best-effort I/O shell against a mocked Supabase client
(the shared ``FakeDB`` double from ``conftest``), verifying:

  - init_from_grade_map: populates ``topic_backlog`` from
    ``GRADE_LEVEL_TOPIC_MAP`` (deduped, levelled), and is idempotent (skips when
    the table already holds rows / never re-seeds an evolving backlog).
  - load_pending: excludes ``done`` items at the query level.
  - spawn_adjacent_for / spawn_from_engagement: route proposed related slugs
    through the pure core so growth dedupes against the existing backlog,
    respects the per-seed cap (MAX_ADJACENT_PER_SEED) and the MAX_BACKLOG bound.

backlog_store binds ``get_client`` via ``from app.db.supabase import
get_client`` and reaches the proposal source via a function-local
``from app.agents.recommendation_agent import _generate_related_topics``, so
both are patched in the namespace that actually resolves them.

Run from the backend/ dir: ``.venv/bin/python -m pytest tests/test_backlog_store.py``.

Validates: Requirements 3.2, 3.3, 3.4, 3.7, 3.8, 3.9
"""
import pytest

from app.services import backlog_store as bs
from app.services.discover_seeding import GRADE_LEVEL_TOPIC_MAP, _GRADE_DIFFICULTY
from app.services.topic_frontier import (
    MAX_ADJACENT_PER_SEED,
    MAX_BACKLOG,
    BacklogItem,
)
from tests.conftest import FakeDB

_TABLE = "topic_backlog"
_DEFAULT_LEVEL = "intermediate"


def _use_db(monkeypatch, db):
    """Point backlog_store at the supplied fake client.

    backlog_store does ``from app.db.supabase import get_client``, so the bound
    name to patch lives in the backlog_store module namespace.
    """
    monkeypatch.setattr(bs, "get_client", lambda: db)


def _patch_proposals(monkeypatch, proposals):
    """Make the proposal source deterministic.

    ``_propose_candidates`` resolves ``_generate_related_topics`` lazily via
    ``from app.agents.recommendation_agent import _generate_related_topics``,
    so the bound name to patch lives in the recommendation_agent module.
    """
    import app.agents.recommendation_agent as rec_agent

    monkeypatch.setattr(
        rec_agent, "_generate_related_topics", lambda slugs: list(proposals)
    )


def _expected_seed_rows() -> dict[str, str]:
    """Replicate the pure dedupe/levelling init applies: first band wins."""
    expected: dict[str, str] = {}
    for grade_band, category_map in GRADE_LEVEL_TOPIC_MAP.items():
        level = _GRADE_DIFFICULTY.get(grade_band, _DEFAULT_LEVEL)
        for slugs in category_map.values():
            for slug in slugs:
                if slug not in expected:
                    expected[slug] = level
    return expected


def _all_upserted_rows(db: FakeDB) -> list[dict]:
    """Flatten every row across all upsert calls recorded by the fake client."""
    rows: list[dict] = []
    for table, payload in db.rec["upserts"]:
        assert table == _TABLE
        rows.extend(payload)
    return rows


class TestInitFromGradeMap:
    def test_populates_from_grade_map(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        bs.init_from_grade_map()

        rows = _all_upserted_rows(db)
        expected = _expected_seed_rows()

        # Every unique slug is seeded exactly once, deduped across grade bands.
        assert len(rows) == len(expected)
        topics = [r["topic"] for r in rows]
        assert len(topics) == len(set(topics))  # no duplicates
        assert set(topics) == set(expected)

        # Each row carries the first band's level, default priority, pending.
        by_topic = {r["topic"]: r for r in rows}
        for slug, level in expected.items():
            assert by_topic[slug]["level"] == level
            assert by_topic[slug]["priority"] == 0.5
            assert by_topic[slug]["status"] == "pending"

    def test_skips_when_table_already_has_rows(self, monkeypatch):
        # A non-empty table means an evolving backlog -> never overwrite/re-seed.
        db = FakeDB(store={_TABLE: [{"topic": "algebra-2", "status": "pending"}]})
        _use_db(monkeypatch, db)

        bs.init_from_grade_map()

        assert db.rec["upserts"] == []

    def test_idempotent_across_calls(self, monkeypatch):
        # First call seeds; persist those rows so the table is now non-empty.
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        bs.init_from_grade_map()
        seeded = _all_upserted_rows(db)
        assert seeded  # sanity: the first call actually seeded
        db.store[_TABLE] = list(seeded)
        db.rec["upserts"].clear()

        # Second call must observe the existing rows and re-seed nothing.
        bs.init_from_grade_map()
        assert db.rec["upserts"] == []

    def test_read_failure_is_swallowed(self, monkeypatch):
        # Best-effort: a failing read neither raises nor writes.
        db = FakeDB(store={}, fail={_TABLE})
        _use_db(monkeypatch, db)

        bs.init_from_grade_map()  # must not raise

        assert db.rec["upserts"] == []


class TestLoadPending:
    def test_excludes_done_items(self, monkeypatch):
        db = FakeDB(
            store={
                _TABLE: [
                    {"topic": "algebra-2", "level": "intermediate", "priority": 0.7, "status": "pending"},
                    {"topic": "calculus-intro", "level": "intermediate", "priority": 0.6, "status": "done"},
                    {"topic": "statistics", "level": "intermediate", "priority": 0.5, "status": "pending"},
                ]
            }
        )
        _use_db(monkeypatch, db)

        pending = bs.load_pending()

        topics = {item.topic for item in pending}
        assert topics == {"algebra-2", "statistics"}
        assert all(item.status == "pending" for item in pending)
        assert "calculus-intro" not in topics

    def test_empty_table_returns_empty(self, monkeypatch):
        _use_db(monkeypatch, FakeDB(store={_TABLE: []}))
        assert bs.load_pending() == []

    def test_read_failure_returns_empty(self, monkeypatch):
        _use_db(monkeypatch, FakeDB(store={}, fail={_TABLE}))
        assert bs.load_pending() == []


class TestSpawning:
    def test_spawn_dedupes_against_existing_and_seed(self, monkeypatch):
        # "algebra-2" already pending; the seed slug itself and an intra-batch
        # duplicate must both be dropped, leaving only genuinely new slugs.
        db = FakeDB(
            store={
                _TABLE: [
                    {"topic": "algebra-2", "level": "intermediate", "priority": 0.5, "status": "pending"},
                ]
            }
        )
        _use_db(monkeypatch, db)
        _patch_proposals(
            monkeypatch,
            [
                {"slug": "trigonometry", "difficulty": "intermediate"},   # seed -> dropped
                {"slug": "algebra-2", "difficulty": "intermediate"},      # existing -> dropped
                {"slug": "calculus-intro", "difficulty": "intermediate"},
                {"slug": "calculus-intro", "difficulty": "intermediate"}, # intra-batch dup
                {"slug": "statistics", "difficulty": "advanced"},
            ],
        )

        new_items = bs.spawn_adjacent_for("trigonometry")

        new_topics = {item.topic for item in new_items}
        assert new_topics == {"calculus-intro", "statistics"}
        # Only the newly accepted items are persisted.
        upserted = {r["topic"] for r in _all_upserted_rows(db)}
        assert upserted == {"calculus-intro", "statistics"}

    def test_spawn_respects_per_seed_cap(self, monkeypatch):
        _use_db(monkeypatch, FakeDB(store={_TABLE: []}))
        # Propose more distinct new slugs than the per-seed cap allows.
        proposals = [
            {"slug": f"adjacent-{i}", "difficulty": "intermediate"}
            for i in range(MAX_ADJACENT_PER_SEED + 4)
        ]
        _patch_proposals(monkeypatch, proposals)

        new_items = bs.spawn_adjacent_for("seed-topic")

        assert len(new_items) == MAX_ADJACENT_PER_SEED

    def test_spawn_respects_max_backlog_bound(self, monkeypatch):
        # A full backlog (MAX_BACKLOG pending items) rejects every new candidate.
        full = [
            {"topic": f"topic-{i}", "level": "intermediate", "priority": 0.5, "status": "pending"}
            for i in range(MAX_BACKLOG)
        ]
        db = FakeDB(store={_TABLE: full})
        _use_db(monkeypatch, db)
        _patch_proposals(
            monkeypatch,
            [{"slug": f"new-{i}", "difficulty": "intermediate"} for i in range(MAX_ADJACENT_PER_SEED)],
        )

        new_items = bs.spawn_adjacent_for("seed-topic")

        assert new_items == []
        assert db.rec["upserts"] == []  # nothing persisted when the bound is hit

    def test_spawn_from_engagement_uses_same_capped_dedupe_path(self, monkeypatch):
        db = FakeDB(
            store={
                _TABLE: [
                    {"topic": "cells", "level": "beginner", "priority": 0.5, "status": "pending"},
                ]
            }
        )
        _use_db(monkeypatch, db)
        _patch_proposals(
            monkeypatch,
            [
                {"slug": "cells", "difficulty": "beginner"},      # existing -> dropped
                {"slug": "ecosystems", "difficulty": "beginner"},
                {"slug": "forces-motion", "difficulty": "intermediate"},
            ],
        )

        new_items = bs.spawn_from_engagement("cells")

        new_topics = {item.topic for item in new_items}
        assert new_topics == {"ecosystems", "forces-motion"}
        # A proposal with an invalid/missing difficulty would degrade to the
        # default level; here both carry valid levels and are preserved.
        by_topic = {item.topic: item for item in new_items}
        assert by_topic["ecosystems"].level == "beginner"
        assert by_topic["forces-motion"].level == "intermediate"

    def test_spawn_returns_empty_when_no_proposals(self, monkeypatch):
        _use_db(monkeypatch, FakeDB(store={_TABLE: []}))
        _patch_proposals(monkeypatch, [])

        assert bs.spawn_adjacent_for("seed-topic") == []
