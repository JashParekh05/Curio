"""Property-based tests for the pure Key_Pool quota-accounting core.

# Feature: cold-start-content-library, Property 1: Quota bounds invariant

For any project with ``used`` in ``[0, DAILY_QUOTA]`` and any affordable charge,
``remaining(p) == DAILY_QUOTA - p.used`` and ``0 <= p.used <= DAILY_QUOTA``
continue to hold after charging.

Validates: Requirements 1.1, 1.2
"""
from hypothesis import given, settings, strategies as st

from app.services.quota_pool import (
    DAILY_QUOTA,
    ProjectQuota,
    charge,
    remaining,
)


@st.composite
def _project(draw):
    """Generate a ProjectQuota whose used respects the 0..DAILY_QUOTA invariant."""
    used = draw(st.integers(min_value=0, max_value=DAILY_QUOTA))
    project_id = draw(st.text(min_size=1, max_size=12))
    return ProjectQuota(project_id=project_id, used=used)


class TestQuotaBoundsInvariant:
    # Feature: cold-start-content-library, Property 1: Quota bounds invariant
    @settings(max_examples=100)
    @given(p=_project(), cost=st.integers(min_value=-50, max_value=DAILY_QUOTA + 50))
    def test_quota_bounds_invariant(self, p, cost):
        # --- Invariant holds for the starting state. ---
        assert 0 <= p.used <= DAILY_QUOTA
        assert remaining(p) == DAILY_QUOTA - p.used

        # remaining is never negative, regardless of state.
        assert remaining(p) >= 0

        # --- Charging preserves the bounds invariant. ---
        result = charge(p, cost)

        # used stays within [0, DAILY_QUOTA] after any charge attempt.
        assert 0 <= result.used <= DAILY_QUOTA
        # remaining stays consistent with used and never negative.
        assert remaining(result) == DAILY_QUOTA - result.used
        assert remaining(result) >= 0

        # An affordable charge (valid, fits in remaining budget) advances used by
        # exactly cost while keeping it within bounds.
        if 0 < cost <= remaining(p):
            assert result.used == p.used + cost
            assert result.used <= DAILY_QUOTA


# Feature: cold-start-content-library, Property 2: Affordability correctness and purity
#
# For any project p and any integer cost N, can_afford(p, N) returns True if and
# only if N > 0 and remaining(p) >= N (no cost split across projects), and the
# call never mutates p.
#
# Validates: Requirements 1.3, 1.4, 1.5
from dataclasses import replace as _dc_replace

from app.services.quota_pool import can_afford


class TestAffordabilityCorrectnessAndPurity:
    # Feature: cold-start-content-library, Property 2: Affordability correctness and purity
    @settings(max_examples=100)
    @given(
        p=_project(),
        cost=st.integers(min_value=-50, max_value=DAILY_QUOTA + 50),
    )
    def test_affordability_correctness_and_purity(self, p, cost):
        # Capture an independent snapshot of the input to detect any mutation.
        before = _dc_replace(p)

        result = can_afford(p, cost)

        # --- Correctness: True iff cost is valid AND fits this single project. ---
        expected = cost > 0 and remaining(p) >= cost
        assert result is expected
        assert isinstance(result, bool)

        # Non-positive costs are always rejected (Req 1.3 invalid-cost rejection).
        if cost <= 0:
            assert result is False

        # --- Purity: the call never mutates the input project. ---
        assert p == before
        assert p.project_id == before.project_id
        assert p.used == before.used


# Feature: cold-start-content-library, Property 3: Charge correctness and purity
#
# For any project p and cost N, charge(p, N) with N > 0 and affordable returns a
# new project whose used is exactly p.used + N (instances: 100 for search, 1 for
# metadata), leaves the input p (and any other projects) unchanged, and for
# N <= 0 or an unaffordable N returns p unchanged.
#
# Validates: Requirements 1.5, 1.6, 1.7, 1.8
from app.services.quota_pool import METADATA_COST, SEARCH_COST


