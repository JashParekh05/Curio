"""Property-based test for canonical YouTube embed URL construction.

# Feature: adaptive-learning-game, Property 6: Clip URLs are always canonical embed URLs

For any ``video_id``, ``to_embed_url(video_id)`` returns
``https://www.youtube.com/embed/{video_id}``, which ``ReelPlayer``'s embed
detection recognizes as a YouTube embed.

Validates: Requirements 10.3
"""
from hypothesis import given, settings, strategies as st

from app.services.game import to_embed_url

# Arbitrary video_id strings span the full text space (including empty, unicode,
# and punctuation) so the canonical-form guarantee is exercised broadly rather
# than only for well-formed 11-char YouTube ids.
_video_ids = st.text()

_EMBED_PREFIX = "https://www.youtube.com/embed/"


class TestClipUrlsAreCanonicalEmbedUrls:
    @settings(max_examples=200)
    @given(video_id=_video_ids)
    def test_output_equals_canonical_embed_form(self, video_id):
        # The output is exactly the canonical embed URL for the video id.
        assert to_embed_url(video_id) == f"{_EMBED_PREFIX}{video_id}"

    @settings(max_examples=200)
    @given(video_id=_video_ids)
    def test_output_is_prefixed_and_suffixed(self, video_id):
        result = to_embed_url(video_id)
        # Canonical embed prefix that ReelPlayer recognizes (Req 10.3) ...
        assert result.startswith(_EMBED_PREFIX)
        # ... followed by exactly the video id, with nothing else appended.
        assert result == _EMBED_PREFIX + video_id
        assert result[len(_EMBED_PREFIX):] == video_id

    @settings(max_examples=200)
    @given(video_id=_video_ids)
    def test_is_deterministic(self, video_id):
        # Same input always yields the same canonical URL.
        assert to_embed_url(video_id) == to_embed_url(video_id)

    def test_typical_youtube_id(self):
        # Explicit example: a well-formed 11-character YouTube id.
        assert (
            to_embed_url("dQw4w9WgXcQ")
            == "https://www.youtube.com/embed/dQw4w9WgXcQ"
        )
