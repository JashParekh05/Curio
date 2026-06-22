"""Integration tests for the Seeding_Worker run loop (Task 12.3).

Exercises ``scripts.seeding_worker.run_once`` end to end against in-memory
fakes for the quota pool, the backlog store, and the generation step, while
leaving the *pure* pacing/selection cores (``can_process_next``,
``estimate_item_cost``, ``select_project``, ``charge``, ``remaining``,
``select_next``, ``apply_seed_outcome``) real. The worker is a thin I/O shell,
so wiring those real cores against fake I/O is exactly the integration surface
worth testing. Each test asserts the returned summary contract
``{processed, skipped, charged_units, stopped_reason}`` plus the I/O side
effects (which topics were generated, persisted done, or spawned from).

Patched in the ``seeding_worker`` module namespace (where the names actually
resolve): ``quota_store.load_today``, ``backlog_store.load_pending`` /
``persist_status`` / ``spawn_adjacent_for``, and the worker's own
``_topic_has_clips`` / ``_generate`` helpers.

Scenarios (per the task):
  - a run never exceeds the affordable budget and stops at the first
    unaffordable Backlog_Item (no later item is charged);
  - resume after interruption skips topics that already have clips with zero
    quota and no regeneration;
  - a per-item failure (zero clips or a raised error) leaves the item pending
    and the loop continues with the next item;
  - an empty backlog exits cleanly without raising.

Run from the backend/ dir: ``.venv/bin/python -m pytest tests/test_seeding_worker.py``.

Validates: Requirements 2.7, 2.10, 2.11, 6.7, 6.8
"""
import pytest

import scripts.seeding_worker as sw
from app.services.quota_pool import DAILY_QUOTA, ProjectQuota, remaining
from app.services.topic_frontier import BacklogItem

# estimate_item_cost(_DEFAULT_SECTION_COUNT=1, _DEFAULT_CACHED_QUERIES=0)
#   = (1 - 0) * (SEARCH_COST + METADATA_COST) = 1 * (100 + 1) = 101
EST_COST = 101

# Summary stopped_reason contract values (mirrors the worker's constants).
_EMPTY = "backlog_empty"
_CAP = "per_run_cap"
_BUDGET = "budget_exhausted"


def _item(topic: str, priority: float = 0.5, *, level: str = "intermediate",
          status: str = "pending") -> BacklogItem:
    return BacklogItem(topic=topic, level=level, priority=priority, status=status)


class _Recorder:
    """Captures the worker's best-effort I/O side effects for assertions."""

    def __init__(self):
        self.persisted: list[tuple[str, str]] = []  # (topic, status)
        self.spawned: list[str] = []                # seeded topics grown from
        self.generated: list[str] = []              # topics generation attempted


def _wire(monkeypatch, *, projects, pending, has_clips=(), results=None):
    """Point run_once at controllable in-memory fakes; return the recorder.

    ``results`` maps topic -> generation outcome: an int clip count (>= 1 is a
    success, 0 is a no-clip failure) or an ``Exception`` instance to raise.
    Topics absent from the map default to a single-clip success.
    """
    rec = _Recorder()
    has_clips_set = set(has_clips)
    results = results or {}

    monkeypatch.setattr(sw.quota_store, "load_today", lambda now_utc=None: list(projects))
    monkeypatch.setattr(sw.backlog_store, "init_from_grade_map", lambda: None)
    monkeypatch.setattr(sw.backlog_store, "load_pending", lambda: list(pending))
    monkeypatch.setattr(
        sw.backlog_store, "persist_status",
        lambda topic, status: rec.persisted.append((topic, status)),
    )

    def _spawn(topic):
        rec.spawned.append(topic)
        return []

    monkeypatch.setattr(sw.backlog_store, "spawn_adjacent_for", _spawn)
    monkeypatch.setattr(sw, "_topic_has_clips", lambda topic: topic in has_clips_set)

    def _generate(item):
        rec.generated.append(item.topic)
        outcome = results.get(item.topic, 1)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(sw, "_generate", _generate)
    return rec


