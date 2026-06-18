"""Property-based test for deterministic, permutation-invariant journey ordering.

# Feature: engagement-telemetry, Property 6: Journey ordering is deterministic and permutation-invariant

For any set of Impressions and Watch_Events presented in any input order,
``telemetry.build_session_journey``:

  - returns entries ordered by ascending ``served_at``, then ascending
    ``feed_position``, then ascending ``impression_id`` (the final deterministic
    tie-break), and
  - returns the identical ordered sequence regardless of input permutation,
    including across a user's multiple sessions (the per-user journey).

Validates: Requirements 3.2, 3.8, 7.1, 7.6
"""
import random
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from app.models.schemas import Impression
from app.services import telemetry

# Impressions span multiple sessions (per-user journey, Req 3.8) and a few clips.
_CLIP_IDS = ["c1", "c2", "c3"]
_SESSION_IDS = ["s1", "s2", "s3"]

_opt_text = st.one_of(st.none(), st.text(min_size=1, max_size=8))
_feedback = st.sampled_from(["want_more", "already_know", None])

# A small base instant; served_at values are drawn from a tight range of second
# offsets so timestamp collisions are common, forcing the feed_position and
# impression_id tie-breaks to do real work.
_BASE = datetime(2022, 1, 1, tzinfo=timezone.utc)


@st.composite
def _impressions(draw):
    n = draw(st.integers(min_value=0, max_value=7))
    impressions = []
    for i in range(n):
        served_offset = draw(st.integers(min_value=0, max_value=3))
        impressions.append(
            Impression(
                # Unique impression_id guarantees a total order via the final
                # tie-break, so the sort is deterministic (Req 3.2).
                id=f"imp-{i:03d}",
                clip_id=draw(st.sampled_from(_CLIP_IDS)),
                session_id=draw(st.sampled_from(_SESSION_IDS)),
                feed_surface=draw(st.sampled_from(sorted(telemetry.FEED_SURFACES))),
                # Constrained feed_position range makes collisions likely.
                feed_position=draw(st.integers(min_value=0, max_value=2)),
                pedagogical_role=draw(_opt_text),
                content_level=draw(_opt_text),
                source_platform=draw(_opt_text),
                topic_slug=draw(_opt_text),
                served_at=(_BASE + timedelta(seconds=served_offset)).isoformat(),
            )
        )
    return impressions


@st.composite
def _watch_events(draw):
    n = draw(st.integers(min_value=0, max_value=8))
    events = []
    for i in range(n):
        events.append(
            {
                "clip_id": draw(st.sampled_from(_CLIP_IDS)),
                "session_id": draw(st.sampled_from(_SESSION_IDS)),
                "watch_ms": draw(st.integers(min_value=0, max_value=600_000)),
                "completed": draw(st.booleans()),
                "replay_count": draw(st.integers(min_value=0, max_value=5)),
                "feedback": draw(_feedback),
                "duration_seconds": draw(st.one_of(st.none(), st.integers(min_value=1, max_value=600))),
                "created_at": (_BASE + timedelta(seconds=i)).isoformat(),
            }
        )
    return events


class TestJourneyOrdering:
    @settings(max_examples=100)
    @given(
        impressions=_impressions(),
        events=_watch_events(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
    )
    def test_ordering_is_deterministic_and_permutation_invariant(
        self, impressions, events, seed
    ):
        journey = telemetry.build_session_journey(impressions, events)

        # Ordered by (served_at asc, feed_position asc, impression_id asc) (Req 3.2).
        keys = [(e.served_at, e.feed_position, e.impression_id) for e in journey]
        assert keys == sorted(keys)

        # Permutation-invariant: shuffling either input list yields the identical
        # ordered output sequence (Req 3.8, 7.1, 7.6).
        rng = random.Random(seed)
        shuffled_impressions = list(impressions)
        shuffled_events = list(events)
        rng.shuffle(shuffled_impressions)
        rng.shuffle(shuffled_events)

        journey_shuffled = telemetry.build_session_journey(
            shuffled_impressions, shuffled_events
        )

        assert journey_shuffled == journey
