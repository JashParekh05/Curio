"""Integration test for placement and anchored checkpoint generation (Task 12.6).

Wires the now-shipped Phase 2 shells + cores together against the in-memory
Supabase double (``FakeDB`` from ``conftest``) with NO external service touched --
no real YouTube, LLM, or DB. Per the design's Testing Strategy, the contract is
exercised at the service seams the design exposes (the full HTTP placement
round-trip is auth- and spine-router-heavy, so the placement contract is asserted
at the ``score_to_level -> placement.place`` seam, mirroring the approach used by
the Phase 1 integration test in ``test_structured_feed_integration.py``):

  1. Placement maps through to ``placement.place``: a graded diagnostic count is
     mapped by ``placement_score.score_to_level`` into a starting Content_Level,
     which feeds the existing ``placement.place`` core to pick an ``entry_index``.
     The documented thresholds (>= 0.75 advanced, >= 0.4 intermediate, else
     beginner) and the monotonic-in-correct behavior hold end-to-end through the
     seam, and a skipped diagnostic (total 0) defers to ``grade_level`` (Req 2.1).

  2. Anchored checkpoint generation:
     ``quiz.generate_and_store_questions(stage="check", section_index=N)`` selects
     ONLY that beat's clip transcripts (the clips query is filtered by
     ``section_index == N``), and the ``stage`` + ``section_index`` anchor is
     persisted on the inserted ``quiz_questions`` rows, degrading gracefully to an
     un-anchored insert when the additive Phase 2 columns are absent. A ``pre`` /
     ``post`` stage uses the whole-topic transcripts (Req 2.2, 4.3).

  3. A failed LLM generation yields an empty result (zero stored) without raising
     into the request path (Req 4.3).

The LLM stages (``_generate_questions`` / ``_vet_questions``) are mocked so no real
API call happens.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_placement_anchor_integration.py``.

Validates: Requirements 2.1, 2.2, 4.3
"""
from __future__ import annotations

import app.db.supabase as supabase_mod
import app.services.quiz as quiz_mod
from app.services import placement, placement_score, quiz
from app.services.level_filter import LEVELS, derive_content_level
from app.services.stage_anchor import DEFAULT_STAGE
from tests.conftest import FakeDB, _FakeQuery


def _use_db(monkeypatch, db):
    """Point the lazy ``get_client`` import inside ``quiz`` at the fake client."""
    monkeypatch.setattr(supabase_mod, "get_client", lambda: db)


# ---------------------------------------------------------------------------
# 1. Placement seam: score_to_level feeds placement.place
# ---------------------------------------------------------------------------

def _nodes() -> list[placement.SpineNode]:
    """A leveled, prerequisite-ordered path: 2 beginner, 2 intermediate, 1
    advanced. The first node of each band is the level-appropriate entry."""
    return [
        placement.SpineNode("intro", "beginner", 5),
        placement.SpineNode("basics", "beginner", 6),
        placement.SpineNode("core", "intermediate", 7),
        placement.SpineNode("applied", "intermediate", 8),
        placement.SpineNode("deep", "advanced", 9),
    ]


def _place(correct: int, total: int, grade_level: str | None = None):
    """Mirror the ``submit_placement`` seam: map the diagnostic to a starting
    level, then place onto the leveled path."""
    level = placement_score.score_to_level(correct, total, grade_level)
    return level, placement.place(_nodes(), [], level)