class TestChargeCorrectnessAndPurity:
    # Feature: cold-start-content-library, Property 3: Charge correctness and purity
    @settings(max_examples=100)
    @given(
        p=_project(),
        others=st.lists(_project(), max_size=4),
        cost=st.integers(min_value=-50, max_value=DAILY_QUOTA + 50),
    )
    def test_charge_correctness_and_purity(self, p, others, cost):
        # Independent snapshots to detect any mutation of inputs.
        before = _dc_replace(p)
        others_before = [_dc_replace(o) for o in others]

        result = charge(p, cost)

        # --- Correctness. ---
        if 0 < cost <= remaining(p):
            # Affordable, valid charge: used advances by exactly cost.
            assert result is not p or cost == 0  # a new value is produced
            assert result.used == p.used + cost
            assert result.project_id == p.project_id
            assert 0 <= result.used <= DAILY_QUOTA
        else:
            # cost <= 0 or unaffordable: input is returned unchanged.
            assert result == p
            assert result.used == p.used

        # --- Purity: the input project is never mutated. ---
        assert p == before
        assert p.project_id == before.project_id
        assert p.used == before.used

        # No other project is touched by a single-project charge.
        assert others == others_before

    # Feature: cold-start-content-library, Property 3: Charge correctness and purity
    @settings(max_examples=100)
    @given(used=st.integers(min_value=0, max_value=DAILY_QUOTA))
    def test_charge_search_and_metadata_cost_instances(self, used):
        # Concrete cost instances called out by the spec: a search costs 100
        # units, a metadata (videos.list) call costs 1 unit.
        p = ProjectQuota(project_id="proj", used=used)

        for cost in (SEARCH_COST, METADATA_COST):
            charged = charge(p, cost)
            if cost <= remaining(p):
                assert charged.used == p.used + cost
            else:
                assert charged == p
            # Input is never mutated regardless of affordability.
            assert p.used == used


# Feature: cold-start-content-library, Property 4: Selection correctness and determinism
#
# For any list of projects and any cost N, select_project returns the lowest
# project_id (ascending) among projects with remaining >= N, returns None when
# N <= 0 or no project is affordable, and never mutates any project.
#
# Validates: Requirements 1.5, 1.9, 1.11, 1.12
from app.services.quota_pool import select_project


class TestSelectionCorrectnessAndDeterminism:
    # Feature: cold-start-content-library, Property 4: Selection correctness and determinism
    @settings(max_examples=100)
    @given(
        # Key_Pool project_ids are unique per project, so the generated list
        # must not contain duplicate ids; otherwise the id-based re-derivation
        # below (min(affordable) then first match by id) could pick a different
        # project than select_project did.
        projects=st.lists(_project(), max_size=6, unique_by=lambda p: p.project_id),
        cost=st.integers(min_value=-50, max_value=DAILY_QUOTA + 50),
    )
    def test_selection_correctness_and_determinism(self, projects, cost):
        # Snapshot inputs to detect mutation.
        before = [_dc_replace(p) for p in projects]

        result = select_project(projects, cost)

        # The set of affordable project ids, computed independently.
        affordable = [p.project_id for p in projects if 0 < cost and remaining(p) >= cost]

        if cost <= 0 or not affordable:
            # No affordable project (or invalid cost) -> None.
            assert result is None
        else:
            # Returns the lexicographically smallest affordable project id.
            assert result == min(affordable)
            # The chosen project really can afford the cost.
            chosen = next(p for p in projects if p.project_id == result)
            assert can_afford(chosen, cost)

        # Determinism: identical input yields identical output.
        assert select_project(projects, cost) == result

        # Purity: no project is mutated.
        assert projects == before


# Feature: cold-start-content-library, Property 5: Failover correctness and purity
#
# For any list of projects, any cost N, and any set of already-tried project ids,
# failover_select returns the lowest affordable project_id not in the tried set
# (or None), and never mutates any project.
#
# Validates: Requirements 1.10, 1.12
from app.services.quota_pool import failover_select


class TestFailoverCorrectnessAndPurity:
    # Feature: cold-start-content-library, Property 5: Failover correctness and purity
    @settings(max_examples=100)
    @given(
        # Key_Pool project_ids are unique per project, so the generated list
        # must not contain duplicate ids; otherwise the id-based re-derivation
        # below (min(candidates) then first match by id) could pick a different
        # project than failover_select did.
        projects=st.lists(_project(), max_size=6, unique_by=lambda p: p.project_id),
        cost=st.integers(min_value=-50, max_value=DAILY_QUOTA + 50),
        tried=st.frozensets(st.text(min_size=1, max_size=12), max_size=6),
    )
    def test_failover_correctness_and_purity(self, projects, cost, tried):
        before = [_dc_replace(p) for p in projects]

        result = failover_select(projects, cost, tried)

        # Affordable project ids that have NOT yet been tried.
        candidates = [
            p.project_id
            for p in projects
            if p.project_id not in tried and 0 < cost and remaining(p) >= cost
        ]

        if cost <= 0 or not candidates:
            assert result is None
        else:
            # Lowest untried affordable project id.
            assert result == min(candidates)
            assert result not in tried
            chosen = next(p for p in projects if p.project_id == result)
            assert can_afford(chosen, cost)

        # Determinism.
        assert failover_select(projects, cost, tried) == result

        # Purity: inputs untouched.
        assert projects == before


