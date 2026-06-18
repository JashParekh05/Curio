"""Property-based test for session-journey construction and the watch-event join.

# Feature: engagement-telemetry, Property 5: Journey construction conserves count and joins watch events latest-wins

For any set of Impressions and any set of Watch_Events,
``telemetry.build_session_journey``:

  - produces exactly one ``JourneyEntry`` per Impression (empty input -> empty
    output),
  - copies each entry's ``feed_position`` / ``feed_surface`` / ``served_context``
    from its Impression,
  - for the matching ``(clip_id, session_id)`` Watch_Events populates ``watch_ms``,
    ``watch_ratio``, ``completed``, ``replay_count``, and ``feedback`` from the
    latest-``created_at`` event (latest-wins), and
  - when no Watch_Event matches, the entry has ``watch_ms = 0``, undefined
    ``watch_ratio`` (``None``), ``completed = False``, ``replay_count = 0``,
    ``feedback = None``, and is classified as a Skip.

Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.9, 3.10
"""
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from app.models.schemas import Impression
from app.services import telemetry

# Small, overlapping pools so Impressions and Watch_Events frequently share a
# join key (clip_id, session_id) -- and so several events land in the same group,
# exercising latest-wins. session_id may be None (discover has no session).
_CLIP_IDS = ["c1", "c2", "c3"]
_SESSION_IDS = ["s1", "s2", None]

_opt_text = st.one_of(st.none(), st.text(min_size=1, max_size=8))
_feedback = st.sampled_from(["want_more", "already_know", None])

# A UTC-aware serve time for each Impression (rendered to ISO-8601, the stored shape).
_served_at_dt = st.datetimes(
    min_value=datetime(2021, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(timezone.utc),
)

# Base instant; per-event created_at offsets are assigned in the test body so every
# Watch_Event timestamp is globally unique and the latest-in-group winner is
# unambiguous regardless of any timestamp ties.
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
                feed_surface=draw(st.sampled_from(sorted(telemetry.FEED_SURFACES))),
                feed_position=draw(st.integers(min_value=0, max_value=10)),
                pedagogical_role=draw(_opt_text),
                content_level=draw(_opt_text),
                source_platform=draw(_opt_text),
                topic_slug=draw(_opt_text),
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


def _expected_winner(events_for_key):
    """The latest-created_at event in a (clip_id, session_id) group.

    created_at values are made globally unique in the test body, so the max is
    unambiguous.
    """
    return max(
        events_for_key,
        key=lambda e: datetime.fromisoformat(e["created_at"]),
    )


class TestJourneyJoin:
    @settings(max_examples=100)
    @given(impressions=_impressions(), events=_watch_events())
    def test_count_conserved_and_latest_wins_join(self, impressions, events):
        # Assign globally-unique, strictly-increasing created_at timestamps so the
        # latest event per (clip_id, session_id) group is well defined regardless
        # of input order (varied timestamps exercise latest-wins).
        for offset, ev in enumerate(events):
            ev["created_at"] = (_CREATED_BASE + timedelta(seconds=offset)).isoformat()

        journey = telemetry.build_session_journey(impressions, events)

        # Exactly one JourneyEntry per Impression (Req 3.1, 3.9, 3.10).
        assert len(journey) == len(impressions)

        # Group events by join key for expected-winner computation.
        by_key = {}
        for ev in events:
            by_key.setdefault((ev["clip_id"], ev["session_id"]), []).append(ev)

        # The journey is reordered; index entries by their (unique) impression_id.
        entries_by_id = {e.impression_id: e for e in journey}
        assert set(entries_by_id) == {imp.id for imp in impressions}

        for imp in impressions:
            entry = entries_by_id[imp.id]

            # feed_position / feed_surface / served_context copied from the
            # Impression (Req 3.3).
            assert entry.clip_id == imp.clip_id
            assert entry.feed_position == imp.feed_position
            assert entry.feed_surface == imp.feed_surface
            assert entry.served_context == {
                "pedagogical_role": imp.pedagogical_role,
                "content_level": imp.content_level,
                "source_platform": imp.source_platform,
                "topic_slug": imp.topic_slug,
            }

            matches = by_key.get((imp.clip_id, imp.session_id))
            if matches:
                # Matching Watch_Events: the latest-created_at event wins (Req 3.4).
                winner = _expected_winner(matches)
                expected_watch_ms = winner.get("watch_ms") or 0
                expected_ratio = telemetry.watch_ratio(
                    expected_watch_ms, winner.get("duration_seconds")
                )
                assert entry.watch_ms == expected_watch_ms
                assert entry.watch_ratio == expected_ratio
                assert entry.completed == bool(winner.get("completed"))
                assert entry.replay_count == (winner.get("replay_count") or 0)
                assert entry.feedback == winner.get("feedback")
                # Skip flag follows classify_skip over the joined ratio (Req 3.5).
                assert entry.skipped == telemetry.classify_skip(
                    entry.watch_ratio, has_watch_event=True
                )
            else:
                # No matching Watch_Event (Req 3.5).
                assert entry.watch_ms == 0
                assert entry.watch_ratio is None
                assert entry.completed is False
                assert entry.replay_count == 0
                assert entry.feedback is None
                assert entry.skipped is True

    @settings(max_examples=100)
    @given(events=_watch_events())
    def test_empty_impressions_yield_empty_journey(self, events):
        for offset, ev in enumerate(events):
            ev["created_at"] = (_CREATED_BASE + timedelta(seconds=offset)).isoformat()
        # Empty input -> empty output (Req 3.9, 3.10).
        assert telemetry.build_session_journey([], events) == []
