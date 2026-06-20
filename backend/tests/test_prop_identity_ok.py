"""Property-based test for the returned-item provider identity pure core.

# Feature: alternative-content-streams, Property 3: Returned-item provider identity

A returned candidate Source_Item carries a valid provider identity iff its own
Provider_Id and its Embed_Reference's Provider_Id both equal the producing
provider's expected Provider_Id AND its Embed_Reference carries a non-empty
external item identifier. An item failing this check is rejected with a
provider-identity mismatch and excluded from ingestion.

Validates: Requirements 1.5, 1.6
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import (
    EmbedReference,
    SourceItem,
    identity_ok,
)

# A small pool of provider-id-like strings so matches and mismatches both occur
# with meaningful frequency, including the empty string as an edge case.
_provider_ids = st.sampled_from(["youtube", "vimeo", "podcast", "khan_academy", ""])

# external_id ranges over empty and non-empty values, including non-ASCII text.
_external_ids = st.text(max_size=12)

_playback_params = st.dictionaries(
    keys=st.text(min_size=1, max_size=5),
    values=st.text(max_size=8),
    max_size=3,
)


@st.composite
def _source_item_and_expected(draw):
    """Build a SourceItem with independently chosen provider ids / external id.

    The item's provider_id, the embed_ref's provider_id, the external_id, and the
    expected_provider_id are drawn independently so the generated space spans all
    combinations of (item match / mismatch), (embed match / mismatch), and
    (empty / non-empty external_id).
    """
    expected = draw(_provider_ids)
    item_provider_id = draw(_provider_ids)
    embed_provider_id = draw(_provider_ids)
    external_id = draw(_external_ids)

    embed = EmbedReference(
        provider_id=embed_provider_id,
        external_id=external_id,
        playback_params=draw(_playback_params),
    )
    item = SourceItem(
        provider_id=item_provider_id,
        embed_ref=embed,
        title=draw(st.text(max_size=10)),
        duration_seconds=draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1e4))),
        transcript=None,
        metadata={},
    )
    return item, expected


class TestReturnedItemProviderIdentity:
    # Feature: alternative-content-streams, Property 3: Returned-item provider identity
    @settings(max_examples=100)
    @given(_source_item_and_expected())
    def test_identity_ok_iff_all_three_conditions(self, item_expected):
        item, expected = item_expected

        item_provider_matches = item.provider_id == expected
        embed_provider_matches = item.embed_ref.provider_id == expected
        external_id_non_empty = bool(item.embed_ref.external_id)

        expected_result = (
            item_provider_matches
            and embed_provider_matches
            and external_id_non_empty
        )

        assert identity_ok(item, expected) is expected_result

    @settings(max_examples=100)
    @given(
        provider_id=st.text(min_size=1, max_size=12),
        external_id=st.text(min_size=1, max_size=12),
        params=_playback_params,
    )
    def test_identity_ok_true_when_all_conditions_hold(
        self, provider_id, external_id, params
    ):
        # All three conditions satisfied -> True branch.
        item = SourceItem(
            provider_id=provider_id,
            embed_ref=EmbedReference(
                provider_id=provider_id,
                external_id=external_id,
                playback_params=params,
            ),
            title="t",
            duration_seconds=10.0,
            transcript=None,
            metadata={},
        )
        assert identity_ok(item, provider_id) is True

    def test_false_when_item_provider_mismatches(self):
        item = SourceItem(
            provider_id="vimeo",
            embed_ref=EmbedReference("youtube", "abc", {}),
            title="t",
            duration_seconds=10.0,
            transcript=None,
            metadata={},
        )
        assert identity_ok(item, "youtube") is False

    def test_false_when_embed_provider_mismatches(self):
        item = SourceItem(
            provider_id="youtube",
            embed_ref=EmbedReference("vimeo", "abc", {}),
            title="t",
            duration_seconds=10.0,
            transcript=None,
            metadata={},
        )
        assert identity_ok(item, "youtube") is False

    def test_false_when_external_id_empty(self):
        item = SourceItem(
            provider_id="youtube",
            embed_ref=EmbedReference("youtube", "", {}),
            title="t",
            duration_seconds=10.0,
            transcript=None,
            metadata={},
        )
        assert identity_ok(item, "youtube") is False
