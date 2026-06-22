"""Bug condition exploration tests for the feed clip generation hang.

These tests encode the DESIRED (post-fix) behavior of the self-heal retry
budget, the terminal failed state, and the segmentation fallback. They are
written BEFORE the fix and are EXPECTED TO FAIL on the unfixed code - each
failure is a counterexample that proves the bug.

Property 1 (Bug Condition): unbounded self-heal re-trigger + segmentation
zero-out. Cases A-C drive get_path_feed with FakeDB across simulated polls;
case D exercises _node_segment directly with a scoped Hypothesis strategy.

Run (unfixed): cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py
ASCII only.
"""
import asyncio
import time
from unittest.mock import patch

from fastapi import BackgroundTasks
from hypothesis import given, settings, strategies as st

import app.api.feed as feed_api
import app.api.topics as topics_api
import app.services.path_extension as path_extension
import app.services.topic_expansion as topic_expansion
from app.agents import pipeline_agent, section_planner
from app.api.feed import get_path_feed
from tests.conftest import FakeDB

# The module under construction (created in task 3.1). The test file must stay
# collectible on the unfixed code, so every reference to it is guarded.
try:  # pragma: no cover - import guard
    from app.services import self_heal_state as _shs
except ImportError:  # unfixed code: module does not exist yet
    _shs = None


def _max_attempts() -> int:
    """MAX_SELF_HEAL_ATTEMPTS from the policy module, or the design default."""
    if _shs is not None:
        return _shs.MAX_SELF_HEAL_ATTEMPTS
    return 3


def _record_failed_attempt(slug: str, backdate: bool = False) -> None:
    """Simulate a background generation attempt finishing with 0 clips.

    This mirrors what _process_single_topic will do post-fix (task 3.5):
    record a failed/empty attempt so the retry budget survives the
    generating_slugs lifecycle. On the unfixed code the policy module does not
    exist yet, so this is a no-op - which is exactly why the runaway loop
    reproduces (nothing remembers the failed attempt).

    backdate pushes the recorded attempt time past the cooldown window so a
    subsequent poll is gated only by the attempt cap, not the cooldown.
    """
    if _shs is None:
        return
    _shs.record_attempt(slug)
    if backdate:
        count, _last = _shs._self_heal_attempts[slug]
        _shs._self_heal_attempts[slug] = (
            count,
            time.monotonic() - _shs.SELF_HEAL_COOLDOWN_SECONDS - 1,
        )


def _empty_topic_store(slug: str = "empty-topic", session_id: str = "s1"):
    """FakeDB store for a single empty, non-generating topic."""
    return {
        "learning_paths": [
            {
                "session_id": session_id,
                "topic_slugs": [slug],
                "user_query": "teach me",
                "user_id": None,
            }
        ],
        "topics": [{"slug": slug, "name": "Empty Topic"}],
        "clips": [],
        "clip_events": [],
        "session_embeddings": [],
    }


def _self_heal_tasks(bg: BackgroundTasks):
    """Background tasks queued for the self-heal generation pipeline."""
    return [t for t in bg.tasks if t.func is topics_api._process_single_topic]


