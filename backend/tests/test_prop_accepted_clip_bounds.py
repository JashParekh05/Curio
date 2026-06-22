"""Property-based test for accepted-clip population and bounds.

# Feature: content-hook-engagement, Property 30: Accepted clips are fully populated and within bounds

For any raw clip, if ``validate_clip`` accepts it (returns ``(clip, None)``)
then the accepted clip is fully populated and within bounds:
  - the required fields (id, topic_slug, video_url, title) are populated,
  - the title length is 1-200 (overlong titles are truncated to 200),
  - the description, when present, has length 1-1000 (overlong descriptions
    are truncated to 1000),
  - the ``pedagogical_role``, when set, is a defined role value,
  - any supplied ``start``/``end`` timestamps are within bounds
    (start >= 0 and end > start).

Validates: Requirements 7.2, 7.4
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip, PedagogicalRole
from app.services.arc_assembler import validate_clip

_VALID_ROLES = list(get_args(PedagogicalRole))

_TITLE_MAX_LEN = 200
_DESCRIPTION_MAX_LEN = 1000

# ---------------------------------------------------------------------------
# Smart generators — cover the full input space so the batch contains both
# accepted and rejected clips: empty/non-empty required fields, in-bounds and
# overlong titles/descriptions, valid/invalid/None roles, and optional
# in-bounds / out-of-bounds start-end timestamps.
# ---------------------------------------------------------------------------

# Required-string fields: mostly non-empty (so clips can be accepted), with a
# slice of empty values to exercise the exclusion path.
_required_str = st.one_of(
    st.text(min_size=1, max_size=20),
    st.just(""),
)

# Titles: in-bounds, empty, and overlong (overlong is accepted via truncation).
_title = st.one_of(
    st.text(min_size=1, max_size=_TITLE_MAX_LEN),
    st.text(min_size=_TITLE_MAX_LEN + 1, max_size=_TITLE_MAX_LEN + 80),
    st.just(""),
)

# Descriptions: absent (None), in-bounds, empty, and overlong (truncated).
_description = st.one_of(
    st.none(),
    st.text(min_size=1, max_size=_DESCRIPTION_MAX_LEN),
    st.text(min_size=_DESCRIPTION_MAX_LEN + 1, max_size=_DESCRIPTION_MAX_LEN + 120),
    st.just(""),
)

# Roles: defined values, None (allowed), and undefined strings (rejected).
_role = st.one_of(
    st.sampled_from(_VALID_ROLES),
    st.none(),
    st.text(max_size=15).filter(lambda s: s not in _VALID_ROLES),
)

# Optional timestamps, including negatives and end <= start to drive rejection.
_timestamp = st.one_of(
    st.none(),
    st.floats(min_value=-50.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _clip(draw):
    clip = Clip(
        id=draw(_required_str),
        topic_slug=draw(_required_str),
        title=draw(_title),
        description=draw(_description),
        # video_url is a required-string field for acceptance purposes.
        video_url=draw(_required_str),
        pedagogical_role=None,  # set below via construction-safe path
    )

    # pedagogical_role may be an undefined string for the rejection path, which
    # the Clip schema would reject at construction; bypass schema validation by
    # writing directly so validate_clip is the component under test.
    role = draw(_role)
    object.__setattr__(clip, "pedagogical_role", role)

    # start/end are not first-class Clip fields; attach them as extra
    # attributes so validate_clip's getattr-based checks can see them.
    start = draw(_timestamp)
    end = draw(_timestamp)
    if start is not None:
        object.__setattr__(clip, "start", start)
    if end is not None:
        object.__setattr__(clip, "end", end)

    return clip


class TestAcceptedClipBounds:
    @settings(max_examples=100)
    @given(clip=_clip())
    def test_accepted_clips_are_populated_and_within_bounds(self, clip):
        result, warning = validate_clip(clip)

        if result is None:
            # Rejected clips carry a descriptive warning and are out of scope
            # for the population/bounds invariant.
            assert isinstance(warning, str) and warning.strip() != ""
            return

        # Accepted: no warning is emitted.
        assert warning is None

        # Required fields are populated (non-empty) on the accepted clip.
        assert result.id
        assert result.topic_slug
        assert result.video_url

        # Title is populated and within 1-200 chars.
        assert result.title
        assert 1 <= len(result.title) <= _TITLE_MAX_LEN

        # Description, when present, is non-empty and within 1-1000 chars.
        if result.description is not None:
            assert 1 <= len(result.description) <= _DESCRIPTION_MAX_LEN

        # pedagogical_role, when set, is a defined role value.
        if result.pedagogical_role is not None:
            assert result.pedagogical_role in _VALID_ROLES

        # Optional timestamps, when present, are within bounds.
        start = getattr(result, "start", None)
        end = getattr(result, "end", None)
        if start is not None:
            assert float(start) >= 0
            if end is not None:
                assert float(end) > float(start)