# Feature: cold-start-content-library, Property 6: Rollover reset and 24-hour window
#
# For any list of projects, rollover yields a list in which every project's used
# is 0 and remaining is DAILY_QUOTA; and quota_window_date is constant for all
# timestamps within a single 24-hour Pacific window and strictly increments at
# the boundary.
#
# Validates: Requirements 1.13, 6.5
from datetime import date, datetime as _dt, time as _time, timedelta as _td
from zoneinfo import ZoneInfo as _ZoneInfo

from app.services.quota_pool import quota_window_date, rollover

_PACIFIC_TZ = _ZoneInfo("America/Los_Angeles")


class TestRolloverResetAnd24HourWindow:
    # Feature: cold-start-content-library, Property 6: Rollover reset and 24-hour window
    @settings(max_examples=100)
    @given(projects=st.lists(_project(), max_size=6))
    def test_rollover_resets_all_projects(self, projects):
        before = [_dc_replace(p) for p in projects]

        result = rollover(projects)

        # Every project's used is reset to 0 and remaining to the full budget.
        assert len(result) == len(projects)
        for new_p, old_p in zip(result, projects):
            assert new_p.used == 0
            assert remaining(new_p) == DAILY_QUOTA
            # Project identity is preserved by the reset.
            assert new_p.project_id == old_p.project_id

        # Purity: the input list/projects are not mutated.
        assert projects == before

    # Feature: cold-start-content-library, Property 6: Rollover reset and 24-hour window
    @settings(max_examples=100)
    @given(
        d=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)),
        # Two distinct seconds-of-day, kept away from the 0/23:00 hours to avoid
        # DST gap/fold edge minutes; any two instants on the same Pacific
        # calendar day must map to that day.
        secs_a=st.integers(min_value=3600, max_value=82799),
        secs_b=st.integers(min_value=3600, max_value=82799),
    )
    def test_window_constant_within_pacific_day(self, d, secs_a, secs_b):
        midnight = _dt.combine(d, _time(0, 0), tzinfo=_PACIFIC_TZ)
        ts_a = midnight + _td(seconds=secs_a)
        ts_b = midnight + _td(seconds=secs_b)

        # All instants within the same Pacific calendar day share one window key.
        assert quota_window_date(ts_a) == d
        assert quota_window_date(ts_b) == d
        assert quota_window_date(ts_a) == quota_window_date(ts_b)

    # Feature: cold-start-content-library, Property 6: Rollover reset and 24-hour window
    @settings(max_examples=100)
    @given(d=st.dates(min_value=date(2000, 1, 2), max_value=date(2100, 12, 30)))
    def test_window_increments_at_pacific_boundary(self, d):
        midnight = _dt.combine(d, _time(0, 0), tzinfo=_PACIFIC_TZ)

        # Midnight Pacific belongs to that day's window.
        assert quota_window_date(midnight) == d
        # The instant just before midnight belongs to the previous day's window,
        # so the window key strictly increments at the boundary.
        assert quota_window_date(midnight - _td(seconds=1)) == d - _td(days=1)


# Feature: cold-start-content-library, Property 7: Budget additivity
#
# For any list of projects, total_daily_budget == DAILY_QUOTA * len(projects),
# and appending one additional project increases the total by exactly
# DAILY_QUOTA.
#
# Validates: Requirements 1.14
from app.services.quota_pool import total_daily_budget


class TestBudgetAdditivity:
    # Feature: cold-start-content-library, Property 7: Budget additivity
    @settings(max_examples=100)
    @given(
        projects=st.lists(_project(), max_size=8),
        extra=_project(),
    )
    def test_budget_additivity(self, projects, extra):
        total = total_daily_budget(projects)

        # Total budget is exactly DAILY_QUOTA per project, independent of usage.
        assert total == DAILY_QUOTA * len(projects)

        # Appending one more project adds exactly DAILY_QUOTA.
        grown = total_daily_budget(projects + [extra])
        assert grown == total + DAILY_QUOTA
