"""Property-based test for discarding invalid normalization candidates.

# Feature: alternative-content-streams, Property 7: Invalid candidates are discarded with a specific reason

A candidate Source_Item that cannot be normalized into a valid Embed_Reference is
discarded with no Clip admitted, and the failure is reported with a *specific*
reason. ``normalize`` applies its checks in a fixed, deterministic order, so a
candidate that fails one or more checks always maps to the reason of the FIRST
failing check in that order:

  1. ``missing_provider_id``     -- embed reference Provider_Id is empty.
  2. ``missing_external_id``     -- embed reference external item id is empty.
  3. ``missing_playback_params`` -- embed reference has no playback params.
  4. ``missing_duration``        -- the item reports no total duration (Req 4.6).
  5. ``bad_embedding_dim``       -- the embedding is not exactly 384 dims (Req 4.2).
  6. ``bad_timestamps``          -- NOT ``0 <= start < end <= duration`` (Req 4.3).

This test imports only the pure module under test (and its dataclasses) so it runs
offline with no external service.

Validates: Requirements 4.5, 4.6
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import EmbedReference, SourceItem
from app.services.embed_reference import EMBEDDING_DIM, NormalizeError, normalize

# The six validation checks, in the exact order ``normalize`` applies them.
_FIELDS = [
    "provider_id",
    "external_id",
    "playback_params",
    "duration",
    "embedding",
    "timestamps",
]

# Embedding lengths that are NOT the required 384 dimensions (Req 4.2).
_BAD_EMBEDDING_LENS = st.sampled_from([0, EMBEDDING_DIM - 1, EMBEDDING_DIM + 1])

# Valid building blocks.
_provider_ids = st.text(min_size=1, max_size=12)
_external_ids = st.text(min_size=1, max_size=16)
_playback_params = st.dictionaries(
    keys=st.text(min_size=1, max_size=6),
    values=st.text(max_size=10),
    min_size=1,
    max_size=4,
)


def _expected_error(
    provider_id: str,
    external_id: str,
    playback_params: dict,
    duration: float | None,
    embedding_len: int,
    start: float,
    end: float,
) -> NormalizeError | None:
    """Reference oracle: the first failing check in the documented order.

    Mirrors ``normalize``'s ordering so the test independently derives the
    expected reason rather than trusting the implementation.
    """
    if not provider_id:
        return "missing_provider_id"
    if not external_id:
        return "missing_external_id"
    if not playback_params:
        return "missing_playback_params"
    if duration is None:
        return "missing_duration"
    if embedding_len != EMBEDDING_DIM:
        return "bad_embedding_dim"
    if not (0 <= start < end <= duration):
        return "bad_timestamps"
    return None


@st.composite
def _invalid_candidate(draw):
    """Build a candidate invalid in at least one specific way.

    Picks a non-empty subset of the six checks to break; the remaining fields are
    constructed valid. This guarantees the candidate is invalid (so ``normalize``
    must reject it) while letting us assert that the reported reason matches the
    FIRST failing check in the documented order.
    """
    broken = draw(st.sets(st.sampled_from(_FIELDS), min_size=1))

    # A concrete positive duration used to construct timestamp bounds; whether the
    # item actually reports it is governed by the "duration" break below.
    duration_value = draw(st.floats(min_value=1.0, max_value=1e4))

    # provider_id: empty when broken (-> missing_provider_id).
    provider_id = "" if "provider_id" in broken else draw(_provider_ids)

    # external_id: empty when broken (-> missing_external_id).
    external_id = "" if "external_id" in broken else draw(_external_ids)

    # playback_params: empty dict when broken (-> missing_playback_params).
    playback_params = {} if "playback_params" in broken else draw(_playback_params)

    # duration_seconds: None when broken (-> missing_duration, Req 4.6).
    duration_seconds = None if "duration" in broken else duration_value

    # embedding: length 0 / 383 / 385 when broken (-> bad_embedding_dim, Req 4.2).
    embedding_len = draw(_BAD_EMBEDDING_LENS) if "embedding" in broken else EMBEDDING_DIM
    embedding = [0.0] * embedding_len

    # timestamps: out-of-bounds when broken (-> bad_timestamps, Req 4.3).
    if "timestamps" in broken:
        form = draw(st.sampled_from(["start_negative", "start_ge_end", "end_gt_duration"]))
        if form == "start_negative":
            # start < 0 violates 0 <= start.
            start = draw(st.floats(min_value=-1e4, max_value=-0.001))
            end = draw(st.floats(min_value=0.001, max_value=duration_value))
        elif form == "start_ge_end":
            # start >= end violates start < end.
            start = draw(st.floats(min_value=0.0, max_value=duration_value))
            end = draw(st.floats(min_value=0.0, max_value=start))
        else:  # end_gt_duration
            # end > duration violates end <= duration.
            start = draw(st.floats(min_value=0.0, max_value=duration_value, exclude_max=True))
            end = draw(st.floats(min_value=duration_value + 0.001, max_value=duration_value + 1e4))
    else:
        # Valid timestamps: 0 <= start < end <= duration.
        start = draw(st.floats(min_value=0.0, max_value=duration_value, exclude_max=True))
        end = draw(st.floats(min_value=start, max_value=duration_value, exclude_min=True))

    item = SourceItem(
        provider_id=provider_id,
        embed_ref=EmbedReference(
            provider_id=provider_id,
            external_id=external_id,
            playback_params=playback_params,
        ),
        title=draw(st.text(max_size=20)),
        duration_seconds=duration_seconds,
        transcript=None,
        metadata={},
    )
    return item, start, end, embedding


class TestNormalizeDiscardsInvalidCandidates:
    # Feature: alternative-content-streams, Property 7: Invalid candidates are discarded with a specific reason
    @settings(max_examples=100)
    @given(_invalid_candidate())
    def test_invalid_candidate_rejected_with_first_failing_reason(self, case):
        item, start, end, embedding = case

        embed_ref, error = normalize(item, start=start, end=end, embedding=embedding)

        expected = _expected_error(
            provider_id=item.embed_ref.provider_id,
            external_id=item.embed_ref.external_id,
            playback_params=item.embed_ref.playback_params,
            duration=item.duration_seconds,
            embedding_len=len(embedding),
            start=start,
            end=end,
        )

        # The candidate is invalid by construction.
        assert expected is not None

        # No Clip is admitted from a discarded candidate (Req 4.5, 4.6).
        assert embed_ref is None

        # The reported reason is the FIRST failing check in the documented order.
        assert error == expected
