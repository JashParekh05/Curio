"""Property-based test for the Embed_Reference round-trip pure core.

# Feature: alternative-content-streams, Property 5: Embed_Reference round-trip preserves identity

Normalizing a valid candidate Source_Item into an Embed_Reference and then
rendering that Embed_Reference back into provider playback coordinates reproduces
the original Provider_Id and external item identifier without modification (the
round-trip identity). A valid candidate carries a non-empty Provider_Id and
external item identifier, non-empty playback parameters, a reported total
duration, timestamps satisfying 0 <= start < end <= duration, and an exactly
384-dimension embedding.

Validates: Requirements 4.1, 4.4
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import EmbedReference, SourceItem
from app.services.embed_reference import (
    EMBEDDING_DIM,
    normalize,
    render_coords,
    roundtrip_preserves,
)

# Provider ids are non-empty; include non-ASCII so identity preservation is
# exercised across the full text space, not just ASCII slugs.
_provider_ids = st.text(min_size=1, max_size=12)

# external_id is non-empty and explicitly spans non-ASCII characters (Req 4.4).
_external_ids = st.text(min_size=1, max_size=16)

# Playback params must be non-empty for a candidate to normalize successfully.
_playback_params = st.dictionaries(
    keys=st.text(min_size=1, max_size=6),
    values=st.text(max_size=10),
    min_size=1,
    max_size=4,
)

# A 384-length embedding of finite numeric values.
_embeddings = st.lists(
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    min_size=EMBEDDING_DIM,
    max_size=EMBEDDING_DIM,
)


@st.composite
def _valid_item_with_clip(draw):
    """Build a valid SourceItem plus valid start/end timestamps and embedding.

    Guarantees a positive duration and 0 <= start < end <= duration so that
    ``normalize`` accepts the candidate, letting the test assert the round-trip
    property on the resulting Embed_Reference.
    """
    provider_id = draw(_provider_ids)
    external_id = draw(_external_ids)

    duration = draw(st.floats(min_value=1.0, max_value=1e4))
    # 0 <= start < end <= duration: draw start in [0, duration), end in (start, duration].
    start = draw(st.floats(min_value=0.0, max_value=duration, exclude_max=True))
    end = draw(st.floats(min_value=start, max_value=duration, exclude_min=True))

    item = SourceItem(
        provider_id=provider_id,
        embed_ref=EmbedReference(
            provider_id=provider_id,
            external_id=external_id,
            playback_params=draw(_playback_params),
        ),
        title=draw(st.text(max_size=20)),
        duration_seconds=duration,
        transcript=None,
        metadata={},
    )
    return item, start, end, draw(_embeddings)


class TestEmbedReferenceRoundTrip:
    # Feature: alternative-content-streams, Property 5: Embed_Reference round-trip preserves identity
    @settings(max_examples=100)
    @given(_valid_item_with_clip())
    def test_normalize_then_render_preserves_identity(self, case):
        item, start, end, embedding = case

        embed_ref, error = normalize(item, start=start, end=end, embedding=embedding)

        # A valid candidate normalizes successfully (Req 4.1).
        assert error is None
        assert embed_ref is not None

        # Rendering the Embed_Reference back reproduces the originating
        # Provider_Id and external item identifier unchanged (Req 4.4).
        coords = render_coords(embed_ref)
        assert coords.provider_id == item.embed_ref.provider_id
        assert coords.external_id == item.embed_ref.external_id

        # The round-trip helper agrees the identity is preserved.
        assert roundtrip_preserves(embed_ref) is True
