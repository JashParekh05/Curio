"""Numerical-identity regression test for ``analytics.compute_dropoff``.

The engagement-telemetry feature refactors ``analytics._watch_ratio`` to delegate
to the shared ``telemetry.watch_ratio`` definition. ``compute_dropoff`` (and the
``GET /api/analytics/dropoff/{topic_slug}`` endpoint built on it) MUST return
per-beat retention funnel results that are numerically identical to the results
produced before the refactor.

This test pins a fixed ``clip_meta`` + ``events`` fixture and asserts the exact
expected per-beat funnel so the watch-ratio refactor cannot silently change
results.

Validates: Requirements 6.5
"""
from app.services.analytics import compute_dropoff

# Fixed fixture exercising: multiple events per beat, a zero-duration clip
# (undefined watch ratio, excluded from the average), the None-section bucket
# (sorted last), over-watch clamping to 1.0, and an event for a clip with no
# metadata (ignored).
CLIP_META = {
    "c0": {"section_index": 0, "duration_seconds": 100},
    "c1": {"section_index": 0, "duration_seconds": 100},
    "c2": {"section_index": 1, "duration_seconds": 50},
    "c3": {"section_index": 2, "duration_seconds": 0},     # duration <= 0 -> ratio undefined
    "c4": {"section_index": None, "duration_seconds": 60},  # None section -> sorted last
}

EVENTS = [
    {"clip_id": "c0", "watch_ms": 50_000, "completed": True},    # ratio 0.5
    {"clip_id": "c0", "watch_ms": 100_000, "completed": True},   # ratio 1.0
    {"clip_id": "c1", "watch_ms": 20_000, "completed": False},   # ratio 0.2
    {"clip_id": "c2", "watch_ms": 50_000, "completed": True},    # ratio 1.0
    {"clip_id": "c2", "watch_ms": 200_000, "completed": False},  # over-watch -> 1.0
    {"clip_id": "c3", "watch_ms": 10_000, "completed": False},   # ratio undefined (dur 0)
    {"clip_id": "c4", "watch_ms": 30_000, "completed": True},    # ratio 0.5
    {"clip_id": "missing", "watch_ms": 5_000, "completed": True},  # no metadata -> ignored
]

EXPECTED = [
    {
        "section_index": 0,
        "views": 3,
        "completions": 2,
        "completion_rate": 0.6667,
        "avg_watch_ratio": 0.5667,
        "retention_vs_first": 1.0,
    },
    {
        "section_index": 1,
        "views": 2,
        "completions": 1,
        "completion_rate": 0.5,
        "avg_watch_ratio": 1.0,
        "retention_vs_first": 0.6667,
    },
    {
        "section_index": 2,
        "views": 1,
        "completions": 0,
        "completion_rate": 0.0,
        "avg_watch_ratio": None,
        "retention_vs_first": 0.3333,
    },
    {
        "section_index": None,
        "views": 1,
        "completions": 1,
        "completion_rate": 1.0,
        "avg_watch_ratio": 0.5,
        "retention_vs_first": 0.3333,
    },
]


def test_compute_dropoff_numerical_identity():
    result = compute_dropoff(CLIP_META, EVENTS)
    assert result == EXPECTED


def test_compute_dropoff_empty_events_is_empty():
    assert compute_dropoff(CLIP_META, []) == []