# --------------------------------------------------------------------------
# Budget pacing: never overspend; stop at the first unaffordable item
# --------------------------------------------------------------------------

class TestNeverOverspends:
    def test_stops_at_first_unaffordable_and_never_exceeds_budget(self, monkeypatch):
        # One project with exactly 250 affordable units. At 101 units/item the
        # run can afford two items (202) and must stop before a third (303).
        used = DAILY_QUOTA - 250
        projects = [ProjectQuota(project_id="projA", used=used)]
        affordable = sum(remaining(p) for p in projects)
        assert affordable == 250

        # Five pending items in clear priority order so selection is deterministic.
        pending = [
            _item("a", 0.9), _item("b", 0.8), _item("c", 0.7),
            _item("d", 0.6), _item("e", 0.5),
        ]
        rec = _wire(monkeypatch, projects=projects, pending=pending)

        summary = sw.run_once()

        assert summary["stopped_reason"] == _BUDGET
        assert summary["processed"] == 2
        assert summary["skipped"] == 0
        # Charged exactly two items and never more than the affordable budget.
        assert summary["charged_units"] == 2 * EST_COST
        assert summary["charged_units"] <= affordable
        # The two highest-priority items were the ones processed; later items
        # were never charged (no spend past the first unaffordable item).
        assert rec.generated == ["a", "b"]
        assert rec.persisted == [("a", "done"), ("b", "done")]
        assert "c" not in rec.generated and "d" not in rec.generated

    def test_single_affordable_item_then_budget_exhausted(self, monkeypatch):
        # Only 150 units: affords exactly one item, then stops on the next.
        projects = [ProjectQuota(project_id="projA", used=DAILY_QUOTA - 150)]
        pending = [_item("a", 0.9), _item("b", 0.8)]
        rec = _wire(monkeypatch, projects=projects, pending=pending)

        summary = sw.run_once()

        assert summary["processed"] == 1
        assert summary["charged_units"] == EST_COST
        assert summary["stopped_reason"] == _BUDGET
        assert rec.generated == ["a"]

    def test_exhausted_pool_processes_nothing(self, monkeypatch):
        # A fully-used project affords nothing; the very first item stops the run.
        projects = [ProjectQuota(project_id="projA", used=DAILY_QUOTA)]
        pending = [_item("a", 0.9)]
        rec = _wire(monkeypatch, projects=projects, pending=pending)

        summary = sw.run_once()

        assert summary == {
            "processed": 0,
            "skipped": 0,
            "charged_units": 0,
            "stopped_reason": _BUDGET,
        }
        assert rec.generated == []
        assert rec.persisted == []


# --------------------------------------------------------------------------
# Resume after interruption: skip topics that already have clips
# --------------------------------------------------------------------------

class TestResumeSkipsAlreadySeeded:
    def test_skips_topics_with_existing_clips_with_zero_quota(self, monkeypatch):
        # Ample budget so nothing is budget-limited; "a" and "c" already seeded.
        projects = [ProjectQuota(project_id="projA", used=0)]
        pending = [_item("a", 0.9), _item("b", 0.8), _item("c", 0.7)]
        rec = _wire(
            monkeypatch,
            projects=projects,
            pending=pending,
            has_clips=("a", "c"),
        )

        summary = sw.run_once()

        assert summary["stopped_reason"] == _EMPTY
        assert summary["processed"] == 1
        assert summary["skipped"] == 2
        # Skips consume no quota; only the un-seeded "b" is charged.
        assert summary["charged_units"] == EST_COST
        # Already-seeded topics are never regenerated...
        assert rec.generated == ["b"]
        # ...but are marked done so they are not reconsidered on the next run.
        assert ("a", "done") in rec.persisted
        assert ("c", "done") in rec.persisted
        assert ("b", "done") in rec.persisted
        # Only the freshly seeded topic grows the frontier.
        assert rec.spawned == ["b"]