class TestPlacementSeam:
    def test_low_score_places_at_beginner_entry(self):
        level, result = _place(0, 4)
        assert level == "beginner"
        assert result.entry_index == 0  # first beginner node

    def test_mid_score_places_at_intermediate_entry(self):
        level, result = _place(2, 4)  # fraction 0.5 -> intermediate
        assert level == "intermediate"
        assert result.entry_index == 2  # first intermediate node

    def test_high_score_places_at_advanced_entry(self):
        level, result = _place(8, 8)  # fraction 1.0 -> advanced
        assert level == "advanced"
        assert result.entry_index == 4  # the advanced node

    def test_threshold_boundaries_map_through(self):
        # fraction == 0.4 -> intermediate; fraction == 0.75 -> advanced.
        inter_level, inter = _place(2, 5)   # exactly 0.4
        adv_level, adv = _place(3, 4)        # exactly 0.75
        assert inter_level == "intermediate" and inter.entry_index == 2
        assert adv_level == "advanced" and adv.entry_index == 4

    def test_skipped_diagnostic_defers_to_grade_level(self):
        # total == 0 -> defer entirely to derive_content_level(grade_level).
        level, result = _place(0, 0, grade_level=None)
        assert level == derive_content_level(None)  # 'intermediate'
        assert result.entry_index == 2  # first node at the deferred level

    def test_entry_index_monotonic_non_decreasing_in_correct(self):
        # Holding total fixed, increasing correct never lowers the entry index.
        entries = []
        for correct in range(0, 9):
            level, result = _place(correct, 8)
            assert level in LEVELS
            entries.append(result.entry_index)
        assert entries == sorted(entries)
        # spans the full band range as the score climbs
        assert entries[0] == 0 and entries[-1] == 4


# ---------------------------------------------------------------------------
# 2. Anchored checkpoint generation (LLM mocked)
# ---------------------------------------------------------------------------

def _seed_clips_db() -> FakeDB:
    """A topic with clips spread across three beats; beat 1 has two clips."""
    return FakeDB(store={
        "clips": [
            {"id": "c0", "topic_slug": "binary-search", "section_index": 0,
             "transcript": "beat0 text"},
            {"id": "c1", "topic_slug": "binary-search", "section_index": 1,
             "transcript": "beat1 text"},
            {"id": "c2", "topic_slug": "binary-search", "section_index": 1,
             "transcript": "beat1 text two"},
            {"id": "c3", "topic_slug": "binary-search", "section_index": 2,
             "transcript": "beat2 text"},
        ],
        "quiz_questions": [],
    })


def _mock_llm(monkeypatch, captured: dict):
    """Mock the two LLM stages so no real API call happens. ``_generate_questions``
    records the transcripts it is handed (the scope under test); ``_vet_questions``
    passes the generated questions straight through."""
    pool = [
        {"question": f"Q{i}?", "options": ["a", "b", "c"],
         "correct_index": 0, "explanation": "because"}
        for i in range(quiz_mod.QUESTIONS_PER_TOPIC)
    ]

    def fake_generate(topic_name, sections, transcripts, n=quiz_mod.QUESTIONS_PER_TOPIC):
        captured["transcripts"] = list(transcripts)
        return list(pool)

    def fake_vet(topic_name, raw_questions):
        return list(raw_questions)

    monkeypatch.setattr(quiz_mod, "_generate_questions", fake_generate)
    monkeypatch.setattr(quiz_mod, "_vet_questions", fake_vet)
    return pool


