"""Property-based test for Engagement_Rollup count conservation and bounded rates.

# Feature: engagement-telemetry, Property 7: Rollup conserves count and reports bounded rates per group

For any set of Impressions (including ones with a null Slice_Dimension key), any
matching Watch_Events, and any supported Slice_Dimension,
``telemetry.compute_rollup``:

  - produces exactly one group per distinct dimension value present, with
    null-keyed Impressions collected into a single dedicated null-key group,
  - conserves count: the group counts sum to ``len(impressions)`` (empty input
    -> no groups),
  - reports per group ``count >= 0``, ``skip_rate = skipped / count`` and
    ``completion_rate = completed / count`` each in ``[0.0, 1.0]`` with
    ``skipped <= count`` and ``completed <= count``, and
  - reports ``avg_watch_ratio`` as the mean of the group's defined Watch_Ratios
    (each in ``[0.0, 1.0]``), or ``None`` when no Impression in the group has a
    defined Watch_Ratio.

Validates: Requirements 4.1, 4.2, 4.3, 4.6, 4.7, 4.8, 4.9, 4.11, 7.7, 7.8
"""
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from app.models.schemas import Impression
from app.services import telemetry

# Small, overlapping pools so Impressions and Watch_Events frequently share a
# join key (clip_id, session_id). session_id may be None (discover has no session).
_CLIP_IDS = ["c1", "c2", "c3"]
_SESSION_IDS = ["s1", "s2", None]

# Slice_Dimension key pools deliberately include None so the dedicated null-key
# group is exercised. feed_surface is drawn from the accepted values (never null).
_opt_key = st.one_of(st.none(), st.sampled_from(["a", "b", "c"]))
_feedback = st.sampled_from(["want_more", "already_know", None])

_served_at_dt = st.datetimes(
    min_value=datetime(2021, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(timezone.utc),
)

_CREATED_BASE = datetime(2021, 1, 1, tzinfo=timezone.utc)


@st.composite
def _impressions(draw):
    n = draw(st.integers(min_value=0, max_value=6))
    impressions = []
    for i in range(n):
        impressions.append(
            Impression(
                id=f"imp-{i}",
                clip_id=draw(st.sampled_from(_CLIP_IDS)),
                session_id=draw(st.sampled_from(_SESSION_IDS)),
                user_id=draw(_opt_key),
                feed_surface=draw(st.sampled_from(sorted(telemetry.FEED_SURFACES))),
                feed_position=draw(st.integers(min_value=0, max_value=10)),
                pedagogical_role=draw(_opt_key),
                content_level=draw(_opt_key),
                source_platform=draw(_opt_key),
                topic_slug=draw(_opt_key),
                served_at=draw(_served_at_dt).isoformat(),
            )
        )
    return impressions


@st.composite
def _watch_events(draw):
    n = draw(st.integers(min_value=0, max_value=10))
    events = []
    for _ in range(n):
        events.append(
            {
                "clip_id": draw(st.sampled_from(_CLIP_IDS)),
                "session_id": draw(st.sampled_from(_SESSION_IDS)),
                "watch_ms": draw(st.one_of(st.none(), st.integers(min_value=-1000, max_value=600_000))),
                "completed": draw(st.booleans()),
                "replay_count": draw(st.integers(min_value=0, max_value=5)),
                "feedback": draw(_feedback),
                "duration_seconds": draw(st.one_of(st.none(), st.integers(min_value=1, max_value=600))),
                # created_at assigned in the test body for global uniqueness.
            }
        )
    return events


def _engagement(imp, by_key):
    """Independently derive (skipped, completed, ratio) for one Impression.

    Mirrors compute_rollup's latest-wins join on (clip_id, session_id) using the
    public ``watch_ratio`` / ``classify_skip`` helpers; created_at values are
    globally unique so the latest event is unambiguous.
    """
    matches = by_key.get((imp.clip_id, imp.session_id))
    if not matches:
        return True, False, None
    winner = max(matches, key=lambda e: datetime.fromisoformat(e["created_at"]))
    watch_ms = winner.get("watch_ms") or 0
    ratio = telemetry.watch_ratio(watch_ms, winner.get("duration_seconds"))
    completed = bool(winner.get("completed"))
    skipped = telemetry.classify_skip(ratio, has_watch_event=True)
    return skipped, completed, ratio


_DIMENSIONS = sorted(telemetry.SLICE_DIMENSIONS)


class TestRollupCounts:
    @settings(max_examples=100)
    @given(
        impressions=_impressions(),
        events=_watch_events(),
        dimension=st.sampled_from(_DIMENSIONS),
    )
    def test_count_conserved_and_rates_bounded(self, impressions, events, dimension):
        for offset, ev in enumerate(events):
            ev["created_at"] = (_CREATED_BASE + timedelta(seconds=offset)).isoformat()

        groups = telemetry.compute_rollup(impressions, events, dimension)

        # Empty input -> no groups (Req 4.2).
        if not impressions:
            assert groups == []
            return

        # Independent latest-wins index, then per-Impression engagement.
        by_key = {}
        for ev in events:
            by_key.setdefault((ev["clip_id"], ev["session_id"]), []).append(ev)

        # Expected grouping: one group per distinct key value (None is its own,
        # single dedicated null group) -- Req 4.1, 4.9.
        expected = {}
        for imp in impressions:
            key = getattr(imp, dimension)
            skipped, completed, ratio = _engagement(imp, by_key)
            acc = expected.setdefault(key, {"count": 0, "skipped": 0, "completed": 0, "ratios": []})
            acc["count"] += 1
            acc["skipped"] += int(skipped)
            acc["completed"] += int(completed)
            if ratio is not None:
                acc["ratios"].append(ratio)

        # One group per distinct key present (Req 4.1, 4.9).
        result_by_key = {g.key: g for g in groups}
        assert len(groups) == len(result_by_key)  # keys are unique
        assert set(result_by_key) == set(expected)

        # Count conservation: group counts sum to len(impressions) (Req 7.7).
        assert sum(g.count for g in groups) == len(impressions)

        for key, exp in expected.items():
            g = result_by_key[key]
            count = exp["count"]

            # Non-negative count (Req 4.3) matching the expected grouping.
            assert g.count == count
            assert count >= 0

            # skipped/completed bounded by count (Req 7.8).
            assert exp["skipped"] <= count
            assert exp["completed"] <= count

            # skip_rate / completion_rate definitions and bounds (Req 4.6, 4.7, 4.11).
            assert g.skip_rate == exp["skipped"] / count
            assert g.completion_rate == exp["completed"] / count
            assert 0.0 <= g.skip_rate <= 1.0
            assert 0.0 <= g.completion_rate <= 1.0

            # avg_watch_ratio = mean of defined ratios, else None (Req 4.8, 4.11).
            ratios = exp["ratios"]
            if ratios:
                assert g.avg_watch_ratio == sum(ratios) / len(ratios)
                assert 0.0 <= g.avg_watch_ratio <= 1.0
            else:
                assert g.avg_watch_ratio is None

    @settings(max_examples=100)
    @given(events=_watch_events(), dimension=st.sampled_from(_DIMENSIONS))
    def test_empty_impressions_yield_no_groups(self, events, dimension):
        for offset, ev in enumerate(events):
            ev["created_at"] = (_CREATED_BASE + timedelta(seconds=offset)).isoformat()
        # Empty input -> [] (Req 4.2).
        assert telemetry.compute_rollup([], events, dimension) == []
