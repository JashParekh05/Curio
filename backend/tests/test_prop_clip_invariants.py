"""Property-based test for clip structural invariants of the normalize pure core.

# Feature: alternative-content-streams, Property 6: Clip structural invariants

A candidate whose embed fields (Provider_Id, external item identifier, playback
parameters) and reported total duration are all valid is normalized into a Clip
only when the proposed embedding carries exactly 384 numeric dimensions AND the
proposed timestamps satisfy 0 <= start < end <= duration. When accepted, the
result is an EmbedReference -- a provider-tagged playback descriptor carrying only
provider_id, external_id, and playback_params, with NO hosted-media field (a Clip
is an embed plus timestamps, never hosted media). In every other case normalize
discards the candidate and returns (None, error).

This test fixes the embed fields and duration to valid values and varies ONLY the
embedding length (including 384 and != 384) and the timestamps (valid and
invalid), isolating the embedding-dimension and timestamp-bound invariants.

Validates: Requirements 4.2, 4.3
"""
import dataclasses

from hypothesis import given, settings, strategies as st

from app.services.content_provider import EmbedReference, SourceItem
from app.services.embed_reference import EMBEDDING_DIM, normalize

# Always-valid embed fields so the embedding-dim and timestamp checks are what
# decide acceptance (the embed-field/duration checks run earlier and would mask
# the invariants under test if they ever failed).
_provider_ids = st.text(min_size=1, max_size=12)
_external_ids = st.text(min_size=1, max_size=16)
_playback_params = st.dictionaries(
    keys=st.text(min_size=1, max_size=6),
    values=st.text(max_size=10),
    min_size=1,
    max_size=4,
)

# Finite numeric values for embeddings of varying length, including the exact
# accepted length (384) and lengths just below / above and far from it.
_floats = st.floats(allow_nan=False, allow_infinity=False, width=32)
_embedding_lengths = st.sampled_from([0, 1, 383, EMBEDDING_DIM, 385, 768])

# Timestamps span valid and invalid regions: negatives, zero, equal start/end,
# reversed order, and values beyond the duration are all reachable.
_timestamps = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-100.0, max_value=1e4
)
_durations = st.floats(
    allow_nan=False, allow_infinity=False, min_value=1.0, max_value=1e4
)


@st.composite
def _candidate(draw):
    """Build a SourceItem with valid embed fields/duration and varied clip inputs."""
    provider_id = draw(_provider_ids)
    external_id = draw(_external_ids)
    duration = draw(_durations)
    start = draw(_timestamps)
    end = draw(_timestamps)
    length = draw(_embedding_lengths)
    embedding = draw(st.lists(_floats, min_size=length, max_size=length))

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
    return item, start, end, embedding, duration


class TestClipStructuralInvariants:
    # Feature: alternative-content-streams, Property 6: Clip structural invariants
    @settings(max_examples=100)
    @given(_candidate())
    def test_accepts_only_valid_embedding_and_timestamps(self, case):
        item, start, end, embedding, duration = case

        embed_ref, error = normalize(item, start=start, end=end, embedding=embedding)

        # The structural invariants under test: a Clip is admissible only with an
        # exactly-384-dim embedding AND in-bound, ordered timestamps (Req 4.2, 4.3).
        embedding_ok = len(embedding) == EMBEDDING_DIM
        timestamps_ok = 0 <= start < end <= duration
        should_accept = embedding_ok and timestamps_ok

        if should_accept:
            # Accepted: a non-None EmbedReference with no error.
            assert error is None
            assert embed_ref is not None
            assert isinstance(embed_ref, EmbedReference)

            # It carries the provider identity and playback params...
            assert embed_ref.provider_id == item.embed_ref.provider_id
            assert embed_ref.external_id == item.embed_ref.external_id
            assert embed_ref.playback_params == item.embed_ref.playback_params

            # ...and NOTHING else: exactly the embed-descriptor fields, so there is
            # no hosted-media field anywhere on the accepted result (Req 4.2).
            field_names = {f.name for f in dataclasses.fields(embed_ref)}
            assert field_names == {"provider_id", "external_id", "playback_params"}
        else:
            # Every other case is discarded with a specific error and no Clip.
            assert embed_ref is None
            assert error is not None