class TestBugConditionExploration:
    """Property 1 - bounded self-heal, terminal state, graceful segmentation."""

    def setup_method(self):
        # Mirror TestShouldExpandTopic clearing te._expanding: reset the
        # in-process generation guard and the (post-fix) attempt tracker so
        # each case starts from a clean slate.
        topics_api.generating_slugs.clear()
        if _shs is not None:
            _shs._self_heal_attempts.clear()

    # --- Case A: unbounded re-trigger -------------------------------------
    def test_case_a_self_heal_is_bounded_by_max_attempts(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        cap = _max_attempts()
        polls = cap * 3  # well past the budget
        triggers = 0
        for _ in range(polls):
            bg = BackgroundTasks()
            asyncio.run(get_path_feed("s1", bg, caller_id="u"))
            queued = _self_heal_tasks(bg)
            if queued:
                triggers += len(queued)
                # The attempt produced 0 clips: record it (desired) and let the
                # slug fall out of generating_slugs as the real finally would.
                _record_failed_attempt(slug, backdate=True)
            topics_api.generating_slugs.discard(slug)

        # DESIRED: generation is triggered at most MAX_SELF_HEAL_ATTEMPTS times.
        # UNFIXED: the gate fires on every poll -> triggers == polls -> FAILS.
        assert triggers <= cap, (
            f"empty topic re-triggered {triggers} times across {polls} polls "
            f"(expected at most {cap})"
        )

    # --- Case B: no cooldown ----------------------------------------------
    def test_case_b_cooldown_suppresses_consecutive_retrigger(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # First poll: in budget, queues exactly one background generation.
        bg1 = BackgroundTasks()
        asyncio.run(get_path_feed("s1", bg1, caller_id="u"))
        assert len(_self_heal_tasks(bg1)) == 1

        # Attempt finishes with 0 clips, within the cooldown window (no backdate).
        _record_failed_attempt(slug)
        topics_api.generating_slugs.discard(slug)

        # Second, immediately consecutive poll.
        bg2 = BackgroundTasks()
        asyncio.run(get_path_feed("s1", bg2, caller_id="u"))

        # DESIRED: the cooldown suppresses the second trigger.
        # UNFIXED: no cooldown -> the second poll re-triggers -> FAILS.
        assert _self_heal_tasks(bg2) == [], (
            "second consecutive poll re-triggered generation within cooldown"
        )

    # --- Case C: no terminal state ----------------------------------------
    def test_case_c_out_of_budget_topic_is_terminal_failed(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # Drive the topic out of its retry budget.
        for _ in range(_max_attempts()):
            _record_failed_attempt(slug, backdate=True)
        topics_api.generating_slugs.discard(slug)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        fr = next(f for f in feeds if f.topic_slug == slug)

        # DESIRED: an out-of-budget empty topic is terminal - the spinner stops.
        # UNFIXED: processing is always True for empty and there is no failed
        # field -> both assertions FAIL.
        assert fr.processing is False, "out-of-budget empty topic still reports processing=True"
        assert getattr(fr, "failed", False) is True, "out-of-budget empty topic does not report failed=True"

    # --- Case D: segmentation zero-out ------------------------------------
    @settings(max_examples=25, deadline=None)
    @given(transcript=st.text(min_size=1, max_size=80))
    def test_case_d_empty_segmentation_falls_back_to_one_clip(self, transcript):
        video = {
            "video_id": "vid1",
            "title": "Intro to Binary Search",
            "description": "a description",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "duration_seconds": 300,
            "transcript": transcript,
        }
        state = {
            "topic_slug": "binary-search",
            "topic_name": "Binary Search",
            "search_query": None,
            "section_index": None,
            "section_title": None,
            "section_description": None,
            "arc_titles": [],
            "clear_existing": True,
            "videos": [video],
            "clips": [],
            "stored_count": 0,
            "errors": [],
        }
        # Segmentation returns [] (LLM error / unparseable JSON, both return []);
        # keep embeddings offline and deterministic.
        with patch("app.services.pipeline._identify_segments", return_value=[]), \
                patch("app.services.embeddings.embed_texts",
                      side_effect=lambda texts: [None] * len(texts)):
            result = pipeline_agent._node_segment(state)

        # DESIRED: a transcript-bearing video still yields at least one clip.
        # UNFIXED: an empty segment list contributes 0 clips -> FAILS.
        assert len(result["clips"]) >= 1, (
            "transcript-bearing video with empty segmentation produced 0 clips"
        )


def _populated_topic_store(slug: str, clip_ids: list[str], session_id: str = "s1"):
    """FakeDB store for a single topic seeded with clips (section-less, so the
    fallback retrieval path runs deterministically)."""
    return {
        "learning_paths": [
            {
                "session_id": session_id,
                "topic_slugs": [slug],
                "user_query": "teach me",
                "user_id": None,
            }
        ],
        "topics": [{"slug": slug, "name": "Some Topic"}],
        "clips": [
            {
                "id": cid,
                "topic_slug": slug,
                "title": f"Clip {cid}",
                "description": "d",
                "video_url": "https://example.com/v",
                "thumbnail_url": None,
                "duration_seconds": 120,
                "source_url": None,
                "source_platform": "youtube",
                "hook_score": 0.5,
                "created_at": None,
                "section_index": None,
            }
            for cid in clip_ids
        ],
        "clip_events": [],
        "session_embeddings": [],
    }


class TestPreservation:
    """Property 2 - non-buggy feed and segmentation behavior is unchanged.

    These assertions are observation-first: they record what the UNFIXED code
    does for inputs that do NOT satisfy the bug condition (populated topics,
    actively-generating topics, in-budget empty topics, and non-empty
    segmentation) and they MUST pass on the unfixed code. After the fix they
    must keep passing, proving no regression.
    """

    def setup_method(self):
        # Mirror TestShouldExpandTopic clearing te._expanding.
        topics_api.generating_slugs.clear()
        if _shs is not None:
            _shs._self_heal_attempts.clear()

    # --- Populated topic: returns its clips, processing=false -------------
    def test_populated_topic_returns_clips_not_processing(self, monkeypatch):
        slug = "populated-topic"
        ids = ["c1", "c2", "c3"]
        db = FakeDB(store=_populated_topic_store(slug, ids))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        fr = next(f for f in feeds if f.topic_slug == slug)

        # Observed on unfixed code: the seeded clips come back, processing=false,
        # and no self-heal task is queued for a populated topic.
        assert {c.id for c in fr.clips} == set(ids)
        assert fr.processing is False
        assert _self_heal_tasks(bg) == []

    # --- Generating topic: processing=true, no duplicate trigger ----------
    def test_generating_topic_processing_no_duplicate_task(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # Mark the slug as actively generating before the poll.
        topics_api.generating_slugs.add(slug)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        fr = next(f for f in feeds if f.topic_slug == slug)

        # Observed on unfixed code: a slug already generating reports
        # processing=true and is NOT re-queued (the generating_slugs guard
        # de-dupes the concurrent run).
        assert fr.processing is True
        assert _self_heal_tasks(bg) == []

    # --- In-budget empty topic: first poll queues exactly one task --------
    def test_in_budget_empty_topic_queues_one_task(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # No prior attempts recorded (in budget): the first poll self-heals.
        bg = BackgroundTasks()
        asyncio.run(get_path_feed("s1", bg, caller_id="u"))

        # Observed on unfixed code: exactly one background generation task is
        # queued for an empty, non-generating topic with no prior attempts.
        assert len(_self_heal_tasks(bg)) == 1

    # --- Non-empty segmentation: exactly N clips, no extra base clip ------
    @settings(max_examples=25, deadline=None)
    @given(n=st.integers(min_value=1, max_value=5))
    def test_non_empty_segmentation_yields_exactly_n_clips(self, n):
        video = {
            "video_id": "vid1",
            "title": "Base Video Title",
            "description": "a description",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "duration_seconds": 300,
            "transcript": "some transcript text",
        }
        state = {
            "topic_slug": "binary-search",
            "topic_name": "Binary Search",
            "search_query": None,
            "section_index": None,
            "section_title": None,
            "section_description": None,
            "arc_titles": [],
            "clear_existing": True,
            "videos": [video],
            "clips": [],
            "stored_count": 0,
            "errors": [],
        }
        # Segmentation returns N >= 1 segments with titles distinct from the
        # base video title, so an extra base clip would be detectable.
        segments = [
            {
                "title": f"Segment {i}",
                "description": f"desc {i}",
                "start": i * 10,
                "end": i * 10 + 8,
                "transcript": f"seg transcript {i}",
                "hook_score": 0.6,
            }
            for i in range(n)
        ]
        with patch("app.services.pipeline._identify_segments", return_value=segments), \
                patch("app.services.embeddings.embed_texts",
                      side_effect=lambda texts: [None] * len(texts)):
            result = pipeline_agent._node_segment(state)

        clips = result["clips"]
        # Observed on unfixed code: exactly N clips, all from the segments, with
        # no extra fallback "base" clip (no clip carries the base video title).
        assert len(clips) == n
        assert [c["title"] for c in clips] == [f"Segment {i}" for i in range(n)]
        assert all(c["title"] != "Base Video Title" for c in clips)

    # --- Property: non-buggy feed states have unchanged processing + clips -
    @settings(max_examples=30, deadline=None)
    @given(num_clips=st.integers(min_value=0, max_value=4), is_generating=st.booleans())
    def test_non_buggy_feed_state_processing_and_clipset_unchanged(self, num_clips, is_generating):
        # Restrict to NON-buggy states: a populated topic OR an actively
        # generating one. The bug condition is (no clips AND not generating),
        # which is excluded here.
        if num_clips == 0 and not is_generating:
            return

        slug = "feed-topic"
        ids = [f"k{i}" for i in range(num_clips)]
        db = FakeDB(store=_populated_topic_store(slug, ids))

        topics_api.generating_slugs.clear()
        if _shs is not None:
            _shs._self_heal_attempts.clear()
        if is_generating:
            topics_api.generating_slugs.add(slug)

        try:
            with patch.object(feed_api, "get_client", lambda: db):
                bg = BackgroundTasks()
                feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        finally:
            topics_api.generating_slugs.discard(slug)

        fr = next(f for f in feeds if f.topic_slug == slug)

        # Observed on unfixed code: processing == (is_generating or no clips),
        # and the returned clip set is exactly the seeded set.
        assert fr.processing == (is_generating or num_clips == 0)
        assert {c.id for c in fr.clips} == set(ids)


class TestSelfHealStatePolicy:
    """Pure unit + property tests for the self_heal_state policy module.

    The module is implemented (task 3.1), so these tests exercise the pure
    decision functions (should_self_heal / is_terminal_failed) and the thin
    in-process attempt tracker (read / record_attempt / clear) directly. They
    MUST pass on the implemented module. Constants are read from the module
    (cap-1, cap, cooldown boundary) so the tests stay correct if the policy
    constants change. ASCII only.
    """

    def setup_method(self):
        # Mirror the other classes: reset the in-process attempt tracker so each
        # test starts from a clean slate.
        if _shs is not None:
            _shs._self_heal_attempts.clear()

    # --- should_self_heal: short-circuits ---------------------------------
    def test_has_clips_short_circuits_to_false(self):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        # A populated topic never self-heals, regardless of attempts/age.
        assert _shs.should_self_heal(True, False, 0, None) is False
        assert _shs.should_self_heal(True, False, cap - 1, None) is False
        assert _shs.should_self_heal(True, True, 0, 0.0) is False

    def test_is_generating_short_circuits_to_false(self):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        # An actively-generating topic never self-heals (no double trigger).
        assert _shs.should_self_heal(False, True, 0, None) is False
        assert _shs.should_self_heal(False, True, cap - 1, None) is False

    # --- should_self_heal: attempt cap boundary ---------------------------
    def test_cap_boundary_attempts_below_cap_triggers(self):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        # attempts == cap - 1, in budget and past any cooldown -> trigger.
        age_past_cooldown = _shs.SELF_HEAL_COOLDOWN_SECONDS + 1
        assert _shs.should_self_heal(False, False, cap - 1, age_past_cooldown) is True

    def test_cap_boundary_attempts_at_cap_does_not_trigger(self):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        # attempts == cap -> terminal, never trigger even past the cooldown.
        age_past_cooldown = _shs.SELF_HEAL_COOLDOWN_SECONDS + 1
        assert _shs.should_self_heal(False, False, cap, age_past_cooldown) is False

    # --- should_self_heal: cooldown boundary ------------------------------
    def test_cooldown_boundary_age_just_under_suppresses(self):
        # A prior attempt exists and age is just under the cooldown -> suppress.
        cooldown = _shs.SELF_HEAL_COOLDOWN_SECONDS
        assert _shs.should_self_heal(False, False, 1, cooldown - 0.001) is False

    def test_cooldown_boundary_age_at_cooldown_triggers(self):
        # age == cooldown is NOT < cooldown, so the cooldown no longer suppresses
        # (in budget) -> trigger.
        cooldown = _shs.SELF_HEAL_COOLDOWN_SECONDS
        assert _shs.should_self_heal(False, False, 1, cooldown) is True

    def test_attempts_zero_ignores_cooldown(self):
        # attempts == 0: the cooldown gate (which requires attempts > 0) does not
        # apply, so even a tiny/zero age still triggers.
        assert _shs.should_self_heal(False, False, 0, 0.0) is True
        assert _shs.should_self_heal(False, False, 0, None) is True

    # --- is_terminal_failed ----------------------------------------------
    def test_is_terminal_failed_only_when_empty_not_generating_at_cap(self):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        # True: empty AND not generating AND attempts >= cap.
        assert _shs.is_terminal_failed(False, False, cap) is True
        assert _shs.is_terminal_failed(False, False, cap + 5) is True
        # False when below cap.
        assert _shs.is_terminal_failed(False, False, cap - 1) is False
        # False when it has clips.
        assert _shs.is_terminal_failed(True, False, cap) is False
        # False when it is still generating.
        assert _shs.is_terminal_failed(False, True, cap) is False

    # --- read / record_attempt / clear ------------------------------------
    def test_read_unknown_slug_returns_zero_none(self):
        attempts, age = _shs.read("never-seen")
        assert attempts == 0
        assert age is None

    def test_record_attempt_increments_count(self):
        slug = "topic-x"
        _shs.record_attempt(slug)
        attempts, _age = _shs.read(slug)
        assert attempts == 1
        _shs.record_attempt(slug)
        _shs.record_attempt(slug)
        attempts, _age = _shs.read(slug)
        assert attempts == 3

    def test_record_attempt_age_is_non_negative_float(self):
        slug = "topic-y"
        _shs.record_attempt(slug)
        attempts, age = _shs.read(slug)
        assert attempts == 1
        assert isinstance(age, float)
        assert age >= 0.0
        # The age is computed immediately after recording, so it is tiny.
        assert age < 5.0

    def test_record_attempt_monotonic_age_is_computed(self, monkeypatch):
        # Drive a controllable monotonic clock so the computed age is exact.
        clock = {"now": 1000.0}
        monkeypatch.setattr(_shs.time, "monotonic", lambda: clock["now"])

        slug = "topic-z"
        _shs.record_attempt(slug)  # stamped at t=1000.0
        clock["now"] = 1042.5      # 42.5s later
        attempts, age = _shs.read(slug)
        assert attempts == 1
        assert age == 42.5

    def test_clear_removes_the_entry(self):
        slug = "topic-clear"
        _shs.record_attempt(slug)
        assert _shs.read(slug)[0] == 1
        _shs.clear(slug)
        attempts, age = _shs.read(slug)
        assert attempts == 0
        assert age is None

    def test_clear_unknown_slug_is_noop(self):
        # Clearing a slug that was never recorded must not raise.
        _shs.clear("not-there")
        assert _shs.read("not-there") == (0, None)

    # --- Property 1 invariants (Hypothesis) -------------------------------
    @settings(max_examples=200, deadline=None)
    @given(
        has_clips=st.booleans(),
        is_generating=st.booleans(),
        attempts=st.integers(min_value=0, max_value=_shs.MAX_SELF_HEAL_ATTEMPTS + 5),
        last_attempt_age_seconds=st.one_of(
            st.none(),
            st.floats(
                min_value=0.0,
                max_value=_shs.SELF_HEAL_COOLDOWN_SECONDS * 3,
                allow_nan=False,
                allow_infinity=False,
            ),
        ),
    )
    def test_property1_invariants(self, has_clips, is_generating, attempts, last_attempt_age_seconds):
        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        cooldown = _shs.SELF_HEAL_COOLDOWN_SECONDS
        decision = _shs.should_self_heal(has_clips, is_generating, attempts, last_attempt_age_seconds)
        terminal = _shs.is_terminal_failed(has_clips, is_generating, attempts)

        # Invariant 1: never trigger when at/over the attempt cap.
        if attempts >= cap:
            assert decision is False

        # Invariant 2: never trigger when a prior attempt exists and the age is
        # still within the cooldown window.
        if attempts > 0 and last_attempt_age_seconds is not None \
                and last_attempt_age_seconds < cooldown:
            assert decision is False

        # Invariant 3: is_terminal_failed implies a terminal state - empty, not
        # generating, out of budget - and the policy must NOT self-heal it.
        if terminal:
            assert has_clips is False
            assert is_generating is False
            assert attempts >= cap
            assert decision is False


def _clip_row(slug: str, cid: str) -> dict:
    """A single section-less clip row for FakeDB (mirrors _populated_topic_store)."""
    return {
        "id": cid,
        "topic_slug": slug,
        "title": f"Clip {cid}",
        "description": "d",
        "video_url": "https://example.com/v",
        "thumbnail_url": None,
        "duration_seconds": 120,
        "source_url": None,
        "source_platform": "youtube",
        "hook_score": 0.5,
        "created_at": None,
        "section_index": None,
    }


def _extend_tasks(bg: BackgroundTasks):
    """Background tasks queued for path auto-extension."""
    return [t for t in bg.tasks if t.func is feed_api._extend_path]


def _expand_tasks(bg: BackgroundTasks):
    """Background tasks queued for within-topic expansion."""
    return [t for t in bg.tasks if t.func is feed_api._expand_topic]


def _segment(i: int, start: int, length: int) -> dict:
    """A segmentation result with the keys _node_segment reads."""
    return {
        "title": f"Segment {i}",
        "description": f"desc {i}",
        "start": start,
        "end": start + length,
        "transcript": f"seg transcript {i}",
        "hook_score": 0.6,
    }


@st.composite
def _segment_lists(draw):
    """Random segmentation outputs, including the empty list."""
    n = draw(st.integers(min_value=0, max_value=6))
    out = []
    for i in range(n):
        start = draw(st.integers(min_value=0, max_value=300))
        length = draw(st.integers(min_value=1, max_value=60))
        out.append(_segment(i, start, length))
    return out


class TestFixedSeamsOrchestration:
    """FakeDB orchestration for the fixed seams (task 3.7).

    Exercises the fixed get_path_feed gate + terminal wiring, the
    _process_single_topic record/clear of the attempt budget, and the
    _node_segment base-clip fallback. The in-process attempt tracker and the
    extension/expansion throttles are cleared in setup (mirroring
    TestShouldExpandTopic clearing te._expanding). ASCII only.
    """

    def setup_method(self):
        topics_api.generating_slugs.clear()
        _shs._self_heal_attempts.clear()
        path_extension._extending_sessions.clear()
        topic_expansion._expanding.clear()

    # --- get_path_feed: empty topic across polls -> cap -> terminal --------
    def test_empty_topic_across_polls_reaches_cap_then_terminal(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        cap = _shs.MAX_SELF_HEAL_ATTEMPTS
        polls = cap * 3  # well past the budget
        triggers = 0
        last_feeds = None
        for _ in range(polls):
            bg = BackgroundTasks()
            last_feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
            queued = _self_heal_tasks(bg)
            if queued:
                triggers += len(queued)
                # Simulate the background attempt finishing with 0 clips: record
                # the failed attempt (backdated past the cooldown) and let the
                # slug fall out of generating_slugs as the real finally would.
                _record_failed_attempt(slug, backdate=True)
            topics_api.generating_slugs.discard(slug)

        # Bounded: generation is triggered exactly MAX_SELF_HEAL_ATTEMPTS times
        # across many polls, never once per poll.
        assert triggers == cap, (
            f"empty topic triggered {triggers} times across {polls} polls "
            f"(expected {cap})"
        )

        # Out of budget: the topic is terminal - spinner can stop.
        fr = next(f for f in last_feeds if f.topic_slug == slug)
        assert fr.processing is False
        assert fr.failed is True

        # And a further poll queues no more self-heal tasks.
        bg = BackgroundTasks()
        asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        assert _self_heal_tasks(bg) == []

    # --- get_path_feed: populated + generating topics unaffected -----------
    def test_populated_and_generating_unaffected_when_empty_exhausted(self, monkeypatch):
        empty, pop, gen = "empty-t", "pop-t", "gen-t"
        store = {
            "learning_paths": [
                {
                    "session_id": "s1",
                    "topic_slugs": [empty, pop, gen],
                    "user_query": "teach me",
                    "user_id": None,
                }
            ],
            "topics": [
                {"slug": empty, "name": "Empty"},
                {"slug": pop, "name": "Pop"},
                {"slug": gen, "name": "Gen"},
            ],
            "clips": [_clip_row(pop, "p1"), _clip_row(pop, "p2")],
            "clip_events": [],
            "session_embeddings": [],
        }
        db = FakeDB(store=store)
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # Drive the empty topic out of its retry budget.
        for _ in range(_shs.MAX_SELF_HEAL_ATTEMPTS):
            _record_failed_attempt(empty, backdate=True)
        # Mark the third topic as actively generating.
        topics_api.generating_slugs.add(gen)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        by = {f.topic_slug: f for f in feeds}

        # Empty topic: terminal.
        assert by[empty].processing is False
        assert by[empty].failed is True

        # Populated topic: unchanged - its clips, not processing, not failed.
        assert {c.id for c in by[pop].clips} == {"p1", "p2"}
        assert by[pop].processing is False
        assert by[pop].failed is False

        # Generating topic: unchanged - processing, not failed.
        assert by[gen].processing is True
        assert by[gen].failed is False

        # No self-heal task is queued: empty is exhausted, generating de-dupes,
        # populated has clips.
        assert _self_heal_tasks(bg) == []

    # --- extension independence: terminal-failed does NOT block extension --
    def test_terminal_failed_topic_does_not_block_auto_extension(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # Exhaust the budget so the only topic is terminal (processing=False).
        for _ in range(_shs.MAX_SELF_HEAL_ATTEMPTS):
            _record_failed_attempt(slug, backdate=True)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        fr = next(f for f in feeds if f.topic_slug == slug)
        assert fr.processing is False
        assert fr.failed is True

        # Auto-extension fires independent of the self-heal budget: a terminal
        # topic reports processing=False, so still_processing is False and the
        # low-clips extension path is reached and queued.
        assert _extend_tasks(bg), "terminal-failed topic should not block auto-extension"

    # --- contrast: an in-budget empty topic still blocks extension ---------
    def test_in_budget_empty_topic_blocks_auto_extension(self, monkeypatch):
        slug = "empty-topic"
        db = FakeDB(store=_empty_topic_store(slug))
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        bg = BackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="u"))
        fr = next(f for f in feeds if f.topic_slug == slug)

        # In budget: still processing, so extension is blocked but self-heal
        # fires exactly once - both behaviors unchanged by the fix.
        assert fr.processing is True
        assert _extend_tasks(bg) == []
        assert len(_self_heal_tasks(bg)) == 1

    # --- expansion independence: engaged topic expands while empty exhausted
    def test_expansion_fires_independent_of_self_heal_budget(self, monkeypatch):
        engaged, empty = "engaged-t", "empty-t"
        store = {
            "learning_paths": [
                {
                    "session_id": "s1",
                    "topic_slugs": [engaged, empty],
                    "user_query": "teach me",
                    "user_id": None,
                }
            ],
            "topics": [
                {"slug": engaged, "name": "Engaged"},
                {"slug": empty, "name": "Empty"},
            ],
            "clips": [_clip_row(engaged, "e1"), _clip_row(engaged, "e2")],
            # One completed event makes the viewer "engaged" on this topic and
            # marks e1 as seen, leaving exactly one unseen clip (low on clips).
            "clip_events": [
                {"clip_id": "e1", "session_id": "s1", "watch_ms": 60000, "completed": True}
            ],
            "session_embeddings": [],
        }
        db = FakeDB(store=store)
        monkeypatch.setattr(feed_api, "get_client", lambda: db)

        # Exhaust the OTHER topic's self-heal budget.
        for _ in range(_shs.MAX_SELF_HEAL_ATTEMPTS):
            _record_failed_attempt(empty, backdate=True)

        bg = BackgroundTasks()
        asyncio.run(get_path_feed("s1", bg, caller_id="u"))

        # Expansion is queued for the engaged topic even though the empty topic
        # is out of self-heal budget: expansion is independent of that budget.
        assert engaged in [t.args[0] for t in _expand_tasks(bg)]
        # And the exhausted empty topic is NOT self-healed.
        assert empty not in [t.args[0] for t in _self_heal_tasks(bg)]

    # --- _process_single_topic: 0-clip run records an attempt -------------
    def test_process_single_topic_zero_clip_run_records_attempt(self, monkeypatch):
        slug = "run-empty"
        # No sections -> the else branch runs run_pipeline(slug, name) only, so
        # the story/quiz best-effort calls (inside the sections branch) are
        # skipped entirely under FakeDB.
        monkeypatch.setattr(section_planner, "plan_and_store_sections", lambda *a, **k: [])
        monkeypatch.setattr(pipeline_agent, "run_pipeline", lambda *a, **k: 0)

        asyncio.run(topics_api._process_single_topic(slug, "Run Empty"))

        attempts, _age = _shs.read(slug)
        assert attempts >= 1
        # The slug is no longer marked generating.
        assert slug not in topics_api.generating_slugs

    # --- _process_single_topic: >=1-clip run clears tracking --------------
    def test_process_single_topic_successful_run_clears_tracking(self, monkeypatch):
        slug = "run-ok"
        # Seed a prior failed attempt so "clearing" is observable.
        _shs.record_attempt(slug)
        assert _shs.read(slug)[0] == 1

        monkeypatch.setattr(section_planner, "plan_and_store_sections", lambda *a, **k: [])
        monkeypatch.setattr(pipeline_agent, "run_pipeline", lambda *a, **k: 2)

        asyncio.run(topics_api._process_single_topic(slug, "Run Ok"))

        assert _shs.read(slug) == (0, None)
        assert slug not in topics_api.generating_slugs

    # --- _node_segment property: max(len(segments), 1) clips --------------
    @settings(max_examples=50, deadline=None)
    @given(segments=_segment_lists())
    def test_node_segment_yields_max_len_segments_or_one_clip(self, segments):
        """Validates: Requirements 2.5, 3.5

        For a single transcript-bearing video and any segmentation output, the
        fixed _node_segment yields exactly len(segments) clips when segmentation
        is non-empty, and falls back to one base clip when it is empty -
        i.e. max(len(segments), 1) clips, never zero.
        """
        video = {
            "video_id": "vid1",
            "title": "Base Video Title",
            "description": "a description",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "duration_seconds": 300,
            "transcript": "some transcript text",
        }
        state = {
            "topic_slug": "binary-search",
            "topic_name": "Binary Search",
            "search_query": None,
            "section_index": None,
            "section_title": None,
            "section_description": None,
            "arc_titles": [],
            "clear_existing": True,
            "videos": [video],
            "clips": [],
            "stored_count": 0,
            "errors": [],
        }
        with patch("app.services.pipeline._identify_segments", return_value=segments), \
                patch("app.services.embeddings.embed_texts",
                      side_effect=lambda texts: [None] * len(texts)):
            result = pipeline_agent._node_segment(state)

        assert len(result["clips"]) == max(len(segments), 1)
