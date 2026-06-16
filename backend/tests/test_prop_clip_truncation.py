"""Property-based test for clip text truncation.

# Feature: content-hook-engagement, Property 31: Overlong clip text is truncated to bounds

For any raw Clip whose title exceeds 200 characters and/or whose description
exceeds 1000 characters, when ``validate_clip`` accepts the clip the accepted
result must have:

  - a title that is exactly 200 characters and equals the original title's
    first 200 characters (when the original title exceeded 200), and
  - a description that is exactly 1000 characters and equals the original
    description's first 1000 characters (when the original description
    exceeded 1000).

Validates: Requirements 7.3
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.arc_assembler import validate_clip

_TITLE_MAX_LEN = 200
_DESCRIPTION_MAX_LEN = 1000


@st.composite
def _overlong_clips(draw):
    """Generate Clips with all required fields valid but overlong text.

    At least one of title (>200) or description (>1000) is overlong; both may
    be. All other required fields (id, topic_slug, video_url) are non-empty and
    valid, and no start/end timestamps or pedagogical_role are set so the clip
    is accepted by ``validate_clip`` and only truncation applies.
    """
    # Decide which fields are overlong; ensure at least one is.
    title_overlong = draw(st.booleans())
    desc_overlong = draw(st.booleans())
    if not (title_overlong or desc_overlong):
        title_overlong = True

    if title_overlong:
        # title length strictly greater than 200.
        title = draw(st.text(min_size=_TITLE_MAX_LEN + 1, max_size=_TITLE_MAX_LEN + 300))
    else:
        # A valid, non-empty title within bounds.
        title = draw(st.text(min_size=1, max_size=_TITLE_MAX_LEN))

    if desc_overlong:
        description = draw(
            st.text(min_size=_DESCRIPTION_MAX_LEN + 1, max_size=_DESCRIPTION_MAX_LEN + 500)
        )
    else:
        # Either a within-bounds non-empty description or None.
        description = draw(
            st.one_of(
                st.none(),
                st.text(min_size=1, max_size=_DESCRIPTION_MAX_LEN),
            )
        )

    clip = Clip(
        id=draw(st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != "")),
        topic_slug="topic-slug",
        title=title,
        description=description,
        video_url="https://example.com/video",
    )
    return clip


class TestClipTruncation:
    @settings(max_examples=100)
    @given(clip=_overlong_clips())
    def test_overlong_text_truncated_to_bounds(self, clip):
        original_title = clip.title
        original_description = clip.description

        result, warning = validate_clip(clip)

        # Truncation-only clips must be accepted (no exclusion warning).
        assert result is not None, f"clip was excluded unexpectedly: {warning}"
        assert warning is None

        # Title: if the original exceeded 200, result must be exactly 200 chars
        # and equal to the original's first 200 chars.
        if len(original_title) > _TITLE_MAX_LEN:
            assert len(result.title) == _TITLE_MAX_LEN
            assert result.title == original_title[:_TITLE_MAX_LEN]

        # Description: if the original exceeded 1000, result must be exactly
        # 1000 chars and equal to the original's first 1000 chars.
        if original_description is not None and len(original_description) > _DESCRIPTION_MAX_LEN:
            assert result.description is not None
            assert len(result.description) == _DESCRIPTION_MAX_LEN
            assert result.description == original_description[:_DESCRIPTION_MAX_LEN]
