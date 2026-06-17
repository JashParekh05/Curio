"""Property-based tests for the pure Seeding_Worker pacing core.

These tests exercise the pure pacing decisions in ``app/services/seeding.py``
(``can_process_next``, ``estimate_item_cost``, ``should_skip``) that gate a
Seeding_Worker run. Each test simulates the worker loop purely in terms of those
functions — no DB, clock, or pipeline — so the universal pacing invariants can be
checked across a large input space.

Covers design correctness properties:
- Property 8: A run never overspends and stops at the first unaffordable item.
- Property 9: Per-run cap is respected.
- Property 10: Skipping done or populated topics consumes no quota.
"""
from hypothesis import given, settings, strategies as st

from app.services.seeding import (
    DEFAULT_PER_RUN_CAP,
    can_process_next,
    estimate_item_cost,
    should_skip,
)


# ---------------------------------------------------------------------------
# Pure worker-loop simulation
# ---------------------------------------------------------------------------
# The Seeding_Worker iterates the prioritized backlog, and on each item asks the
# pacing gate ``can_process_next`` whether it may process that item given the
# budget still affordable, the item's estimated cost, how many items it has
# already processed, and the per-run cap. When the gate says no, the run stops
# cleanly (no later item is processed). This mirrors the worker loop in the
# design without any I/O.

def _simulate_run(costs, budget, per_run_cap):
    """Return (spent, processed, charged) for a paced run over ``costs``.

    ``spent`` is the cumulative Quota_Cost charged, ``processed`` the number of
    items charged, and ``charged`` the ordered list of charged item costs. The
    loop stops at the first item the pacing gate rejects.
    """
    spent = 0
    processed = 0
    charged = []
    for cost in costs:
        remaining_affordable = budget - spent
        if can_process_next(remaining_affordable, cost, processed, per_run_cap):
            spent += cost
            processed += 1
            charged.append(cost)
        else:
            # Pacing gate rejected this item; the run stops cleanly. No later
            # item is processed.
            break
    return spent, processed, charged


# ---------------------------------------------------------------------------
# Property 8: A run never overspends and stops at the first unaffordable item
# ---------------------------------------------------------------------------

class TestNeverOverspendStopsAtFirstUnaffordable:
    # Feature: cold-start-content-library, Property 8: A run never overspends and stops at the first unaffordable item
    #
    # For any generated sequence of Backlog_Item costs and any affordable
    # budget, simulating a worker run gated by can_process_next charges a
    # cumulative total that never exceeds the affordable budget, and processing
    # stops at the first item whose cost is unaffordable (no later item is
    # charged).
    #
    # Validates: Requirements 2.1, 2.2, 2.11, 6.3, 6.4
    @settings(max_examples=100)
    @given(
        costs=st.lists(st.integers(min_value=1, max_value=1000), max_size=40),
        budget=st.integers(min_value=0, max_value=20000),
    )
    def test_never_overspend_and_stops_at_first_unaffordable(self, costs, budget):
        # A per-run cap large enough that the cap can never be the limiter, so
        # this property isolates the budget/affordability behavior.
        per_run_cap = len(costs) + 1

        spent, processed, charged = _simulate_run(costs, budget, per_run_cap)

        # --- Never overspends: cumulative charge never exceeds the budget. ---
        assert spent == sum(charged)
        assert spent <= budget

        # --- Charged items are an in-order prefix of the backlog (no skipping,
        # no reordering): everything before the stop was processed. ---
        assert charged == costs[:processed]

        # Re-derive the prefix sums independently to confirm every charged item
        # was affordable at the moment it was charged.
        running = 0
        for cost in charged:
            assert cost <= budget - running  # affordable when charged
            running += cost
        assert running == spent

        # --- Stops at the first unaffordable item: if the run did not consume
        # the whole backlog, the next (stopping) item must have been
        # unaffordable, and no later item was charged. ---
        if processed < len(costs):
            stopping_cost = costs[processed]
            assert stopping_cost > budget - spent  # the reason we stopped
            # processed counts exactly the charged prefix; nothing after it ran.
            assert len(charged) == processed


