"""Property-based test for invalid-clip exclusion.

# Feature: content-hook-engagement, Property 32: Invalid clips are excluded with a warning

For any raw clip that violates a hard constraint — a negative ``start``, an
``end`` that is not strictly greater than ``start``, a missing/empty required
field, an empty title or description, or a ``pedagogical_role`` outside the
defined :data:`PedagogicalRole` set — ``validate_clip`` excludes it by returning
``(None, reason)`` and the ``reason`` is a non-empty string that identifies the
excluded clip.

Each generated clip deliberately violates exactly one constraint at a time so
that the exclusion can be attributed to that single defect.

Validates: Requirements 7.5, 7.6, 7.7
"""
import uuid
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.arc_assembler import validate_clip

_VALID_ROLES = list(get_args(PedagogicalRole))


def _valid_kwargs(clip_id: str) -> dict:
    """Return kwargs for an otherwise-valid Clip (one violation injected by caller)."""
    return {
        "id": clip_id,
        "topic_slug": "topic-slug",
        "title": "A Perfectly Valid Title",
        "description": "A perfectly valid description.",
        "video_url": "https://example.com/video",
        "pedagogical_role": "definition",
    }


# A role string guaranteed to be outside the defined PedagogicalRole set.
def _undefined_role(draw) -> str:
    candidate = draw(
        st.text(min_size=1, max_size=20).filter(lambda s: s not in _VALID_ROLES)
    )
    return candidate


@st.composite
def _invalid_clips(draw):
    """Generate a Clip violating exactly one validate_clip constraint.

    Returns a 2-tuple ``(clip, clip_id)`` where ``clip_id`` is the non-empty id
    that the exclusion warning is expected to name.
    """
    clip_id = f"clip-{draw(st.integers(min_value=0, max_value=10_000))}-{uuid.uuid4().hex[:8]}"

    category = draw(
        st.sampled_from(
            [
                "empty_title",
                "empty_description",
                "undefined_role",
                "missing_topic_slug",
                "missing_video_url",
                "negative_start",
                "end_le_start",
            ]
        )
    )

    kwargs = _valid_kwargs(clip_id)

    if category == "empty_title":
        # Clip requires title: str — an empty string is schema-valid but is an
        # excludable defect for validate_clip (Req 7.6).
        kwargs["title"] = ""
        clip = Clip(**kwargs)

    elif category == "empty_description":
        # description="" is present-but-empty -> excluded (Req 7.6).
        kwargs["description"] = ""
        clip = Clip(**kwargs)

    elif category == "undefined_role":
        # The Clip schema constrains pedagogical_role to a Literal set, so a
        # value outside that set cannot be constructed normally. model_construct
        # bypasses validation to feed validate_clip an out-of-spec role (Req 7.7).
        kwargs["pedagogical_role"] = _undefined_role(draw)
        clip = Clip.model_construct(**kwargs)

    elif category == "missing_topic_slug":
        kwargs["topic_slug"] = ""
        clip = Clip(**kwargs)

    elif category == "missing_video_url":
        kwargs["video_url"] = ""
        clip = Clip(**kwargs)

    elif category == "negative_start":
        # start/end are not first-class Clip fields; validate_clip reads them
        # via getattr. Attach a negative start (Req 7.5).
        clip = Clip(**kwargs)
        start = draw(
            st.floats(min_value=-1000.0, max_value=-0.001, allow_nan=False, allow_infinity=False)
        )
        object.__setattr__(clip, "start", start)

    else:  # end_le_start
        clip = Clip(**kwargs)
        start = draw(
            st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
        )
        end = draw(
            st.floats(min_value=-50.0, max_value=0.0, allow_nan=False, allow_infinity=False)
        )
        object.__setattr__(clip, "start", start)
        object.__setattr__(clip, "end", start + end)  # end <= start

    return clip, clip_id


class TestInvalidClipExclusion:
    @settings(max_examples=100)
    @given(data=_invalid_clips())
    def test_invalid_clip_is_excluded_with_naming_warning(self, data):
        clip, clip_id = data

        result, reason = validate_clip(clip)

        # The clip must be excluded.
        assert result is None, (
            f"expected exclusion but clip was accepted: id={clip_id!r}"
        )

        # The warning must be a non-empty string.
        assert isinstance(reason, str)
        assert reason != ""

        # The warning must identify the excluded clip by its id.
        assert clip_id in reason, (
            f"warning {reason!r} does not name the excluded clip {clip_id!r}"
        )
