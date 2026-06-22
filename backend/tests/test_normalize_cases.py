"""Unit tests for `embed_reference.normalize` concrete cases.

These are plain pytest example-based tests that pin the specific
``NormalizeError`` returned for each invalid candidate, plus one valid-candidate
success case. They complement the property tests for the normalization core by
nailing down the exact error label for representative concrete inputs.

The validation order enforced by ``normalize`` is fixed and deterministic:
missing provider id -> missing external id -> missing playback params ->
missing duration -> bad embedding dim -> bad timestamps. Each case below is
constructed so the field under test is the FIRST (and only) check it trips.

Validates: Requirements 4.5, 4.6
"""
from app.services.content_provider import EmbedReference, SourceItem
from app.services.embed_reference import EMBEDDING_DIM, normalize

# A duration the timestamp cases stay within unless deliberately violating it.
_DURATION = 100.0

# A valid 384-dim embedding reused by every case whose embedding is not under test.
_VALID_EMBEDDING = [0.0] * EMBEDDING_DIM


def _make_item(
    *,
    provider_id: str = "vimeo",
    external_id: str = "abc123",
    playback_params: dict | None = None,
    duration_seconds: float | None = _DURATION,
) -> SourceItem:
    """Build a SourceItem whose fields default to a fully valid candidate.

    Individual tests override exactly the field they are exercising so the case
    trips that specific check first.
    """
    if playback_params is None:
        playback_params = {"start": "0"}
    return SourceItem(
        provider_id=provider_id,
        embed_ref=EmbedReference(
            provider_id=provider_id,
            external_id=external_id,
            playback_params=playback_params,
        ),
        title="A clip",
        duration_seconds=duration_seconds,
        transcript=None,
        metadata={},
    )


class TestNormalizeConcreteCases:
    def test_missing_external_id_returns_missing_external_id(self):
        item = _make_item(external_id="")

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=_VALID_EMBEDDING
        )

        assert embed_ref is None
        assert error == "missing_external_id"

    def test_missing_playback_params_returns_missing_playback_params(self):
        item = _make_item(playback_params={})

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=_VALID_EMBEDDING
        )

        assert embed_ref is None
        assert error == "missing_playback_params"

    def test_embedding_383_dims_returns_bad_embedding_dim(self):
        item = _make_item()

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=[0.0] * 383
        )

        assert embed_ref is None
        assert error == "bad_embedding_dim"

    def test_embedding_385_dims_returns_bad_embedding_dim(self):
        item = _make_item()

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=[0.0] * 385
        )

        assert embed_ref is None
        assert error == "bad_embedding_dim"

    def test_start_equals_end_returns_bad_timestamps(self):
        item = _make_item()

        embed_ref, error = normalize(
            item, start=5.0, end=5.0, embedding=_VALID_EMBEDDING
        )

        assert embed_ref is None
        assert error == "bad_timestamps"

    def test_end_greater_than_duration_returns_bad_timestamps(self):
        item = _make_item(duration_seconds=_DURATION)

        embed_ref, error = normalize(
            item, start=0.0, end=_DURATION + 1.0, embedding=_VALID_EMBEDDING
        )

        assert embed_ref is None
        assert error == "bad_timestamps"

    def test_duration_none_returns_missing_duration(self):
        item = _make_item(duration_seconds=None)

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=_VALID_EMBEDDING
        )

        assert embed_ref is None
        assert error == "missing_duration"

    def test_valid_candidate_returns_embed_reference(self):
        item = _make_item(
            provider_id="vimeo",
            external_id="vid-42",
            playback_params={"start": "0", "end": "10"},
            duration_seconds=_DURATION,
        )

        embed_ref, error = normalize(
            item, start=0.0, end=10.0, embedding=_VALID_EMBEDDING
        )

        assert error is None
        assert embed_ref is not None
        assert embed_ref.provider_id == "vimeo"
        assert embed_ref.external_id == "vid-42"
        assert embed_ref.playback_params == {"start": "0", "end": "10"}