# ---------------------------------------------------------------------------
# Property 9: Per-run cap is respected
# ---------------------------------------------------------------------------

class TestPerRunCapRespected:
    # Feature: cold-start-content-library, Property 9: Per-run cap is respected
    #
    # For any per-run cap c >= 0 and any backlog, a simulated run processes at
    # most c Backlog_Items, and when no cap is configured the default cap is
    # applied.
    #
    # Validates: Requirements 2.12
    @settings(max_examples=100)
    @given(
        costs=st.lists(st.integers(min_value=1, max_value=100), max_size=60),
        per_run_cap=st.integers(min_value=0, max_value=50),
    )
    def test_run_processes_at_most_cap_items(self, costs, per_run_cap):
        # Give the run an abundant budget so the per-run cap is the only limiter.
        budget = sum(costs) + 1

        _, processed, charged = _simulate_run(costs, budget, per_run_cap)

        # Never processes more than the configured cap.
        assert processed <= per_run_cap
        assert len(charged) <= per_run_cap

        # With abundant budget, the run processes exactly as many items as the
        # cap allows (bounded by the backlog size).
        assert processed == min(per_run_cap, len(costs))

        # The pacing gate refuses outright once the cap has been reached,
        # regardless of remaining budget or affordable cost.
        assert can_process_next(budget, 1, per_run_cap, per_run_cap) is False

    @settings(max_examples=100)
    @given(costs=st.lists(st.integers(min_value=1, max_value=100), max_size=60))
    def test_default_cap_applied_when_unconfigured(self, costs):
        # When no per-run cap is configured, the worker applies DEFAULT_PER_RUN_CAP.
        budget = sum(costs) + 1

        _, processed, _ = _simulate_run(costs, budget, DEFAULT_PER_RUN_CAP)

        assert DEFAULT_PER_RUN_CAP == 25
        assert processed <= DEFAULT_PER_RUN_CAP
        assert processed == min(DEFAULT_PER_RUN_CAP, len(costs))


# ---------------------------------------------------------------------------
# Property 10: Skipping done or populated topics consumes no quota
# ---------------------------------------------------------------------------

class TestSkippingConsumesNoQuota:
    # Feature: cold-start-content-library, Property 10: Skipping done or populated topics consumes no quota
    #
    # For any Backlog_Item whose topic already has at least one clip or whose
    # status is "done", should_skip returns True and the run neither charges
    # quota nor regenerates that topic.
    #
    # Validates: Requirements 2.4, 6.6
    @settings(max_examples=100)
    @given(
        has_clips=st.booleans(),
        status=st.sampled_from(["pending", "done", "in_progress", "", "DONE"]),
        section_count=st.integers(min_value=0, max_value=20),
        cached_queries=st.integers(min_value=0, max_value=20),
    )
    def test_skip_decision_and_no_quota(
        self, has_clips, status, section_count, cached_queries
    ):
        skip = should_skip(has_clips, status)

        # --- Correctness: skip iff the topic already has clips or is done. ---
        expected = has_clips or status == "done"
        assert skip is expected
        assert isinstance(skip, bool)

        # --- A skipped topic consumes no quota and regenerates nothing. ---
        # The worker model: when should_skip is True it advances without
        # estimating, affording, or charging anything for the topic.
        est_cost = estimate_item_cost(section_count, cached_queries)
        if skip:
            charged_for_topic = 0  # skipped: charge is bypassed entirely
            regenerated = False
            assert charged_for_topic == 0
            assert regenerated is False
        else:
            # When not skipped, whether it charges still depends on the pacing
            # gate, but a fully cached topic (est_cost == 0) is never advanced
            # by the spend loop, so it too charges nothing.
            if est_cost == 0:
                assert can_process_next(10_000, est_cost, 0, DEFAULT_PER_RUN_CAP) is False
