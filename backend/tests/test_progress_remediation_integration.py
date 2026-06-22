"""Integration test for progression and the soft remediation invariant (Task 18.5).

Wires the now-shipped Phase 3 shells + cores together against the in-memory
Supabase double (``FakeDB`` from ``conftest``) with NO external service touched --
no real YouTube, LLM, or DB. Per the design's Testing Strategy, the contract is
exercised at the service seams the design exposes (the full HTTP
``GET /api/progress`` / ``GET /api/feed`` round-trips are auth-heavy, so the
progression + remediation contract is asserted at the ``progress.compute_progress``
and ``feed._select_rewatch_clips`` shell boundaries, mirroring the approach used by
the Phase 1/2 integration tests):

  1. Progress computed + upserted from quiz + completion: ``compute_progress`` reads
     a ``learning_paths`` row (topic_slugs + session_id + serialized LeveledPath),
     ``quiz_results``, and ``clip_events`` joined to ``clips``, runs the pure
     ``mastery.topic_mastery`` per topic, and upserts ``learning_progress`` rows
     whose ``mastery_score`` is the clamped core value (Req 3.1, 5.4).
  2. Advisory only (no locked state): every per-topic ``unlock`` is one of
     ``available | recommended | mastered`` -- there is no blocking value -- and
     exactly the earliest unmastered topic in serving order is ``recommended``
     (Req 3.3).
  3. Soft remediation targets the weak beat: ``_select_rewatch_clips`` returns only
     the learner's seen clips on the weak beat, bounded by ``max_clips``, in
     Canonical_Arc order; an empty / absent signal yields ``[]`` without raising
     (Req 3.2, 4.2).
  4. Graceful degradation: when the ``learning_progress`` upsert fails (the additive
     Phase 3 table may be absent, like a pre-migration legacy DB), ``compute_progress``
     still returns a well-formed object without raising -- progress never blocks the
     request path (Req 4.2, 4.3).

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_progress_remediation_integration.py``.

Validates: Requirements 3.1, 3.3, 4.2
"""
from __future__ import annotations

from app.api.feed import _select_rewatch_clips
from app.models.schemas import Clip
from app.services import mastery, progress, soft_unlock
from app.services.remediation import DEFAULT_MAX_REWATCH
from tests.conftest import FakeDB

_SUBJECT = "user-1"
_SESSION = "sess-1"

# Advisory Soft_Unlock status values; there is intentionally NO blocking value.
_ADVISORY = {"available", "recommended", "mastered"}


# ---------------------------------------------------------------------------
# Progress fixtures: a 3-topic path across two levels, with quiz + completion
# signals chosen so one topic is mastered, one in progress, one untouched.
# ---------------------------------------------------------------------------

def _progress_db() -> FakeDB:
    """Seed a learner whose path is grouped into two levels.

    intro:    3/3 quiz correct, 2/2 clips completed -> mastered.
    core:     1/4 quiz correct, 1/4 clips completed -> in_progress.
    advanced: nothing answered or seen              -> not_started.
    """
    return FakeDB(store={
        "learning_paths": [
            {
                "session_id": _SESSION,
                "user_id": _SUBJECT,
                "topic_slugs": ["intro", "core", "advanced"],
                "levels": [
                    {"ordinal": 1, "name": "Foundations",
                     "topic_slugs": ["intro", "core"]},
                    {"ordinal": 2, "name": "Core", "topic_slugs": ["advanced"]},
                ],
            }
        ],
        # One row per answered question (summarize_mastery counts rows). Matched
        # by user_id only (no session_id) so the by-session read never double counts.
        "quiz_results": (
            [{"topic_slug": "intro", "user_id": _SUBJECT, "correct": True, "points": 10}
             for _ in range(3)]
            + [{"topic_slug": "core", "user_id": _SUBJECT, "correct": True, "points": 10}]
            + [{"topic_slug": "core", "user_id": _SUBJECT, "correct": False, "points": 0}
               for _ in range(3)]
        ),
        # clip_events for the learner's session; completion is per distinct clip.
        "clip_events": [
            {"session_id": _SESSION, "clip_id": "intro-c1", "completed": True},
            {"session_id": _SESSION, "clip_id": "intro-c2", "completed": True},
            {"session_id": _SESSION, "clip_id": "core-c1", "completed": True},
            {"session_id": _SESSION, "clip_id": "core-c2", "completed": False},
            {"session_id": _SESSION, "clip_id": "core-c3", "completed": False},
            {"session_id": _SESSION, "clip_id": "core-c4", "completed": False},
        ],
        # The clip -> topic join used to attribute completion to a topic.
        "clips": [
            {"id": "intro-c1", "topic_slug": "intro"},
            {"id": "intro-c2", "topic_slug": "intro"},
            {"id": "core-c1", "topic_slug": "core"},
            {"id": "core-c2", "topic_slug": "core"},
            {"id": "core-c3", "topic_slug": "core"},
            {"id": "core-c4", "topic_slug": "core"},
        ],
    })


