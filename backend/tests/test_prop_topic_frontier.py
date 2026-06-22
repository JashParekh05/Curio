"""Property-based tests for the pure Topic_Frontier / Backlog decision core.

Covers Properties 11-16 from the cold-start-content-library design, all
targeting ``app/services/topic_frontier.py``. Every function under test is pure
and deterministic, so the properties assert ordering, range, idempotence,
bounding, and status-transition invariants across many generated backlogs.

# Feature: cold-start-content-library, Property 11: Frontier prioritization order, determinism, and done-exclusion
# Feature: cold-start-content-library, Property 12: Priority is always in range
# Feature: cold-start-content-library, Property 13: Enqueue dedupe is idempotent
# Feature: cold-start-content-library, Property 14: Frontier size is bounded
# Feature: cold-start-content-library, Property 15: Adjacency growth is bounded
# Feature: cold-start-content-library, Property 16: Seed-outcome status transitions
"""
from dataclasses import replace as _dc_replace

from hypothesis import given, settings, strategies as st

from app.services.topic_frontier import (
    MAX_ADJACENT_PER_SEED,
    BacklogItem,
    apply_seed_outcome,
    clamp_priority,
    derive_priority,
    enqueue,
    enqueue_adjacent,
    mark_done,
    prioritize,
    select_next,
)

_LEVELS = ("beginner", "intermediate", "advanced")


@st.composite
def _item(draw, status=None):
    """Generate a single BacklogItem with valid field values."""
    topic = draw(st.text(min_size=1, max_size=10))
    level = draw(st.sampled_from(_LEVELS))
    priority = draw(st.floats(min_value=0.0, max_value=1.0))
    st_val = status if status is not None else draw(st.sampled_from(["pending", "done"]))
    return BacklogItem(topic=topic, level=level, priority=priority, status=st_val)


@st.composite
def _backlog(draw, max_size=12):
    """Generate a backlog list of items with unique topic slugs.

    Topics are made unique so dedupe semantics are well-defined and the
    ordering assertions are unambiguous.
    """
    n = draw(st.integers(min_value=0, max_value=max_size))
    items = []
    used_topics = set()
    for _ in range(n):
        item = draw(_item())
        if item.topic in used_topics:
            item = _dc_replace(item, topic=item.topic + f"_{len(used_topics)}")
        used_topics.add(item.topic)
        items.append(item)
    return items


def _snapshot(items):
    """Return an independent copy used to detect input mutation."""
    return [_dc_replace(i) for i in items]


class TestFrontierPrioritization:
    # Feature: cold-start-content-library, Property 11: Frontier prioritization order, determinism, and done-exclusion
    #
    # For any backlog, select_next returns the pending item with the highest
    # priority, breaking ties by the lexicographically smallest topic slug; it
    # never returns a done item; identical input yields identical output.
    #
    # Validates: Requirements 3.6, 3.10, 3.12
    @settings(max_examples=100)
    @given(items=_backlog())
    def test_prioritization_order_determinism_done_exclusion(self, items):
        before = _snapshot(items)

        ordered = prioritize(items)
        nxt = select_next(items)

        # --- Done-exclusion: no done item ever appears in selection. ---
        assert all(i.status == "pending" for i in ordered)

        # --- Ordering: sorted by (-priority, topic). Each adjacent pair obeys
        #     priority desc, then slug asc on ties. ---
        for a, b in zip(ordered, ordered[1:]):
            assert (a.priority > b.priority) or (
                a.priority == b.priority and a.topic <= b.topic
            )

        # The ordered list contains exactly the pending items.
        pending = [i for i in items if i.status == "pending"]
        assert len(ordered) == len(pending)
        assert sorted(id(i) for i in ordered) == sorted(id(i) for i in pending)

        # --- select_next is the head of prioritize (or None). ---
        if ordered:
            assert nxt is ordered[0]
            assert nxt.status == "pending"
        else:
            assert nxt is None

        # --- Determinism: identical input -> identical output. ---
        assert prioritize(items) == ordered
        assert select_next(items) == nxt

        # --- Purity: inputs are never mutated. ---
        assert items == before


class TestPriorityRange:
    # Feature: cold-start-content-library, Property 12: Priority is always in range
    #
    # For any demand and coverage-gap inputs, derive_priority (and
    # clamp_priority) returns a value in the inclusive range [0.0, 1.0].
    #
    # Validates: Requirements 3.5
    @settings(max_examples=100)
    @given(
        demand=st.floats(allow_nan=False, allow_infinity=False),
        coverage_gap=st.floats(allow_nan=False, allow_infinity=False),
    )
    def test_derive_priority_in_range(self, demand, coverage_gap):
        result = derive_priority(demand, coverage_gap)
        assert 0.0 <= result <= 1.0

    @settings(max_examples=100)
    @given(p=st.floats(allow_nan=False, allow_infinity=False))
    def test_clamp_priority_in_range(self, p):
        result = clamp_priority(p)
        assert 0.0 <= result <= 1.0
        # Values already in range are returned unchanged.
        if 0.0 <= p <= 1.0:
            assert result == p


