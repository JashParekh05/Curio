"""Property-based test for impression construction.

# Feature: engagement-telemetry, Property 3: Impression construction preserves order, identity, and context

For any ordered list of clips (Clip objects or raw row dicts) with optionally-null
Served_Context fields, and any serve context (``feed_surface`` in
``{discover, learn_path}``, ``session_id``, ``user_id`` possibly ``None``, and a
UTC ``served_at`` datetime), ``telemetry.build_impressions``:

  - produces exactly one Impression per clip, in serve order,
  - assigns ``feed_position`` values ``0..n-1`` (consecutive, unique, gapless,
    order-preserving),
  - copies ``clip_id`` / ``session_id`` / ``user_id`` / ``feed_surface`` from the
    inputs (with ``user_id`` ``None`` passed through unchanged),
  - snapshots each clip's ``pedagogical_role`` / ``content_level`` /
    ``source_platform`` / ``topic_slug`` as the Served_Context (null when absent
    on the clip), and
  - stores the serve time as a UTC ISO-8601 string.

Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.9
"""
from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services import telemetry

# The two accepted Feed_Surfaces (Req 1.7).
_feed_surface = st.sampled_from(sorted(telemetry.FEED_SURFACES))

# Optional simple identifiers; user_id may be None (Req 1.9), session may be None
# (discover has no session).
_opt_id = st.one_of(st.none(), st.text(min_size=1, max_size=12))

# Pedagogical roles valid for the Clip model, plus None (absent).
_pedagogical_role = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "problem_statement", "meaning", "visualization", "approach",
            "worked_example", "edge_cases", "definition", "motivation",
            "mechanism", "example", "common_misconception",
        ]
    ),
)
_opt_text = st.one_of(st.none(), st.text(min_size=1, max_size=10))

# A UTC-aware serve time. Naive datetimes are treated as UTC by build_impressions,
# but we generate aware UTC datetimes to assert exact ISO round-tripping.
_served_at = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2035, 1, 1),
    timezones=st.just(timezone.utc),
)


def _clip_spec():
    """A spec dict describing one clip's Served_Context fields and id."""
    return st.fixed_dictionaries(
        {
            "id": st.text(min_size=1, max_size=12),
            "topic_slug": _opt_text,
            "pedagogical_role": _pedagogical_role,
            "content_level": _opt_text,
            "source_platform": _opt_text,
        }
    )


def _as_clip_object(spec: dict) -> Clip:
    return Clip(
        id=spec["id"],
        topic_slug=spec["topic_slug"] or "",
        title="t",
        video_url="https://example.com/v",
        pedagogical_role=spec["pedagogical_role"],
        content_level=spec["content_level"],
        source_platform=spec["source_platform"],
    )


def _as_dict_clip(spec: dict) -> dict:
    """A raw row dict that omits absent (None) Served_Context fields entirely,
    exercising the 'field absent on the clip' path (Req 1.5)."""
    row = {"id": spec["id"]}
    for field in ("topic_slug", "pedagogical_role", "content_level", "source_platform"):
        if spec[field] is not None:
            row[field] = spec[field]
    return row


class TestImpressionConstruction:
    @settings(max_examples=100)
    @given(
        specs=st.lists(_clip_spec(), max_size=8),
        use_dicts=st.booleans(),
        feed_surface=_feed_surface,
        session_id=_opt_id,
        user_id=_opt_id,
        served_at=_served_at,
    )
    def test_order_identity_and_context_preserved(
        self, specs, use_dicts, feed_surface, session_id, user_id, served_at
    ):
        clips = [(_as_dict_clip(s) if use_dicts else _as_clip_object(s)) for s in specs]

        impressions = telemetry.build_impressions(
            clips,
            feed_surface=feed_surface,
            session_id=session_id,
            user_id=user_id,
            served_at=served_at,
        )

        # Exactly one Impression per clip, in serve order (Req 1.2).
        assert len(impressions) == len(clips)

        # feed_position is 0..n-1: consecutive, unique, gapless, order-preserving (Req 1.4).
        positions = [imp.feed_position for imp in impressions]
        assert positions == list(range(len(clips)))

        served_at_iso = served_at.astimezone(timezone.utc).isoformat()

        for spec, imp in zip(specs, impressions):
            # clip_id copied; serve order preserved (Req 1.2).
            assert imp.clip_id == spec["id"]
            # session_id / user_id / feed_surface copied; user_id None passes through (Req 1.2, 1.9).
            assert imp.session_id == session_id
            assert imp.user_id == user_id
            assert imp.feed_surface == feed_surface
            # Served_Context snapshotted; null when absent on the clip (Req 1.3, 1.5).
            # Clip objects coerce empty topic_slug back to None via the build path,
            # so compare against the original spec value (None when absent).
            assert imp.pedagogical_role == spec["pedagogical_role"]
            assert imp.content_level == spec["content_level"]
            assert imp.source_platform == spec["source_platform"]
            expected_topic = spec["topic_slug"]
            if not use_dicts and expected_topic is None:
                # Clip object stores "" for an absent topic_slug; the snapshot
                # reflects whatever the clip carries.
                assert imp.topic_slug in (None, "")
            else:
                assert imp.topic_slug == expected_topic
            # served_at stored as a UTC ISO-8601 string (Req 1.6).
            assert imp.served_at == served_at_iso

    @settings(max_examples=100)
    @given(served_at=_served_at)
    def test_empty_clip_list_yields_no_impressions(self, served_at):
        impressions = telemetry.build_impressions(
            [],
            feed_surface="discover",
            session_id=None,
            user_id=None,
            served_at=served_at,
        )
        assert impressions == []