def _expected_masteries() -> dict[str, mastery.TopicMastery]:
    """Re-derive the per-topic mastery from the pure core (the source of truth)."""
    return {
        "intro": mastery.topic_mastery("intro", quiz_correct=3, quiz_answered=3,
                                       clips_completed=2, clips_seen=2),
        "core": mastery.topic_mastery("core", quiz_correct=1, quiz_answered=4,
                                      clips_completed=1, clips_seen=4),
        "advanced": mastery.topic_mastery("advanced", quiz_correct=0, quiz_answered=0,
                                          clips_completed=0, clips_seen=0),
    }


# ---------------------------------------------------------------------------
# 1. Progress computed + upserted from quiz + completion
# ---------------------------------------------------------------------------

class TestProgressComputedAndUpserted:
    def test_per_topic_status_and_mastery_match_the_core(self):
        db = _progress_db()
        result = progress.compute_progress(_SUBJECT, db=db)

        exp = _expected_masteries()
        topics = result["topics"]
        assert set(topics) == {"intro", "core", "advanced"}
        for slug, tm in exp.items():
            assert topics[slug]["mastery_score"] == tm.mastery_score
            assert topics[slug]["status"] == tm.status
            assert topics[slug]["mastered"] == tm.mastered

        # Sanity on the chosen fixture: the three progression states are covered.
        assert exp["intro"].status == "mastered"
        assert exp["core"].status == "in_progress"
        assert exp["advanced"].status == "not_started"

    def test_per_level_progress_matches_soft_unlock_core(self):
        db = _progress_db()
        result = progress.compute_progress(_SUBJECT, db=db)
        exp = _expected_masteries()

        foundations = soft_unlock.level_progress(
            1, [exp["intro"].mastered, exp["core"].mastered]
        )
        core_level = soft_unlock.level_progress(2, [exp["advanced"].mastered])

        levels = result["levels"]
        assert [lvl["name"] for lvl in levels] == ["Foundations", "Core"]

        assert levels[0]["percent_complete"] == foundations.percent_complete == 50
        assert levels[0]["all_mastered"] is foundations.all_mastered is False
        assert levels[1]["percent_complete"] == core_level.percent_complete == 0
        assert levels[1]["all_mastered"] is core_level.all_mastered is False

    def test_learning_progress_rows_upserted_with_clamped_scores(self):
        db = _progress_db()
        progress.compute_progress(_SUBJECT, db=db)

        upserts = [p for (t, p) in db.rec["upserts"] if t == "learning_progress"]
        assert upserts, "expected a best-effort learning_progress upsert"
        rows = upserts[-1]
        by_slug = {r["topic_slug"]: r for r in rows}
        assert set(by_slug) == {"intro", "core", "advanced"}

        exp = _expected_masteries()
        for slug, r in by_slug.items():
            assert r["subject"] == _SUBJECT
            # The persisted score is the clamped core value, so the DB
            # check (mastery_score >= 0 and <= 1) can never be violated (Req 5.4).
            assert 0.0 <= r["mastery_score"] <= 1.0
            assert r["mastery_score"] == exp[slug].mastery_score
            assert r["status"] == exp[slug].status


# ---------------------------------------------------------------------------
# 2. Advisory only: no locked state; exactly one recommended topic
# ---------------------------------------------------------------------------

class TestAdvisoryOnlyNoLockedState:
    def test_every_unlock_is_advisory(self):
        db = _progress_db()
        result = progress.compute_progress(_SUBJECT, db=db)

        for slug, topic in result["topics"].items():
            assert topic["unlock"] in _ADVISORY, slug

    def test_exactly_earliest_unmastered_topic_is_recommended(self):
        db = _progress_db()
        result = progress.compute_progress(_SUBJECT, db=db)
        topics = result["topics"]

        recommended = [s for s, t in topics.items() if t["unlock"] == "recommended"]
        # intro is mastered, so the earliest unmastered in serving order is core.
        assert recommended == ["core"]
        assert topics["intro"]["unlock"] == "mastered"
        assert topics["advanced"]["unlock"] == "available"