class TestCheckUsesOnlyBeatTranscripts:
    def test_check_selects_only_the_anchored_beat_clips(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)
        captured: dict = {}
        _mock_llm(monkeypatch, captured)

        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="check", section_index=1
        )

        assert stored == quiz_mod.QUESTIONS_PER_TOPIC
        # The clips query was filtered by section_index == 1: ONLY beat 1's two
        # transcripts reached generation (beat 0 and beat 2 are excluded).
        assert captured["transcripts"] == ["beat1 text", "beat1 text two"]

    def test_check_persists_stage_and_section_index_on_rows(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)
        _mock_llm(monkeypatch, {})

        quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="check", section_index=2
        )

        rows = [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"]
        assert rows and len(rows) == quiz_mod.QUESTIONS_PER_TOPIC
        for r in rows:
            assert r["stage"] == "check"
            assert r["section_index"] == 2
            assert r["topic_slug"] == "binary-search"


class TestTopicWideStages:
    def test_pre_uses_whole_topic_transcripts(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)
        captured: dict = {}
        _mock_llm(monkeypatch, captured)

        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="pre"
        )

        assert stored == quiz_mod.QUESTIONS_PER_TOPIC
        # pre is topic-wide: transcripts from every beat are included, unfiltered.
        assert captured["transcripts"] == [
            "beat0 text", "beat1 text", "beat1 text two", "beat2 text",
        ]
        rows = [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"]
        for r in rows:
            assert r["stage"] == "pre"
            assert r["section_index"] is None  # topic-wide carries no beat anchor

    def test_post_uses_whole_topic_transcripts(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)
        captured: dict = {}
        _mock_llm(monkeypatch, captured)

        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="post"
        )

        assert stored == quiz_mod.QUESTIONS_PER_TOPIC
        assert captured["transcripts"] == [
            "beat0 text", "beat1 text", "beat1 text two", "beat2 text",
        ]
        rows = [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"]
        for r in rows:
            assert r["stage"] == "post"
            assert r["section_index"] is None


# ---------------------------------------------------------------------------
# 2b. Graceful fallback when the additive Phase 2 columns are absent
# ---------------------------------------------------------------------------

class _AnchorRejectingQuery(_FakeQuery):
    """A FakeDB query whose INSERT raises when the payload carries the additive
    Phase 2 columns, simulating a pre-migration ``quiz_questions`` schema. Selects
    and anchor-free inserts behave normally."""

    def execute(self):
        if self.op == "insert" and self.payload and (
            "stage" in self.payload or "section_index" in self.payload
        ):
            raise RuntimeError("column \"stage\" of relation \"quiz_questions\" does not exist")
        return super().execute()


class _LegacyQuizDB(FakeDB):
    """FakeDB that rejects anchored inserts (additive columns absent)."""

    def table(self, name):
        return _AnchorRejectingQuery(name, self.store, self.rec, self.fail)


class TestGracefulFallbackWhenColumnsAbsent:
    def test_anchored_insert_degrades_to_unanchored_without_raising(self, monkeypatch):
        db = _LegacyQuizDB(store={
            "clips": [
                {"id": "c1", "topic_slug": "binary-search", "section_index": 1,
                 "transcript": "beat1 text"},
            ],
            "quiz_questions": [],
        })
        _use_db(monkeypatch, db)
        _mock_llm(monkeypatch, {})

        # Must not raise even though every anchored insert is rejected.
        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="check", section_index=1
        )

        # The questions are still stored, just without the anchor columns.
        assert stored == quiz_mod.QUESTIONS_PER_TOPIC
        rows = [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"]
        assert len(rows) == quiz_mod.QUESTIONS_PER_TOPIC
        for r in rows:
            assert "stage" not in r
            assert "section_index" not in r
            assert r["topic_slug"] == "binary-search"


# ---------------------------------------------------------------------------
# 3. A failed generation yields an empty result without raising
# ---------------------------------------------------------------------------

class TestFailedGenerationNeverRaises:
    def test_llm_failure_returns_zero_and_stores_nothing(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)

        def boom(*args, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(quiz_mod, "_generate_questions", boom)

        # Best-effort: a generation failure is swallowed, never raised.
        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="check", section_index=1
        )

        assert stored == 0
        assert [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"] == []

    def test_empty_generation_pool_returns_zero(self, monkeypatch):
        db = _seed_clips_db()
        _use_db(monkeypatch, db)
        monkeypatch.setattr(quiz_mod, "_generate_questions", lambda *a, **k: [])
        monkeypatch.setattr(quiz_mod, "_vet_questions", lambda *a, **k: [])

        stored = quiz.generate_and_store_questions(
            "binary-search", "Binary Search", stage="post"
        )

        assert stored == 0
        assert [p for (t, p) in db.rec["inserts"] if t == "quiz_questions"] == []


# A sanity check that DEFAULT_STAGE is the anchor default the migration mirrors,
# so the un-anchored fallback path above lands on a topic-wide 'check' (Req 5.3).
def test_default_stage_is_check():
    assert DEFAULT_STAGE == "check"