# --------------------------------------------------------------------------
# Per-item failure: leave the item pending and continue the loop
# --------------------------------------------------------------------------

class TestPerItemFailureContinues:
    def test_zero_clip_and_raised_failures_leave_items_pending(self, monkeypatch):
        projects = [ProjectQuota(project_id="projA", used=0)]
        pending = [_item("a", 0.9), _item("b", 0.8), _item("c", 0.7)]
        # "a" produces no clips, "b" raises mid-generation, "c" succeeds.
        rec = _wire(
            monkeypatch,
            projects=projects,
            pending=pending,
            results={"a": 0, "b": RuntimeError("boom"), "c": 2},
        )

        summary = sw.run_once()  # must not raise

        assert summary["stopped_reason"] == _EMPTY
        assert summary["processed"] == 1
        assert summary["skipped"] == 0
        # Only the successful topic is charged.
        assert summary["charged_units"] == EST_COST
        # Every item was attempted; the loop continued past both failures.
        assert rec.generated == ["a", "b", "c"]
        # Failed items are left pending (never persisted done) for a later retry.
        assert ("a", "done") not in rec.persisted
        assert ("b", "done") not in rec.persisted
        # The successful item is marked done and grows the frontier.
        assert rec.persisted == [("c", "done")]
        assert rec.spawned == ["c"]

    def test_failure_does_not_halt_a_subsequent_affordable_item(self, monkeypatch):
        # A failure early in the run must not consume budget or stop the run.
        projects = [ProjectQuota(project_id="projA", used=0)]
        pending = [_item("fails", 0.9), _item("ok", 0.5)]
        rec = _wire(
            monkeypatch,
            projects=projects,
            pending=pending,
            results={"fails": 0, "ok": 1},
        )

        summary = sw.run_once()

        assert summary["processed"] == 1
        assert summary["charged_units"] == EST_COST
        assert rec.persisted == [("ok", "done")]


# --------------------------------------------------------------------------
# Empty backlog: exit cleanly without raising
# --------------------------------------------------------------------------

class TestEmptyBacklog:
    def test_empty_backlog_exits_cleanly(self, monkeypatch):
        projects = [ProjectQuota(project_id="projA", used=0)]
        rec = _wire(monkeypatch, projects=projects, pending=[])

        summary = sw.run_once()  # must not raise

        assert summary == {
            "processed": 0,
            "skipped": 0,
            "charged_units": 0,
            "stopped_reason": _EMPTY,
        }
        assert rec.generated == []
        assert rec.persisted == []
        assert rec.spawned == []

    def test_all_done_backlog_exits_cleanly(self, monkeypatch):
        # A backlog of only done items has nothing pending to select.
        projects = [ProjectQuota(project_id="projA", used=0)]
        pending = [_item("a", 0.9, status="done"), _item("b", 0.8, status="done")]
        rec = _wire(monkeypatch, projects=projects, pending=pending)

        summary = sw.run_once()

        assert summary["processed"] == 0
        assert summary["stopped_reason"] == _EMPTY
        assert rec.generated == []


# --------------------------------------------------------------------------
# Per-run cap: stop distinctly from an exhausted budget
# --------------------------------------------------------------------------

class TestPerRunCap:
    def test_per_run_cap_caps_processed_items(self, monkeypatch):
        # Ample budget, but a cap of 2 stops the run after two items.
        projects = [ProjectQuota(project_id="projA", used=0)]
        pending = [_item("a", 0.9), _item("b", 0.8), _item("c", 0.7)]
        rec = _wire(monkeypatch, projects=projects, pending=pending)

        summary = sw.run_once(per_run_cap=2)

        assert summary["processed"] == 2
        assert summary["stopped_reason"] == _CAP
        assert rec.generated == ["a", "b"]
        assert "c" not in rec.generated