# ---------------------------------------------------------------------------
# 3. Soft remediation targets the weak beat
# ---------------------------------------------------------------------------

_WEAK_BEAT = 1


def _clip_row(clip_id, section_index, role_ordinal, final_score):
    return {
        "id": clip_id,
        "topic_slug": "binary-search",
        "title": clip_id,
        "video_url": "https://example.com/v",
        "section_index": section_index,
        "role_ordinal": role_ordinal,
        "final_score": final_score,
    }


def _remediation_db() -> FakeDB:
    """Seen clips spread across three beats for one topic; the weak beat (1) has
    three clips with mixed role ordinals (incl. a None ordinal that sorts last)."""
    return FakeDB(store={
        "clip_events": [
            {"session_id": _SESSION, "clip_id": cid}
            for cid in ("bs-b1-a", "bs-b1-b", "bs-b1-c", "bs-b0", "bs-b2")
        ],
        "clips": [
            _clip_row("bs-b1-a", 1, 2, 0.9),       # weak beat, ordinal 2
            _clip_row("bs-b1-b", 1, 1, 0.5),       # weak beat, ordinal 1 (first)
            _clip_row("bs-b1-c", 1, None, 0.8),    # weak beat, no ordinal (last)
            _clip_row("bs-b0", 0, 1, 0.95),        # other beat -> excluded
            _clip_row("bs-b2", 2, 1, 0.95),        # other beat -> excluded
        ],
    })


class TestSoftRemediationTargetsWeakBeat:
    def test_returns_only_weak_beat_clips_in_arc_order(self):
        db = _remediation_db()
        clips = _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT)

        # Only the weak beat's clips, never the other beats'.
        assert all(c.section_index == _WEAK_BEAT for c in clips)
        assert {c.id for c in clips} == {"bs-b1-a", "bs-b1-b", "bs-b1-c"}
        # Arc order: role_ordinal asc (None last), then final_score desc, then id asc.
        assert [c.id for c in clips] == ["bs-b1-b", "bs-b1-a", "bs-b1-c"]

    def test_bounded_by_max_clips(self):
        db = _remediation_db()
        clips = _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT, max_clips=2)

        assert len(clips) == 2
        assert [c.id for c in clips] == ["bs-b1-b", "bs-b1-a"]

    def test_default_max_clips_caps_result(self):
        db = _remediation_db()
        clips = _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT)
        assert len(clips) <= DEFAULT_MAX_REWATCH

    def test_returns_clip_objects(self):
        db = _remediation_db()
        clips = _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT)
        assert clips and all(isinstance(c, Clip) for c in clips)

    def test_no_seen_clips_returns_empty_without_raising(self):
        db = FakeDB(store={"clip_events": [], "clips": []})
        assert _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT) == []

    def test_absent_weak_beat_signal_returns_empty(self):
        # The learner has seen clips, but none on the requested (empty) beat.
        db = _remediation_db()
        clips = _select_rewatch_clips(db, _SESSION, "binary-search", 3)
        assert clips == []

    def test_read_failure_degrades_to_empty_without_raising(self):
        # A failed clip_events read must never raise into the request path (Req 4.3).
        db = FakeDB(store={"clips": []}, fail={"clip_events"})
        assert _select_rewatch_clips(db, _SESSION, "binary-search", _WEAK_BEAT) == []


# ---------------------------------------------------------------------------
# 4. Graceful degradation: a failed learning_progress upsert never raises
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_failed_progress_upsert_still_returns_well_formed_object(self):
        # learning_progress upsert raises (table absent / pre-migration legacy DB),
        # mirroring the Phase-2 legacy DB double. Reads still succeed.
        db = _progress_db()
        db.fail.add("learning_progress")

        # Must not raise even though every learning_progress upsert is rejected.
        result = progress.compute_progress(_SUBJECT, db=db)

        # The progress object is still well-formed and complete.
        assert result["subject"] == _SUBJECT
        assert [lvl["name"] for lvl in result["levels"]] == ["Foundations", "Core"]
        assert set(result["topics"]) == {"intro", "core", "advanced"}
        for topic in result["topics"].values():
            assert topic["unlock"] in _ADVISORY
            assert 0.0 <= topic["mastery_score"] <= 1.0

        # The upsert was attempted but swallowed -- nothing recorded, no crash.
        assert [p for (t, p) in db.rec["upserts"] if t == "learning_progress"] == []