class TestEnqueueDedupeIdempotent:
    # Feature: cold-start-content-library, Property 13: Enqueue dedupe is idempotent
    #
    # For any backlog and any candidate whose topic matches an existing non-done
    # item, enqueue leaves the backlog unchanged (no duplicate added).
    #
    # Validates: Requirements 3.7
    @settings(max_examples=100)
    @given(items=_backlog(), data=st.data())
    def test_dedupe_is_idempotent(self, items, data):
        before = _snapshot(items)

        non_done = [i for i in items if i.status != "done"]
        if non_done:
            # Pick an existing non-done topic and build a candidate that collides.
            existing = data.draw(st.sampled_from(non_done))
            candidate = BacklogItem(
                topic=existing.topic,
                level=data.draw(st.sampled_from(_LEVELS)),
                priority=data.draw(st.floats(min_value=0.0, max_value=1.0)),
                status="pending",
            )
            result = enqueue(items, candidate)
            # No duplicate added; backlog content is unchanged.
            assert result == items
            assert len(result) == len(items)

        # Inputs are never mutated.
        assert items == before


class TestFrontierSizeBounded:
    # Feature: cold-start-content-library, Property 14: Frontier size is bounded
    #
    # For any sequence of enqueue operations, the count of non-done items never
    # exceeds the bound; once full, a new item is rejected and existing non-done
    # items are retained unchanged.
    #
    # Validates: Requirements 3.8, 3.9
    @settings(max_examples=100)
    @given(
        cap=st.integers(min_value=0, max_value=6),
        candidate_count=st.integers(min_value=0, max_value=20),
    )
    def test_size_is_bounded(self, cap, candidate_count):
        items: list[BacklogItem] = []
        # Each candidate has a unique topic so dedupe never masks the bound.
        for i in range(candidate_count):
            candidate = BacklogItem(
                topic=f"topic_{i}",
                level="intermediate",
                priority=0.5,
                status="pending",
            )
            prev = list(items)
            items = enqueue(items, candidate, max_backlog=cap)

            non_done = [x for x in items if x.status != "done"]
            # Non-done count never exceeds the configured bound.
            assert len(non_done) <= cap

            # If the backlog was already full, the candidate is rejected and the
            # existing non-done items are retained unchanged.
            prev_non_done = [x for x in prev if x.status != "done"]
            if len(prev_non_done) >= cap:
                assert items == prev


class TestAdjacencyGrowthBounded:
    # Feature: cold-start-content-library, Property 15: Adjacency growth is bounded
    #
    # For any set of candidate topics, enqueue_adjacent adds at most cap new
    # items (default MAX_ADJACENT_PER_SEED) per seeded topic / engagement signal.
    #
    # Validates: Requirements 3.3, 3.4
    @settings(max_examples=100)
    @given(data=st.data(), n_candidates=st.integers(min_value=0, max_value=15))
    def test_adjacency_growth_bounded(self, data, n_candidates):
        items: list[BacklogItem] = []
        # Distinct candidate topics so every accepted candidate is genuinely new.
        candidates = [
            BacklogItem(
                topic=f"adj_{i}",
                level="beginner",
                priority=data.draw(st.floats(min_value=0.0, max_value=1.0)),
                status="pending",
            )
            for i in range(n_candidates)
        ]

        # Default cap.
        result = enqueue_adjacent(items, candidates)
        added = len(result) - len(items)
        assert added <= MAX_ADJACENT_PER_SEED
        assert added <= n_candidates

        # Explicit cap is also respected.
        cap = data.draw(st.integers(min_value=0, max_value=10))
        result_capped = enqueue_adjacent(items, candidates, cap=cap)
        added_capped = len(result_capped) - len(items)
        assert added_capped <= max(cap, 0)
        assert added_capped <= n_candidates


class TestSeedOutcomeTransitions:
    # Feature: cold-start-content-library, Property 16: Seed-outcome status transitions
    #
    # For any backlog and topic, apply_seed_outcome with success=True marks that
    # item done and thereafter excludes it from selection; success=False leaves
    # the item's status unchanged so it remains eligible for retry.
    #
    # Validates: Requirements 2.8, 3.10, 3.11, 6.8
    @settings(max_examples=100)
    @given(items=_backlog(), data=st.data())
    def test_seed_outcome_transitions(self, items, data):
        before = _snapshot(items)

        if items:
            target = data.draw(st.sampled_from(items))
            topic = target.topic

            # --- success=True marks the item done and excludes it. ---
            done_result = apply_seed_outcome(items, topic, success=True)
            matching = [i for i in done_result if i.topic == topic]
            assert matching and all(i.status == "done" for i in matching)
            # Excluded from future selection.
            assert all(i.topic != topic for i in prioritize(done_result))
            assert select_next(done_result) is None or select_next(done_result).topic != topic
            # Consistent with mark_done.
            assert done_result == mark_done(items, topic)

            # --- success=False leaves the backlog unchanged (retry-eligible). ---
            fail_result = apply_seed_outcome(items, topic, success=False)
            assert fail_result == items
            original_status = {i.topic: i.status for i in items}
            for i in fail_result:
                assert i.status == original_status[i.topic]

        # Inputs are never mutated.
        assert items == before
